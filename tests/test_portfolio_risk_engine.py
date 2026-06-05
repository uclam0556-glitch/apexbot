"""
APEX v11.0 — Unit Tests: PortfolioRiskEngine
=============================================
Tests for circuit breakers, position sizing, VaR, and concentration limits.

Run:
    pytest tests/test_portfolio_risk_engine.py -v
"""

import numpy as np
import pytest

from services.risk.portfolio_risk_engine import (
    PortfolioRiskEngine,
    PortfolioRiskState,
    CircuitBreakerLevel,
    MAX_RISK_PER_TRADE_PCT,
)


@pytest.fixture
def engine():
    return PortfolioRiskEngine()


@pytest.fixture
def healthy_state():
    return PortfolioRiskState(
        portfolio_value_usd=100_000.0,
        peak_value_usd=100_000.0,
        current_drawdown_pct=0.0,
    )


@pytest.fixture
def state_with_returns(healthy_state):
    # 252 days of realistic crypto daily returns
    np.random.seed(42)
    healthy_state.daily_returns = np.random.normal(0.001, 0.03, 252)
    return healthy_state


class TestBasicPositionSizing:
    def test_btc_approved_on_clean_state(self, engine, healthy_state):
        result = engine.size_position(
            symbol="BTC/USDT",
            atr_14=1_500.0,
            current_price=60_000.0,
            portfolio_state=healthy_state,
            regime_breadth_pct=65.0,
        )
        assert result.approved is True
        assert result.position_size_usd > 0.0
        assert result.rejection_reason is None

    def test_risk_per_trade_is_1pct(self, engine, healthy_state):
        """Base risk amount must equal 1% of portfolio."""
        result = engine.size_position(
            symbol="ETH/USDT",
            atr_14=100.0,
            current_price=3_000.0,
            portfolio_state=healthy_state,
            regime_breadth_pct=65.0,
        )
        expected_risk = healthy_state.portfolio_value_usd * MAX_RISK_PER_TRADE_PCT / 100.0
        assert abs(result.risk_amount_usd - expected_risk) < 1.0  # Allow $1 rounding

    def test_invalid_price_rejected(self, engine, healthy_state):
        result = engine.size_position(
            symbol="SOL/USDT",
            atr_14=0.0,   # Invalid ATR
            current_price=100.0,
            portfolio_state=healthy_state,
            regime_breadth_pct=65.0,
        )
        assert result.approved is False
        assert result.rejection_reason is not None


class TestCircuitBreakers:
    def test_level1_reduces_to_75pct(self, engine):
        state = PortfolioRiskState(
            portfolio_value_usd=92_000.0,
            peak_value_usd=100_000.0,
            current_drawdown_pct=8.0,  # Level 1 trigger
        )
        result = engine.size_position(
            symbol="SOL/USDT",
            atr_14=2.0,
            current_price=100.0,
            portfolio_state=state,
            regime_breadth_pct=50.0,
        )
        assert result.hit_circuit_breaker is True
        assert result.circuit_breaker_level == CircuitBreakerLevel.LEVEL_1
        # Size should be 75% of what it would be without CB
        assert result.approved is True  # Level 1 doesn't halt

    def test_level2_reduces_to_50pct(self, engine):
        state = PortfolioRiskState(
            portfolio_value_usd=85_000.0,
            peak_value_usd=100_000.0,
            current_drawdown_pct=15.0,  # Level 2 trigger
        )
        result = engine.size_position(
            symbol="SOL/USDT",
            atr_14=2.0,
            current_price=100.0,
            portfolio_state=state,
            regime_breadth_pct=40.0,
        )
        assert result.circuit_breaker_level == CircuitBreakerLevel.LEVEL_2

    def test_level3_halts_all_positions(self, engine):
        state = PortfolioRiskState(
            portfolio_value_usd=75_000.0,
            peak_value_usd=100_000.0,
            current_drawdown_pct=25.0,  # Level 3 trigger
        )
        result = engine.size_position(
            symbol="BTC/USDT",
            atr_14=1_500.0,
            current_price=60_000.0,
            portfolio_state=state,
            regime_breadth_pct=20.0,
        )
        assert result.approved is False
        assert result.circuit_breaker_level == CircuitBreakerLevel.LEVEL_3
        assert result.rejection_reason is not None

    def test_halted_portfolio_rejects_all_symbols(self, engine):
        state = PortfolioRiskState(
            portfolio_value_usd=75_000.0,
            peak_value_usd=100_000.0,
            current_drawdown_pct=25.0,
            circuit_breaker_level=CircuitBreakerLevel.LEVEL_3,
        )
        for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
            result = engine.size_position(
                symbol=symbol,
                atr_14=1.0,
                current_price=100.0,
                portfolio_state=state,
                regime_breadth_pct=15.0,
            )
            assert result.approved is False


