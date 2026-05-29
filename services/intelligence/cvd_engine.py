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

    cvd = recent["delta"].sum()
    cvd_pct = cvd / recent["volume"].sum() if recent["volume"].sum() > 0 else 0.0

    # Price direction over same period
    price_change = recent["close"].iloc[-1] - recent["close"].iloc[0]

    # Detect divergence: price up but CVD down (hidden selling)
    divergence = price_change > 0 and cvd < 0

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

    # Penalize divergence heavily
    if divergence:
        score -= 2
        logger.debug(f"CVD Bearish Divergence detected: price↑ but CVD={cvd:.0f}")

    score = max(-2, min(2, score))

    logger.debug(f"CVD={cvd:.0f} ({cvd_pct:+.1%}) | Signal={signal} | Divergence={divergence}")

    return {
        "cvd": cvd,
        "cvd_pct": cvd_pct,
        "cvd_signal": signal,
        "divergence": divergence,
        "score": score,
    }
