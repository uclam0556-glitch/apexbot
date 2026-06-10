"""
APEX Trading System v4.0
services/engine/mtf_engine.py

Multi-Timeframe Alignment Engine (MTF Engine).

Design philosophy:
- Trend direction on each timeframe is determined by a combination of:
  (a) EMA positioning (price vs EMA20, EMA50, EMA200)
  (b) Recent swing structure (higher-highs / higher-lows or lower-highs / lower-lows)
- A weighted score aggregates direction votes across timeframes.
- The higher timeframes (1d, 4h) carry significantly more weight than lower
  ones (5m) because they reflect institutional positioning, not noise.
- The engine outputs MTFScore with a clear signal label for downstream use.

Signal thresholds (configurable via trading config):
    STRONG_LONG  if weighted score  > +5.5
    STRONG_SHORT if weighted score  < -5.5
    NO_SIGNAL    otherwise

Usage:
    engine = MTFEngine()
    score = engine.get_alignment_score("BTC/USDT", ohlcv_by_tf)
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import structlog

from shared.models import MTFScore, TimeframeTrend

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMEFRAMES: list[str] = ["1d", "4h", "1h", "15m", "5m"]

WEIGHTS: dict[str, float] = {
    "1d":  3.0,
    "4h":  2.0,
    "1h":  1.5,
    "15m": 1.0,
    "5m":  0.5,
}

# Max possible score when all timeframes are STRONG_LONG:
# 3.0 + 2.0 + 1.5 + 1.0 + 0.5 = 8.0
MAX_SCORE: float = sum(WEIGHTS.values())  # 8.0

# Signal thresholds
STRONG_LONG_THRESHOLD: float = 5.5    # > +5.5  → STRONG_LONG
STRONG_SHORT_THRESHOLD: float = -5.5  # < -5.5  → STRONG_SHORT

# EMA periods used for trend bias
EMA_SHORT: int = 20
EMA_MID: int = 50
EMA_LONG: int = 200

# Minimum candles required to compute all EMAs meaningfully
MIN_CANDLES_FOR_TREND: int = EMA_LONG + 10  # 210


# ---------------------------------------------------------------------------
# MTFEngine
# ---------------------------------------------------------------------------

class MTFEngine:
    """
    Multi-Timeframe Alignment Engine.

    Aggregates trend direction across 5 timeframes using a weighted voting
    model. The output MTFScore indicates whether institutional bias is aligned
    across timeframes — a prerequisite for high-confidence entries.

    Attributes:
        timeframes: Ordered list of timeframes from highest to lowest.
        weights: Per-timeframe weight map (higher TF = higher weight).
        strong_long_threshold: Score threshold for STRONG_LONG signal.
        strong_short_threshold: Score threshold for STRONG_SHORT signal.

    Usage:
        engine = MTFEngine()
        mtf_score = engine.get_alignment_score(
            symbol="BTC/USDT",
            ohlcv_by_tf={
                "1d": daily_df,
                "4h": h4_df,
                "1h": h1_df,
                "15m": m15_df,
                "5m": m5_df,
            }
        )
    """

    def __init__(
        self,
        timeframes: Optional[list[str]] = None,
        weights: Optional[dict[str, float]] = None,
        strong_long_threshold: float = STRONG_LONG_THRESHOLD,
        strong_short_threshold: float = STRONG_SHORT_THRESHOLD,
    ) -> None:
        """
        Initialize the MTF engine with optionally customized timeframe weights.

        Args:
            timeframes: Ordered list of timeframe labels. Defaults to the
                        canonical 5-timeframe set ['1d','4h','1h','15m','5m'].
            weights: Dict mapping timeframe label → weight. Must include all
                     timeframes. Defaults to APEX canonical weights.
            strong_long_threshold: Weighted sum threshold for STRONG_LONG.
            strong_short_threshold: Weighted sum threshold for STRONG_SHORT
                                    (should be negative).
        """
        self.timeframes = timeframes or TIMEFRAMES
        self.weights = weights or WEIGHTS
        self.strong_long_threshold = strong_long_threshold
        self.strong_short_threshold = strong_short_threshold
        self._log = structlog.get_logger(self.__class__.__name__)

        # Validate all timeframes have weights
        missing = set(self.timeframes) - set(self.weights)
        if missing:
            raise ValueError(
                f"MTFEngine: weights missing for timeframes: {missing}"
            )

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    def get_alignment_score(
        self,
        symbol: str,
        ohlcv_by_tf: dict[str, pd.DataFrame],
    ) -> MTFScore:
        """
        Compute a multi-timeframe alignment score for the given symbol.

        Algorithm:
            For each configured timeframe:
                1. Look up the OHLCV DataFrame from ohlcv_by_tf.
                2. Call get_trend_direction(df) → direction ∈ {-1, 0, +1}.
                3. weighted_score = direction × weight.
            Total score = Σ weighted_score.
            Signal:
                STRONG_LONG  if total_score > strong_long_threshold
                STRONG_SHORT if total_score < strong_short_threshold
                NO_SIGNAL    otherwise.

        Missing timeframe data is treated as direction=0 (neutral) to avoid
        contaminating the score with assumptions.

        Args:
            symbol: Instrument identifier (e.g. 'BTC/USDT').
            ohlcv_by_tf: Dict mapping timeframe label → OHLCV DataFrame
                         with columns ['timestamp','open','high','low','close','volume'].

        Returns:
            MTFScore Pydantic model with detailed per-timeframe breakdown.
        """
        self._log.info("mtf_alignment_start", symbol=symbol)

        tf_trends: list[TimeframeTrend] = []
        total_score: float = 0.0

        for tf in self.timeframes:
            weight = self.weights[tf]

            if tf not in ohlcv_by_tf or ohlcv_by_tf[tf] is None:
                self._log.warning("mtf_missing_tf", symbol=symbol, timeframe=tf)
                direction = 0
            else:
                df = ohlcv_by_tf[tf]
                try:
                    direction = self.get_trend_direction(df)
                except Exception as exc:
                    self._log.error(
                        "mtf_trend_direction_error",
                        symbol=symbol,
                        timeframe=tf,
                        error=str(exc),
                    )
                    direction = 0

            weighted_score = float(direction) * weight
            total_score += weighted_score

            tf_trends.append(
                TimeframeTrend(
                    timeframe=tf,
                    direction=direction,
                    weight=weight,
                    weighted_score=round(weighted_score, 4),
                )
            )

        signal = self._classify_signal(total_score)

        self._log.info(
            "mtf_alignment_complete",
            symbol=symbol,
            total_score=round(total_score, 4),
            signal=signal,
        )

        return MTFScore(
            symbol=symbol,
            score=round(total_score, 4),
            signal=signal,
            timeframes=tf_trends,
            computed_at=datetime.now(tz=timezone.utc),
        )

    def get_trend_direction(self, df: pd.DataFrame) -> int:
        """
        Determine the trend direction for a single timeframe.

        Composite scoring approach (5-component vote):

        Component 1 — Price vs EMA20 (+1 / -1 / 0):
            close > EMA20 → bullish pressure; close < EMA20 → bearish pressure

        Component 2 — Price vs EMA50 (+1 / -1 / 0):
            close > EMA50 → intermediate trend bullish

        Component 3 — Price vs EMA200 (+1 / -1 / 0):
            close > EMA200 → macro trend bullish (most important single EMA)

        Component 4 — EMA20/50 stack (+1 / -1 / 0):
            EMA20 > EMA50 → short-term momentum bullish

        Component 5 — Recent swing structure (+1 / -1 / 0):
            Last two swing highs: HH (Higher High) → bullish
            Last two swing highs: LH (Lower High)  → bearish
            Same for lows: HL → bullish, LL → bearish
            Average of the two votes.

        Decision rule:
            Vote = sum of all 5 component votes (max ±5)
            +1 (BULLISH) if Vote >= +2  (at least 3 of 5 components bullish)
             0 (NEUTRAL) if -1 <= Vote <= +1
            -1 (BEARISH) if Vote <= -2

        WHY composite: Using only price vs a single EMA produces many false
        signals in ranging markets. The swing structure component adds
        a structural filter that is consistent with SMC methodology.

        Args:
            df: OHLCV DataFrame with at least 10 candles. Fewer candles than
                EMA periods are handled gracefully (component excluded).

        Returns:
            int: +1 (bullish), -1 (bearish), 0 (neutral).
        """
        if df is None or df.empty or len(df) < 5:
            return 0

        df = df.copy().reset_index(drop=True)
        closes = df["close"]
        n = len(df)

        vote: int = 0

        # ── Component 1: Price vs EMA20 ──────────────────────────────────
        if n >= EMA_SHORT:
            ema20 = closes.ewm(span=EMA_SHORT, adjust=False).mean()
            last_close = float(closes.iloc[-1])
            last_ema20 = float(ema20.iloc[-1])
            if last_close > last_ema20:
                vote += 1
            elif last_close < last_ema20:
                vote -= 1
        else:
            ema20 = None

        # ── Component 2: Price vs EMA50 ──────────────────────────────────
        if n >= EMA_MID:
            ema50 = closes.ewm(span=EMA_MID, adjust=False).mean()
            last_ema50 = float(ema50.iloc[-1])
            if last_close > last_ema50:
                vote += 1
            elif last_close < last_ema50:
                vote -= 1
        else:
            ema50 = None

        # ── Component 3: Price vs EMA200 ─────────────────────────────────
        if n >= EMA_LONG:
            ema200 = closes.ewm(span=EMA_LONG, adjust=False).mean()
            last_ema200 = float(ema200.iloc[-1])
            if last_close > last_ema200:
                vote += 1
            elif last_close < last_ema200:
                vote -= 1
        else:
            ema200 = None

        # ── Component 4: EMA20/50 stack ───────────────────────────────────
        if ema20 is not None and ema50 is not None:
            if float(ema20.iloc[-1]) > float(ema50.iloc[-1]):
                vote += 1
            elif float(ema20.iloc[-1]) < float(ema50.iloc[-1]):
                vote -= 1

        # ── Component 5: Recent swing structure ───────────────────────────
        struct_vote = self._swing_structure_vote(df)
        vote += struct_vote

        # ── Decision ──────────────────────────────────────────────────────
        if vote >= 2:
            return 1
        elif vote <= -2:
            return -1
        else:
            return 0

    # ------------------------------------------------------------------
    # PRIVATE HELPERS
    # ------------------------------------------------------------------

    def _swing_structure_vote(self, df: pd.DataFrame) -> int:
        """
        Vote on trend direction based on recent swing high/low structure.

        Uses a simple fractal with lookback=2 (suitable for trend-following
        on higher timeframes where fewer candles are available).

        Algorithm:
            Find the last 2 confirmed swing highs and last 2 confirmed swing lows.
            High structure: HH (Higher High) → +1, LH (Lower High) → -1, else 0
            Low structure:  HL (Higher Low)  → +1, LL (Lower Low)  → -1, else 0
            Return: round(average of the two votes)

        WHY lookback=2: The MTF engine uses this internally without importing
        smc_core (to avoid circular dependency). Lookback=2 gives faster
        confirmation on lower timeframes while still being structurally valid.

        Args:
            df: OHLCV DataFrame.

        Returns:
            int: +1, -1, or 0.
        """
        if len(df) < 6:
            return 0

        highs = df["high"].values
        lows = df["low"].values
        n = len(df)
        lookback = 2

        swing_high_prices: list[float] = []
        swing_low_prices: list[float] = []

        for i in range(lookback, n - lookback):
            left_h = highs[i - lookback : i]
            right_h = highs[i + 1 : i + lookback + 1]
            if highs[i] > left_h.max() and highs[i] > right_h.max():
                swing_high_prices.append(float(highs[i]))

            left_l = lows[i - lookback : i]
            right_l = lows[i + 1 : i + lookback + 1]
            if lows[i] < left_l.min() and lows[i] < right_l.min():
                swing_low_prices.append(float(lows[i]))

        high_vote = 0
        low_vote = 0

        if len(swing_high_prices) >= 2:
            if swing_high_prices[-1] > swing_high_prices[-2]:
                high_vote = 1   # Higher High
            elif swing_high_prices[-1] < swing_high_prices[-2]:
                high_vote = -1  # Lower High

        if len(swing_low_prices) >= 2:
            if swing_low_prices[-1] > swing_low_prices[-2]:
                low_vote = 1    # Higher Low
            elif swing_low_prices[-1] < swing_low_prices[-2]:
                low_vote = -1   # Lower Low

        combined = (high_vote + low_vote) / 2.0
        # Round to nearest integer, biased toward non-zero when borderline
        if combined > 0:
            return 1
        elif combined < 0:
            return -1
        return 0

    def _classify_signal(self, score: float) -> str:
        """
        Map the numeric weighted score to a categorical signal label.

        Args:
            score: Weighted sum of directional votes.

        Returns:
            str: 'STRONG_LONG' | 'STRONG_SHORT' | 'NO_SIGNAL'
        """
        if score > self.strong_long_threshold:
            return "STRONG_LONG"
        # SPOT ONLY V4: Ignore strong short threshold, do not emit short signals
        return "NO_SIGNAL"

def compute_mtf_score(tf_signals: dict[str, int],
                      weights: dict[str, float] | None = None) -> tuple[float, bool]:
    """
    Weighted MTF score instead of binary gate.
    
    Args:
        tf_signals: e.g. {'1d': 1, '4h': 1, '1h': -1, '15m': 1, '5m': 1}
                    +1 = bullish, -1 = bearish, 0 = neutral
        weights: default weights {'1d':0.30, '4h':0.25, '1h':0.20, '15m':0.15, '5m':0.10}
        
    Returns:
        mtf_score: float [-1, +1]
        strong_alignment: bool (True if > 0.5 or < -0.5)
    """
    if weights is None:
        weights = {'1d': 0.30, '4h': 0.25, '1h': 0.20, '15m': 0.15, '5m': 0.10}
        
    weighted_sum = 0.0
    total_weight = 0.0
    
    for tf, signal in tf_signals.items():
        w = weights.get(tf, 0.0)
        weighted_sum += w * signal
        total_weight += w
        
    if total_weight == 0:
        return 0.0, False
        
    mtf_score = weighted_sum / total_weight
    strong_alignment = mtf_score > 0.5 or mtf_score < -0.5
    
    return float(mtf_score), strong_alignment

def get_mtf_v7_bonus(mtf_score: float) -> float:
    """
    Map mtf_score [-1, +1] to V7 bonus.
    """
    if mtf_score > 0.7:
        return 15.0
    elif mtf_score > 0.5:
        return 8.0
    elif mtf_score > 0.3:
        return 3.0
    elif mtf_score >= 0.0:
        return 0.0
    else:
        return -20.0

