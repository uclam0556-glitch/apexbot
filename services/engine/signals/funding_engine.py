"""
APEX Trading System v4.0
services/engine/signals/funding_engine.py

Analyzes Binance Futures Funding Rates and Open Interest to detect leverage traps.
"""

from dataclasses import dataclass
import structlog

logger = structlog.get_logger(__name__)

@dataclass
class FundingSignal:
    bias: str           # "EXTREME_LONG", "HIGH_LONG", "NEUTRAL", "SHORT_BIAS", "EXTREME_SHORT"
    funding_rate: float # Percentage
    score_modifier: float # Value to add/subtract from V7 Score
    
class FundingRateEngine:
    """
    Evaluates Funding Rate and Open Interest to score Liquidation Risk.
    Positive funding = Longs pay Shorts (retail is highly levered long).
    Negative funding = Shorts pay Longs (retail is heavily shorting).
    """
    
    THRESHOLDS = {
        "extreme_long_bias":  0.10,   # >= 0.10% per 8h
        "high_long_bias":     0.05,   # >= 0.05% per 8h
        "neutral":            0.01,   # Base rate
        "short_bias":        -0.03,   # <= -0.03%
        "extreme_short":     -0.07,   # <= -0.07%
    }
    
    def evaluate(
        self, 
        funding_rate: float, 
        oi_change_1h: float = 0.0,
        cvd_signal: str = "NEUTRAL",
        price_rejection: bool = False
    ) -> FundingSignal:
        
        # Funding is extreme, but we only give the massive bonus if there's confluence
        if funding_rate >= self.THRESHOLDS["extreme_long_bias"]:
            if cvd_signal == "BEARISH" and oi_change_1h > 1.0 and price_rejection:
                logger.info("funding_extreme_long_confluence", rate=funding_rate, oi_change=oi_change_1h)
                return FundingSignal("EXTREME_LONG", funding_rate, 15.0)
            else:
                # Just a very crowded market, no breakdown yet
                return FundingSignal("HIGH_LONG", funding_rate, 5.0)
            
        elif funding_rate >= self.THRESHOLDS["high_long_bias"]:
            score = 3.0 if oi_change_1h > 0 else 0.0
            return FundingSignal("HIGH_LONG", funding_rate, score)
            
        elif funding_rate <= self.THRESHOLDS["extreme_short"]:
            # Retail is dangerously short. DO NOT SHORT.
            logger.warning("funding_extreme_short", rate=funding_rate)
            return FundingSignal("EXTREME_SHORT", funding_rate, -20.0)
            
        elif funding_rate <= self.THRESHOLDS["short_bias"]:
            return FundingSignal("SHORT_BIAS", funding_rate, -10.0)
            
        return FundingSignal("NEUTRAL", funding_rate, 0.0)

funding_engine = FundingRateEngine()
