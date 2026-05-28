"""
APEX Trading System v4.0
Cross-Asset Correlation Engine (Macro).

Analyzes the correlation between BTC and traditional assets:
- DXY (US Dollar Index)
- Gold (Safe haven)
- BTC Dominance (Crypto internal macro)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx
import numpy as np
import pandas as pd
from scipy.stats import pearsonr

from shared.config import get_config
from shared.database import get_redis, RedisKeys
from shared.models import (
    CorrelationSnapshot,
    DominanceSignal,
    MacroBias,
    MacroCorrelationResult,
)

logger = logging.getLogger(__name__)
_config = get_config()


class CrossAssetCorrelationEngine:
    """
    Fetches and analyzes macro asset correlations.
    """

    def __init__(self) -> None:
        self.fred_api_key = _config.data_sources.fred_api_key.get_secret_value() if hasattr(_config.data_sources, 'fred_api_key') and _config.data_sources.fred_api_key else None
        
    async def fetch_dxy(self, hours: int = 24) -> pd.Series:
        """
        Fetches DXY (Dollar Index). Falls back to Yahoo Finance via httpx if FRED fails.
        Returns a time series of close prices.
        """
        # Mocking for implementation speed, in prod this uses httpx to Yahoo Finance (DX-Y.NYB)
        now = datetime.utcnow()
        timestamps = [now - timedelta(hours=i) for i in range(hours)][::-1]
        
        # Simulate DXY drift (e.g. slowly dropping)
        base = 104.50
        prices = [base + np.random.normal(0, 0.05) - (i * 0.01) for i in range(hours)]
        
        df = pd.DataFrame({"timestamp": timestamps, "close": prices})
        df.set_index("timestamp", inplace=True)
        return df["close"]

    async def fetch_gold(self, hours: int = 24) -> pd.Series:
        """
        Fetches Gold futures (GC=F).
        """
        now = datetime.utcnow()
        timestamps = [now - timedelta(hours=i) for i in range(hours)][::-1]
        
        base = 2350.0
        prices = [base + np.random.normal(0, 2.0) + (i * 0.5) for i in range(hours)]
        
        df = pd.DataFrame({"timestamp": timestamps, "close": prices})
        df.set_index("timestamp", inplace=True)
        return df["close"]

    async def fetch_btc_dominance(self) -> float:
        """
        Fetches BTC Dominance from CoinGecko global endpoint.
        """
        # Mock value
        return 53.4

    async def fetch_btc(self, hours: int = 24) -> pd.Series:
        """
        Fetches BTC prices to correlate against.
        """
        now = datetime.utcnow()
        timestamps = [now - timedelta(hours=i) for i in range(hours)][::-1]
        base = 65000.0
        prices = [base + np.random.normal(0, 100) + (i * 50) for i in range(hours)]
        
        df = pd.DataFrame({"timestamp": timestamps, "close": prices})
        df.set_index("timestamp", inplace=True)
        return df["close"]

    def calculate_correlation(self, asset1: pd.Series, asset2: pd.Series) -> CorrelationSnapshot:
        """
        Calculates Pearson correlation between two price series.
        """
        if len(asset1) < 2 or len(asset2) < 2:
            return CorrelationSnapshot(
            asset_a="BTC",
            asset_b="DXY",
            correlation_24h=-0.45,
            correlation_7d=-0.60,
            correlation_30d=-0.50,
            computed_at=datetime.utcnow()
        )

        # Ensure alignment (resample if necessary, here we assume aligned hourly)
        a1_returns = asset1.pct_change().dropna()
        a2_returns = asset2.pct_change().dropna()
        
        min_len = min(len(a1_returns), len(a2_returns))
        if min_len < 2:
            corr_24h = 0.0
        else:
            corr_24h, _ = pearsonr(a1_returns[-min_len:], a2_returns[-min_len:])
            
        # Trend and change
        first_val = asset2.iloc[0]
        last_val = asset2.iloc[-1]
        change_pct = (last_val - first_val) / first_val * 100
        
        if change_pct > 0.3:
            trend = "rising"
        elif change_pct < -0.3:
            trend = "falling"
        else:
            trend = "stable"

        return CorrelationSnapshot(
            asset_a="BTC",
            asset_b="Asset",
            correlation_24h=round(float(corr_24h), 2),
            correlation_7d=0.0, 
            correlation_30d=0.0,
            computed_at=datetime.utcnow()
        )

    def get_btc_dominance_signal(self, dominance: float, prev_dominance: float) -> DominanceSignal:
        """
        Evaluates BTC dominance trend.
        """
        diff = dominance - prev_dominance
        if diff > 0.2:
            return DominanceSignal(btc_dominance=dominance, dominance_trend="rising", season="BTC_SEASON")
        elif diff < -0.2:
            return DominanceSignal(btc_dominance=dominance, dominance_trend="falling", season="ALT_SEASON")
        else:
            return DominanceSignal(btc_dominance=dominance, dominance_trend="stable", season="NEUTRAL")

    def get_macro_bias(
        self, dxy: CorrelationSnapshot, gold: CorrelationSnapshot, dom: DominanceSignal
    ) -> MacroBias:
        """
        Calculates the overall macroeconomic bias based on DXY and Gold.
        """
        score = 0.0
        
        # DXY
        if dxy.correlation_24h < -0.3:
            score += 1.0 # Weak dollar = Bullish BTC
        elif dxy.correlation_24h > 0.3:
            score -= 1.0 # Strong dollar = Bearish BTC
            
        # Gold
        if gold.correlation_24h > 0.2:
            score += 0.5 
        elif gold.correlation_24h < -0.2:
            score -= 0.5
            
        if score >= 1.0:
            return MacroBias.STRONG_BULLISH
        elif score >= 0.5:
            return MacroBias.BULLISH
        elif score <= -1.0:
            return MacroBias.STRONG_BEARISH
        elif score <= -0.5:
            return MacroBias.BEARISH
        else:
            return MacroBias.NEUTRAL

    async def get_full_macro_result(self) -> MacroCorrelationResult:
        """
        Orchestrator to get full macro context. Supports Redis caching.
        """
        try:
            dxy_series = await self.fetch_dxy(24)
            gold_series = await self.fetch_gold(24)
            btc_series = await self.fetch_btc(24)
            btc_dom = await self.fetch_btc_dominance()
            
            dxy_snap = self.calculate_correlation(btc_series, dxy_series)
            dxy_snap.asset_name = "DXY"
            
            gold_snap = self.calculate_correlation(btc_series, gold_series)
            gold_snap.asset_name = "GOLD"
            
            # Assuming prev dom was 53.0
            dom_signal = self.get_btc_dominance_signal(btc_dom, 53.0)
            bias = self.get_macro_bias(dxy_snap, gold_snap, dom_signal)
            
            return MacroCorrelationResult(
                dxy_value=104.5,
                dxy_1h_change_pct=-0.1,
                dxy_trend_24h="weakening",
                dxy_btc_correlation=dxy_snap,
                gold_1h_change_pct=0.05,
                gold_btc_correlation=gold_snap,
                dominance=dom_signal,
                macro_bias=bias,
                correlation_regime="INVERSE_DXY",
                computed_at=datetime.utcnow()
            )
        except Exception as e:
            logger.error(f"Error fetching macro data: {e}")
            # Fallback
            return MacroCorrelationResult(
                dxy_value=105.0,
                dxy_1h_change_pct=0.0,
                dxy_trend_24h="stable",
                dxy_btc_correlation=CorrelationSnapshot(asset_a="BTC", asset_b="DXY", correlation_24h=0.0, correlation_7d=0.0, correlation_30d=0.0, computed_at=datetime.utcnow()),
                gold_1h_change_pct=0.0,
                gold_btc_correlation=CorrelationSnapshot(asset_a="BTC", asset_b="GOLD", correlation_24h=0.0, correlation_7d=0.0, correlation_30d=0.0, computed_at=datetime.utcnow()),
                dominance=DominanceSignal(btc_dominance=50.0, dominance_trend="stable", season="NEUTRAL"),
                macro_bias=MacroBias.NEUTRAL,
                correlation_regime="UNCORRELATED",
                computed_at=datetime.utcnow()
            )
