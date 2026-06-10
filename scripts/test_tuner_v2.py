"""
Test script for the Institutional Auto-Tuner V2
Generates mock trades across different regimes and demonstrates Regime-Conditioned calibration.
"""
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from services.engine.auto_tuner import InstitutionalGridSearch

def generate_mock_regime_data(regime: str, count: int, optimal_v7: float, decay_bias: bool = False):
    data = []
    now = datetime.utcnow()
    
    for i in range(count):
        # Generate timestamps stretching back 30 days
        days_ago = np.random.uniform(0, 30)
        trade_time = now - timedelta(days=days_ago)
        
        # Simulate scores around the optimal
        score = np.random.uniform(30.0, 75.0)
        
        # Determine if it was a winner or loser based on the regime and optimal V7
        if score >= optimal_v7:
            # Good trades
            if np.random.rand() > 0.4:
                mfe, mae = 2.0, 0.0 # Winner
            else:
                mfe, mae = 0.0, -1.5 # Loser
        else:
            # Bad trades (if gate was lowered here, it would catch these losers)
            if np.random.rand() > 0.8:
                mfe, mae = 2.0, 0.0 # Lucky winner
            else:
                mfe, mae = 0.0, -1.5 # Loser
                
        # If decay_bias is true, make older trades less profitable to test Time-Decay
        if decay_bias and days_ago > 15 and mfe > 1.0:
            mfe, mae = 0.0, -1.5 # Convert older winners to losers
            
        data.append({
            'created_at': trade_time,
            'regime': regime,
            'block_reason': 'V7_GATE_FAIL',
            'v7_score': score,
            'mfe_pct': mfe,
            'mae_pct': mae
        })
    return data

def mock_test():
    print("Generating 5000 mock multi-regime shadow trades...")
    
    # In a BULL market, lower V7 is fine (e.g. 38)
    bull_data = generate_mock_regime_data('BULL', 2000, optimal_v7=38.0)
    # In a BEAR market, higher V7 is needed (e.g. 56)
    bear_data = generate_mock_regime_data('BEAR', 2000, optimal_v7=56.0, decay_bias=True)
    # SIDEWAYS market is in between
    side_data = generate_mock_regime_data('SIDEWAYS', 1000, optimal_v7=45.0)
    
    df = pd.DataFrame(bull_data + bear_data + side_data)
    
    # Initialize the Grid Search with a 14-day half-life memory
    tuner = InstitutionalGridSearch(half_life_days=14.0)
    
    print("\n--- INSTITUTIONAL V2 SIMULATION (14-Day Memory) ---")
    
    # Run the equivalent of the async run_calibration but directly on the dataframe for testing
    from services.engine.quant_metrics import calculate_time_decay_weights
    df['time_weight'] = calculate_time_decay_weights(df['created_at'], tuner.half_life_days)
    
    regimes = df['regime'].unique()
    
    for regime in regimes:
        regime_df = df[df['regime'] == regime]
        
        best_sortino = -99.9
        best_v7 = 50.0
        
        for v7_test in np.arange(30.0, 80.0, 2.0):
            sortino, net_r, w, l = tuner.evaluate_parameters(regime_df, v7_test, 1.5)
            if sortino > best_sortino and net_r > 0:
                best_sortino = sortino
                best_v7 = v7_test
                
        print(f"[{regime.ljust(8)}] Optimal V7: {best_v7:4.1f} | Sortino: {best_sortino:4.2f}")

    print("\nAuto-Tuner V2 has completed Regime-Conditioned calibration.")

if __name__ == "__main__":
    mock_test()
