"""
Guard mechanisms for APEX to prevent duplicate trades and invalid data.
"""
from typing import Set
import structlog
from database.timescaledb import get_open_shadow_trades

logger = structlog.get_logger(__name__)

class OpenPositionGuard:
    def __init__(self):
        # Cache of currently open symbols to provide O(1) lookup
        self._open_symbols: Set[str] = set()
    
    async def load_from_db(self):
        """
        Loads all currently open shadow trades from the database to initialize the guard.
        Should be called on startup.
        """
        try:
            open_trades = await get_open_shadow_trades()
            self._open_symbols = {t['symbol'] for t in open_trades if t['status'] != 'BLOCKED'}
            logger.info(f"OpenPositionGuard initialized. Tracking {len(self._open_symbols)} active symbols.")
        except Exception as e:
            logger.error(f"Failed to load open positions for guard: {e}")
            
    def is_open(self, symbol: str) -> bool:
        """Returns True if there is already an active position for this symbol."""
        return symbol in self._open_symbols
    
    def on_trade_opened(self, symbol: str):
        """Registers a newly opened trade."""
        self._open_symbols.add(symbol)
        
    def on_trade_closed(self, symbol: str):
        """Removes a closed trade from the cache."""
        self._open_symbols.discard(symbol)
