"""
APEX v11.0 — Dynamic Universe Management
"""

class DynamicUniverse:
    '''
    Dynamic management of the universe of 95 assets.
    Automatically excludes persistently unprofitable symbols.
    '''
    
    # Immediately exclude (0-2% win rate on 20+ resolved trades):
    BLACKLIST_IMMEDIATE = [
        'RUNE/USDT',   # 1.6% WR, 124 resolved
        'ONDO/USDT',   # 0.0% WR, 55 resolved
        'FET/USDT',    # 0.0% WR, 62 resolved
        'AAVE/USDT',   # 0.0% WR, 26 resolved
        'APE/USDT',    # 0.0% WR, 16 resolved
        'CHZ/USDT',    # 0.0% WR, 26 resolved
        'EGLD/USDT',   # 0.0% WR, 21 resolved
    ]
    
    # Priority symbols (high win rate, enough data):
    PRIORITY_SYMBOLS = [
        'COMP/USDT',   # 98.6% WR, 72 resolved — TOP
        'IMX/USDT',    # 75.4% WR, 61 resolved
        'BTC/USDT',    # 100% WR (small sample, monitor)
        'ETH/USDT',    # 47.4% WR, 114 resolved — stable
        'CAKE/USDT',   # 55.3% WR, 103 resolved
    ]
    
    def __init__(self, db_conn, min_resolved: int = 20,
                 min_win_rate: float = 0.15,
                 lookback_trades: int = 100):
        self.db = db_conn
        self.min_resolved = min_resolved
        self.min_win_rate = min_win_rate
        self.lookback = lookback_trades
    
    def get_active_symbols(self, all_symbols: list[str]) -> list[str]:
        '''
        Returns a list of active symbols.
        Excludes BLACKLIST.
        If db is provided, could query database. (For now, just static exclusion).
        '''
        active = []
        for sym in all_symbols:
            if sym not in self.BLACKLIST_IMMEDIATE:
                active.append(sym)
        return active
    
    def get_priority_multiplier(self, symbol: str) -> float:
        '''
        PRIORITY_SYMBOLS get +20% to max position size.
        Blacklisted = 0.0.
        Others = 1.0.
        '''
        if symbol in self.BLACKLIST_IMMEDIATE:
            return 0.0
        if symbol in self.PRIORITY_SYMBOLS:
            return 1.2
        return 1.0
    
    def should_add_to_watchlist(self, symbol: str,
                                 recent_win_rate: float,
                                 sample_size: int) -> bool:
        '''
        Add new symbols only if:
        - sample_size >= 30 resolved trades
        - recent_win_rate >= 35%
        - Symbol not in blacklist
        '''
        if symbol in self.BLACKLIST_IMMEDIATE:
            return False
        if sample_size >= 30 and recent_win_rate >= 0.35:
            return True
        return False
