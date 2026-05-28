"""
APEX Trading System v4.0
Adversarial Signal Tester.

Acts as Filter #0 before the AI Audit.
Actively searches for manipulation patterns, liquidity traps, 
and spoofing that trick standard SMC concepts.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd

from shared.models import (
    AdversarialCheck,
    AdversarialResult,
    AdversarialRisk,
    OrderBook,
    SignalCore,
    SMCAnalysis,
    SpoofingAlert,
)

logger = logging.getLogger(__name__)


class AdversarialSignalTester:
    """
    Tests signals against known adversarial manipulation patterns.
    High scores indicate manipulation -> forces AI rejection or penalty.
    """

    def _check_stop_hunt_proximity(
        self, signal: SignalCore, smc: SMCAnalysis, orderbook: OrderBook
    ) -> AdversarialCheck:
        """
        Are we placing our Stop Loss right where market makers want to hunt it?
        1. Near major round numbers.
        2. Clustered with known swing points (obvious liquidity).
        """
        score = 0.0
        detail_parts = []
        sl = signal.stop_loss

        # 1. Round numbers (0.1% proximity)
        round_magnitudes = [10000, 1000, 100]
        for mag in round_magnitudes:
            nearest_round = round(sl / mag) * mag
            if nearest_round > 0 and abs(sl - nearest_round) / nearest_round <= 0.001:
                score += 2.5
                detail_parts.append(f"SL ({sl}) is within 0.1% of round number {nearest_round}")
                break

        # 2. Obvious Swing Point clustering
        all_swings = [sp.price for sp in smc.swing_highs + smc.swing_lows]
        for sp in all_swings:
            if abs(sl - sp) / sp <= 0.001:
                score += 2.0
                detail_parts.append(f"SL ({sl}) is exactly at obvious swing point {sp} (high risk of sweep)")
                break

        # 3. Orderbook liquidity check
        book_side = orderbook.bids if signal.direction.value == "LONG" else orderbook.asks
        total_depth = sum(lvl.size for lvl in book_side[:20])
        sl_depth = sum(lvl.size for lvl in book_side if abs(lvl.price - sl)/sl <= 0.002)

        if total_depth > 0 and (sl_depth / total_depth) > 0.3:
            score += 1.5
            detail_parts.append("High liquidity wall right at SL (magnet risk)")

        detail = " | ".join(detail_parts) if detail_parts else "SL placement looks safe"
        
        return AdversarialCheck(
            check_name="stop_hunt_proximity",
            passed=score < 2.0,
            score_contribution=score,
            detail=detail
        )

    def _check_liquidity_vacuum_trap(
        self, signal: SignalCore, orderbook: OrderBook
    ) -> AdversarialCheck:
        """
        Checks if the entry range is suspiciously thin.
        Market makers pull liquidity to cause a fast vacuum drop/pump.
        """
        entry_mid = (signal.entry_low + signal.entry_high) / 2
        book_side = orderbook.asks if signal.direction.value == "LONG" else orderbook.bids
        
        total_size_top20 = sum(lvl.size for lvl in book_side[:20])
        avg_level_size = total_size_top20 / 20 if total_size_top20 > 0 else 0
        
        entry_range_levels = [lvl for lvl in book_side if signal.entry_low <= lvl.price <= signal.entry_high]
        
        score = 0.0
        detail = "Normal liquidity in entry range"
        
        if entry_range_levels and avg_level_size > 0:
            avg_entry_size = sum(lvl.size for lvl in entry_range_levels) / len(entry_range_levels)
            if avg_entry_size < (avg_level_size * 0.2):
                score = 2.0
                detail = "Liquidity vacuum in entry range (Ask/Bid depth < 20% of normal). Potential trap."
                
        return AdversarialCheck(
            check_name="liquidity_vacuum_trap",
            passed=score == 0.0,
            score_contribution=score,
            detail=detail
        )

    def _check_false_breakout_pattern(
        self, signal: SignalCore, df_15m: pd.DataFrame
    ) -> AdversarialCheck:
        """
        Checks if a recent Break of Structure was actually a false breakout.
        """
        score = 0.0
        detail = "No false breakout patterns detected"

        if len(df_15m) < 21:
            return AdversarialCheck("false_breakout", True, 0.0, "Insufficient data")

        vol_ma20 = df_15m["volume"].rolling(20).mean().iloc[-1]
        last_3_candles = df_15m.iloc[-3:]
        
        local_high = df_15m["high"].iloc[-10:].max()
        local_low = df_15m["low"].iloc[-10:].min()
        
        current_price = df_15m["close"].iloc[-1]
        
        if signal.direction.value == "LONG" and current_price >= local_high * 0.999:
            breakout_vol = last_3_candles["volume"].max()
            if breakout_vol < vol_ma20 * 0.7:
                score = 2.0
                detail = "Bullish breakout occurred on weak volume (< 70% MA). Likely trap."
                
        elif signal.direction.value == "SHORT" and current_price <= local_low * 1.001:
            breakout_vol = last_3_candles["volume"].max()
            if breakout_vol < vol_ma20 * 0.7:
                score = 2.0
                detail = "Bearish breakdown occurred on weak volume (< 70% MA). Likely trap."

        return AdversarialCheck(
            check_name="false_breakout_pattern",
            passed=score < 2.0,
            score_contribution=score,
            detail=detail
        )

    def _check_wash_trading_volume(self, df_5m: pd.DataFrame) -> AdversarialCheck:
        """
        Detects artificial volume spikes with zero price movement.
        """
        if len(df_5m) < 20:
            return AdversarialCheck("wash_trading", True, 0.0, "Insufficient data")

        vol_ma = df_5m["volume"].rolling(20).mean().iloc[-1]
        recent_vol = df_5m["volume"].iloc[-1]
        recent_range = (df_5m["high"].iloc[-1] - df_5m["low"].iloc[-1]) / df_5m["low"].iloc[-1] * 100

        score = 0.0
        detail = "Volume profile looks organic"

        if recent_vol > vol_ma * 2.5 and recent_range < 0.05:
            score = 1.5
            detail = "High volume spike with almost zero price movement. Likely wash trading / manipulation."

        return AdversarialCheck(
            check_name="wash_trading_volume",
            passed=score == 0.0,
            score_contribution=score,
            detail=detail
        )

    def _check_coordinated_spoofing(self, spoofing: SpoofingAlert) -> AdversarialCheck:
        score = 0.0
        detail = "No spoofing detected"

        if spoofing.detected:
            if spoofing.episodes_count > 5:
                score = 3.0
                detail = f"Coordinated spoofing detected! {spoofing.episodes_count} episodes."
            elif spoofing.episodes_count >= 3:
                score = 1.5
                detail = f"Mild spoofing detected ({spoofing.episodes_count} episodes). Caution."
            else:
                score = 0.5
                detail = f"Isolated spoofing event ({spoofing.episodes_count} episodes)."

        return AdversarialCheck(
            check_name="coordinated_spoofing",
            passed=score < 1.5,
            score_contribution=score,
            detail=detail
        )

    def _check_news_timing_anomaly(
        self, signal: SignalCore, news_context: dict[str, Any]
    ) -> AdversarialCheck:
        score = 0.0
        detail = "No imminent news anomalies"

        imminent_events = news_context.get("imminent_high_impact_events_hours", [])
        
        if imminent_events:
            nearest = min(imminent_events)
            if nearest < 0.5:
                score = 2.0
                detail = f"High impact news in {nearest*60:.0f} mins. High sweep risk."
            elif nearest < 2.0:
                score = 1.5
                detail = f"High impact news in {nearest:.1f} hours."

        return AdversarialCheck(
            check_name="news_timing_anomaly",
            passed=score < 1.5,
            score_contribution=score,
            detail=detail
        )

    def _check_round_number_proximity(self, signal: SignalCore) -> AdversarialCheck:
        score = 0.0
        detail_parts = []
        entry_mid = (signal.entry_low + signal.entry_high) / 2

        round_magnitudes = [10000, 1000, 100]
        for mag in round_magnitudes:
            nearest_round = round(entry_mid / mag) * mag
            if nearest_round > 0 and abs(entry_mid - nearest_round) / nearest_round <= 0.0015:
                score += 1.0
                detail_parts.append(f"Entry ({entry_mid}) is within 0.15% of round number {nearest_round}")
                break

        tp1 = signal.take_profit_1
        for mag in round_magnitudes:
            nearest_round = round(tp1 / mag) * mag
            if nearest_round > 0 and abs(tp1 - nearest_round) / nearest_round <= 0.0005:
                score += 0.5
                detail_parts.append(f"TP1 ({tp1}) is exactly at round number {nearest_round}")
                break

        detail = " | ".join(detail_parts) if detail_parts else "Entry/TP spacing organic"

        return AdversarialCheck(
            check_name="round_number_proximity",
            passed=score < 1.0,
            score_contribution=score,
            detail=detail
        )

    def _check_overcrowded_trade(
        self, copy_traders: dict[str, Any], social_data: dict[str, Any]
    ) -> AdversarialCheck:
        score = 0.0
        detail = "Trade crowding normal"

        social_spike = social_data.get("social_volume_spike_pct", 0)
        ct_alignment = copy_traders.get("alignment_pct", 50)

        if ct_alignment > 80 and social_spike > 300:
            score = 2.0
            detail = "Trade is severely overcrowded (>80% copy traders aligned + 300% social volume spike)."
        elif ct_alignment > 80:
            score = 1.0
            detail = "Trade is highly crowded by retail copy traders (>80%)."

        return AdversarialCheck(
            check_name="overcrowded_trade",
            passed=score < 1.0,
            score_contribution=score,
            detail=detail
        )

    def run_adversarial_test(
        self,
        signal: SignalCore,
        smc: SMCAnalysis,
        orderbook: OrderBook,
        df_15m: pd.DataFrame,
        df_5m: pd.DataFrame,
        spoofing: SpoofingAlert,
        news_context: dict[str, Any],
        copy_traders: dict[str, Any],
        social_data: dict[str, Any]
    ) -> AdversarialResult:
        checks = [
            self._check_stop_hunt_proximity(signal, smc, orderbook),
            self._check_liquidity_vacuum_trap(signal, orderbook),
            self._check_false_breakout_pattern(signal, df_15m),
            self._check_wash_trading_volume(df_5m),
            self._check_coordinated_spoofing(spoofing),
            self._check_news_timing_anomaly(signal, news_context),
            self._check_round_number_proximity(signal),
            self._check_overcrowded_trade(copy_traders, social_data),
        ]

        total_score = sum(c.score_contribution for c in checks)
        key_concerns = [c.detail for c in checks if c.score_contribution >= 1.0]

        auto_rejected = False
        confluence_adj = 0

        if total_score >= 10.0:
            risk = AdversarialRisk.EXTREME
            manipulation = "EXTREME"
            passed = False
            auto_rejected = True
        elif total_score >= 7.0:
            risk = AdversarialRisk.HIGH
            manipulation = "HIGH"
            passed = False
            confluence_adj = 2
        elif total_score >= 4.0:
            risk = AdversarialRisk.MEDIUM
            manipulation = "MEDIUM"
            passed = True
            confluence_adj = 1
        else:
            risk = AdversarialRisk.LOW
            manipulation = "LOW"
            passed = True

        logger.info(
            "Adversarial test complete",
            extra={
                "symbol": signal.symbol,
                "score": total_score,
                "risk": risk.value,
                "passed": passed
            }
        )

        return AdversarialResult(
            adversarial_score=round(total_score, 2),
            risk_level=risk,
            manipulation_probability=manipulation,
            passed=passed,
            checks=checks,
            key_concerns=key_concerns,
            auto_rejected=auto_rejected,
            confluence_min_adjustment=confluence_adj,
            computed_at=datetime.utcnow()
        )
