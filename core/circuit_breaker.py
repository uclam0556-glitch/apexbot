"""
APEX Trading System v10.5
core/circuit_breaker.py
"""

from datetime import datetime, timezone

class CircuitBreaker:
    """
    Система защиты от катастрофических потерь.
    Работает на уровне всего портфеля/алгоритма.
    """

    LIMITS = {
        'DAILY_HARD': -5.0,     # % от баланса. Стоп на 24 часа.
        'DAILY_SOFT': -3.0,     # Только закрытие текущих сделок, новые не открываются.
        'WEEKLY_HARD': -10.0,   # Остановка до конца недели + алерт.
    }

    def __init__(self):
        self.daily_pnl_pct = 0.0
        self.weekly_pnl_pct = 0.0
        self.lock_until = None
        self.soft_lock = False

    def update_pnl(self, realized_pnl_pct: float, floating_pnl_pct: float):
        total_pnl = realized_pnl_pct + floating_pnl_pct
        self.daily_pnl_pct = total_pnl
        # weekly logic would accumulate daily

    def check(self) -> dict:
        """
        Returns: {'allowed': bool, 'action': str, 'reason': str}
        """
        now = datetime.now(timezone.utc)
        if self.lock_until and now < self.lock_until:
            return {'allowed': False, 'action': 'HALT', 'reason': 'CIRCUIT_BREAKER_ACTIVE'}

        # Очистка лока если срок вышел
        if self.lock_until and now >= self.lock_until:
            self.lock_until = None
            self.daily_pnl_pct = 0.0 # reset for new day
            self.soft_lock = False

        if self.daily_pnl_pct <= self.LIMITS['DAILY_HARD']:
            # trigger hard stop (needs external time mechanism to set lock_until properly)
            return {'allowed': False, 'action': 'TRIGGER_HARD', 'reason': 'DAILY_HARD_LIMIT_REACHED'}
            
        if self.weekly_pnl_pct <= self.LIMITS['WEEKLY_HARD']:
             return {'allowed': False, 'action': 'TRIGGER_HARD_WEEKLY', 'reason': 'WEEKLY_HARD_LIMIT_REACHED'}

        if self.daily_pnl_pct <= self.LIMITS['DAILY_SOFT']:
            self.soft_lock = True
            return {'allowed': False, 'action': 'SOFT_LOCK', 'reason': 'DAILY_SOFT_LIMIT_REACHED'}

        return {'allowed': True, 'action': 'PROCEED', 'reason': 'OK'}
