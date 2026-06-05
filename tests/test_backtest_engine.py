"""
Tests for Phase 5 - BacktestEngine
"""
import pytest
import numpy as np
import pandas as pd
from services.backtest.engine import BacktestEngine, BacktestTrade

def create_mock_data():
    dates = pd.date_range('2023-01-01', periods=10, freq='D')
    df = pd.DataFrame({
        'open': [100, 101, 102, 105, 110, 108, 112, 115, 110, 120],
        'high': [102, 103, 106, 112, 112, 110, 115, 118, 115, 125],
        'low': [99, 100, 101, 104, 107, 105, 110, 110, 108, 118],
        'close': [101, 102, 105, 110, 108, 112, 115, 110, 120, 122],
        'volume': [1000] * 10
    }, index=dates)
    return {'BTC': df}

def create_mock_signals():
    dates = pd.date_range('2023-01-01', periods=10, freq='D')
    # Buy signal on day 1 (index 0)
    return pd.DataFrame({
        'timestamp': [dates[0]],
        'symbol': ['BTC'],
        'direction': ['LONG'],
        'prob': [0.60],
        'sl_price': [95.0],
        'tp_price': [115.0],
        'close': [101.0]
    })

def test_backtest_engine_run():
    engine = BacktestEngine(initial_capital=100000)
    signals = create_mock_signals()
    ohlcv = create_mock_data()
    
    metrics = engine.run(signals, ohlcv)
    
    assert metrics.total_trades == 1
    # Trade should hit TP at 115 on day 6 (index 6 where high=115)
    trade = engine.trades[0]
    assert trade.exit_reason == "TP"
    assert trade.exit_price == 115.0
    assert trade.gross_pnl_usd > 0
    assert metrics.win_rate == 1.0

def test_empty_signals():
    engine = BacktestEngine()
    metrics = engine.run(pd.DataFrame(), create_mock_data())
    assert metrics.total_trades == 0
