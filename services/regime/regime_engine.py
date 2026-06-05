"""
APEX v11.0 — Multi-Dimensional Market Regime Classifier (Phase 4)
==================================================================
Classifies the market into one of 5 master regimes based on trend, volatility (HMM),
correlation, liquidity, and crypto-specific factors.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

class MasterRegime(Enum):
    TRENDING_BULL = 'TRENDING_BULL'      # Full size (1.0x)
    CHOPPY_BULL = 'CHOPPY_BULL'          # 75% size
    TRANSITIONAL = 'TRANSITIONAL'        # 50% size
    BEAR_RISK_OFF = 'BEAR_RISK_OFF'      # 25% size
    EXTREME_STRESS = 'EXTREME_STRESS'    # 0% new exposure

@dataclass
class RegimeState:
    master_regime: MasterRegime
    size_multiplier: float
    hmm_state: int
    hmm_state_prob: float
    hmm_fitted: bool
    trend_regime: str
    correlation_regime: str
    liquidity_index: float
    liquidity_regime: str
    btc_dominance_delta: float
    altcoin_season_index: float
    adx: float
    pct_above_50d: float
    pct_above_200d: float
    bars_in_current_state: int
    timestamp: float
    description: str

class RegimeEngine:
    def __init__(self):
        self._hmm_model = None
        self._is_fitted = False
        self._last_hmm_state = None
        self._bars_in_state = 0
        self._state_mapping = {}  # Map internal HMM state to ordered state (0=low, 2=high)
        
    def fit_hmm(self, features: np.ndarray) -> None:
        """
        Fits a 3-state Gaussian HMM on [log_return, log_vol_5d, log_vol_ratio].
        Standardizes features internally.
        """
        try:
            from hmmlearn import hmm
        except ImportError:
            logger.error("hmmlearn not installed. Cannot fit HMM.")
            return
            
        if len(features) < 100:
            logger.warning("Insufficient data to fit HMM (< 100).")
            return
            
        self._scaler_mean = np.mean(features, axis=0)
        self._scaler_std = np.std(features, axis=0) + 1e-8
        
        scaled_features = (features - self._scaler_mean) / self._scaler_std
        
        self._hmm_model = hmm.GaussianHMM(n_components=3, covariance_type='diag', n_iter=200, random_state=42)
        self._hmm_model.fit(scaled_features)
        
        # Sort states by volatility feature (index 1)
        means = self._hmm_model.means_[:, 1]
        sorted_indices = np.argsort(means)
        
        # Map: internal state -> ordered state (0: low, 1: medium, 2: high)
        self._state_mapping = {internal: ordered for ordered, internal in enumerate(sorted_indices)}
        self._is_fitted = True
        logger.info("HMM Volatility Regime model fitted successfully.")

    def compute_trend_regime(self, adx: float) -> str:
        if adx > 25:
            return 'TRENDING'
        elif adx < 20:
            return 'RANGING'
        else:
            return 'TRANSITIONAL_TREND'

    def classify_hmm(self, features_row: np.ndarray, vol_ratio: float) -> tuple[int, float]:
        if not self._is_fitted or self._hmm_model is None:
            # Fallback
            if vol_ratio < 0.8:
                ordered_state = 0
            elif vol_ratio <= 1.5:
                ordered_state = 1
            else:
                ordered_state = 2
            state_prob = 1.0
        else:
            scaled_features = (features_row - self._scaler_mean) / self._scaler_std
            # Predict probability
            probs = self._hmm_model.predict_proba(scaled_features.reshape(1, -1))[0]
            internal_state = int(np.argmax(probs))
            ordered_state = self._state_mapping[internal_state]
            state_prob = float(np.max(probs))
        
        # Hysteresis
        if self._last_hmm_state is None:
            self._last_hmm_state = ordered_state
            self._bars_in_state = 1
        else:
            if ordered_state == self._last_hmm_state:
                self._bars_in_state += 1
            else:
                # Require 3 bars to flip
                if self._bars_in_state >= 3:
                    self._last_hmm_state = ordered_state
                    self._bars_in_state = 1
                else:
                    self._bars_in_state += 1
                    ordered_state = self._last_hmm_state # keep old state
                    
        return ordered_state, state_prob

    def compute_correlation_regime(self, avg_corr: float) -> str:
        if avg_corr < 0.30:
            return 'DIVERSIFIED'
        elif avg_corr < 0.60:
            return 'NORMAL'
        elif avg_corr <= 0.80:
            return 'HIGH_CORRELATION'
        else:
            return 'EMERGENCY_RISK_OFF'

    def compute_liquidity_regime(self, avg_spread_pct: float, avg_adv_usd: float, volume_breadth: float) -> tuple[float, str]:
        spread_score = 1.0 - min(avg_spread_pct / 0.5, 1.0)
        depth_score = min(avg_adv_usd / 1e9, 1.0)
        
        liq_index = 0.4 * spread_score + 0.3 * depth_score + 0.3 * volume_breadth
        
        if liq_index < 0.40:
            regime = 'IMPAIRED'
        elif liq_index <= 0.65:
            regime = 'REDUCED'
        else:
            regime = 'NORMAL'
            
        return float(liq_index), regime

    def compute_crypto_regime(self, btc_dominance_series: pd.Series, alt_performance_90d: float) -> tuple[float, float]:
        if len(btc_dominance_series) >= 7:
            current = btc_dominance_series.iloc[-1]
            past = btc_dominance_series.iloc[-7]
            delta = float(current - past)
        else:
            delta = 0.0
            
        return delta, float(alt_performance_90d)

    def classify(self, pct_above_50d: float, pct_above_200d: float, adx: float, 
                 avg_corr: float, realized_vol_ratio: float, features_row: np.ndarray,
                 avg_spread_pct: float, avg_adv_usd: float, volume_breadths: float,
                 btc_dominance_series: pd.Series, alt_performance_90d: float,
                 timestamp: float = 0.0) -> RegimeState:
                 
        trend_regime = self.compute_trend_regime(adx)
        hmm_state, hmm_prob = self.classify_hmm(features_row, realized_vol_ratio)
        corr_regime = self.compute_correlation_regime(avg_corr)
        liq_idx, liq_regime = self.compute_liquidity_regime(avg_spread_pct, avg_adv_usd, volume_breadths)
        dom_delta, alt_idx = self.compute_crypto_regime(btc_dominance_series, alt_performance_90d)
        
        # Master Regime Classification
        if avg_corr > 0.80 or (hmm_state == 2 and pct_above_50d < 30):
            master = MasterRegime.EXTREME_STRESS
            size_mult = 0.0
        elif pct_above_50d < 40 and avg_corr > 0.60:
            master = MasterRegime.BEAR_RISK_OFF
            size_mult = 0.25
        elif pct_above_50d > 60 and adx > 25 and hmm_state == 0 and avg_corr < 0.50:
            master = MasterRegime.TRENDING_BULL
            size_mult = 1.0
        elif pct_above_50d > 50 and adx < 20:
            master = MasterRegime.CHOPPY_BULL
            size_mult = 0.75
        else:
            master = MasterRegime.TRANSITIONAL
            size_mult = 0.50
            
        desc = f"Master: {master.value}. HMM Vol: State {hmm_state}. Trend: {trend_regime}."
        
        return RegimeState(
            master_regime=master,
            size_multiplier=size_mult,
            hmm_state=hmm_state,
            hmm_state_prob=hmm_prob,
            hmm_fitted=self._is_fitted,
            trend_regime=trend_regime,
            correlation_regime=corr_regime,
            liquidity_index=liq_idx,
            liquidity_regime=liq_regime,
            btc_dominance_delta=dom_delta,
            altcoin_season_index=alt_idx,
            adx=adx,
            pct_above_50d=pct_above_50d,
            pct_above_200d=pct_above_200d,
            bars_in_current_state=self._bars_in_state,
            timestamp=timestamp,
            description=desc
        )

    def get_size_multiplier(self, regime: Optional[MasterRegime] = None) -> float:
        if regime == MasterRegime.TRENDING_BULL:
            return 1.0
        elif regime == MasterRegime.CHOPPY_BULL:
            return 0.75
        elif regime == MasterRegime.TRANSITIONAL:
            return 0.50
        elif regime == MasterRegime.BEAR_RISK_OFF:
            return 0.25
        elif regime == MasterRegime.EXTREME_STRESS:
            return 0.0
        return 0.50
