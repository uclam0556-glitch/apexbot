"""
Tests for Phase 4 - RegimeEngine
"""
import pytest
import numpy as np
import pandas as pd
from services.regime.regime_engine import RegimeEngine, MasterRegime, RegimeState

@pytest.fixture
def engine():
    return RegimeEngine()

def test_extreme_stress_high_corr(engine):
    # avg_corr > 0.80 -> EXTREME_STRESS
    state = engine.classify(
        pct_above_50d=50.0, pct_above_200d=50.0, adx=20.0,
        avg_corr=0.85, realized_vol_ratio=1.0, features_row=np.array([0,0,0]),
        avg_spread_pct=0.1, avg_adv_usd=1e9, volume_breadths=0.5,
        btc_dominance_series=pd.Series([50, 50, 50, 50, 50, 50, 50]), alt_performance_90d=0.5
    )
    assert state.master_regime == MasterRegime.EXTREME_STRESS
    assert state.size_multiplier == 0.0

def test_bear_risk_off(engine):
    # pct_above_50d < 40 and avg_corr > 0.60
    state = engine.classify(
        pct_above_50d=35.0, pct_above_200d=50.0, adx=20.0,
        avg_corr=0.70, realized_vol_ratio=1.0, features_row=np.array([0,0,0]),
        avg_spread_pct=0.1, avg_adv_usd=1e9, volume_breadths=0.5,
        btc_dominance_series=pd.Series([50]*7), alt_performance_90d=0.5
    )
    assert state.master_regime == MasterRegime.BEAR_RISK_OFF
    assert state.size_multiplier == 0.25

def test_trending_bull(engine):
    # pct_above_50d > 60 and adx > 25 and hmm_state == 0 and avg_corr < 0.50
    state = engine.classify(
        pct_above_50d=65.0, pct_above_200d=50.0, adx=30.0,
        avg_corr=0.40, realized_vol_ratio=0.5, features_row=np.array([0,0,0]), # vol_ratio 0.5 -> fallback state 0
        avg_spread_pct=0.1, avg_adv_usd=1e9, volume_breadths=0.5,
        btc_dominance_series=pd.Series([50]*7), alt_performance_90d=0.5
    )
    assert state.master_regime == MasterRegime.TRENDING_BULL
    assert state.size_multiplier == 1.0

def test_hmm_hysteresis(engine):
    # First call: set state to 2 (high vol)
    st1, _ = engine.classify_hmm(np.array([0,0,0]), vol_ratio=2.0)
    assert st1 == 2
    
    # Second call: change input to low vol (0.5), but hysteresis should keep it at 2
    st2, _ = engine.classify_hmm(np.array([0,0,0]), vol_ratio=0.5)
    assert st2 == 2
    
    # Third call: low vol
    st3, _ = engine.classify_hmm(np.array([0,0,0]), vol_ratio=0.5)
    assert st3 == 2
    
    # Fourth call: low vol -> should flip to 0 now
    st4, _ = engine.classify_hmm(np.array([0,0,0]), vol_ratio=0.5)
    assert st4 == 0

def test_liquidity_index_calculation(engine):
    idx, reg = engine.compute_liquidity_regime(
        avg_spread_pct=0.25, # spread score = 1 - 0.25/0.5 = 0.5
        avg_adv_usd=5e8,     # depth score = 5e8 / 1e9 = 0.5
        volume_breadth=0.5   # breadth = 0.5
    )
    # index = 0.4*0.5 + 0.3*0.5 + 0.3*0.5 = 0.2 + 0.15 + 0.15 = 0.50 -> REDUCED
    assert pytest.approx(idx) == 0.50
    assert reg == 'REDUCED'

def test_correlation_regimes(engine):
    assert engine.compute_correlation_regime(0.20) == 'DIVERSIFIED'
    assert engine.compute_correlation_regime(0.40) == 'NORMAL'
    assert engine.compute_correlation_regime(0.70) == 'HIGH_CORRELATION'
    assert engine.compute_correlation_regime(0.90) == 'EMERGENCY_RISK_OFF'

def test_btc_dominance_delta(engine):
    series = pd.Series([50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 51.5])
    delta, alt = engine.compute_crypto_regime(series, 0.8)
    assert delta == 1.5
    assert alt == 0.8
