"""
APEX Trading System v4.0
services/engine/flash_crash_catcher.py

Spot-Only Advanced Strategy.
Anticipates sudden liquidity cascades (flash crashes) and calculates 
deep discount targets to catch the wicks with Limit Buy orders.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from shared.models import FlashCrashTarget, SwingPoint, VolumeNode

logger = logging.getLogger(__name__)


class FlashCrashCatcher:
    """
    Calculates extreme deep discount levels to place limit buy orders in anticipation
    of flash crashes / liquidity vacuums. Since Spot trading has no liquidation risk,
    buying these massive dips is a high-EV strategy.
    """
    
    def __init__(self) -> None:
        self.min_discount_pct = 10.0
        self.max_discount_pct = 25.0
        
    def calculate_deep_discount_targets(
        self, 
        symbol: str, 
        current_price: float, 
        atr: float, 
        swing_points: List[SwingPoint], 
        volume_nodes: List[VolumeNode]
    ) -> List[FlashCrashTarget]:
        """
        Finds strong support levels at a deep discount (10-25% below market).
        Priority:
        1. Clusters of High Volume Nodes (HVN) and Swing Lows.
        2. Fallback to 10x ATR deviation.
        """
        targets = []
        
        # We only care about prices below current
        hvns_below = [n for n in volume_nodes if n.type == "HVN" and n.price < current_price]
        swing_lows = [sp for sp in swing_points if sp.type == "LOW" and sp.price < current_price]
        
        candidates = [n.price for n in hvns_below] + [sp.price for sp in swing_lows]
        
        # Filter candidates by discount range
        valid_candidates = []
        for price in candidates:
            discount = ((current_price - price) / current_price) * 100
            if self.min_discount_pct <= discount <= self.max_discount_pct:
                valid_candidates.append((price, discount))
                
        valid_candidates.sort(key=lambda x: x[0]) # Ascending by price (deepest first)
        
        if not valid_candidates:
            # Fallback: ATR based deep drop (e.g. 10x ATR)
            deep_price = current_price - (10.0 * atr)
            discount = ((current_price - deep_price) / current_price) * 100
            if self.min_discount_pct <= discount <= self.max_discount_pct:
                targets.append(FlashCrashTarget(
                    symbol=symbol,
                    target_price=round(deep_price, 2),
                    discount_pct=round(discount, 2),
                    reasoning="Extreme ATR Deviation (10x ATR Drop)",
                    recommended_size_usd=1000.0, # Will be adjusted by risk engine
                    placed_at=datetime.utcnow()
                ))
            return targets

        # Select the deepest strong cluster (we want the absolute lowest within reason)
        best_target = valid_candidates[0] 
        
        targets.append(FlashCrashTarget(
            symbol=symbol,
            target_price=round(best_target[0], 2),
            discount_pct=round(best_target[1], 2),
            reasoning="Deep Liquidity Cluster (HVN/Swing Low confluence)",
            recommended_size_usd=1000.0,
            placed_at=datetime.utcnow()
        ))
        
        logger.info(f"Flash Crash Target calculated for {symbol}: {best_target[0]} (-{best_target[1]:.2f}%)")
        
        return targets
