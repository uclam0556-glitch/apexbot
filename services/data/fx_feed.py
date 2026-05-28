"""
APEX Trading System v4.0
FX Rate Feed.

Continuously fetches traditional fiat exchange rates and calculates 
short-term volatility for the P2P Arbitrage Engine.
Focus: USDT/RUB, EUR/USD, etc.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import httpx
import numpy as np

from shared.database import get_redis

logger = logging.getLogger(__name__)


class FXRateFeed:
    """
    Maintains current FX rates and their historical rolling volatility in Redis.
    """

    def __init__(self) -> None:
        self.redis = get_redis()
        # Mocking endpoints, in prod we might use Binance P2P API for USDT/RUB 
        # or a standard FX API (like Fixer.io or AlphaVantage) for pure fiat pairs.
        self.pairs = ["USDT/RUB", "EUR/USD"]
        
        # In-memory short term history for volatility calculation
        # pair -> list of prices
        self.price_history: dict[str, list[float]] = {p: [] for p in self.pairs}

    async def fetch_usdt_rub(self) -> float:
        """
        Mock: Fetches current USDT/RUB rate from P2P or spot proxy.
        """
        # Simulated slight drift
        return 92.50 + np.random.normal(0, 0.1)

    async def fetch_eur_usd(self) -> float:
        """
        Mock: Fetches current EUR/USD rate.
        """
        return 1.08 + np.random.normal(0, 0.001)

    async def calculate_and_store_volatility(self, pair: str, current_price: float) -> None:
        """
        Maintains a rolling buffer and calculates 1h realized volatility (in %).
        """
        history = self.price_history[pair]
        history.append(current_price)
        
        # Keep last 60 data points (assuming 1 update per minute = 1 hour)
        if len(history) > 60:
            history.pop(0)
            
        if len(history) >= 10:
            # Calculate volatility (standard deviation of returns * 100)
            returns = np.diff(history) / history[:-1]
            volatility_pct = float(np.std(returns) * 100)
            
            # Annualize or scale if needed, here we just keep raw 1h volatility %
            # Store in Redis for the P2PFXSlippageModel
            key = f"fx_volatility_1h:{pair}"
            await self.redis.set(key, str(volatility_pct), ex=3600)
            
            logger.debug(f"{pair} 1h Volatility updated: {volatility_pct:.3f}%")

    async def run_feed(self) -> None:
        """
        Continuous loop to fetch FX rates and update Redis.
        """
        logger.info("Starting FX Rate Feed...")
        
        while True:
            try:
                # Fetch USDT/RUB
                usdt_rub = await self.fetch_usdt_rub()
                await self.redis.set("fx_rate:USDT/RUB", str(usdt_rub), ex=120)
                await self.calculate_and_store_volatility("USDT/RUB", usdt_rub)
                
                # Fetch EUR/USD
                eur_usd = await self.fetch_eur_usd()
                await self.redis.set("fx_rate:EUR/USD", str(eur_usd), ex=120)
                await self.calculate_and_store_volatility("EUR/USD", eur_usd)
                
            except Exception as e:
                logger.error(f"FX Feed error: {e}")
                
            # Update every 60 seconds
            await asyncio.sleep(60)
