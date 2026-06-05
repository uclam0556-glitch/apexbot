"""
APEX v11.0 — core/circuit_breaker.py
=====================================
DEPRECATED SHIM → replaced by PortfolioRiskEngine circuit breakers.

The new institutional circuit breaker logic lives in:
    services/risk/portfolio_risk_engine.py → CircuitBreakerLevel

This class is preserved ONLY for backward compatibility with legacy
call sites in main.py (self.circuit_breaker.update_pnl / .check()).

The old class had critical design flaws:
- PnL was stored as volatile in-memory state (lost on restart).
- Drawdown thresholds were magic numbers without academic justification.
- Weekly P&L was never actually tracked (the field existed but wasn't maintained).
- No VaR/CVaR integration.

Migration status:
- main.py uses self.circuit_breaker.update_pnl() and .check()
- TODO(v11.1): Replace with PortfolioRiskEngine.size_position() circuit gates

DO NOT use this in new code. DO NOT extend this class.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    LEGACY: In-memory drawdown circuit breaker.
    
    DO NOT extend. Migrate to PortfolioRiskEngine.
    All new circuit-breaker decisions must go through PortfolioRiskEngine.
    """

    # Legacy thresholds maintained for backward compatibility.
    # In PortfolioRiskEngine these are 8%, 15%, 25% — more conservative.
    LIMITS = {
        'DAILY_HARD': -5.0,
        'DAILY_SOFT': -3.0,
        'WEEKLY_HARD': -10.0,
    }

    def __init__(self) -> None:
        self.daily_pnl_pct: float = 0.0
        self.weekly_pnl_pct: float = 0.0
        self.lock_until = None
        self.soft_lock: bool = False
        logger.info(
            "[CircuitBreaker] Legacy circuit breaker initialized. "
            "NOTE: Migrate to PortfolioRiskEngine in v11.1."
        )

    def update_pnl(self, realized_pnl_pct: float, floating_pnl_pct: float) -> None:
        total = realized_pnl_pct + floating_pnl_pct
        self.daily_pnl_pct = total

    def check(self) -> dict:
        """Returns legacy-format {'allowed': bool, 'action': str, 'reason': str}."""
        now = datetime.now(timezone.utc)
        if self.lock_until and now < self.lock_until:
            return {'allowed': False, 'action': 'HALT', 'reason': 'CIRCUIT_BREAKER_ACTIVE'}

        if self.lock_until and now >= self.lock_until:
            self.lock_until = None
            self.daily_pnl_pct = 0.0
            self.soft_lock = False

        if self.daily_pnl_pct <= self.LIMITS['DAILY_HARD']:
            return {'allowed': False, 'action': 'TRIGGER_HARD', 'reason': 'DAILY_HARD_LIMIT_REACHED'}

        if self.weekly_pnl_pct <= self.LIMITS['WEEKLY_HARD']:
            return {'allowed': False, 'action': 'TRIGGER_HARD_WEEKLY', 'reason': 'WEEKLY_HARD_LIMIT_REACHED'}

        if self.daily_pnl_pct <= self.LIMITS['DAILY_SOFT']:
            self.soft_lock = True
            return {'allowed': False, 'action': 'SOFT_LOCK', 'reason': 'DAILY_SOFT_LIMIT_REACHED'}

        return {'allowed': True, 'action': 'PROCEED', 'reason': 'OK'}
