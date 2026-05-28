"""
APEX v5.0 — Advanced Technical Indicators
VWAP, EMA Ribbon, RSI Divergence, CVD, Bollinger Bands, Fibonacci
All computed on pandas DataFrames. No external dependencies beyond numpy/pandas.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# VWAP — Volume Weighted Average Price
# ─────────────────────────────────────────────────────────────────────────────

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Calculate VWAP (Volume Weighted Average Price).
    VWAP = Σ(typical_price × volume) / Σ(volume)
    """
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    vwap = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()
    return vwap


def vwap_signal(df: pd.DataFrame) -> dict:
    """
    Returns VWAP-based signal info.
    - Price above VWAP = bullish bias
    - Price below VWAP = bearish bias
    - Distance from VWAP = overextension warning
    """
    vwap = calculate_vwap(df)
    current_price = df['close'].iloc[-1]
    current_vwap = vwap.iloc[-1]

    distance_pct = (current_price - current_vwap) / current_vwap * 100
    above_vwap = current_price > current_vwap

    # Score: +1 if above VWAP, -1 if below, 0 if too extended
    if above_vwap and abs(distance_pct) < 3.0:
        score = 1
        label = "ABOVE_VWAP"
    elif not above_vwap and abs(distance_pct) < 3.0:
        score = -1
        label = "BELOW_VWAP"
    elif above_vwap and abs(distance_pct) >= 3.0:
        score = 0
        label = "EXTENDED_ABOVE"
    else:
        score = 0
        label = "EXTENDED_BELOW"

    return {
        "vwap": round(current_vwap, 6),
        "price": round(current_price, 6),
        "distance_pct": round(distance_pct, 2),
        "above_vwap": above_vwap,
        "label": label,
        "score": score,  # -1, 0, +1
    }


# ─────────────────────────────────────────────────────────────────────────────
# EMA RIBBON — 6 EMAs (5/8/13/21/34/55)
# ─────────────────────────────────────────────────────────────────────────────

EMA_PERIODS = [5, 8, 13, 21, 34, 55]


def calculate_ema_ribbon(df: pd.DataFrame) -> dict:
    """
    EMA Ribbon: 6 EMAs. If all aligned upward (5>8>13>21>34>55) = STRONG BULL.
    Returns alignment score and trend direction.
    """
    close = df['close']
    emas = {}
    for period in EMA_PERIODS:
        if len(close) >= period:
            emas[period] = close.ewm(span=period, adjust=False).mean().iloc[-1]

    if len(emas) < 4:
        return {"score": 0, "label": "INSUFFICIENT_DATA", "emas": emas}

    values = [emas[p] for p in EMA_PERIODS if p in emas]

    # Check if sorted descending (bull) or ascending (bear)
    bull_aligned = all(values[i] > values[i+1] for i in range(len(values)-1))
    bear_aligned = all(values[i] < values[i+1] for i in range(len(values)-1))

    # Current price vs EMAs
    current_price = df['close'].iloc[-1]
    above_all = all(current_price > v for v in values)
    below_all = all(current_price < v for v in values)

    if bull_aligned and above_all:
        score = 2
        label = "STRONG_BULL_RIBBON"
    elif bull_aligned:
        score = 1
        label = "BULL_RIBBON"
    elif bear_aligned and below_all:
        score = -2
        label = "STRONG_BEAR_RIBBON"
    elif bear_aligned:
        score = -1
        label = "BEAR_RIBBON"
    else:
        score = 0
        label = "MIXED_RIBBON"

    return {
        "score": score,
        "label": label,
        "emas": {str(k): round(v, 6) for k, v in emas.items()},
        "bull_aligned": bull_aligned,
        "bear_aligned": bear_aligned,
    }


# ─────────────────────────────────────────────────────────────────────────────
# RSI DIVERGENCE — Bullish & Bearish
# ─────────────────────────────────────────────────────────────────────────────

