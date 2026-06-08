import pytest
from services.engine.data_health import check_data_health, DataHealthLevel

def test_data_health_tiers():
    # Старые данные = HARD_BLOCK
    r = check_data_health({'open': 100, 'high': 101, 'low': 99, 'close': 100}, 0.1, 10_000_000, 36000, 1, 3600)
    assert r.level == DataHealthLevel.HARD_BLOCK
    
    # Немного устаревшие = SOFT_WARN
    r = check_data_health({'open': 100, 'high': 101, 'low': 99, 'close': 100}, 0.1, 10_000_000, 7500, 1, 3600)
    assert r.level == DataHealthLevel.SOFT_WARN
    assert r.position_size_multiplier == 0.5
    
    # OK = полный размер
    r = check_data_health({'open': 100, 'high': 101, 'low': 99, 'close': 100}, 0.05, 50_000_000, 30, 1, 3600)
    assert r.level == DataHealthLevel.OK
    assert r.position_size_multiplier == 1.0
