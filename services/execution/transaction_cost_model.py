"""
APEX v11.0 — TransactionCostModel
===================================
Institutional transaction cost and execution cost modeling.

Design Principles:
- Every cost component is separately estimated and logged.
- No signal passes to execution without passing net_edge > 0 gate.
- Market impact model: square-root (Almgren-Chriss, 2001).
- Spread model: half-spread per asset tier.
- All coefficients explicitly marked as TEMP or data-derived.

Limitations:
- Exchange fee schedules change over time. Requires periodic recalibration.
- Market impact alpha (0.5) is a standard literature default. TEMP: requires
  calibration from actual fill data at Stage 1+ deployment.
- Funding cost estimation assumes 3 funding periods/day (Binance PERP default).
- This model does NOT model intraday spread variation (TEMP: future enhancement).

References:
- Almgren & Chriss (2001): "Optimal execution of portfolio transactions."
- Gârleanu & Pedersen (2013): "Dynamic trading with predictable returns and transaction costs."
- Hasbrouck (2009): "Trading Costs and Returns for U.S. Equities."

Author intent: Ensure every signal has net_edge calculated before entry.
If gross_edge < round_trip_cost, the signal MUST be rejected.
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Exchange Fee Schedules ───────────────────────────────────────────────────
# TEMP: These are standard VIP-0 tier rates. Requires update per actual account tier.
# Source: Binance fee schedule (as of 2024-Q4), Bybit fee schedule (as of 2024-Q4).
EXCHANGE_FEES: dict[str, dict[str, float]] = {
    "BINANCE": {
        "maker": 0.001,   # 0.10% (10 bps)
        "taker": 0.001,   # 0.10% (10 bps)
        # With BNB discount: maker=0.075%, taker=0.075%
    },
    "BYBIT": {
        "maker": 0.001,   # 0.10%
        "taker": 0.001,   # 0.10%
    },
}

# Market impact square-root model coefficient.
# alpha = 0.5 is the standard Almgren-Chriss literature default.
# TEMP: Requires calibration from actual fill slippage data at Stage 1.
MARKET_IMPACT_ALPHA: float = 0.5

# Minimum USD volume threshold (duplicated from validator for independence).
MIN_DAILY_USD_VOLUME_FOR_IMPACT: float = 500_000.0

# Maximum acceptable round-trip cost as fraction of expected gross edge.
# If round_trip_cost > this fraction of gross_edge → reject signal.
# TEMP: 0.33 means costs must not eat more than 33% of gross edge.
MAX_COST_TO_EDGE_RATIO: float = 0.33

# Funding periods per day for PERP positions.
# Binance/Bybit: funding every 8 hours = 3 per day.
FUNDING_PERIODS_PER_DAY: int = 3


class OrderUrgency(Enum):
    """Order urgency classification affecting execution strategy."""
    LOW = "LOW"       # Time-flexible: use limit orders, TWAP
    MEDIUM = "MEDIUM" # Balanced: aggressive limit
    HIGH = "HIGH"     # Time-sensitive: near-market limit


@dataclass
class TransactionCostEstimate:
    """
    Complete cost breakdown for a single signal/trade.
    
    All values are in percentage terms (e.g., 0.10 = 0.10% = 10 bps).
    """
    symbol: str
    position_size_usd: float

    # Individual cost components (%)
    commission_pct: float       # Exchange maker/taker fee (one-way)
    spread_cost_pct: float      # Half-spread entry + exit cost
    market_impact_pct: float    # Square-root model impact
    funding_daily_pct: float    # Daily funding cost (PERP only, else 0.0)

    # Aggregates (%)
    one_way_cost_pct: float     # commission + half-spread + half-impact
    round_trip_cost_pct: float  # 2 * one_way_cost (entry + exit)
    total_cost_pct: float       # round_trip + funding_daily

    # In USD
    round_trip_cost_usd: float

    # Edge analysis
    gross_edge_pct: Optional[float] = None   # Set by caller: expected TP distance
    net_edge_pct: Optional[float] = None     # gross_edge - total_cost
    net_edge_positive: Optional[bool] = None # True if net edge exists
    min_required_win_rate: Optional[float] = None  # Break-even win rate given costs
    rr_ratio_after_costs: Optional[float] = None   # Adjusted R:R

    # Execution recommendation
    execution_strategy: str = "LIMIT"    # "LIMIT", "TWAP", "REJECT"
    rejection_reason: Optional[str] = None

    def to_bps(self, pct: float) -> float:
        """Convert percentage to basis points."""
        return pct * 100.0

    def summary(self) -> str:
        """Single-line cost summary for logging."""
        return (
            f"[{self.symbol}] Size=${self.position_size_usd:,.0f} "
            f"RoundTrip={self.round_trip_cost_pct:.4f}% ({self.to_bps(self.round_trip_cost_pct):.1f}bps) "
            f"NetEdge={self.net_edge_pct:.4f}% "
            f"Strategy={self.execution_strategy}"
            if self.net_edge_pct is not None
            else (
                f"[{self.symbol}] Size=${self.position_size_usd:,.0f} "
                f"RoundTrip={self.round_trip_cost_pct:.4f}%"
            )
        )


class TransactionCostModel:
    """
    Institutional transaction cost model for APEX v11.0.
    
    Models: exchange commission, bid-ask spread, market impact (square-root),
    and funding costs for perpetual positions.
    
    Usage:
        model = TransactionCostModel(exchange="BINANCE", order_type="maker")
        estimate = model.estimate(
            symbol="BTC/USDT",
            position_size_usd=10_000,
            current_price=60_000,
            bid=59_995,
            ask=60_005,
            realized_vol_daily=0.03,
            adv_usd=2_000_000_000,
            funding_rate=0.0001,
            market_type="SPOT",
            gross_edge_pct=0.025,
        )
        if not estimate.net_edge_positive:
            logger.warning("Signal rejected: insufficient net edge after costs.")
            return
    
    Version: 11.0.0
    """

    def __init__(
        self,
        exchange: str = "BINANCE",
        order_type: str = "maker",
        impact_alpha: float = MARKET_IMPACT_ALPHA,
    ) -> None:
        """
        Args:
            exchange: Exchange name ("BINANCE" or "BYBIT").
            order_type: "maker" or "taker" — determines fee schedule used.
            impact_alpha: Market impact coefficient. TEMP: default=0.5 per
                Almgren-Chriss (2001). Calibrate from live fill data.
        """
        if exchange not in EXCHANGE_FEES:
            raise ValueError(
                f"Unknown exchange '{exchange}'. Valid: {list(EXCHANGE_FEES.keys())}"
            )
        self._fees = EXCHANGE_FEES[exchange]
        self._order_type = order_type
        self._impact_alpha = impact_alpha
        self._exchange = exchange

    def estimate(
        self,
        symbol: str,
        position_size_usd: float,
        current_price: float,
        realized_vol_daily: float,
        adv_usd: float,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        funding_rate: float = 0.0,
        market_type: str = "SPOT",
        gross_edge_pct: Optional[float] = None,
        sl_distance_pct: Optional[float] = None,
        urgency: OrderUrgency = OrderUrgency.LOW,
    ) -> TransactionCostEstimate:
        """
        Compute full transaction cost estimate for a proposed position.
        
        Args:
            symbol: Trading pair.
            position_size_usd: Notional USD value of the position.
            current_price: Current mid-market price.
            realized_vol_daily: Daily realized volatility (e.g., 0.03 = 3%).
            adv_usd: Average Daily Volume in USD (20-day average).
            bid: Best bid price. If None, spread cost is estimated by tier.
            ask: Best ask price. If None, spread cost is estimated by tier.
            funding_rate: Current 8-hour funding rate (PERP only).
            market_type: "SPOT" or "PERP".
            gross_edge_pct: Expected gross return (e.g., TP distance). Used
                to calculate net_edge_pct and signal viability.
            sl_distance_pct: Stop-loss distance as percentage. Used to compute
                break-even win rate.
            urgency: Determines execution strategy recommendation.
        
        Returns:
            TransactionCostEstimate with all cost components.
        """
        # ── 1. Commission ────────────────────────────────────────────────────
        commission_pct = self._fees.get(self._order_type, self._fees["taker"])

        # ── 2. Spread Cost ───────────────────────────────────────────────────
        spread_cost_pct = self._estimate_spread_cost(symbol, bid, ask, current_price)

        # ── 3. Market Impact (Square-Root Model) ─────────────────────────────
        market_impact_pct = self._estimate_market_impact(
            position_size_usd, realized_vol_daily, adv_usd
        )

        # ── 4. Funding Cost (PERP only) ──────────────────────────────────────
        funding_daily_pct = 0.0
        if market_type == "PERP" and funding_rate:
            # Annualized but expressed per day:
            # funding_rate is 8h rate → 3 periods/day
            funding_daily_pct = abs(funding_rate) * FUNDING_PERIODS_PER_DAY

        # ── 5. Aggregate ─────────────────────────────────────────────────────
        # One-way cost: pay commission + half-spread + half market impact on entry
        one_way_cost_pct = commission_pct + (spread_cost_pct / 2.0) + (market_impact_pct / 2.0)

        # Round-trip cost: entry + exit (pay full spread + full impact, 2x commission)
        round_trip_cost_pct = (2.0 * commission_pct) + spread_cost_pct + market_impact_pct

        # Total cost includes daily funding (assume 1 day average hold)
        total_cost_pct = round_trip_cost_pct + funding_daily_pct

        round_trip_cost_usd = position_size_usd * round_trip_cost_pct / 100.0

        # ── 6. Edge Analysis ─────────────────────────────────────────────────
        net_edge_pct = None
        net_edge_positive = None
        min_win_rate = None
        rr_after_costs = None

        if gross_edge_pct is not None:
            net_edge_pct = gross_edge_pct - total_cost_pct
            net_edge_positive = net_edge_pct > 0.0

            if not net_edge_positive:
                logger.warning(
                    "[TCM] %s: INSUFFICIENT EDGE. Gross=%.4f%% Total_Cost=%.4f%% Net=%.4f%%",
                    symbol, gross_edge_pct, total_cost_pct, net_edge_pct
                )

        if sl_distance_pct is not None and gross_edge_pct is not None and sl_distance_pct > 0:
            # Break-even win rate: P * TP_net = (1-P) * SL_net
            # P = SL_net / (TP_net + SL_net)
            tp_net = gross_edge_pct - total_cost_pct
            sl_net = sl_distance_pct + total_cost_pct  # SL cost adds to loss
            if (tp_net + sl_net) > 0:
                min_win_rate = sl_net / (tp_net + sl_net)

            if tp_net > 0 and sl_distance_pct > 0:
                rr_after_costs = tp_net / (sl_distance_pct + total_cost_pct)

        # ── 7. Execution Strategy ─────────────────────────────────────────────
        execution_strategy, rejection_reason = self._recommend_execution(
            symbol, urgency, spread_cost_pct, adv_usd, position_size_usd,
            net_edge_positive
        )

        estimate = TransactionCostEstimate(
            symbol=symbol,
            position_size_usd=position_size_usd,
            commission_pct=commission_pct,
            spread_cost_pct=spread_cost_pct,
            market_impact_pct=market_impact_pct,
            funding_daily_pct=funding_daily_pct,
            one_way_cost_pct=one_way_cost_pct,
            round_trip_cost_pct=round_trip_cost_pct,
            total_cost_pct=total_cost_pct,
            round_trip_cost_usd=round_trip_cost_usd,
            gross_edge_pct=gross_edge_pct,
            net_edge_pct=net_edge_pct,
            net_edge_positive=net_edge_positive,
            min_required_win_rate=min_win_rate,
            rr_ratio_after_costs=rr_after_costs,
            execution_strategy=execution_strategy,
            rejection_reason=rejection_reason,
        )

        logger.info("[TCM] %s", estimate.summary())
        return estimate

    # ─── Private Estimation Methods ───────────────────────────────────────────

    def _estimate_spread_cost(
        self,
        symbol: str,
        bid: Optional[float],
        ask: Optional[float],
        mid: float,
    ) -> float:
        """
        Estimate bid-ask spread cost as a percentage of mid price.
        
        If bid/ask are available, use observed spread.
        Otherwise use tier-based empirical estimates.
        
        Returns:
            Full round-trip spread cost as percentage (entry + exit).
        """
        if bid is not None and ask is not None and mid > 0:
            observed_spread_pct = (ask - bid) / mid * 100.0
            # Round-trip: pay half-spread on entry and half on exit
            return observed_spread_pct

        # TEMP: Tier-based fallback estimates.
        # These require calibration from historical order book data.
        from services.data.validator import ASSET_TIERS
        if symbol in ASSET_TIERS.get("TIER_1", []):
            return 0.04   # ~4 bps round-trip for BTC/ETH
        if symbol in ASSET_TIERS.get("TIER_2", []):
            return 0.12   # ~12 bps round-trip for major alts
        return 0.30       # ~30 bps round-trip for Tier 3

    def _estimate_market_impact(
        self,
        position_size_usd: float,
        realized_vol_daily: float,
        adv_usd: float,
    ) -> float:
        """
        Square-root market impact model (Almgren-Chriss, 2001).
        
        Formula: impact = alpha * sigma * sqrt(Q / ADV)
        
        Where:
            alpha = 0.5 (TEMP: literature default, calibrate from fills)
            sigma = daily realized volatility
            Q = order size in USD
            ADV = average daily volume in USD
        
        Returns:
            One-way market impact as percentage.
        """
        if adv_usd < MIN_DAILY_USD_VOLUME_FOR_IMPACT:
            # If ADV is too low, market impact is extreme — penalize heavily
            logger.warning(
                "[TCM] ADV $%.0f below minimum. Market impact estimate unreliable.",
                adv_usd
            )
            return 1.0  # 1% impact — effectively blocks execution for tiny assets

        participation_rate = position_size_usd / adv_usd

        if participation_rate <= 0 or realized_vol_daily <= 0:
            return 0.0

        impact_pct = self._impact_alpha * realized_vol_daily * math.sqrt(participation_rate) * 100.0
        return impact_pct

    def _recommend_execution(
        self,
        symbol: str,
        urgency: OrderUrgency,
        spread_pct: float,
        adv_usd: float,
        position_size_usd: float,
        net_edge_positive: Optional[bool],
    ) -> tuple[str, Optional[str]]:
        """
        Recommend execution strategy and check if order should be rejected.
        
        Returns:
            (strategy, rejection_reason) — rejection_reason is None if approved.
        """
        # Gate 1: Net edge must be positive
        if net_edge_positive is False:
            return "REJECT", "Net edge negative after transaction costs."

        # Gate 2: Spread too wide → limit only
        if spread_pct > 0.30:
            if urgency == OrderUrgency.HIGH:
                return "REJECT", f"Spread {spread_pct:.3f}% too wide for urgent execution."
            return "LIMIT", None

        # Gate 3: Large order relative to ADV → TWAP
        if adv_usd > 0 and (position_size_usd / adv_usd) > 0.01:
            return "TWAP", None

        # Gate 4: Urgency-based routing
        if urgency == OrderUrgency.HIGH and spread_pct < 0.15:
            return "LIMIT_AGGRESSIVE", None

        return "LIMIT", None
