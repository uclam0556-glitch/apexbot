"""
APEX Trading System v4.0
services/engine/risk_engine.py

Risk Engine — Kelly Criterion + Historical VaR + Drawdown Protection.

Design philosophy:
- Risk is managed at THREE levels:
  1. Per-trade sizing (Kelly + volatility/drawdown multipliers + hard caps)
  2. Portfolio-level VaR (historical simulation, not parametric)
  3. System-level circuit breakers (daily loss, drawdown, signal count, VaR)
- All inputs are explicit numeric parameters; no hidden state.
- Every method is independently callable and unit-testable.
- SL/TP placement is anchored to structural levels (swing points, volume nodes)
  with a mandatory minimum R:R of 1.5 enforced by SignalCore.

Volatility regime multipliers:
    LOW    = 1.2  (size up slightly in calm markets)
    NORMAL = 1.0  (base case)
    HIGH   = 0.7  (reduce in elevated volatility)
    CRISIS = 0.3  (survival mode — minimal exposure)

Drawdown multipliers:
    <  5%  drawdown → 1.00 (full allocation)
    5-10%  drawdown → 0.75 (cautious)
    > 10%  drawdown → 0.50 (half allocation until recovery)

Hard size caps (% of deposit):
    Min: 0.25%   Max: 2.00%

Structlog-format logging throughout for log aggregation compatibility.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import structlog

from shared.models import (
    CorrelationResult,
    ImbalanceZone,
    PositionSizeResult,
    RiskStatus,
    SLTPResult,
    StructureEvent,
    SwingPoint,
    VaRResult,
    VolatilityRegime,
    VolumeNode,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Volatility regime → position size multiplier
VOLATILITY_MULTIPLIERS: dict[VolatilityRegime, float] = {
    VolatilityRegime.LOW:    1.2,
    VolatilityRegime.NORMAL: 1.0,
    VolatilityRegime.HIGH:   0.7,
    VolatilityRegime.CRISIS: 0.3,
}

# Drawdown thresholds → multiplier
DRAWDOWN_THRESHOLDS: list[tuple[float, float]] = [
    (5.0,  1.00),   # < 5% drawdown → full size
    (10.0, 0.75),   # 5-10% drawdown → 75%
    (math.inf, 0.50),  # > 10% drawdown → 50%
]

# Hard position size caps
MIN_POSITION_PCT: float = 0.25   # 0.25% of deposit
MAX_POSITION_PCT: float = 2.00   # 2.00% of deposit

# Correlation threshold for new position rejection
CORRELATION_THRESHOLD: float = 0.85

# Round-number proximity warning (±0.1%)
ROUND_NUMBER_PROXIMITY_PCT: float = 0.1

# TP allocation: 40% at TP1, 35% at TP2, 25% at TP3
DEFAULT_TP_ALLOCATION: list[float] = [0.40, 0.35, 0.25]

# SL structural buffer (% beyond swing point)
SL_BUFFER_PCT: float = 0.003   # 0.3%

# Daily trading limits
DAILY_LOSS_STOP_PCT: float = 3.0      # stop if daily P&L < -3%
DRAWDOWN_STOP_PCT: float = 15.0       # stop if drawdown from peak > 15%
MAX_DAILY_SIGNALS: int = 5            # max signals per day
VAR_PORTFOLIO_STOP_PCT: float = 5.0   # stop if portfolio VaR > 5%


# ---------------------------------------------------------------------------
# RiskEngine
# ---------------------------------------------------------------------------

class RiskEngine:
    """
    Institutional-grade Risk Engine for APEX Trading System v4.0.

    Implements:
        - Half-Kelly position sizing with regime and drawdown adjustments
        - Historical simulation VaR (95% and 99%)
        - Rolling 30-day correlation screening for open positions
        - Structural SL/TP placement with key level awareness
        - Daily trading limit enforcement
        - Portfolio-level circuit breakers

    All methods are stateless (no persistent state between calls) to ensure
    deterministic, auditable outputs that the AI audit layer can validate.

    Usage:
        engine = RiskEngine()
        size_result = engine.calculate_position_size_kelly(
            deposit=10_000,
            win_rate_calibrated=0.58,
            avg_win_pct=2.1,
            avg_loss_pct=1.0,
            volatility_regime=VolatilityRegime.NORMAL,
            current_drawdown_pct=2.5,
        )
    """

    def __init__(self) -> None:
        """Initialize the Risk Engine with structlog-bound logger."""
        self._log = structlog.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # POSITION SIZING — HALF KELLY + ADJUSTMENTS
    # ------------------------------------------------------------------

    def calculate_position_size_kelly(
        self,
        deposit: float,
        win_rate_calibrated: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        volatility_regime: VolatilityRegime,
        current_drawdown_pct: float,
    ) -> PositionSizeResult:
        """
        Calculate the risk-adjusted position size using the Half-Kelly Criterion.

        Kelly Formula (fraction of capital to risk):
            W = win rate (calibrated from Feature Store)
            R = average win / average loss ratio
            Kelly = W - (1 - W) / R

        Half Kelly (our operating mode):
            half_kelly = kelly / 2
            WHY half: Full Kelly maximizes expected log-wealth but produces
            extreme drawdowns (50%+ is common). Half-Kelly halves the variance
            while retaining ~75% of the geometric growth rate. Industry standard
            for institutional systematic trading.

        Adjustments applied sequentially:
            1. Volatility multiplier   (regime-dependent, see VOLATILITY_MULTIPLIERS)
            2. Drawdown multiplier     (scaled back during drawdown periods)
            3. Hard cap enforcement    (min 0.25%, max 2.00%)

        Args:
            deposit: Account size in USD.
            win_rate_calibrated: Calibrated win rate from Feature Store (0-1).
                                 Must be > 0 and < 1.
            avg_win_pct: Average winning trade magnitude as a decimal percentage
                         (e.g. 2.1 means 2.1% average winner).
            avg_loss_pct: Average losing trade magnitude as a positive decimal
                          (e.g. 1.0 means 1.0% average loser).
            volatility_regime: Current market volatility regime.
            current_drawdown_pct: Current drawdown from peak in percentage
                                  (positive value, e.g. 7.5 means 7.5% drawdown).

        Returns:
            PositionSizeResult with all intermediate values for auditability.

        Raises:
            ValueError: If win_rate_calibrated is outside (0, 1) or avg_loss_pct
                        is zero (which would cause division by zero in R).
        """
        # Input validation
        w = float(np.clip(win_rate_calibrated, 0.001, 0.999))
        if avg_loss_pct <= 0:
            self._log.error("kelly_invalid_avg_loss", avg_loss_pct=avg_loss_pct)
            raise ValueError("avg_loss_pct must be > 0 to compute Kelly criterion")
        if avg_win_pct <= 0:
            self._log.error("kelly_invalid_avg_win", avg_win_pct=avg_win_pct)
            raise ValueError("avg_win_pct must be > 0 to compute Kelly criterion")

        # Step 1: Kelly Criterion
        R = avg_win_pct / avg_loss_pct   # win/loss ratio
        kelly_fraction = w - (1.0 - w) / R

        # Negative Kelly means the edge is negative — refuse trade
        if kelly_fraction <= 0:
            self._log.warning(
                "kelly_negative_edge",
                w=round(w, 4),
                R=round(R, 4),
                kelly=round(kelly_fraction, 4),
            )
            kelly_fraction = 0.0
            half_kelly = 0.0
        else:
            # Step 2: Half Kelly
            half_kelly = kelly_fraction / 2.0

        # Step 3: Volatility multiplier
        vol_mult = VOLATILITY_MULTIPLIERS.get(volatility_regime, 1.0)

        # Step 4: Drawdown multiplier
        dd_mult = self._get_drawdown_multiplier(abs(current_drawdown_pct))

        # Step 5: Adjusted size (as % of deposit)
        # half_kelly is a fraction (e.g. 0.02 = 2%); convert appropriately
        # Kelly outputs fraction of capital to risk, already in % terms when
        # avg_win_pct and avg_loss_pct are expressed in %
        adjusted_size_pct = half_kelly * 100.0 * vol_mult * dd_mult

        # Step 6: Hard caps (Regime-based)
        # V6.1: Regime caps: LOW (BULL)=2.0%, NORMAL (SIDEWAYS)=1.0%, HIGH (BEAR)=0.5%
        max_pct = 2.00
        if volatility_regime == VolatilityRegime.NORMAL:
            max_pct = 1.00
        elif volatility_regime in [VolatilityRegime.HIGH, VolatilityRegime.CRISIS]:
            max_pct = 0.50
            
        min_pct = 0.10  # 0.1% minimum risk
            
        capped_at: str | None = None
        if half_kelly > 0:
            if adjusted_size_pct < min_pct:
                adjusted_size_pct = min_pct
                capped_at = "min"
            elif adjusted_size_pct > max_pct:
                adjusted_size_pct = max_pct
                capped_at = "max"

        final_size_usd = deposit * adjusted_size_pct / 100.0

        self._log.info(
            "kelly_position_size",
            kelly_fraction=round(kelly_fraction, 6),
            half_kelly=round(half_kelly, 6),
            vol_mult=vol_mult,
            dd_mult=dd_mult,
            final_size_pct=round(adjusted_size_pct, 4),
            final_size_usd=round(final_size_usd, 2),
            capped_at=capped_at,
        )

        return PositionSizeResult(
            kelly_fraction=round(kelly_fraction, 6),
            half_kelly=round(half_kelly, 6),
            volatility_multiplier=vol_mult,
            drawdown_multiplier=dd_mult,
            final_size_pct=round(adjusted_size_pct, 6),
            final_size_usd=round(final_size_usd, 2),
            regime=volatility_regime,
            current_drawdown_pct=round(current_drawdown_pct, 4),
            capped_at=capped_at,
        )

    # ------------------------------------------------------------------
    # VALUE AT RISK — HISTORICAL SIMULATION
    # ------------------------------------------------------------------

    def calculate_var(
        self,
        open_positions: list[str],
        prices_history: dict[str, pd.Series],
        confidence_level: float = 0.95,
        horizon_days: int = 1,
    ) -> VaRResult:
        """
        Calculate portfolio Value at Risk using historical simulation.

        Method — Historical Simulation (non-parametric):
            1. For each symbol in open_positions, retrieve its daily return
               series from prices_history.
            2. Build an equal-weighted portfolio return series by averaging
               all constituent return series for overlapping dates.
               WHY equal-weight: We don't have position sizes in this method;
               equal weight gives a conservative/average-case estimate.
            3. Compute the empirical quantile at (1 - confidence_level) for 95%
               and at 0.01 for 99% VaR.
            4. Scale to horizon_days using the square-root-of-time rule:
               VaR(T) = VaR(1) × √T
               WHY sqrt(T): This assumes i.i.d. returns — appropriate for
               short horizons (1-5 days). For longer horizons, this understates
               tail risk but is the standard regulatory approximation.

        Args:
            open_positions: List of symbol strings currently in the portfolio.
            prices_history: Dict mapping symbol → pd.Series of price history
                            (indexed by date/timestamp, any frequency).
            confidence_level: VaR confidence level (default 0.95 → 95% VaR).
            horizon_days: Holding period in days (default 1).

        Returns:
            VaRResult with 95% and 99% VaR, expressed as positive percentages
            (e.g. 3.2 means "3.2% VaR").
        """
        n_positions = len(open_positions)

        if not open_positions or not prices_history:
            self._log.warning("var_no_positions_or_history")
            return VaRResult(
                var_95_pct=0.0,
                var_99_pct=0.0,
                horizon_days=horizon_days,
                open_positions=n_positions,
                exceeds_threshold=False,
            )

        # Build return matrix for symbols with available history
        return_series: list[pd.Series] = []

        for symbol in open_positions:
            if symbol not in prices_history:
                self._log.warning(
                    "var_missing_price_history", symbol=symbol
                )
                continue

            prices = prices_history[symbol].dropna()
            if len(prices) < 10:
                self._log.warning(
                    "var_insufficient_history", symbol=symbol, rows=len(prices)
                )
                continue

            # Compute percentage returns
            pct_returns = prices.pct_change().dropna() * 100.0
            return_series.append(pct_returns)

        if not return_series:
            self._log.error("var_no_usable_return_series")
            return VaRResult(
                var_95_pct=0.0,
                var_99_pct=0.0,
                horizon_days=horizon_days,
                open_positions=n_positions,
                exceeds_threshold=False,
            )

        # Align all return series to their common date range (inner join)
        if len(return_series) == 1:
            portfolio_returns = return_series[0]
        else:
            aligned = pd.concat(return_series, axis=1).dropna()
            if aligned.empty:
                # Fall back to first available series
                portfolio_returns = return_series[0]
            else:
                # Equal-weighted portfolio return
                portfolio_returns = aligned.mean(axis=1)

        returns_arr = portfolio_returns.values

        # Empirical VaR at the given confidence level
        # VaR is expressed as a LOSS, so we take the left-tail quantile
        var_1d_95 = float(np.abs(np.percentile(returns_arr, (1 - confidence_level) * 100)))
        var_1d_99 = float(np.abs(np.percentile(returns_arr, 1.0)))  # 99% VaR = 1st percentile

        # Scale to horizon using square-root-of-time rule
        sqrt_horizon = math.sqrt(horizon_days)
        var_95_scaled = var_1d_95 * sqrt_horizon
        var_99_scaled = var_1d_99 * sqrt_horizon

        exceeds = var_95_scaled > VAR_PORTFOLIO_STOP_PCT

        self._log.info(
            "var_calculated",
            var_95_pct=round(var_95_scaled, 4),
            var_99_pct=round(var_99_scaled, 4),
            horizon_days=horizon_days,
            open_positions=n_positions,
            exceeds_threshold=exceeds,
        )

        return VaRResult(
            var_95_pct=round(var_95_scaled, 4),
            var_99_pct=round(var_99_scaled, 4),
            horizon_days=horizon_days,
            open_positions=n_positions,
            exceeds_threshold=exceeds,
        )

    # ------------------------------------------------------------------
    # CORRELATION SCREENING
    # ------------------------------------------------------------------

    def check_correlation(
        self,
        new_symbol: str,
        open_positions: list[str],
        prices_30d: dict[str, pd.Series],
    ) -> CorrelationResult:
        """
        Check whether adding a new position would introduce excessive correlation
        with existing portfolio positions.

        Method — Rolling 30-day Pearson Correlation:
            1. Compute daily percentage returns for new_symbol and each
               existing position over the last 30 days.
            2. Calculate the Pearson correlation coefficient between new_symbol
               and each existing position.
            3. Flag as correlated if any |correlation| > CORRELATION_THRESHOLD (0.85).

        WHY 0.85 threshold: At |r| > 0.85, two assets are essentially the same
        risk factor — holding both doubles exposure without meaningful
        diversification. This is the standard institutional threshold.

        Args:
            new_symbol: Symbol being considered for the new trade.
            open_positions: List of currently open position symbols.
            prices_30d: Dict mapping symbol → pd.Series of prices over the
                        last 30 days (daily close prices, indexed by date).

        Returns:
            CorrelationResult with correlation analysis details.
        """
        if not open_positions:
            return CorrelationResult(
                new_symbol=new_symbol,
                open_positions=[],
                max_correlation=0.0,
                correlated_with=None,
                correlation_ok=True,
                deribit_vol_warn=False,
            )

        if new_symbol not in prices_30d:
            self._log.warning(
                "correlation_missing_new_symbol_data", symbol=new_symbol
            )
            return CorrelationResult(
                new_symbol=new_symbol,
                open_positions=open_positions,
                max_correlation=0.0,
                correlated_with=None,
                correlation_ok=True,  # assume OK if no data
                deribit_vol_warn=False,
            )

        new_prices = prices_30d[new_symbol].dropna()
        new_returns = new_prices.pct_change().dropna()

        if len(new_returns) < 5:
            self._log.warning(
                "correlation_insufficient_data",
                symbol=new_symbol,
                rows=len(new_returns),
            )
            return CorrelationResult(
                new_symbol=new_symbol,
                open_positions=open_positions,
                max_correlation=0.0,
                correlated_with=None,
                correlation_ok=True,
                deribit_vol_warn=False,
            )

        max_corr: float = 0.0
        correlated_with: str | None = None

        for existing_symbol in open_positions:
            if existing_symbol not in prices_30d:
                self._log.debug(
                    "correlation_missing_existing_data",
                    symbol=existing_symbol,
                )
                continue

            existing_prices = prices_30d[existing_symbol].dropna()
            existing_returns = existing_prices.pct_change().dropna()

            # Align to common timestamps
            aligned = pd.concat(
                [new_returns, existing_returns], axis=1
            ).dropna()

            if len(aligned) < 5:
                continue

            # Pearson correlation
            corr_matrix = aligned.corr()
            corr_value = float(abs(corr_matrix.iloc[0, 1]))

            if corr_value > max_corr:
                max_corr = corr_value
                if corr_value > CORRELATION_THRESHOLD:
                    correlated_with = existing_symbol

        correlation_ok = max_corr <= CORRELATION_THRESHOLD

        # deribit_vol_warn: check if BTC/USDT is in open positions and new
        # symbol is highly correlated (options IV risk warning — v3 feature)
        deribit_vol_warn = (
            not correlation_ok
            and ("BTC/USDT" in open_positions or new_symbol == "BTC/USDT")
        )

        self._log.info(
            "correlation_check",
            new_symbol=new_symbol,
            max_correlation=round(max_corr, 4),
            correlated_with=correlated_with,
            correlation_ok=correlation_ok,
        )

        return CorrelationResult(
            new_symbol=new_symbol,
            open_positions=open_positions,
            max_correlation=round(max_corr, 4),
            correlated_with=correlated_with,
            correlation_ok=correlation_ok,
            deribit_vol_warn=deribit_vol_warn,
        )

    # ------------------------------------------------------------------
    # SL/TP CALCULATION
    # ------------------------------------------------------------------

    def calculate_sl_tp(
        self,
        entry: float,
        direction: str,
        atr: float,
        swing_points: list[SwingPoint],
        imbalance_zones: list[ImbalanceZone],
        volume_nodes: list[VolumeNode],
        key_levels: Optional[list[float]] = None,
    ) -> SLTPResult:
        """
        Calculate Stop Loss and three Take Profit levels anchored to market structure.

        Stop Loss placement:
            For LONG trades:
                SL = nearest swing LOW below entry, minus SL_BUFFER_PCT (0.3%)
                If no swing low found: SL = entry - 2.0 × ATR
            For SHORT trades:
                SL = nearest swing HIGH above entry, plus SL_BUFFER_PCT (0.3%)
                If no swing high found: SL = entry + 2.0 × ATR
            Round-number warning: if SL is within ±0.1% of a round number
            (multiple of 100, 1000, or 10000), set sl_near_round_number=True

        Take Profit levels:
            TP1: Nearest LVN (Low Volume Node) in the direction of trade.
                 WHY LVN: Price moves quickly through low-volume zones;
                 a TP at the near edge of an LVN has high fill probability.
                 Fallback: entry ± 1.5 × ATR
            TP2: Next significant key level (from key_levels list) or
                 nearest bullish/bearish FVG midpoint beyond TP1.
                 Fallback: entry ± 2.5 × ATR
            TP3: Structural target — next swing high/low in direction.
                 Fallback: entry ± 4.0 × ATR
            Minimum R:R enforced at TP1: (TP1 - entry) / (entry - SL) >= 1.5

        Args:
            entry: Planned entry price.
            direction: 'LONG' or 'SHORT'.
            atr: ATR value for the current timeframe (used for fallbacks).
            swing_points: List of SwingPoint models (both HIGHs and LOWs).
            imbalance_zones: List of ImbalanceZone models.
            volume_nodes: List of VolumeNode models.
            key_levels: Optional additional key price levels (e.g. from HTF).

        Returns:
            SLTPResult with SL, TP1/TP2/TP3, allocation, R:R, and metadata.

        Raises:
            ValueError: If entry <= 0, ATR <= 0, or direction is invalid.
        """
        if entry <= 0:
            raise ValueError(f"Entry price must be positive, got {entry}")
        if atr <= 0:
            raise ValueError(f"ATR must be positive, got {atr}")
        if direction != "LONG":
            raise ValueError(f"direction must be 'LONG', got {direction} (Spot-Only mode)")

        is_long = True
        key_levels = key_levels or []

        # ── Stop Loss ──────────────────────────────────────────────────────
        stop_loss = self._calculate_stop_loss(entry, is_long, atr, swing_points)

        # Calculate Stop Loss distance
        sl_distance = abs(entry - stop_loss)
        
        # MINIMUM SL CHECK (Protection against wicks and noise)
        # 1. At least 1.5% absolute minimum for crypto volatility
        min_sl_pct_dist = entry * 0.015
        # 2. At least 1.5x ATR minimum for current market noise
        min_sl_atr_dist = atr * 1.5
        
        min_required_distance = max(min_sl_pct_dist, min_sl_atr_dist)
        
        if sl_distance < min_required_distance:
            self._log.debug("sl_too_tight_widening", original=sl_distance, new=min_required_distance)
            sl_distance = min_required_distance
            stop_loss = entry - sl_distance if is_long else entry + sl_distance
            
        # Hard Cap SL at 5%
        max_sl_distance = entry * 0.05
        if sl_distance > max_sl_distance:
            # Cap the SL to 5% exactly
            sl_distance = max_sl_distance
            stop_loss = entry - sl_distance if is_long else entry + sl_distance

        # Round-number proximity check
        sl_near_round_number = self._is_near_round_number(stop_loss)

        # Buffer percentage
        sl_buffer_actual = abs(stop_loss - entry) / entry * 100

        # ── Take Profits (Fixed R:R for Realistic Sniper Mode) ──
        if is_long:
            tp1 = entry + 1.5 * sl_distance
            tp2 = entry + 2.5 * sl_distance
            tp3 = entry + 3.5 * sl_distance
        else:
            tp1 = entry - 1.5 * sl_distance
            tp2 = entry - 2.5 * sl_distance
            tp3 = entry - 3.5 * sl_distance
            
        tp1_rr = 1.5

        self._log.info(
            "sl_tp_calculated",
            entry=round(entry, 4),
            direction=direction,
            stop_loss=round(stop_loss, 4),
            tp1=round(tp1, 4),
            tp2=round(tp2, 4),
            tp3=round(tp3, 4),
            rr_tp1=round(tp1_rr, 2),
            sl_near_round=sl_near_round_number,
        )

        return SLTPResult(
            stop_loss=round(stop_loss, 8),
            take_profit_1=round(tp1, 8),
            take_profit_2=round(tp2, 8),
            take_profit_3=round(tp3, 8),
            tp_allocation=DEFAULT_TP_ALLOCATION,
            rr_ratio_tp1=round(tp1_rr, 4),
            sl_buffer_pct=round(sl_buffer_actual, 4),
            sl_near_round_number=sl_near_round_number,
        )

    # ------------------------------------------------------------------
    # DAILY LIMITS CHECK
    # ------------------------------------------------------------------

    def calculate_daily_limits(
        self,
        daily_stats: dict,
    ) -> RiskStatus:
        """
        Evaluate whether trading can continue today based on daily risk limits.

        Limit checks (ALL are hard stops — any breach halts trading):
            1. daily_pnl_pct  < -3.0%        → daily loss limit breached
            2. drawdown_from_peak_pct > 15.0% → maximum drawdown reached
            3. daily_signals_used >= 5        → max daily signal count reached
            4. var_portfolio_pct > 5.0%       → portfolio VaR too high
            5. consecutive_losses >= 4        → consecutive loss streak stop

        Args:
            daily_stats: Dictionary with keys:
                - 'daily_pnl_pct' (float): Today's P&L as percentage (negative = loss)
                - 'drawdown_from_peak_pct' (float): Current drawdown from peak (positive)
                - 'daily_signals_used' (int): Number of signals already used today
                - 'var_portfolio_pct' (float): Current portfolio VaR %
                - 'consecutive_losses' (int): Current streak of consecutive losing trades

        Returns:
            RiskStatus with can_trade flag and reason if stopped.
        """
        daily_pnl = float(daily_stats.get("daily_pnl_pct", 0.0))
        drawdown = float(daily_stats.get("drawdown_from_peak_pct", 0.0))
        signals_used = int(daily_stats.get("daily_signals_used", 0))
        var_pct = float(daily_stats.get("var_portfolio_pct", 0.0))
        consecutive_losses = int(daily_stats.get("consecutive_losses", 0))

        stop_reason: str | None = None

        if daily_pnl < -DAILY_LOSS_STOP_PCT:
            stop_reason = (
                f"Daily loss limit breached: {daily_pnl:.2f}% "
                f"(limit: -{DAILY_LOSS_STOP_PCT:.1f}%)"
            )
        elif drawdown > DRAWDOWN_STOP_PCT:
            stop_reason = (
                f"Maximum drawdown reached: {drawdown:.2f}% "
                f"(limit: {DRAWDOWN_STOP_PCT:.1f}%)"
            )
        elif signals_used >= MAX_DAILY_SIGNALS:
            stop_reason = (
                f"Maximum daily signal count reached: {signals_used} "
                f"(limit: {MAX_DAILY_SIGNALS})"
            )
        elif var_pct > VAR_PORTFOLIO_STOP_PCT:
            stop_reason = (
                f"Portfolio VaR exceeds threshold: {var_pct:.2f}% "
                f"(limit: {VAR_PORTFOLIO_STOP_PCT:.1f}%)"
            )
        elif consecutive_losses >= 4:
            stop_reason = (
                f"Consecutive loss streak: {consecutive_losses} losses "
                "(limit: 4)"
            )

        can_trade = stop_reason is None

        if not can_trade:
            self._log.warning(
                "daily_limit_breached",
                stop_reason=stop_reason,
                daily_pnl=daily_pnl,
                drawdown=drawdown,
                signals_used=signals_used,
                var_pct=var_pct,
                consecutive_losses=consecutive_losses,
            )
        else:
            self._log.debug(
                "daily_limits_ok",
                daily_pnl=daily_pnl,
                drawdown=drawdown,
                signals_used=signals_used,
                var_pct=var_pct,
            )

        return RiskStatus(
            can_trade=can_trade,
            stop_reason=stop_reason,
            daily_signals_used=signals_used,
            daily_pnl_pct=daily_pnl,
            drawdown_from_peak_pct=drawdown,
            consecutive_losses=consecutive_losses,
            var_portfolio_pct=var_pct,
        )

    # ------------------------------------------------------------------
    # CIRCUIT BREAKERS
    # ------------------------------------------------------------------

    def check_circuit_breakers(
        self,
        market_data: dict,
        portfolio_data: dict,
    ) -> tuple[bool, str]:
        """
        Check system-level circuit breakers for emergency trading halt.

        Circuit breakers (evaluated in priority order):
            1. BTC Flash Crash: btc_1h_change_pct < -10%
               WHY: A 10% BTC crash in 1 hour triggers cascading liquidations
               across all crypto assets — no new entries until stability.

            2. Extreme VaR: portfolio VaR > 8% (alert level, not daily limit)
               This is the EMERGENCY level (above the 5% daily-limit level).

            3. Liquidity Crisis: bid-ask spread > 2% on primary instrument
               WHY: Extreme spreads indicate market maker withdrawal — entries
               at these spreads guarantee slippage that destroys edge.

            4. Exchange Down: API error count in last 60s > threshold
               WHY: Partial data is worse than no data; stale prices lead to
               wrong SL placement.

            5. Consecutive Losses (hard): >= 4 consecutive losses (emergency stop)
               This duplicates the daily limit but as an emergency breaker.

            6. ML Model Staleness: last_model_retrain_days > 45
               WHY: An ML model more than 45 days old has likely drifted from
               the current market regime and should not be trusted.

            7. Calibration Drift: calibration_drift_pct > 15%
               WHY: If the calibrated win rate diverges from actual by >15pp,
               the Kelly sizing is fundamentally miscalibrated.

        Args:
            market_data: Dictionary with keys:
                - 'btc_1h_change_pct' (float): BTC 1-hour price change %
                - 'primary_spread_pct' (float): Current bid-ask spread %
                - 'api_error_count_60s' (int): API errors in last 60 seconds
            portfolio_data: Dictionary with keys:
                - 'var_portfolio_pct' (float): Current portfolio VaR %
                - 'consecutive_losses' (int): Streak of consecutive losses
                - 'last_model_retrain_days' (int): Days since last ML retrain
                - 'calibration_drift_pct' (float): Calibration drift (positive)
                - 'open_positions_count' (int): Number of open positions

        Returns:
            Tuple (can_trade: bool, stop_reason: str).
            stop_reason is empty string if can_trade is True.
        """
        # Market data checks
        btc_change = float(market_data.get("btc_1h_change_pct", 0.0))
        spread_pct = float(market_data.get("primary_spread_pct", 0.0))
        api_errors = int(market_data.get("api_error_count_60s", 0))

        # Portfolio data checks
        var_pct = float(portfolio_data.get("var_portfolio_pct", 0.0))
        consecutive_losses = int(portfolio_data.get("consecutive_losses", 0))
        model_age_days = int(portfolio_data.get("last_model_retrain_days", 0))
        calibration_drift = float(portfolio_data.get("calibration_drift_pct", 0.0))

        # ── Evaluate circuit breakers ──────────────────────────────────
        checks: list[tuple[bool, str]] = [
            (
                btc_change < -10.0,
                f"BTC flash crash detected: {btc_change:.2f}% in 1h (threshold: -10%)",
            ),
            (
                var_pct > 8.0,
                f"Emergency portfolio VaR: {var_pct:.2f}% (emergency threshold: 8%)",
            ),
            (
                spread_pct > 2.0,
                f"Extreme bid-ask spread: {spread_pct:.2f}% (threshold: 2%)",
            ),
            (
                api_errors > 10,
                f"Excessive API errors: {api_errors} in 60s (threshold: 10)",
            ),
            (
                consecutive_losses >= 4,
                f"Emergency: {consecutive_losses} consecutive losses (threshold: 4)",
            ),
            (
                model_age_days > 45,
                f"ML model stale: {model_age_days} days since retrain (threshold: 45)",
            ),
            (
                calibration_drift > 15.0,
                f"Calibration drift critical: {calibration_drift:.2f}% (threshold: 15%)",
            ),
        ]

        for triggered, reason in checks:
            if triggered:
                self._log.critical(
                    "circuit_breaker_triggered",
                    reason=reason,
                    btc_change=btc_change,
                    var_pct=var_pct,
                    spread_pct=spread_pct,
                    api_errors=api_errors,
                    consecutive_losses=consecutive_losses,
                    model_age_days=model_age_days,
                    calibration_drift=calibration_drift,
                )
                return False, reason

        self._log.debug(
            "circuit_breakers_ok",
            btc_change=btc_change,
            var_pct=var_pct,
            spread_pct=spread_pct,
        )
        return True, ""

    # ------------------------------------------------------------------
    # PRIVATE HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _get_drawdown_multiplier(drawdown_pct: float) -> float:
        """
        Map a drawdown percentage to the corresponding position size multiplier.

        Uses the DRAWDOWN_THRESHOLDS table:
            <  5% → 1.00 (full allocation)
            5-10% → 0.75 (reduced)
            > 10% → 0.50 (half allocation)

        Args:
            drawdown_pct: Current drawdown from peak as a positive percentage.

        Returns:
            float: Multiplier in [0.50, 1.00].
        """
        prev_threshold = 0.0
        for threshold, multiplier in DRAWDOWN_THRESHOLDS:
            if prev_threshold <= drawdown_pct < threshold:
                return multiplier
            prev_threshold = threshold
        # > all thresholds (should be covered by math.inf in last entry)
        return 0.50

    @staticmethod
    def _calculate_stop_loss(
        entry: float,
        is_long: bool,
        atr: float,
        swing_points: list[SwingPoint],
    ) -> float:
        """
        Find the nearest structural stop loss level.

        For LONG trades: nearest swing LOW below entry, minus SL_BUFFER_PCT.
        For SHORT trades: nearest swing HIGH above entry, plus SL_BUFFER_PCT.
        Fallback: ATR-based SL (2.0 × ATR).

        Args:
            entry: Entry price.
            is_long: True for long trade, False for short.
            atr: Current ATR value.
            swing_points: List of SwingPoint models.

        Returns:
            float: Stop loss price.
        """
        buffer_mult = 1.0 - SL_BUFFER_PCT

        # Candidate: swing LOWS below entry
        candidates = [
            sp.price for sp in swing_points
            if sp.type == "LOW" and sp.price < entry
        ]
        if candidates:
            nearest = max(candidates)  # highest low below entry
            return nearest * buffer_mult
        else:
            return entry - 2.0 * atr

    @staticmethod
    def _find_tp1(
        entry: float,
        is_long: bool,
        atr: float,
        volume_nodes: list[VolumeNode],
        sl_distance: float,
    ) -> float:
        """
        Find TP1: nearest LVN in the direction of trade.

        LVN (Low Volume Node) is the ideal first target because price moves
        quickly through thin liquidity areas — high probability of reaching
        and filling TP1 in a single impulse move.

        Args:
            entry: Entry price.
            is_long: Direction flag.
            atr: ATR fallback.
            volume_nodes: List of VolumeNode models.
            sl_distance: SL distance (used for R:R enforcement).

        Returns:
            float: TP1 price.
        """
        min_tp1_distance = 1.5 * sl_distance  # enforce 1.5R minimum

        lvns = [n for n in volume_nodes if n.type == "LVN"]

        candidates = [n.price for n in lvns if n.price > entry + min_tp1_distance]
        if candidates:
            return min(candidates)  # nearest LVN above entry
        return entry + max(1.5 * atr, min_tp1_distance)

    @staticmethod
    def _find_tp2(
        entry: float,
        is_long: bool,
        atr: float,
        key_levels: list[float],
        imbalance_zones: list[ImbalanceZone],
        tp1: float,
        sl_distance: float,
    ) -> float:
        """
        Find TP2: next key level beyond TP1, or FVG midpoint.

        Priority:
            1. Nearest key level beyond TP1 in trade direction.
            2. Midpoint of nearest unfilled FVG beyond TP1.
            3. Fallback: TP1 + 1.0 ATR (or TP1 - 1.0 ATR for shorts).

        Args:
            entry: Entry price.
            is_long: Direction flag.
            atr: ATR fallback.
            key_levels: External key price levels.
            imbalance_zones: FVG zones.
            tp1: TP1 level (TP2 must be beyond this).
            sl_distance: SL distance.

        Returns:
            float: TP2 price.
        """
        min_tp2_distance = 2.5 * sl_distance

        # Key levels beyond TP1
        kl_candidates = [
            lv for lv in key_levels if lv > tp1 and lv > entry + min_tp2_distance
        ]
        if kl_candidates:
            return min(kl_candidates)

        # FVG midpoints beyond TP1 (bearish FVGs act as resistance)
        fvg_candidates = [
            (z.low + z.high) / 2
            for z in imbalance_zones
            if z.type == "BEARISH_FVG"
            and not z.filled
            and (z.low + z.high) / 2 > tp1
            and (z.low + z.high) / 2 > entry + min_tp2_distance
        ]
        if fvg_candidates:
            return min(fvg_candidates)

        return max(tp1 + 1.0 * atr, entry + min_tp2_distance)

    @staticmethod
    def _find_tp3(
        entry: float,
        is_long: bool,
        atr: float,
        swing_points: list[SwingPoint],
        tp2: float,
        sl_distance: float,
    ) -> float:
        """
        Find TP3: structural swing target beyond TP2.

        Logic:
            For longs: nearest swing HIGH above TP2 (structural resistance).
            For shorts: nearest swing LOW below TP2 (structural support).
            Fallback: TP2 + 2.0 ATR (or TP2 - 2.0 ATR for shorts).

        Args:
            entry: Entry price.
            is_long: Direction flag.
            atr: ATR fallback.
            swing_points: SwingPoint list.
            tp2: TP2 level (TP3 must be beyond this).
            sl_distance: SL distance.

        Returns:
            float: TP3 price.
        """
        min_tp3_distance = 4.0 * sl_distance

        candidates = [
            sp.price for sp in swing_points
            if sp.type == "HIGH"
            and sp.price > tp2
            and sp.price > entry + min_tp3_distance
        ]
        if candidates:
            return min(candidates)  # nearest structural high
        return max(tp2 + 2.0 * atr, entry + min_tp3_distance)

    @staticmethod
    def _is_near_round_number(price: float) -> bool:
        """
        Check whether a price is within ±0.1% of a psychologically significant
        round number (multiple of 100, 1000, or 10,000).

        WHY adversarial warning: Institutional stop-hunters commonly place orders
        just beyond round numbers. Having our SL on a round number increases the
        probability of a stop-hunt taking us out before the trade plays out.

        Args:
            price: Price to check.

        Returns:
            bool: True if price is within 0.1% of a round number.
        """
        proximity_pct = ROUND_NUMBER_PROXIMITY_PCT / 100.0

        for multiple in [100.0, 1000.0, 10_000.0]:
            nearest_round = round(price / multiple) * multiple
            if nearest_round == 0:
                continue
            distance_pct = abs(price - nearest_round) / nearest_round
            if distance_pct <= proximity_pct:
                return True

        return False
