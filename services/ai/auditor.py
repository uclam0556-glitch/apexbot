"""
APEX Trading System v4.0
AI Audit Layer v4 — with ACTIVE Parameter Modification.

CRITICAL v4 CHANGE: AI no longer just writes recommendations in text.
AI returns structured `parameter_adjustments` that executor MUST apply.

Architecture:
1. Receives FullSignalPackage JSON from backend
2. Runs 11 audit checks (7 original + 4 NEW v4)
3. Returns AIAuditResult with:
   - Standard audit (APPROVED / APPROVED_CAUTIOUS / REJECTED)
   - parameter_adjustments (AI can modify entry_range, SL, size, execution_type)
   
90/10 Rule strictly enforced:
- 90%: interpret structured data from backend
- 10%: narrative coherence (news + on-chain vs direction)
- NEVER: find reasons to fail without data support
- NEVER: verbose reasoning about market philosophy

AI audit must complete in < 8 seconds (hard timeout).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

import anthropic

from shared.config import get_config
from shared.models import (
    AIAuditResult,
    AnomalyInterpretation,
    ApprovalType,
    AuditChecks,
    Direction,
    ExecutionType,
    FullSignalPackage,
    ParameterAdjustments,
)

logger = logging.getLogger(__name__)
_config = get_config()


SYSTEM_PROMPT = """=======================================================================
APEX TRADING SYSTEM — AI AUDIT AGENT v4.0
WORLD-CLASS PRODUCTION SYSTEM PROMPT — 2026 EDITION
=======================================================================

## IDENTITY & STRICT SCOPE (90/10 RULE)

You are APEX AI Auditor v4.0, the final intelligent layer of a world-class trading system.

**90/10 RULE — ALWAYS:**
- 90% of your work = interpreting structured data already computed by backend
- 10% = your own narrative analysis (news + on-chain coherence)
- You do NOT search for patterns on top of backend data independently
- You do NOT reason about "market philosophy" or "general feeling"
- You do NOT find "reasons" for FAIL/WARN without explicit data from JSON

**NEW v4 — PARAMETER MODIFICATION:**
You now ACTIVELY modify signal parameters when data justifies it.
This is not a text recommendation — it's a structured modification that executor MUST apply.

When to modify:
- SPOOFING_DETECTED: force execution_type = "LIMIT_FORCED"
- HIGH_OI_GROWTH approaching threshold: reduce position_size_multiplier by 0.1-0.2
- adversarial_score > 5: tighten entry_range to lower half
- Multiple WARNs: reduce position_size_multiplier to 0.5-0.75

**Your 11 audit checks:**
CHECK 1: Narrative-technical alignment (news vs direction)
CHECK 2: On-chain narrative coherence (on-chain vs direction)
CHECK 3: Anomaly flags interpretation (from backend)
CHECK 4: Calibrated confidence validation (Feature Store winrate)
CHECK 5: Positioning & sentiment risk (F&G, funding, crowding)
CHECK 6: Risk/reward adequacy (RR ratio, Kelly, VaR)
CHECK 7: Portfolio context (open positions, correlation)
CHECK 8: Adversarial test result (NEW v4: manipulation probability)
CHECK 9: Smart money divergence (NEW v4: crowd vs smart money)
CHECK 10: Macro alignment (NEW v4: DXY/Gold correlation)
CHECK 11: Temporal bias (NEW v4: FOMC/halving/day-of-week)

=======================================================================
CIRCUIT BREAKERS — NEVER OVERRIDE
=======================================================================
IMMEDIATE REJECT if:
- market_regime == "CRISIS"
- blackout_window_active == true
- daily_signals_today >= regime_confluence_min (from JSON)
- daily_pnl_pct <= -3.0
- drawdown_from_peak_pct >= 15.0
- open_positions_count >= 3
- consecutive_losses >= 4
- btc_crash (implied by CRISIS regime)
- var_portfolio_pct >= 8.0
- liquidation_status == "CASCADE_IN_PROGRESS"
- adversarial_result.auto_rejected == true (NEW v4)

REQUIRED for APPROVED:
- stop_loss present
- risk_pct <= 2.0
- at least 1 do_not_enter_if condition
- execution_note if SPOOFING_DETECTED
- parameter_adjustments.execution_type_override = "LIMIT_FORCED" if SPOOFING_DETECTED (NEW v4)

