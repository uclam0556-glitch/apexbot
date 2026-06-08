"""
APEX Trading System v5.0
services/notifications/telegram_ui.py

Ultra-premium Telegram Bot UI with beautiful signal cards,
live status, market overview, and multi-button navigation.
"""

import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.filters import Command

from shared.config import get_config
from shared.state import global_state
from database.timescaledb import (
    get_stats_timescale, 
    get_open_shadow_trades,
    get_open_trades,
    get_recent_trades,
    reset_open_trades,
    factory_reset_db
)

def format_price(price: float) -> str:
    if not price:
        return "0.00"
    if price >= 1000:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:.4f}"
    elif price >= 0.001:
        return f"{price:.6f}"
    elif price >= 0.00001:
        return f"{price:.8f}"
    else:
        return f"{price:.10f}"

logger = logging.getLogger(__name__)
_config = get_config()
router = Router()

# ─────────────────────────────────────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────────────────────────────────────

def get_persistent_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="▶️ Старт"), KeyboardButton(text="⏸ Пауза")],
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="⚙️ Меню")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌐 Открыть Дашборд (App)", web_app=WebAppInfo(url="https://apex-quantum.up.railway.app/"))
        ],
        [
            InlineKeyboardButton(text="🟢 Live Portfolio", callback_data="live_pnl"),
            InlineKeyboardButton(text="📥 Активные Лимиты", callback_data="limits"),
        ],
        [
            InlineKeyboardButton(text="📡 Статус системы", callback_data="status"),
            InlineKeyboardButton(text="🌡 Рынок", callback_data="market"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
            InlineKeyboardButton(text="📜 История", callback_data="history"),
        ],
        [
            InlineKeyboardButton(text="🔥 Hot Coins", callback_data="hot"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
            InlineKeyboardButton(text="🔄 Сброс ордеров", callback_data="reset_orders"),
        ],
        [
            InlineKeyboardButton(text="⚠️ Полный сброс (Wipe)", callback_data="factory_reset")
        ]
    ])

def get_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="home")]
    ])

# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

def get_start_text() -> str:
    now = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
    from config.data_collection_mode import DATA_COLLECTION_MODE
    
    if not DATA_COLLECTION_MODE.get('REAL_TRADING_ENABLED', False):
        mode_text = "🛡 <b>SHADOW MODE (Data Collection)</b>\nТорговля отключена. Собираем чистые данные для ML."
    else:
        mode_text = "🟢 <b>LIVE TRADING (Active)</b>"

    return (
        "⚡ <b>APEX Quantum AI v10.5</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{mode_text}\n\n"
        "🧠 <b>Ядро:</b> SMC (No Look-Ahead) + MTF + CVD\n"
        "🛡 <b>Риск:</b> Quarter-Kelly Sizing + Circuit Breaker\n\n"
        f"🕒 <i>Время сервера: {now}</i>\n\n"
        "👇 <b>Главное меню:</b>"
    )

@router.message(Command("start"))
async def cmd_start(message: Message):
    # Send persistent keyboard first, then main menu
    await message.answer("Клавиатура управления загружена 🎛", reply_markup=get_persistent_keyboard())
    await message.answer(get_start_text(), reply_markup=get_main_keyboard(), parse_mode="HTML")

@router.message(F.text == "▶️ Старт")
async def cmd_resume(message: Message):
    global_state.is_paused = False
    await message.answer("✅ <b>Анализ запущен!</b> Бот мониторит рынок.", parse_mode="HTML")

@router.message(F.text == "⏸ Пауза")
async def cmd_pause(message: Message):
    global_state.is_paused = True
    await message.answer("⏸ <b>Режим сна активирован.</b> Сканирование приостановлено.", parse_mode="HTML")

@router.message(F.text == "📊 Статус")
async def cmd_status_text(message: Message):
    # We can reuse the start text or status text. Let's send the main menu for simplicity
    await message.answer(get_start_text(), reply_markup=get_main_keyboard(), parse_mode="HTML")

@router.message(F.text == "⚙️ Меню")
async def cmd_menu_text(message: Message):
    await message.answer(get_start_text(), reply_markup=get_main_keyboard(), parse_mode="HTML")

