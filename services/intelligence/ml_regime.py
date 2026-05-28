import numpy as np
import pandas as pd
from shared.models import MarketRegime
import structlog
import os

logger = structlog.get_logger(__name__)


class MLRegimeClassifier:
    """
    APEX v5.0 Regime Classifier.
    Uses a robust rule-based + statistical approach to classify market regime.
    Combines: trend direction (SMA), momentum (ROC), and volatility (ATR ratio).
    This is production-safe and does NOT require external ML training libraries.
    """

    def __init__(self, model_dir: str = "models"):
        self.model_dir = model_dir
        os.makedirs(self.model_dir, exist_ok=True)
        self.is_trained = False  # Always ready after first call

    def _compute_indicators(self, df: pd.DataFrame) -> dict:
        """Computes key indicators for regime classification."""
        close = df['close']
        high = df['high']
        low = df['low']

        # Trend: SMA relationship
        sma_20 = close.rolling(20).mean()
        sma_50 = close.rolling(50).mean()
        price_vs_sma20 = (close.iloc[-1] - sma_20.iloc[-1]) / sma_20.iloc[-1]
        sma20_vs_sma50 = (sma_20.iloc[-1] - sma_50.iloc[-1]) / sma_50.iloc[-1] if not pd.isna(sma_50.iloc[-1]) else 0

        # Momentum: Rate of Change over 10 periods
        roc_10 = (close.iloc[-1] - close.iloc[-11]) / close.iloc[-11] if len(close) > 11 else 0

        # Volatility: ATR ratio (current ATR vs average ATR)
        atr = (high - low).rolling(14).mean()
        atr_current = atr.iloc[-1]
        atr_avg = atr.rolling(50).mean().iloc[-1] if len(atr) > 50 else atr_current
        vol_ratio = atr_current / atr_avg if atr_avg > 0 else 1.0

        return {
            'price_vs_sma20': price_vs_sma20,
            'sma20_vs_sma50': sma20_vs_sma50,
            'roc_10': roc_10,
            'vol_ratio': vol_ratio,
        }

    def train_hmm(self, df: pd.DataFrame):
        """
        No actual training needed for this robust classifier.
        Called for compatibility with existing code — marks as trained immediately.
        """
        if len(df) < 50:
            logger.warning("Not enough candles for regime analysis (need 50+). Using SIDEWAYS default.")
            return
        logger.info("Regime Classifier: rule-based model ready (no training needed).")
        self.is_trained = True

    def classify_current_regime(self, df: pd.DataFrame) -> MarketRegime:
        """
        Classifies current market regime using technical rules.
        Returns: MarketRegime.BULL / BEAR / SIDEWAYS / CRISIS
        """
        if len(df) < 50:
            self.is_trained = True  # Enable future calls even with little data
            return MarketRegime.SIDEWAYS

        self.is_trained = True

        try:
            ind = self._compute_indicators(df)
            price_vs_sma20 = ind['price_vs_sma20']
            sma20_vs_sma50 = ind['sma20_vs_sma50']
            roc_10 = ind['roc_10']
            vol_ratio = ind['vol_ratio']

            # Score system: +1 bull point, -1 bear point
            score = 0
            score += 1 if price_vs_sma20 > 0.01 else (-1 if price_vs_sma20 < -0.01 else 0)
            score += 1 if sma20_vs_sma50 > 0.005 else (-1 if sma20_vs_sma50 < -0.005 else 0)
            score += 1 if roc_10 > 0.02 else (-1 if roc_10 < -0.02 else 0)

            # Crisis: extreme volatility
            if vol_ratio > 2.5:
                logger.info(f"Regime: CRISIS (vol_ratio={vol_ratio:.2f})")
                return MarketRegime.CRISIS

            if score >= 2:
                regime = MarketRegime.BULL
            elif score <= -2:
                regime = MarketRegime.BEAR
            else:
                regime = MarketRegime.SIDEWAYS

            logger.info(f"Regime classified: {regime.value} (score={score}, roc={roc_10:.3f}, vol_ratio={vol_ratio:.2f})")
            return regime

        except Exception as e:
            logger.warning(f"Regime classification failed: {e}. Returning SIDEWAYS.")
            return MarketRegime.SIDEWAYS
