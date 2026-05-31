import logging
from typing import List, Dict

logger = logging.getLogger("ExposureManager")

class ExposureManager:
    """
    Manages portfolio-level risk and exposure limits.
    Fetches real-time open trades directly from the database instead of relying on loop snapshots.
    """
    def __init__(self, config):
        self.config = config

    async def get_current_exposure(self) -> dict:
        """
        Returns a fresh snapshot of current portfolio exposure.
        """
        from shared.lite_db import get_open_trades, get_pullback_items_by_status
        open_trades = await get_open_trades()
        waiting_pullbacks = await get_pullback_items_by_status("WAITING")
        
        open_symbols = set([t['symbol'] for t in open_trades])
        waiting_symbols = set([p['symbol'] for p in waiting_pullbacks])
        
        return {
            "open_trades": open_trades,
            "waiting_pullbacks": waiting_pullbacks,
            "open_symbols": open_symbols,
            "waiting_symbols": waiting_symbols,
            "total_slots_used": len(open_trades) + len(waiting_pullbacks)
        }

    async def can_add_market_position(self, symbol: str, breadth_pct: float, regime: str) -> bool:
        """
        Checks if we can open a new MARKET position for the given symbol.
        """
        exposure = await self.get_current_exposure()
        
        # 1. Symbol already active?
        if symbol in exposure["open_symbols"]:
            logger.info(f"[EXPOSURE] {symbol} already has an OPEN position.")
            return False
            
        # 2. Hard limits based on Market Breadth and Regime
        max_slots = 3
        if breadth_pct > 75:
            max_slots = 5
        elif breadth_pct < 25 or regime in ["BEAR_MARKET", "DISTRIBUTION"]:
            max_slots = 1
            
        if exposure["total_slots_used"] >= max_slots:
            logger.info(f"[EXPOSURE] Max slots reached ({exposure['total_slots_used']}/{max_slots}). Cannot add {symbol}.")
            return False
            
        # 3. Sector Limits (Risk Diversification)
        if not await self.check_sector_limit(symbol, exposure["open_trades"], exposure["waiting_pullbacks"]):
            return False

        return True

    async def check_sector_limit(self, symbol: str, open_trades: List[dict], waiting_pullbacks: List[dict]) -> bool:
        """
        Ensures we don't overexpose to a single sector (e.g., max 2 MEME coins).
        """
        sector_map = getattr(self.config.trading, 'token_sectors', {})
        target_sector = sector_map.get(symbol, "ALT")
        
        if target_sector == "ALT":
            return True # Generic alts don't have strict overlapping limits right now
            
        # Count current exposure in this sector
        sector_count = 0
        for t in open_trades:
            if sector_map.get(t['symbol'], "ALT") == target_sector:
                sector_count += 1
                
        for p in waiting_pullbacks:
            if sector_map.get(p['symbol'], "ALT") == target_sector:
                sector_count += 1
                
        # Define limits
        sector_limits = {
            "MEME": 1,
            "AI": 2,
            "L1": 2,
            "L2": 2,
            "DEFI": 2,
            "GAMEFI": 1
        }
        
        max_for_sector = sector_limits.get(target_sector, 2)
        if sector_count >= max_for_sector:
            logger.info(f"[EXPOSURE] Sector {target_sector} limit reached ({sector_count}/{max_for_sector}). Skipping {symbol}.")
            return False
            
        return True