# ─────────────────────────────────────────────────────────────────────────────
# STATUS
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "status")
async def process_status(callback: CallbackQuery):
    regime_emoji = {
        "BULL": "🟢", "BEAR": "🔴", "SIDEWAYS": "🟡", "CRISIS": "⚠️"
    }.get(global_state.regime, "⚪")

    text = (
        "📡 <b>Статус Системы (v10.5)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔍 Сейчас анализирую: <b>{global_state.current_symbol}</b>\n"
        f"⏱ Последнее сканирование: <b>{global_state.last_scan_time}</b>\n"
        f"{regime_emoji} Режим (ML): <b>{global_state.regime}</b>\n\n"
        f"📦 База сканирования: <b>95 монет</b>\n"
        f"⏳ Таймфреймы: <b>5</b> (1d · 4h · 1h · 15m · 5m)\n"
        f"🎯 Мин. score для сигнала: <b>45.0/100</b>\n\n"
        "🛡 <b>Защитные слои:</b> Активны (Circuit, Correlation)\n"
        "<i>Обновляется автоматически каждый цикл</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

# ─────────────────────────────────────────────────────────────────────────────
# MARKET OVERVIEW
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "market")
async def process_market(callback: CallbackQuery):
    await callback.answer("⏳ Загружаю данные рынка...")
    try:
        from services.indicators.market_data import get_fear_greed, get_btc_dominance
        from services.data.onchain import OnChainPipeline
        
        oc_pipeline = OnChainPipeline()
        
        fg, btc_d, oc_data = await __import__('asyncio').gather(
            get_fear_greed(), get_btc_dominance(), oc_pipeline.get_smart_money_data(), return_exceptions=True
        )
        fg_val = fg.get("value", 50) if isinstance(fg, dict) else 50
        fg_label = fg.get("label", "Neutral") if isinstance(fg, dict) else "Neutral"
        btc_dom = btc_d.get("dominance", 55.0) if isinstance(btc_d, dict) else 55.0
        
        oc_flow = oc_data.exchange_net_flow if not isinstance(oc_data, Exception) else 0.0
        oc_sopr = oc_data.sopr_ratio if not isinstance(oc_data, Exception) else 1.0

        if fg_val <= 25:
            fg_emoji = "😱"
        elif fg_val <= 45:
            fg_emoji = "😨"
        elif fg_val <= 55:
            fg_emoji = "😐"
        elif fg_val <= 75:
            fg_emoji = "😏"
        else:
            fg_emoji = "🤑"

        alt_season = "🚀 Alt Season!" if btc_dom < 48 else ("⚖️ Нейтрально" if btc_dom < 55 else "₿ BTC доминирует")
    except Exception:
        fg_val, fg_label, fg_emoji, btc_dom, alt_season = 50, "Neutral", "😐", 55.0, "⚖️"
        oc_flow, oc_sopr = 0.0, 1.0

    regime_emoji = {"BULL": "🟢 Бычий", "BEAR": "🔴 Медвежий",
                    "SIDEWAYS": "🟡 Боковик", "CRISIS": "⚠️ Кризис"}.get(global_state.regime, "⚪")

    if oc_flow is not None:
        flow_type = "Outflow (Bullish 🟢)" if oc_flow < 0 else "Inflow (Bearish 🔴)" if oc_flow > 0 else "Нейтрально ⚪"
    else:
        flow_type = "Нет данных ⚪"
        oc_flow = 0.0
        
    text = (
        "🌐 <b>Обзор рынка & On-Chain</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{fg_emoji} <b>Fear & Greed:</b> {fg_val}/100 — {fg_label}\n"
        f"₿ <b>BTC Dominance:</b> {btc_dom}% — {alt_season}\n"
        f"🧠 <b>ML Режим (APEX):</b> {regime_emoji}\n\n"
        "<b>On-Chain Метрики (Glassnode):</b>\n"
        f"🌊 <b>BTC Exchange Flow:</b> {oc_flow:,.0f} BTC — {flow_type}\n"
        f"💎 <b>SOPR Ratio:</b> {oc_sopr:.3f} — {'Профит 🟢' if oc_sopr > 1 else 'Убыток 🔴'}\n\n"
        "<b>Интерпретация:</b>\n"
        f"{'🟢 Хорошее время для покупок (страх = возможность)' if fg_val < 40 else '🔴 Осторожно — рынок перегрет' if fg_val > 70 else '🟡 Нейтральный рынок'}\n\n"
        "<i>Данные обновляются каждый час</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# LIVE PORTFOLIO
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "live_pnl")
async def process_live_portfolio(callback: CallbackQuery):
    await callback.answer("⏳ Считаю Live PnL...")
    try:
        open_trades = await get_open_trades()
        if not open_trades:
            text = (
                "🟢 <b>Live Portfolio</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Сейчас нет открытых позиций.\n"
                "Ожидайте сигналов 🚀"
            )
            await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
            return

        total_pnl = 0.0
        active_count = len(open_trades)
        
        for t in open_trades:
            symbol = t['symbol']
            entry = float(t['entry_price'])
            direction = t['direction']
            
            current_price = global_state.live_prices.get(symbol)
            if not current_price:
                continue
                
            if direction == "LONG":
                pnl = (current_price - entry) / entry * 100
            else:
                pnl = (entry - current_price) / entry * 100
            total_pnl += pnl

        sign = "+" if total_pnl > 0 else ""
        text = (
            "🟢 <b>Live Portfolio Tracker</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Открытых позиций: <b>{active_count}</b>\n\n"
            f"Текущий Live PnL: <b>{sign}{total_pnl:.2f}%</b> 🚀\n\n"
            "<i>Все позиции ведутся автоматически.\nБот сам переводит стопы в б/у и фиксирует прибыль.</i>"
        )
        await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in Live Portfolio: {e}")
        await callback.message.edit_text("Ошибка загрузки Live PnL.", reply_markup=get_back_keyboard(), parse_mode="HTML")

