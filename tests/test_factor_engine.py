"""
Tests for Phase 2.1 - FactorEngine
"""
import pytest
import numpy as np
import pandas as pd
from services.factors.factor_engine import FactorEngine

@pytest.fixture
def engine():
    return FactorEngine()

def create_synthetic_data(n_bars=100, seed=42):
    np.random.seed(seed)
    dates = pd.date_range('2023-01-01', periods=n_bars, freq='D')
    
    # Random walk
    returns = np.random.normal(0.001, 0.05, n_bars)
    prices = 100 * np.exp(np.cumsum(returns))
    
    # OHLCV
    high = prices * (1 + np.random.uniform(0, 0.05, n_bars))
    low = prices * (1 - np.random.uniform(0, 0.05, n_bars))
    open_prices = prices * (1 + np.random.uniform(-0.02, 0.02, n_bars))
    volume = np.random.lognormal(10, 1, n_bars)
    
    df = pd.DataFrame({
        'open': open_prices,
        'high': high,
        'low': low,
        'close': prices,
        'volume': volume,
    }, index=dates)
    
    # Extras
    df['cvd'] = np.cumsum(np.random.normal(0, 100, n_bars))
    df['bid_vol'] = np.random.uniform(10, 100, n_bars)
    df['ask_vol'] = np.random.uniform(10, 100, n_bars)
    df['liquidation_pressure'] = np.random.uniform(0, 1000, n_bars)
    df['open_interest'] = np.random.uniform(50000, 100000, n_bars)
    df['implied_vol'] = np.random.uniform(0.5, 1.5, n_bars)
    df['funding_rate'] = np.random.normal(0.0001, 0.0005, n_bars)
    
    return df

def test_factor_engine_single_asset(engine):
    df = create_synthetic_data()
    scores = engine.compute_single_asset('BTC', df)
    
    assert scores.symbol == 'BTC'
    assert isinstance(scores.timestamp, float)
    assert isinstance(scores.cross_sectional_momentum, float)
    assert isinstance(scores.garman_klass_vol, float)
    assert scores.garman_klass_vol >= 0.0
    
def test_factor_engine_all_assets(engine):
    asset_data = {
        'BTC': create_synthetic_data(seed=1),
        'ETH': create_synthetic_data(seed=2),
        'SOL': create_synthetic_data(seed=3)
    }
    
    df = engine.compute_all_assets(asset_data)
    assert len(df) == 3
    assert 'cross_sectional_momentum' in df.columns
    # Check bounds for normalized CS momentum
    assert df['cross_sectional_momentum'].max() <= 1.0
    assert df['cross_sectional_momentum'].min() >= -1.0
    
def test_oi_delta_4state(engine):
    df = create_synthetic_data()
    # Force state
    df.loc[df.index[-2], 'close'] = 100.0
    df.loc[df.index[-1], 'close'] = 105.0 # price up
    df.loc[df.index[-2], 'open_interest'] = 1000.0
    df.loc[df.index[-1], 'open_interest'] = 1100.0 # oi up -> +1.0
    
    scores = engine.compute_single_asset('BTC', df)
    assert scores.oi_delta_4state == 1.0
    
def test_nan_handling_and_normalization(engine):
    df = engine.normalize_cross_sectional(pd.DataFrame({
        'A': [1.0, 2.0, np.nan],
        'B': [-5.0, 0.0, 5.0]
    }))
    assert len(df) == 3
    assert df['B'].max() <= 1.0

def test_winsorize(engine):
    df = pd.DataFrame({
        'A': np.concatenate([np.random.normal(0, 1, 98), [100.0, -100.0]])
    })
    win_df = engine.winsorize(df)
    assert win_df['A'].max() < 100.0
    assert win_df['A'].min() > -100.0

def test_correlation_alert(caplog, engine):
    df = pd.DataFrame({
        'A': np.random.normal(0, 1, 100),
    })
    df['B'] = df['A'] * 0.9 + np.random.normal(0, 0.1, 100) # High correlation
    
    corr = engine.factor_correlations(df)
    assert "HIGH CORRELATION DETECTED" in caplog.text
    
def test_garman_klass_always_positive(engine):
    df = create_synthetic_data()
    scores = engine.compute_single_asset('BTC', df)
    assert scores.garman_klass_vol >= 0.0

def test_cross_sectional_momentum_zero_sum(engine):
    asset_data = {f"A_{i}": create_synthetic_data(seed=i) for i in range(10)}
    df = engine.compute_all_assets(asset_data)
    # The sum of normalized ranks should be very close to 0
    assert abs(df['cross_sectional_momentum'].sum()) < 1e-5

def test_insufficient_data(engine):
    df = create_synthetic_data(n_bars=10) # Too few for 63-day features
    asset_data = {'BTC': df}
    res = engine.compute_all_assets(asset_data)
    assert res.empty
