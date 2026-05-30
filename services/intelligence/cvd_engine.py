"""
APEX v5.1 — Cumulative Volume Delta (CVD) Engine
Used by Citadel, Virtu and top HFT firms to detect real buying/selling pressure.

CVD = Sum of (bullish candle volume) - Sum of (bearish candle volume)
over the last N candles.

If price goes UP but CVD goes DOWN → sellers absorbing buyers → reversal risk.
If price goes UP and CVD goes UP → real buyers driving price → strong trend.

We use 5m candles for precision (20 candles = 100 minutes of pressure).
"""
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


def calculate_cvd(df_5m: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Calculate Cumulative Volume Delta from 5m OHLCV data.
    
    Returns:
        dict with:
          - cvd: float (positive = net buying, negative = net selling)
          - cvd_signal: 'BULLISH' | 'BEARISH' | 'NEUTRAL'
          - divergence: bool (price up but CVD down = bearish divergence)
          - score: int (-2 to +2)
    """
    if df_5m.empty or len(df_5m) < lookback:
        return {"cvd": 0.0, "cvd_signal": "NEUTRAL", "divergence": False, "score": 0}

    recent = df_5m.tail(lookback).copy()

    # Assign volume as positive (bullish candle) or negative (bearish candle)
    recent["delta"] = recent.apply(
        lambda row: row["volume"] if row["close"] >= row["open"] else -row["volume"],
        axis=1
    )
    recent["cum_cvd"] = recent["delta"].cumsum()

    cvd = recent["delta"].sum()
    cvd_pct = cvd / recent["volume"].sum() if recent["volume"].sum() > 0 else 0.0

    # TRUE DIVERGENCE: Higher High in Price, Lower High in CVD
    mid = lookback // 2
    period1 = recent.iloc[:mid]
    period2 = recent.iloc[mid:]
    
    p1_peak_idx = period1["high"].idxmax()
    p2_peak_idx = period2["high"].idxmax()
    
    p1_high = period1.loc[p1_peak_idx, "high"]
    p2_high = period2.loc[p2_peak_idx, "high"]
    
    p1_cvd = period1.loc[p1_peak_idx, "cum_cvd"]
    p2_cvd = period2.loc[p2_peak_idx, "cum_cvd"]
    
    divergence = bool((p2_high > p1_high) and (p2_cvd < p1_cvd))

    # Score
    if cvd_pct > 0.20:
        signal = "BULLISH"
        score = 2
    elif cvd_pct > 0.05:
        signal = "BULLISH"
        score = 1
    elif cvd_pct < -0.20:
        signal = "BEARISH"
        score = -2
    elif cvd_pct < -0.05:
        signal = "BEARISH"
        score = -1
    else:
        signal = "NEUTRAL"
        score = 0

    # Penalize true divergence heavily
    if divergence:
        score -= 25  # Massive penalty for true exhaustion
        logger.debug(f"CVD True Bearish Divergence: HH Price ({p1_high:.2f}->{p2_high:.2f}), LH CVD ({p1_cvd:.0f}->{p2_cvd:.0f})")

    score = max(-25, min(2, score))

    logger.debug(f"CVD={cvd:.0f} ({cvd_pct:+.1%}) | Signal={signal} | Divergence={divergence}")

    return {
        "cvd": cvd,
        "cvd_pct": cvd_pct,
        "cvd_signal": signal,
        "divergence": divergence,
        "score": score,
    }