def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 30) -> dict:
    """
    Detect RSI divergence over the last N candles.
    Bullish: price makes lower low, RSI makes higher low → buy signal
    Bearish: price makes higher high, RSI makes lower high → sell signal
    """
    if len(df) < lookback + 14:
        return {"type": "NONE", "score": 0, "label": "INSUFFICIENT_DATA"}

    rsi = calculate_rsi(df['close'])
    recent_df = df.tail(lookback)
    recent_rsi = rsi.tail(lookback)

    price_min_idx = recent_df['low'].idxmin()
    price_max_idx = recent_df['high'].idxmax()

    # Bullish divergence: last low vs previous low
    price_lows = recent_df['low'].nsmallest(2)
    if len(price_lows) >= 2:
        price_low_1 = price_lows.iloc[1]   # older low
        price_low_2 = price_lows.iloc[0]   # recent low
        rsi_at_low_1 = recent_rsi.loc[price_lows.index[1]] if price_lows.index[1] in recent_rsi.index else 50
        rsi_at_low_2 = recent_rsi.loc[price_lows.index[0]] if price_lows.index[0] in recent_rsi.index else 50

        # Bullish divergence: price lower low but RSI higher low
        if price_low_2 < price_low_1 and rsi_at_low_2 > rsi_at_low_1 and rsi_at_low_2 < 45:
            return {"type": "BULLISH", "score": 2, "label": "BULLISH_DIVERGENCE",
                    "rsi_now": round(recent_rsi.iloc[-1], 1)}

    # Bearish divergence: last high vs previous high
    price_highs = recent_df['high'].nlargest(2)
    if len(price_highs) >= 2:
        price_high_1 = price_highs.iloc[1]   # older high
        price_high_2 = price_highs.iloc[0]   # recent high
        rsi_at_high_1 = recent_rsi.loc[price_highs.index[1]] if price_highs.index[1] in recent_rsi.index else 50
        rsi_at_high_2 = recent_rsi.loc[price_highs.index[0]] if price_highs.index[0] in recent_rsi.index else 50

        # Bearish divergence: price higher high but RSI lower high
        if price_high_2 > price_high_1 and rsi_at_high_2 < rsi_at_high_1 and rsi_at_high_2 > 55:
            return {"type": "BEARISH", "score": -2, "label": "BEARISH_DIVERGENCE",
                    "rsi_now": round(recent_rsi.iloc[-1], 1)}

    rsi_now = recent_rsi.iloc[-1]
    return {
        "type": "NONE",
        "score": 1 if rsi_now < 35 else (-1 if rsi_now > 70 else 0),
        "label": "OVERSOLD" if rsi_now < 35 else ("OVERBOUGHT" if rsi_now > 70 else "NEUTRAL"),
        "rsi_now": round(rsi_now, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BOLLINGER BANDS SQUEEZE
# ─────────────────────────────────────────────────────────────────────────────

def bollinger_bands_signal(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> dict:
    """
    Bollinger Bands Squeeze detector.
    Squeeze = bands very narrow (low volatility before big move).
    """
    close = df['close']
    if len(close) < period * 2:
        return {"squeeze": False, "score": 0, "label": "INSUFFICIENT_DATA"}

    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    bandwidth = (upper - lower) / sma * 100

    current_bw = bandwidth.iloc[-1]
    avg_bw = bandwidth.rolling(50).mean().iloc[-1]
    min_bw_20 = bandwidth.rolling(20).min().iloc[-1]

    # Squeeze: bandwidth is at 20-period low AND below average
    squeeze = (current_bw == min_bw_20) and (current_bw < avg_bw * 0.7)

    current_price = close.iloc[-1]
    current_sma = sma.iloc[-1]
    price_above_mid = current_price > current_sma

    if squeeze and price_above_mid:
        score = 1
        label = "SQUEEZE_BULL"
    elif squeeze and not price_above_mid:
        score = -1
        label = "SQUEEZE_BEAR"
    elif current_price > upper.iloc[-1]:
        score = -1
        label = "ABOVE_UPPER_BB"
    elif current_price < lower.iloc[-1]:
        score = 1
        label = "BELOW_LOWER_BB"
    else:
        score = 0
        label = "NORMAL"

    return {
        "squeeze": squeeze,
        "bandwidth": round(current_bw, 2),
        "avg_bandwidth": round(avg_bw, 2) if not pd.isna(avg_bw) else 0,
        "upper": round(upper.iloc[-1], 6),
        "lower": round(lower.iloc[-1], 6),
        "mid": round(current_sma, 6),
        "score": score,
        "label": label,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FIBONACCI AUTO-LEVELS
# ─────────────────────────────────────────────────────────────────────────────

FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]


def fibonacci_levels(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    Auto-calculate Fibonacci retracement levels from recent swing high/low.
    Returns nearest fib level and if price is at key support (0.382/0.5/0.618).
    """
    if len(df) < lookback:
        return {"at_key_level": False, "score": 0, "nearest_fib": None}

    recent = df.tail(lookback)
    swing_high = recent['high'].max()
    swing_low = recent['low'].min()
    current_price = df['close'].iloc[-1]

    if swing_high == swing_low:
        return {"at_key_level": False, "score": 0, "nearest_fib": None}

    range_size = swing_high - swing_low
    fib_prices = {level: swing_high - (level * range_size) for level in FIB_LEVELS}

    # Find nearest fib level
    nearest_level = min(FIB_LEVELS, key=lambda l: abs(fib_prices[l] - current_price))
    distance_to_nearest = abs(fib_prices[nearest_level] - current_price) / current_price * 100

    # Key support levels for LONG: 0.382, 0.5, 0.618
    at_key_level = nearest_level in [0.382, 0.5, 0.618] and distance_to_nearest < 1.5

    score = 1 if at_key_level else 0

    return {
        "swing_high": round(swing_high, 6),
        "swing_low": round(swing_low, 6),
        "nearest_fib": nearest_level,
        "nearest_price": round(fib_prices[nearest_level], 6),
        "distance_pct": round(distance_to_nearest, 2),
        "at_key_level": at_key_level,
        "score": score,
        "fib_prices": {str(k): round(v, 6) for k, v in fib_prices.items()},
    }


# ─────────────────────────────────────────────────────────────────────────────
# VOLUME SPIKE DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

def volume_spike_signal(df: pd.DataFrame, period: int = 20, threshold: float = 2.0) -> dict:
    """
    Detects abnormal volume spikes (institutional activity).
    Spike = current volume > threshold × average volume.
    """
    if len(df) < period + 5:
        return {"spike": False, "ratio": 1.0, "score": 0}

    avg_volume = df['volume'].rolling(period).mean().iloc[-1]
    current_volume = df['volume'].iloc[-1]

    if avg_volume == 0:
        return {"spike": False, "ratio": 1.0, "score": 0}

    ratio = current_volume / avg_volume
    spike = ratio >= threshold

    # Bullish spike: price up + volume spike
    price_up = df['close'].iloc[-1] > df['open'].iloc[-1]
    if spike and price_up:
        score = 1
        label = "BULLISH_VOLUME_SPIKE"
    elif spike and not price_up:
        score = -1
        label = "BEARISH_VOLUME_SPIKE"
    else:
        score = 0
        label = "NORMAL_VOLUME"

    return {
        "spike": spike,
        "ratio": round(ratio, 2),
        "score": score,
        "label": label,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MASTER INDICATOR BUNDLE — run all at once
# ─────────────────────────────────────────────────────────────────────────────

def run_all_indicators(df: pd.DataFrame, symbol: str = "") -> dict:
    """
    Run all indicators in one call. Returns unified dict with all signals.
    """
    results = {}

    try:
        results["vwap"] = vwap_signal(df)
    except Exception as e:
        logger.warning(f"VWAP error on {symbol}: {e}")
        results["vwap"] = {"score": 0, "label": "ERROR"}

    try:
        results["ema_ribbon"] = calculate_ema_ribbon(df)
    except Exception as e:
        logger.warning(f"EMA Ribbon error on {symbol}: {e}")
        results["ema_ribbon"] = {"score": 0, "label": "ERROR"}

    try:
        results["rsi_divergence"] = detect_rsi_divergence(df)
    except Exception as e:
        logger.warning(f"RSI Divergence error on {symbol}: {e}")
        results["rsi_divergence"] = {"score": 0, "label": "ERROR", "rsi_now": 50}

    try:
        results["bollinger"] = bollinger_bands_signal(df)
    except Exception as e:
        logger.warning(f"Bollinger error on {symbol}: {e}")
        results["bollinger"] = {"score": 0, "label": "ERROR"}

    try:
        results["fibonacci"] = fibonacci_levels(df)
    except Exception as e:
        logger.warning(f"Fibonacci error on {symbol}: {e}")
        results["fibonacci"] = {"score": 0, "at_key_level": False}

    try:
        results["volume"] = volume_spike_signal(df)
    except Exception as e:
        logger.warning(f"Volume error on {symbol}: {e}")
        results["volume"] = {"score": 0, "label": "ERROR"}

    # Composite bull score (sum of all positive indicators)
    total_score = sum([
        results["vwap"].get("score", 0),
        results["ema_ribbon"].get("score", 0),
        results["rsi_divergence"].get("score", 0),
        results["bollinger"].get("score", 0),
        results["fibonacci"].get("score", 0),
        results["volume"].get("score", 0),
    ])

    results["composite_score"] = total_score
    results["symbol"] = symbol

    return results
