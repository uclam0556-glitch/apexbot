import asyncio
import logging
from datetime import datetime, timedelta
import aiosqlite
import ccxt.async_support as ccxt
import json

from database.timescaledb import get_tracking_shadow_trades, update_shadow_trade_status, get_pool
from shared.config import get_config

logger = logging.getLogger("ShadowMonitor")

class ShadowTradeMonitor:
    def __init__(self):
        self.running = False
        config = get_config()
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot'
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
                except Exception as ind_err:
                    logger.warning(f"Individual fetch failed for {sym}. Adding to invalid cache.")
                    self.invalid_symbol_cache.add(sym)
            
        now = datetime.utcnow()
        
        for t in trades:
            sym = t['symbol']
            strategy = t.get('strategy', 'TREND')
            created_at = t['created_at']
            if not created_at:
                continue
                
            if isinstance(created_at, str):
                try:
                    created_at = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                except:
                    continue
                    
            if hasattr(created_at, 'tzinfo') and created_at.tzinfo is not None:
                created_at = created_at.replace(tzinfo=None)
                
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
            
            status = 'TRACKING'
            mfe = 0.0
            mae = 0.0
            pnl_pct = 0.0
            duration_bars = 0
            
            try:
                since_ms = int(created_at.timestamp() * 1000)
                # Fetch 1m candles since creation to reconstruct exact price path
                ohlcv = await self.exchange.fetch_ohlcv(sym, '1m', since=since_ms, limit=1000)
                if ohlcv:
                    for candle in ohlcv:
                        c_ts    = candle[0]
                        c_open  = candle[1]
                        c_high  = candle[2]
                        c_low   = candle[3]
                        c_close = candle[4]
                        
                        duration_bars = int((c_ts - since_ms) / 60000)
                        
                        # [SENIOR QUANT FIX 1] Filter out pre-entry wicks in the entry minute
                        if c_ts <= since_ms < c_ts + 60000:
                            if direction == 'LONG':
                                c_low = max(c_low, entry)
                            else:
                                c_high = min(c_high, entry)
                        
                        # MFE: maximum favourable excursion — always POSITIVE (upside reached)
                        # MAE: maximum adverse excursion — always NEGATIVE (downside hit)
                        if direction == 'LONG':
                            cur_mfe = (c_high - entry) / entry * 100       # positive
                            cur_mae = (c_low  - entry) / entry * 100       # negative
                        else:
                            cur_mfe = (entry - c_low)  / entry * 100       # positive
                            cur_mae = (entry - c_high) / entry * 100       # negative (inverted)
                            
                        prev_mfe = mfe
                        if cur_mfe > mfe: mfe = cur_mfe          # track peak MFE
                        if cur_mae < mae: mae = cur_mae          # track worst MAE (most negative)
                        
                        # [SENIOR QUANT FIX 2] Intra-candle Path-Dependent SL/TP resolution
                        if direction == 'LONG':
                            sl_hit = c_low <= sl
                            tp_hit = c_high >= tp1
                            be_hit = prev_mfe >= 1.0 and c_low <= entry
                            
                            if sl_hit and tp_hit:
                                sl_dist = abs(c_open - sl) / entry
                                tp_dist = abs(c_open - tp1) / entry
                                if sl_dist < tp_dist:
                                    status = 'LOST'
                                    mae = min(mae, (sl - entry) / entry * 100)
                                    break
                                else:
                                    pnl_pct = (tp1 - entry) / entry * 100
                                    status = 'WON' if pnl_pct >= 1.0 else 'BREAKEVEN'
                                    break
                            elif be_hit and tp_hit:
                                sl_dist = abs(c_open - entry) / entry
                                tp_dist = abs(c_open - tp1) / entry
                                if sl_dist < tp_dist:
                                    status = 'WON_BREAKEVEN'
                                    break
                                else:
                                    pnl_pct = (tp1 - entry) / entry * 100
                                    status = 'WON' if pnl_pct >= 1.0 else 'BREAKEVEN'
                                    break
                            elif tp_hit:
                                pnl_pct = (tp1 - entry) / entry * 100
                                status = 'WON' if pnl_pct >= 1.0 else 'BREAKEVEN'
                                break
                            elif sl_hit:
                                status = 'LOST'
                                mae = min(mae, (sl - entry) / entry * 100)
                                break
                            elif be_hit:
                                status = 'WON_BREAKEVEN'
                                break

                        elif direction == 'SHORT':
                            sl_hit = c_high >= sl
                            tp_hit = c_low <= tp1
                            be_hit = prev_mfe >= 1.0 and c_high >= entry
                            
                            if sl_hit and tp_hit:
                                sl_dist = abs(c_open - sl) / entry
                                tp_dist = abs(c_open - tp1) / entry
                                if sl_dist < tp_dist:
                                    status = 'LOST'
                                    mae = min(mae, (entry - sl) / entry * 100)
                                    break
                                else:
                                    pnl_pct = (entry - tp1) / entry * 100
                                    status = 'WON' if pnl_pct >= 1.0 else 'BREAKEVEN'
                                    break
                            elif be_hit and tp_hit:
                                sl_dist = abs(c_open - entry) / entry
                                tp_dist = abs(c_open - tp1) / entry
                                if sl_dist < tp_dist:
                                    status = 'WON_BREAKEVEN'
                                    break
                                else:
                                    pnl_pct = (entry - tp1) / entry * 100
                                    status = 'WON' if pnl_pct >= 1.0 else 'BREAKEVEN'
                                    break
                            elif tp_hit:
                                pnl_pct = (entry - tp1) / entry * 100
                                status = 'WON' if pnl_pct >= 1.0 else 'BREAKEVEN'
                                break
                            elif sl_hit:
                                status = 'LOST'
                                mae = min(mae, (entry - sl) / entry * 100)
                                break
                            elif be_hit:
                                status = 'WON_BREAKEVEN'
                                break
                                
            except Exception as e:
                logger.debug(f"Could not fetch OHLCV for path-dependency check on {sym}: {e}")
                
            if status == 'TRACKING' and now - created_at > timeout_delta:
                status = 'TIMEOUT'
                
            if duration_bars == 0:
                duration_bars = int((now - created_at).total_seconds() / 60)
                
            if status != 'TRACKING':
                await update_shadow_trade_status(t['id'], status, mfe, mae, duration_bars, pnl_pct)
                logger.info(f"[SHADOW TRADE] {sym} {direction} [{strategy}] resolved as {status}. MFE: +{mfe:.2f}%, MAE: {mae:.2f}%, PnL: {pnl_pct:.2f}%")
            else:
                # Still tracking, but update MFE/MAE in DB so we can see it on the dashboard
                pool = await get_pool()
                async with pool.acquire() as conn:
                    await conn.execute('''
                        UPDATE shadow_trades 
                        SET mfe_pct = $1, mae_pct = $2, bars_to_outcome = $3
                        WHERE id = $4
                    ''', mfe, mae, duration_bars, t['id'])

