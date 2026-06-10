"""
APEX Root Cause Analysis & Database Queries
Fetches shadow trade performance grouped by block reasons and parameters.
"""

from typing import List, Dict, Any
from database.timescaledb import get_pool
import pandas as pd

async def get_shadow_trades_for_calibration(days: int = 7) -> pd.DataFrame:
    """
    Fetches completed shadow trades from the database for the last N days.
    Returns a DataFrame suitable for auto-tuning simulation.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = f"""
        SELECT 
            s.id,
            s.created_at,
            s.symbol,
            s.strategy,
            s.direction,
            s.entry_price,
            s.sl_price as stop_loss,
            s.tp1_price as take_profit_1,
            s.tp2_price as take_profit_2,
            s.tp3_price as take_profit_3,
            s.v7_score_raw as v7_score,
            s.block_reason,
            st.outcome as status,
            st.mfe_pct,
            st.mae_pct,
            s.regime
        FROM signals s
        JOIN shadow_trades_blocked st ON s.id = st.signal_id
        WHERE st.outcome IN ('WON', 'LOST', 'BREAKEVEN', 'TIMEOUT') 
          AND s.created_at >= NOW() - INTERVAL '{days} days'
        """
        records = await conn.fetch(query)
        if not records:
            return pd.DataFrame()
            
        data = [dict(r) for r in records]
        return pd.DataFrame(data)

async def get_filter_performance_stats() -> List[Dict[str, Any]]:
    """
    Returns aggregated stats for each block reason (how many SLs saved, how many TPs missed).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = """
        WITH shadow_stats AS (
            SELECT 
                s.block_reason,
                COUNT(*) as total_blocked,
                SUM(CASE WHEN st.mfe_pct >= 1.5 THEN 1 ELSE 0 END) as tp_missed,
                SUM(CASE WHEN st.mae_pct <= -1.0 THEN 1 ELSE 0 END) as sl_saved
            FROM signals s
            JOIN shadow_trades st ON s.id = st.signal_id
            WHERE st.outcome NOT IN ('OPEN', 'TRACKING')
            GROUP BY s.block_reason
        )
        SELECT 
            block_reason,
            total_blocked,
            tp_missed,
            sl_saved,
            (sl_saved::float / GREATEST(tp_missed, 1)) as efficiency_ratio
        FROM shadow_stats
        ORDER BY total_blocked DESC;
        """
        records = await conn.fetch(query)
        return [dict(r) for r in records]
