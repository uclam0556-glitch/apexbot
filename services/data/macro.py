"""
APEX Trading System v4.0
Macro Data Pipeline.

Fetches traditional finance macro indicators:
- US Dollar Index (DXY) via FRED API
- Gold futures via Yahoo Finance
- BTC Dominance via CoinGecko
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx

from shared.config import get_config
from shared.database import get_redis, RedisKeys

logger = logging.getLogger(__name__)
_config = get_config()


class MacroDataPipeline:
    """
    Fetches raw macro data to feed into the Correlation Engine.
    """

    def __init__(self) -> None:
        self.redis = get_redis()
        self.fred_key = _config.datasources.fred_api_key.get_secret_value() if _config.datasources.fred_api_key else ""
        
    async def fetch_dxy(self) -> float | None:
        """
        Fetches current DXY value from FRED API.
        """
        if not self.fred_key:
            return 104.50 # Fallback
            
        cache_key = "macro_dxy_current"
        cached = await self.redis.get(cache_key)
        if cached:
            return float(cached)
            
        # FRED Series ID for DXY: DTWEXBGS (Broad Dollar Index, daily)
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": "DTWEXBGS",
            "api_key": self.fred_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    val = float(data["observations"][0]["value"])
                    await self.redis.set(cache_key, str(val), ex=3600) # 1h cache
                    return val
        except Exception as e:
            logger.error(f"FRED DXY fetch error: {e}")
            
        return 104.50

    async def fetch_gold(self) -> float | None:
        """
        Fetches Gold from Yahoo Finance API (GC=F).
        """
        cache_key = "macro_gold_current"
        cached = await self.redis.get(cache_key)
        if cached:
            return float(cached)
            
        url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
        try:
            # Need strict headers for Yahoo Finance
            headers = {"User-Agent": "Mozilla/5.0"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    val = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                    await self.redis.set(cache_key, str(val), ex=3600)
                    return float(val)
        except Exception as e:
            logger.error(f"Yahoo Gold fetch error: {e}")
            
        return 2350.0

    async def fetch_btc_dominance(self) -> float | None:
        """
        Fetches BTC Dominance from CoinGecko /global endpoint.
        """
        cache_key = "macro_btc_dominance"
        cached = await self.redis.get(cache_key)
        if cached:
            return float(cached)
            
        url = "https://api.coingecko.com/api/v3/global"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    val = float(data["data"]["market_cap_percentage"]["btc"])
                    await self.redis.set(cache_key, str(val), ex=3600)
                    return val
        except Exception as e:
            logger.error(f"CoinGecko fetch error: {e}")
            
        return 53.4