=======================================================================
VERDICT THRESHOLDS
=======================================================================
APPROVED (FULL): >= 8 of 11 checks PASS, 0 FAIL, <= 2 WARN
APPROVED_CAUTIOUS: >= 6 PASS, 0 FAIL, <= 4 WARN → reduce size to 50% Kelly
REJECTED: any FAIL, or >= 5 WARN

=======================================================================
OUTPUT FORMAT — STRICT JSON (NO MARKDOWN, NO EXPLANATIONS OUTSIDE JSON)
=======================================================================

Return ONLY valid JSON matching this schema exactly:
{
  "approved": bool,
  "approval_type": "FULL" | "CAUTIOUS" | "REJECTED",
  "final_confidence": float (0-10),
  "confidence_modifier": float,
  
  "parameter_adjustments": {
    "entry_range_adjusted": [low, high] | null,
    "stop_loss_adjusted": float | null,
    "position_size_multiplier": float (0.1-1.0),
    "execution_type_override": "MARKET"|"LIMIT"|"TWAP"|"LIMIT_FORCED" | null,
    "tp1_adjusted": float | null,
    "tp2_adjusted": float | null,
    "remove_tp3": bool,
    "adjustment_reason": "string explaining ALL adjustments made"
  },
  
  "audit_checks": {
    "narrative_technical": "PASS/WARN/FAIL — brief explanation",
    "onchain_narrative": "PASS/WARN/FAIL — brief explanation",
    "anomaly_interpretation": "PASS/WARN/FAIL — brief explanation",
    "calibrated_confidence": "PASS/WARN/FAIL — brief explanation",
    "sentiment_positioning": "PASS/WARN/FAIL — brief explanation",
    "risk_reward": "PASS/WARN/FAIL — brief explanation",
    "portfolio_context": "PASS/WARN/FAIL — brief explanation",
    "adversarial_check": "PASS/WARN/FAIL — brief explanation",
    "smart_money_divergence": "PASS/WARN/FAIL — brief explanation",
    "macro_alignment": "PASS/WARN/FAIL — brief explanation",
    "temporal_bias": "PASS/WARN/FAIL — brief explanation"
  },
  
  "anomaly_interpretation": [
    {
      "flag": "FLAG_NAME",
      "value": "value from JSON",
      "interpretation": "what it means IN CONTEXT of current data",
      "impact": "WARN_retained|WARN_managed|ACTION_REQUIRED|FAIL",
      "action": "concrete action or null"
    }
  ],
  
  "audit_summary": "2-3 sentences, ONLY facts with numbers",
  "strengths": ["list of specific strengths with numbers"],
  "risks": ["list of specific risks with numbers"],
  "do_not_enter_if": ["specific measurable conditions"],
  "execution_note": "concrete execution instruction",
  "monitoring_notes": "what to watch after entry",
  
  "position_adjustment": null | {
    "recommended_size_pct": float,
    "original_kelly_pct": float,
    "reduction_reason": "string",
    "reconsider_if": "string"
  },
  
  "rejection_reason": null | "specific reason with data"
}
"""

SIGNAL_AUDIT_PROMPT_TEMPLATE = """
[TASK_TYPE: SIGNAL_AUDIT]

Audit this trading signal. Apply all 11 checks. Return ONLY valid JSON.

Signal Data:
{signal_json}

Remember:
1. 90% = interpret backend data. 10% = narrative coherence.
2. Check ALL circuit breakers first.
3. If SPOOFING_DETECTED: set execution_type_override = "LIMIT_FORCED" in parameter_adjustments.
4. If adversarial_score > 6: increase scrutiny, reduce position_size_multiplier.
5. Smart money divergence STRONG_BULL + LONG = powerful confirming signal — reflect in strengths.
6. Temporal bias CAUTION = add to do_not_enter_if: "Wait until FOMC passes".
7. Return ONLY JSON, no markdown, no explanations outside JSON.
"""

P2P_AUDIT_PROMPT_TEMPLATE = """
[TASK_TYPE: P2P_AUDIT]

Audit this P2P arbitrage opportunity. Apply P2P checks. Return ONLY valid JSON.

