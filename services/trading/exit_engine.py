import asyncio
import logging
from datetime import datetime, timedelta, timezone

from database.timescaledb import get_tracking_shadow_trades, update_exit_engine_state
from shared.state import global_state
from shared.config import get_config

logger = logging.getLogger("ExitEngine")

class ExitEngine:
    def __init__(self):
        self.running = False
        self.config = get_config()
        self.bot = None
        self.chat_id = None
        
        # Pull telegram config for notifications
        try:
            token = self.config.alerts.telegram_bot_token.get_secret_value()
            chat_id_str = self.config.alerts.telegram_chat_id.get_secret_value()
            if token and chat_id_str:
                from aiogram import Bot
                self.bot = Bot(token=token)
                self.chat_id = int(chat_id_str)
        except Exception as e:
            logger.warning(f"[EXIT_ENGINE] Telegram not configured: {e}")

    async def start(self):
        self.running = True
        logger.info("[EXIT_ENGINE] Starting Institutional Exit Engine...")
        asyncio.create_task(self._loop())

    async def stop(self):
        self.running = False
        if self.bot:
            await self.bot.session.close()

    async def _loop(self):
        while self.running:
            try:
                await self._manage_trades()
            except Exception as e:
                logger.error(f"[EXIT_ENGINE] Error in loop: {e}", exc_info=True)
            await asyncio.sleep(10)  # Check every 10 seconds for real-time trailing

    async def _manage_trades(self):
        trades = await get_tracking_shadow_trades()
        if not trades:
            return
            
        for t in trades:
            sym = t['symbol']
            live_price = global_state.live_prices.get(sym)
            if not live_price:
                continue
                
            entry = t['entry_price']
            direction = t['direction']
            sl = t['stop_loss']
            tp1 = t['take_profit_1']
            trade_id = t['id']
            
            # Use original strategy logic or default to TREND
            strategy = t.get('strategy', 'TREND')
            
            highest = t.get('highest_price_seen') or entry
            lowest = t.get('lowest_price_seen') or entry
            partials = t.get('partial_exit_count') or 0
            size_pct = t.get('remaining_size_pct') or 100.0
            breakeven = t.get('breakeven_activated') or False
            trail_price = t.get('trailing_stop_price')
            
            # --- MFE / MAE / PnL Calculations ---
            if direction == 'LONG':
                pnl_pct = (live_price - entry) / entry * 100
                mfe_pct = (highest - entry) / entry * 100
                mae_pct = (lowest - entry) / entry * 100
                highest = max(highest, live_price)
                lowest = min(lowest, live_price)
                # Current active stop
                curr_sl = trail_price if trail_price else (entry if breakeven else sl)
            else:
                pnl_pct = (entry - live_price) / entry * 100
                mfe_pct = (entry - lowest) / entry * 100
                mae_pct = (entry - highest) / entry * 100
                highest = max(highest, live_price)
                lowest = min(lowest, live_price)
                # Current active stop
                curr_sl = trail_price if trail_price else (entry if breakeven else sl)

            # --- EXIT LOGIC ---
            status = t['outcome']
            exit_reason = None
            update_db = False
            notify_update = None
            
            # 1. Stop Loss / Trailing Stop / Break-Even Hit
            if direction == 'LONG' and live_price <= curr_sl:
                exit_reason = 'TRAILING_STOP' if trail_price else ('BREAKEVEN' if breakeven else 'STOP_LOSS')
            elif direction == 'SHORT' and live_price >= curr_sl:
                exit_reason = 'TRAILING_STOP' if trail_price else ('BREAKEVEN' if breakeven else 'STOP_LOSS')
                
            # 2. Hard Take Profit Hit
            if not exit_reason:
                if direction == 'LONG' and live_price >= tp1:
                    exit_reason = 'TAKE_PROFIT'
                elif direction == 'SHORT' and live_price <= tp1:
                    exit_reason = 'TAKE_PROFIT'

            # 3. Dynamic Momentum Decay Timeout
            # If trade has been open for > N hours and MFE dropped significantly
            if not exit_reason and t.get('created_at'):
                created_at = t['created_at']
                if hasattr(created_at, 'tzinfo') and created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
                
                # If we are over 6 hours and barely profitable, close it
                if age_hours > 6.0 and pnl_pct < 0.5:
                    exit_reason = 'MOMENTUM_DECAY'

            # Process Exit
            if exit_reason:
                status = 'CLOSED_WON' if ('PROFIT' in exit_reason or 'TRAIL' in exit_reason or pnl_pct > 0) else 'CLOSED_LOST'
                if exit_reason == 'BREAKEVEN': status = 'CLOSED_BREAKEVEN'
                
                logger.info(f"[EXIT_ENGINE] {sym} {direction} Closed. Reason: {exit_reason}. PnL: {pnl_pct:+.2f}%")
                await update_exit_engine_state(trade_id, {
                    "outcome": status,
                    "exit_reason": exit_reason,
                    "realized_pnl_pct": pnl_pct,
                    "unrealized_pnl_pct": 0.0,
                    "remaining_size_pct": 0.0,
                    "highest_price_seen": highest,
                    "lowest_price_seen": lowest
                })
                
                if self.bot and self.chat_id:
                    from services.notifications.telegram_ui import send_trade_result_notification
                    trade_dict = dict(t)
                    trade_dict['symbol'] = sym
                    trade_dict['direction'] = direction
                    trade_dict['entry_price'] = entry
                    trade_dict['stop_loss'] = sl
                    trade_dict['take_profit_3'] = tp1
                    asyncio.create_task(send_trade_result_notification(
                        self.bot, self.chat_id, trade_dict, status, pnl_pct, mfe_pct, exit_reason
                    ))
                continue

            # --- DYNAMIC MANAGEMENT (Trade remains open) ---

            # 4. Partial TP Logic
            if partials == 0 and mfe_pct >= 1.0:  # 1% Quick Profit or 0.8R
                logger.info(f"[EXIT_ENGINE] Partial TP triggered: {sym} closed 40% at +{pnl_pct:.2f}%")
                partials += 1
                size_pct -= 40.0
                update_db = True
                notify_update = f"Частичный фиксаж (TP0). Закрыто 40% позиции по +{pnl_pct:.2f}%"
                
            # 5. Break-even Logic
            if not breakeven and mfe_pct >= 0.8:
                logger.info(f"[EXIT_ENGINE] Stop moved to breakeven: {sym}")
                breakeven = True
                update_db = True
                notify_update = "Стоп переведен в БУ (Break-Even) 🛡"
                
            # 6. Trailing Stop Activation (Tighten stop as price moves favorably)
            if mfe_pct >= 1.5:
                trail_distance_pct = 0.5  # Trail 0.5% behind the highest point
                if direction == 'LONG':
                    new_trail = highest * (1 - (trail_distance_pct / 100.0))
                    if not trail_price or new_trail > trail_price:
                        trail_price = new_trail
                        update_db = True
                else:
                    new_trail = lowest * (1 + (trail_distance_pct / 100.0))
                    if not trail_price or new_trail < trail_price:
                        trail_price = new_trail
                        update_db = True
            
            # Ensure DB is updated on meaningful changes
            # Also update unrealized PnL periodically
            if update_db or t['unrealized_pnl_pct'] != pnl_pct:
                await update_exit_engine_state(trade_id, {
                    "unrealized_pnl_pct": pnl_pct,
                    "highest_price_seen": highest,
                    "lowest_price_seen": lowest,
                    "partial_exit_count": partials,
                    "remaining_size_pct": size_pct,
                    "breakeven_activated": breakeven,
                    "trailing_stop_price": trail_price,
                    "outcome": "PARTIAL_TP" if partials > 0 else ("TRAILING" if trail_price else ("BREAKEVEN" if breakeven else "OPEN"))
                })
                
            # Send Telegram update if a major event occurred
            if notify_update and self.bot and self.chat_id:
                from services.notifications.telegram_ui import send_trade_update_notification
                asyncio.create_task(send_trade_update_notification(
                    self.bot, self.chat_id, sym, direction, pnl_pct, mfe_pct, notify_update, size_pct
                ))
