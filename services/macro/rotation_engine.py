"""
APEX Trading System v4.0
services/macro/rotation_engine.py

Spot-Only Advanced Strategy.
Monitors BTC Dominance to detect capital rotation (Alt Season) 
and dynamically adjusts AI/Confluence scoring weights for Altcoins.
"""

from __future__ import annotations

import logging
from datetime import datetime

from shared.models import RotationSignal, DominanceSignal, MacroBias

logger = logging.getLogger(__name__)


class CapitalRotationEngine:
    """
    Detects Capital Rotation (Alt Season) based on BTC Dominance and Macro Bias,
    and boosts the confluence score of high-beta altcoins.
    """
    
    def __init__(self) -> None:
        # High beta altcoins that typically outperform when BTC dominance drops
        self.target_altcoins = [
            "ETH/USDT", "SOL/USDT", "BNB/USDT", 
            "AVAX/USDT", "LINK/USDT", "ARB/USDT", 
            "OP/USDT", "TON/USDT"
        ]
        
    def get_rotation_multipliers(
        self, 
        dominance_signal: DominanceSignal, 
        macro_bias: MacroBias
    ) -> RotationSignal:
        """
        Calculates multipliers for altcoins.
        If Alt Season is active (Dominance falling + Macro is OK), altcoins get a boost > 1.0.
        If BTC Season is active (Dominance rising), altcoins get penalized < 1.0.
        """
        multipliers = {}
        
        base_multiplier = 1.0
        
        # Only boost alts if macro is not severely bearish (in panic, everything dumps)
        if macro_bias in (MacroBias.STRONG_BEARISH, MacroBias.BEARISH):
            # In bear market, alts bleed harder than BTC. We penalize them.
            base_multiplier = 0.8
        else:
            if dominance_signal.season == "ALT_SEASON":
                # Prime time for alts
                base_multiplier = 1.25
            elif dominance_signal.season == "BTC_SEASON":
                # Capital flowing into BTC, alts bleed
                base_multiplier = 0.85
                
        for alt in self.target_altcoins:
            multipliers[alt] = base_multiplier
            
        # BTC always stays at 1.0 unless it's BTC season
        multipliers["BTC/USDT"] = 1.1 if dominance_signal.season == "BTC_SEASON" else 1.0
        
        logger.info(f"Rotation Multipliers generated: {multipliers}")
        
        return RotationSignal(
            dominance_signal=dominance_signal,
            macro_bias=macro_bias,
            altcoin_multipliers=multipliers,
            generated_at=datetime.utcnow()
        )
