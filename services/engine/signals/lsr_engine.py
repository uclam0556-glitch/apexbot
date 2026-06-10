"""
APEX Trading System v4.0
services/engine/signals/lsr_engine.py

Analyzes Binance Futures Long/Short Account Ratio to detect retail crowding.
"""

from dataclasses import dataclass
import structlog

logger = structlog.get_logger(__name__)

@dataclass
class LSRSignal:
    bias: str           # "EXTREME_LONG", "HIGH_LONG", "NEUTRAL", "SHORT_BIAS", "EXTREME_SHORT"
    lsr: float          # Ratio (e.g. 2.5 means 2.5 long accounts for every 1 short account)
    score_modifier: float
    
class LongShortRatioEngine:
    """
    Evaluates Long/Short Account Ratio.
    High LSR = Retail is predominantly long. Smart Money is short.
    Low LSR = Retail is predominantly short. Smart Money is long.
    """
    
    THRESHOLDS = {
        "extreme_long": 3.0,   # > 3.0 = Insanely crowded long (Short signal)
        "high_long":    2.0,   # > 2.0 = Heavy long bias
        "high_short":   0.8,   # < 0.8 = Retail heavily shorting
        "extreme_short":0.5,   # < 0.5 = Insanely crowded short
    }
    
    def evaluate(
        self, 
        lsr: float,
        funding_bias: str = "NEUTRAL",
        mtf_score: float = 0.0,
        cvd_signal: str = "NEUTRAL"
    ) -> LSRSignal:
        
        if lsr >= self.THRESHOLDS["extreme_long"]:
            if funding_bias in ["EXTREME_LONG", "HIGH_LONG"] and mtf_score <= 0.0 and cvd_signal == "BEARISH":
                logger.info("lsr_extreme_long_confluence", lsr=lsr)
                return LSRSignal("EXTREME_LONG", lsr, 15.0)  # Confluence achieved
            else:
                return LSRSignal("HIGH_LONG", lsr, 3.0) # Just crowded
            
        elif lsr >= self.THRESHOLDS["high_long"]:
            return LSRSignal("HIGH_LONG", lsr, 2.0)
            
        elif lsr <= self.THRESHOLDS["extreme_short"]:
            logger.warning("lsr_extreme_short_block", lsr=lsr)
            return LSRSignal("EXTREME_SHORT", lsr, -25.0) # -25 to Short Score (Block shorts)
            
        elif lsr <= self.THRESHOLDS["high_short"]:
            return LSRSignal("SHORT_BIAS", lsr, -10.0)
            
        return LSRSignal("NEUTRAL", lsr, 0.0)

lsr_engine = LongShortRatioEngine()
