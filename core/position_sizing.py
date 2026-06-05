"""
APEX v11.0 — core/position_sizing.py
=====================================
DEPRECATED SHIM → replaced by PortfolioRiskEngine ATR-based sizing.

The new institutional position sizing lives in:
    services/risk/portfolio_risk_engine.py → PortfolioRiskEngine.size_position()

Critical flaws in the legacy KellyPositionSizer:
- Kelly formula requires calibrated win_rate and avg_win_pct.
  Until Phase 2 ML is complete, these inputs are ESTIMATES, not calibrated
  probabilities → Full Kelly on uncalibrated estimates = gambling.
- The old 0.25 kelly_fraction was a magic number with no justification.
- No correlation constraint, no VaR gate, no regime multiplier.

Current v11.0 approach: ATR-based fixed 1% risk per trade with regime multiplier.
Kelly will be re-enabled in Phase 2 when calibrated_probability is available.

DO NOT use in new code. DO NOT extend this class.
"""
import logging

logger = logging.getLogger(__name__)


class KellyPositionSizer:
    """
    LEGACY: Kelly-based position sizer.
    
    DO NOT extend. Migrate to PortfolioRiskEngine.size_position().
    Kelly sizing is DISABLED until Phase 2 ML calibration is complete.
    """

    def __init__(self, kelly_fraction: float = 0.25, max_risk_pct: float = 0.05) -> None:
        self.kelly_fraction = kelly_fraction
        self.max_risk_pct = max_risk_pct
        logger.warning(
            "[KellyPositionSizer] LEGACY: Kelly sizing is disabled pending ML calibration. "
            "All sizing is now handled by PortfolioRiskEngine (ATR-based, 1%% risk per trade)."
        )

    def calculate_size(
        self,
        capital: float,
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        stop_loss_pct: float,
        is_bootstrap: bool = False,
    ) -> float:
        """
        Returns conservative bootstrap size (0.5% risk) regardless of inputs.
        Full Kelly is blocked until Phase 2 ML calibration is complete.
        
        # BLOCKED: Full Kelly formula requires calibrated probability from Phase 2 ML.
        # TODO(Phase 2): Uncomment Kelly formula after IsotonicRegression calibration.
        """
        logger.debug(
            "[KellyPositionSizer] Returning bootstrap size (0.5%% risk). "
            "Kelly formula locked until Phase 2 ML."
        )
        # Always use bootstrap conservative sizing until calibrated probs are ready
        risk_amount = capital * 0.005  # 0.5% risk — conservative floor
        return risk_amount / stop_loss_pct if stop_loss_pct > 0 else 0.0
