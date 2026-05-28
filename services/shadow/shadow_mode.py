"""
APEX Trading System v4.0
Shadow Mode Framework.

Executes trades in a simulated environment using LIVE market data.
Accounts for real-time orderbook depth to calculate realistic slippage.
Essential for evaluating v4 models before allocating real capital.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from shared.database import execute_ch_async
from shared.models import FullSignalPackage, AIAuditResult, OrderExecutionType

logger = logging.getLogger(__name__)


class ShadowModeEngine:
    """
    Simulates execution on live data, calculating exact slippage via Orderbook depth.
    """

    async def execute_shadow_trade(
        self, package: FullSignalPackage, audit: AIAuditResult, current_orderbook: dict[str, Any]
    ) -> None:
        """
        Executes a paper trade with realistic orderbook impact.
        """
        if not audit.approved:
            return

        sig = package.signal
        symbol = sig.symbol
        side = "buy" if sig.direction.value == "LONG" else "sell"
        
        # Calculate size
        risk_pct = sig.risk_pct * audit.parameter_adjustments.position_size_multiplier
        balance_usd = 10000.0 # Fixed paper balance
        position_usd = balance_usd * (risk_pct / 100)
        
        # Determine exact fill price based on orderbook depth
        book_side = current_orderbook["asks"] if side == "buy" else current_orderbook["bids"]
        
        filled_amount_usd = 0.0
        avg_price = 0.0
        total_crypto = 0.0
        
        if not book_side:
            logger.warning(f"[SHADOW] No orderbook data for {symbol}. Using naive mid-price.")
            mid_price = (package.signal.entry_low + package.signal.entry_high) / 2
            avg_price = mid_price
        else:
            # Walk the book
            for level in book_side:
                # Level format: [price, amount] (ccxt format) or an object
                price = float(level[0]) if isinstance(level, list) else float(level.price)
                amount_crypto = float(level[1]) if isinstance(level, list) else float(level.size)
                
                level_usd = price * amount_crypto
                
                if filled_amount_usd + level_usd >= position_usd:
                    # Partial fill at this level to complete order
                    remaining_usd = position_usd - filled_amount_usd
                    remaining_crypto = remaining_usd / price
                    
                    total_crypto += remaining_crypto
                    filled_amount_usd += remaining_usd
                    break
                else:
                    # Full level fill
                    total_crypto += amount_crypto
                    filled_amount_usd += level_usd

            if total_crypto > 0:
                avg_price = filled_amount_usd / total_crypto
            else:
                avg_price = (package.signal.entry_low + package.signal.entry_high) / 2

        # Record to ClickHouse
        order_id = f"shadow_{uuid.uuid4().hex[:8]}"
        
        query = """
            INSERT INTO execution_metrics (
                order_id, symbol, execution_type, intended_price, avg_fill_price,
                slippage_bps, total_fees_usd, fill_rate_pct, execution_time_ms,
                orderbook_depth_usd, created_at
            ) VALUES (
                %(order_id)s, %(symbol)s, %(execution_type)s, %(intended_price)s, %(avg_fill_price)s,
                %(slippage_bps)s, %(fees)s, %(fill_rate)s, %(exec_time)s, %(depth)s, now()
            )
        """
        
        best_price = float(book_side[0][0] if isinstance(book_side[0], list) else book_side[0].price) if book_side else avg_price
        slippage_pct = abs(avg_price - best_price) / best_price
        
        try:
            await execute_ch_async(query, {
                "order_id": order_id,
                "symbol": symbol,
                "execution_type": "SHADOW_" + (audit.parameter_adjustments.execution_type_override.value if audit.parameter_adjustments.execution_type_override else "MARKET"),
                "intended_price": best_price,
                "avg_fill_price": avg_price,
                "slippage_bps": round(slippage_pct * 10000, 2),
                "fees": position_usd * 0.0004, # 0.04% maker/taker avg fee
                "fill_rate": 100.0,
                "exec_time": 10, # ms
                "depth": position_usd
            })
            logger.info(f"[SHADOW] Executed {side.upper()} {symbol}. Avg Price: {avg_price:.2f} (Slippage: {slippage_pct*10000:.1f} bps)")
        except Exception as e:
            logger.error(f"[SHADOW] DB Error: {e}")
