"""
APEX Trading System v4.0
Execution Learning Loop.

Analyzes past trade executions to optimize routing:
- Slippage tracking
- TWAP vs Market performance
- Fill rates and maker/taker fee optimization
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from shared.database import execute_ch_async
from shared.models import (
    ExecutionMetrics,
    ExecutionRoute,
    OrderExecutionType,
)

logger = logging.getLogger(__name__)


class ExecutionLearningLoop:
    """
    Learns the optimal execution strategy based on historical slippage and fill rates.
    """

    async def record_execution(self, metrics: ExecutionMetrics) -> None:
        """
        Saves execution metrics to ClickHouse for future learning.
        """
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
        try:
            await execute_ch_async(query, {
                "order_id": metrics.order_id,
                "symbol": metrics.symbol,
                "execution_type": metrics.execution_type.value,
                "intended_price": metrics.intended_price,
                "avg_fill_price": metrics.avg_fill_price,
                "slippage_bps": metrics.slippage_bps,
                "fees": metrics.total_fees_usd,
                "fill_rate": metrics.fill_rate_pct,
                "exec_time": metrics.execution_time_ms,
                "depth": metrics.orderbook_depth_usd
            })
            logger.info(f"Recorded execution metrics for order {metrics.order_id} (Slippage: {metrics.slippage_bps} bps)")
        except Exception as e:
            logger.error(f"Failed to record execution metrics: {e}")

    async def get_optimal_route(
        self, symbol: str, size_usd: float, urgency: str
    ) -> ExecutionRoute:
        """
        Determines the optimal execution route (Market, TWAP, Limit) based on past data.
        """
        # In production: query ClickHouse for average slippage by execution type
        # where orderbook_depth is similar to current.
        
        # Heuristics + mock historical lookup
        if urgency == "CRITICAL":
            # Must enter/exit NOW. E.g. Stop Loss, or Post-Cascade Reversal.
            return ExecutionRoute(
                execution_type=OrderExecutionType.MARKET,
                reasoning="Urgency is CRITICAL. Prioritizing guaranteed fill over slippage.",
                expected_slippage_bps=15.0,
                recommended_chunks=1
            )
            
        if size_usd < 50_000:
            # Small size, market order is fine unless spreads are wide
            return ExecutionRoute(
                execution_type=OrderExecutionType.MARKET,
                reasoning="Size is small. Market order cost is negligible.",
                expected_slippage_bps=2.0,
                recommended_chunks=1
            )
            
        # Large size -> Need TWAP or smart routing
        if urgency == "LOW":
            # E.g. taking profit 1 slowly
            return ExecutionRoute(
                execution_type=OrderExecutionType.TWAP,
                reasoning="Large size + Low urgency. TWAP minimizes market impact.",
                expected_slippage_bps=1.5,
                recommended_chunks=int(size_usd // 10_000) or 2
            )
            
        # Medium urgency, large size
        return ExecutionRoute(
            execution_type=OrderExecutionType.TWAP,
            reasoning="Large size. Accelerated TWAP recommended.",
            expected_slippage_bps=5.0,
            recommended_chunks=int(size_usd // 25_000) or 2
        )

    async def detect_toxic_flow(self, symbol: str) -> bool:
        """
        Checks if recent limit orders are getting run over (toxic flow/adverse selection).
        """
        # Mock ClickHouse query: 
        # SELECT avg(slippage_bps) FROM execution_metrics 
        # WHERE execution_type='LIMIT' AND created_at > now() - 1h
        recent_limit_slippage_bps = 5.0 # mock
        
        if recent_limit_slippage_bps > 10.0:
            logger.warning(f"Toxic flow detected on {symbol}. Limit orders are suffering adverse selection.")
            return True
            
        return False
