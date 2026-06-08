import pytest
from services.engine.universe import DynamicUniverse

def test_universe():
    # Blacklisted возвращают 0.0 multiplier
    u = DynamicUniverse(None)
    assert u.get_priority_multiplier('RUNE/USDT') == 0.0
    assert u.get_priority_multiplier('COMP/USDT') == 1.2
    assert u.get_priority_multiplier('SOL/USDT')  == 1.0
