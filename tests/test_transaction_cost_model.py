"""
APEX v11.0 — Unit Tests: TransactionCostModel
===============================================
Tests for institutional transaction cost estimation.

Run:
    pytest tests/test_transaction_cost_model.py -v
"""

import pytest
from services.execution.transaction_cost_model import (
    TransactionCostModel,
    OrderUrgency,
    EXCHANGE_FEES,
)


@pytest.fixture
def model():
    return TransactionCostModel(exchange="BINANCE", order_type="taker")


class TestCommissionCost:
    def test_commission_matches_fee_schedule(self, model):
        estimate = model.estimate(
            symbol="BTC/USDT",
            position_size_usd=10_000.0,
            current_price=60_000.0,
            realized_vol_daily=0.03,
            adv_usd=2_000_000_000.0,
        )
        expected_commission = EXCHANGE_FEES["BINANCE"]["taker"]
        assert estimate.commission_pct == expected_commission

    def test_round_trip_includes_two_commissions(self, model):
        estimate = model.estimate(
            symbol="BTC/USDT",
            position_size_usd=10_000.0,
            current_price=60_000.0,
            realized_vol_daily=0.03,
            adv_usd=2_000_000_000.0,
        )
        min_round_trip = 2 * estimate.commission_pct
        assert estimate.round_trip_cost_pct >= min_round_trip


class TestMarketImpact:
    def test_larger_order_has_higher_impact(self, model):
        small = model.estimate(
            "SOL/USDT", 1_000.0, 100.0, 0.05, 100_000_000.0
        )
        large = model.estimate(
            "SOL/USDT", 50_000.0, 100.0, 0.05, 100_000_000.0
        )
        assert large.market_impact_pct > small.market_impact_pct

    def test_higher_vol_increases_impact(self, model):
        low_vol = model.estimate(
            "SOL/USDT", 10_000.0, 100.0, 0.01, 100_000_000.0
        )
        high_vol = model.estimate(
            "SOL/USDT", 10_000.0, 100.0, 0.10, 100_000_000.0
        )
        assert high_vol.market_impact_pct > low_vol.market_impact_pct

    def test_low_adv_returns_high_impact(self, model):
        """Assets with ADV below minimum should receive extreme impact penalty."""
        estimate = model.estimate(
            "LOWLIQ/USDT", 10_000.0, 1.0, 0.05, 100_000.0  # ADV < minimum
        )
        assert estimate.market_impact_pct >= 1.0  # Must be extreme


class TestNetEdgeCalculation:
    def test_positive_net_edge_approved(self, model):
        estimate = model.estimate(
            "BTC/USDT", 10_000.0, 60_000.0, 0.02, 2_000_000_000.0,
            gross_edge_pct=0.5,  # 0.5% gross edge
        )
        assert estimate.net_edge_pct is not None
        assert estimate.net_edge_positive is True

    def test_negative_net_edge_rejected(self, model):
        """Signal with gross edge < costs must be rejected."""
        estimate = model.estimate(
            "BTC/USDT", 10_000.0, 60_000.0, 0.02, 2_000_000_000.0,
            gross_edge_pct=0.001,  # 0.001% gross edge — far less than ~0.04% round-trip costs
        )
        assert estimate.net_edge_pct is not None
        assert estimate.net_edge_positive is False
        assert estimate.execution_strategy == "REJECT"

    def test_none_gross_edge_skips_net_calculation(self, model):
        estimate = model.estimate(
            "BTC/USDT", 10_000.0, 60_000.0, 0.02, 2_000_000_000.0,
        )
        assert estimate.net_edge_pct is None
        assert estimate.net_edge_positive is None


class TestFundingCost:
    def test_spot_has_no_funding_cost(self, model):
        estimate = model.estimate(
            "BTC/USDT", 10_000.0, 60_000.0, 0.02, 2_000_000_000.0,
            funding_rate=0.0001, market_type="SPOT",
        )
        assert estimate.funding_daily_pct == 0.0

    def test_perp_includes_funding_cost(self, model):
        estimate = model.estimate(
            "BTC/USDT", 10_000.0, 60_000.0, 0.02, 2_000_000_000.0,
            funding_rate=0.0001, market_type="PERP",
        )
        assert estimate.funding_daily_pct > 0.0


class TestBreakevenWinRate:
    def test_breakeven_win_rate_computed(self, model):
        estimate = model.estimate(
            "SOL/USDT", 5_000.0, 100.0, 0.04, 500_000_000.0,
            gross_edge_pct=0.3,
            sl_distance_pct=0.15,
        )
        assert estimate.min_required_win_rate is not None
        assert 0.0 < estimate.min_required_win_rate < 1.0

    def test_rr_after_costs_positive_for_viable_trade(self, model):
        estimate = model.estimate(
            "SOL/USDT", 5_000.0, 100.0, 0.04, 500_000_000.0,
            gross_edge_pct=0.5,
            sl_distance_pct=0.2,
        )
        if estimate.rr_ratio_after_costs is not None:
            assert estimate.rr_ratio_after_costs > 0.0


class TestExecutionStrategy:
    def test_normal_order_recommends_limit(self, model):
        estimate = model.estimate(
            "BTC/USDT", 5_000.0, 60_000.0, 0.02, 2_000_000_000.0,
            gross_edge_pct=0.5,
        )
        assert estimate.execution_strategy in ("LIMIT", "LIMIT_AGGRESSIVE")

    def test_invalid_exchange_raises(self):
        with pytest.raises(ValueError, match="Unknown exchange"):
            TransactionCostModel(exchange="INVALID_EXCHANGE")
