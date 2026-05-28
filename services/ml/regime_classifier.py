"""
APEX Trading System v4.0
ML Regime Classifier v4 & Online Drift Detector.

CRITICAL v4 FEATURE: Online Regime Drift Detector.
In v3, models could silently degrade if market behavior changed fundamentally.
In v4, we use the `river` library (ADWIN) for real-time drift detection.
If the ML ensemble (HMM + XGBoost) consistently disagrees with the 
hardcoded rule-based fallback, the system dynamically reduces ML weight 
and eventually forces an emergency retraining.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from river.drift import ADWIN
import xgboost as xgb
import joblib

from shared.config import get_config
from shared.database import get_redis, RedisKeys
from shared.models import (
    DriftStatus,
    MarketRegime,
    RegimePrediction,
    RegimeThresholds,
)

logger = logging.getLogger(__name__)
_config = get_config()


class OnlineRegimeDriftDetector:
    """
    Online drift detector using ADWIN (Adaptive Windowing).
    Monitors agreement between ML predictions and strict Rule-Based fallback.
    """

    def __init__(self) -> None:
        self.adwin = ADWIN()
        # Track last 48 hours of agreement (1 = agree, 0 = disagree)
        self.agreement_history: deque[int] = deque(maxlen=48)
        self.disagreement_hours = 0
        self.alert_sent = False

    def check_drift(
        self,
        ml_regime: MarketRegime,
        rule_regime: MarketRegime,
        ml_confidence: float
    ) -> DriftStatus:
        """
        Check for concept drift. Should be called hourly.
        Returns the current DriftStatus and actions taken.
        """
        # Exclude CRISIS from drift detection (rule-based always wins)
        if rule_regime == MarketRegime.CRISIS:
            return self._current_status("none")

        is_agreement = 1 if ml_regime == rule_regime else 0
        self.agreement_history.append(is_agreement)
        self.adwin.update(is_agreement)

        if is_agreement == 0:
            self.disagreement_hours += 1
        else:
            # Decay disagreement if they agree again
            self.disagreement_hours = max(0, self.disagreement_hours - 1)

        action = "none"
        drift_detected = self.adwin.drift_detected

        if drift_detected or self.disagreement_hours >= 24:
            action = "weights_adjusted"
            logger.warning(
                f"Regime drift detected! Disagreement hours: {self.disagreement_hours}"
            )

        if self.disagreement_hours >= 48 and not self.alert_sent:
            action = "retraining_triggered"
            self.alert_sent = True
            logger.critical("Severe regime drift (48h). Emergency retraining required!")
            # In a full system, this would trigger a Celery task

        # Reset alert if things normalize
        if self.disagreement_hours < 12:
            self.alert_sent = False

        return self._current_status(action)

    def _current_status(self, action: str) -> DriftStatus:
        agreement_pct = sum(self.agreement_history) / max(1, len(self.agreement_history)) * 100
        
        # Calculate dynamic weights based on disagreement
        ml_weight, rule_weight = self.calculate_ensemble_weights(
            self.disagreement_hours, 
            ml_confidence=0.8  # placeholder, actual confidence applied in ensemble
        )

        return DriftStatus(
            drift_detected=self.disagreement_hours >= 24 or self.adwin.drift_detected,
            ml_rule_agreement_pct=round(agreement_pct, 1),
            disagreement_hours=self.disagreement_hours,
            action_taken=action,
            ml_weight=ml_weight,
            rule_weight=rule_weight,
            alert_sent=self.alert_sent
        )

    @staticmethod
    def calculate_ensemble_weights(
        disagreement_hours: int,
        ml_confidence: float
    ) -> tuple[float, float]:
        """
        Dynamically adjusts the weight of the ML model vs Rule-based fallback.
        Base: 60% ML / 40% Rule.
        If ML is low confidence or drifting heavily, rule-based takes over.
        """
        ml_base = 0.6
        rule_base = 0.4

        if ml_confidence < 0.65:
            return 0.0, 1.0  # Fallback completely

        if disagreement_hours >= 48:
            return 0.3, 0.7  # Rule heavily favored
        elif disagreement_hours >= 24:
            return 0.45, 0.55  # Rule slightly favored
            
        return ml_base, rule_base


class MLRegimeClassifierV4:
    """
    Ensemble ML Classifier: HMM (Volatility/States) + XGBoost (Directional/Macro).
    """

    def __init__(self) -> None:
        self.hmm_model: GaussianHMM | None = None
        self.xgb_model: xgb.XGBClassifier | None = None
        self.drift_detector = OnlineRegimeDriftDetector()
        self._load_models()

    def _load_models(self) -> None:
        """Load trained models from local storage/MinIO (simulated)."""
        try:
            self.hmm_model = joblib.load("models/hmm_regime_v4.pkl")
            self.xgb_model = xgb.XGBClassifier()
            self.xgb_model.load_model("models/xgb_regime_v4.json")
            logger.info("ML Regime models loaded successfully.")
        except Exception as e:
            logger.warning(f"Could not load ML models, will rely on rule-based. Error: {e}")

    def train_hmm(self, features_df: pd.DataFrame) -> None:
        """
        Train Gaussian HMM on 4 hidden states.
        Features: daily_returns, volatility_20d, volume_change, funding_rate, oi_change
        """
        logger.info("Training HMM Regime model...")
        req_cols = ["daily_returns", "volatility_20d", "volume_change", "funding_rate", "oi_change"]
        X = features_df[req_cols].dropna().values
        
        self.hmm_model = GaussianHMM(n_components=4, covariance_type="full", n_iter=100)
        self.hmm_model.fit(X)
        
        # Save model (simulated)
        joblib.dump(self.hmm_model, "models/hmm_regime_v4.pkl")
        logger.info("HMM training complete.")

    def train_xgboost(self, features_df: pd.DataFrame, labels: pd.Series) -> None:
        """
        Train XGBoost Classifier.
        Features: btc_vs_ema200, volatility_30d_percentile, funding_7d_avg, etc.
        """
        logger.info("Training XGBoost Regime model...")
        
        # XGBoost requires numeric labels, map MarketRegime strings to int
        label_map = {r.value: i for i, r in enumerate(MarketRegime)}
        y = labels.map(label_map).values
        X = features_df.values

        self.xgb_model = xgb.XGBClassifier(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=5,
            objective="multi:softprob",
            eval_metric="mlogloss"
        )
        self.xgb_model.fit(X, y)
        self.xgb_model.save_model("models/xgb_regime_v4.json")
        logger.info("XGBoost training complete.")

    def rule_based_regime(self, market_data: dict[str, Any]) -> RegimePrediction:
        """
        Strict, deterministic fallback rules.
        """
        btc_price = market_data.get("btc_price", 0)
        btc_ema200 = market_data.get("btc_ema200", 0)
        funding = market_data.get("funding_rate", 0)
        fear_greed = market_data.get("fear_greed", 50)
        btc_change_7d = market_data.get("btc_change_7d", 0)
        volatility_pct = market_data.get("volatility_percentile", 50)

        # 1. CRISIS Check (Highest Priority)
        if btc_change_7d < -15.0 or volatility_pct > 95:
            regime = MarketRegime.CRISIS
        # 2. BULL Check
        elif btc_price > btc_ema200 and funding > -0.01 and fear_greed > 40:
            regime = MarketRegime.BULL
        # 3. BEAR Check
        elif btc_price < btc_ema200 and fear_greed < 50:
            regime = MarketRegime.BEAR
        # 4. Default to SIDEWAYS
        else:
            regime = MarketRegime.SIDEWAYS

        return RegimePrediction(
            regime=regime,
            confidence=1.0,
            hmm_regime=regime,
            xgb_regime=regime,
            rule_regime=regime,
            source="rule_based_fallback",
            ensemble_weights={"ml": 0.0, "rule": 1.0},
            drift_hours=self.drift_detector.disagreement_hours,
            computed_at=datetime.utcnow()
        )

    def ensemble_predict(
        self, 
        hmm_features: np.ndarray, 
        xgb_features: np.ndarray,
        market_data: dict[str, Any]
    ) -> RegimePrediction:
        """
        Combines HMM and XGBoost.
        """
        if not self.hmm_model or not self.xgb_model:
            return self.rule_based_regime(market_data)

        # 1. XGBoost Prediction (0-BULL, 1-BEAR, 2-SIDEWAYS, 3-CRISIS mapped)
        xgb_probs = self.xgb_model.predict_proba(xgb_features.reshape(1, -1))[0]
        xgb_idx = int(np.argmax(xgb_probs))
        regimes_list = list(MarketRegime)
        xgb_regime = regimes_list[xgb_idx]
        xgb_conf = xgb_probs[xgb_idx]

        # 2. HMM Prediction (requires state mapping, simplified here)
        hmm_state = self.hmm_model.predict(hmm_features.reshape(1, -1))[0]
        # Simplification: Assume state 0=BULL, 1=BEAR, 2=SIDEWAYS, 3=CRISIS for this demo
        hmm_regime = regimes_list[hmm_state]
        hmm_conf = 0.7  # HMM doesn't natively output probability directly easily without full seq

        # 3. Ensemble Logic
        # If they disagree, XGBoost takes priority but confidence is slashed
        if xgb_regime != hmm_regime:
            ml_regime = xgb_regime
            ml_conf = xgb_conf * 0.7
        else:
            ml_regime = xgb_regime
            ml_conf = (xgb_conf + hmm_conf) / 2

        rule_pred = self.rule_based_regime(market_data)
        
        # CRISIS override always
        if rule_pred.regime == MarketRegime.CRISIS:
            return rule_pred

        # 4. Drift detection and weighting
        drift_status = self.drift_detector.check_drift(
            ml_regime=ml_regime,
            rule_regime=rule_pred.regime,
            ml_confidence=ml_conf
        )

        if ml_conf < 0.65 or drift_status.ml_weight == 0.0:
            final_regime = rule_pred.regime
            source = "rule_based_fallback"
            final_conf = 1.0
        else:
            # If Rule weight > ML weight and they disagree, Rule wins
            if drift_status.rule_weight > drift_status.ml_weight and ml_regime != rule_pred.regime:
                final_regime = rule_pred.regime
                source = "ml_rule_blend"
                final_conf = drift_status.rule_weight
            else:
                final_regime = ml_regime
                source = "ml_ensemble"
                final_conf = ml_conf

        return RegimePrediction(
            regime=final_regime,
            confidence=round(final_conf, 2),
            hmm_regime=hmm_regime,
            xgb_regime=xgb_regime,
            rule_regime=rule_pred.regime,
            source=source,
            ensemble_weights={"ml": drift_status.ml_weight, "rule": drift_status.rule_weight},
            drift_hours=drift_status.disagreement_hours,
            computed_at=datetime.utcnow()
        )

    def get_regime(self, market_data: dict[str, Any]) -> RegimePrediction:
        """
        Main entry point. Constructs features and runs ensemble.
        """
        # In a real system, features are constructed here from market_data
        # For simplicity, we mock the feature arrays
        hmm_feats = np.array([0.01, 0.05, 1.2, 0.005, 0.02])
        xgb_feats = np.array([1, 80, 0.01, 2.5, 65, 0.05, -1.0])

        return self.ensemble_predict(hmm_feats, xgb_feats, market_data)

    def regime_specific_thresholds(self, regime: MarketRegime) -> RegimeThresholds:
        """
        Returns hardcoded risk and confluence thresholds based on regime.
        """
        cfg = _config.trading
        
        if regime == MarketRegime.BULL:
            return RegimeThresholds(
                regime=regime,
                confluence_min=cfg.bull_confluence_min,
                daily_signals_max=cfg.bull_signals_max,
                risk_pct_max=cfg.bull_risk_max_pct
            )
        elif regime == MarketRegime.BEAR:
            return RegimeThresholds(
                regime=regime,
                confluence_min=cfg.bear_confluence_min,
                daily_signals_max=cfg.bear_signals_max,
                risk_pct_max=cfg.bear_risk_max_pct
            )
        elif regime == MarketRegime.SIDEWAYS:
            return RegimeThresholds(
                regime=regime,
                confluence_min=cfg.sideways_confluence_min,
                daily_signals_max=cfg.sideways_signals_max,
                risk_pct_max=cfg.sideways_risk_max_pct
            )
        else: # CRISIS
            return RegimeThresholds(
                regime=regime,
                confluence_min=99.0, # Impossible to hit
                daily_signals_max=0,
                risk_pct_max=0.0
            )
