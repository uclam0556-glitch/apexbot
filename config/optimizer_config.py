"""
APEX Trading System v10.5
config/optimizer_config.py
"""

import logging
from database.timescaledb import get_pool

logger = logging.getLogger(__name__)

OPTIMIZER_CONFIG = {
    # КРИТИЧНО: заморозить на весь период первичного сбора данных
    'WEIGHTS_OPTIMIZER_LOCKED': True,
    'ISOTONIC_CALIBRATION_LOCKED': True,

    # Разморозить только после накопления:
    'MIN_TRADES_FOR_WEIGHTS_UNLOCK': 300,
    'MIN_TRADES_FOR_ISOTONIC_UNLOCK': 500,

    # Текущие фиксированные веса V7 (не менять до разморозки):
    'V7_WEIGHTS_FROZEN': {
        'rsi': 1.0,           # базовый вес (динамический множитель отключен)
        'cvd': 1.0,           # базовый вес
        'fvg_alignment': 1.0,
        'market_breadth': 1.0,
        'ofi': 1.0,
        'btc_dominance': 1.0,
        'overextension_penalty': 1.0,
        'structural_chop_penalty': 1.0,
        'risk_off_penalty': 1.0,
    },

    # Порог входа — также фиксирован:
    'V7_ENTRY_THRESHOLD_NORMAL': 45.0,
    'V7_ENTRY_THRESHOLD_RISK_OFF': 52.0,
}

async def check_optimizer_lock():
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            shadow_count = await conn.fetchval(
                "SELECT COUNT(*) FROM shadow_trades WHERE outcome IS NOT NULL AND logic_version = '10.5.0'"
            )
            
        if shadow_count is not None and shadow_count >= OPTIMIZER_CONFIG['MIN_TRADES_FOR_WEIGHTS_UNLOCK']:
            logger.warning(f"OPTIMIZER UNLOCK AVAILABLE: {shadow_count} trades collected. "
                          f"Manual review required before enabling. Run: apex optimize --review")
        # НЕ разблокировать автоматически — только после ручного подтверждения
    except Exception as e:
        logger.error(f"Failed to check optimizer lock status: {e}")
