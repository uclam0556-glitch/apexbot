"""
APEX v11.0 — DataValidator
==========================
Institutional-grade data quality and integrity validation framework.

Design Principles:
- Every check is independently scored and logged.
- No signal can bypass the validator. It is the gatekeeper.
- Scores are normalized to [0.0, 1.0], not magic 0-100 ranges.
- All thresholds documented with academic or empirical justification.
- DataHealthStatus is an explicit enum, not a string.

Limitations:
- Cross-exchange validation requires real-time feeds from both Binance and Bybit.
- Bid-ask spread thresholds are TEMP values; require calibration from historical spread data.
- Volume z-score window (20 periods) is TEMP; requires calibration per asset tier.

Author intent: Replace the ad-hoc compute_data_health() function with a rigorous,
testable, versioned validator that produces structured DataQualityReport objects.
"""

from __future__ import annotations

import math
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ─── Asset Tier Classification ────────────────────────────────────────────────
# Justification: Tiering by market cap and liquidity depth follows standard
# institutional practice (cf. Almgren & Chriss, 2001; Gârleanu & Pedersen, 2013).
ASSET_TIERS: dict[str, list[str]] = {
    "TIER_1": ["BTC/USDT", "ETH/USDT"],
    "TIER_2": [
        "BNB/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT", "AVAX/USDT",
        "DOGE/USDT", "DOT/USDT", "LINK/USDT", "MATIC/USDT", "TRX/USDT",
        "TON/USDT", "LTC/USDT", "BCH/USDT", "UNI/USDT", "ATOM/USDT",
        "ICP/USDT", "FIL/USDT", "APT/USDT", "NEAR/USDT", "ARB/USDT",
    ],
}

# Maximum bid-ask spread tolerance by tier.
# TEMP: These thresholds require calibration from 90-day historical spread data.
# Reference: Chordia et al. (2001), "Trading Activity and Expected Stock Returns."
MAX_SPREAD_PCT: dict[str, float] = {
    "TIER_1": 0.05,   # 5 bps
    "TIER_2": 0.15,   # 15 bps
    "TIER_3": 0.40,   # 40 bps
}

# Minimum daily USD volume to qualify for signal generation.
# Justification: Ensures the market impact of a $10k position is < 0.5% of
# average daily volume (100x safety margin on $500k minimum).
MIN_DAILY_USD_VOLUME: float = 500_000.0

# WebSocket data freshness thresholds (seconds).
# TEMP: 2.5x expected interval. For 1s ws heartbeat, grace = 2.5s.
WS_STALE_SOFT_THRESHOLD_S: float = 5.0    # Soft penalty
WS_STALE_HARD_THRESHOLD_S: float = 15.0   # Hard reject

# Volume anomaly detection: flag if volume > mean + N * std.
# Justification: 4-sigma threshold minimizes false positives to ~0.003%
# assuming approximately normal log-volume distribution.
VOLUME_ZSCORE_ANOMALY_THRESHOLD: float = 4.0

# Return sanity check: flag 1h returns > this threshold.
# TEMP: Requires calibration. Set at 15% as extreme-move reference.
MAX_ACCEPTABLE_1H_RETURN_PCT: float = 15.0

# Cross-exchange price discrepancy tolerance.
# TEMP: 0.5% accounts for exchange arbitrage window before bots close gap.
MAX_CROSS_EXCHANGE_SPREAD_PCT: float = 0.5


class DataHealthStatus(Enum):
    """Explicit data quality status enum.
    
    Never use raw strings for status — it prevents typos and enables
    exhaustive pattern matching.
    """
    OK = "OK"                     # All checks pass. Full execution allowed.
    DEGRADED = "DEGRADED"         # Minor issues. Limit orders only.
    DEGRADED_SEVERE = "DEGRADED_SEVERE"  # Significant issues. No execution.
    BAD = "BAD"                   # Critical failure. All execution blocked.


class AssetTier(Enum):
    TIER_1 = "TIER_1"
    TIER_2 = "TIER_2"
    TIER_3 = "TIER_3"


@dataclass
class DataQualityCheck:
    """Result of a single atomic data quality check."""
    name: str
    passed: bool
    penalty: float          # Score deduction in [0.0, 1.0]
    detail: str             # Human-readable detail for audit logs
    is_critical: bool = False  # If True, immediately forces BAD status


