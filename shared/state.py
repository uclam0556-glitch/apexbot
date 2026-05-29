# shared/state.py — Global State for APEX v5.0
from typing import List


class SystemState:
    last_scan_time: str = "Еще не было"
    current_symbol: str = "Ожидание..."
    regime: str = "Ожидание..."
    hot_coins: List[dict] = []
    signals_sent_today: int = 0
    scan_cycle_count: int = 0
    total_symbols: int = 40
    is_paused: bool = False
    live_prices: dict = {}
    trade_excursions: dict = {}

    def __init__(self):
        self.hot_coins = []


global_state = SystemState()
