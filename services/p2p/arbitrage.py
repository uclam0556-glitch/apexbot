"""
APEX Trading System v4.0
P2P Arbitrage Engine.

Scans for and executes P2P arbitrage opportunities across platforms
(e.g., Binance P2P, Bybit P2P, HTX) against spot markets.
Integrates with FX Slippage Model for true net margin calculation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from shared.models import OpportunityGrade
from services.p2p.fx_slippage import P2PFXSlippageModel

logger = logging.getLogger(__name__)


class P2PArbitrageEngine:
    """
    Engine for identifying and grading P2P Arbitrage opportunities.
    """

    def __init__(self) -> None:
        self.fx_model = P2PFXSlippageModel()
        self.min_gross_margin = 1.5 # Baseline requirement

    async def scan_opportunities(self, fiat: str = "RUB", crypto: str = "USDT") -> list[dict[str, Any]]:
        """
        Scans P2P orderbooks to find Maker/Taker spreads.
        """
        # Mock logic: in prod, fetches from Binance/Bybit P2P endpoints
        logger.info(f"Scanning P2P opportunities for {crypto}/{fiat}...")
        
        # Simulated raw opportunities
        opportunities = [
            {
                "id": "opp_1",
                "buy_platform": "Bybit",
                "sell_platform": "Binance",
                "buy_price": 91.50,
                "sell_price": 94.20,
                "crypto": crypto,
                "fiat": fiat,
                "gross_margin_pct": ((94.20 - 91.50) / 91.50) * 100,
                "execution_time_est_mins": 45
            },
            {
                "id": "opp_2",
                "buy_platform": "HTX",
                "sell_platform": "Bybit",
                "buy_price": 92.10,
                "sell_price": 93.80,
                "crypto": crypto,
                "fiat": fiat,
                "gross_margin_pct": ((93.80 - 92.10) / 92.10) * 100,
                "execution_time_est_mins": 30
            }
        ]
        
        return opportunities

    async def evaluate_opportunities(self) -> list[dict[str, Any]]:
        """
        Evaluates raw opportunities through the FX Slippage Model to get true net margin.
        """
        raw_opps = await self.scan_opportunities()
        valid_opps = []
        
        now_msk = (datetime.utcnow().hour + 3) % 24 # Crude MSK conversion
        
        fx_vol = await self.fx_model.get_fx_volatility_1h("USDT/RUB")
        btc_vol = await self.fx_model.get_btc_volatility_1h()

        for opp in raw_opps:
            if opp["gross_margin_pct"] < self.min_gross_margin:
                continue
                
            slippage_estimate = self.fx_model.estimate_fx_slippage(
                pair=f"{opp['crypto']}/{opp['fiat']}",
                execution_minutes=opp["execution_time_est_mins"],
                btc_volatility_1h=btc_vol,
                fx_volatility_1h=fx_vol,
                net_margin_current=opp["gross_margin_pct"],
                time_of_day_msk=now_msk
            )
            
            grade = self.fx_model.grade_with_slippage(opp["gross_margin_pct"], slippage_estimate)
            
            if grade.final_grade != OpportunityGrade.SKIP:
                opp["net_margin_pct"] = grade.adjusted_margin
                opp["grade"] = grade.final_grade.value
                opp["alert_info"] = grade.alert_string
                valid_opps.append(opp)
                
        # Sort by best net margin
        valid_opps.sort(key=lambda x: x["net_margin_pct"], reverse=True)
        return valid_opps

    async def run_cycle(self) -> None:
        """
        Main loop cycle for the worker.
        """
        opps = await self.evaluate_opportunities()
        for opp in opps:
            if opp["grade"] in ["PREMIUM", "GOOD"]:
                logger.info(f"FOUND {opp['grade']} P2P OPP: {opp['buy_platform']} -> {opp['sell_platform']} | Net Margin: {opp['net_margin_pct']:.2f}%")
                # Trigger alert/execution