@dataclass
class DataQualityReport:
    """
    Structured output of the DataValidator.
    
    All execution decisions MUST be based on this object — never on raw
    float scores from legacy compute_data_health().
    """
    symbol: str
    timestamp: float         # UNIX timestamp of validation
    raw_score: float         # [0.0, 1.0] composite quality score
    status: DataHealthStatus
    tier: AssetTier
    checks: list[DataQualityCheck] = field(default_factory=list)
    block_reasons: list[str] = field(default_factory=list)

    # Execution permissions derived from status
    market_order_allowed: bool = False
    limit_order_allowed: bool = False

    # Legacy compatibility field — DO NOT use for new logic
    legacy_score_0_100: float = 0.0

    def is_tradeable(self) -> bool:
        """Returns True only if limit orders are permitted."""
        return self.limit_order_allowed

    def summary(self) -> str:
        """Single-line summary for logging."""
        reasons = "; ".join(self.block_reasons) if self.block_reasons else "none"
        return (
            f"[{self.symbol}] Quality={self.raw_score:.3f} "
            f"Status={self.status.value} Tier={self.tier.value} "
            f"BlockReasons=[{reasons}]"
        )


class DataValidator:
    """
    Institutional-grade data quality validator.
    
    Replaces the legacy compute_data_health() function with a fully
    structured, testable, and versioned validation pipeline.
    
    Usage:
        validator = DataValidator()
        report = validator.validate(symbol="BTC/USDT", ...)
        if not report.is_tradeable():
            return  # Do not process signal
    
    Version: 11.0.0
    """

    def __init__(self, startup_grace_seconds: float = 60.0) -> None:
        """
        Args:
            startup_grace_seconds: During system startup, WS missing data
                receives reduced penalty. Prevents false blocks on boot.
        """
        self._start_time: float = time.time()
        self._startup_grace: float = startup_grace_seconds

    @staticmethod
    def get_asset_tier(symbol: str) -> AssetTier:
        """Classify asset into liquidity tier."""
        if symbol in ASSET_TIERS["TIER_1"]:
            return AssetTier.TIER_1
        if symbol in ASSET_TIERS["TIER_2"]:
            return AssetTier.TIER_2
        return AssetTier.TIER_3

    def validate(
        self,
        symbol: str,
        last_ws_update: Optional[float],
        ohlcv_volume_series: Optional[np.ndarray],  # Last 20 bars of volume
        current_volume: float,
        daily_volume_usd: float,
        funding_rate: Optional[float],
        market_type: str = "SPOT",
        bid_price: Optional[float] = None,
        ask_price: Optional[float] = None,
        secondary_exchange_price: Optional[float] = None,
        current_price: Optional[float] = None,
        prev_hour_price: Optional[float] = None,
    ) -> DataQualityReport:
        """
        Run all data quality checks and return a structured report.
        
        Args:
            symbol: Trading pair, e.g. "BTC/USDT".
            last_ws_update: UNIX timestamp of last WebSocket tick. None if
                WebSocket has never received data for this symbol.
            ohlcv_volume_series: Numpy array of last N bar volumes for z-score
                anomaly detection. None if historical data unavailable.
            current_volume: Current bar volume (native units).
            daily_volume_usd: Estimated 24h USD volume.
            funding_rate: Perpetual funding rate. None for spot markets.
            market_type: "SPOT" or "PERP".
            bid_price: Best bid from order book. None if unavailable.
            ask_price: Best ask from order book. None if unavailable.
            secondary_exchange_price: Price from secondary exchange for
                cross-validation (e.g. Bybit if primary is Binance).
            current_price: Mid-market price. Used for spread calculation.
            prev_hour_price: Price 1 hour ago. Used for return sanity check.
        
        Returns:
            DataQualityReport with all check results and execution permissions.
        """
        now = time.time()
        tier = self.get_asset_tier(symbol)
        checks: list[DataQualityCheck] = []

        # ── Check 1: WebSocket Freshness ────────────────────────────────────
        ws_check = self._check_ws_freshness(symbol, last_ws_update, now)
        checks.append(ws_check)

        # ── Check 2: Daily USD Volume (Critical Gate) ────────────────────────
        vol_usd_check = self._check_daily_volume_usd(symbol, daily_volume_usd)
        checks.append(vol_usd_check)

        # ── Check 3: Volume Z-Score Anomaly ─────────────────────────────────
        if ohlcv_volume_series is not None and len(ohlcv_volume_series) >= 10:
            zscore_check = self._check_volume_zscore(
                symbol, current_volume, ohlcv_volume_series
            )
            checks.append(zscore_check)

        # ── Check 4: OHLCV Integrity (from current bar data) ─────────────────
        # Note: Full OHLCV integrity check requires open/high/low/close.
        # Here we perform the volume >= 0 check.
        if current_volume < 0:
            checks.append(DataQualityCheck(
                name="volume_non_negative",
                passed=False,
                penalty=0.5,
                detail=f"Negative volume detected: {current_volume}",
                is_critical=True,
            ))
        else:
            checks.append(DataQualityCheck(
                name="volume_non_negative",
                passed=True,
                penalty=0.0,
                detail="Volume >= 0: OK",
            ))

        # ── Check 5: Bid-Ask Spread ──────────────────────────────────────────
        if bid_price is not None and ask_price is not None and current_price:
            spread_check = self._check_bid_ask_spread(
                symbol, bid_price, ask_price, current_price, tier
            )
            checks.append(spread_check)

        # ── Check 6: Cross-Exchange Price Sanity ─────────────────────────────
        if secondary_exchange_price is not None and current_price:
            cross_check = self._check_cross_exchange_spread(
                symbol, current_price, secondary_exchange_price
            )
            checks.append(cross_check)

        # ── Check 7: Return Sanity Check ─────────────────────────────────────
        if prev_hour_price is not None and current_price and prev_hour_price > 0:
            return_check = self._check_return_sanity(
                symbol, current_price, prev_hour_price
            )
            checks.append(return_check)

        # ── Check 8: Funding Rate Validity (Perp only) ───────────────────────
        if market_type == "PERP":
            funding_check = self._check_funding_rate(symbol, funding_rate)
            checks.append(funding_check)

        # ── Aggregate Score ──────────────────────────────────────────────────
        raw_score, block_reasons, has_critical_fail = self._aggregate(checks)

        # ── Derive Status ────────────────────────────────────────────────────
        status = self._derive_status(raw_score, has_critical_fail)

        # ── Derive Execution Permissions ─────────────────────────────────────
        market_allowed = status == DataHealthStatus.OK
        limit_allowed = status in (DataHealthStatus.OK, DataHealthStatus.DEGRADED)

        report = DataQualityReport(
            symbol=symbol,
            timestamp=now,
            raw_score=raw_score,
            status=status,
            tier=tier,
            checks=checks,
            block_reasons=block_reasons,
            market_order_allowed=market_allowed,
            limit_order_allowed=limit_allowed,
            legacy_score_0_100=raw_score * 100.0,  # Backward compatibility only
        )

        if not report.is_tradeable():
            logger.warning(
                "[DataValidator] %s BLOCKED. %s", symbol, report.summary()
            )
        else:
            logger.debug("[DataValidator] %s %s", symbol, report.summary())

        return report

    # ─── Individual Check Methods ─────────────────────────────────────────────

    def _check_ws_freshness(
        self, symbol: str, last_ws_update: Optional[float], now: float
    ) -> DataQualityCheck:
        """Check WebSocket data staleness."""
        if last_ws_update is None:
            uptime = now - self._start_time
            if uptime < self._startup_grace:
                return DataQualityCheck(
                    name="ws_freshness",
                    passed=True,
                    penalty=0.0,
                    detail=f"WS missing but within {self._startup_grace:.0f}s startup grace.",
                )
            return DataQualityCheck(
                name="ws_freshness",
                passed=False,
                penalty=0.50,
                detail="WS feed never received data after startup grace period.",
                is_critical=True,
            )

        delay = now - last_ws_update
        if delay > WS_STALE_HARD_THRESHOLD_S:
            return DataQualityCheck(
                name="ws_freshness",
                passed=False,
                penalty=0.30,
                detail=f"WS stale: {delay:.1f}s > hard threshold {WS_STALE_HARD_THRESHOLD_S}s.",
            )
        if delay > WS_STALE_SOFT_THRESHOLD_S:
            return DataQualityCheck(
                name="ws_freshness",
                passed=True,
                penalty=0.10,
                detail=f"WS slightly delayed: {delay:.1f}s > soft threshold {WS_STALE_SOFT_THRESHOLD_S}s.",
            )
        return DataQualityCheck(
            name="ws_freshness",
            passed=True,
            penalty=0.0,
            detail=f"WS fresh: delay={delay:.2f}s.",
        )

    def _check_daily_volume_usd(
        self, symbol: str, daily_volume_usd: float
    ) -> DataQualityCheck:
        """Critical gate: reject assets below minimum USD liquidity threshold."""
        if daily_volume_usd < MIN_DAILY_USD_VOLUME:
            return DataQualityCheck(
                name="daily_volume_usd",
                passed=False,
                penalty=1.0,  # Full score wipe — immediate BAD
                detail=(
                    f"Daily USD volume ${daily_volume_usd:,.0f} "
                    f"< minimum ${MIN_DAILY_USD_VOLUME:,.0f}."
                ),
                is_critical=True,
            )
        return DataQualityCheck(
            name="daily_volume_usd",
            passed=True,
            penalty=0.0,
            detail=f"Daily USD volume ${daily_volume_usd:,.0f}: OK.",
        )

    def _check_volume_zscore(
        self, symbol: str, current_volume: float, volume_series: np.ndarray
    ) -> DataQualityCheck:
        """
        Flag anomalous volume spikes using z-score.
        
        Justification: Volume > mean + 4σ occurs with probability ~0.003%
        under normality, suggesting data corruption or exchange anomaly.
        Not a block — only a flag (penalty 0.10) as extreme volume can
        legitimately occur during news events.
        """
        if current_volume <= 0 or len(volume_series) < 5:
            return DataQualityCheck(
                name="volume_zscore",
                passed=True,
                penalty=0.0,
                detail="Insufficient history for z-score (< 5 bars). Skipped.",
            )

        mean_vol = np.mean(volume_series)
        std_vol = np.std(volume_series, ddof=1)

        if std_vol < 1e-10:  # Avoid division by zero on flat series
            return DataQualityCheck(
                name="volume_zscore",
                passed=True,
                penalty=0.0,
                detail="Volume std ≈ 0 — series appears flat. Z-score not meaningful.",
            )

        zscore = (current_volume - mean_vol) / std_vol

        if zscore > VOLUME_ZSCORE_ANOMALY_THRESHOLD:
            return DataQualityCheck(
                name="volume_zscore",
                passed=True,  # Not a hard block — only a flag
                penalty=0.10,
                detail=(
                    f"Volume z-score={zscore:.1f} > {VOLUME_ZSCORE_ANOMALY_THRESHOLD}σ. "
                    f"Possible data anomaly or news event. Minor penalty applied."
                ),
            )

        return DataQualityCheck(
            name="volume_zscore",
            passed=True,
            penalty=0.0,
            detail=f"Volume z-score={zscore:.2f}: normal range.",
        )

    def _check_bid_ask_spread(
        self,
        symbol: str,
        bid: float,
        ask: float,
        mid: float,
        tier: AssetTier,
    ) -> DataQualityCheck:
        """Check bid-ask spread against tier-specific thresholds."""
        if mid <= 0 or ask <= 0 or bid < 0:
            return DataQualityCheck(
                name="bid_ask_spread",
                passed=False,
                penalty=0.20,
                detail=f"Invalid bid/ask/mid prices: bid={bid}, ask={ask}, mid={mid}.",
            )

        spread_pct = (ask - bid) / mid * 100.0
        max_spread = MAX_SPREAD_PCT.get(tier.value, MAX_SPREAD_PCT["TIER_3"])

        if spread_pct > max_spread:
            penalty = 0.30 if tier == AssetTier.TIER_1 else 0.15
            return DataQualityCheck(
                name="bid_ask_spread",
                passed=False,
                penalty=penalty,
                detail=(
                    f"Spread {spread_pct:.3f}% > {tier.value} max {max_spread:.2f}%. "
                    f"Execution costs unacceptably high."
                ),
            )

        return DataQualityCheck(
            name="bid_ask_spread",
            passed=True,
            penalty=0.0,
            detail=f"Spread {spread_pct:.4f}% within {tier.value} limit {max_spread:.2f}%.",
        )

    def _check_cross_exchange_spread(
        self, symbol: str, primary_price: float, secondary_price: float
    ) -> DataQualityCheck:
        """Flag if primary and secondary exchange prices diverge significantly."""
        if primary_price <= 0 or secondary_price <= 0:
            return DataQualityCheck(
                name="cross_exchange_spread",
                passed=True,
                penalty=0.0,
                detail="One or both exchange prices invalid. Check skipped.",
            )

        divergence_pct = abs(primary_price - secondary_price) / primary_price * 100.0

        if divergence_pct > MAX_CROSS_EXCHANGE_SPREAD_PCT:
            return DataQualityCheck(
                name="cross_exchange_spread",
                passed=False,
                penalty=0.20,
                detail=(
                    f"Cross-exchange price divergence {divergence_pct:.3f}% "
                    f"> {MAX_CROSS_EXCHANGE_SPREAD_PCT:.1f}%. "
                    f"Possible data feed issue or extreme arbitrage event."
                ),
            )

        return DataQualityCheck(
            name="cross_exchange_spread",
            passed=True,
            penalty=0.0,
            detail=f"Cross-exchange spread {divergence_pct:.4f}%: OK.",
        )

    def _check_return_sanity(
        self, symbol: str, current_price: float, prev_hour_price: float
    ) -> DataQualityCheck:
        """Flag extreme 1h returns that may indicate bad data."""
        return_pct = abs(current_price - prev_hour_price) / prev_hour_price * 100.0

        if return_pct > MAX_ACCEPTABLE_1H_RETURN_PCT:
            return DataQualityCheck(
                name="return_sanity",
                passed=True,  # Flag only — extreme moves can be real
                penalty=0.10,
                detail=(
                    f"1h return {return_pct:.1f}% > {MAX_ACCEPTABLE_1H_RETURN_PCT:.0f}%. "
                    f"Requires multi-source confirmation. Minor penalty applied."
                ),
            )

        return DataQualityCheck(
            name="return_sanity",
            passed=True,
            penalty=0.0,
            detail=f"1h return {return_pct:.2f}%: within normal range.",
        )

    def _check_funding_rate(
        self, symbol: str, funding_rate: Optional[float]
    ) -> DataQualityCheck:
        """Validate perpetual funding rate availability."""
        if funding_rate is None or (funding_rate == 0.0 and symbol not in ["BTC/USDT", "ETH/USDT"]):
            return DataQualityCheck(
                name="funding_rate",
                passed=False,
                penalty=0.20,
                detail="Funding rate missing or exactly 0.0 for non-major perp. Likely data feed issue.",
            )
        return DataQualityCheck(
            name="funding_rate",
            passed=True,
            penalty=0.0,
            detail=f"Funding rate={funding_rate:.6f}: valid.",
        )

    # ─── Aggregation Logic ────────────────────────────────────────────────────

    def _aggregate(
        self, checks: list[DataQualityCheck]
    ) -> tuple[float, list[str], bool]:
        """
        Aggregate individual check results into a composite score.
        
        Returns:
            (raw_score [0,1], block_reasons, has_critical_failure)
        """
        total_penalty = sum(c.penalty for c in checks)
        has_critical = any(c.is_critical and not c.passed for c in checks)
        block_reasons = [c.detail for c in checks if not c.passed]

        # Clamp to [0, 1]
        raw_score = max(0.0, 1.0 - total_penalty)

        # Critical failure forces score to 0 regardless of other checks
        if has_critical:
            raw_score = 0.0

        return raw_score, block_reasons, has_critical

    def _derive_status(
        self, raw_score: float, has_critical: bool
    ) -> DataHealthStatus:
        """Map composite score to DataHealthStatus enum."""
        if has_critical or raw_score <= 0.0:
            return DataHealthStatus.BAD
        if raw_score >= 0.90:
            return DataHealthStatus.OK
        if raw_score >= 0.75:
            return DataHealthStatus.DEGRADED
        return DataHealthStatus.DEGRADED_SEVERE


