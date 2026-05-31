"""
APEX v5.0 — Live Market Data
Funding Rate, Open Interest, Fear & Greed, BTC Dominance
All via free public APIs — no auth required.
"""

import asyncio
import aiohttp
import structlog
from datetime import datetime

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# FEAR & GREED INDEX — alternative.me (free, no auth)
# ─────────────────────────────────────────────────────────────────────────────

_fear_greed_cache = {"value": None, "label": None, "fetched_at": None}


async def get_fear_greed() -> dict:
    """Fetch Crypto Fear & Greed Index. Cached for 1 hour."""
    now = datetime.utcnow()
    if (_fear_greed_cache["fetched_at"] and
            (now - _fear_greed_cache["fetched_at"]).total_seconds() < 3600):
        return _fear_greed_cache

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get("https://api.alternative.me/fng/?limit=1") as resp:
                data = await resp.json()
                item = data["data"][0]
                value = int(item["value"])
                label = item["value_classification"]

                _fear_greed_cache.update({
                    "value": value,
                    "label": label,
                    "fetched_at": now,
                })

                logger.info(f"Fear & Greed: {value} ({label})")
                return _fear_greed_cache
    except Exception as e:
        logger.warning(f"Fear & Greed fetch failed: {e}")
        return {"value": 50, "label": "Neutral", "fetched_at": None}


def fear_greed_score(fg_value: int) -> tuple[int, str]:
    """
    Returns (score, emoji) based on Fear & Greed value.
    For LONG signals: Extreme Fear is bullish (buy the fear).
    """
    if fg_value <= 25:
        return 2, "😱 Extreme Fear"    # Best time to buy
    elif fg_value <= 45:
        return 1, "😨 Fear"            # Good time
    elif fg_value <= 55:
        return 0, "😐 Neutral"
    elif fg_value <= 75:
        return -1, "😏 Greed"          # Caution
    else:
        return -2, "🤑 Extreme Greed"  # Danger zone


# ─────────────────────────────────────────────────────────────────────────────
# FUNDING RATE — Binance Futures API (free, no auth)
# ─────────────────────────────────────────────────────────────────────────────

_funding_cache: dict = {}


async def get_funding_rate(symbol: str) -> dict:
    """
    Get current funding rate for a futures symbol.
    symbol: 'BTC/USDT' → converts to 'BTCUSDT'
    Cached per symbol for 30 minutes.
    """
    now = datetime.utcnow()
    binance_symbol = symbol.replace("/", "")

    cached = _funding_cache.get(binance_symbol)
    if cached and (now - cached["fetched_at"]).total_seconds() < 1800:
        return cached

    try:
        url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={binance_symbol}&limit=1"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                data = await resp.json()
                if not data:
                    raise Exception("Empty response")

                rate = float(data[0]["fundingRate"]) * 100  # Convert to %

                result = {
                    "symbol": symbol,
                    "rate_pct": round(rate, 4),
                    "fetched_at": now,
                    "is_valid": True,
                }
                _funding_cache[binance_symbol] = result
                return result
    except Exception as e:
        logger.debug(f"Funding rate fetch failed for {symbol}: {e}")
        return {"symbol": symbol, "rate_pct": 0.0, "fetched_at": now, "is_valid": False}


def funding_rate_score(rate_pct: float) -> tuple[int, str]:
    """
    Positive funding = longs paying shorts (overheated longs → bearish)
    Negative funding = shorts paying longs (overheated shorts → bullish)
    """
    if rate_pct <= -0.05:
        return 2, f"🟢 {rate_pct:+.3f}% (Shorts squeezed)"
    elif rate_pct <= 0.0:
        return 1, f"🟡 {rate_pct:+.3f}% (Healthy)"
    elif rate_pct <= 0.05:
        return 0, f"🟡 {rate_pct:+.3f}% (Neutral)"
    elif rate_pct <= 0.1:
        return -1, f"🟠 {rate_pct:+.3f}% (Longs paying)"
    else:
        return -2, f"🔴 {rate_pct:+.3f}% (Overheated)"


# ─────────────────────────────────────────────────────────────────────────────
# FUNDING RATE — Coinglass API / Binance Fallback
# ─────────────────────────────────────────────────────────────────────────────

_funding_cache: dict = {}

