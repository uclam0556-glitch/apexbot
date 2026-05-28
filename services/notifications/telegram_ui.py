"""
APEX Trading System v5.0
services/notifications/telegram_ui.py

Ultra-premium Telegram Bot UI with beautiful signal cards,
live status, market overview, and multi-button navigation.
"""

import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command

from shared.config import get_config
from shared.lite_db import get_stats, get_recent_trades, get_open_trades
from shared.state import global_state

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
            InlineKeyboardButton(text="📡 Статус системы", callback_data="status"),
            InlineKeyboardButton(text="🌡 Рынок", callback_data="market"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
            InlineKeyboardButton(text="📜 История", callback_data="history"),
        ],
        [
            InlineKeyboardButton(text="🔥 Hot Coins", callback_data="hot"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
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
    return (
        "⚡ <b>APEX Quantum AI v5.0</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🟢 <b>Система активна:</b> Мониторинг 40 пар 24/7\n"
        "🧠 <b>Ядро:</b> SMC + MTF + Macro Alignment\n"
        "🛡 <b>Риск-менеджмент:</b> $3000 | 1% на сделку\n\n"
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
        "📡 <b>Статус сканирования</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔍 Сейчас анализирую: <b>{global_state.current_symbol}</b>\n"
        f"⏱ Последнее сканирование: <b>{global_state.last_scan_time}</b>\n"
        f"{regime_emoji} Рыночный режим (ML): <b>{global_state.regime}</b>\n\n"
        f"📦 Монет в списке: <b>40</b>\n"
        f"⏳ Таймфреймов: <b>5</b> (1d · 4h · 1h · 15m · 5m)\n"
        f"🎯 Мин. score для сигнала: <b>6.0/10</b>\n\n"
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
        fg, btc_d = await __import__('asyncio').gather(
            get_fear_greed(), get_btc_dominance(), return_exceptions=True
        )
        fg_val = fg.get("value", 50) if isinstance(fg, dict) else 50
        fg_label = fg.get("label", "Neutral") if isinstance(fg, dict) else "Neutral"
        btc_dom = btc_d.get("dominance", 55.0) if isinstance(btc_d, dict) else 55.0

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

    regime_emoji = {"BULL": "🟢 Бычий", "BEAR": "🔴 Медвежий",
                    "SIDEWAYS": "🟡 Боковик", "CRISIS": "⚠️ Кризис"}.get(global_state.regime, "⚪")

    text = (
        "🌐 <b>Обзор рынка</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{fg_emoji} <b>Fear & Greed:</b> {fg_val}/100 — {fg_label}\n"
        f"₿ <b>BTC Dominance:</b> {btc_dom}% — {alt_season}\n"
        f"🧠 <b>ML Режим (APEX):</b> {regime_emoji}\n\n"
        "<b>Интерпретация:</b>\n"
        f"{'🟢 Хорошее время для покупок (страх = возможность)' if fg_val < 40 else '🔴 Осторожно — рынок перегрет' if fg_val > 70 else '🟡 Нейтральный рынок'}\n\n"
        "<i>Данные обновляются каждый час</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "stats")
async def process_stats(callback: CallbackQuery):
    stats = await get_stats()
    open_trades = await get_open_trades()
    active_count = len(open_trades) if open_trades else 0
    
    wr = stats['win_rate']
    
    # Generate progress bar for win rate
    filled = int(wr / 10) if stats['total'] > 0 else 0
    bar = "🟩" * filled + "⬜" * (10 - filled)
    
    text = (
        "📊 <b>Статистика & Эффективность</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ <b>Открытых позиций:</b> {active_count}\n"
        f"📈 <b>Закрытых сделок:</b> {stats['total']}\n"
        f"   ┣ Успешных (TP): <b>{stats['won']}</b> ✅\n"
        f"   ┗ Убыточных (SL): <b>{stats['lost']}</b> ❌\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Win Rate: {wr:.1f}%</b>\n"
        f"[{bar}]\n\n"
        f"💰 <b>Суммарный PnL:</b> {stats['pnl_sum']:+.2f}%\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>Рабочий депозит:</b> $3,000\n"
        f"⚖️ <b>Риск на сделку:</b> 1% ($30)\n"
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
            "Бот сканирует 40 монет — ожидайте сигнал 🔍"
        )
    else:
        text = "📜 <b>Последние 10 сделок:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for t in trades:
            dir_emoji = "🟢 L" if t['direction'] == "LONG" else "🔴 S"
            
            if t['status'] == "OPEN":
                status_emoji = "⏳"
                pnl_str = "В процессе..."
            elif t['status'] == "WON":
                status_emoji = "✅"
                pnl_str = f"+{t['pnl_pct']:.2f}%"
            else:
                status_emoji = "❌"
                pnl_str = f"{t['pnl_pct']:.2f}%"
                
            open_dt = datetime.strptime(t['opened_at'], '%Y-%m-%d %H:%M:%S.%f') if '.' in t['opened_at'] else datetime.strptime(t['opened_at'], '%Y-%m-%d %H:%M:%S')
            date_str = open_dt.strftime("%d.%m %H:%M")

            text += (
                f"{status_emoji} <b>{t['symbol']}</b> {dir_emoji}  |  <code>{date_str}</code>\n"
                f"   Вход: ${t['entry_price']:.4f}  →  PnL: <b>{pnl_str}</b>\n"
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
    hot = global_state.hot_coins if hasattr(global_state, 'hot_coins') and global_state.hot_coins else []

    if not hot:
        text = (
            "🔥 <b>Горячие монеты</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⏳ Данные накапливаются...\n\n"
            "После первого полного цикла сканирования\n"
            "здесь появятся топ монеты по силе сигнала!"
        )
    else:
        text = "🔥 <b>Горячие монеты сейчас:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, coin in enumerate(hot[:8], 1):
            medal = ["🥇", "🥈", "🥉"].get(i - 1, f"{i}.")
            text += f"{medal} <b>{coin['symbol']}</b> — score {coin['score']:.1f}/10\n"
            text += f"   RSI: {coin.get('rsi', '—')} | Режим: {coin.get('regime', '—')}\n\n"

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
        "⚙️ <b>Настройки APEX v5.0</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💵 <b>Депозит:</b> $3,000\n"
        "⚖️ <b>Риск/сделка:</b> 1% ($30)\n"
        "🎯 <b>Мин. score:</b> 7.0/10\n"
        "📦 <b>Монет в скане:</b> 40\n"
        "⏱ <b>Таймфреймы:</b> 1d · 4h · 1h · 15m · 5m\n"
        "🏦 <b>Биржа:</b> MEXC\n"
        "🤖 <b>Режим:</b> Paper Trading\n\n"
        "<b>Индикаторы:</b>\n"
        "✅ SMC (BOS/CHoCH/FVG)\n"
        "✅ MTF Alignment\n"
        "✅ VWAP\n"
        "✅ EMA Ribbon (5/8/13/21/34/55)\n"
        "✅ RSI Divergence\n"
        "✅ Bollinger Bands Squeeze\n"
        "✅ Fibonacci Auto-Levels\n"
        "✅ Volume Spike\n"
        "✅ Funding Rate\n"
        "✅ Open Interest\n"
        "✅ Fear & Greed Index\n"
        "✅ BTC Dominance\n"
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
# SIGNAL CARD — called from main.py when signal is found
# ─────────────────────────────────────────────────────────────────────────────

def build_signal_card(signal_data: dict) -> str:
    """
    Build a beautiful, information-rich signal card.
    """
    s = signal_data
    symbol = s.get("symbol", "???")
    direction = s.get("direction", "LONG")
    entry_low = s.get("entry_low", 0)
    entry_high = s.get("entry_high", 0)
    sl = s.get("stop_loss", 0)
    tp1 = s.get("tp1", 0)
    tp2 = s.get("tp2", 0)
    tp3 = s.get("tp3", 0)
    score = s.get("score", 0)
    regime = s.get("regime", "UNKNOWN")
    rsi = s.get("rsi", 50)
    funding = s.get("funding_rate", 0)
    oi_change = s.get("oi_change", 0)
    fg_value = s.get("fear_greed", 50)
    btc_dom = s.get("btc_dominance", 55)
    position_usd = s.get("position_usd", 30)
    risk_usd = s.get("risk_usd", 30)
    rr = s.get("rr_ratio", 0)
    vwap_label = s.get("vwap_label", "")
    ema_label = s.get("ema_label", "")
    rsi_div = s.get("rsi_divergence", "NONE")
    bb_label = s.get("bb_label", "")
    fib_level = s.get("fib_level", None)

    dir_emoji = "🚀" if direction == "LONG" else "🔻"
    regime_emoji = {"BULL": "🟢", "BEAR": "🔴", "SIDEWAYS": "🟡", "CRISIS": "⚠️"}.get(regime, "⚪")

    # Score stars
    stars = int(score / 2)
    star_str = "⭐" * stars + "✩" * (5 - stars)

    # Entry SL risk %
    entry_mid = (entry_low + entry_high) / 2 if entry_high > 0 else entry_low
    sl_pct = abs(entry_mid - sl) / entry_mid * 100 if entry_mid > 0 else 0
    tp1_pct = abs(tp1 - entry_mid) / entry_mid * 100 if entry_mid > 0 else 0
    tp2_pct = abs(tp2 - entry_mid) / entry_mid * 100 if entry_mid > 0 else 0
    tp3_pct = abs(tp3 - entry_mid) / entry_mid * 100 if entry_mid > 0 else 0

    # Format prices smartly (crypto can be 0.0000001 or 100000)
    def fmt(price):
        if price == 0:
            return "—"
        if price >= 1000:
            return f"${price:,.2f}"
        elif price >= 1:
            return f"${price:.4f}"
        else:
            return f"${price:.6f}"

    funding_str = f"{funding:+.3f}%" if funding != 0 else "—"
    oi_str = f"{oi_change:+.1f}%" if oi_change != 0 else "—"
    fib_str = f"Fib {fib_level}" if fib_level else "—"

    card = (
        f"{dir_emoji} <b>СИГНАЛ {direction} • {symbol}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>Вход:</b>     {fmt(entry_low)} – {fmt(entry_high)}\n"
        f"🛑 <b>Стоп-лосс:</b> {fmt(sl)}  <i>(-{sl_pct:.1f}%)</i>\n"
        f"🎯 <b>TP1 (40%):</b>  {fmt(tp1)}  <i>(+{tp1_pct:.1f}%)</i>\n"
        f"🎯 <b>TP2 (35%):</b>  {fmt(tp2)}  <i>(+{tp2_pct:.1f}%)</i>\n"
        f"🎯 <b>TP3 (25%):</b>  {fmt(tp3)}  <i>(+{tp3_pct:.1f}%)</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Score:</b>    <b>{score:.1f}/10</b>  {star_str}\n"
        f"{regime_emoji} <b>Режим:</b>    {regime}\n"
        f"📈 <b>RSI(1h):</b>  {rsi:.1f}{'  🔥 Перепродан' if rsi < 30 else ''}\n"
        f"📉 <b>VWAP:</b>     {vwap_label}\n"
        f"🎀 <b>EMA Ribbon:</b> {ema_label}\n"
        f"↗️ <b>RSI Div:</b>  {rsi_div}\n"
        f"🔮 <b>Fib:</b>      {fib_str}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💸 <b>Funding:</b>  {funding_str}\n"
        f"📦 <b>OI Change:</b> {oi_str}\n"
        f"😱 <b>Fear&Greed:</b> {fg_value}/100\n"
        f"₿  <b>BTC.D:</b>    {btc_dom}%\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>Позиция:</b>  ${position_usd:.0f} (1% от $3,000)\n"
        f"⚠️ <b>Риск $:</b>   ${risk_usd:.0f} макс потеря\n"
        f"⚖️ <b>R/R Ratio:</b> 1:{rr:.1f}\n\n"
        f"⏳ <b>Сигнал действителен:</b> 24 часа\n"
        f"🕐 <i>{datetime.utcnow().strftime('%d.%m.%Y %H:%M')} UTC</i>"
    )
    return card


async def send_signal(bot: Bot, chat_id: int, signal_data: dict):
    """Send a formatted signal card to Telegram."""
    card = build_signal_card(signal_data)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
         InlineKeyboardButton(text="🏠 Меню", callback_data="home")]
    ])
    try:
        await bot.send_message(chat_id=chat_id, text=card, reply_markup=kb, parse_mode="HTML")
        logger.info(f"Signal sent: {signal_data.get('symbol')} score={signal_data.get('score')}")
    except Exception as e:
        logger.error(f"Failed to send signal: {e}")

async def send_trade_result_notification(bot: Bot, chat_id: int, trade_data: dict, status: str, pnl_pct: float):
    """Sends a notification when a trade hits TP or SL."""
    if status == "WON":
        header = "✅ <b>ТЕЙК-ПРОФИТ ДОСТИГНУТ</b>"
        pnl_text = f"+{pnl_pct:.2f}%"
        color_emoji = "🟢"
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
        f"💰 <b>Вход:</b>  ${trade_data.get('entry_price', 0):.4f}\n"
        f"🏁 <b>Выход:</b> ${trade_data.get('take_profit_1', 0) if status == 'WON' else trade_data.get('stop_loss', 0):.4f}\n\n"
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

