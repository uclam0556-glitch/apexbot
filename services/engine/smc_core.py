"""
APEX Trading System v4.0
services/engine/smc_core.py

Formalized Smart Money Concepts (SMC) Core Engine.

Design philosophy:
- FULLY DETERMINISTIC — every decision is based on strict mathematical rules.
- No subjectivity: each structure (swing, FVG, BOS, sweep) has an explicit
  algorithmic definition that produces identical output for identical input.
- Pure pandas/numpy; no ML inference in this layer.
- This module is the structural backbone consumed by the confluence engine,
  MTF engine, and the AI audit layer.

All return types are Pydantic v2 models from shared/models.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import structlog

from shared.models import (
    ImbalanceZone,
    LiquiditySweep,
    SMCAnalysis,
    StructureEvent,
    SwingPoint,
    VolumeNode,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# ATR helper (used internally — not exported)
# ---------------------------------------------------------------------------

def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Compute Average True Range over *period* candles.

    WHY: ATR is used as a volatility-normalised filter for FVG minimum size,
    ensuring we skip micro-gaps that have no structural significance.

    Args:
        df: OHLCV DataFrame with columns ['high', 'low', 'close'].
        period: Lookback period in candles (default 14).

    Returns:
        pd.Series of ATR values, same index as df.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - close).abs(),
            (low - close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.rolling(window=period, min_periods=1).mean()


# ---------------------------------------------------------------------------
# FormalizedSMCCore
# ---------------------------------------------------------------------------

class FormalizedSMCCore:
    """
    Formalized Smart Money Concepts (SMC) analysis engine.

    All detection algorithms are strictly rule-based:
    - Swing highs/lows: n-candle fractal confirmation
    - Imbalance zones (FVG): 3-candle pattern + ATR size filter
    - Volume nodes: numpy histogram-based HVN/LVN classification
    - Structure breaks (BOS/CHoCH): close-based confirmation vs swing levels
    - Liquidity sweeps: wick beyond swing + close reversal
    - Premium/discount: normalized price position in swing range

    Usage:
        smc = FormalizedSMCCore(timeframe="1h")
        analysis = smc.analyze(df, symbol="BTC/USDT")
    """

    def __init__(self, timeframe: str = "1h") -> None:
        """
        Initialize the SMC core for a specific timeframe context.

        Args:
            timeframe: The timeframe label for all generated model instances
                       (e.g. '1h', '4h', '1d').
        """
        self.timeframe = timeframe
        self._log = structlog.get_logger(self.__class__.__name__).bind(
            timeframe=timeframe
        )

    # ------------------------------------------------------------------
    # PUBLIC API — full analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        df: pd.DataFrame,
        symbol: str,
        lookback: int = 5,
        volume_bins: int = 20,
    ) -> SMCAnalysis:
        """
        Run a complete SMC analysis on the given OHLCV DataFrame.

        Orchestrates all sub-analyses in the correct dependency order:
        1. Swing points (prerequisite for BOS, sweeps, premium/discount)
        2. Imbalance zones (FVG)
        3. Volume nodes (HVN/LVN)
        4. Structure events (BOS/CHoCH)
        5. Liquidity sweeps
        6. Premium/discount ratio

        Args:
            df: OHLCV DataFrame with columns
                ['timestamp', 'open', 'high', 'low', 'close', 'volume'].
                Must have at least 2 * lookback + 1 rows.
            symbol: Instrument symbol (e.g. 'BTC/USDT').
            lookback: Half-width of the fractal window for swing detection.
            volume_bins: Number of bins for the volume profile histogram.

        Returns:
            SMCAnalysis Pydantic model with all structural elements.

        Raises:
            ValueError: If the DataFrame is too small or missing required columns.
        """
        self._validate_df(df)
        self._log.info("smc_analysis_start", symbol=symbol, rows=len(df))

        swing_highs, swing_lows = self.find_swing_points(df, lookback=lookback)
        all_swings = swing_highs + swing_lows

        imbalance_zones = self.find_imbalance_zones(df)
        volume_nodes = self.find_volume_nodes(df, bins=volume_bins)
        structure_events = self.detect_structure_break(df, all_swings)
        liquidity_sweeps = self.find_liquidity_sweeps(df, all_swings)

        # Premium/discount: use the last N swing points to define range
        recent_highs = [sp.price for sp in swing_highs[-10:]] if swing_highs else []
        recent_lows = [sp.price for sp in swing_lows[-10:]] if swing_lows else []

        swing_high_price = max(recent_highs) if recent_highs else df["high"].max()
        swing_low_price = min(recent_lows) if recent_lows else df["low"].min()
        current_price = df["close"].iloc[-1]

        premium_discount = self.calculate_premium_discount(
            swing_high_price, swing_low_price, current_price
        )

        result = SMCAnalysis(
            symbol=symbol,
            timeframe=self.timeframe,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
            imbalance_zones=imbalance_zones,
            volume_nodes=volume_nodes,
            structure_events=structure_events,
            liquidity_sweeps=liquidity_sweeps,
            premium_discount_ratio=premium_discount,
            analyzed_at=datetime.now(tz=timezone.utc),
        )

        self._log.info(
            "smc_analysis_complete",
            symbol=symbol,
            swing_highs=len(swing_highs),
            swing_lows=len(swing_lows),
            fvg_zones=len(imbalance_zones),
            volume_nodes=len(volume_nodes),
            structure_events=len(structure_events),
            sweeps=len(liquidity_sweeps),
            premium_discount=round(premium_discount, 4),
        )
        return result

    # ------------------------------------------------------------------
    # SWING POINTS
    # ------------------------------------------------------------------

    def find_swing_points(
        self, df: pd.DataFrame, lookback: int = 5
    ) -> tuple[list[SwingPoint], list[SwingPoint]]:
        """
        Detect swing highs and swing lows using a strict n-candle fractal rule.

        Algorithm (for swing HIGH at index i):
            candle[i].high  >  max(candle[i-lookback : i].high)   [left side]
            candle[i].high  >  max(candle[i+1 : i+lookback+1].high) [right side]

        Both conditions must be True simultaneously; no approximation.
        The strength field records the actual lookback used (confirming candles
        on each side).

        WHY strict fractal: SMC structure is only meaningful when a pivot is
        genuinely dominant over its neighbourhood. A 2-candle fractal generates
        too many false pivots in choppy markets; 5-candle confirmation yields
        clean, institutional-grade swing levels.

        Args:
            df: OHLCV DataFrame.
            lookback: Number of candles on each side required to confirm a pivot.
                      Default 5 gives N=5 left + N=5 right confirmation.

        Returns:
            Tuple (swing_highs, swing_lows), each a list of SwingPoint models
            sorted chronologically (oldest first).
        """
        self._validate_df(df)
        df = df.reset_index(drop=True)

        swing_highs: list[SwingPoint] = []
        swing_lows: list[SwingPoint] = []

        highs = df["high"].values
        lows = df["low"].values
        n = len(df)

        for i in range(lookback, n - lookback):
            left_highs = highs[i - lookback : i]   # exclusive of i
            right_highs = highs[i + 1 : i + lookback + 1]

            # Swing HIGH: candle[i].high strictly greater than all neighbours
            if highs[i] > left_highs.max() and highs[i] > right_highs.max():
                ts = self._get_timestamp(df, i)
                swing_highs.append(
                    SwingPoint(
                        price=float(highs[i]),
                        timestamp=ts,
                        timeframe=self.timeframe,
                        type="HIGH",
                        strength=lookback,
                    )
                )

            left_lows = lows[i - lookback : i]
            right_lows = lows[i + 1 : i + lookback + 1]

            # Swing LOW: candle[i].low strictly less than all neighbours
            if lows[i] < left_lows.min() and lows[i] < right_lows.min():
                ts = self._get_timestamp(df, i)
                swing_lows.append(
                    SwingPoint(
                        price=float(lows[i]),
                        timestamp=ts,
                        timeframe=self.timeframe,
                        type="LOW",
                        strength=lookback,
                    )
                )

        self._log.debug(
            "swing_points_found",
            highs=len(swing_highs),
            lows=len(swing_lows),
            lookback=lookback,
        )
        return swing_highs, swing_lows

    # ------------------------------------------------------------------
    # IMBALANCE ZONES (Fair Value Gaps)
    # ------------------------------------------------------------------

    def find_imbalance_zones(self, df: pd.DataFrame) -> list[ImbalanceZone]:
        """
        Identify Fair Value Gaps (FVG) — bullish and bearish imbalance zones.

        Bullish FVG (3-candle pattern):
            - candle[i-1] is bullish  (close[i-1] > open[i-1])
            - candle[i].low > candle[i-2].high  (gap between i-2 top and i bottom)
            - Gap size >= 1.5 × ATR[i]  (filters micro-gaps with no significance)

        Bearish FVG (3-candle pattern):
            - candle[i-1] is bearish  (close[i-1] < open[i-1])
            - candle[i].high < candle[i-2].low  (gap between i-2 bottom and i top)
            - Gap size >= 1.5 × ATR[i]

        Fill tracking:
            fill_pct = how much of the zone has been traded back through.
            filled = True when current price has closed fully inside the zone.

        WHY ATR filter: Gaps smaller than 1.5 ATR are noise in the current
        volatility environment and do not represent genuine institutional
        order imbalance. The 1.5 multiplier is calibrated empirically.

        Args:
            df: OHLCV DataFrame.

        Returns:
            List of ImbalanceZone models (bullish + bearish), chronological.
        """
        self._validate_df(df)
        df = df.reset_index(drop=True)

        atr = _compute_atr(df, period=14)
        zones: list[ImbalanceZone] = []

        highs = df["high"].values
        lows = df["low"].values
        opens = df["open"].values
        closes = df["close"].values
        n = len(df)

        for i in range(2, n):
            # V6.1: Reduced FVG ATR threshold from 1.5 to 0.8 for sideways market visibility
            atr_threshold = 0.8 * atr.iloc[i]
            ts = self._get_timestamp(df, i - 1)  # zone created at middle candle

            # ── Bullish FVG ──────────────────────────────────────────────
            # Pattern: candle[i-1] is bullish AND gap[i-2 high → i low] exists
            middle_bullish = closes[i - 1] > opens[i - 1]
            bull_gap = lows[i] - highs[i - 2]

            if middle_bullish and bull_gap > 0 and bull_gap >= atr_threshold:
                zone_low = float(highs[i - 2])
                zone_high = float(lows[i])

                # Compute fill percentage: how much subsequent price has
                # retraced into the zone from below
                fill_pct, filled = self._compute_fill(
                    df, i, zone_low, zone_high, direction="BULLISH"
                )

                zones.append(
                    ImbalanceZone(
                        type="BULLISH_FVG",
                        low=zone_low,
                        high=zone_high,
                        timeframe=self.timeframe,
                        created_at=ts,
                        filled=filled,
                        fill_pct=round(fill_pct, 4),
                    )
                )

            # ── Bearish FVG ──────────────────────────────────────────────
            # Pattern: candle[i-1] is bearish AND gap[i low → i-2 high] exists
            middle_bearish = closes[i - 1] < opens[i - 1]
            bear_gap = lows[i - 2] - highs[i]

            if middle_bearish and bear_gap > 0 and bear_gap >= atr_threshold:
                zone_low = float(highs[i])
                zone_high = float(lows[i - 2])

                fill_pct, filled = self._compute_fill(
                    df, i, zone_low, zone_high, direction="BEARISH"
                )

                zones.append(
                    ImbalanceZone(
                        type="BEARISH_FVG",
                        low=zone_low,
                        high=zone_high,
                        timeframe=self.timeframe,
                        created_at=ts,
                        filled=filled,
                        fill_pct=round(fill_pct, 4),
                    )
                )

        self._log.debug("imbalance_zones_found", count=len(zones))
        return zones

    # ------------------------------------------------------------------
    # VOLUME NODES (Volume Profile)
    # ------------------------------------------------------------------

    def find_volume_nodes(
        self, df: pd.DataFrame, bins: int = 20
    ) -> list[VolumeNode]:
        """
        Build a simplified volume profile and classify price levels as
        High Volume Nodes (HVN) or Low Volume Nodes (LVN).

        Method — numpy histogram approach:
            1. Bin the price range [min_low, max_high] into `bins` equal buckets.
            2. For each OHLCV candle, assign its volume to the bin whose centre
               is closest to the candle's typical price (TP = (H+L+C)/3).
            3. Compute the 80th and 20th percentile of bin volumes.
            4. HVN: bin volume >= 80th percentile (high-volume support/resistance)
            5. LVN: bin volume <= 20th percentile (thin liquidity — price moves fast)

        WHY typical price: TP accounts for the candle's entire range, not just
        close, giving a better volume-weighted centroid.

        Args:
            df: OHLCV DataFrame.
            bins: Number of price bins for the histogram (default 20).

        Returns:
            List of VolumeNode models (HVN + LVN combined), sorted by price.
        """
        self._validate_df(df)

        # Build numpy histogram over price range
        price_min = df["low"].min()
        price_max = df["high"].max()

        if price_max <= price_min:
            self._log.warning("volume_nodes_degenerate_range")
            return []

        # Bin edges: bins+1 edges define `bins` intervals
        bin_edges = np.linspace(price_min, price_max, bins + 1)
        bin_volumes = np.zeros(bins, dtype=np.float64)

        typical_prices = (df["high"] + df["low"] + df["close"]) / 3.0
        volumes = df["volume"].values

        for tp, vol in zip(typical_prices, volumes):
            # Find the bin index for this candle's typical price
            # np.searchsorted returns the right-side insertion index
            idx = np.searchsorted(bin_edges[1:], tp)
            idx = min(idx, bins - 1)  # clamp to last bin
            bin_volumes[idx] += vol

        # Percentile thresholds
        hvn_threshold = np.percentile(bin_volumes, 80)
        lvn_threshold = np.percentile(bin_volumes, 20)

        total_volume = bin_volumes.sum()
        nodes: list[VolumeNode] = []

        if total_volume > 0:
            poc_idx = np.argmax(bin_volumes)
            poc_price = (bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2.0
            poc_vol = bin_volumes[poc_idx]
            nodes.append(
                VolumeNode(
                    price=round(float(poc_price), 8),
                    volume=round(float(poc_vol), 2),
                    type="POC",
                    percentile=100.0,
                )
            )

            # Calculate Value Area (70% of total volume)
            va_volume = poc_vol
            lower_idx = poc_idx - 1
            upper_idx = poc_idx + 1
            target_va_vol = total_volume * 0.70

            while va_volume < target_va_vol and (lower_idx >= 0 or upper_idx < bins):
                vol_lower = bin_volumes[lower_idx] if lower_idx >= 0 else -1
                vol_upper = bin_volumes[upper_idx] if upper_idx < bins else -1

                if vol_lower > vol_upper:
                    va_volume += vol_lower
                    lower_idx -= 1
                else:
                    va_volume += vol_upper
                    upper_idx += 1

            vah_price = (bin_edges[min(upper_idx, bins - 1)] + bin_edges[min(upper_idx + 1, bins)]) / 2.0
            val_price = (bin_edges[max(lower_idx, 0)] + bin_edges[max(lower_idx + 1, 1)]) / 2.0

            nodes.append(VolumeNode(price=round(float(vah_price), 8), volume=0, type="VAH", percentile=70.0))
            nodes.append(VolumeNode(price=round(float(val_price), 8), volume=0, type="VAL", percentile=70.0))

        for i in range(bins):
            bin_centre = (bin_edges[i] + bin_edges[i + 1]) / 2.0
            bin_vol = bin_volumes[i]
            percentile = float(np.searchsorted(np.sort(bin_volumes), bin_vol)) / bins * 100.0

            if bin_vol >= hvn_threshold:
                nodes.append(
                    VolumeNode(
                        price=round(float(bin_centre), 8),
                        volume=round(float(bin_vol), 2),
                        type="HVN",
                        percentile=round(percentile, 2),
                    )
                )
            elif bin_vol <= lvn_threshold:
                nodes.append(
                    VolumeNode(
                        price=round(float(bin_centre), 8),
                        volume=round(float(bin_vol), 2),
                        type="LVN",
                        percentile=round(percentile, 2),
                    )
                )

        # Sort by price ascending for easy lookup by callers
        nodes.sort(key=lambda n: n.price)
        self._log.debug(
            "volume_nodes_classified",
            hvn=sum(1 for n in nodes if n.type == "HVN"),
            lvn=sum(1 for n in nodes if n.type == "LVN"),
        )
        return nodes

    # ------------------------------------------------------------------
    # STRUCTURE BREAK (BOS / CHoCH)
    # ------------------------------------------------------------------

    def detect_structure_break(
        self, df: pd.DataFrame, swing_points: list[SwingPoint]
    ) -> list[StructureEvent]:
        """
        Detect Break of Structure (BOS) and Change of Character (CHoCH) events.

        Definitions (strictly close-based, no wick):
            BOS BULLISH: close[i] > previous confirmed swing HIGH
                → continuation of bullish structure
            BOS BEARISH: close[i] < previous confirmed swing LOW
                → continuation of bearish structure
            CHoCH BULLISH: close[i] > previous swing HIGH after a sequence of
                lower-highs (structure was previously bearish — first break up)
            CHoCH BEARISH: close[i] < previous swing LOW after a sequence of
                higher-lows (structure was previously bullish — first break down)

        Simplified heuristic for CHoCH vs BOS:
            If the prior_structure was BEARISH and price breaks a swing HIGH → CHoCH.
            If the prior_structure was BULLISH and price breaks a swing HIGH → BOS.
            (Symmetric for bearish.)

        WHY close-based: Wicks can penetrate levels briefly (liquidity sweeps);
        a close beyond the level indicates genuine structural acceptance.

        Args:
            df: OHLCV DataFrame.
            swing_points: Combined list of SwingPoint (HIGHs and LOWs) from
                          find_swing_points(), sorted chronologically.

        Returns:
            List of StructureEvent models in chronological order.
        """
        if not swing_points:
            return []

        self._validate_df(df)
        df = df.reset_index(drop=True)

        closes = df["close"].values
        timestamps = [self._get_timestamp(df, i) for i in range(len(df))]

        # Build sorted lists of confirmed swing prices by type
        swing_highs = sorted(
            [sp for sp in swing_points if sp.type == "HIGH"],
            key=lambda sp: sp.timestamp,
        )
        swing_lows = sorted(
            [sp for sp in swing_points if sp.type == "LOW"],
            key=lambda sp: sp.timestamp,
        )

        if not swing_highs or not swing_lows:
            return []

        # V6.1: Add ATR filter for BOS validation
        atr = _compute_atr(df, period=14)

        events: list[StructureEvent] = []

        # Track structural bias: start with the most recent swing direction
        # +1 = bullish structure (HH, HL pattern), -1 = bearish (LH, LL)
        prior_structure: int = 0  # unknown at start

        # Walk forward through candles; compare each close to the most recent
        # swing level that was confirmed *before* the current candle
        for i in range(1, len(df)):
            current_ts = timestamps[i]

            # Most recent swing HIGH before candle i
            prior_sh = [sp for sp in swing_highs if sp.timestamp < current_ts]
            prior_sl = [sp for sp in swing_lows if sp.timestamp < current_ts]

            if not prior_sh or not prior_sl:
                continue

            latest_sh = prior_sh[-1].price
            latest_sl = prior_sl[-1].price
            close = float(closes[i])

            # ── Bullish break ────────────────────────────────────────────
            # V6.1: Require break to exceed previous swing high by > 0.5 ATR
            if close > latest_sh and (close - latest_sh) > 0.5 * atr.iloc[i]:
                event_type = "CHOCH" if prior_structure == -1 else "BOS"
                events.append(
                    StructureEvent(
                        event_type=event_type,
                        direction="BULLISH",
                        price=float(latest_sh),
                        timestamp=timestamps[i],
                        timeframe=self.timeframe,
                        confirmed=True,
                    )
                )
                prior_structure = 1

            # ── Bearish break ────────────────────────────────────────────
            # V6.1: Require break to exceed previous swing low by > 0.5 ATR
            elif close < latest_sl and (latest_sl - close) > 0.5 * atr.iloc[i]:
                event_type = "CHOCH" if prior_structure == 1 else "BOS"
                events.append(
                    StructureEvent(
                        event_type=event_type,
                        direction="BEARISH",
                        price=float(latest_sl),
                        timestamp=timestamps[i],
                        timeframe=self.timeframe,
                        confirmed=True,
                    )
                )
                prior_structure = -1

        self._log.debug("structure_events_found", count=len(events))
        return events

    # ------------------------------------------------------------------
    # LIQUIDITY SWEEPS
    # ------------------------------------------------------------------

    def find_liquidity_sweeps(
        self, df: pd.DataFrame, swing_points: list[SwingPoint]
    ) -> list[LiquiditySweep]:
        """
        Detect liquidity sweep events at swing point levels.

        Sweep HIGH (LONG_SWEEP — sweeping buy-side liquidity):
            1. candle[i].high > swing_high_price    (wick above the level)
            2. candle[i].close < swing_high_price   (close back below)
            → Institutions swept buy stops, now potentially reversing short

        Sweep LOW (SHORT_SWEEP — sweeping sell-side liquidity):
            1. candle[i].low  < swing_low_price    (wick below the level)
            2. candle[i].close > swing_low_price   (close back above)
            → Institutions swept sell stops, now potentially reversing long

        WHY close-back rule: The defining characteristic of a sweep vs a genuine
        breakout is that price RETURNS beyond the swept level within the same
        candle. A close that remains beyond the level is a break, not a sweep.

        Args:
            df: OHLCV DataFrame.
            swing_points: Combined list of SwingPoints (HIGHs + LOWs).

        Returns:
            List of LiquiditySweep models, chronological.
        """
        if not swing_points:
            return []

        self._validate_df(df)
        df = df.reset_index(drop=True)

        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        n = len(df)

        swing_high_prices = [sp.price for sp in swing_points if sp.type == "HIGH"]
        swing_low_prices = [sp.price for sp in swing_points if sp.type == "LOW"]

        sweeps: list[LiquiditySweep] = []

        for i in range(1, n):
            ts = self._get_timestamp(df, i)
            high_i = float(highs[i])
            low_i = float(lows[i])
            close_i = float(closes[i])

            # ── Sweep of swing HIGHS (LONG_SWEEP) ───────────────────────
            for sh_price in swing_high_prices:
                if high_i > sh_price and close_i < sh_price:
                    sweeps.append(
                        LiquiditySweep(
                            swept_price=sh_price,
                            sweep_high=high_i,
                            close_price=close_i,
                            direction="LONG_SWEEP",
                            timestamp=ts,
                            timeframe=self.timeframe,
                        )
                    )
                    break  # one sweep event per candle per direction

            # ── Sweep of swing LOWS (SHORT_SWEEP) ───────────────────────
            for sl_price in swing_low_prices:
                if low_i < sl_price and close_i > sl_price:
                    sweeps.append(
                        LiquiditySweep(
                            swept_price=sl_price,
                            sweep_high=high_i,  # candle high during the sweep candle
                            close_price=close_i,
                            direction="SHORT_SWEEP",
                            timestamp=ts,
                            timeframe=self.timeframe,
                        )
                    )
                    break

        self._log.debug("liquidity_sweeps_found", count=len(sweeps))
        return sweeps

    # ------------------------------------------------------------------
    # PREMIUM / DISCOUNT RATIO
    # ------------------------------------------------------------------

    def calculate_premium_discount(
        self,
        swing_high: float,
        swing_low: float,
        current_price: float,
    ) -> float:
        """
        Compute the premium/discount ratio of the current price within the
        most recent swing range.

        Formula:
            ratio = (current_price - swing_low) / (swing_high - swing_low)
            clamped to [0.0, 1.0]

        Interpretation:
            0.0  → price AT or BELOW the swing low  (deep discount)
            0.5  → price at the 50% equilibrium (OTE midpoint)
            1.0  → price AT or ABOVE the swing high (premium)

        WHY: SMC entries are only acceptable in the discount zone (< 0.5 for longs,
        > 0.5 for shorts). This ratio is used by the confluence engine to verify
        the entry is at a structurally favourable price.

        Args:
            swing_high: Price of the most recent dominant swing high.
            swing_low: Price of the most recent dominant swing low.
            current_price: Current market price (latest close).

        Returns:
            Float in [0.0, 1.0].

        Raises:
            ValueError: If swing_high <= swing_low (degenerate range).
        """
        if swing_high <= swing_low:
            self._log.warning(
                "premium_discount_degenerate",
                swing_high=swing_high,
                swing_low=swing_low,
            )
            return 0.5  # neutral — cannot compute

        range_size = swing_high - swing_low
        ratio = (current_price - swing_low) / range_size
        return float(np.clip(ratio, 0.0, 1.0))

    # ------------------------------------------------------------------
    # PRIVATE HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_df(df: pd.DataFrame) -> None:
        """
        Validate that the DataFrame has required OHLCV columns and is non-empty.

        Args:
            df: DataFrame to validate.

        Raises:
            ValueError: On missing columns or empty DataFrame.
        """
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")
        if df.empty:
            raise ValueError("DataFrame is empty — cannot run SMC analysis.")

    @staticmethod
    def _get_timestamp(df: pd.DataFrame, i: int) -> datetime:
        """
        Extract a timezone-aware UTC datetime from df['timestamp'] at row i.

        Handles both datetime objects and epoch milliseconds gracefully.

        Args:
            df: OHLCV DataFrame.
            i: Row index.

        Returns:
            timezone-aware datetime in UTC.
        """
        ts = df["timestamp"].iloc[i]
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts
        # Assume epoch milliseconds (common from exchange APIs)
        try:
            return datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return datetime.now(tz=timezone.utc)

    @staticmethod
    def _compute_fill(
        df: pd.DataFrame,
        zone_created_idx: int,
        zone_low: float,
        zone_high: float,
        direction: str,
    ) -> tuple[float, bool]:
        """
        Compute how much of an FVG zone has been filled by subsequent price action.

        For a BULLISH FVG (gap below current price):
            price retracing INTO the zone from above counts as fill.
            fill_pct = how deep into the zone price penetrated.

        For a BEARISH FVG (gap above current price):
            price retracing INTO the zone from below counts as fill.

        Args:
            df: OHLCV DataFrame.
            zone_created_idx: Index of the candle that completed the pattern.
            zone_low: Lower boundary of the FVG zone.
            zone_high: Upper boundary of the FVG zone.
            direction: 'BULLISH' or 'BEARISH'.

        Returns:
            Tuple (fill_pct: float in [0,1], filled: bool).
        """
        zone_size = zone_high - zone_low
        if zone_size <= 0:
            return 0.0, False

        # Examine candles after the zone was created
        future_slice = df.iloc[zone_created_idx + 1 :]
        if future_slice.empty:
            return 0.0, False

        fill_pct = 0.0
        filled = False

        if direction == "BULLISH":
            # Price retraces down into the bullish FVG
            for _, row in future_slice.iterrows():
                if row["low"] <= zone_low:
                    # Fully passed through
                    fill_pct = 1.0
                    filled = True
                    break
                elif row["low"] < zone_high:
                    # Partial fill — how deep into zone
                    penetration = zone_high - row["low"]
                    fill_pct = max(fill_pct, min(penetration / zone_size, 1.0))
        else:  # BEARISH
            # Price retraces up into the bearish FVG
            for _, row in future_slice.iterrows():
                if row["high"] >= zone_high:
                    fill_pct = 1.0
                    filled = True
                    break
                elif row["high"] > zone_low:
                    penetration = row["high"] - zone_low
                    fill_pct = max(fill_pct, min(penetration / zone_size, 1.0))

        return fill_pct, filled