# ─── Backward Compatibility Shim ─────────────────────────────────────────────
# This function exists ONLY to maintain compatibility with legacy main.py calls
# during the v10.5 → v11.0 migration. It MUST be removed after full migration.
# DO NOT use in new code.

_default_validator = DataValidator()


def compute_data_health(
    symbol: str,
    last_ws_update: Optional[float],
    avg_vol_3: float,
    baseline_hourly_vol: float,
    funding_rate: Optional[float],
    daily_volume_usd: float = 0.0,
    market_type: str = "SPOT",
) -> dict:
    """
    DEPRECATED: Legacy compatibility wrapper.
    
    This function will be removed in APEX v11.1. Use DataValidator.validate()
    directly in all new code.
    """
    report = _default_validator.validate(
        symbol=symbol,
        last_ws_update=last_ws_update,
        ohlcv_volume_series=None,
        current_volume=avg_vol_3,
        daily_volume_usd=daily_volume_usd,
        funding_rate=funding_rate,
        market_type=market_type,
    )

    return {
        "score": report.legacy_score_0_100,
        "status": report.status.value,
        "reasons": report.block_reasons,
        "market_allowed": report.market_order_allowed,
        "limit_allowed": report.limit_order_allowed,
        "market_disabled": not report.market_order_allowed,
        # v11 extensions
        "report": report,
    }
