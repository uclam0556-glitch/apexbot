"""
APEX Dynamic Exit Engine v2 (audit-fixes-v11.1)

Single owner of the open-trade lifecycle:
    entry -> partial TP -> break-even -> adaptive trailing -> exit reason -> realized PnL

Design is calibrated on 7,991 resolved production trades (2026-06-07..08 export):
  - median trade MFE was +2.4..2.9% while TP1 sat at +4..5% -> only 12% reached TP;
  - partial 40-50% at ~+1.2-1.5% with a break-even stop and a runner produced
    +0.48%/trade vs -0.44% for the legacy "all-or-nothing TP1" logic.

Every closing write goes through update_exit_engine_state(), which also syncs
signals.status so ExposureManager never counts phantom open slots.
"""

import asyncio
import logging
from datetime import datetime, timezone

from database.timescaledb import get_tracking_shadow_trades, update_exit_engine_state
from shared.state import global_state
from shared.config import get_config

logger = logging.getLogger("ExitEngine")

# ─── Exit policy (calibrated on shadow_trades export, see audit report §13) ──────
PARTIAL_TRIGGER_MFE_PCT = 1.2     # book partial profit once MFE reaches this
PARTIAL_SIZE_PCT = 40.0           # % of position closed at the partial
BREAKEVEN_TRIGGER_MFE_PCT = 0.8   # move stop to entry after this MFE
TRAIL_ACTIVATION_MFE_PCT = 1.5    # start trailing after this MFE
TRAIL_MIN_DISTANCE_PCT = 0.6      # never trail tighter than this (alt noise floor)
TRAIL_RATIO_OF_MFE = 0.35         # trail distance = max(floor, ratio * MFE)
RUNNER_HARD_TP_PCT = 4.0          # absolute cap for the runner leg
TREND_HARD_TIMEOUT_H = 8.0        # unconditional time stop for TREND trades
DECAY_TIMEOUT_H = 6.0             # close if barely profitable after this long
DECAY_MIN_PNL_PCT = 0.5           # "barely profitable" threshold for decay exit
WS_PRICE_MAX_AGE_S = 120.0        # ignore live prices staler than this

TERMINAL_WIN = "CLOSED_WON"
TERMINAL_LOSS = "CLOSED_LOST"
TERMINAL_BE = "CLOSED_BREAKEVEN"


def _extract_live_price(sym: str) -> float | None:
    """live_prices values are dicts: {'price': float, 'timestamp': float}."""
    data = global_state.live_prices.get(sym)
    if data is None:
        from shared.symbols import normalize_symbol
        data = global_state.live_prices.get(normalize_symbol(sym))
    if isinstance(data, dict):
        price = data.get("price")
        ts = data.get("timestamp", 0) or 0
        if price and ts:
            age = datetime.now(timezone.utc).timestamp() - float(ts)
            if age > WS_PRICE_MAX_AGE_S:
                return None
        return float(price) if price else None
    if isinstance(data, (int, float)) and data > 0:
        return float(data)
    return None


