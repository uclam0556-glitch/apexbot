"""
APEX v11.0 — Dynamic V7 Gate
"""
import numpy as np

def compute_dynamic_gate(breadth_index: float,
                         v7_history: list[float],
                         base_gate: float = 40.0) -> float:
    """
    Dynamic gate instead of a hardcoded 48.0 threshold.
    
    Logic:
    - Base gate: 40.0
    - Breadth adjustment:
        breadth > 60% → gate = base_gate
        breadth 40-60% → gate = base + 2.0
        breadth 20-40% → gate = base + 5.0
        breadth < 20%  → gate = base + 10.0
    - Percentile adjustment:
        If 95th percentile of recent V7 history < gate:
        → gate = max(p95_v7 - 2.0, base_gate - 5.0)
    """
    if breadth_index > 60.0:
        gate = base_gate
    elif breadth_index >= 40.0:
        gate = base_gate + 2.0
    elif breadth_index >= 20.0:
        gate = base_gate + 5.0
    else:
        gate = base_gate + 10.0
        
    if v7_history and len(v7_history) > 0:
        p95 = np.percentile(v7_history, 95)
        if p95 < gate:
            gate = max(p95 - 2.0, base_gate - 5.0)
            
    return round(float(gate), 2)
