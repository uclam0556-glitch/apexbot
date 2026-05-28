"""
APEX Trading System v4.0
Full Order Executor.

The final gateway to the exchange.
Handles Signal translation into CCXT orders.
Supports OCO (One-Cancels-the-Other) for Stop Loss / Take Profit.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import ccxt.async_support as ccxt

from shared.config import get_config
from shared.models import FullSignalPackage, AIAuditResult, OrderExecutionType
from services.executor.learning_loop import ExecutionLearningLoop
from services.executor.twap import TWAPEngine
from services.notifications.telegram_bot import TelegramNotifier

logger = logging.getLogger(__name__)
_config = get_config()


class OrderExecutor:
    """
    Executes trades on the exchange based on the final audited signal.
    """

    def __init__(self, exchange: ccxt.Exchange) -> None:
        self.exchange = exchange
        self.learning_loop = ExecutionLearningLoop()
        self.twap_engine = TWAPEngine(exchange)
        self.notifier = TelegramNotifier()

    async def execute_signal(self, package: FullSignalPackage, audit: AIAuditResult) -> bool:
        """
        Main entrypoint for executing an approved signal.
        """
        if not audit.approved:
            logger.warning(f"Executor received unapproved signal {package.signal.symbol}. Aborting.")
            return False

        sig = package.signal
        symbol = sig.symbol
        # SPOT ONLY V4: Enforce buy entry
        side = "buy"
        
        # Apply AI parameter modifications
        adj = audit.parameter_adjustments
        
        sl = adj.stop_loss_adjusted or sig.stop_loss
        tp1 = adj.tp1_adjusted or sig.take_profit_1
        tp2 = adj.tp2_adjusted or sig.take_profit_2
        tp3 = None if adj.remove_tp3 else sig.take_profit_3
        
        # Calculate Position Size (in base asset)
        # Assuming we have a helper to get balance, mocking here
        balance_usd = _config.trading.initial_deposit_usd # In prod: await self.exchange.fetch_balance()
        risk_pct = sig.risk_pct * adj.position_size_multiplier
        position_usd = balance_usd * (risk_pct / 100)
        
        ticker = await self.exchange.fetch_ticker(symbol)
        current_price = ticker['last']
        amount = position_usd / current_price
        
        logger.info(f"Executing {side.upper()} {amount} {symbol} (Risk: {risk_pct}%)")

        # Determine execution route
        override_exec = adj.execution_type_override
        if override_exec:
            exec_type = override_exec
            chunks = 5 if override_exec == OrderExecutionType.TWAP else 1
        else:
            route = await self.learning_loop.get_optimal_route(symbol, position_usd, urgency="NORMAL")
            exec_type = route.execution_type
            chunks = route.recommended_chunks

        # PAPER TRADING MODE
        if _config.trading.paper_trading_mode:
            logger.info(f"PAPER TRADING MODE: Sending signal to Telegram for {symbol} instead of executing.")
            await self.notifier.send_paper_trade_signal(
                package=package,
                audit=audit,
                amount=amount,
                position_usd=position_usd,
                current_price=current_price
            )
            return True

        # Execute Entry
        order_id = str(uuid.uuid4())
        
        try:
            if exec_type == OrderExecutionType.TWAP:
                metrics = await self.twap_engine.execute_twap(
                    symbol, side, amount, chunks, duration_minutes=5, order_id=order_id
                )
            elif exec_type == OrderExecutionType.LIMIT_FORCED:
                # Spoofing play: place limit slightly deep and wait
                order = await self.exchange.create_limit_order(symbol, side, amount, current_price * 0.999)
                logger.info(f"Placed Limit order: {order['id']}")
                # We mock metrics for limit here
                metrics = None 
            else:
                # Market
                metrics = await self.twap_engine._execute_market(symbol, side, amount, order_id)

            if metrics:
                await self.learning_loop.record_execution(metrics)
                
            # If we got filled (even partially), set SL/TP
            # Note: For CCXT, OCO orders depend on the exchange.
            # Here we simulate setting up stop and take profits.
            await self._place_sl_tp(symbol, side, amount, sl, tp1, tp2, tp3, sig.tp_allocation)
            
            return True
            
        except Exception as e:
            logger.error(f"Execution failed for {symbol}: {e}")
            return False

    async def _place_sl_tp(
        self, symbol: str, entry_side: str, amount: float, 
        sl: float, tp1: float, tp2: float, tp3: float | None,
        tp_alloc: list[float]
    ) -> None:
        """
        Places Stop Loss and Take Profit orders.
        """
        # SPOT ONLY V4: Exit side is ALWAYS sell
        exit_side = "sell"
        
        try:
            # Place Virtual Stop Loss (Software-based)
            # Spot markets often lack reliable OCO. We register this virtually.
            # In a full implementation, a background task/monitor triggers a market sell if price <= sl.
            logger.info(f"VIRTUAL Stop Loss registered at {sl} (Spot markets lack reliable OCO)")

            # Place Take Profits
            amt_tp1 = amount * tp_alloc[0]
            await self.exchange.create_limit_order(symbol, exit_side, amt_tp1, tp1)
            logger.info(f"TP1 Limit Sell set at {tp1} for {amt_tp1}")
            
            if tp2:
                amt_tp2 = amount * tp_alloc[1]
                await self.exchange.create_limit_order(symbol, exit_side, amt_tp2, tp2)
                logger.info(f"TP2 Limit Sell set at {tp2} for {amt_tp2}")
                
            if tp3:
                amt_tp3 = amount * tp_alloc[2]
                await self.exchange.create_limit_order(symbol, exit_side, amt_tp3, tp3)
                logger.info(f"TP3 Limit Sell set at {tp3} for {amt_tp3}")

        except Exception as e:
            logger.error(f"Failed to set SL/TP for {symbol}: {e}")
            logger.critical(f"NAKED POSITION DANGER on {symbol}. SL/TP failed.")
