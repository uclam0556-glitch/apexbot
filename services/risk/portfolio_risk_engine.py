"""
APEX v11.0 — Portfolio Risk Engine
=====================================
Institutional portfolio-level risk management.

Implements:
- Volatility-adjusted position sizing (ATR-based, not fixed %).
- Correlation-adjusted sizing (rolling 30-day returns correlation).
- Portfolio VaR / CVaR (Historical Simulation, 252-day window).
- Circuit breakers with tiered drawdown levels.
- Concentration limits by asset tier and cluster.
- Regime multipliers integrated from RegimeEngine.

Design Principles:
- Risk engine is a hard gate. No position can bypass it.
- All risk limits are conservative and explicitly justified.
- Every risk decision is logged with full context.
- The engine maintains no memory of P&L between restarts
  (uses DB as source of truth).

Limitations:
- Historical VaR assumes stationarity — crypto is highly non-stationary.
  TEMP: Use with awareness; CVaR provides more tail-risk sensitivity.
- Correlation clusters (DeFi, L1, etc.) are manually defined.
  TEMP: Replace with dynamic clustering (k-means on returns) in v11.1.
- Kelly sizing requires calibrated probability model (Phase 2 ML).
  Until then, uses conservative fixed risk per trade.

References:
- Kelly (1956): "A New Interpretation of Information Rate."
- Roncalli (2013): "Introduction to Risk Parity and Budgeting."
- Jorion (2006): "Value at Risk: The New Benchmark for Managing Financial Risk."

Author intent: This is the most critical module in APEX v11.0.
Every dollar of risk must be explicitly authorized by this engine.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ─── Risk Configuration ───────────────────────────────────────────────────────
# All values explicitly justified. "TEMP" marks values requiring calibration.

# Maximum risk per trade as fraction of portfolio.
# Justification: 1% per trade allows 10 consecutive losses before 10% drawdown.
# Standard at systematic funds (cf. Roncalli, 2013, ch. 4).
MAX_RISK_PER_TRADE_PCT: float = 1.0  # 1% of portfolio per trade

# ATR multiplier for stop-loss distance.
# Justification: 1.5x ATR avoids noise-driven stops (cf. Wilder, 1978).
# TEMP: Optimize per asset tier using historical stop-hit analysis.
ATR_SL_MULTIPLIER: float = 1.5

# Maximum position sizes by asset tier.
MAX_POSITION_PCT: dict[str, float] = {
    "TIER_1": 5.0,   # Max 5% of portfolio in BTC/ETH
    "TIER_2": 3.0,   # Max 3% in major alts
    "TIER_3": 2.0,   # Max 2% in Tier 3 assets
}

# Maximum correlation for allowing a new correlated position.
# Justification: 0.70 correlation means 49% shared variance — high overlap.
# Standard portfolio construction limit.
MAX_CORRELATION_FOR_NEW_POSITION: float = 0.70

# Correlation cluster exposure limits.
# TEMP: Clusters are manually defined. Replace with dynamic clustering in v11.1.
MAX_CLUSTER_EXPOSURE_PCT: dict[str, float] = {
    "BTC_ETH": 30.0,
    "L1_ALT": 20.0,
    "L2": 15.0,
    "DEFI": 15.0,
    "AI_TOKENS": 10.0,
    "MEME": 8.0,
    "GAMING": 8.0,
}

# VaR parameters.
VAR_WINDOW_DAYS: int = 252  # 1-year rolling window (standard)
VAR_CONFIDENCE_95: float = 0.05  # 5th percentile
VAR_CONFIDENCE_99: float = 0.01  # 1st percentile

# VaR circuit breaker thresholds.
VAR_95_REDUCE_THRESHOLD: float = 3.0   # % portfolio VaR_95 → reduce sizing 25%
VAR_99_REDUCE_THRESHOLD: float = 5.0   # % portfolio VaR_99 → reduce sizing 50%

# Drawdown circuit breaker levels.
DRAWDOWN_LEVELS: dict[str, dict] = {
    "LEVEL_1": {
        "threshold_pct": 8.0,
        "size_multiplier": 0.75,
        "description": "8% drawdown: Reduce sizing to 75%. Raise signal threshold."
    },
    "LEVEL_2": {
        "threshold_pct": 15.0,
        "size_multiplier": 0.50,
        "description": "15% drawdown: Reduce sizing to 50%. High-probability signals only."
    },
    "LEVEL_3": {
        "threshold_pct": 25.0,
        "size_multiplier": 0.0,
        "description": "25% drawdown: HALT all new positions. Capital preservation mode."
    },
}

# Recovery requirements after Level 1+ circuit breaker.
RECOVERY_REQUIRED_WINS: int = 5
RECOVERY_REQUIRED_DAYS: int = 10

# Concentration limits.
MAX_SINGLE_ASSET_PCT: float = 5.0
MAX_TIER3_TOTAL_PCT: float = 15.0
MAX_GROSS_EXPOSURE_PCT: float = 100.0


# ─── Asset Cluster Map ────────────────────────────────────────────────────────
# TEMP: Manual cluster assignment. Replace with dynamic clustering in v11.1.
ASSET_CLUSTERS: dict[str, str] = {
    "BTC/USDT": "BTC_ETH", "ETH/USDT": "BTC_ETH",
    "SOL/USDT": "L1_ALT", "AVAX/USDT": "L1_ALT", "ADA/USDT": "L1_ALT",
    "DOT/USDT": "L1_ALT", "ATOM/USDT": "L1_ALT", "NEAR/USDT": "L1_ALT",
    "APT/USDT": "L1_ALT", "SUI/USDT": "L1_ALT", "SEI/USDT": "L1_ALT",
    "TIA/USDT": "L1_ALT", "TON/USDT": "L1_ALT",
    "ARB/USDT": "L2", "OP/USDT": "L2", "MATIC/USDT": "L2",
    "STRK/USDT": "L2", "ZK/USDT": "L2", "METIS/USDT": "L2",
    "AAVE/USDT": "DEFI", "UNI/USDT": "DEFI", "CRV/USDT": "DEFI",
    "SNX/USDT": "DEFI", "COMP/USDT": "DEFI", "GMX/USDT": "DEFI",
    "RUNE/USDT": "DEFI", "DYDX/USDT": "DEFI", "1INCH/USDT": "DEFI",
    "PENDLE/USDT": "DEFI", "LDO/USDT": "DEFI", "ENA/USDT": "DEFI",
    "FET/USDT": "AI_TOKENS", "RENDER/USDT": "AI_TOKENS",
    "TAO/USDT": "AI_TOKENS", "ARKM/USDT": "AI_TOKENS", "GRT/USDT": "AI_TOKENS",
    "WLD/USDT": "AI_TOKENS",
    "PEPE/USDT": "MEME", "DOGE/USDT": "MEME", "SHIB/USDT": "MEME",
    "WIF/USDT": "MEME", "BONK/USDT": "MEME", "FLOKI/USDT": "MEME",
    "MEW/USDT": "MEME", "TURBO/USDT": "MEME", "BOME/USDT": "MEME",
    "SAND/USDT": "GAMING", "MANA/USDT": "GAMING", "AXS/USDT": "GAMING",
    "GALA/USDT": "GAMING", "APE/USDT": "GAMING", "IMX/USDT": "GAMING",
}


class CircuitBreakerLevel(Enum):
    NONE = "NONE"
    LEVEL_1 = "LEVEL_1"
    LEVEL_2 = "LEVEL_2"
    LEVEL_3 = "LEVEL_3"


class RegimeMultiplierState(Enum):
    FULL = 1.0
    REDUCED_75 = 0.75
    REDUCED_50 = 0.50
    REDUCED_25 = 0.25
    MINIMAL = 0.10


@dataclass
class PositionSizingResult:
    """Output of position sizing calculation."""
    symbol: str
    portfolio_value_usd: float
    approved: bool
    rejection_reason: Optional[str]

    # Sizing
    risk_amount_usd: float       # Dollar amount at risk
    position_size_usd: float     # Total position size
    position_size_pct: float     # As % of portfolio

    # Risk inputs
    atr_pct: float               # ATR as % of price (used for SL distance)
    sl_distance_pct: float       # Stop-loss distance in %
    regime_multiplier: float     # Applied regime multiplier

    # Constraints applied
    hit_tier_max: bool = False
    hit_correlation_block: bool = False
    hit_concentration_limit: bool = False
    hit_circuit_breaker: bool = False
    circuit_breaker_level: CircuitBreakerLevel = CircuitBreakerLevel.NONE


@dataclass
class PortfolioRiskState:
    """
    Current portfolio risk state snapshot.
    
    This is the authoritative state object passed between risk engine calls.
    All fields are sourced from the database or live position tracking.
    """
    portfolio_value_usd: float
    peak_value_usd: float
    current_drawdown_pct: float

    # Open positions: {symbol: position_size_usd}
    open_positions: dict[str, float] = field(default_factory=dict)

    # Historical daily returns for VaR calculation (numpy array)
    daily_returns: Optional[np.ndarray] = None

    # Circuit breaker state
    circuit_breaker_level: CircuitBreakerLevel = CircuitBreakerLevel.NONE
    cb_consecutive_wins_since: int = 0
    cb_days_since_activation: int = 0

    @property
    def total_exposure_usd(self) -> float:
        return sum(self.open_positions.values())

    @property
    def total_exposure_pct(self) -> float:
        if self.portfolio_value_usd <= 0:
            return 0.0
        return self.total_exposure_usd / self.portfolio_value_usd * 100.0

    @property
    def is_halted(self) -> bool:
        return self.circuit_breaker_level == CircuitBreakerLevel.LEVEL_3


@dataclass
class VaRResult:
    """Value at Risk calculation result."""
    var_95_pct: float        # 95% VaR as % of portfolio
    var_99_pct: float        # 99% VaR as % of portfolio
    cvar_95_pct: float       # CVaR (Expected Shortfall) at 95%
    window_days: int
    is_reliable: bool        # False if insufficient history
    size_reduction_factor: float  # Multiplier from 0.0 to 1.0 to apply to sizing


class PortfolioRiskEngine:
    """
    Institutional portfolio-level risk management engine for APEX v11.0.
    
    This is a HARD GATE. No position enters without passing all risk checks.
    The engine never approves a position that violates concentration limits,
    correlation constraints, or circuit breakers.
    
    Usage:
        risk_engine = PortfolioRiskEngine()
        result = risk_engine.size_position(
            symbol="SOL/USDT",
            atr_14=1.5,
            current_price=100.0,
            portfolio_state=state,
            regime_breadth_pct=55.0,
        )
        if not result.approved:
            logger.warning("Position rejected: %s", result.rejection_reason)
            return
    
    Version: 11.0.0
    """

    def __init__(self) -> None:
        logger.info("[RiskEngine] Initialized. All risk gates ACTIVE.")

    def size_position(
        self,
        symbol: str,
        atr_14: float,
        current_price: float,
        portfolio_state: PortfolioRiskState,
        regime_breadth_pct: float,
        expanding_volatility: bool = False,
        crowded_funding: bool = False,
        open_position_returns: Optional[dict[str, np.ndarray]] = None,
        calibrated_probability: Optional[float] = None,
    ) -> PositionSizingResult:
        """
        Calculate approved position size after all risk constraints.
        
        Args:
            symbol: Trading pair to size.
            atr_14: 14-period ATR in price units.
            current_price: Current price of asset.
            portfolio_state: Current portfolio state snapshot.
            regime_breadth_pct: % of coins above 200 EMA (market breadth).
            expanding_volatility: True if realized vol is expanding.
            crowded_funding: True if funding rate is extreme (crowded longs).
            open_position_returns: Dict of {symbol: returns_array} for
                correlation calculation. None = skip correlation check.
            calibrated_probability: Calibrated ML probability [0, 1].
                If None, uses conservative fixed sizing.
        
        Returns:
            PositionSizingResult with approved size or rejection reason.
        """
        # ── Gate 0: Portfolio halt check ─────────────────────────────────────
        if portfolio_state.is_halted:
            return self._reject(
                symbol, portfolio_state,
                reason="LEVEL_3 circuit breaker: portfolio halted.",
                atr_pct=0.0, sl_dist=0.0, multiplier=0.0,
                cb_level=CircuitBreakerLevel.LEVEL_3,
            )

        # ── Gate 1: Gross exposure limit ─────────────────────────────────────
        if portfolio_state.total_exposure_pct >= MAX_GROSS_EXPOSURE_PCT:
            return self._reject(
                symbol, portfolio_state,
                reason=f"Gross exposure {portfolio_state.total_exposure_pct:.1f}% at maximum {MAX_GROSS_EXPOSURE_PCT:.0f}%.",
                atr_pct=0.0, sl_dist=0.0, multiplier=0.0,
            )

        # ── Step 1: ATR-based SL distance ────────────────────────────────────
        if current_price <= 0 or atr_14 <= 0:
            return self._reject(
                symbol, portfolio_state,
                reason="Invalid price or ATR. Cannot size position.",
                atr_pct=0.0, sl_dist=0.0, multiplier=0.0,
            )

        atr_pct = (atr_14 / current_price) * 100.0
        sl_distance_pct = ATR_SL_MULTIPLIER * atr_pct

        # ── Step 2: Regime multiplier ─────────────────────────────────────────
        regime_mult = self._compute_regime_multiplier(
            regime_breadth_pct, expanding_volatility, crowded_funding
        )

        # ── Step 3: Circuit breaker multiplier ───────────────────────────────
        cb_level = self._check_circuit_breaker(portfolio_state)
        cb_mult = 1.0
        if cb_level != CircuitBreakerLevel.NONE:
            cb_mult = DRAWDOWN_LEVELS[cb_level.value]["size_multiplier"]
            if cb_mult == 0.0:
                return self._reject(
                    symbol, portfolio_state,
                    reason=f"{cb_level.value} circuit breaker active: {DRAWDOWN_LEVELS[cb_level.value]['description']}",
                    atr_pct=atr_pct, sl_dist=sl_distance_pct,
                    multiplier=regime_mult * cb_mult,
                    cb_level=cb_level,
                )

        combined_multiplier = regime_mult * cb_mult

        # ── Step 4: Base position size ────────────────────────────────────────
        risk_amount_usd = (
            portfolio_state.portfolio_value_usd
            * MAX_RISK_PER_TRADE_PCT / 100.0
            * combined_multiplier
        )

        if sl_distance_pct <= 0:
            return self._reject(
                symbol, portfolio_state,
                reason="SL distance is zero. Cannot calculate position size.",
                atr_pct=atr_pct, sl_dist=sl_distance_pct, multiplier=combined_multiplier,
            )

        raw_position_usd = risk_amount_usd / (sl_distance_pct / 100.0)

        # ── Step 5: Apply Kelly if calibrated probability is available ────────
        # BLOCKED until Phase 2 ML is complete.
        # TEMP: Use fixed risk per trade.
        # TODO(Phase 2): Enable Kelly sizing: f* = (p * b - q) / b
        #   where b = TP/SL ratio, p = calibrated_probability, q = 1 - p.
        #   Apply half-Kelly: position *= 0.5 * kelly_fraction.
        if calibrated_probability is not None:
            logger.debug(
                "[RiskEngine] Calibrated probability %.3f available for %s. "
                "Kelly sizing DEFERRED until Phase 2 completion.",
                calibrated_probability, symbol
            )

        # ── Step 6: Tier-based maximum cap ───────────────────────────────────
        from services.data.validator import ASSET_TIERS
        if symbol in ASSET_TIERS.get("TIER_1", []):
            tier_key = "TIER_1"
        elif symbol in ASSET_TIERS.get("TIER_2", []):
            tier_key = "TIER_2"
        else:
            tier_key = "TIER_3"

        max_pos_usd = (
            portfolio_state.portfolio_value_usd
            * MAX_POSITION_PCT[tier_key] / 100.0
        )
        hit_tier_max = raw_position_usd > max_pos_usd
        position_usd = min(raw_position_usd, max_pos_usd)

        # ── Step 7: Concentration limit ───────────────────────────────────────
        hit_concentration = False
        concentration_rejection = None

        # Single asset max
        existing_in_symbol = portfolio_state.open_positions.get(symbol, 0.0)
        total_in_symbol = existing_in_symbol + position_usd
        if total_in_symbol / portfolio_state.portfolio_value_usd * 100.0 > MAX_SINGLE_ASSET_PCT:
            concentration_rejection = (
                f"Single asset {symbol} would reach "
                f"{total_in_symbol / portfolio_state.portfolio_value_usd * 100:.1f}% "
                f"> {MAX_SINGLE_ASSET_PCT}% limit."
            )
            hit_concentration = True

        # Tier 3 total cap
        if tier_key == "TIER_3" and not hit_concentration:
            tier3_total = sum(
                v for k, v in portfolio_state.open_positions.items()
                if k not in ASSET_TIERS.get("TIER_1", [])
                and k not in ASSET_TIERS.get("TIER_2", [])
            )
            tier3_pct = (tier3_total + position_usd) / portfolio_state.portfolio_value_usd * 100.0
            if tier3_pct > MAX_TIER3_TOTAL_PCT:
                concentration_rejection = (
                    f"Tier 3 total exposure would be {tier3_pct:.1f}% "
                    f"> {MAX_TIER3_TOTAL_PCT}% limit."
                )
                hit_concentration = True

        # Cluster exposure
        cluster = ASSET_CLUSTERS.get(symbol, "OTHER")
        max_cluster = MAX_CLUSTER_EXPOSURE_PCT.get(cluster, 10.0)
        cluster_total = sum(
            v for k, v in portfolio_state.open_positions.items()
            if ASSET_CLUSTERS.get(k) == cluster
        )
        cluster_pct = (cluster_total + position_usd) / portfolio_state.portfolio_value_usd * 100.0
        if cluster_pct > max_cluster and not hit_concentration:
            concentration_rejection = (
                f"Cluster '{cluster}' would reach {cluster_pct:.1f}% "
                f"> {max_cluster}% limit."
            )
            hit_concentration = True

        if hit_concentration and concentration_rejection:
            return self._reject(
                symbol, portfolio_state,
                reason=concentration_rejection,
                atr_pct=atr_pct, sl_dist=sl_distance_pct, multiplier=combined_multiplier,
                hit_concentration=True,
            )

        # ── Step 8: Correlation check ─────────────────────────────────────────
        hit_correlation = False
        if open_position_returns and len(portfolio_state.open_positions) > 0:
            correlation_rejection = self._check_correlation(
                symbol, open_position_returns, portfolio_state.open_positions
            )
            if correlation_rejection:
                return self._reject(
                    symbol, portfolio_state,
                    reason=correlation_rejection,
                    atr_pct=atr_pct, sl_dist=sl_distance_pct, multiplier=combined_multiplier,
                    hit_correlation=True,
                )

        # ── Step 9: VaR check ─────────────────────────────────────────────────
        var_reduction = 1.0
        if portfolio_state.daily_returns is not None:
            var_result = self.compute_var(portfolio_state)
            var_reduction = var_result.size_reduction_factor
            if var_reduction < 1.0:
                logger.warning(
                    "[RiskEngine] VaR reduction factor %.2f applied to %s. "
                    "VaR_95=%.2f%% VaR_99=%.2f%%",
                    var_reduction, symbol, var_result.var_95_pct, var_result.var_99_pct,
                )

        position_usd *= var_reduction

        # ── Final sanity: minimum viable position ─────────────────────────────
        if position_usd < 10.0:
            return self._reject(
                symbol, portfolio_state,
                reason=f"Position size ${position_usd:.2f} < $10 minimum after all constraints.",
                atr_pct=atr_pct, sl_dist=sl_distance_pct, multiplier=combined_multiplier,
            )

        position_pct = position_usd / portfolio_state.portfolio_value_usd * 100.0

        logger.info(
            "[RiskEngine] %s APPROVED. Size=$%,.0f (%.1f%%) "
            "Risk=$%,.0f SL=%.2f%% Regime=%.2f CB=%s",
            symbol, position_usd, position_pct,
            risk_amount_usd, sl_distance_pct, combined_multiplier,
            cb_level.value,
        )

        return PositionSizingResult(
            symbol=symbol,
            portfolio_value_usd=portfolio_state.portfolio_value_usd,
            approved=True,
            rejection_reason=None,
            risk_amount_usd=risk_amount_usd,
            position_size_usd=position_usd,
            position_size_pct=position_pct,
            atr_pct=atr_pct,
            sl_distance_pct=sl_distance_pct,
            regime_multiplier=combined_multiplier,
            hit_tier_max=hit_tier_max,
            hit_circuit_breaker=(cb_level != CircuitBreakerLevel.NONE),
            circuit_breaker_level=cb_level,
        )

    def compute_var(self, state: PortfolioRiskState) -> VaRResult:
        """
        Historical simulation VaR and CVaR.
        
        Uses 252-day rolling window of portfolio daily returns.
        
        Args:
            state: Current portfolio risk state with daily_returns array.
        
        Returns:
            VaRResult with VaR_95, VaR_99, CVaR_95 and size reduction factor.
        """
        returns = state.daily_returns
        is_reliable = True

        if returns is None or len(returns) < 30:
            logger.warning(
                "[RiskEngine] VaR: Insufficient return history (%s days < 30 minimum). "
                "VaR not computed.",
                len(returns) if returns is not None else 0
            )
            return VaRResult(
                var_95_pct=0.0, var_99_pct=0.0, cvar_95_pct=0.0,
                window_days=0, is_reliable=False, size_reduction_factor=1.0
            )

        if len(returns) < VAR_WINDOW_DAYS:
            logger.warning(
                "[RiskEngine] VaR: Only %d days of history (target: %d). "
                "Results less reliable.",
                len(returns), VAR_WINDOW_DAYS
            )
            is_reliable = False

        # Use last VAR_WINDOW_DAYS returns
        r = returns[-VAR_WINDOW_DAYS:]

        # Historical VaR (no distribution assumption — more robust for crypto)
        var_95 = abs(np.percentile(r, VAR_CONFIDENCE_95 * 100)) * 100.0
        var_99 = abs(np.percentile(r, VAR_CONFIDENCE_99 * 100)) * 100.0

        # CVaR (Expected Shortfall): mean of returns below VaR_95
        threshold_95 = np.percentile(r, VAR_CONFIDENCE_95 * 100)
        tail_returns = r[r <= threshold_95]
        cvar_95 = abs(np.mean(tail_returns)) * 100.0 if len(tail_returns) > 0 else var_95

        # Size reduction factor based on VaR thresholds
        reduction = 1.0
        if var_99 > VAR_99_REDUCE_THRESHOLD:
            reduction = 0.50
            logger.warning(
                "[RiskEngine] VaR_99 %.2f%% > %.1f%% threshold. Applying 50%% size reduction.",
                var_99, VAR_99_REDUCE_THRESHOLD,
            )
        elif var_95 > VAR_95_REDUCE_THRESHOLD:
            reduction = 0.75
            logger.warning(
                "[RiskEngine] VaR_95 %.2f%% > %.1f%% threshold. Applying 25%% size reduction.",
                var_95, VAR_95_REDUCE_THRESHOLD,
            )

        return VaRResult(
            var_95_pct=var_95,
            var_99_pct=var_99,
            cvar_95_pct=cvar_95,
            window_days=len(r),
            is_reliable=is_reliable,
            size_reduction_factor=reduction,
        )

    # ─── Private Methods ──────────────────────────────────────────────────────

    def _compute_regime_multiplier(
        self,
        breadth_pct: float,
        expanding_vol: bool,
        crowded_funding: bool,
    ) -> float:
        """
        Compute regime-based size multiplier.
        
        Justification: Position sizing must be proportional to the quality
        of the market environment. Systematic funds scale down in deteriorating
        regimes (cf. Moskowitz, Ooi & Pedersen, 2012).
        """
        # Breadth-based multiplier
        if breadth_pct >= 60.0:
            mult = 1.00
        elif breadth_pct >= 40.0:
            mult = 0.75
        elif breadth_pct >= 25.0:
            mult = 0.50
        else:
            mult = 0.25

        # Volatility expansion penalty
        # TEMP: 0.80 factor. Calibrate from regime-stratified performance data.
        if expanding_vol:
            mult *= 0.80

        # Crowded longs penalty (high funding rate = crowded)
        # TEMP: 0.70 factor. Calibrate from funding rate vs forward return data.
        if crowded_funding:
            mult *= 0.70

        # Hard clamp: never below 10%, never above 100%
        mult = max(0.10, min(1.00, mult))

        logger.debug(
            "[RiskEngine] Regime multiplier: breadth=%.1f%% expanding_vol=%s "
            "crowded=%s → multiplier=%.2f",
            breadth_pct, expanding_vol, crowded_funding, mult,
        )
        return mult

    def _check_circuit_breaker(
        self, state: PortfolioRiskState
    ) -> CircuitBreakerLevel:
        """Check if any drawdown circuit breaker is active."""
        dd = state.current_drawdown_pct

        # Preserve existing CB level (only escalate, don't de-escalate automatically)
        if state.circuit_breaker_level == CircuitBreakerLevel.LEVEL_3:
            if not self._check_recovery(state):
                return CircuitBreakerLevel.LEVEL_3

        if dd >= DRAWDOWN_LEVELS["LEVEL_3"]["threshold_pct"]:
            if state.circuit_breaker_level != CircuitBreakerLevel.LEVEL_3:
                logger.critical(
                    "[RiskEngine] CIRCUIT BREAKER LEVEL 3 ACTIVATED. "
                    "Drawdown=%.2f%%. ALL NEW POSITIONS HALTED.", dd
                )
            return CircuitBreakerLevel.LEVEL_3

        if dd >= DRAWDOWN_LEVELS["LEVEL_2"]["threshold_pct"]:
            if state.circuit_breaker_level.value not in ("LEVEL_2", "LEVEL_3"):
                logger.warning(
                    "[RiskEngine] Circuit Breaker Level 2 activated. "
                    "Drawdown=%.2f%%. Sizing reduced to 50%%.", dd
                )
            return CircuitBreakerLevel.LEVEL_2

        if dd >= DRAWDOWN_LEVELS["LEVEL_1"]["threshold_pct"]:
            if state.circuit_breaker_level == CircuitBreakerLevel.NONE:
                logger.warning(
                    "[RiskEngine] Circuit Breaker Level 1 activated. "
                    "Drawdown=%.2f%%. Sizing reduced to 75%%.", dd
                )
            return CircuitBreakerLevel.LEVEL_1

        return CircuitBreakerLevel.NONE

    def _check_recovery(self, state: PortfolioRiskState) -> bool:
        """Check if circuit breaker recovery conditions are met."""
        return (
            state.cb_consecutive_wins_since >= RECOVERY_REQUIRED_WINS
            or state.cb_days_since_activation >= RECOVERY_REQUIRED_DAYS
        )

    def _check_correlation(
        self,
        new_symbol: str,
        position_returns: dict[str, np.ndarray],
        open_positions: dict[str, float],
    ) -> Optional[str]:
        """
        Check if new position is too correlated with existing portfolio.
        
        Uses rolling 30-day return correlation to identify redundant exposure.
        
        Returns:
            Rejection reason string if correlation is too high, else None.
        """
        new_returns = position_returns.get(new_symbol)
        if new_returns is None or len(new_returns) < 10:
            logger.debug(
                "[RiskEngine] No return history for %s. Skipping correlation check.",
                new_symbol,
            )
            return None  # Cannot block without data

        high_corr_pairs = []

        for existing_symbol, existing_size in open_positions.items():
            existing_returns = position_returns.get(existing_symbol)
            if existing_returns is None or len(existing_returns) < 10:
                continue

            # Align lengths
            min_len = min(len(new_returns), len(existing_returns))
            if min_len < 5:
                continue

            corr = np.corrcoef(
                new_returns[-min_len:], existing_returns[-min_len:]
            )[0, 1]

            if abs(corr) > MAX_CORRELATION_FOR_NEW_POSITION:
                high_corr_pairs.append((existing_symbol, corr))

        if high_corr_pairs:
            pairs_str = ", ".join(
                f"{s}(ρ={c:.2f})" for s, c in high_corr_pairs
            )
            return (
                f"{new_symbol} correlation > {MAX_CORRELATION_FOR_NEW_POSITION} "
                f"with open positions: {pairs_str}. "
                f"Adding correlated position increases undiversified risk."
            )

        return None

    def _reject(
        self,
        symbol: str,
        state: PortfolioRiskState,
        reason: str,
        atr_pct: float,
        sl_dist: float,
        multiplier: float,
        cb_level: CircuitBreakerLevel = CircuitBreakerLevel.NONE,
        hit_concentration: bool = False,
        hit_correlation: bool = False,
    ) -> PositionSizingResult:
        """Create a rejected PositionSizingResult with logging."""
        logger.warning("[RiskEngine] %s REJECTED: %s", symbol, reason)
        return PositionSizingResult(
            symbol=symbol,
            portfolio_value_usd=state.portfolio_value_usd,
            approved=False,
            rejection_reason=reason,
            risk_amount_usd=0.0,
            position_size_usd=0.0,
            position_size_pct=0.0,
            atr_pct=atr_pct,
            sl_distance_pct=sl_dist,
            regime_multiplier=multiplier,
            hit_circuit_breaker=(cb_level != CircuitBreakerLevel.NONE),
            circuit_breaker_level=cb_level,
            hit_concentration_limit=hit_concentration,
            hit_correlation_block=hit_correlation,
        )
