"""
APEX Trading System v4.0
Telegram Alert System.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from shared.config import get_config
from shared.database import get_redis, RedisKeys
from shared.models import AIAuditResult, FullSignalPackage, ApprovalType

logger = logging.getLogger(__name__)
_config = get_config()


class TelegramAlertService:
    def __init__(self) -> None:
        self.bot_token = _config.alerts.telegram_bot_token.get_secret_value()
        self.chat_id = _config.alerts.telegram_chat_id
        self.error_chat_id = _config.alerts.telegram_error_chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    async def _deduplicate(self, alert_type: str, ttl_seconds: int = 300) -> bool:
        """Returns True if alert is new, False if duplicate."""
        if not self.bot_token:
            return False
            
        redis = get_redis()
        key = RedisKeys.alert_dedup(alert_type)
        is_new = await redis.set(key, "1", ex=ttl_seconds, nx=True)
        return bool(is_new)

    async def _send_message(self, text: str, chat_id: str | None = None) -> None:
        if not self.bot_token:
            logger.debug(f"Telegram not configured. Skipped msg: {text[:50]}")
            return
            
        target_chat = chat_id or self.chat_id
        if not target_chat:
            return

        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, json=payload) as resp:
                    if resp.status != 200:
                        logger.error(f"Telegram API error: {await resp.text()}")
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")

    async def send_signal_alert(self, package: FullSignalPackage, audit: AIAuditResult) -> None:
        """Formats and sends the main trade signal alert."""
        
        sig = package.signal
        param_adj = audit.parameter_adjustments
        
        # Apply AI adjustments for display
        entry_low = param_adj.entry_range_adjusted[0] if param_adj.entry_range_adjusted else sig.entry_low
        entry_high = param_adj.entry_range_adjusted[1] if param_adj.entry_range_adjusted else sig.entry_high
        sl = param_adj.stop_loss_adjusted or sig.stop_loss
        tp1 = param_adj.tp1_adjusted or sig.take_profit_1
        tp2 = param_adj.tp2_adjusted or sig.take_profit_2
        tp3 = None if param_adj.remove_tp3 else sig.take_profit_3
        size_multiplier = param_adj.position_size_multiplier
        exec_type = param_adj.execution_type_override or package.microstructure.slippage_estimate.recommended_execution
        
        # Calculate % diffs for display
        entry_mid = (entry_low + entry_high) / 2
        dir_sign = 1 if sig.direction.value == "LONG" else -1
        
        sl_pct = abs(entry_mid - sl) / entry_mid * 100
        tp1_pct = abs(tp1 - entry_mid) / entry_mid * 100
        tp2_pct = abs(tp2 - entry_mid) / entry_mid * 100
        tp3_pct = (abs(tp3 - entry_mid) / entry_mid * 100) if tp3 else 0.0
        
        emoji = "🟢" if sig.direction.value == "LONG" else "🔴"
        cautious_tag = " ⚠️ CAUTIOUS" if audit.approval_type == ApprovalType.CAUTIOUS else ""
        
        # Calculate actual risk amounts
        deposit_1k = 1000 * (sig.risk_pct / 100) * size_multiplier
        deposit_5k = 5000 * (sig.risk_pct / 100) * size_multiplier
        deposit_10k = 10000 * (sig.risk_pct / 100) * size_multiplier
        
        cond_str = "\n".join(f"• {c}" for c in audit.do_not_enter_if) if audit.do_not_enter_if else "• None"
        
        msg = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 APEX SIGNAL | <b>{sig.symbol}</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 <b>{sig.direction.value}</b> {emoji}{cautious_tag} | AI Conf: {audit.final_confidence}/10
📈 Hist. Winrate: {package.calibrated_winrate.winrate_estimate*100:.1f}% ({package.calibrated_winrate.sample_size} setups)
💰 Entry: ${entry_low:,.2f} — ${entry_high:,.2f}
🛑 Stop: ${sl:,.2f} (-{sl_pct:.2f}%)
🎯 TP1: ${tp1:,.2f} (+{tp1_pct:.2f}%) → {sig.tp_allocation[0]*100}%
🎯 TP2: ${tp2:,.2f} (+{tp2_pct:.2f}%) → {sig.tp_allocation[1]*100}%"""

        if tp3:
            msg += f"\n🎯 TP3: ${tp3:,.2f} (+{tp3_pct:.2f}%) → {sig.tp_allocation[2]*100}%"

        msg += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💎 Confluence: {package.confluence.active_count}/{package.confluence.total_factors} ({package.confluence.normalized_score:.1f}/10)
🔗 Top: {', '.join(package.confluence.top_3_factors)}
📀 OFI: {package.microstructure.ofi.ofi_score:.2f} ({package.microstructure.ofi.trend})
🐳 Smart Money: {package.sentiment_divergence.divergence_strength.value}
🌍 Macro: {package.macro_correlation.macro_bias.value} (DXY {package.macro_correlation.dxy_trend_24h})
⏰ Temporal: {package.temporal_bias.label.value} ({package.temporal_bias.combined_score:+.1f})
🌊 Liquidations: {package.liquidation.status.value}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ RISK: {sig.risk_pct * size_multiplier:.2f}% (Kelly)
   $1k → ${deposit_1k:.1f}
   $5k → ${deposit_5k:.1f}
  $10k → ${deposit_10k:.1f}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚫 NOT ENTERING IF:
{cond_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚙️ Execution: {exec_type.value} | Valid: {sig.signal_valid_hours}h
<i>{audit.audit_summary}</i>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

        await self._send_message(msg)

    async def send_critical_alert(self, title: str, message: str, extra_data: dict | None = None) -> None:
        """Sends alert to error channel with deduplication."""
        dedup_key = f"crit_{title.replace(' ', '_')}"
        if not await self._deduplicate(dedup_key, 600): # 10m dedup
            return
            
        msg = f"🚨 <b>CRITICAL: {title}</b>\n\n{message}"
        if extra_data:
            msg += "\n\n<pre>" + "\n".join(f"{k}: {v}" for k, v in extra_data.items()) + "</pre>"
            
        await self._send_message(msg, chat_id=self.error_chat_id)

    async def send_p2p_alert(self, opportunity: dict[str, Any], audit_result: dict[str, Any]) -> None:
        """Formats and sends P2P arbitrage alert."""
        pass # To be implemented
        
    async def send_weekly_report(self, stats: Any) -> None:
        """Sends weekly performance summary."""
        pass # To be implemented
