"""
APEX Trading System v4.0
Smart Money vs Crowd Divergence Detector.

Calculates the difference between what retail (crowd) is doing
and what institutions (smart money) are doing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from shared.database import execute_ch_async
from shared.models import (
    DivergenceResult,
    DivergenceStrength,
    DivergenceType,
    MarketRegime,
    SmartMoneyData,
    SocialData,
)

logger = logging.getLogger(__name__)


class SentimentDivergenceDetector:
    """
    Analyzes divergence between retail sentiment and on-chain smart money action.
    """

    def calculate_divergence(
        self, social_data: SocialData, smart_money: SmartMoneyData
    ) -> DivergenceResult:
        """
        Calculate CROWD score and SMART MONEY score (0-100).
        High Crowd = euphoric retail buying.
        High Smart Money = institutional accumulation.
        """
        
        # 1. CROWD SCORE (0-100)
        # Fear & Greed directly maps (0=Extreme Fear, 100=Extreme Greed)
        fg_score = social_data.fear_greed_index * 0.5
        
        # LunarCrush Galaxy Score (normalized 0-100)
        lc_score = (social_data.lunarcrush_social_score or 50.0) * 0.3
        
        # Funding rate (high positive = retail long bias)
        funding = social_data.funding_rate_pct
        if funding > 0.05:
            fund_score = 90
        elif funding < -0.05:
            fund_score = 10
        else:
            # Map -0.05 to 0.05 into 10 to 90
            fund_score = 50 + (funding / 0.05) * 40
            
        fund_score = max(0, min(100, fund_score)) * 0.2
        
        crowd_score = fg_score + lc_score + fund_score

        # 2. SMART MONEY SCORE (0-100)
        # Exchange flow (outflow = bullish/accumulation)
        if smart_money.exchange_flow_direction == "outflow":
            flow_score = 90
        elif smart_money.exchange_flow_direction == "inflow":
            flow_score = 10
        else:
            flow_score = 50
            
        flow_score *= 0.4
        
        # Whale direction
        if smart_money.whale_net_direction == "accumulation":
            whale_score = 80
        elif smart_money.whale_net_direction == "distribution":
            whale_score = 20
        else:
            whale_score = 50
            
        whale_score *= 0.3
        
        # SOPR (Spent Output Profit Ratio)
        # < 1.0 means selling at a loss (capitulation, smart money buys)
        if smart_money.sopr < 0.98:
            sopr_score = 85
        elif smart_money.sopr > 1.05:
            sopr_score = 20
        else:
            sopr_score = 50
            
        sopr_score *= 0.2
        
        # Stablecoin inflows (buying power)
        if smart_money.stablecoin_inflow_24h_usd > 100_000_000:
            stable_score = 80
        else:
            stable_score = 50
            
        stable_score *= 0.1
        
        sm_score = flow_score + whale_score + sopr_score + stable_score

        # 3. CALCULATE DIVERGENCE
        divergence_raw = sm_score - crowd_score
        
        if sm_score > 70 and crowd_score < 30:
            strength = DivergenceStrength.STRONG_BULL
            div_type = DivergenceType.SMART_MONEY_VS_CROWD
        elif sm_score > 60 and crowd_score < 45:
            strength = DivergenceStrength.BULL
            div_type = DivergenceType.SMART_MONEY_VS_CROWD
        elif sm_score < 30 and crowd_score > 70:
            strength = DivergenceStrength.STRONG_BEAR
            div_type = DivergenceType.CROWD_VS_SMART_MONEY
        elif sm_score < 40 and crowd_score > 55:
            strength = DivergenceStrength.BEAR
            div_type = DivergenceType.CROWD_VS_SMART_MONEY
        else:
            strength = DivergenceStrength.NEUTRAL
            div_type = DivergenceType.NEUTRAL

        # Narrative labels for AI Audit
        if crowd_score > 75:
            crowd_narrative = "EXTREME_GREED"
        elif crowd_score > 60:
            crowd_narrative = "GREED"
        elif crowd_score < 25:
            crowd_narrative = "EXTREME_FEAR"
        elif crowd_score < 40:
            crowd_narrative = "FEAR"
        else:
            crowd_narrative = "NEUTRAL"

        if sm_score > 65:
            sm_narrative = "ACCUMULATING"
        elif sm_score < 35:
            sm_narrative = "DISTRIBUTING"
        else:
            sm_narrative = "NEUTRAL"

        return DivergenceResult(
            crowd_score=round(crowd_score, 1),
            smart_money_score=round(sm_score, 1),
            divergence_raw=round(divergence_raw, 1),
            divergence_type=div_type,
            divergence_strength=strength,
            crowd_sentiment=crowd_narrative,
            smart_money_action=sm_narrative,
            historical_accuracy_pct=None, # Loaded later from Feature Store
            historical_sample_size=None,
            computed_at=datetime.utcnow()
        )

    async def get_historical_accuracy(
        self, divergence_strength: DivergenceStrength, regime: MarketRegime
    ) -> tuple[float, int]:
        """
        Query ClickHouse to find the historical accuracy of this specific divergence
        strength in the current market regime.
        """
        if divergence_strength == DivergenceStrength.NEUTRAL:
            return 50.0, 0
            
        # Mock ClickHouse response
        # In prod: SELECT count(win), count(*) FROM feature_store_signals WHERE divergence = X AND regime = Y
        return 68.5, 120

    def detect_smart_money_front_run(
        self, whale_data: dict[str, Any], price_action: list[float]
    ) -> dict[str, Any]:
        """
        Detects if whales are front-running a move.
        Pattern: large tx 15-30 mins before price jump.
        """
        # Mock implementation
        return {
            "front_run_detected": False,
            "timestamp": datetime.utcnow().isoformat(),
            "size_usd": 0.0
        }
