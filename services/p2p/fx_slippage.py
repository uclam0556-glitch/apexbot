"""
APEX Trading System v4.0
P2P FX-Slippage Model.

Calculates expected margin degradation during P2P execution due to:
1. FX drift (e.g. USDT/RUB dropping while we execute).
2. BTC volatility impact (if hedging on spot).
3. Time of day liquidity constraints.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from shared.database import get_redis
from shared.models import (
    AdjustedGrade,
    FXSlippageEstimate,
    OpportunityGrade,
)

logger = logging.getLogger(__name__)


class P2PFXSlippageModel:
    """
    Evaluates net profitability of P2P Arbitrage opportunities 
    adjusted for execution time and market volatility.
    """

    def __init__(self) -> None:
        self.default_fx_volatility_1h = 0.15 # Typical USDT/RUB 1h volatility %
        self.default_btc_volatility_1h = 1.2 # Typical BTC 1h volatility %

    async def get_fx_volatility_1h(self, pair: str = "USDT/RUB") -> float:
        """
        Gets current 1h realized volatility for the FX pair from Redis.
        Falls back to safe defaults.
        """
        try:
            redis = get_redis()
            val = await redis.get(f"fx_volatility_1h:{pair}")
            if val:
                return float(val)
        except Exception as e:
            logger.debug(f"Could not fetch FX vol from Redis, using default: {e}")
            
        if pair == "EUR/USD":
            return 0.05
        return self.default_fx_volatility_1h

    async def get_btc_volatility_1h(self) -> float:
        """Gets current BTC volatility from Redis."""
        try:
            redis = get_redis()
            val = await redis.get("btc_volatility_1h")
            if val:
                return float(val)
        except Exception:
            pass
        return self.default_btc_volatility_1h

    def estimate_fx_slippage(
        self,
        pair: str,
        execution_minutes: int,
        btc_volatility_1h: float,
        fx_volatility_1h: float,
        net_margin_current: float,
        time_of_day_msk: int
    ) -> FXSlippageEstimate:
        """
        Uses Square Root of Time rule to estimate volatility impact over execution window.
        """
        time_factor = math.sqrt(execution_minutes / 60.0)
        
        # 1. Expected FX drift
        fx_drift_expected = fx_volatility_1h * time_factor
        
        # 2. BTC hedging impact (assuming 30% correlation impact or execution delay)
        btc_impact = btc_volatility_1h * time_factor * 0.3
        
        total_baseline_slippage = fx_drift_expected + btc_impact
        
        # 3. Time of day liquidity multipliers (Moscow Time)
        # Peak liquidity = tighter spreads, faster execution
        if 19 <= time_of_day_msk <= 23:
            time_multiplier = 0.8 # Peak liquidity, 20% less slippage
        elif 0 <= time_of_day_msk <= 7:
            time_multiplier = 1.5 # Dead hours, 50% more slippage
        else:
            time_multiplier = 1.2 # Off-peak
            
        # Weekend penalty
        now = datetime.utcnow() # Note: strict implementation should use MSK tz
        if now.weekday() >= 5:
            time_multiplier *= 1.3
            
        adjusted_total_slippage = total_baseline_slippage * time_multiplier
        adjusted_net_margin = net_margin_current - adjusted_total_slippage
        
        # Grade the final margin
        if adjusted_net_margin >= 4.0:
            grade = OpportunityGrade.PREMIUM
        elif adjusted_net_margin >= 2.5:
            grade = OpportunityGrade.GOOD
        elif adjusted_net_margin >= 1.5:
            grade = OpportunityGrade.WEAK
        else:
            grade = OpportunityGrade.SKIP

        return FXSlippageEstimate(
            pair=pair,
            execution_minutes=execution_minutes,
            fx_drift_expected=round(fx_drift_expected, 3),
            btc_impact=round(btc_impact, 3),
            total_estimated_slippage=round(adjusted_total_slippage, 3),
            time_multiplier=round(time_multiplier, 2),
            adjusted_net_margin=round(adjusted_net_margin, 3),
            grade_adjusted=grade,
            computed_at=datetime.utcnow()
        )

    def get_optimal_execution_window(self, pair: str, required_margin: float) -> dict[str, Any]:
        """
        Returns the best time of day to execute a specific pair to guarantee the margin.
        """
        # Hardcoded priors for USDT/RUB
        return {
            "pair": pair,
            "best_hours_msk": [19, 20, 21, 22],
            "average_execution_time_mins": 45,
            "required_gross_margin": round(required_margin + 1.2, 2) # Assuming 1.2% typical slippage
        }

    def grade_with_slippage(self, gross_margin: float, slippage: FXSlippageEstimate) -> AdjustedGrade:
        """
        Helper to return the final grade and alert string for Telegram.
        """
        alert_str = f"Net margin with FX drift: {slippage.adjusted_net_margin:.2f}% (was {gross_margin:.2f}%)"
        
        return AdjustedGrade(
            final_grade=slippage.grade_adjusted,
            adjusted_margin=slippage.adjusted_net_margin,
            alert_string=alert_str
        )
