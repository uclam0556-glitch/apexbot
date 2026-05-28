"""
APEX Trading System v4.0
Temporal Pattern Recognition Engine.

Analyzes time-based anomalies and historical biases:
- Day of Week bias
- FOMC meeting windows
- Bitcoin Halving cycle phase
- Monthly Options Expiry (Max Pain)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from shared.models import (
    DayOfWeekBias,
    FOMCBias,
    HalvingBias,
    TemporalBiasLabel,
    TemporalBiasResult,
)

logger = logging.getLogger(__name__)


class TemporalPatternEngine:
    """
    Evaluates temporal and calendar anomalies for Crypto.
    """
    
    # Known fixed dates (example for 2024-2026)
    FOMC_DATES = [
        datetime(2024, 1, 31, 19, 0), datetime(2024, 3, 20, 18, 0),
        datetime(2024, 5, 1, 18, 0), datetime(2024, 6, 12, 18, 0),
        datetime(2025, 1, 29, 19, 0), datetime(2025, 3, 19, 18, 0),
        datetime(2026, 3, 18, 18, 0), datetime(2026, 5, 6, 18, 0),
        datetime(2026, 6, 17, 18, 0), datetime(2026, 7, 29, 18, 0),
        datetime(2026, 9, 16, 18, 0), datetime(2026, 11, 4, 19, 0),
        datetime(2026, 12, 16, 19, 0)
    ]
    
    # Known halvings
    HALVING_DATES = [
        datetime(2012, 11, 28),
        datetime(2016, 7, 9),
        datetime(2020, 5, 11),
        datetime(2024, 4, 20),
        datetime(2028, 4, 20) # Approx
    ]

    def get_day_of_week_bias(self, symbol: str, direction: str, weekday: int) -> DayOfWeekBias:
        """
        Calculates historical bias for the given weekday (0=Monday, 6=Sunday).
        Uses known crypto priors (weekends often mean-revert, Mondays gap close).
        """
        # Priors for BTC LONG
        priors_long = {
            0: -0.31, # Mon
            1: 0.42,  # Tue
            2: 0.18,  # Wed
            3: 0.09,  # Thu
            4: -0.22, # Fri
            5: 0.35,  # Sat (low vol markup)
            6: -0.15  # Sun (dump before CME open)
        }
        
        hist_return = priors_long.get(weekday, 0.0)
        if direction == "SHORT":
            hist_return *= -1 # Inverse for short
            
        return DayOfWeekBias(
            weekday=weekday,
            historical_return_pct=hist_return,
            sample_size=104, # 2 years of weeks
            statistically_significant=abs(hist_return) > 0.2
        )

    def get_fomc_window_bias(self, now: datetime) -> FOMCBias:
        """
        Determines if we are in the danger zone around an FOMC rate decision.
        """
        future_fomc = [d for d in self.FOMC_DATES if d > now]
        past_fomc = [d for d in self.FOMC_DATES if d <= now]
        
        hours_to_next = (future_fomc[0] - now).total_seconds() / 3600 if future_fomc else 9999
        hours_since_last = (now - past_fomc[-1]).total_seconds() / 3600 if past_fomc else 9999
        
        caution = hours_to_next < 2.0
        pre_window = hours_to_next < 24.0
        post_boost = hours_since_last < 24.0
        
        adj = 0
        if caution:
            adj = 1 # Increase confluence required by 1
            
        return FOMCBias(
            hours_to_fomc=round(hours_to_next, 1),
            fomc_caution_active=caution,
            fomc_pre_window_active=pre_window,
            fomc_post_boost_active=post_boost,
            confluence_min_adjustment=adj
        )

    def get_halving_cycle_bias(self, now: datetime) -> HalvingBias:
        """
        Determines the current macro phase of the 4-year cycle.
        """
        past_halvings = [d for d in self.HALVING_DATES if d <= now]
        future_halvings = [d for d in self.HALVING_DATES if d > now]
        
        last_halving = past_halvings[-1] if past_halvings else datetime(2024, 4, 20)
        next_halving = future_halvings[0] if future_halvings else datetime(2028, 4, 20)
        
        days_since = (now - last_halving).days
        days_to = (next_halving - now).days
        
        score = 0.0
        if 0 <= days_since <= 90:
            phase = "POST_HALVING" # Usually sideways/choppy
            score = 0.0
        elif 90 < days_since <= 365:
            phase = "BULL_RUN" # Parabolic phase
            score = 0.6
        elif 90 < days_to <= 365:
            phase = "PRE_HALVING_EARLY" # Steady grind up
            score = 0.3
        elif 0 <= days_to <= 90:
            phase = "PRE_HALVING_LATE" # Volatile shakeouts
            score = -0.2
        else:
            phase = "NONE" # Bear market / Accumulation
            score = 0.0
            
        return HalvingBias(
            cycle_phase=phase,
            days_since_last=days_since,
            days_to_next=days_to,
            bias_score=score
        )

    def get_monthly_expiry_days(self, now: datetime) -> int:
        """
        Calculates days until the last Friday of the month (Deribit Expiry).
        Max Pain theory suggests price pins to max pain in the final 3 days.
        """
        # Find last day of month
        if now.month == 12:
            next_month = datetime(now.year + 1, 1, 1)
        else:
            next_month = datetime(now.year, now.month + 1, 1)
            
        last_day = next_month - timedelta(days=1)
        
        # Walk back to Friday (weekday() == 4)
        while last_day.weekday() != 4:
            last_day -= timedelta(days=1)
            
        # Set expiry time to 08:00 UTC
        expiry = datetime(last_day.year, last_day.month, last_day.day, 8, 0)
        
        if now > expiry:
            # We are past this month's expiry, find next month's
            return 30 # Rough estimate until properly calculated
            
        return max(0, (expiry - now).days)

    def get_combined_temporal_bias(self, symbol: str, direction: str) -> TemporalBiasResult:
        """
        Combines all temporal factors into a single score.
        """
        now = datetime.utcnow()
        
        dow = self.get_day_of_week_bias(symbol, direction, now.weekday())
        fomc = self.get_fomc_window_bias(now)
        halving = self.get_halving_cycle_bias(now)
        expiry_days = self.get_monthly_expiry_days(now)
        
        # Calculate raw score
        # DOW is in percentages (-0.3 to +0.4)
        # Halving is a score (-0.2 to +0.6)
        
        fomc_score = 0.0
        if fomc.fomc_caution_active:
            fomc_score = -2.0 # Extreme danger
        elif fomc.fomc_pre_window_active:
            fomc_score = -0.5 # Chop
        elif fomc.fomc_post_boost_active:
            fomc_score = 0.8 # Trend continuation
            
        expiry_score = 0.0
        if expiry_days <= 3:
            # Expiry magnet effect -> dampens trend following
            expiry_score = -0.5
            
        combined = (dow.historical_return_pct * 1.5) + (halving.bias_score * 1.0) + fomc_score + expiry_score
        
        if fomc.fomc_caution_active:
            label = TemporalBiasLabel.CAUTION
        elif combined > 1.5:
            label = TemporalBiasLabel.STRONG_POSITIVE
        elif combined > 0.5:
            label = TemporalBiasLabel.MILD_POSITIVE
        elif combined < -1.5:
            label = TemporalBiasLabel.STRONG_NEGATIVE
        elif combined < -0.5:
            label = TemporalBiasLabel.MILD_NEGATIVE
        else:
            label = TemporalBiasLabel.NEUTRAL

        return TemporalBiasResult(
            day_of_week_bias=dow,
            fomc_bias=fomc,
            halving_bias=halving,
            days_to_monthly_expiry=expiry_days,
            combined_score=round(combined, 2),
            label=label,
            computed_at=now
        )