async def get_funding_rate(symbol: str) -> dict:
    """
    Get current funding rate. Tries Coinglass API first, falls back to Binance Futures.
    Cached per symbol for 30 minutes.
    """
    now = datetime.utcnow()
    symbol_base = symbol.split('/')[0]
    binance_symbol = symbol.replace("/", "")

    cached = _funding_cache.get(binance_symbol)
    if cached and (now - cached["fetched_at"]).total_seconds() < 1800:
        return cached

    from shared.config import get_config
    config = get_config()
    cg_key = config.data_sources.coinglass_api_key.get_secret_value() if config.data_sources.coinglass_api_key else ""

    rate_pct = 0.0
    success = False

    # 1. Try Coinglass
    if cg_key:
        try:
            url = f"https://open-api.coinglass.com/public/v2/funding?exName=Binance&symbol={symbol_base}"
            headers = {"accept": "application/json", "coinglassSecret": cg_key}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and data.get("success") and "data" in data and len(data["data"]) > 0:
                            # Usually data is a list of funding rates
                            item = data["data"][0] if isinstance(data["data"], list) else data["data"]
                            # Depending on Coinglass version, field is uMarginFundingRate or fundingRate
                            val = item.get("uMarginFundingRate", item.get("fundingRate", 0.0))
                            rate_pct = float(val)
                            # If the API returns it as decimal (e.g. 0.0001), convert to %
                            if abs(rate_pct) < 0.1: rate_pct *= 100 
                            success = True
        except Exception as e:
            logger.debug(f"Coinglass Funding fetch failed for {symbol}: {e}")

    # 2. Fallback to Bybit
    if not success:
        try:
            url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={binance_symbol}"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if (data and data.get("retCode") == 0 and "result" in data 
                                and isinstance(data["result"], dict) and "list" in data["result"] 
                                and isinstance(data["result"]["list"], list) and len(data["result"]["list"]) > 0):
                            rate_str = data["result"]["list"][0].get("fundingRate")
                            if rate_str is not None:
                                rate = float(rate_str) * 100  # Convert to %
                                rate_pct = rate
                                success = True
        except Exception as e:
            logger.debug(f"Bybit Funding fetch failed for {symbol}: {e}")

    result = {
        "symbol": symbol,
        "rate_pct": round(rate_pct, 4) if success else 0.0,
        "fetched_at": now,
        "is_valid": success,
    }
    _funding_cache[binance_symbol] = result
    return result

def funding_rate_score(rate_pct: float) -> tuple[int, str]:
    """
    Positive funding = longs paying shorts (overheated longs → bearish)
    Negative funding = shorts paying longs (overheated shorts → bullish)
    """
    if rate_pct <= -0.05:
        return 2, f"🟢 {rate_pct:+.3f}% (Shorts squeezed)"
    elif rate_pct <= 0.0:
        return 1, f"🟡 {rate_pct:+.3f}% (Healthy)"
    elif rate_pct <= 0.05:
        return 0, f"🟡 {rate_pct:+.3f}% (Neutral)"
    elif rate_pct <= 0.1:
        return -1, f"🟠 {rate_pct:+.3f}% (Longs paying)"
    else:
        return -2, f"🔴 {rate_pct:+.3f}% (Overheated)"


# ─────────────────────────────────────────────────────────────────────────────
# OPEN INTEREST — Coinglass API / Binance Fallback
# ─────────────────────────────────────────────────────────────────────────────

_oi_cache: dict = {}

async def get_open_interest_change(symbol: str) -> dict:
    """
    Get OI change over last 4 hours. Tries Coinglass, falls back to Binance.
    """
    now = datetime.utcnow()
    symbol_base = symbol.split('/')[0]
    binance_symbol = symbol.replace("/", "")

    cached = _oi_cache.get(binance_symbol)
    if cached and (now - cached["fetched_at"]).total_seconds() < 900:  # 15 min cache
        return cached

    from shared.config import get_config
    config = get_config()
    cg_key = config.data_sources.coinglass_api_key.get_secret_value() if config.data_sources.coinglass_api_key else ""

    change_pct = 0.0
    oi_now = 0.0
    success = False

    # 1. Try Coinglass
    if cg_key:
        try:
            url = f"https://open-api.coinglass.com/public/v2/open_interest?symbol={symbol_base}"
            headers = {"accept": "application/json", "coinglassSecret": cg_key}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and data.get("success") and "data" in data and len(data["data"]) > 0:
                            item = data["data"][0] if isinstance(data["data"], list) else data["data"]
                            # Use h1OIChangePercent or h4OIChangePercent if available
                            change_pct = float(item.get("h4OIChangePercent", item.get("h1OIChangePercent", 0.0)))
                            oi_now = float(item.get("openInterestAmount", 0.0))
                            success = True
        except Exception as e:
            logger.debug(f"Coinglass OI fetch failed for {symbol}: {e}")

    # 2. Fallback to Bybit
    if not success:
        try:
            url = f"https://api.bybit.com/v5/market/open-interest?category=linear&symbol={binance_symbol}&intervalTime=1h&limit=5"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("retCode") == 0 and "result" in data and "list" in data["result"]:
                            lst = data["result"]["list"]
                            if len(lst) >= 2:
                                # Bybit list is sorted descending (latest first)
                                oi_now_val = float(lst[0]["openInterest"])
                                oi_prev_val = float(lst[-1]["openInterest"])
                                change_pct = (oi_now_val - oi_prev_val) / oi_prev_val * 100 if oi_prev_val > 0 else 0.0
                                oi_now = oi_now_val
                                success = True
        except Exception as e:
            logger.debug(f"Bybit OI fetch failed for {symbol}: {e}")

    result = {
        "symbol": symbol,
        "oi_now": oi_now,
        "change_pct": round(change_pct, 2) if success else 0.0,
        "fetched_at": now,
        "is_valid": success,
    }
    _oi_cache[binance_symbol] = result
    return result