P2P Data:
{p2p_json}

P2P JSON schema for output:
{
  "approved": bool,
  "grade": "PREMIUM"|"GOOD"|"WEAK"|"SKIP",
  "risk_level": "LOW"|"MEDIUM"|"HIGH",
  "checks": {"counterparty": "PASS/FAIL — ...", "data_source": "...", "market_stability": "...", "spread_realistic": "...", "sync_risk": "...", "timing": "..."},
  "recommendation": "string",
  "execution_tips": ["tip1", "tip2"],
  "abort_conditions": ["condition1", "condition2"],
  "profit_scenarios": {"10000_usdt": "X RUB", "30000_usdt": "Y RUB"},
  "fx_slippage_note": "string with adjusted margin explanation (NEW v4)"
}
"""


class AIAuditor:
    """
    APEX AI Audit Layer v4.
    Wraps Anthropic Claude API with strict scope enforcement.
    
    Key properties:
    - Deterministic: same JSON → same result (scope discipline achieves this)
    - Fast: < 8 seconds timeout hard enforced
    - Active: returns parameter_adjustments, not just text recommendations
    - Failsafe: if AI fails → REJECTED with reason "AI_UNAVAILABLE"
    """

    def __init__(self) -> None:
        cfg = _config.ai
        self._client = anthropic.Anthropic(
            api_key=cfg.anthropic_api_key.get_secret_value()
        )
        self._fallback_client = None  # OpenAI fallback initialized on demand
        self._model = cfg.primary_model
        self._max_tokens = cfg.max_tokens
        self._temperature = cfg.temperature
        self._timeout = cfg.timeout_seconds

    async def audit_signal(self, package: FullSignalPackage) -> AIAuditResult:
        """
        Main audit entry point. Takes full signal package → returns audit result.
        
        Steps:
        1. Check circuit breakers (sync, instant)
        2. Serialize package to JSON
        3. Call Claude API with strict prompt
        4. Parse and validate response
        5. Return AIAuditResult with parameter_adjustments
        
        Hard timeout: 8 seconds. If exceeded → REJECTED.
        """
        start_time = time.monotonic()

        # Step 1: Fast circuit breaker check (no AI needed)
        cb_result = self._check_circuit_breakers(package)
        if cb_result:
            return cb_result

        # Step 2: Build compact JSON for AI
        signal_json = self._serialize_package(package)

        # Step 3: Call AI with timeout
        try:
            ai_response_text = await self._call_ai_with_timeout(
                prompt=SIGNAL_AUDIT_PROMPT_TEMPLATE.format(signal_json=signal_json),
                timeout_seconds=self._timeout,
            )
        except TimeoutError:
            logger.error(f"AI audit timeout after {self._timeout}s for {package.signal.symbol}")
            return self._create_timeout_rejection(package, start_time)
        except Exception as e:
            logger.error(f"AI audit API error: {e}")
            return self._create_error_rejection(package, str(e), start_time)

        # Step 4: Parse and validate
        try:
            audit_data = json.loads(ai_response_text)
            result = self._parse_ai_response(audit_data, package, start_time)
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Failed to parse AI response: {e}\nResponse: {ai_response_text[:500]}")
            return self._create_error_rejection(package, f"Parse error: {e}", start_time)

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "AI audit completed",
            extra={
                "symbol": package.signal.symbol,
                "approved": result.approved,
                "approval_type": result.approval_type.value,
                "confidence": result.final_confidence,
                "duration_ms": elapsed_ms,
                "param_adjustments": result.parameter_adjustments.adjustment_reason,
            }
        )

        return result

    def _check_circuit_breakers(self, package: FullSignalPackage) -> AIAuditResult | None:
        """
        Check hard circuit breakers without AI.
        Returns rejection if triggered, None if all pass.
        """
        risk = package.risk_status

        checks = [
            (package.regime.regime.value == "CRISIS", "CRISIS regime — all trading halted"),
            (not risk.can_trade, f"Risk status: {risk.stop_reason}"),
            (risk.daily_pnl_pct <= -3.0, f"Daily loss {risk.daily_pnl_pct:.1f}% exceeded -3% limit"),
            (risk.drawdown_from_peak_pct >= 15.0, f"Drawdown {risk.drawdown_from_peak_pct:.1f}% exceeded 15% limit"),
            (risk.consecutive_losses >= 4, f"{risk.consecutive_losses} consecutive losses — mandatory pause"),
            (package.var_result.var_95_pct >= 8.0, f"Portfolio VaR {package.var_result.var_95_pct:.1f}% exceeded 8% limit"),
            (package.liquidation.status.value == "CASCADE_IN_PROGRESS", "Liquidation cascade in progress"),
            (package.adversarial.auto_rejected, f"Adversarial test EXTREME score — auto-rejected"),
        ]

        for triggered, reason in checks:
            if triggered:
                return self._create_circuit_breaker_rejection(package, reason)

        return None

    def _serialize_package(self, package: FullSignalPackage) -> str:
        """
        Compact JSON representation for AI.
        Only includes what AI needs — removes raw data arrays.
        """
        data = {
            "signal": {
                "symbol": package.signal.symbol,
                "direction": package.signal.direction.value,
                "entry_range": [package.signal.entry_low, package.signal.entry_high],
                "stop_loss": package.signal.stop_loss,
                "take_profits": [
                    package.signal.take_profit_1,
                    package.signal.take_profit_2,
                    package.signal.take_profit_3,
                ],
                "tp_allocation": package.signal.tp_allocation,
                "risk_pct": package.signal.risk_pct,
                "signal_valid_hours": package.signal.signal_valid_hours,
            },
            "technical": {
                "confluence_score": package.confluence.normalized_score,
                "confluence_weighted": package.confluence.weights_source,
                "top_confluence_factors": [
                    {"factor": f.name, "weight": f.weight, "value": f.value}
                    for f in sorted(package.confluence.factors, key=lambda x: x.weight, reverse=True)[:5]
                ],
                "calibrated_winrate_pct": package.calibrated_winrate.winrate_estimate * 100,
                "calibrated_sample_size": package.calibrated_winrate.sample_size,
                "calibration_ci": [
                    round(package.calibrated_winrate.confidence_interval[0] * 100, 1),
                    round(package.calibrated_winrate.confidence_interval[1] * 100, 1),
                ],
                "slippage_estimate_pct": package.microstructure.slippage_estimate.estimated_slippage_pct,
                "order_flow_imbalance": package.microstructure.ofi.ofi_score,
                "cumulative_delta_trend": package.microstructure.cumulative_delta.cd_trend,
            },
            "market_context": {
                "regime": package.regime.regime.value,
                "regime_confidence": package.regime.confidence,
                "volatility_regime": package.regime.hmm_regime.value,
                "liquidation_status": package.liquidation.status.value,
                "deribit_max_pain": None,  # from onchain
                "fear_greed_index": package.social_data.fear_greed_index if hasattr(package, 'social_data') else None,
            },
            "adversarial_result": {
                "adversarial_score": package.adversarial.adversarial_score,
                "manipulation_probability": package.adversarial.risk_level.value,
                "key_concerns": package.adversarial.key_concerns,
                "passed": package.adversarial.passed,
                "confluence_min_adjustment": package.adversarial.confluence_min_adjustment,
            },
            "sentiment_divergence": {
                "divergence_type": package.sentiment_divergence.divergence_type.value,
                "divergence_strength": package.sentiment_divergence.divergence_strength.value,
                "crowd_sentiment": package.sentiment_divergence.crowd_sentiment,
                "smart_money_action": package.sentiment_divergence.smart_money_action,
                "historical_accuracy_pct": package.sentiment_divergence.historical_accuracy_pct,
            },
            "macro_correlation": {
                "dxy_trend_24h": package.macro_correlation.dxy_trend_24h,
                "dxy_1h_change_pct": package.macro_correlation.dxy_1h_change_pct,
                "gold_1h_change_pct": package.macro_correlation.gold_1h_change_pct,
                "btc_dominance_trend": package.macro_correlation.dominance.dominance_trend,
                "macro_bias": package.macro_correlation.macro_bias.value,
                "correlation_regime": package.macro_correlation.correlation_regime,
            },
            "temporal_bias": {
                "day_of_week": package.temporal_bias.day_of_week.weekday_name,
                "dow_historical_bias": package.temporal_bias.day_of_week.historical_return_pct,
                "fomc_pre_window_active": package.temporal_bias.fomc.fomc_pre_window_active,
                "fomc_caution_active": package.temporal_bias.fomc.fomc_caution_active,
                "halving_cycle_phase": package.temporal_bias.halving.cycle_phase,
                "temporal_bias_score": package.temporal_bias.combined_score,
                "temporal_bias_label": package.temporal_bias.label.value,
            },
            "anomaly_flags": [
                {"flag": f.flag, "severity": f.severity.value, "value": f.value}
                for f in package.anomaly_flags
            ],
            "risk_checks": {
                "daily_signals_today": package.risk_status.daily_signals_used,
                "daily_pnl_pct": package.risk_status.daily_pnl_pct,
                "drawdown_from_peak_pct": package.risk_status.drawdown_from_peak_pct,
                "kelly_position_size_pct": package.position_size.final_size_pct,
                "var_portfolio_pct": package.var_result.var_95_pct,
                "consecutive_losses": package.risk_status.consecutive_losses,
            },
            "news_context": {
                "sentiment_score": package.news_sentiment,
                "top_headlines": package.news_headlines[:3],
            },
            "onchain_summary": {
                "exchange_flow_direction": package.onchain_data.exchange_flow_direction,
                "exchange_flow_btc_24h": package.onchain_data.exchange_outflow_btc_24h,
                "whale_net_direction": package.onchain_data.whale_net_direction,
                "stablecoin_inflow_24h_usd": package.onchain_data.stablecoin_inflow_24h_usd,
                "sopr": package.onchain_data.sopr,
                "mvrv_z_score": package.onchain_data.mvrv_z_score,
            },
            "copy_traders": package.copy_trader_data,
        }

        return json.dumps(data, indent=2, default=str)

    async def _call_ai_with_timeout(
        self,
        prompt: str,
        timeout_seconds: int,
    ) -> str:
        """
        Call Claude API with hard timeout.
        Uses asyncio.wait_for for timeout enforcement.
        """
        import asyncio

        async def _call() -> str:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text

        return await asyncio.wait_for(_call(), timeout=timeout_seconds)

    def _parse_ai_response(
        self,
        data: dict[str, Any],
        package: FullSignalPackage,
        start_time: float,
    ) -> AIAuditResult:
        """Parse and validate AI JSON response into AIAuditResult."""
        elapsed_ms = (time.monotonic() - start_time) * 1000

        # Parse parameter adjustments (NEW v4)
        adj_data = data.get("parameter_adjustments", {})
        param_adj = ParameterAdjustments(
            entry_range_adjusted=tuple(adj_data["entry_range_adjusted"]) if adj_data.get("entry_range_adjusted") else None,
            stop_loss_adjusted=adj_data.get("stop_loss_adjusted"),
            position_size_multiplier=float(adj_data.get("position_size_multiplier", 1.0)),
            execution_type_override=ExecutionType(adj_data["execution_type_override"]) if adj_data.get("execution_type_override") else None,
            tp1_adjusted=adj_data.get("tp1_adjusted"),
            tp2_adjusted=adj_data.get("tp2_adjusted"),
            remove_tp3=bool(adj_data.get("remove_tp3", False)),
            adjustment_reason=adj_data.get("adjustment_reason", ""),
        )

        # Parse audit checks
        checks_data = data.get("audit_checks", {})
        checks = AuditChecks(
            narrative_technical=checks_data.get("narrative_technical", "WARN — no data"),
            onchain_narrative=checks_data.get("onchain_narrative", "WARN — no data"),
            anomaly_interpretation=checks_data.get("anomaly_interpretation", "PASS — no flags"),
            calibrated_confidence=checks_data.get("calibrated_confidence", "WARN — no data"),
            sentiment_positioning=checks_data.get("sentiment_positioning", "PASS"),
            risk_reward=checks_data.get("risk_reward", "PASS"),
            portfolio_context=checks_data.get("portfolio_context", "PASS"),
            adversarial_check=checks_data.get("adversarial_check", "PASS"),
            smart_money_divergence=checks_data.get("smart_money_divergence", "PASS"),
            macro_alignment=checks_data.get("macro_alignment", "PASS"),
            temporal_bias=checks_data.get("temporal_bias", "PASS"),
        )

        # Parse anomaly interpretations
        anomaly_interps = [
            AnomalyInterpretation(
                flag=item.get("flag", "UNKNOWN"),
                value=str(item.get("value", "")),
                interpretation=item.get("interpretation", ""),
                impact=item.get("impact", "WARN_retained"),
                action=item.get("action"),
            )
            for item in data.get("anomaly_interpretation", [])
        ]

        approval_str = data.get("approval_type", "REJECTED")
        try:
            approval_type = ApprovalType(approval_str)
        except ValueError:
            approval_type = ApprovalType.REJECTED

        return AIAuditResult(
            approved=bool(data.get("approved", False)),
            approval_type=approval_type,
            final_confidence=float(data.get("final_confidence", 0.0)),
            confidence_modifier=float(data.get("confidence_modifier", 0.0)),
            parameter_adjustments=param_adj,
            audit_checks=checks,
            anomaly_interpretation=anomaly_interps,
            audit_summary=data.get("audit_summary", ""),
            strengths=data.get("strengths", []),
            risks=data.get("risks", []),
            do_not_enter_if=data.get("do_not_enter_if", []),
            execution_note=data.get("execution_note", ""),
            monitoring_notes=data.get("monitoring_notes", ""),
            position_adjustment=data.get("position_adjustment"),
            rejection_reason=data.get("rejection_reason"),
            audit_duration_ms=round(elapsed_ms, 1),
            audited_at=datetime.utcnow(),
        )

    def _create_circuit_breaker_rejection(
        self,
        package: FullSignalPackage,
        reason: str,
    ) -> AIAuditResult:
        """Create instant rejection from circuit breaker (no AI call)."""
        return AIAuditResult(
            approved=False,
            approval_type=ApprovalType.REJECTED,
            final_confidence=0.0,
            confidence_modifier=0.0,
            parameter_adjustments=ParameterAdjustments(adjustment_reason="Circuit breaker triggered — no modification possible"),
            audit_checks=AuditChecks(
                narrative_technical="N/A — circuit breaker",
                onchain_narrative="N/A",
                anomaly_interpretation="N/A",
                calibrated_confidence="N/A",
                sentiment_positioning="N/A",
                risk_reward="N/A",
                portfolio_context="FAIL — circuit breaker",
                adversarial_check="N/A",
                smart_money_divergence="N/A",
                macro_alignment="N/A",
                temporal_bias="N/A",
            ),
            anomaly_interpretation=[],
            audit_summary=f"Circuit breaker triggered: {reason}",
            strengths=[],
            risks=[reason],
            do_not_enter_if=["Circuit breaker active — no trading allowed"],
            execution_note="NO EXECUTION — circuit breaker active",
            monitoring_notes=f"Wait until condition resolves: {reason}",
            rejection_reason=reason,
            audit_duration_ms=0.0,
            audited_at=datetime.utcnow(),
        )

    def _create_timeout_rejection(self, package: FullSignalPackage, start_time: float) -> AIAuditResult:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        reason = f"AI audit timeout after {elapsed_ms:.0f}ms (limit: {self._timeout * 1000}ms)"
        return self._create_circuit_breaker_rejection(package, reason)

    def _create_error_rejection(self, package: FullSignalPackage, error: str, start_time: float) -> AIAuditResult:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        reason = f"AI audit error: {error}"
        result = self._create_circuit_breaker_rejection(package, reason)
        return result._replace(audit_duration_ms=round(elapsed_ms, 1)) if hasattr(result, '_replace') else result

    async def audit_p2p(self, opportunity: dict[str, Any]) -> dict[str, Any]:
        """
        P2P arbitrage audit. Returns dict (simpler schema than signal audit).
        """
        prompt = P2P_AUDIT_PROMPT_TEMPLATE.format(p2p_json=json.dumps(opportunity, indent=2))

        try:
            response = await self._call_ai_with_timeout(prompt, timeout_seconds=5)
            return json.loads(response)
        except Exception as e:
            logger.error(f"P2P audit failed: {e}")
            return {
                "approved": False,
                "grade": "SKIP",
                "risk_level": "HIGH",
                "recommendation": f"Audit unavailable: {e}",
                "abort_conditions": ["AI audit failed — do not execute"],
            }