class ExitEngine:
    def __init__(self):
        self.running = False
        self.config = get_config()
        self.bot = None
        self.chat_id = None

        try:
            token = self.config.alerts.telegram_bot_token.get_secret_value()
            chat_id_str = self.config.alerts.telegram_chat_id  # plain str, NOT SecretStr
            if token and chat_id_str:
                from aiogram import Bot
                self.bot = Bot(token=token)
                self.chat_id = int(chat_id_str)
        except Exception as e:
            logger.warning(f"[EXIT_ENGINE] Telegram not configured: {e}")

    async def start(self):
        self.running = True
        logger.info("[EXIT_ENGINE] Starting Dynamic Exit Engine v2...")
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
            await asyncio.sleep(10)

    async def _manage_trades(self):
        trades = await get_tracking_shadow_trades()
        if not trades:
            return

        for t in trades:
            # Blocked counterfactuals live in shadow_trades_blocked and are resolved
            # by ShadowTradeMonitor — their ids must never be written to shadow_trades.
            if t.get('src') == 'blocked':
                continue
            try:
                await self._manage_one(t)
            except Exception as e:
                # One broken trade must never stall the whole book
                logger.error(f"[EXIT_ENGINE] Failed to manage {t.get('symbol')}: {e}", exc_info=True)

    async def _manage_one(self, t):
        sym = t['symbol']
        live_price = _extract_live_price(sym)
        if not live_price:
            return

        entry = t['entry_price']
        direction = t['direction']
        sl = t['stop_loss']
        tp1 = t['take_profit_1']
        trade_id = t['id']
        strategy = t.get('strategy') or 'TREND'
        if not entry or entry <= 0:
            return

        highest = t.get('highest_price_seen') or entry
        lowest = t.get('lowest_price_seen') or entry
        partials = t.get('partial_exit_count') or 0
        size_pct = t.get('remaining_size_pct') if t.get('remaining_size_pct') is not None else 100.0
        breakeven = bool(t.get('breakeven_activated'))
        trail_price = t.get('trailing_stop_price')
        realized = t.get('pnl_pct') or 0.0  # realized_pnl_pct booked so far (weighted)

        highest = max(highest, live_price)
        lowest = min(lowest, live_price)

        if direction == 'LONG':
            pnl_pct = (live_price - entry) / entry * 100
            mfe_pct = (highest - entry) / entry * 100
            mae_pct = (lowest - entry) / entry * 100
        else:
            pnl_pct = (entry - live_price) / entry * 100
            mfe_pct = (entry - lowest) / entry * 100
            mae_pct = (entry - highest) / entry * 100

        # Active protective stop: trailing > breakeven > original SL
        if trail_price:
            curr_sl = trail_price
        elif breakeven:
            curr_sl = entry
        else:
            curr_sl = sl

        age_hours = 0.0
        created_at = t.get('created_at')
        if created_at:
            if hasattr(created_at, 'tzinfo') and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600

        # ─── EXIT CHECKS (full close of remaining size) ─────────────────────────
        exit_reason = None

        stop_hit = (direction == 'LONG' and live_price <= curr_sl) or \
                   (direction == 'SHORT' and live_price >= curr_sl)
        if stop_hit:
            exit_reason = 'TRAILING_STOP' if trail_price else ('BREAKEVEN' if breakeven else 'STOP_LOSS')

        if not exit_reason and tp1 and tp1 > 0:
            runner_tp = tp1
            # Cap the runner leg at a realistic distance (median MFE is ~2.5-3%)
            cap = entry * (1 + RUNNER_HARD_TP_PCT / 100) if direction == 'LONG' else entry * (1 - RUNNER_HARD_TP_PCT / 100)
            runner_tp = min(runner_tp, cap) if direction == 'LONG' else max(runner_tp, cap)
            if (direction == 'LONG' and live_price >= runner_tp) or \
               (direction == 'SHORT' and live_price <= runner_tp):
                exit_reason = 'TAKE_PROFIT'

        # Momentum decay: open too long with nothing to show for it
        if not exit_reason and age_hours > DECAY_TIMEOUT_H and pnl_pct < DECAY_MIN_PNL_PCT:
            exit_reason = 'MOMENTUM_DECAY'

        # Unconditional time stop for TREND (previously trades at +1.3% lived forever)
        if not exit_reason and strategy == 'TREND' and age_hours > TREND_HARD_TIMEOUT_H:
            exit_reason = 'TIME_STOP'

        if exit_reason:
            # Weighted realized PnL: partials already booked + remaining size at current price
            total_realized = realized + pnl_pct * (size_pct / 100.0)
            if total_realized > 0.1:
                status = TERMINAL_WIN
            elif total_realized < -0.1:
                status = TERMINAL_LOSS
            else:
                status = TERMINAL_BE

            logger.info(
                f"[EXIT_ENGINE] {sym} {direction} CLOSED ({exit_reason}). "
                f"Leg PnL: {pnl_pct:+.2f}% x {size_pct:.0f}% | Total realized: {total_realized:+.2f}% "
                f"| MFE: {mfe_pct:+.2f}% | MAE: {mae_pct:+.2f}% | Age: {age_hours:.1f}h"
            )
            await update_exit_engine_state(trade_id, {
                "status": status,
                "exit_reason": exit_reason,
                "realized_pnl_pct": total_realized,
                "pnl_pct": total_realized,
                "unrealized_pnl_pct": 0.0,
                "remaining_size_pct": 0.0,
                "highest_price_seen": highest,
                "lowest_price_seen": lowest,
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
                "bars_to_outcome": int(age_hours * 60),
            })

            if self.bot and self.chat_id:
                from services.notifications.telegram_ui import send_trade_result_notification
                trade_dict = dict(t)
                trade_dict.update({'symbol': sym, 'direction': direction, 'entry_price': entry,
                                   'stop_loss': sl, 'take_profit_3': tp1})
                asyncio.create_task(send_trade_result_notification(
                    self.bot, self.chat_id, trade_dict, status, total_realized, mfe_pct, exit_reason
                ))
            return

        # ─── DYNAMIC MANAGEMENT (trade stays open) ──────────────────────────────
        update_db = False
        notify_update = None

        # 1. Partial TP — book REAL realized PnL for the closed fraction
        if partials == 0 and mfe_pct >= PARTIAL_TRIGGER_MFE_PCT and pnl_pct > 0:
            booked = pnl_pct * (PARTIAL_SIZE_PCT / 100.0)
            realized += booked
            partials += 1
            size_pct -= PARTIAL_SIZE_PCT
            update_db = True
            notify_update = (
                f"Частичный фиксаж: закрыто {PARTIAL_SIZE_PCT:.0f}% по +{pnl_pct:.2f}% "
                f"(забукировано +{booked:.2f}%)"
            )
            logger.info(f"[EXIT_ENGINE] {sym} partial TP: {PARTIAL_SIZE_PCT:.0f}% @ +{pnl_pct:.2f}% -> booked {booked:+.2f}%")

        # 2. Break-even after the position has proven itself
        if not breakeven and mfe_pct >= BREAKEVEN_TRIGGER_MFE_PCT:
            breakeven = True
            update_db = True
            if not notify_update:
                notify_update = "Стоп переведен в безубыток (Break-Even) 🛡"
            logger.info(f"[EXIT_ENGINE] {sym} stop moved to breakeven")

        # 3. Adaptive trailing: distance widens with MFE so winners can breathe,
        #    but never tighter than the alt-noise floor.
        if mfe_pct >= TRAIL_ACTIVATION_MFE_PCT:
            trail_distance_pct = max(TRAIL_MIN_DISTANCE_PCT, TRAIL_RATIO_OF_MFE * mfe_pct)
            if direction == 'LONG':
                new_trail = highest * (1 - trail_distance_pct / 100.0)
                if not trail_price or new_trail > trail_price:
                    trail_price = new_trail
                    update_db = True
            else:
                new_trail = lowest * (1 + trail_distance_pct / 100.0)
                if not trail_price or new_trail < trail_price:
                    trail_price = new_trail
                    update_db = True

        prev_unrealized = t.get('unrealized_pnl_pct')
        if update_db or prev_unrealized is None or abs((prev_unrealized or 0.0) - pnl_pct) > 0.05:
            open_state = "PARTIAL_TP" if partials > 0 else ("TRAILING" if trail_price else ("BREAKEVEN" if breakeven else "OPEN"))
            await update_exit_engine_state(trade_id, {
                "status": open_state,
                "unrealized_pnl_pct": pnl_pct,
                "realized_pnl_pct": realized,
                "pnl_pct": realized,
                "highest_price_seen": highest,
                "lowest_price_seen": lowest,
                "partial_exit_count": partials,
                "remaining_size_pct": size_pct,
                "breakeven_activated": breakeven,
                "trailing_stop_price": trail_price,
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            })

        if notify_update and self.bot and self.chat_id:
            from services.notifications.telegram_ui import send_trade_update_notification
            asyncio.create_task(send_trade_update_notification(
                self.bot, self.chat_id, sym, direction, pnl_pct, mfe_pct, notify_update, size_pct
            ))