def oi_score(change_pct: float, price_change_pct: float) -> tuple[int, str]:
    """OI interpretation based on OI + price direction."""
    if change_pct > 3 and price_change_pct > 0:
        return 2, f"📈 OI +{change_pct:.1f}% (Trend healthy)"
    elif change_pct > 3 and price_change_pct < 0:
        return -1, f"⚠️ OI +{change_pct:.1f}% (Shorts building)"
    elif change_pct < -3:
        return 1, f"📉 OI {change_pct:.1f}% (Positions closing)"
    else:
        return 0, f"➡️ OI {change_pct:+.1f}% (Stable)"


# ─────────────────────────────────────────────────────────────────────────────
# BTC DOMINANCE — CoinGecko (free)
# ─────────────────────────────────────────────────────────────────────────────

_dominance_cache = {"dominance": None, "fetched_at": None}


async def get_btc_dominance() -> dict:
    """BTC.D — cached for 30 min. Rising = bad for alts, falling = alt season."""
    now = datetime.utcnow()
    if (_dominance_cache["fetched_at"] and
            (now - _dominance_cache["fetched_at"]).total_seconds() < 1800):
        return _dominance_cache

    try:
        url = "https://api.coingecko.com/api/v3/global"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.get(url, headers={"Accept": "application/json"}) as resp:
                data = await resp.json()
                dominance = data["data"]["market_cap_percentage"]["btc"]
                _dominance_cache.update({
                    "dominance": round(dominance, 1),
                    "fetched_at": now,
                })
                logger.info(f"BTC Dominance: {dominance:.1f}%")
                return _dominance_cache
    except Exception as e:
        logger.warning(f"BTC Dominance fetch failed: {e}")
        return {"dominance": 55.0, "fetched_at": None}


def dominance_score(btc_dominance: float, symbol: str) -> tuple[int, str]:
    """
    For non-BTC pairs: high BTC.D = bad for alts.
    For BTC: BTC.D doesn't matter.
    """
    is_btc = "BTC" in symbol.upper() and "BTCB" not in symbol.upper()
    if is_btc:
        return 0, f"₿ BTC.D: {btc_dominance}% (N/A for BTC)"

    if btc_dominance < 48:
        return 2, f"🟢 BTC.D: {btc_dominance}% (Alt Season!)"
    elif btc_dominance < 52:
        return 1, f"🟡 BTC.D: {btc_dominance}% (Neutral)"
    elif btc_dominance < 58:
        return 0, f"🟠 BTC.D: {btc_dominance}% (BTC leads)"
    else:
        return -2, f"🔴 BTC.D: {btc_dominance}% (Avoid alts)"


# ─────────────────────────────────────────────────────────────────────────────
# MASTER FETCH — get all market context at once
# ─────────────────────────────────────────────────────────────────────────────

async def get_market_context(symbol: str, price_change_pct: float = 0.0) -> dict:
    """
    Fetch all external market data concurrently.
    Returns unified context dict.
    """
    fg, funding, oi, dominance = await asyncio.gather(
        get_fear_greed(),
        get_funding_rate(symbol),
        get_open_interest_change(symbol),
        get_btc_dominance(),
        return_exceptions=True,
    )

    # Safely handle any exceptions from gather
    if isinstance(fg, Exception):
        fg = {"value": 50, "label": "Neutral"}
    if isinstance(funding, Exception):
        funding = {"rate_pct": 0.0}
    if isinstance(oi, Exception):
        oi = {"change_pct": 0.0}
    if isinstance(dominance, Exception):
        dominance = {"dominance": 55.0}

    fg_score, fg_label = fear_greed_score(fg.get("value", 50))
    
    fund_score, fund_label = funding_rate_score(funding.get("rate_pct", 0.0))
    if funding.get("is_valid", True) is False:
        fund_score, fund_label = 0, "⚪ N/A"
        
    oi_score_val, oi_label = oi_score(oi.get("change_pct", 0.0), price_change_pct)
    if oi.get("is_valid", True) is False:
        oi_score_val, oi_label = 0, "⚪ N/A"
        
    dom_score, dom_label = dominance_score(dominance.get("dominance", 55.0), symbol)

    return {
        "fear_greed": {
            "value": fg.get("value", 50),
            "label": fg_label,
            "score": fg_score,
        },
        "funding": {
            "rate_pct": funding.get("rate_pct", 0.0),
            "label": fund_label,
            "score": fund_score,
            "is_valid": funding.get("is_valid", False),
        },
        "open_interest": {
            "change_pct": oi.get("change_pct", 0.0),
            "label": oi_label,
            "score": oi_score_val,
            "is_valid": oi.get("is_valid", False),
        },
        "btc_dominance": {
            "value": dominance.get("dominance", 55.0),
            "label": dom_label,
            "score": dom_score,
        },
        "total_context_score": 0 if (funding.get("is_valid", True) is False and oi.get("is_valid", True) is False) else (fg_score + fund_score + oi_score_val + dom_score),
    }