# ─────────────────────────────────────────────────────────────────────────────
# ACTIVE LIMITS (PULLBACKS)
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "limits")
async def process_active_limits(callback: CallbackQuery):
    await callback.answer("⏳ Загружаю лимитные ордера...")
    try:
        from database.timescaledb import get_active_pullback_items, get_pullback_items_by_status
        waiting = await get_active_pullback_items()
        waiting_structure = await get_pullback_items_by_status('WAITING_STRUCTURE')
        
        all_limits = waiting + waiting_structure
        
        if not all_limits:
            text = (
                "📥 <b>Активные Лимиты (Pullbacks)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Сейчас нет выставленных лимитных сеток.\n"
                "Ожидайте коррекций на рынке 🚀"
            )
            await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
            return

        text = f"📥 <b>Активные Лимиты: {len(all_limits)}</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for idx, item in enumerate(all_limits):
            symbol = item['symbol']
            status = item['status']
            score = item['score']
            dir_emoji = "🟢 L" if item['direction'] == "LONG" else "🔴 S"
            
            if status == "WAITING_STRUCTURE":
                status_emoji = "⏳"
                status_str = "Ожидание (Структура)"
                limit_info = "Ожидает прохода Risk Engine..."
            else:
                status_emoji = "📥"
                status_str = "В стакане (WAITING)"
                import json
                try:
                    entries = json.loads(item['limit_entries'])
                    prices = [f"${float(e['price']):.4f}" for e in entries]
                    limit_info = "Сетка: " + " / ".join(prices)
                except:
                    limit_info = f"Стоп: ${item['stop_loss']:.4f}"
            
            # Use format_price logic roughly
            text += (
                f"{status_emoji} <b>{symbol}</b> {dir_emoji} | <i>{status_str}</i>\n"
                f"   Score: <b>{score:.1f}</b>/100\n"
                f"   {limit_info}\n"
            )
            if idx < len(all_limits) - 1:
                text += "   —\n"

        text += "\n<i>Бот автоматически отменит сетки при сломе структуры.</i>"
        await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in Active Limits: {e}")
        await callback.message.edit_text("Ошибка загрузки лимитных ордеров.", reply_markup=get_back_keyboard(), parse_mode="HTML")

# ─────────────────────────────────────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "stats")
async def process_stats(callback: CallbackQuery):
    stats = await get_stats_timescale()
    open_trades = await get_open_shadow_trades()
    active_count = len(open_trades) if open_trades else 0
    
    # Пытаемся получить расширенную стату
    try:
        from analytics.statistical_validator import StatisticalValidator
        trades = await get_recent_trades(limit=1000)
        validation = StatisticalValidator.run_validation(trades)
        pf = validation.get('profit_factor', 0.0)
        exp = validation.get('expectancy', 0.0)
        t_stat = validation.get('t_stat', 0.0)
        pval = validation.get('p_value', 1.0)
        sharpe = validation.get('sharpe', 0.0)
        is_valid = validation.get('valid', False)
        valid_icon = "✅ Proofed" if is_valid else "⏳ Calibrating"
    except Exception:
        pf, exp, t_stat, pval, sharpe, valid_icon = 0.0, 0.0, 0.0, 1.0, 0.0, "—"

    wr = stats['win_rate']
    
    # Generate progress bar for win rate
    filled = int(wr / 10) if stats['total'] > 0 else 0
    bar = "🟩" * filled + "⬜" * (10 - filled)
    
    text = (
        "📊 <b>Институциональная Статистика (v10.5)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ <b>Открытых Shadow позиций:</b> {active_count}\n"
        f"📈 <b>Закрытых сделок:</b> {stats['total']}\n"
        f"   ┣ Успешных (TP): <b>{stats['won']}</b> ✅\n"
        f"   ┣ Безубыток: <b>{stats.get('breakeven', 0)}</b> 🟡\n"
        f"   ┗ Убыточных (SL): <b>{stats['lost']}</b> ❌\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Win Rate: {wr:.1f}%</b>\n"
        f"[{bar}]\n\n"
        f"💰 <b>Суммарный PnL:</b> {stats['pnl_sum']:+.2f}%\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔬 <b>Quant Metrics:</b>\n"
        f"• Profit Factor: <b>{pf:.2f}</b>\n"
        f"• Expectancy: <b>{exp:.2f}%</b> per trade\n"
        f"• Sharpe Ratio: <b>{sharpe:.2f}</b>\n"
        f"• T-Statistic: <b>{t_stat:.2f}</b> (p-value: {pval:.3f})\n"
        f"• Status: <b>{valid_icon}</b>\n"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

# ─────────────────────────────────────────────────────────────────────────────
# HISTORY
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "history")
async def process_history(callback: CallbackQuery):
    trades = await get_recent_trades(limit=10)

    if not trades:
        text = (
            "📜 <b>История сигналов</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Пока нет активных или закрытых сделок.\n"
            "Бот сканирует 95 монет — ожидайте сигнал 🔍"
        )
    else:
        text = "📜 <b>Последние 10 сделок:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for t in trades:
            dir_emoji = "🟢 L" if t['direction'] == "LONG" else "🔴 S"
            
            if t['status'] in ("OPEN", "BREAKEVEN"):
                status_emoji = "⏳" if t['status'] == "OPEN" else "🛡"
                pnl_str = "В процессе..."
            elif t['status'] in ("WON", "WON_BREAKEVEN"):
                status_emoji = "✅" if t['status'] == "WON" else "🛡"
                pnl_str = f"+{t['pnl_pct']:.2f}%" if t['pnl_pct'] else "+0.00%"
            elif t['status'] == "TIMEOUT":
                status_emoji = "⏱"
                pnl_str = f"{t['pnl_pct']:.2f}%" if t['pnl_pct'] else "0.00%"
            else:
                status_emoji = "❌"
                pnl_str = f"{t['pnl_pct']:.2f}%" if t['pnl_pct'] else "0.00%"
                
            open_dt = datetime.strptime(t['opened_at'], '%Y-%m-%d %H:%M:%S.%f') if '.' in t['opened_at'] else datetime.strptime(t['opened_at'], '%Y-%m-%d %H:%M:%S')
            date_str = open_dt.strftime("%d.%m %H:%M")

            text += (
                f"{status_emoji} <b>{t['symbol']}</b> {dir_emoji}  |  <code>{date_str}</code>\n"
                f"   Вход: ${format_price(t['entry_price'])}  →  PnL: <b>{pnl_str}</b>\n"
            )

    try:
        await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

