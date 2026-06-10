"""
Helper to fetch auto-tuner active parameters
"""

from database.timescaledb import get_pool
import structlog

logger = structlog.get_logger(__name__)

async def get_active_tuning_params() -> dict:
    """
    Fetches all auto-tuner recommendations where applied = TRUE.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            query = "SELECT parameter_name, recommended_value FROM auto_tuner_recommendations WHERE applied = TRUE"
            records = await conn.fetch(query)
            return {r['parameter_name']: float(r['recommended_value']) for r in records}
    except Exception as e:
        # Table might not exist yet if auto_tuner hasn't run
        return {}
