import asyncio
import logging
from datetime import datetime, timedelta
import aiosqlite
import ccxt.async_support as ccxt
import json

from shared.lite_db import get_tracking_shadow_trades, update_shadow_trade_status
from shared.config import get_config

logger = logging.getLogger("ShadowMonitor")

class ShadowTradeMonitor:
    def __init__(self):
        self.running = False
        config = get_config()
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future'
            }
        })

    async def start(self):
        self.running = True
        logger.info("Starting Shadow Trade Monitor...")
        asyncio.create_task(self._loop())

    async def stop(self):
        self.running = False
        await self.exchange.close()

    async def _loop(self):
        while self.running:
            try:
                await self._check_shadow_trades()
            except Exception as e:
                logger.error(f"Error in ShadowMonitor loop: {e}")
            await asyncio.sleep(60)  # Check once per minute

    async def _check_shadow_trades(self):
        trades = await get_tracking_shadow_trades()
        if not trades:
            return
            
        # Fetch markets if not loaded
        if not self.exchange.markets:
            try:
                await self.exchange.load_markets()
            except Exception as e:
                logger.error(f"Failed to load markets in ShadowMonitor: {e}")
                return

        # Filter symbols that exist in exchange markets
        valid_symbols = []
        from shared.symbols import is_symbol_supported
        
        if not hasattr(self, "invalid_symbol_cache"):
            self.invalid_symbol_cache = set()
            
        for symbol in list(set([t['symbol'] for t in trades])):
            if is_symbol_supported(symbol, self.exchange.markets):
                valid_symbols.append(symbol)
            else:
                if symbol not in self.invalid_symbol_cache:
                    logger.warning(f"ShadowMonitor: Symbol {symbol} not supported by {self.exchange.id}. Skipping.")
                    self.invalid_symbol_cache.add(symbol)
                
        if not valid_symbols:
            return
            
        tickers = {}
        try:
            tickers = await self.exchange.fetch_tickers(valid_symbols)
        except Exception as e:
            logger.warning(f"Batch fetch_tickers failed in ShadowMonitor: {e}. Falling back to individual fetches.")
            for sym in valid_symbols:
                try:
                    ticker = await self.exchange.fetch_ticker(sym)
                    tickers[sym] = ticker
                except Exception:
                    pass
            
        now = datetime.utcnow()
        
        for t in trades:
            sym = t['symbol']
            strategy = t.get('strategy', 'TREND')
            created_str = t['created_at']
            if not created_str:
                continue
                
            try:
                created_at = datetime.strptime(created_str, "%Y-%m-%d %H:%M:%S")
            except:
                continue
                
            # Dynamic Timeout based on Strategy
            timeout_hours = 6
            if strategy == 'MEAN_REVERSION':
                timeout_hours = 2
            elif strategy == 'CAPITULATION':
                timeout_hours = 1
            elif strategy == 'PULLBACK':
                timeout_hours = 6
                
            timeout_delta = timedelta(hours=timeout_hours)
            
            # Fetch live ticker for basic check
            ticker = tickers.get(sym)
            if not ticker or not ticker.get('last'):
                continue
                
            live_price = ticker['last']
            direction = t['direction']
            entry = t['entry_price']
            sl = t['stop_loss']
            tp1 = t['take_profit_1']
            
            # We will approximate MFE/MAE using current price if we don't fetch full OHLCV.
            # A full implementation would fetch OHLCV since created_at upon resolution.
            # Here we just flag WIN/LOSS/TIMEOUT.
            
            status = 'TRACKING'
            if now - created_at > timeout_delta:
                status = 'TIMEOUT'
            else:
                if direction == 'LONG':
                    if live_price <= sl:
                        status = 'LOST'
                    elif live_price >= tp1:
                        status = 'WON'
                elif direction == 'SHORT':
                    if live_price >= sl:
                        status = 'LOST'
                    elif live_price <= tp1:
                        status = 'WON'
            
            if status != 'TRACKING':
                # Basic MFE/MAE approximation at point of resolution
                mfe = 0.0
                mae = 0.0
                if direction == 'LONG':
                    if status == 'WON': mfe = (tp1 - entry) / entry * 100
                    if status == 'LOST': mae = (sl - entry) / entry * 100
                else:
                    if status == 'WON': mfe = (entry - tp1) / entry * 100
                    if status == 'LOST': mae = (entry - sl) / entry * 100
                    
                await update_shadow_trade_status(t['id'], status, mfe, mae)
                logger.info(f"[SHADOW TRADE] {sym} {direction} [{strategy}] resolved as {status}. MFE: {mfe:.2f}%, MAE: {mae:.2f}%")
