"""
Test script for the Auto-Tuner logic
"""
import asyncio
import pandas as pd
from services.engine.auto_tuner import AutoTuner

def mock_test():
    print("Generating 1866 mock V7_GATE_FAIL trades for demonstration...")
    # Mock data mirroring the user's dashboard
    # Let's say out of 1866 trades:
    # - 423 would have hit TP (mfe_pct >= 1.5)
    # - 892 hit SL (mae_pct <= -1.0)
    # - remaining hit breakeven/timeout
    
    data = []
    
    # Generate 423 missed TPs
    # If the threshold was high (e.g. 50), all these were blocked.
    # The actual scores of these good trades might be between 40 and 49.
    import random
    for _ in range(423):
        score = random.uniform(35.0, 49.9)
        data.append({'block_reason': 'V7_GATE_FAIL', 'v7_score': score, 'mfe_pct': 2.0, 'mae_pct': 0.0})
        
    # Generate 892 saved SLs
    # Losers might have slightly lower scores on average (30 to 45)
    for _ in range(892):
        score = random.uniform(30.0, 45.0)
        data.append({'block_reason': 'V7_GATE_FAIL', 'v7_score': score, 'mfe_pct': 0.0, 'mae_pct': -1.5})
        
    df = pd.DataFrame(data)
    
    tuner = AutoTuner()
    
    print("\n--- SIMULATION RESULTS ---")
    best_thresh = 50.0
    best_net_r = 0.0
    
    for thresh in [30, 35, 38, 40, 42, 45, 48, 50]:
        wins, losses, net_r = tuner.simulate_v7_gate(df, float(thresh))
        print(f"If V7 Gate = {thresh:2d} -> We capture +{wins:3d} TPs and +{losses:3d} SLs | Net R Gained: {net_r:+.1f}R")
        
        if net_r > best_net_r:
            best_net_r = net_r
            best_thresh = thresh
            
    print(f"\n=> OPTIMAL GATE FOUND: {best_thresh} (Maximized Net Profit at {best_net_r:+.1f}R)")
    print("This recommendation would now be saved to 'auto_tuner_recommendations' table.")
    print("To apply it, update the 'applied' column to TRUE, and main.py will pick it up instantly.")

if __name__ == "__main__":
    mock_test()
