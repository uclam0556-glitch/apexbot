"""Tests for CVD Engine v2: real taker-flow math, divergence, proxy fallback."""
import time

import pandas as pd
import pytest

import services.intelligence.cvd_engine as cvd


def _kline(o, h, l, c, vol, taker_buy):
    # Binance kline layout: [9] = taker buy base asset volume
    return [0, str(o), str(h), str(l), str(c), str(vol), 0, 0, 0, str(taker_buy), 0, 0]


@pytest.mark.asyncio
async def test_taker_flow_divergence_detection():
    """Higher highs in price with fading taker buyers must flag exhaustion."""
    klines = [_kline(100 + i, 101 + i, 99 + i, 100.5 + i, 1000, 700) for i in range(10)]
    klines += [_kline(110 + i, 112 + i, 109 + i, 110.2 + i, 1000, 350) for i in range(10)]
    cvd._klines_cache['TESTAUSDT_5m_20'] = {'time': time.time(), 'data': klines}

    r = await cvd.calculate_cvd_real('TESTA/USDT', lookback=20)
    assert r['source'] == 'taker_flow'
    assert r['divergence'] is True
    assert r['score'] <= -20  # exhaustion penalty applied


@pytest.mark.asyncio
async def test_taker_flow_bullish():
    klines = [_kline(100 + i, 101 + i, 99 + i, 100.5 + i, 1000, 800) for i in range(20)]
    cvd._klines_cache['TESTBUSDT_5m_20'] = {'time': time.time(), 'data': klines}

    r = await cvd.calculate_cvd_real('TESTB/USDT', lookback=20)
    assert r['source'] == 'taker_flow'
    assert r['cvd_signal'] == 'BULLISH'
    assert r['score'] == 2
    # delta = 2*800 - 1000 = +600 per candle -> cvd_pct = 600/1000 = 60%
    assert abs(r['cvd_pct'] - 0.60) < 1e-9


@pytest.mark.asyncio
async def test_fallback_to_proxy_when_api_unavailable(monkeypatch):
    async def no_klines(symbol, interval, limit):
        return None
    monkeypatch.setattr(cvd, '_fetch_binance_klines', no_klines)

    df = pd.DataFrame({
        'open': [1.0] * 25, 'high': [1.1] * 25, 'low': [0.9] * 25,
        'close': [1.05] * 25, 'volume': [100.0] * 25,
    })
    r = await cvd.calculate_cvd_real('NOWHERE/XYZ', lookback=20, fallback_df=df)
    assert r['source'] == 'proxy'
    assert r['cvd_signal'] == 'BULLISH'  # all green candles


def test_proxy_empty_df_neutral():
    r = cvd.calculate_cvd(pd.DataFrame(), lookback=20)
    assert r['cvd_signal'] == 'NEUTRAL'
    assert r['score'] == 0
    assert r['source'] == 'proxy'
