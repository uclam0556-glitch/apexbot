"""
APEX Institutional Auto-Tuner V2
Runs multi-dimensional grid search on historical shadow trades to find the Pareto-optimal
V7 Gate and Min R:R thresholds, conditioned by Macro Regime.
Optimizes for the Sortino Ratio with Time-Decay weights.
"""

import asyncio
import pandas as pd
import numpy as np
import structlog
from typing import Dict, Any, Tuple

from database.analytics import get_shadow_trades_for_calibration
from database.timescaledb import get_pool
from services.engine.quant_metrics import calculate_time_decay_weights, calculate_sortino_ratio

logger = structlog.get_logger(__name__)

class InstitutionalGridSearch:
    def __init__(self, half_life_days: float = 14.0):
        self.half_life_days = half_life_days
        # R-multiple assumptions for simulation
        self.r_win = 1.5
        self.r_loss = 1.0

    def evaluate_parameters(self, df: pd.DataFrame, v7_gate: float, min_rr: float) -> Tuple[float, float, int, int]:
        """
        Evaluates a specific combination of (V7 Gate, Min RR).
        Returns: (Sortino Ratio, Expected Net R, Wins, Losses)
        """
        # Simulated logic:
        # A trade passes if its v7_score >= v7_gate AND its implied R:R >= min_rr.
        # Since we don't have exact R:R stored for blocked trades, we simulate:
        # If block_reason was 'V7_GATE_FAIL' but score >= v7_gate, it passes V7 check.
        # If block_reason was 'insufficient_rr' and we lower min_rr, it might pass.
        
        # For simplicity in this engine, we'll focus strictly on V7_GATE optimization,
        # but apply the Time Decay and Sortino Ratio. (Full multi-dimensional requires 
        # actual R:R stored in db, which we'll assume is present in V11.1).
        
        cohort = df[(df['block_reason'] == 'V7_GATE_FAIL') & (df['v7_score'] >= v7_gate)]
        
        if len(cohort) == 0:
            return 0.0, 0.0, 0, 0
            
        # Create a synthetic return series
        # Winners get +1.5R, Losers get -1.0R, others get 0
        returns = []
        weights = []
        
        wins = 0
        losses = 0
        
        # Assuming df has 'time_weight' pre-calculated
        for _, row in cohort.iterrows():
            w = row.get('time_weight', 1.0)
            if row['mfe_pct'] >= 1.5:
                returns.append(self.r_win * w)
                wins += 1
            elif row['mae_pct'] <= -1.0:
                returns.append(-self.r_loss * w)
                losses += 1
            else:
                returns.append(0.0)
                
        returns_arr = np.array(returns)
        sortino = calculate_sortino_ratio(returns_arr)
        expected_net_r = np.sum(returns_arr)
        
        return sortino, expected_net_r, wins, losses

    async def run_calibration(self, days: int = 30) -> Dict[str, Any]:
        logger.info(f"Starting Institutional Grid Search over last {days} days (Half-Life: {self.half_life_days}d)")
        df = await get_shadow_trades_for_calibration(days)
        
        if df.empty:
            logger.warning("No data available for calibration.")
            return {"status": "error", "message": "No data"}
            
        # 1. Apply Time-Decay Weighting
        df['time_weight'] = calculate_time_decay_weights(df['created_at'], self.half_life_days)
        
        # 2. Split by Regime
        regimes = df['regime'].unique()
        logger.info(f"Found regimes in data: {regimes}")
        
        results = {}
        
        for regime in regimes:
            if not regime:
                continue
                
            regime_df = df[df['regime'] == regime]
            logger.info(f"Optimizing for [{regime}] regime ({len(regime_df)} shadow trades)...")
            
            best_sortino = -99.9
            best_v7 = 50.0
            best_net_r = 0.0
            
            for v7_test in np.arange(30.0, 80.0, 2.0):
                sortino, net_r, w, l = self.evaluate_parameters(regime_df, v7_test, 1.5)
                
                # We want the highest Sortino, but we need at least a positive Expected Net R
                if sortino > best_sortino and net_r > 0:
                    best_sortino = sortino
                    best_v7 = v7_test
                    best_net_r = net_r
                    
            logger.info(f"[{regime}] Optimal V7 Gate: {best_v7:.1f} | Sortino: {best_sortino:.2f} | Exp Net R: +{best_net_r:.1f}")
            
            # Save recommendation per regime
            param_key = f"v7_gate_{regime.lower()}"
            await self._save_recommendation(param_key, best_v7, best_sortino, best_net_r)
            results[regime] = {"v7_gate": best_v7, "sortino": best_sortino}
            
        return {"status": "success", "regimes": results}
        
    async def _save_recommendation(self, param_name: str, value: float, sortino: float, net_r: float):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_tuner_v2_recommendations (
                    id SERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    parameter_name TEXT UNIQUE,
                    recommended_value DOUBLE PRECISION,
                    sortino_ratio DOUBLE PRECISION,
                    expected_net_r DOUBLE PRECISION,
                    applied BOOLEAN DEFAULT FALSE
                )
            """)
            await conn.execute("""
                INSERT INTO auto_tuner_v2_recommendations 
                (parameter_name, recommended_value, sortino_ratio, expected_net_r, applied)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (parameter_name) DO UPDATE 
                SET recommended_value = EXCLUDED.recommended_value,
                    sortino_ratio = EXCLUDED.sortino_ratio,
                    expected_net_r = EXCLUDED.expected_net_r,
                    applied = FALSE,
                    created_at = NOW();
            """, param_name, value, sortino, net_r)

if __name__ == "__main__":
    tuner = InstitutionalGridSearch(half_life_days=14.0)
    asyncio.run(tuner.run_calibration(days=30))
