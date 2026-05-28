import asyncio
import ccxt.pro as ccxtpro
import structlog
from typing import Dict, Any, Callable
import time

logger = structlog.get_logger(__name__)

class ExchangeWSManager:
    """
    WebSocket Engine for APEX v5.0
    Maintains a continuous connection to Binance via ccxt.pro for real-time 
    orderbook and ticker data.
    """
    def __init__(self, exchange_id: str = 'mexc'):
        self.exchange_id = exchange_id
        exchange_class = getattr(ccxtpro, self.exchange_id)
        self.exchange = exchange_class({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        self.active_subscriptions: Dict[str, bool] = {}
        self.running = False
        self.callbacks: Dict[str, Callable] = {}
        
    def register_callback(self, symbol: str, callback: Callable):
        """Register a callback function to handle incoming WS data for a symbol."""
        self.callbacks[symbol] = callback
        
    async def watch_ticker_loop(self, symbol: str):
        """Background loop to continuously watch a ticker."""
        self.active_subscriptions[symbol] = True
        logger.info(f"Started WebSocket subscription for {symbol}")
        
        consecutive_errors = 0
        while self.running and self.active_subscriptions.get(symbol):
            try:
                # CCXT Pro handles reconnects automatically
                ticker = await self.exchange.watch_ticker(symbol)
                consecutive_errors = 0
                
                # If we have a callback registered, push the data to it
                if symbol in self.callbacks:
                    await self.callbacks[symbol](symbol, ticker)
                    
            except ccxtpro.NetworkError as e:
                consecutive_errors += 1
                if "451" in str(e):
                    logger.error(f"🚨 Binance GEO-BLOCK (451) on {symbol}. Server must be in EU (Amsterdam).")
                    await asyncio.sleep(60) # Sleep longer if geo-blocked
                else:
                    logger.warning(f"WS Network Error on {symbol}. Attempt {consecutive_errors}")
                    await asyncio.sleep(min(2 ** consecutive_errors, 60))
            except Exception as e:
                logger.error(f"WS Error on {symbol}: {e}")
                await asyncio.sleep(5)
                
    async def start(self, symbols: list[str]):
        """Starts WebSocket loops for all provided symbols."""
        self.running = True
        logger.info(f"Initializing WebSockets for {len(symbols)} symbols...")
        
        # We start a separate task for each symbol to ensure one slow symbol 
        # doesn't block the others
        for symbol in symbols:
            asyncio.create_task(self.watch_ticker_loop(symbol))
            await asyncio.sleep(0.1) # Stagger connections slightly
            
    async def stop(self):
        """Stops all WebSockets and cleans up."""
        self.running = False
        self.active_subscriptions.clear()
        if self.exchange:
            await self.exchange.close()
        logger.info("WebSocket Manager shutdown complete.")
