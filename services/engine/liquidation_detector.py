"""
APEX Trading System v4.0
Liquidation Cascade Detector.

Detects when the market is caught in a forced-liquidation spiral.
- CASCADE_IN_PROGRESS = Extreme danger (DO NOT ENTER)
- POST_CASCADE_REVERSAL = High-probability opportunity
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta

from shared.models import (
    LiquidationAnalysis,
    LiquidationStatus,
)

logger = logging.getLogger(__name__)


class LiquidationCascadeDetector:
    """
    Maintains a rolling buffer of liquidations to detect cascades.
    """
    def __init__(self) -> None:
        # symbol -> deque of (timestamp, size_usd, direction)
        self._buffers: dict[str, deque[tuple[datetime, float, str]]] = {}
        self._window_minutes = 60
        self._avg_window_minutes = 30

    def update_liquidation(
        self, symbol: str, size_usd: float, direction: str, timestamp: datetime
    ) -> None:
        if symbol not in self._buffers:
            self._buffers[symbol] = deque()
            
        self._buffers[symbol].append((timestamp, size_usd, direction))
        
        # Cleanup
        cutoff = timestamp - timedelta(minutes=self._window_minutes)
        while self._buffers[symbol] and self._buffers[symbol][0][0] < cutoff:
            self._buffers[symbol].popleft()

    def get_avg_liquidation_rate(self, symbol: str, window_minutes: int = 30) -> float:
        """Returns average liquidation USD per minute over the window."""
        if symbol not in self._buffers or not self._buffers[symbol]:
            return 0.0

        now = datetime.utcnow()
        cutoff = now - timedelta(minutes=window_minutes)
        
        total_usd = sum(
            usd for ts, usd, _ in self._buffers[symbol] if ts >= cutoff
        )
        return total_usd / window_minutes

    def is_cascade_in_progress(self, symbol: str) -> bool:
        """
        Check if current short-term rate (5m) is > 3x the medium-term (30m) average.
        """
        if symbol not in self._buffers:
            return False

        avg_30m = self.get_avg_liquidation_rate(symbol, 30)
        if avg_30m < 10_000: # Base noise level
            return False
            
        avg_5m = self.get_avg_liquidation_rate(symbol, 5)
        
        return avg_5m > (avg_30m * 3)

    def is_post_cascade_reversal(
        self, symbol: str, current_price: float, prices_last_10min: list[float]
    ) -> bool:
        """
        True if we were in a cascade recently, but price has stabilized.
        Stabilized = range < 0.3% in last 10 minutes.
        """
        # In a real system, we'd track historical cascade state.
        # Mocking check for stability:
        if not prices_last_10min:
            return False
            
        max_p = max(prices_last_10min)
        min_p = min(prices_last_10min)
        price_range_pct = (max_p - min_p) / min_p * 100
        
        return price_range_pct < 0.3

    def analyze_liquidation_cluster(
        self,
        symbol: str,
        liquidations_1h_usd: float,
        liquidations_direction: str,
        price_action: list[float],
        oi: float,
        oi_change_pct: float
    ) -> LiquidationAnalysis:
        """
        Full analysis for the Signal Engine.
        """
        cascade = self.is_cascade_in_progress(symbol)
        reversal = False
        
        if cascade:
            status = LiquidationStatus.CASCADE_IN_PROGRESS
            risk = "EXTREME"
            rec = "DO_NOT_ENTER"
            opp = None
        else:
            # Mock reversal check
            if oi_change_pct < -5.0 and self.is_post_cascade_reversal(symbol, price_action[-1] if price_action else 0, price_action[-10:]):
                status = LiquidationStatus.POST_CASCADE_REVERSAL
                risk = "LOW"
                rec = "ENTER_REVERSAL"
                opp = "REVERSAL"
                reversal = True
            elif liquidations_1h_usd > self.get_avg_liquidation_rate(symbol, 60) * 1.5 * 60:
                status = LiquidationStatus.ELEVATED
                risk = "MEDIUM"
                rec = "CAUTION"
                opp = None
            else:
                status = LiquidationStatus.NORMAL
                risk = "LOW"
                rec = "NORMAL"
                opp = None

        return LiquidationAnalysis(
            symbol=symbol,
            liquidations_1h_usd=round(liquidations_1h_usd, 2),
            liquidations_direction=liquidations_direction,
            risk_level=risk,
            status=status,
            opportunity_type=opp,
            recommended_action=rec,
            cascade_in_progress=cascade,
            post_cascade_reversal=reversal,
            computed_at=datetime.utcnow()
        )

    def get_cascade_adjustment(self, symbol: str) -> int:
        """
        Reduce confluence min by 1 if post-cascade reversal (strong opportunity).
        """
        # Mock historical check
        return -1 if False else 0
