"""
APEX v11.0 — Institutional Order Router (Phase 8)
===================================================
Centralized execution layer handling direct exchange interactions.
Replaces raw retail "market" orders with slippage-capped aggressive limits.
Provides an asynchronous limit dispatcher to place PENDING_ZONES pullback orders.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

import ccxt.async_support as ccxt

from database.timescaledb import get_pullback_items_by_status, update_pullback_status
from services.execution.transaction_cost_model import OrderUrgency

logger = logging.getLogger("OrderRouter")

@dataclass
class ExecutionRequest:
    symbol: str
    direction: str  # "LONG" or "SHORT"
    amount: float
    current_price: float
    urgency: OrderUrgency
    stop_loss: float
    take_profit: float

class OrderRouter:
    def __init__(self, exchange: ccxt.Exchange, is_live: bool = False):
        self.exchange = exchange
        self.is_live = is_live
        self.running = False

    def _calculate_aggressive_limit(self, current_price: float, direction: str, urgency: OrderUrgency) -> float:
        """
        Calculates a slippage-capped limit price instead of sending a raw market order.
        """
        # bps offsets (1 bp = 0.01%)
        if urgency == OrderUrgency.HIGH:
            offset_bps = 15.0  # Willing to cross the spread aggressively (15 bps slippage cap)
        elif urgency == OrderUrgency.MEDIUM:
            offset_bps = 5.0   # Moderate aggressiveness
        else:
            offset_bps = 0.0   # Passive limit (at mid or bid/ask)
            
        offset_pct = offset_bps / 10000.0
        
        if direction == "LONG":
            return current_price * (1.0 + offset_pct)
        else:
            return current_price * (1.0 - offset_pct)

    async def submit_aggressive_entry(self, req: ExecutionRequest) -> Dict[str, Any]:
        """
        Submits an aggressive limit order. If it fills, attaches SL and TP.
        If it doesn't fill immediately, the exchange handles it as an open limit.
        """
        if not self.is_live:
            logger.info(f"[DEMO] Simulated Aggressive Entry {req.direction} for {req.symbol}")
            return {
                "status": "closed",
                "average": req.current_price,
                "amount": req.amount,
                "id": "demo_order_123"
            }

        limit_price = self._calculate_aggressive_limit(req.current_price, req.direction, req.urgency)
        side = "buy" if req.direction == "LONG" else "sell"
        
        try:
            # 1. Place Aggressive Limit Entry
            logger.info(f"Submitting {req.urgency.value} urgency LIMIT {side} for {req.symbol} @ {limit_price:.6f}")
            order = await self.exchange.create_order(
                symbol=req.symbol,
                type="limit",
                side=side,
                amount=req.amount,
                price=limit_price
            )
            
            # Note: We must wait or fetch to see if it filled, but usually we just place the OCO SL/TP
            # directly against the filled/unfilled entry. In CCXT, we often have to wait for the fill 
            # to know the exact average price for the SL. We will offload SL/TP placement to the background
            # or rely on the exchange's reduce-only logic.
            
            # For APEX, we'll place the SL/TP asynchronously.
            asyncio.create_task(self._place_protective_orders(req, order))
            
            return order
            
        except Exception as e:
            logger.error(f"Failed to submit aggressive limit for {req.symbol}: {e}")
            raise

    async def _place_protective_orders(self, req: ExecutionRequest, entry_order: Dict[str, Any], max_retries: int = 3):
        """Places SL and TP asynchronously after an entry is placed."""
        symbol = req.symbol
        amount = entry_order.get('amount', req.amount)
        side = "sell" if req.direction == "LONG" else "buy"
        
        # Give the exchange a moment to process the entry fill
        await asyncio.sleep(2)
        
        # 1. Stop Loss (Market Stop)
        sl_success = False
        for attempt in range(max_retries):
            try:
                sl_order = await self.exchange.create_order(
                    symbol=symbol,
                    type="stop",
                    side=side,
                    amount=amount,
                    price=None, # Stop Market
                    params={'stopPrice': req.stop_loss, 'reduceOnly': True}
                )
                logger.info(f"Placed Stop Market SL for {symbol} at {req.stop_loss}")
                sl_success = True
                break
            except Exception as e:
                logger.warning(f"Failed to place SL for {symbol} (attempt {attempt+1}/{max_retries}): {e}")
                await asyncio.sleep(1 + attempt * 2)
                
        if not sl_success:
            logger.critical(f"CRITICAL: Failed to place SL for {symbol}! Position UNPROTECTED.")
            
        # 2. Take Profit (Limit)
        # Often placed for 40% of the position as per v10.5 logic
        tp_amount = amount * 0.40
        tp_success = False
        for attempt in range(max_retries):
            try:
                tp_order = await self.exchange.create_order(
                    symbol=symbol,
                    type="limit",
                    side=side,
                    amount=tp_amount,
                    price=req.take_profit,
                    params={'reduceOnly': True}
                )
                logger.info(f"Placed Limit TP for {symbol} at {req.take_profit}")
                tp_success = True
                break
            except Exception as e:
                logger.warning(f"Failed to place TP for {symbol} (attempt {attempt+1}/{max_retries}): {e}")
                await asyncio.sleep(1 + attempt * 2)

    async def start_limit_dispatcher(self):
        """Background loop to dispatch PENDING_ZONES to the exchange."""
        self.running = True
        logger.info("Starting Limit Dispatcher for PENDING_ZONES...")
        await self._dispatch_loop()

    def stop(self):
        self.running = False

    async def _dispatch_loop(self):
        while self.running:
            try:
                if self.is_live:
                    pending_items = await get_pullback_items_by_status("PENDING_ZONES")
                    
                    for item in pending_items:
                        await self._dispatch_limit(item)
            except Exception as e:
                logger.error(f"Error in Limit Dispatcher loop: {e}")
                
            await asyncio.sleep(10)  # Polling interval

    async def _dispatch_limit(self, item: Dict[str, Any]):
        symbol = item['symbol']
        direction = item['direction']
        entry_price = item['original_entry']  # Or limit_entries[0] ideally
        
        # Recover amount from position_usd
        amount = item.get('position_usd', 100.0) / entry_price
        side = "buy" if direction == "LONG" else "sell"
        
        try:
            order = await self.exchange.create_order(
                symbol=symbol,
                type="limit",
                side=side,
                amount=amount,
                price=entry_price
            )
            
            exchange_order_id = order.get('id')
            if exchange_order_id:
                logger.info(f"Dispatched actual LIMIT order for {symbol} to exchange (ID: {exchange_order_id})")
                
                from database.timescaledb import get_connection
                pool = await get_connection()
                async with pool.acquire() as conn:
                    # Direct update to append exchange_order_id and move to WAITING
                    await conn.execute('''
                        UPDATE pullback_limits 
                        SET status = 'WAITING', exchange_order_id = $1
                        WHERE id = $2
                    ''', exchange_order_id, item['id'])
                    
        except Exception as e:
            logger.error(f"Failed to dispatch limit order for {symbol}: {e}")
            # Do not change status so it retries, unless it's a fatal error
