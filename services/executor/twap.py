"""
APEX Trading System v4.0
TWAP (Time-Weighted Average Price) Engine.

Executes large orders incrementally over time to minimize market impact.
Integrates with the Execution Learning Loop for dynamic pacing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import ccxt.async_support as ccxt

from shared.models import ExecutionMetrics, OrderExecutionType

logger = logging.getLogger(__name__)


class TWAPEngine:
    """
    Splits large orders into smaller chunks and executes them over a time window.
    """

    def __init__(self, exchange: ccxt.Exchange) -> None:
        self.exchange = exchange

    async def execute_twap(
        self,
        symbol: str,
        side: str, # "buy" or "sell"
        total_amount: float,
        chunks: int,
        duration_minutes: int,
        order_id: str
    ) -> ExecutionMetrics:
        """
        Executes a TWAP order.
        """
        if chunks < 2:
            logger.warning(f"TWAP requested with {chunks} chunks. Falling back to 1 (Market).")
            return await self._execute_market(symbol, side, total_amount, order_id)

        chunk_amount = total_amount / chunks
        sleep_interval = (duration_minutes * 60) / chunks
        
        logger.info(f"Starting TWAP for {symbol}: {total_amount} {side} over {duration_minutes}m in {chunks} chunks.")
        
        fills = []
        total_cost = 0.0
        total_fees = 0.0
        start_time = datetime.utcnow()
        
        # Get starting price for slippage calculation
        ticker = await self.exchange.fetch_ticker(symbol)
        intended_price = ticker["ask"] if side == "buy" else ticker["bid"]

        for i in range(chunks):
            try:
                # Place market order for chunk
                # In a more advanced implementation, we would place limit orders at the bid/ask
                # and fall back to market if unfilled after N seconds.
                order = await self.exchange.create_market_order(symbol, side, chunk_amount)
                
                # Wait for fill details
                await asyncio.sleep(1) # Give exchange time to process
                order_info = await self.exchange.fetch_order(order['id'], symbol)
                
                filled = order_info.get("filled", 0.0)
                cost = order_info.get("cost", 0.0)
                fee = order_info.get("fee", {}).get("cost", 0.0)
                
                fills.append(filled)
                total_cost += cost
                total_fees += fee
                
                logger.debug(f"TWAP Chunk {i+1}/{chunks} filled. Amount: {filled}, Avg Price: {order_info.get('average')}")
                
            except Exception as e:
                logger.error(f"TWAP Chunk {i+1}/{chunks} failed: {e}")
                
            if i < chunks - 1:
                await asyncio.sleep(sleep_interval)

        end_time = datetime.utcnow()
        execution_time_ms = int((end_time - start_time).total_seconds() * 1000)
        
        total_filled = sum(fills)
        avg_fill_price = total_cost / total_filled if total_filled > 0 else 0.0
        
        # Calculate slippage
        if avg_fill_price > 0 and intended_price > 0:
            if side == "buy":
                slippage_pct = (avg_fill_price - intended_price) / intended_price
            else:
                slippage_pct = (intended_price - avg_fill_price) / intended_price
        else:
            slippage_pct = 0.0
            
        slippage_bps = round(slippage_pct * 10000, 2)
        fill_rate_pct = round((total_filled / total_amount) * 100, 2)

        metrics = ExecutionMetrics(
            order_id=order_id,
            symbol=symbol,
            execution_type=OrderExecutionType.TWAP,
            intended_price=intended_price,
            avg_fill_price=avg_fill_price,
            slippage_bps=slippage_bps,
            total_fees_usd=total_fees,
            fill_rate_pct=fill_rate_pct,
            execution_time_ms=execution_time_ms,
            orderbook_depth_usd=0.0 # Mock
        )
        
        logger.info(f"TWAP Complete. Fill rate: {fill_rate_pct}%. Slippage: {slippage_bps} bps.")
        return metrics

    async def _execute_market(self, symbol: str, side: str, amount: float, order_id: str) -> ExecutionMetrics:
        """Fallback to single market order."""
        start_time = datetime.utcnow()
        ticker = await self.exchange.fetch_ticker(symbol)
        intended_price = ticker["ask"] if side == "buy" else ticker["bid"]
        
        try:
            order = await self.exchange.create_market_order(symbol, side, amount)
            order_info = await self.exchange.fetch_order(order['id'], symbol)
            
            filled = order_info.get("filled", 0.0)
            avg_price = order_info.get("average", intended_price)
            fees = order_info.get("fee", {}).get("cost", 0.0)
            
        except Exception as e:
            logger.error(f"Market fallback failed: {e}")
            filled, avg_price, fees = 0.0, 0.0, 0.0
            
        execution_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        if avg_price > 0 and intended_price > 0:
            if side == "buy":
                slippage_pct = (avg_price - intended_price) / intended_price
            else:
                slippage_pct = (intended_price - avg_price) / intended_price
        else:
            slippage_pct = 0.0

        return ExecutionMetrics(
            order_id=order_id,
            symbol=symbol,
            execution_type=OrderExecutionType.MARKET,
            intended_price=intended_price,
            avg_fill_price=avg_price,
            slippage_bps=round(slippage_pct * 10000, 2),
            total_fees_usd=fees,
            fill_rate_pct=round((filled / amount) * 100, 2) if amount > 0 else 0.0,
            execution_time_ms=execution_time_ms,
            orderbook_depth_usd=0.0
        )
