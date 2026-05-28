"""
APEX Trading System v4.0
Confluence Engine v4 — Dynamic Weighted Scoring.

CRITICAL v4 CHANGE: Equal weights (simple count) replaced with
Feature-Store-trained SHAP weights per market regime.

"liquidity_sweep" in BULL historically weights 2.31x more than average.
"news_confirm" in BULL historically weights only 0.58x.
The Feature Store KNOWS the real predictive power of each factor.

Architecture:
  Backend detects all 18 factors (binary yes/no).
  ConfluenceEngineV4 applies dynamic weights → weighted score 0-10.
  Score threshold is weighted (not count-based).
  Weights retrained monthly by ConfluenceWeightTrainer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from shared.config import get_config
from shared.database import get_redis, RedisKeys
from shared.models import (
    ConfluenceFactor,
    Direction,
    ImbalanceZone,
    LiquidationAnalysis,
    LiquiditySweep,
    MarketRegime,
    MTFScore,
    OFIResult,
    SMCAnalysis,
    SwingPoint,
    VolumeNode,
    WeightedConfluenceScore,
    RotationSignal,
)

logger = logging.getLogger(__name__)
_config = get_config()


# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT EQUAL WEIGHTS (fallback when Feature Store has < 50 samples)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_EQUAL_WEIGHTS: dict[str, float] = {
    "key_sr_level":     1.0,
    "imbalance_zone":   1.0,
    "volume_node":      1.0,
    "volume_spike":     1.0,
    "candle_pattern":   1.0,
    "rsi_divergence":   1.0,
    "rsi_extreme":      1.0,
    "ema_alignment":    1.0,
    "htf_trend_match":  1.0,
    "fibonacci":        1.0,
    "onchain_confirm":  1.0,
    "news_confirm":     1.0,
    "copy_trader":      1.0,
    "liquidity_sweep":  1.0,
    "order_flow_bias":  1.0,
    "temporal_bias":    1.0,  # NEW v4
    "macro_align":      1.0,  # NEW v4
    "smart_money_bias": 1.0,  # NEW v4
}

# Pre-seeded educated weights (used before Feature Store is trained)
# Based on SMC practitioner knowledge + academic literature on confluence
SEEDED_WEIGHTS_BY_REGIME: dict[str, dict[str, float]] = {
    "BULL": {
        "liquidity_sweep":  2.31,  # sweep before entry = most reliable setup signal
        "order_flow_bias":  1.98,  # OFI = real buyer/seller pressure
        "imbalance_zone":   1.75,  # FVG = structural magnet
        "onchain_confirm":  1.62,  # whale accumulation = smart money alignment
        "smart_money_bias": 1.55,  # NEW v4: crowd vs smart money divergence
        "volume_node":      1.45,  # HVN as support = high probability
        "htf_trend_match":  1.38,  # 1D trend alignment = major edge
        "macro_align":      1.30,  # NEW v4: DXY/Gold alignment
        "rsi_divergence":   1.22,  # hidden divergence = momentum confirmation
        "fibonacci":        1.10,  # 0.618 retracement = institutional level
        "ema_alignment":    1.05,  # EMA at key level = confluence
        "temporal_bias":    1.00,  # NEW v4: time-of-day/week patterns
        "key_sr_level":     0.95,  # S/R alone is common knowledge
        "copy_trader":      0.87,  # reduced: front-running risk (v4)
        "volume_spike":     0.82,  # volume alone not enough
        "candle_pattern":   0.71,  # candles weakest without other factors
        "rsi_extreme":      0.65,  # RSI extreme can persist
        "news_confirm":     0.58,  # in BULL everything gets spun positive
    },
    "SIDEWAYS": {
        "liquidity_sweep":  2.15,
        "order_flow_bias":  2.05,  # critical in sideways — confirms breakout
        "imbalance_zone":   1.85,
        "volume_node":      1.70,  # levels hold longer in sideways
        "rsi_divergence":   1.55,  # divergence more reliable in ranges
        "fibonacci":        1.40,
        "htf_trend_match":  1.20,
        "smart_money_bias": 1.45,
        "macro_align":      1.25,
        "key_sr_level":     1.15,
        "onchain_confirm":  1.10,
        "ema_alignment":    1.05,
        "temporal_bias":    0.95,
        "copy_trader":      0.80,
        "volume_spike":     0.75,
        "rsi_extreme":      0.70,
        "candle_pattern":   0.65,
        "news_confirm":     0.55,
    },
    "BEAR": {
        "liquidity_sweep":  2.45,  # even more critical in bear (bear traps)
        "order_flow_bias":  2.20,
        "smart_money_bias": 2.00,  # smart money signal strongest in bear
        "imbalance_zone":   1.80,
        "onchain_confirm":  1.75,  # exchange inflow confirmation critical
        "htf_trend_match":  1.60,  # against trend = very high bar
        "volume_node":      1.45,
        "macro_align":      1.40,
        "rsi_divergence":   1.35,
        "fibonacci":        1.20,
        "key_sr_level":     1.00,
        "ema_alignment":    0.95,
        "temporal_bias":    0.90,
        "copy_trader":      0.70,  # most copy traders lose in bear
        "volume_spike":     0.65,
        "candle_pattern":   0.60,
        "rsi_extreme":      0.55,
        "news_confirm":     0.45,
    },
    "CRISIS": {
        # Crisis: system blocked, weights don't matter — but kept for logging
        factor: 1.0 for factor in DEFAULT_EQUAL_WEIGHTS
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# CONFLUENCE ENGINE v4
# ─────────────────────────────────────────────────────────────────────────────

class ConfluenceEngineV4:
    """
    Dynamic Weighted Confluence Engine.

    Replaces v3's simple count with Feature-Store-trained SHAP weights.
    Each factor has a regime-specific weight reflecting its ACTUAL
    predictive power on historical trades.

    Threshold is now weighted score (not count):
      BULL:     >= 6.5 weighted score (out of max ~18-20)
      SIDEWAYS: >= 8.0
      BEAR:     >= 9.5
    """

    def __init__(self) -> None:
        self._weights_cache: dict[str, dict[str, float]] = {}

    async def get_dynamic_weights(self, regime: MarketRegime) -> tuple[dict[str, float], str]:
        """
        Load weights from Redis (trained by ConfluenceWeightTrainer).
        Falls back to seeded weights if not trained yet.
        Falls back to equal weights if no seeded data.

        Returns: (weights_dict, source_description)
        """
        redis = get_redis()
        key = RedisKeys.confluence_weights(regime.value)

        try:
            import json
            weights_json = await redis.get(key)
            if weights_json:
                weights = json.loads(weights_json)
                logger.debug(f"Loaded trained weights for {regime.value} from Feature Store")
                return weights, "feature_store_trained"
        except Exception as e:
            logger.warning(f"Failed to load weights from Redis: {e}")

        # Use seeded weights
        seeded = SEEDED_WEIGHTS_BY_REGIME.get(regime.value, DEFAULT_EQUAL_WEIGHTS)
        logger.debug(f"Using seeded weights for {regime.value}")
        return seeded, "seeded_educated_guess"

    def _check_support_resistance(
        self,
        current_price: float,
        smc: SMCAnalysis,
        tolerance_pct: float = 0.3,
    ) -> tuple[bool, str]:
        """
        True if price is within tolerance of a key support/resistance level.
        Key levels: recent swing highs/lows across timeframes.
        """
        all_levels = (
            [sp.price for sp in smc.swing_highs] +
            [sp.price for sp in smc.swing_lows]
        )

        for level in all_levels:
            distance_pct = abs(current_price - level) / level * 100
            if distance_pct <= tolerance_pct:
                return True, f"price within {distance_pct:.2f}% of S/R level {level:.2f}"

        return False, "no key S/R level within tolerance"

    def _check_imbalance_zone(
        self,
        current_price: float,
        smc: SMCAnalysis,
        direction: Direction,
    ) -> tuple[bool, str]:
        """
        True if current price is within an unfilled imbalance zone (FVG)
        that aligns with signal direction.
        """
        relevant_type = "BULLISH_FVG" if direction == Direction.LONG else "BEARISH_FVG"

        for zone in smc.imbalance_zones:
            if zone.type == relevant_type and not zone.filled:
                if zone.low <= current_price <= zone.high:
                    return True, f"price in {zone.type} [{zone.low:.2f} - {zone.high:.2f}]"

        return False, "no relevant imbalance zone at current price"

    def _check_volume_node(
        self,
        current_price: float,
        smc: SMCAnalysis,
        node_type: str = "HVN",
        tolerance_pct: float = 0.4,
    ) -> tuple[bool, str]:
        """
        True if price is near a High Volume Node (support) or
        Low Volume Node (fast move area for TP).
        """
        for node in smc.volume_nodes:
            if node.type == node_type:
                distance_pct = abs(current_price - node.price) / node.price * 100
                if distance_pct <= tolerance_pct:
                    return True, f"price near {node_type} at {node.price:.2f}"

        return False, f"no {node_type} within tolerance"

    def _check_volume_spike(
        self,
        df: pd.DataFrame,
        threshold_pct: float = 150.0,
    ) -> tuple[bool, str]:
        """
        True if recent candle volume > threshold% of 20-period moving average.
        Volume spike = institutional participation.
        """
        if len(df) < 21:
            return False, "insufficient data for volume check"

        vol_ma20 = df["volume"].rolling(20).mean().iloc[-1]
        current_vol = df["volume"].iloc[-1]
        ratio_pct = (current_vol / vol_ma20) * 100 if vol_ma20 > 0 else 0

        if ratio_pct >= threshold_pct:
            return True, f"volume {ratio_pct:.0f}% of 20-period MA (threshold: {threshold_pct}%)"

        return False, f"volume {ratio_pct:.0f}% of MA (below {threshold_pct}% threshold)"

    def _check_candle_confirmation(
        self,
        df: pd.DataFrame,
        direction: Direction,
    ) -> tuple[bool, str]:
        """
        True if recent candle shows confirmation pattern:
        LONG: bullish engulfing, hammer, morning star, pin bar with tail down
        SHORT: bearish engulfing, shooting star, pin bar with tail up
        """
        if len(df) < 3:
            return False, "insufficient candles"

        c = df.iloc[-1]   # current candle
        p = df.iloc[-2]   # previous candle

        body = abs(c["close"] - c["open"])
        candle_range = c["high"] - c["low"]

        if candle_range == 0:
            return False, "zero-range candle"

        body_ratio = body / candle_range

        if direction == Direction.LONG:
            # Bullish engulfing
            if c["close"] > c["open"] and c["close"] > p["open"] and c["open"] < p["close"]:
                return True, "bullish engulfing candle"

            # Hammer (small body at top, long lower wick)
            lower_wick = min(c["open"], c["close"]) - c["low"]
            upper_wick = c["high"] - max(c["open"], c["close"])
            if lower_wick > 2 * body and upper_wick < 0.3 * body and body_ratio < 0.4:
                return True, "hammer pattern (bullish reversal)"

            # Pin bar with close in upper half
            if c["close"] > (c["high"] + c["low"]) / 2 and lower_wick > 1.5 * body:
                return True, "bullish pin bar"

        else:  # SHORT
            # Bearish engulfing
            if c["close"] < c["open"] and c["close"] < p["open"] and c["open"] > p["close"]:
                return True, "bearish engulfing candle"

            # Shooting star
            upper_wick = c["high"] - max(c["open"], c["close"])
            lower_wick = min(c["open"], c["close"]) - c["low"]
            if upper_wick > 2 * body and lower_wick < 0.3 * body and body_ratio < 0.4:
                return True, "shooting star pattern (bearish reversal)"

            # Pin bar with close in lower half
            if c["close"] < (c["high"] + c["low"]) / 2 and upper_wick > 1.5 * body:
                return True, "bearish pin bar"

        return False, "no candle confirmation pattern"

    def _check_rsi_divergence(
        self,
        df: pd.DataFrame,
        direction: Direction,
        rsi_series: pd.Series,
        lookback: int = 14,
    ) -> tuple[bool, str]:
        """
        RSI divergence: price makes new high/low but RSI does not.
        Bullish divergence: lower price low + higher RSI low = bullish
        Bearish divergence: higher price high + lower RSI high = bearish
        """
        if len(df) < lookback * 2:
            return False, "insufficient data for divergence"

        if direction == Direction.LONG:
            # Look for bullish divergence (lower low in price, higher low in RSI)
            recent_low_idx = df["low"].iloc[-lookback:].idxmin()
            prev_low_idx = df["low"].iloc[-lookback * 2:-lookback].idxmin()

            recent_price_low = df.loc[recent_low_idx, "low"]
            prev_price_low = df.loc[prev_low_idx, "low"]
            recent_rsi_low = rsi_series.loc[recent_low_idx] if recent_low_idx in rsi_series.index else rsi_series.iloc[-1]
            prev_rsi_low = rsi_series.loc[prev_low_idx] if prev_low_idx in rsi_series.index else rsi_series.iloc[-lookback]

            if recent_price_low < prev_price_low and recent_rsi_low > prev_rsi_low:
                return True, f"bullish RSI divergence (price {recent_price_low:.2f} < {prev_price_low:.2f}, RSI {recent_rsi_low:.1f} > {prev_rsi_low:.1f})"

        else:  # SHORT
            # Bearish divergence
            recent_high_idx = df["high"].iloc[-lookback:].idxmax()
            prev_high_idx = df["high"].iloc[-lookback * 2:-lookback].idxmax()

            recent_price_high = df.loc[recent_high_idx, "high"]
            prev_price_high = df.loc[prev_high_idx, "high"]
            recent_rsi_high = rsi_series.loc[recent_high_idx] if recent_high_idx in rsi_series.index else rsi_series.iloc[-1]
            prev_rsi_high = rsi_series.loc[prev_high_idx] if prev_high_idx in rsi_series.index else rsi_series.iloc[-lookback]

            if recent_price_high > prev_price_high and recent_rsi_high < prev_rsi_high:
                return True, f"bearish RSI divergence (price {recent_price_high:.2f} > {prev_price_high:.2f}, RSI {recent_rsi_high:.1f} < {prev_rsi_high:.1f})"

        return False, "no RSI divergence found"

    def _check_rsi_extreme(
        self,
        rsi_series: pd.Series,
        direction: Direction,
        oversold_level: float = 35.0,
        overbought_level: float = 65.0,
    ) -> tuple[bool, str]:
        """
        RSI at extreme level for reversal.
        LONG: RSI coming from oversold (was < oversold, now recovering)
        SHORT: RSI coming from overbought (was > overbought, now declining)
        """
        if len(rsi_series) < 5:
            return False, "insufficient RSI data"

        current = rsi_series.iloc[-1]
        previous = rsi_series.iloc[-5:-1]

        if direction == Direction.LONG:
            if (previous < oversold_level).any() and current > oversold_level:
                return True, f"RSI recovering from oversold (current: {current:.1f}, was below {oversold_level})"

        else:  # SHORT
            if (previous > overbought_level).any() and current < overbought_level:
                return True, f"RSI declining from overbought (current: {current:.1f}, was above {overbought_level})"

        return False, f"RSI {current:.1f} not at actionable extreme"

    def _check_ema_alignment(
        self,
        current_price: float,
        ema_data: dict[str, float],
        direction: Direction,
        tolerance_pct: float = 0.5,
    ) -> tuple[bool, str]:
        """
        True if price is at a key EMA level (20, 50, 200) AND EMAs are aligned.
        LONG: price at EMA support AND EMA20 > EMA50 > EMA200
        SHORT: price at EMA resistance AND EMA20 < EMA50 < EMA200
        """
        ema20 = ema_data.get("ema20_4h", 0)
        ema50 = ema_data.get("ema50_4h", 0)

        # Check if price is near key EMA
        near_ema = False
        ema_detail = ""
        for name, val in ema_data.items():
            if val > 0:
                dist = abs(current_price - val) / val * 100
                if dist <= tolerance_pct:
                    near_ema = True
                    ema_detail = f"price within {dist:.2f}% of {name} ({val:.2f})"
                    break

        if not near_ema:
            return False, "price not at any key EMA level"

        # Check EMA alignment
        if direction == Direction.LONG and ema20 > 0 and ema50 > 0:
            if ema20 >= ema50:  # uptrend alignment
                return True, f"bullish EMA alignment + {ema_detail}"
        elif direction == "SHORT" and ema20 > 0 and ema50 > 0:
            if ema20 <= ema50:  # downtrend alignment
                return True, f"bearish EMA alignment + {ema_detail}"

        return False, f"EMA near but not aligned for {direction.value}"

    def _check_htf_trend(
        self,
        mtf_score: MTFScore,
        direction: Direction,
    ) -> tuple[bool, str]:
        """
        True if higher timeframe trend (1D, 4H) confirms signal direction.
        """
        htf_score = 0.0
        for tf_trend in mtf_score.timeframes:
            if tf_trend.timeframe in ("1d", "4h"):
                htf_score += tf_trend.direction * tf_trend.weight

        if direction == Direction.LONG and htf_score > 3.0:
            return True, f"HTF trend bullish (1D+4H score: {htf_score:.1f})"
        elif direction == "SHORT" and htf_score < -3.0:
            return True, f"HTF trend bearish (1D+4H score: {htf_score:.1f})"

        return False, f"HTF trend not aligned (score: {htf_score:.1f})"

    def _check_fibonacci(
        self,
        current_price: float,
        swing_high: float,
        swing_low: float,
        direction: Direction,
        tolerance_pct: float = 0.25,
    ) -> tuple[bool, str]:
        """
        True if price is at key Fibonacci retracement level (0.618 or 0.786).
        LONG: at support fibonacci retracement
        SHORT: at resistance fibonacci retracement
        """
        if swing_high <= swing_low:
            return False, "invalid swing points"

        range_val = swing_high - swing_low

        if direction == Direction.LONG:
            # Retracement from high to low
            fib_618 = swing_high - 0.618 * range_val
            fib_786 = swing_high - 0.786 * range_val

            for level, ratio in [(fib_618, 0.618), (fib_786, 0.786)]:
                dist = abs(current_price - level) / level * 100
                if dist <= tolerance_pct:
                    return True, f"price at Fibonacci {ratio} retracement ({level:.2f})"

        else:  # SHORT — retracement upward
            fib_618 = swing_low + 0.618 * range_val
            fib_786 = swing_low + 0.786 * range_val

            for level, ratio in [(fib_618, 0.618), (fib_786, 0.786)]:
                dist = abs(current_price - level) / level * 100
                if dist <= tolerance_pct:
                    return True, f"price at Fibonacci {ratio} retracement ({level:.2f})"

        return False, "price not at key Fibonacci level"

    def _check_onchain(
        self,
        direction: Direction,
        onchain_data: dict[str, Any],
    ) -> tuple[bool, str]:
        """
        On-chain confirmation:
        LONG: exchange outflow + whale accumulation + stablecoin inflow
        SHORT: exchange inflow + whale distribution
        """
        flow_direction = onchain_data.get("exchange_flow_direction", "neutral")
        whale_direction = onchain_data.get("whale_net_direction", "neutral")
        stablecoin_inflow = onchain_data.get("stablecoin_inflow_24h_usd", 0)

        if direction == Direction.LONG:
            checks_passed = 0
            if flow_direction == "outflow":
                checks_passed += 1
            if whale_direction == "accumulation":
                checks_passed += 1
            if stablecoin_inflow > 100_000_000:  # $100M+
                checks_passed += 1

            if checks_passed >= 2:
                return True, f"on-chain bullish: {checks_passed}/3 signals (flow={flow_direction}, whales={whale_direction})"

        else:  # SHORT
            checks_passed = 0
            if flow_direction == "inflow":
                checks_passed += 1
            if whale_direction == "distribution":
                checks_passed += 1

            if checks_passed >= 1:
                return True, f"on-chain bearish: {checks_passed}/2 signals (flow={flow_direction}, whales={whale_direction})"

        return False, f"on-chain not confirming (flow={flow_direction}, whales={whale_direction})"

    def _check_news_sentiment(
        self,
        direction: Direction,
        news_sentiment: float,  # -1.0 to +1.0
    ) -> tuple[bool, str]:
        """
        News sentiment alignment.
        LONG: sentiment >= +0.3 (positive)
        SHORT: sentiment <= -0.3 (negative)
        NEUTRAL news (-0.3 to +0.3): not a blocker, just no bonus.
        """
        if direction == Direction.LONG and news_sentiment >= 0.3:
            return True, f"positive news sentiment: {news_sentiment:.2f}"
        elif direction == "SHORT" and news_sentiment <= -0.3:
            return True, f"negative news sentiment: {news_sentiment:.2f}"

        return False, f"news sentiment neutral for {direction.value}: {news_sentiment:.2f}"

    def _check_copy_trader(
        self,
        direction: Direction,
        copy_trader_data: dict[str, Any],
    ) -> tuple[bool, str]:
        """
        Copy trader confirmation (with front-running discount in v4).
        Effective weight reduced by ConfluenceEngineV4 if front_running_detected.
        """
        if copy_trader_data.get("signal") == "CONFIRMING":
            matching = copy_trader_data.get("matching_long_positions" if direction == Direction.LONG else "matching_short_positions", 0)
            front_running = copy_trader_data.get("front_running_detected", False)
            suffix = " [front-running risk — weight reduced]" if front_running else ""
            return True, f"{matching} top traders confirming {direction.value}{suffix}"

        return False, "copy traders not confirming direction"

    def _check_liquidity_sweep(
        self,
        direction: Direction,
        smc: SMCAnalysis,
        lookback_bars: int = 5,
    ) -> tuple[bool, str]:
        """
        Liquidity sweep before entry: price swept equal lows/highs then reversed.
        LONG: SHORT_SWEEP (swept sell-side liquidity) → now looking for long
        SHORT: LONG_SWEEP (swept buy-side liquidity) → now looking for short

        This is one of the strongest setup signals in SMC.
        """
        target_sweep_type = "SHORT_SWEEP" if direction == Direction.LONG else "LONG_SWEEP"

        recent_sweeps = [
            s for s in smc.liquidity_sweeps
            if s.direction == target_sweep_type
        ]

        if recent_sweeps:
            latest = max(recent_sweeps, key=lambda s: s.timestamp)
            return True, f"{target_sweep_type} confirmed at {latest.swept_price:.2f} ({latest.timeframe})"

        return False, f"no recent {target_sweep_type} before entry"

    def _check_order_flow_bias(
        self,
        direction: Direction,
        ofi: OFIResult,
    ) -> tuple[bool, str]:
        """
        Order Flow Imbalance confirmation.
        LONG: OFI > 0.60 (buyers dominate)
        SHORT: OFI < 0.40 (sellers dominate)
        """
        if direction == Direction.LONG and ofi.ofi_score > 0.60:
            return True, f"OFI {ofi.ofi_score:.2f} — buyers dominating (delta: +${ofi.delta_usd:,.0f})"
        elif direction == "SHORT" and ofi.ofi_score < 0.40:
            return True, f"OFI {ofi.ofi_score:.2f} — sellers dominating (delta: -${abs(ofi.delta_usd):,.0f})"

        return False, f"OFI {ofi.ofi_score:.2f} — not confirming {direction.value}"

    def _check_temporal_bias(
        self,
        direction: Direction,
        temporal_score: float,
    ) -> tuple[bool, str]:
        """
        NEW v4: Temporal pattern check.
        LONG: temporal_score > 0 (historically positive time)
        SHORT: temporal_score < 0 (historically negative time)
        CAUTION: if |temporal_score| < 0.2 → neutral (factor False)
        """
        if abs(temporal_score) < 0.2:
            return False, f"temporal bias neutral ({temporal_score:+.2f})"

        if direction == Direction.LONG and temporal_score > 0:
            return True, f"temporal bias positive ({temporal_score:+.2f}): historically favorable time"
        elif direction == "SHORT" and temporal_score < 0:
            return True, f"temporal bias negative ({temporal_score:+.2f}): historically favorable for short"

        return False, f"temporal bias ({temporal_score:+.2f}) opposes {direction.value}"

    def _check_macro_alignment(
        self,
        direction: Direction,
        macro_bias: str,
    ) -> tuple[bool, str]:
        """
        NEW v4: Macro correlation check (DXY/Gold/Dominance).
        LONG: macro_bias in (BULLISH, STRONG_BULLISH)
        SHORT: macro_bias in (BEARISH, STRONG_BEARISH)
        """
        bullish_biases = {"BULLISH", "STRONG_BULLISH"}
        bearish_biases = {"BEARISH", "STRONG_BEARISH"}

        if direction == Direction.LONG and macro_bias in bullish_biases:
            return True, f"macro aligned BULLISH for BTC ({macro_bias})"
        elif direction == "SHORT" and macro_bias in bearish_biases:
            return True, f"macro aligned BEARISH for BTC ({macro_bias})"

        return False, f"macro bias {macro_bias} not confirming {direction.value}"

    def _check_smart_money_divergence(
        self,
        direction: Direction,
        divergence_strength: str,
    ) -> tuple[bool, str]:
        """
        NEW v4: Smart Money vs Crowd divergence check.
        LONG: BULL or STRONG_BULL divergence (smart money buying, crowd fearful)
        SHORT: BEAR or STRONG_BEAR divergence (smart money selling, crowd euphoric)
        """
        bull_divergences = {"BULL", "STRONG_BULL"}
        bear_divergences = {"BEAR", "STRONG_BEAR"}

        if direction == Direction.LONG and divergence_strength in bull_divergences:
            return True, f"smart money vs crowd divergence: {divergence_strength} (contrarian long setup)"
        elif direction == "SHORT" and divergence_strength in bear_divergences:
            return True, f"smart money vs crowd divergence: {divergence_strength} (contrarian short setup)"

        return False, f"no smart money divergence confirming {direction.value} ({divergence_strength})"

    async def calculate_score(
        self,
        symbol: str,
        direction: Direction,
        current_price: float,
        df_1h: pd.DataFrame,
        rsi_series: pd.Series,
        smc: SMCAnalysis,
        mtf_score: MTFScore,
        ofi: OFIResult,
        regime: MarketRegime,
        # Optional external data
        onchain_data: dict[str, Any] | None = None,
        news_sentiment: float = 0.0,
        copy_trader_data: dict[str, Any] | None = None,
        ema_data: dict[str, float] | None = None,
        temporal_score: float = 0.0,
        macro_bias: str = "NEUTRAL",
        divergence_strength: str = "NEUTRAL",
        liquidation: LiquidationAnalysis | None = None,
        rotation_signal: RotationSignal | None = None,
    ) -> WeightedConfluenceScore:
        """
        Main method: calculate weighted confluence score for a signal.

        Steps:
        1. Load dynamic weights from Redis (or use seeded fallback)
        2. Run all 18 factor checks
        3. Apply weights: score = Σ(weight × factor_result)
        4. Check against regime-specific threshold
        5. Return WeightedConfluenceScore with full detail
        """
        weights, weights_source = await self.get_dynamic_weights(regime)

        # Get regime-specific threshold
        thresholds = {
            MarketRegime.BULL:     _config.trading.bull_confluence_min,
            MarketRegime.SIDEWAYS: _config.trading.sideways_confluence_min,
            MarketRegime.BEAR:     _config.trading.bear_confluence_min,
            MarketRegime.CRISIS:   999.0,  # always blocked
        }
        threshold = thresholds.get(regime, 8.0)

        # Determine swing high/low for fibonacci
        swing_high = max((sp.price for sp in smc.swing_highs), default=current_price * 1.05)
        swing_low = min((sp.price for sp in smc.swing_lows), default=current_price * 0.95)

        # Run all checks
        raw_checks: dict[str, tuple[bool, str]] = {
            "key_sr_level":     self._check_support_resistance(current_price, smc),
            "imbalance_zone":   self._check_imbalance_zone(current_price, smc, direction),
            "volume_node":      self._check_volume_node(current_price, smc),
            "volume_spike":     self._check_volume_spike(df_1h),
            "candle_pattern":   self._check_candle_confirmation(df_1h, direction),
            "rsi_divergence":   self._check_rsi_divergence(df_1h, direction, rsi_series),
            "rsi_extreme":      self._check_rsi_extreme(rsi_series, direction),
            "ema_alignment":    self._check_ema_alignment(current_price, ema_data or {}, direction),
            "htf_trend_match":  self._check_htf_trend(mtf_score, direction),
            "fibonacci":        self._check_fibonacci(current_price, swing_high, swing_low, direction),
            "onchain_confirm":  self._check_onchain(direction, onchain_data or {}),
            "news_confirm":     self._check_news_sentiment(direction, news_sentiment),
            "copy_trader":      self._check_copy_trader(direction, copy_trader_data or {}),
            "liquidity_sweep":  self._check_liquidity_sweep(direction, smc),
            "order_flow_bias":  self._check_order_flow_bias(direction, ofi),
            "temporal_bias":    self._check_temporal_bias(direction, temporal_score),
            "macro_align":      self._check_macro_alignment(direction, macro_bias),
            "smart_money_bias": self._check_smart_money_divergence(direction, divergence_strength),
        }

        # Apply weights and build ConfluenceFactor list
        factors: list[ConfluenceFactor] = []
        total_raw_score = 0.0
        max_possible = 0.0
        active_count = 0

        for factor_name, (factor_value, detail) in raw_checks.items():
            weight = weights.get(factor_name, 1.0)

            # Front-running penalty for copy trader (v4)
            if factor_name == "copy_trader" and copy_trader_data:
                if copy_trader_data.get("front_running_detected", False):
                    weight = weight * 0.3  # severe penalty
                    detail += " [weight penalized: front-running risk]"

            contribution = weight if factor_value else 0.0
            total_raw_score += contribution
            max_possible += weight

            if factor_value:
                active_count += 1

            factors.append(ConfluenceFactor(
                name=factor_name,
                value=factor_value,
                weight=round(weight, 3),
                contribution=round(contribution, 3),
                detail=detail,
            ))

        # Normalize to 0-10 scale
        normalized_score = (total_raw_score / max_possible * 10) if max_possible > 0 else 0.0

        # Apply Capital Rotation Boost (Spot-Only Advanced Strategy)
        if rotation_signal:
            rotation_multiplier = rotation_signal.altcoin_multipliers.get(symbol, 1.0)
            if rotation_multiplier != 1.0:
                total_raw_score *= rotation_multiplier
                normalized_score *= rotation_multiplier
                normalized_score = min(normalized_score, 10.0)
                logger.info(f"Capital Rotation applied for {symbol}: multiplier {rotation_multiplier:.2f}")

        # Top 3 factors by contribution (for AI audit)
        top_factors = sorted(
            [f for f in factors if f.value],
            key=lambda f: f.contribution,
            reverse=True,
        )[:3]
        top_3_names = [f.name for f in top_factors]

        passed = normalized_score >= (threshold / max_possible * 10) if max_possible > 0 else False
        # Simpler: compare raw score vs regime minimum (weighted)
        regime_min_raw = threshold  # threshold IS the weighted minimum
        passed = total_raw_score >= regime_min_raw

        score = WeightedConfluenceScore(
            symbol=symbol,
            direction=direction,
            raw_score=round(total_raw_score, 3),
            normalized_score=round(normalized_score, 2),
            max_possible_score=round(max_possible, 3),
            factors=factors,
            active_count=active_count,
            total_factors=len(factors),
            weights_source=weights_source,
            regime=regime,
            passed_threshold=passed,
            top_3_factors=top_3_names,
            computed_at=datetime.utcnow(),
        )

        logger.info(
            "Confluence score calculated",
            extra={
                "symbol": symbol,
                "direction": direction.value,
                "score": normalized_score,
                "raw": total_raw_score,
                "active_factors": active_count,
                "regime": regime.value,
                "passed": passed,
                "top_factors": top_3_names,
                "weights_source": weights_source,
            }
        )

        return score

    def explain_score(self, score: WeightedConfluenceScore) -> str:
        """
        Human-readable explanation of confluence score.
        Passed to AI audit for transparency.
        """
        active = [f for f in score.factors if f.value]
        inactive = [f for f in score.factors if not f.value]

        active_str = ", ".join(
            f"{f.name} ({f.weight:.2f}×)"
            for f in sorted(active, key=lambda x: x.contribution, reverse=True)
        )
        missing_str = ", ".join(
            f.name for f in sorted(inactive, key=lambda x: x.weight, reverse=True)[:5]
        )

        return (
            f"Score: {score.normalized_score:.1f}/10 ({score.active_count}/{score.total_factors} factors active). "
            f"Top active: {active_str}. "
            f"Missing top factors: {missing_str}. "
            f"Weights from: {score.weights_source}."
        )
