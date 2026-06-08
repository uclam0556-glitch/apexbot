import pytest
from services.engine.dynamic_gate import compute_dynamic_gate

def test_dynamic_gate():
    # gate должен снизиться когда breadth низкий и V7 history низкий
    gate_low_breadth_low_v7 = compute_dynamic_gate(15.0, [20, 25, 30, 35, 40])
    assert gate_low_breadth_low_v7 < 48.0
    
    # gate не должен опускаться ниже 35.0 никогда
    gate_extreme_low = compute_dynamic_gate(0.0, [1, 2, 3, 4, 5])
    assert gate_extreme_low >= 35.0
    
    # при здоровом рынке gate = base (40.0)
    gate_healthy = compute_dynamic_gate(70.0, [50, 55, 60])
    assert gate_healthy == 40.0
