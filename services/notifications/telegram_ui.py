"""
APEX Trading System v4.0
services/notifications/telegram_ui.py

Aiogram-based Telegram bot for interactive menus and stats.
"""

import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from shared.config import get_config
from shared.lite_db import get_stats, get_recent_trades
from shared.state import global_state

logger = logging.getLogger(__name__)
_config = get_config()

router = Router()

def get_main_keyboard() -> InlineKeyboardMarkup:
    """Returns the main menu keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 Статус сканирования", callback_data="status")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="📜 История сделок", callback_data="history")],
        [InlineKeyboardButton(text="⚙️ Настройки (v5.0)", callback_data="settings")]
    ])

@router.message(Command("start"))
async def cmd_start(message: Message):
    """Handles the /start command."""
    text = (
        "🤖 <b>APEX Quantum AI v5.0</b>\n\n"
        "Добро пожаловать в панель управления снайперским алгоритмом.\n"
        "Система работает в фоне, сканируя топ-монеты каждую 5-минутную свечу.\n\n"
        "🌐 <b>Dashboard:</b> Откройте http://localhost:8501 в браузере\n\n"
        "Выберите действие в меню ниже:"
    )
    await message.answer(text, reply_markup=get_main_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "status")
async def process_status(callback: CallbackQuery):
    """Shows live scanning status."""
    text = (
        "📡 <b>Текущий статус сканирования</b>\n\n"
        f"Анализируется монета: <b>{global_state.current_symbol}</b>\n"
        f"Время последней проверки: <b>{global_state.last_scan_time}</b>\n"
        f"Рыночный режим (ML): <b>{global_state.regime}</b>\n\n"
        "<i>Нажмите кнопку еще раз, чтобы обновить статус</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    except Exception:
        pass # Ignore "message is not modified" error
    await callback.answer()


@router.callback_query(F.data == "stats")
async def process_stats(callback: CallbackQuery):
    """Shows PnL and Win Rate."""
    stats = await get_stats()
    
    text = (
        "📊 <b>Глобальная Статистика (Paper Trading)</b>\n\n"
        f"Всего закрытых сделок: <b>{stats['total']}</b>\n"
        f"Успешных (WON): <b>{stats['won']}</b>\n"
        f"Провальных (LOST): <b>{stats['lost']}</b>\n"
        f"Win Rate: <b>{stats['win_rate']:.1f}%</b>\n"
        f"Чистый PnL: <b>{stats['pnl_sum']:+.2f}%</b>"
    )
    await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "history")
async def process_history(callback: CallbackQuery):
    """Shows the latest trades."""
    trades = await get_recent_trades(limit=5)
    
    if not trades:
        text = "📜 <b>История сделок</b>\n\nПока нет сделок. Бот сканирует рынок..."
    else:
        text = "📜 <b>Последние 5 сделок:</b>\n\n"
        for t in trades:
            emoji = "🟢" if t['direction'] == "LONG" else "🔴"
            status_emoji = "⏳" if t['status'] == "OPEN" else ("✅" if t['status'] == "WON" else "❌")
            
            text += f"{emoji} <b>{t['symbol']}</b> | {status_emoji} {t['status']}\n"
            text += f"Вход: ${t['entry_price']:.2f} | PnL: {t['pnl_pct'] or 0.0:+.2f}%\n"
            text += "──────────────\n"

    await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "settings")
async def process_settings(callback: CallbackQuery):
    """Shows settings."""
    text = (
        "⚙️ <b>Настройки системы</b>\n\n"
        "Режим: <b>Paper Trading (Shadow Mode)</b>\n"
        "Биржи: <b>Binance</b>\n"
        "Фильтр: <b>Confluence >= 6.5</b>\n\n"
        "<i>Изменение настроек пока доступно только через config.py</i>"
    )
    await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="HTML")

async def start_telegram_bot():
    """Starts the Aiogram polling loop."""
    token = _config.alerts.telegram_bot_token.get_secret_value()
    if not token:
        logger.warning("Telegram token missing, bot UI will not start.")
        return

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)
    
    logger.info("Starting Telegram UI Bot Polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Telegram Bot crashed: {e}")
