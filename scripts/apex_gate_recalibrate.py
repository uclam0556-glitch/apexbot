"""
APEX v11.0 — Gate Calibration Monitor
"""
import asyncio
import logging
from datetime import datetime
from database.timescaledb import get_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def check_gate_calibration_health():
    """
    Analyzes the V7 scores of recent shadow signals.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        records = await conn.fetch("""
            SELECT v7_score_raw, status 
            FROM signals 
            WHERE created_at >= NOW() - INTERVAL '48 hours'
        """)
        
        if not records:
            logger.info("No records in last 48h.")
            return
            
        scores = [r['v7_score_raw'] for r in records if r['v7_score_raw'] is not None]
        passed = sum(1 for r in records if r['status'] == 'APPROVED')
        blocked = len(records) - passed
        
        if not scores:
            logger.info("No v7 scores found.")
            return
            
        import numpy as np
        p95 = np.percentile(scores, 95)
        p50 = np.percentile(scores, 50)
        
        logger.info(f"--- APEX Gate Calibration Report ---")
        logger.info(f"Total Signals 48h: {len(scores)}")
        logger.info(f"Passed: {passed} | Blocked: {blocked}")
        logger.info(f"P95 Score: {p95:.1f} | P50 Score: {p50:.1f}")
        
        # Determine if gate is too strict
        pass_rate = passed / len(scores) * 100
        if pass_rate < 0.5:
            logger.warning(f"CRITICAL: Pass rate is {pass_rate:.2f}%. Gate may be too strict!")
            
        # Log to DB
        try:
            await conn.execute("""
                INSERT INTO gate_calibration_log 
                (time, v7_threshold, p95_v7_100, p95_v7_500, signals_passed, signals_blocked)
                VALUES (NOW(), 40.0, $1, $1, $2, $3)
            """, p95, passed, blocked)
        except Exception as e:
            logger.warning(f"Failed to insert gate log: {e}")

if __name__ == "__main__":
    asyncio.run(check_gate_calibration_health())
