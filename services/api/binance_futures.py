"""
APEX Trading System v4.0
services/api/binance_futures.py

Async connector for Binance Futures Public Endpoints.
Retrieves Funding Rates, Open Interest, and Long/Short Ratio without API keys.
"""

import aiohttp
import asyncio
import structlog
from typing import Dict, Any, Optional
import time

logger = structlog.get_logger(__name__)

class BinanceFuturesPublicAPI:
    """
    Handles fetching of public data from Binance Futures.
    No API keys required. Includes caching to prevent rate limits.
    """
    BASE_URL = "https://fapi.binance.com"
    
    def __init__(self):
        self._cache = {}
        self._cache_ttl = 60  # Cache for 60 seconds
        
    async def _fetch(self, endpoint: str, params: Dict[str, Any] = None) -> Optional[Any]:
        # Generate cache key
        cache_key = f"{endpoint}_{str(params)}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached['time'] < self._cache_ttl:
            return cached['data']
            
        url = f"{self.BASE_URL}{endpoint}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        self._cache[cache_key] = {'time': time.time(), 'data': data}
                        return data
                    else:
                        logger.error("binance_fapi_error", status=response.status, url=url)
                        return None
        except Exception as e:
            logger.error("binance_fapi_exception", error=str(e), url=url)
            return None

    async def get_funding_rate(self, symbol: str) -> float:
        """
        Get current funding rate. Returns as percentage (e.g. 0.01 for 0.01%).
        """
        formatted_symbol = symbol.replace("/", "").upper()
        data = await self._fetch("/fapi/v1/premiumIndex", {"symbol": formatted_symbol})
        if data and 'lastFundingRate' in data:
            return float(data['lastFundingRate']) * 100
        return 0.0

    async def get_open_interest(self, symbol: str) -> float:
        """
        Get current open interest in USD.
        """
        formatted_symbol = symbol.replace("/", "").upper()
        data = await self._fetch("/fapi/v1/openInterest", {"symbol": formatted_symbol})
        if data and 'openInterest' in data:
            return float(data['openInterest'])
        return 0.0

    async def get_long_short_ratio(self, symbol: str, period: str = "5m") -> float:
        """
        Get Global Long/Short Account Ratio.
        """
        formatted_symbol = symbol.replace("/", "").upper()
        data = await self._fetch("/futures/data/globalLongShortAccountRatio", {
            "symbol": formatted_symbol,
            "period": period,
            "limit": 1
        })
        if data and isinstance(data, list) and len(data) > 0 and 'longShortRatio' in data[0]:
            return float(data[0]['longShortRatio'])
        return 1.0

binance_fapi = BinanceFuturesPublicAPI()
