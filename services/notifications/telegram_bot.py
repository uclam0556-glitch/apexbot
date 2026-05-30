"""
APEX Trading System v4.0
services/notifications/telegram_bot.py

Handles sending signals and alerts to Telegram.
Used primarily in PAPER_TRADING mode to simulate execution visually.
"""

from __future__ import annotations

import logging
import aiohttp

from shared.config import get_config
from shared.models import FullSignalPackage, AIAuditResult

logger = logging.getLogger(__name__)
_config = get_config()


class TelegramNotifier:
    """
    Sends structured trade signals and alerts to a Telegram chat.
    """

    def __init__(self) -> None:
        self.bot_token = _config.alerts.telegram_bot_token.get_secret_value()
        self.chat_id = _config.alerts.telegram_chat_id
        
        self.enabled = bool(self.bot_token and self.chat_id)
        if not self.enabled:
            logger.warning("Telegram Bot Token or Chat ID not configured. Telegram notifications disabled.")

    async def send_message(self, text: str, parse_mode: str = "MarkdownV2") -> bool:
        """
        Sends a raw message to the configured Telegram chat.
        """
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        err_text = await response.text()
                        logger.error(f"Failed to send Telegram message: {err_text}")
                        return False
            return True
        except Exception as e:
            logger.error(f"Telegram API Exception: {e}")
            return False

    def _escape_markdown(self, text: str) -> str:
        """Escape characters required for MarkdownV2."""
        # Note: minimal escaping for basic markdown.
        # MarkdownV2 requires escaping: _ * [ ] ( ) ~ ` > # + - = | { } . !
        chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for c in chars_to_escape:
            text = text.replace(c, f"\\{c}")
        return text

    async def send_paper_trade_signal(
        self, 
        package: FullSignalPackage, 
        audit: AIAuditResult, 
        amount: float, 
        position_usd: float,
        current_price: float
    ) -> bool:
        """
        Format and send a paper trading signal to Telegram.
        """
        sig = package.signal
        adj = audit.parameter_adjustments
        
        direction = "LONG" if sig.direction.value == "LONG" else "SHORT"
        
        sl = adj.stop_loss_adjusted or sig.stop_loss
        tp1 = adj.tp1_adjusted or sig.take_profit_1
        tp1 = adj.tp1_adjusted or sig.take_profit_1

        emoji = "🟢" if direction == "LONG" else "🔴"
        
        message = (
            f"{emoji} *APEX SIGNAL: {direction} {sig.symbol}* {emoji}\n"
            f"📊 *Одобрено AI Аудитором* (Score: {sig.confluence.normalized_score}/10)\n\n"
            f"🎯 *Вход:* `${current_price:,.2f}` (Paper Trading)\n"
            f"🛑 *Виртуальный Стоп-Лосс:* `${sl:,.2f}`\n\n"
            f"💰 *Тейк-профит:* `${tp1:,.2f}`\n"
        )
            
        message += f"\n💵 *Размер позиции:* `${position_usd:,.2f}` ({amount:,.4f} {sig.symbol.split('/')[0]})\n"
        
        reasoning = audit.reasoning.replace("-", "\\-").replace(".", "\\.")
        message += f"\n🧠 *Логика ИИ:* _{reasoning}_"

        # Note: If MarkdownV2 parsing fails due to unescaped characters, 
        # it might be safer to use 'HTML' parse_mode for complex AI reasoning texts.
        # We will use HTML mode to avoid strict MarkdownV2 escaping issues with arbitrary AI text.
        
        html_message = (
            f"{emoji} <b>APEX SIGNAL: {direction} {sig.symbol}</b> {emoji}\n"
            f"📊 <b>Одобрено AI Аудитором</b> (Score: {sig.confluence.normalized_score}/10)\n\n"
            f"🎯 <b>Вход:</b> ${current_price:,.2f} (Paper Trading)\n"
            f"🛑 <b>Виртуальный Стоп-Лосс:</b> ${sl:,.2f}\n\n"
            f"💰 <b>Тейк-профит:</b> ${tp1:,.2f}\n"
        )
            
        html_message += f"\n💵 <b>Размер позиции:</b> ${position_usd:,.2f} ({amount:,.4f} {sig.symbol.split('/')[0]})\n"
        html_message += f"\n🧠 <b>Логика ИИ:</b> <i>{audit.reasoning}</i>"

        return await self.send_message(html_message, parse_mode="HTML")