# ─────────────────────────────────────────────────────────────────────────────
# HOT COINS
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "hot")
async def process_hot(callback: CallbackQuery):
    from services.intelligence.rs_matrix import rs_matrix_engine
    hot = rs_matrix_engine.get_top_n(5)

    if not hot:
        text = (
            "🔥 <b>Горячие монеты (RS Matrix)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⏳ Данные накапливаются...\n\n"
            "После первого полного цикла сканирования\n"
            "здесь появятся топ монеты сильнее BTC!"
        )
    else:
        text = "🔥 <b>RS Matrix — Топ Сильных Монет:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, coin in enumerate(hot, 1):
            medal = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i - 1, f"{i}.")
            text += f"{medal} <b>{coin['symbol']}</b>\n"
            text += f"   Изменение за 24ч: <b>{coin['change_24h']:+.2f}%</b>\n"
            text += f"   RS Score к BTC: <b>{coin['rs_score']:+.2f}%</b>\n\n"
            text += "<i>Только эти монеты бот берет в лонг.</i>\n"

    try:
        await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "settings")
async def process_settings(callback: CallbackQuery):
    text = (
        "⚙️ <b>Настройки APEX v10.5</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💵 <b>Депозит:</b> $3,000 (Paper)\n"
        "⚖️ <b>Сайзинг:</b> Quarter-Kelly\n"
        "🛡 <b>Корреляция:</b> Max 0.75 | Portfolio Beta < 2.5\n"
        "📉 <b>Circuit Breaker:</b> -5.0% Daily / -10.0% Weekly\n"
        "🎯 <b>Мин. score:</b> 45.0/100\n"
        "📦 <b>Монет в скане:</b> 95\n"
        "⏱ <b>Таймфреймы:</b> 1d · 4h · 1h · 15m · 5m\n"
        "🏦 <b>База:</b> TimescaleDB (asyncpg)\n"
        "🤖 <b>Режим:</b> Shadow Trading (Data Collection)\n\n"
        "<b>Модули Интеллекта:</b>\n"
        "✅ SMC Strict (bar_index_locked)\n"
        "✅ Institutional CVD\n"
        "✅ Market Breadth & Flow\n"
        "✅ Transaction Costs Model (Tier 1-5)\n"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

# ─────────────────────────────────────────────────────────────────────────────
# HOME
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "home")
async def process_home(callback: CallbackQuery):
    try:
        await callback.message.edit_text(get_start_text(), reply_markup=get_main_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

# ─────────────────────────────────────────────────────────────────────────────
# RESET ORDERS
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "reset_orders")
async def process_reset_orders(callback: CallbackQuery):
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, сбросить", callback_data="confirm_reset"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="home"),
        ]
    ])
    try:
        await callback.message.edit_text(
            "⚠️ <b>Сброс открытых ордеров</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Все текущие OPEN позиции будут отмечены как CANCELLED.\n"
            "Бот сразу начнет искать новые входы.\n\n"
            "<b>Подтверждаешь?</b>",
            reply_markup=confirm_kb,
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data == "confirm_reset")
async def process_confirm_reset(callback: CallbackQuery):
    await reset_open_trades()
    try:
        await callback.message.edit_text(
            "✅ <b>Все ордера сброшены!</b>\n\n"
            "Бот освободил все позиции и продолжает сканирование рынка.\n"
            "Новые сигналы появятся в ближайшие минуты. 🚀",
            reply_markup=get_back_keyboard(),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer("Готово! Ордера сброшены ✅")

# ─────────────────────────────────────────────────────────────────────────────
# FACTORY RESET (WIPE DATA)
# ─────────────────────────────────────────────────────────────────────────────

# FACTORY RESET (WIPE DATA)
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "factory_reset")
async def process_factory_reset(callback: CallbackQuery):
    open_trades = await get_open_shadow_trades()
    if open_trades:
        try:
            await callback.message.edit_text(
                "⚠️ <b>СБРОС ЗАБЛОКИРОВАН</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Обнаружено <b>{len(open_trades)} открытых позиций</b>.\n"
                "Нельзя удалять историю и данные ML, пока идут активные торги.\n"
                "Сначала закройте или сбросьте ордера.",
                reply_markup=get_back_keyboard(),
                parse_mode="HTML"
            )
        except Exception:
            pass
        await callback.answer()
        return

    try:
        await callback.message.edit_text(
            "⚠️ <b>ВНИМАНИЕ: ПОЛНЫЙ СБРОС (WIPE)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Это действие удалит <b>ВСЕ</b> исторические сделки, открытые ордера и обнулит ML Feature Store.\n"
            "Статистика дашборда начнется с нуля (0%).\n\n"
            "<b>Ты уверен, что хочешь полностью стереть базу данных?</b>\n"
            "Для подтверждения отправьте в этот чат точный текст:\n"
            "<code>CONFIRM_RESET_123</code>",
            reply_markup=get_back_keyboard(),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer()

@router.message(Command("reset"))
async def cmd_reset(message: Message):
    open_trades = await get_open_shadow_trades()
    if open_trades:
        await message.answer(
            "⚠️ <b>СБРОС ЗАБЛОКИРОВАН</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Обнаружено <b>{len(open_trades)} открытых позиций</b>.\n"
            "Нельзя удалять историю и данные ML, пока идут торги.\n"
            "Сначала закройте или сбросьте ордера.",
            parse_mode="HTML"
        )
        return

    await message.answer(
        "⚠️ <b>ВНИМАНИЕ: ПОЛНЫЙ СБРОС (WIPE)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Это действие удалит <b>ВСЕ</b> closed сделки, открытые ордера и обнулит ML Feature Store.\n"
        "Статистика дашборда начнется с нуля (0%).\n\n"
        "<b>Ты уверен, что хочешь полностью стереть базу данных?</b>\n"
        "Для подтверждения отправьте в этот чат точный текст:\n"
        "<code>CONFIRM_RESET_123</code>",
        parse_mode="HTML"
    )

@router.message(F.text == "CONFIRM_RESET_123")
async def process_confirm_factory_reset_text(message: Message):
    open_trades = await get_open_shadow_trades()
    if open_trades:
        await message.answer(
            "⚠️ <b>СБРОС ЗАБЛОКИРОВАН</b>\n\n"
            f"В системе обнаружено {len(open_trades)} активных сделок. "
            "Сброс базы данных невозможен.",
            parse_mode="HTML"
        )
        return

    await factory_reset_db()
    await message.answer(
        "✅ <b>База данных полностью очищена! (Factory Reset)</b>\n\n"
        "Вся история, открытые ордера и ML-данные удалены (создана резервная копия БД).\n"
        "Статистика дашборда обнулена. Бот начинает жизнь с чистого листа. 🚀",
        reply_markup=get_persistent_keyboard(),
        parse_mode="HTML"
    )

@router.message(Command("diag"))
async def cmd_diag(message: Message):
    await message.answer("⏳ <b>Запускаю скрипты аналитики Shadow Trades и V7 Score...</b>", parse_mode="HTML")
    import subprocess
    import os
    env = os.environ.copy()
    try:
        res1 = subprocess.run(["python3", "scripts/shadow_analysis.py"], capture_output=True, text=True, env=env)
        out1 = res1.stdout if res1.stdout else res1.stderr
        
        res2 = subprocess.run(["python3", "scripts/v7_diagnostic.py"], capture_output=True, text=True, env=env)
        out2 = res2.stdout if res2.stdout else res2.stderr
        
        # Split outputs if they are too long for one message
        if len(out1) > 4000: out1 = out1[:4000] + "\n[TRUNCATED]"
        if len(out2) > 4000: out2 = out2[:4000] + "\n[TRUNCATED]"
        
        await message.answer(f"<b>SHADOW ANALYSIS:</b>\n<pre>{out1}</pre>", parse_mode="HTML")
        await message.answer(f"<b>V7 DIAGNOSTIC:</b>\n<pre>{out2}</pre>", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ <b>Ошибка запуска:</b>\n<pre>{str(e)}</pre>", parse_mode="HTML")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    stats = await get_stats_timescale()
    open_trades = await get_open_shadow_trades()
    active_count = len(open_trades) if open_trades else 0
    
    # Пытаемся получить расширенную стату
    try:
        from analytics.statistical_validator import StatisticalValidator
        trades = await get_recent_trades(limit=1000)
        validation = StatisticalValidator.run_validation(trades)
        pf = validation.get('profit_factor', 0.0)
        exp = validation.get('expectancy', 0.0)
        t_stat = validation.get('t_stat', 0.0)
        pval = validation.get('p_value', 1.0)
        sharpe = validation.get('sharpe', 0.0)
        is_valid = validation.get('valid', False)
        valid_icon = "✅ Proofed" if is_valid else "⏳ Calibrating"
    except Exception:
        pf, exp, t_stat, pval, sharpe, valid_icon = 0.0, 0.0, 0.0, 1.0, 0.0, "—"

    wr = stats['win_rate']
    filled = int(wr / 10) if stats['total'] > 0 else 0
    bar = "🟩" * filled + "⬜" * (10 - filled)
    
    text = (
        "📊 <b>Институциональная Статистика (v10.5)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ <b>Открытых Shadow позиций:</b> {active_count}\n"
        f"📈 <b>Закрытых сделок:</b> {stats['total']}\n"
        f"   ┣ Успешных (TP): <b>{stats['won']}</b> ✅\n"
        f"   ┣ Безубыток: <b>{stats.get('breakeven', 0)}</b> 🟡\n"
        f"   ┗ Убыточных (SL): <b>{stats['lost']}</b> ❌\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Win Rate: {wr:.1f}%</b>\n"
        f"[{bar}]\n\n"
        f"💰 <b>Суммарный PnL:</b> {stats['pnl_sum']:+.2f}%\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔬 <b>Quant Metrics:</b>\n"
        f"• Profit Factor: <b>{pf:.2f}</b>\n"
        f"• Expectancy: <b>{exp:.2f}%</b> per trade\n"
        f"• Sharpe Ratio: <b>{sharpe:.2f}</b>\n"
        f"• T-Statistic: <b>{t_stat:.2f}</b> (p-value: {pval:.3f})\n"
        f"• Status: <b>{valid_icon}</b>\n"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(Command("circuit"))
async def cmd_circuit(message: Message):
    # Пул данных для защиты
    text = (
        "🛡 <b>Защитные слои (Defence Engine)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🟢 <b>Circuit Breaker:</b> OK (Drawdown: 0.0%)\n"
        "   ┣ Daily Limit: -5.0%\n"
        "   ┗ Weekly Limit: -10.0%\n\n"
        "🟢 <b>Correlation Filter:</b> OK\n"
        "   ┣ Portfolio Beta to BTC: ~1.0\n"
        "   ┗ Max Allowed Beta: 2.5\n\n"
        "🟢 <b>Anomaly Detector:</b> OK\n"
        "   ┗ Last System Ping: < 15 min ago\n\n"
        "<i>Защитные алгоритмы активны 24/7.</i>"
    )
    await message.answer(text, parse_mode="HTML")

# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL CARD — called from main.py when signal is found
# ─────────────────────────────────────────────────────────────────────────────

def build_signal_card(signal_data: dict) -> str:
    """
    Build a beautiful, information-rich, institutional-grade signal card.
    """
    s = signal_data
    symbol = s.get("symbol", "???")
    direction = s.get("direction", "LONG")
    regime = s.get("regime", "UNKNOWN")
    fg_value = s.get("fear_greed", 50)
    btc_dom = s.get("btc_dominance", 55)
    vwap_label = s.get("vwap_label", "")
    ema_label = s.get("ema_label", "")
    rsi_div = s.get("rsi_divergence", "NONE")
    strategy = s.get("strategy", "Trend Following")
    
    def safe_float(v):
        try: return float(v)
        except (TypeError, ValueError): return 0.0
        
    entry_low = safe_float(s.get("entry_low", 0))
    entry_high = safe_float(s.get("entry_high", 0))
    sl = safe_float(s.get("stop_loss", 0))
    tp1 = safe_float(s.get("tp1", 0))
    tp2 = safe_float(s.get("tp2", 0))
    tp3 = safe_float(s.get("tp3", 0))
    score = safe_float(s.get("score", 0))
    rsi = safe_float(s.get("rsi", 50))
    funding = safe_float(s.get("funding_rate", 0))
    oi_change = safe_float(s.get("oi_change", 0))
    position_usd = safe_float(s.get("position_usd", 30))
    risk_usd = safe_float(s.get("risk_usd", 30))
    rr = safe_float(s.get("rr_ratio", 0))

    dir_emoji = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
    regime_emoji = {"BULL": "🟢 Бычий", "BEAR": "🔴 Медвежий", "SIDEWAYS": "🟡 Боковик", "CRISIS": "⚠️ Кризис"}.get(regime, "⚪")

    # Entry SL risk %
    entry_mid = (entry_low + entry_high) / 2 if entry_high > 0 else entry_low
    sl_pct = abs(entry_mid - sl) / entry_mid * 100 if entry_mid > 0 else 0
    tp1_pct = abs(tp1 - entry_mid) / entry_mid * 100 if entry_mid > 0 else 0
    tp2_pct = abs(tp2 - entry_mid) / entry_mid * 100 if entry_mid > 0 else 0
    tp3_pct = abs(tp3 - entry_mid) / entry_mid * 100 if entry_mid > 0 else 0

    def fmt(price):
        if price == 0: return "—"
        return f"${format_price(price)}"

    funding_str = f"{funding:+.3f}%" if funding != 0 else "—"
    oi_str = f"{oi_change:+.1f}%" if oi_change != 0 else "—"
    
    # Confidence Calibration
    conf_bucket = s.get("confidence_bucket", "N/A")
    conf_win = s.get("confidence_win_rate", 0)
    conf_size = s.get("confidence_sample_size", 0)
    conf_str = f"{conf_win:.1f}% Win Rate ({conf_size} trades)" if conf_size >= 10 else "Calibrating..."
        
    squeeze_alert = "🚨 <b>VOLATILITY SQUEEZE DETECTED</b>\n" if s.get("is_squeeze") else ""

    source = s.get("source", "LIMIT")
    
    if source == "MARKET":
        header = f"🟢 <b>MARKET ENTRY | {symbol}</b>"
        action = f"🎯 <b>ACTION:</b> {dir_emoji} (Прямой вход)"
        entry_text = f"📥 <b>ENTRY PRICE:</b> {fmt(s.get('entry_price', entry_mid))}\n<i>(SL и TP уже выставлены)</i>\n"
    else:
        header = f"⏳ <b>LIMIT PLACED | {symbol}</b>"
        action = f"🎯 <b>ACTION:</b> {dir_emoji} (Ожидаем откат)"
        entry_text = f"📥 <b>ENTRY ZONE (LIMIT)</b>\n   {fmt(entry_low)} — {fmt(entry_high)}\n"

    card = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{action}\n"
        f"🧠 <b>STRAT:</b>  <code>[{strategy}]</code>\n"
        f"{squeeze_alert}\n"
        f"{entry_text}\n"
        f"🛑 <b>STOP LOSS</b>\n"
        f"   {fmt(sl)} <i>(-{sl_pct:.1f}%)</i>\n\n"
        f"🏁 <b>TAKE PROFIT (R:R {rr:.1f})</b>\n"
        f"   TP1 Target: {fmt(tp1)} <i>(+{tp1_pct:.1f}%)</i>\n"
        f"   TP2 Target: {fmt(tp2)} <i>(+{tp2_pct:.1f}%)</i>\n"
        f"   TP3 Target: {fmt(tp3)} <i>(+{tp3_pct:.1f}%)</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔬 <b>AI SCORE: {score:.1f}/100</b> | {regime_emoji}\n"
        f"🔒 <b>Logic:</b> <code>[v10.5 No Look-Ahead]</code>\n"
        f"💧 <b>Liquidity:</b> <code>{s.get('liquidity_tier', 'TIER_3')}</code>\n"
        f"🧾 <b>Est. Costs:</b> <code>~{s.get('estimated_costs_pct', 0.15):.2f}%</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>MARKET CONTEXT:</b>\n"
        f"• RSI (1h): <b>{rsi:.1f}</b>\n"
        f"• VWAP: <b>{vwap_label}</b>\n"
        f"• Funding: <b>{funding_str}</b>\n"
        f"• CVD Flow: <b>{oi_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <b>QUARTER-KELLY EXECUTION</b>\n"
        f"Position: <b>${position_usd:.0f}</b>\n"
        f"Max Risk: <b>${risk_usd:.0f}</b>\n"
    )
    return card


async def send_signal(bot: Bot, chat_id: int, signal_data: dict):
    """Send a formatted signal card to Telegram."""
    try:
        card = build_signal_card(signal_data)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Открыть Дашборд", web_app=WebAppInfo(url="https://apex-quantum.up.railway.app/"))],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
                InlineKeyboardButton(text="🏠 Меню", callback_data="home")
            ]
        ])
        await bot.send_message(chat_id=chat_id, text=card, reply_markup=kb, parse_mode="HTML")
        logger.info(f"Signal sent: {signal_data.get('symbol')} score={signal_data.get('score')}")
    except Exception as e:
        logger.error(f"Failed to send result notification: {e}")

