import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from services.engine.smc_core import FormalizedSMCCore

def generate_dummy_data(n: int = 100) -> pd.DataFrame:
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    timestamps = [base_time + timedelta(hours=i) for i in range(n)]
    
    # Create a trend to generate structure
    prices = 1000 + np.cumsum(np.random.randn(n) * 10)
    
    # Generate OHLCV
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": prices + 5,
        "low": prices - 5,
        "close": prices + np.random.randn(n) * 2,
        "volume": np.random.randint(100, 1000, n)
    })
    
    # Create an artificial FVG and Swing point
    # Force a swing high at index 20
    df.loc[15:25, "high"] = 1000
    df.loc[20, "high"] = 1050
    
    # Force a FVG at index 40
    df.loc[38, "close"] = 1020
    df.loc[38, "open"] = 1000
    df.loc[38, "high"] = 1025
    
    df.loc[39, "low"] = 1030
    df.loc[39, "high"] = 1060
    
    df.loc[40, "low"] = 1050
    df.loc[40, "high"] = 1070
    
    return df

def test_no_lookahead():
    """
    Test that the SMC Core analysis does not peek into future candles,
    and accurately records the candle_close_ts for all events.
    """
    df = generate_dummy_data(100)
    smc = FormalizedSMCCore(timeframe="1h")
    
    # 1. Test full analysis without current_bar_index constraint
    analysis_full = smc.analyze(df, symbol="TEST")
    
    all_events = (
        analysis_full.swing_highs + 
        analysis_full.swing_lows + 
        analysis_full.imbalance_zones + 
        analysis_full.structure_events + 
        analysis_full.liquidity_sweeps
    )
    
    # All events should have a candle_close_ts less than or equal to the last candle
    last_ts = df["timestamp"].iloc[-1]
    for e in all_events:
        if hasattr(e, "candle_close_ts") and e.candle_close_ts is not None:
            assert e.candle_close_ts <= last_ts, f"Lookahead detected in full run: {e}"
            
    # 2. Test analysis with current_bar_index constraint
    current_idx = 50
    analysis_partial = smc.analyze(df, symbol="TEST", current_bar_index=current_idx)
    
    all_events_partial = (
        analysis_partial.swing_highs + 
        analysis_partial.swing_lows + 
        analysis_partial.imbalance_zones + 
        analysis_partial.structure_events + 
        analysis_partial.liquidity_sweeps
    )
    
    last_partial_ts = df["timestamp"].iloc[current_idx - 1]
    
    for e in all_events_partial:
        if hasattr(e, "candle_close_ts") and e.candle_close_ts is not None:
            assert e.candle_close_ts <= last_partial_ts, f"Lookahead detected in partial run: {e}"

if __name__ == "__main__":
    test_no_lookahead()
    print("test_no_lookahead passed successfully!")
