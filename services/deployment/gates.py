"""
APEX v11.0 — Institutional Deployment Gates (Phase 7)
=====================================================
Hard gates preventing unverified code/models from touching live capital.
No human can override these gates without pushing a documented configuration change.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List

logger = logging.getLogger(__name__)

@dataclass
class GateCheck:
    name: str
    description: str
    passed: bool
    details: str

class DeploymentGatekeeper:
    def __init__(self):
        self.checks: List[GateCheck] = []

    def verify_ml_calibration(self, is_calibrated: bool, max_calibration_error: float) -> GateCheck:
        """Gate 1: Model must be Isotonic-calibrated with < 10% error."""
        passed = is_calibrated and (max_calibration_error < 0.10)
        return GateCheck(
            name="ML Calibration Check",
            description="Isotonic calibration max error < 10%",
            passed=passed,
            details=f"Calibrated: {is_calibrated}, Error: {max_calibration_error:.2f}"
        )

    def verify_shadow_trades(self, total_shadow: int, shadow_win_rate: float, backtest_win_rate: float) -> GateCheck:
        """Gate 2: Minimum 300 shadow trades, tracking error < 5% vs backtest."""
        passed = (total_shadow >= 300) and abs(shadow_win_rate - backtest_win_rate) < 0.05
        return GateCheck(
            name="Shadow Trade Verification",
            description=">=300 shadow trades, win rate matches backtest within 5%",
            passed=passed,
            details=f"Trades: {total_shadow}, Shadow WR: {shadow_win_rate:.2f}, Backtest WR: {backtest_win_rate:.2f}"
        )

    def verify_ws_uptime(self, uptime_pct: float) -> GateCheck:
        """Gate 3: Data infrastructure must have 99%+ uptime."""
        passed = uptime_pct >= 99.0
        return GateCheck(
            name="WebSocket Uptime Check",
            description=">= 99.0% uptime over last 7 days",
            passed=passed,
            details=f"Uptime: {uptime_pct:.2f}%"
        )

    def run_all_gates(self, 
                      is_calibrated: bool, 
                      cal_error: float, 
                      shadow_count: int, 
                      shadow_wr: float, 
                      bt_wr: float, 
                      ws_uptime: float) -> bool:
        
        self.checks = [
            self.verify_ml_calibration(is_calibrated, cal_error),
            self.verify_shadow_trades(shadow_count, shadow_wr, bt_wr),
            self.verify_ws_uptime(ws_uptime)
        ]
        
        all_passed = all(c.passed for c in self.checks)
        
        if all_passed:
            logger.info("ALL DEPLOYMENT GATES PASSED. System is cleared for live trading.")
        else:
            logger.warning("DEPLOYMENT GATES FAILED. Live trading is hard-locked.")
            for c in self.checks:
                if not c.passed:
                    logger.warning(f"FAILED: {c.name} - {c.details}")
                    
        return all_passed
