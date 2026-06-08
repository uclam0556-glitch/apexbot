"""
APEX v11.0 — Data Health Tiers
"""
from dataclasses import dataclass
from enum import Enum

class DataHealthLevel(Enum):
    OK         = "OK"
    SOFT_WARN  = "SOFT_WARN"    # торговать с 50% размером
    HARD_BLOCK = "HARD_BLOCK"   # не торговать

@dataclass  
class DataHealthResult:
    level:        DataHealthLevel
    failed_checks: list[str]
    position_size_multiplier: float  # 1.0, 0.5, или 0.0
    detail:       str

def check_data_health(ohlcv_row: dict,
                      spread_pct: float,
                      volume_24h_usd: float,
                      last_update_age_sec: float,
                      asset_tier: int = 1,
                      timeframe_seconds: int = 3600) -> DataHealthResult:
    '''
    Tiered data health check.
    
    HARD_BLOCK triggers (любое из):
      - last_update_age > 5 * timeframe_seconds  (совсем старые данные)
      - volume_24h_usd < 200_000  (неликвид - снижено с 500k по Fix 6)
      - OHLCV integrity fail: H < max(O,C) или L > min(O,C)
      - return > 20% за 1h (вероятно ошибка данных)
    
    SOFT_WARN triggers (любое из, если не HARD_BLOCK):
      - last_update_age > 2 * timeframe_seconds  (немного устаревшие)
      - spread_pct > tier_max_spread * 1.5       (широкий спред)
      - volume_zscore > 3.5 (аномальный объём) - пока опускаем zscore, так как его нет во входе
      - volume_24h_usd < 2_000_000 (низкая ликвидность)
    '''
    failed_checks = []
    
    # Check OHLCV integrity
    o = ohlcv_row.get('open', 0)
    h = ohlcv_row.get('high', 0)
    l = ohlcv_row.get('low', 0)
    c = ohlcv_row.get('close', 0)
    
    integrity_fail = False
    if h < max(o, c) or l > min(o, c):
        integrity_fail = True
        failed_checks.append("OHLCV Integrity")
        
    return_1h = 0
    if o > 0:
        return_1h = abs(c - o) / o * 100
    if return_1h > 20.0:
        failed_checks.append("Return > 20%")
        
    if last_update_age_sec > 5 * timeframe_seconds:
        failed_checks.append("Data > 5x timeframe")
        
    if volume_24h_usd < 200_000:
        failed_checks.append("Volume < 200k USD")
        
    if failed_checks:
        return DataHealthResult(
            level=DataHealthLevel.HARD_BLOCK,
            failed_checks=failed_checks,
            position_size_multiplier=0.0,
            detail=f"HARD_BLOCK: {', '.join(failed_checks)}"
        )
        
    # SOFT WARN Checks
    soft_checks = []
    
    if last_update_age_sec > 2 * timeframe_seconds:
        soft_checks.append("Data > 2x timeframe")
        
    # max spread by tier: tier 1 = 0.05%, tier 2 = 0.15%, tier 3 = 0.40%
    tier_max_spread = 0.05 if asset_tier == 1 else (0.15 if asset_tier == 2 else 0.40)
    if spread_pct > tier_max_spread * 1.5:
        soft_checks.append("Spread > 1.5x max")
        
    if volume_24h_usd < 2_000_000:
        soft_checks.append("Volume < 2M USD")
        
    if soft_checks:
        return DataHealthResult(
            level=DataHealthLevel.SOFT_WARN,
            failed_checks=soft_checks,
            position_size_multiplier=0.5,
            detail=f"SOFT_WARN: {', '.join(soft_checks)}"
        )
        
    return DataHealthResult(
        level=DataHealthLevel.OK,
        failed_checks=[],
        position_size_multiplier=1.0,
        detail="OK"
    )
