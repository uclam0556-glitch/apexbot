import pytest
from services.engine.trailing_stop import compute_trailing_stop

def test_trailing_stop():
    # Малое движение — не двигать SL
    # entry=100.0, sl=98.0, current_high=100.5, atr=1.0
    sl = compute_trailing_stop(100.0, 98.0, 100.5, 1.0, 'LONG')
    assert sl == 98.0  # не двигать при MFE < 1 ATR
    
    # Большое движение — Phase 3 (MFE = 3.5 ATR)
    sl = compute_trailing_stop(100.0, 98.0, 103.5, 1.0, 'LONG')
    assert sl > 98.0   # сдвинулся вверх
    assert sl < 103.5  # но не выше текущей цены
    assert sl == 102.5 # 103.5 - 1.0 = 102.5