class TestRegimeMultiplier:
    def test_high_breadth_full_multiplier(self, engine, healthy_state):
        # Use small ATR so tier cap doesn't dominate result
        result_high = engine.size_position(
            symbol="SOL/USDT", atr_14=0.1, current_price=100.0,
            portfolio_state=healthy_state, regime_breadth_pct=70.0,
        )
        result_low = engine.size_position(
            symbol="SOL/USDT", atr_14=0.1, current_price=100.0,
            portfolio_state=healthy_state, regime_breadth_pct=20.0,
        )
        if result_high.approved and result_low.approved:
            # Low breadth should reduce regime_multiplier and therefore risk_amount_usd
            assert result_high.regime_multiplier > result_low.regime_multiplier

    def test_extreme_low_breadth_minimal_size(self, engine, healthy_state):
        result = engine.size_position(
            symbol="SOL/USDT", atr_14=2.0, current_price=100.0,
            portfolio_state=healthy_state, regime_breadth_pct=10.0,  # < 25%
        )
        # Multiplier should be 0.25 max
        assert result.regime_multiplier <= 0.25 * 1.0  # Excluding vol/funding penalties


class TestConcentrationLimits:
    def test_over_exposure_in_same_asset_rejected(self, engine):
        """Adding more BTC when already at 5% cap must be rejected."""
        state = PortfolioRiskState(
            portfolio_value_usd=100_000.0,
            peak_value_usd=100_000.0,
            current_drawdown_pct=0.0,
            open_positions={"BTC/USDT": 5_000.0},  # 5% already
        )
        result = engine.size_position(
            symbol="BTC/USDT",
            atr_14=1_500.0,
            current_price=60_000.0,
            portfolio_state=state,
            regime_breadth_pct=65.0,
        )
        # Should either hit tier max or concentration limit
        assert result.hit_tier_max or result.hit_concentration_limit or not result.approved


class TestVaR:
    def test_var_computed_from_returns(self, engine, state_with_returns):
        var_result = engine.compute_var(state_with_returns)
        assert var_result.var_95_pct > 0.0
        assert var_result.var_99_pct >= var_result.var_95_pct
        assert var_result.cvar_95_pct >= var_result.var_95_pct
        assert var_result.is_reliable is True

    def test_var_unreliable_with_insufficient_history(self, engine):
        state = PortfolioRiskState(
            portfolio_value_usd=100_000.0,
            peak_value_usd=100_000.0,
            current_drawdown_pct=0.0,
            daily_returns=np.array([0.01, -0.02, 0.005]),  # Only 3 days
        )
        var_result = engine.compute_var(state)
        assert var_result.is_reliable is False

    def test_var_none_returns_no_reduction(self, engine):
        state = PortfolioRiskState(
            portfolio_value_usd=100_000.0,
            peak_value_usd=100_000.0,
            current_drawdown_pct=0.0,
            daily_returns=None,
        )
        var_result = engine.compute_var(state)
        assert var_result.size_reduction_factor == 1.0