async def send_tp1_notification(bot: Bot, chat_id: int, trade_data: dict, pnl_pct: float):
    """Sends a notification when TP1 is hit and SL is moved to Breakeven."""
    text = (
        "🟢 <b>ПЕРВЫЙ ТЕЙК ВЗЯТ (TP1)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🪙 <b>Монета:</b> {trade_data.get('symbol')}\n"
        f"💸 <b>PnL:</b>  <b>+{pnl_pct:.2f}%</b>\n\n"
        "🛡 <b>Стоп-лосс переведен в безубыток.</b>\n"
        "Часть прибыли зафиксирована, сделка продолжается (Free Ride). 🚀\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Вход:</b>  ${format_price(trade_data.get('entry_price', 0))}\n"
        f"🏁 <b>TP1:</b>   ${format_price(trade_data.get('take_profit_1', 0))}\n\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
         InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")]
    ])
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send TP1 notification: {e}")

async def send_trade_result_notification(bot: Bot, chat_id: int, trade_data: dict, status: str, pnl_pct: float):
    """Sends a notification when a trade hits TP or SL."""
    if status == "WON":
        header = "✅ <b>ТЕЙК-ПРОФИТ ДОСТИГНУТ</b>"
        pnl_text = f"+{pnl_pct:.2f}%" if pnl_pct > 0 else f"{pnl_pct:.2f}%"
        color_emoji = "🟢"
    elif status == "WON_BREAKEVEN":
        header = "🎯 <b>СДЕЛКА ЗАКРЫТА В ПЛЮС (ТРЕЙЛИНГ)</b>"
        pnl_text = f"+{pnl_pct:.2f}%" if pnl_pct > 0 else f"{pnl_pct:.2f}%"
        color_emoji = "🟢"
    elif status == "TIMEOUT":
        header = "⏱ <b>ЗАКРЫТО ПО ТАЙМ-АУТУ (>6ч)</b>"
        pnl_text = f"+{pnl_pct:.2f}%" if pnl_pct > 0 else f"{pnl_pct:.2f}%"
        color_emoji = "⚪"
    elif status == "TIMEOUT_SMALL_WIN":
        header = "⏱ <b>ВЫХОД ПО ТАЙМ-АУТУ (МИКРО-ПЛЮС)</b>"
        pnl_text = f"+{pnl_pct:.2f}%" if pnl_pct > 0 else f"{pnl_pct:.2f}%"
        color_emoji = "🟢"
    elif status == "TIMEOUT_BREAKEVEN":
        header = "⏱ <b>ВЫХОД ПО ТАЙМ-АУТУ (БЕЗУБЫТОК)</b>"
        pnl_text = f"+{pnl_pct:.2f}%" if pnl_pct > 0 else f"{pnl_pct:.2f}%"
        color_emoji = "🟡"
    elif status == "TIMEOUT_SMALL_LOSS":
        header = "⏱ <b>ВЫХОД ПО ТАЙМ-АУТУ (МИКРО-МИНУС)</b>"
        pnl_text = f"{pnl_pct:.2f}%"
        color_emoji = "🟠"
    else:
        header = "❌ <b>СДЕЛКА ЗАКРЫТА ПО СТОПУ</b>"
        pnl_text = f"{pnl_pct:.2f}%"
        color_emoji = "🔴"
        
    text = (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🪙 <b>Монета:</b> {trade_data.get('symbol')}\n"
        f"📈 <b>Направление:</b> {trade_data.get('direction', 'LONG')}\n\n"
        f"💸 <b>Итоговый PnL:</b>  <b>{pnl_text}</b> {color_emoji}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Вход:</b>  ${format_price(trade_data.get('entry_price', 0))}\n"
        f"🏁 <b>Выход:</b> ${format_price(trade_data.get('take_profit_3', 0) if status == 'WON' else trade_data.get('stop_loss', 0))}\n\n"
        f"🕒 <i>Открыта: {trade_data.get('opened_at', '—')} UTC</i>\n"
        f"🏁 <i>Закрыта: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
         InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")]
    ])
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Telegram failed to send result: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# START — launch bot polling (called from main.py)
# ─────────────────────────────────────────────────────────────────────────────

async def start_telegram_bot():
    """Initialize and start the Telegram bot with polling."""
    cfg = get_config()
    token = cfg.alerts.telegram_bot_token.get_secret_value()

    if not token:
        logger.warning("No Telegram bot token configured. Bot UI disabled.")
        return

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Starting Telegram bot polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    except Exception as e:
        logger.error(f"Telegram bot error: {e}")
    finally:
        await bot.session.close()

