# shared/state.py
class SystemState:
    last_scan_time: str = "Еще не было"
    current_symbol: str = "Ожидание..."
    regime: str = "Ожидание..."
    
global_state = SystemState()
