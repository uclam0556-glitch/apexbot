"""
APEX — Cumulative Volume Delta (CVD) Engine v2 (audit-fixes-v11.1)

Two data tiers:

  TIER 1 (real order flow): Binance klines expose `taker buy base volume` —
  the actual aggressor side of every trade in the candle.
      delta = taker_buy - taker_sell = 2 * taker_buy - total_volume
  This is true CVD as used on professional flow desks, obtained without
  websockets or tick storage.

  TIER 2 (fallback proxy): candle-color heuristic (green candle = +volume).
  Used only when the Binance REST call fails or the symbol isn't listed.

Both tiers share one scorer, so every consumer (direction selector, V7
scoring, chop index, pre-route gate) keeps the same contract:
    {cvd, cvd_pct, cvd_signal, divergence, score, source}
"""
import time

import aiohttp
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

BINANCE_SPOT_KLINES = "https://api.binance.com/api/v3/klines"
_klines_cache: dict = {}
_KLINES_CACHE_TTL = 60.0  # seconds; scan cadence is 300s, pre-route checks reuse it


def _score_cvd(recent: pd.DataFrame, source: str) -> dict:
    """
    Shared scorer. `recent` must contain columns: high, volume, delta.
    delta = signed buy/sell volume per candle (real taker flow or proxy).
    """
    recent = recent.copy()
    recent["cum_cvd"] = recent["delta"].cumsum()

    cvd = float(recent["delta"].sum())
    total_vol = float(recent["volume"].sum())
    cvd_pct = cvd / total_vol if total_vol > 0 else 0.0

    # TRUE DIVERGENCE: Higher High in price, Lower High in cumulative CVD
    lookback = len(recent)
    mid = lookback // 2
    period1 = recent.iloc[:mid]
    period2 = recent.iloc[mid:]

    divergence = False
    p1_high = p2_high = p1_cvd = p2_cvd = 0.0
    if len(period1) > 0 and len(period2) > 0:
        p1_peak_idx = period1["high"].idxmax()
        p2_peak_idx = period2["high"].idxmax()
        p1_high = period1.loc[p1_peak_idx, "high"]
        p2_high = period2.loc[p2_peak_idx, "high"]
        p1_cvd = period1.loc[p1_peak_idx, "cum_cvd"]
        p2_cvd = period2.loc[p2_peak_idx, "cum_cvd"]
        divergence = bool((p2_high > p1_high) and (p2_cvd < p1_cvd))

    # Real taker flow is less noisy than the candle-color proxy, so tighter
    # thresholds are statistically meaningful for it.
    strong, weak = (0.12, 0.03) if source == "taker_flow" else (0.20, 0.05)

    if cvd_pct > strong:
        signal, score = "BULLISH", 2
    elif cvd_pct > weak:
        signal, score = "BULLISH", 1
    elif cvd_pct < -strong:
        signal, score = "BEARISH", -2
    elif cvd_pct < -weak:
        signal, score = "BEARISH", -1
    else:
        signal, score = "NEUTRAL", 0

    if divergence:
        score -= 25  # exhaustion: price pushing highs without aggressive buyers
        logger.info(
            f"CVD True Bearish Divergence ({source}): HH Price ({p1_high:.6g}->{p2_high:.6g}), "
            f"LH CVD ({p1_cvd:.0f}->{p2_cvd:.0f})"
        )

    score = max(-25, min(2, score))

    return {
        "cvd": cvd,
        "cvd_pct": cvd_pct,
        "cvd_signal": signal,
        "divergence": divergence,
        "score": score,
        "source": source,
    }


def calculate_cvd(df_5m: pd.DataFrame, lookback: int = 20) -> dict:
    """
    TIER 2 fallback: candle-color proxy CVD from OHLCV.
    Kept for backwards compatibility and as the offline fallback path.
    """
    if df_5m is None or df_5m.empty or len(df_5m) < lookback:
        return {"cvd": 0.0, "cvd_pct": 0.0, "cvd_signal": "NEUTRAL", "divergence": False, "score": 0, "source": "proxy"}

    recent = df_5m.tail(lookback).copy()
    recent["delta"] = recent.apply(
        lambda row: row["volume"] if row["close"] >= row["open"] else -row["volume"],
        axis=1
    )
    return _score_cvd(recent, source="proxy")


async def _fetch_binance_klines(symbol: str, interval: str, limit: int) -> list | None:
    formatted = symbol.replace("/", "").upper()
    cache_key = f"{formatted}_{interval}_{limit}"
    cached = _klines_cache.get(cache_key)
    now = time.time()
    if cached and now - cached["time"] < _KLINES_CACHE_TTL:
        return cached["data"]

    params = {"symbol": formatted, "interval": interval, "limit": limit}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(BINANCE_SPOT_KLINES, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                _klines_cache[cache_key] = {"time": now, "data": data}
                return data
    except Exception as e:
        logger.debug(f"binance_klines_fetch_failed symbol={symbol}: {e}")
        return None


async def calculate_cvd_real(symbol: str, lookback: int = 20,
                             fallback_df: pd.DataFrame | None = None,
                             interval: str = "5m") -> dict:
    """
    TIER 1: real CVD from Binance taker buy volume (kline field [9]).
    Falls back to the candle-color proxy on any failure.
    """
    klines = await _fetch_binance_klines(symbol, interval, lookback)
    if klines and len(klines) >= max(6, lookback // 2):
        try:
            rows = []
            for k in klines:
                volume = float(k[5])
                taker_buy = float(k[9])
                rows.append({
                    "high": float(k[2]),
                    "close": float(k[4]),
                    "open": float(k[1]),
                    "volume": volume,
                    # delta = taker_buy - taker_sell = 2*taker_buy - volume
                    "delta": 2.0 * taker_buy - volume,
                })
            recent = pd.DataFrame(rows)
            result = _score_cvd(recent, source="taker_flow")
            logger.debug(
                f"CVD(real) {symbol}: {result['cvd_signal']} pct={result['cvd_pct']:+.1%} "
                f"div={result['divergence']}"
            )
            return result
        except (IndexError, ValueError, KeyError) as parse_err:
            logger.warning(f"CVD kline parse failed for {symbol}, falling back to proxy: {parse_err}")

    return calculate_cvd(fallback_df, lookback=lookback)
