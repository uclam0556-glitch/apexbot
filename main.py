"""
APEX Trading System v5.0
Main Orchestrator Loop — Ultra World-Class Edition

40 coins × 5 timeframes — 12 indicators — Beautiful Telegram signals
"""

from __future__ import annotations

import structlog

def format_price(price: float) -> str:
    if not price: return "0.00"
    if price >= 1000: return f"{price:,.2f}"
    if price >= 1: return f"{price:.4f}"
    if price >= 0.001: return f"{price:.6f}"
    if price >= 0.00001: return f"{price:.8f}"
    return f"{price:.10f}"

# Programmatic coin classification sectors for APEX pullback limits (APEX v10.4 manual sectors)
def get_sector(symbol: str) -> str:
    base = symbol.split('/')[0]
    if base in ["FET", "RENDER", "WLD", "ARKM", "TAO"]:
        return "AI"
    elif base in ["ARB", "OP", "STRK", "MATIC", "POL"]:
        return "L2"
    elif base in ["PEPE", "WIF", "BONK", "SHIB", "FLOKI", "DOGE"]:
        return "MEME"
    elif base in ["SOL", "AVAX", "SUI", "APT", "SEI", "NEAR"]:
        return "L1"
    elif base in ["AAVE", "UNI", "LDO", "PENDLE", "GMX", "RUNE"]:
        return "DEFI"
    else:
        return "OTHER"

# Configure structlogging
import asyncio
import logging
import os
import signal
from datetime import datetime
from typing import Any

import ccxt.async_support as ccxt
import pandas as pd

from shared.config import get_config
from shared.models import (
    MarketRegime,
    Direction,
    FullSignalPackage,
    SignalCore,
    AIAuditResult,
    ParameterAdjustments
)

# Core Engines
from services.engine.mtf_engine import MTFEngine
from services.engine.smc_core import FormalizedSMCCore
from services.adversarial.tester import AdversarialSignalTester
from services.engine.confluence_v4 import ConfluenceEngineV4
from services.engine.risk_engine import RiskEngine
from database.timescaledb import init_timescaledb, insert_signal_record, create_shadow_trade, create_shadow_trade_blocked, update_shadow_trade, get_open_shadow_trades, insert_filter_block_record, is_on_cooldown, is_pullback_on_structure_cooldown, insert_shadow_trade, get_pullback_items_by_status, get_open_trades
from core.circuit_breaker import CircuitBreaker          # LEGACY → migrate to PortfolioRiskEngine v11.1
from core.correlation_filter import CorrelationFilter    # LEGACY → migrate to PortfolioRiskEngine v11.1
from core.transaction_costs import TransactionCostModel  # SHIM → delegates to services/execution/transaction_cost_model.py
from core.position_sizing import KellyPositionSizer      # LEGACY → locked until Phase 2 ML calibration
from core.session_tagger import SessionTagger
from models.signal_record import SignalRecord
from services.notifications.telegram_ui import start_telegram_bot, send_signal, build_signal_card, send_trade_result_notification, send_tp1_notification
from services.intelligence.rs_matrix import rs_matrix_engine
from services.intelligence.cvd_engine import calculate_cvd
from services.data.macro_calendar import is_macro_blackout_window
from services.engine.liquidation_detector import LiquidationCascadeDetector

# v5.0 Imports
from services.data.ws_manager import ExchangeWSManager
from services.intelligence.ml_regime import MLRegimeClassifier
from services.optimization.dynamic_weights import DynamicWeightsOptimizer
from shared.state import global_state

# V6.0 Shadow & Health
from services.engine.shadow_monitor import ShadowTradeMonitor
from services.engine.data_health import check_data_health
# v11.0 Institutional Modules
from services.data.validator import DataValidator, DataHealthStatus  # Direct institutional use
from services.risk.portfolio_risk_engine import (
    PortfolioRiskEngine,
    PortfolioRiskState,
    CircuitBreakerLevel,
)
from services.execution.transaction_cost_model import (
    TransactionCostModel as InstitutionalTCM,  # Full institutional model
    OrderUrgency,
)

# Ultra indicators
from services.indicators.technical import run_all_indicators
from services.indicators.market_data import get_market_context
from services.intelligence.ofi_engine import calculate_orderbook_imbalance

# Setup basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ApexMain")
struct_logger = structlog.get_logger("telemetry")
_config = get_config()

def check_mtf_gate(symbol: str, mtf_score: float, direction: str, regime: str, strategy: str = "TREND") -> bool:
    """
    MTF Hard Gate: blocks trading against the trend.
    Adjusted: Mean Reversion and Capitulation are exempt from strict trend requirements.
    
    Thresholds per regime (LONG direction):
      BULL:     mtf_score >= 0    (price must be trending up, even weakly)
      SIDEWAYS: mtf_score >= -1   (allow neutral; block only strong downtrends)
      BEAR:     mtf_score >= -2   (only block full waterfall scenarios)
    """
    # ─── STRATEGY EXEMPTIONS ──────────────────────────────────────────────
    if strategy == "CAPITULATION":
        return True  # Pure knife catching, MTF is irrelevant
        
    if strategy == "MEAN_REVERSION":
        # Allow counter-trend, but block if trend is extremely toxic
        if direction == "LONG" and mtf_score < -4.0:
            logger.info(f"{symbol} - [BLOCKED] MTF Gate: score={mtf_score:.1f} < -4.0 for MEAN_REVERSION LONG. Trend is too toxic.")
            return False
        if direction == "SHORT" and mtf_score > 4.0:
            logger.info(f"{symbol} - [BLOCKED] MTF Gate: score={mtf_score:.1f} > 4.0 for MEAN_REVERSION SHORT. Trend is too toxic.")
            return False
        return True

    # ─── TREND STRATEGY LOGIC ─────────────────────────────────────────────
    if direction == "LONG":
        # BULL: require at least neutral MTF (score >= 0)
        if regime == "BULL" and mtf_score < 0:
            logger.info(f"{symbol} - [BLOCKED] MTF Gate: score={mtf_score:.1f} < 0 for LONG in BULL. Trend is against us.")
            return False
        # SIDEWAYS: allow weak/neutral signal, block only clear downtrends
        if regime in ("SIDEWAYS", "CRISIS") and mtf_score < -2.0:
            logger.info(f"{symbol} - [BLOCKED] MTF Gate: score={mtf_score:.1f} < -2.0 for LONG in {regime}. Trend too negative.")
            return False
        # BEAR: only block full waterfall (score < -3)
        if regime == "BEAR" and mtf_score < -3.0:
            logger.info(f"{symbol} - [BLOCKED] MTF Gate: score={mtf_score:.1f} < -3.0 for LONG in BEAR. Trend too toxic for bounce.")
            return False
    if direction == "SHORT":
        if regime == "BEAR" and mtf_score > 0:
            logger.info(f"{symbol} - [BLOCKED] MTF Gate: score={mtf_score:.1f} > 0 for SHORT in BEAR. Trend is against us.")
            return False
        if regime in ("SIDEWAYS", "BULL", "CRISIS") and mtf_score > -2.0:
            logger.info(f"{symbol} - [BLOCKED] MTF Gate: score={mtf_score:.1f} > -2.0 for SHORT in {regime}. Need strong confirmation.")
            return False
    return True

class ApexSystem:
    def __init__(self):
        self.running = False
        self.config = _config
        self.exchange = ccxt.mexc({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        
        # Initialize Legacy Engines
        self.mtf_engine = MTFEngine()
        self.smc_core = FormalizedSMCCore()
        self.adversarial_tester = AdversarialSignalTester()
        self.confluence_engine = ConfluenceEngineV4()
        
        # LEGACY v10.5 Core Modules (preserved, will migrate to v11.0 in phases)
        self.circuit_breaker = CircuitBreaker()        # LEGACY → migrate to portfolio_risk_engine v11.1
        self.correlation_filter = CorrelationFilter()  # LEGACY → migrate to portfolio_risk_engine v11.1
        self.cost_model = TransactionCostModel()       # SHIM → delegates to InstitutionalTCM
        self.kelly_sizer = KellyPositionSizer()        # LOCKED → Kelly blocked until Phase 2 ML
        
        # v11.0 Institutional Risk Engine (ACTIVE — hard gate on all new positions)
        self.risk_engine = RiskEngine()
        self.portfolio_risk_engine = PortfolioRiskEngine()
        self.data_validator = DataValidator()
        self.institutional_tcm = InstitutionalTCM(exchange="BINANCE", order_type="maker")

        
        from services.engine.order_fill_monitor import OrderFillMonitor
        from services.execution.order_router import OrderRouter
        
        self.order_router = OrderRouter(self.exchange, is_live=self.config.trading.live_trading_enabled)
        self.fill_monitor = OrderFillMonitor(self.exchange, self.config)
        self.shadow_monitor = ShadowTradeMonitor()
        
        # v5.0 Engines
        self.ws_manager = ExchangeWSManager()
        self.ml_classifier = MLRegimeClassifier()
        self.weights_optimizer = DynamicWeightsOptimizer()
        self.liquidation_detector = LiquidationCascadeDetector()
        
        # Global State
        self.macro_state = None
        self.signals_sent_today = 0
        
        # Exposure Manager (P1)
        from services.engine.exposure_manager import ExposureManager
        from services.engine.guards import OpenPositionGuard
        self.exposure_manager = ExposureManager(self.config)
        self.position_guard = OpenPositionGuard()
        self.market_breadth = 50.0
        self.dominance_flow_bonus = 0.0
        self.dynamic_params = {}
        self.breadth_last_updated = 0.0
        
    async def fetch_market_data(self, symbol: str, timeframe: str = '1h', limit: int = 100) -> pd.DataFrame:
        """Helper to fetch OHLCV and convert to DataFrame."""
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Failed to fetch data for {symbol}: {e}")
            return pd.DataFrame()

    async def background_macro_updater(self):
        """Updates Macro and Rotation context every 1 hour."""
        while self.running:
            try:
                logger.info("Updating Macro & Rotation State...")
                if hasattr(self, 'macro_engine'):
                    self.macro_state = await self.macro_engine.get_full_macro_result()
                    try:
                        if hasattr(self, 'rotation_engine'):
                            self.rotation_state = self.rotation_engine.get_rotation_multipliers(
                                self.macro_state.dominance,
                                self.macro_state.macro_bias
                            )
                        logger.info(f"Macro Bias: {self.macro_state.macro_bias.value}")
                    except Exception as rot_err:
                        logger.warning(f"Rotation engine error (non-fatal): {rot_err}")
                        self.rotation_state = None
                else:
                    # Mock macro state if engine is not connected
                    class MockBias: value = "NEUTRAL"
                    class MockState:
                        macro_bias = MockBias()
                        dominance = "BTC"
                    self.macro_state = MockState()
                    self.rotation_state = None
                    
                # Update RS Matrix
                await rs_matrix_engine.update_matrix(self.config.trading.symbols)
                
                # Fetch Dynamic Auto-Tuner Params
                from database.dynamic_config import get_active_tuning_params
                try:
                    self.dynamic_params = await get_active_tuning_params()
                    if self.dynamic_params:
                        logger.info(f"Loaded {len(self.dynamic_params)} active tuning parameters from DB.")
                except Exception as e:
                    logger.debug(f"Could not fetch dynamic params: {e}")
                
            except Exception as e:
                logger.error(f"Error in macro updater: {e}")
                # Set a minimal fallback so the scanner loop doesn't block forever
                if self.macro_state is None:
                    from shared.models import MacroBias
                    self.macro_state = type('FallbackMacro', (), {
                        'macro_bias': MacroBias.NEUTRAL,
                        'dominance': None,
                    })()
                    self.rotation_state = None

            await asyncio.sleep(3600)  # 1 hour

    async def background_trade_tracker(self):
        """Continuously monitors open paper trades and closes them if TP/SL is hit."""
        from aiogram import Bot
        token = self.config.alerts.telegram_bot_token.get_secret_value()
        chat_id_str = self.config.alerts.telegram_chat_id
        bot = None
        if token and chat_id_str:
            bot = Bot(token=token)
            
        while self.running:
            try:
                open_trades = await get_open_shadow_trades()
                if open_trades:
                    logger.info(f"Tracking {len(open_trades)} open paper trades...")
                    for t in open_trades:
                        trade = dict(t)
                        symbol = trade['symbol']
                        # Fetch latest 1m candles to catch wicks (Stop Loss hits between 15s intervals)
                        ohlcv = await self.exchange.fetch_ohlcv(symbol, '1m', limit=2)
                        if not ohlcv:
                            continue
                        
                        # Use the highest high and lowest low of the last 2 minutes
                        recent_high = max([c[2] for c in ohlcv])
                        recent_low = min([c[3] for c in ohlcv])
                        current_price = ohlcv[-1][4] # latest close
                        
                        # ─── MFE/MAE TRACKING ────────────────────────────────────────────────
                        from shared.state import global_state
                        trade_id = trade['id']
                        if trade_id not in global_state.trade_excursions:
                            # Rebuild excursions from opening time to now to prevent amnesia
                            historical_high = recent_high
                            historical_low = recent_low
                            if 'opened_at' in trade and trade['opened_at']:
                                try:
                                    from datetime import datetime
                                    opened_val = trade['opened_at']
                                    if isinstance(opened_val, str):
                                        dt_str = opened_val.replace(' ', 'T')
                                        if '.' in dt_str: dt_str = dt_str.split('.')[0]
                                        if not dt_str.endswith('Z') and '+' not in dt_str:
                                            dt_str += 'Z'
                                        opened_dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                                    else:
                                        opened_dt = opened_val
                                    since_ms = int(opened_dt.timestamp() * 1000)
                                    # Fetch history since open to reconstruct peak high/low
                                    hist_ohlcv = await self.exchange.fetch_ohlcv(symbol, '15m', since=since_ms, limit=1000)
                                    if hist_ohlcv:
                                        historical_high = max([c[2] for c in hist_ohlcv] + [recent_high])
                                        historical_low = min([c[3] for c in hist_ohlcv] + [recent_low])
                                except Exception as e:
                                    logger.warning(f"Failed to rebuild excursions for {trade_id}: {e}")
                                    
                            global_state.trade_excursions[trade_id] = {
                                "high": historical_high,
                                "low": historical_low
                            }
                            logger.info(f"Rebuilt trade excursions for {trade_id}: High={historical_high}, Low={historical_low}")
                        
                        excursions = global_state.trade_excursions[trade_id]
                        if recent_high > excursions["high"]:
                            excursions["high"] = recent_high
                        if recent_low < excursions["low"]:
                            excursions["low"] = recent_low

                        entry = trade['entry_price']
                        if trade['direction'] == 'LONG':
                            max_profit_pct = (excursions["high"] - entry) / entry * 100
                            max_drawdown_pct = (excursions["low"] - entry) / entry * 100
                        else:
                            max_profit_pct = (entry - excursions["low"]) / entry * 100
                            max_drawdown_pct = (entry - excursions["high"]) / entry * 100
                            
                        # Bug 4 Fix: Ensure MFE is positive and MAE is negative
                        max_profit_pct = max(0.0, max_profit_pct)
                        max_drawdown_pct = min(0.0, max_drawdown_pct)
                            
                        status = None
                        pnl_pct = 0.0

                        # ─── V7 SMART TIME-BASED EXIT ──────────────────────────
                        if 'opened_at' in trade and trade['opened_at']:
                            try:
                                from datetime import datetime
                                dt_str = trade['opened_at'].replace(' ', 'T')
                                if '.' in dt_str: dt_str = dt_str.split('.')[0]
                                opened_dt = datetime.fromisoformat(dt_str)
                                minutes_open = (datetime.utcnow() - opened_dt).total_seconds() / 60
                                trade_strat = trade.get('strategy', 'TREND')
                                
                                if trade['direction'] == 'LONG':
                                    curr_pnl_pct = (current_price - trade['entry_price']) / trade['entry_price'] * 100
                                else:
                                    curr_pnl_pct = (trade['entry_price'] - current_price) / trade['entry_price'] * 100
                                
                                # 1. Smart Early Exit: 120 mins and stuck (-1% to 1%)
                                if minutes_open > 120 and abs(curr_pnl_pct) <= 1.0:
                                    if trade['status'] == 'OPEN':
                                        if curr_pnl_pct <= -0.4:
                                            status = 'TIMEOUT_SMALL_LOSS'
                                        elif curr_pnl_pct >= 0.4:
                                            status = 'TIMEOUT_SMALL_WIN'
                                        else:
                                            status = 'TIMEOUT_BREAKEVEN'
                                            
                                        pnl_pct = curr_pnl_pct
                                        logger.info(f"⌛ {symbol} - Open for {int(minutes_open)}m. Stuck at {pnl_pct:.2f}%. Smart Exit ({status}) applied.")
                                
                                # 2. Hard Timeout: 6 hours for mean reversion / capitulation
                                elif trade_strat in ['CAPITULATION', 'MEAN_REVERSION'] and minutes_open > 360:
                                    if trade['status'] == 'OPEN':
                                        status = 'TIMEOUT'
                                        pnl_pct = curr_pnl_pct
                                        logger.info(f"⌛ {symbol} - {trade_strat} open for >6h. Force closing.")
                                        
                            except Exception as parse_err:
                                logger.debug(f"Time-based exit parse error: {parse_err}")

                        # ─── V8 CHANDELIER EXIT (DYNAMIC TRAILING STOP) ──────────────────────
                        # If trade is in > 2.0% profit, trail SL 1.5% behind highest/lowest point
                        try:
                            if trade['direction'] == 'LONG':
                                if max_profit_pct >= 2.0:
                                    trail_sl = excursions["high"] * 0.985
                                    if trail_sl > trade['stop_loss']:
                                        logger.info(f"📈 {symbol} - [Chandelier Exit] Trailing SL up to {trail_sl:.4f} (MFE: {excursions['high']:.4f})")
                                        from database.timescaledb import update_signal_sl
                                        await update_signal_sl(trade['id'], trail_sl)
                                        trade['stop_loss'] = trail_sl
                            else:
                                if max_profit_pct >= 2.0:
                                    trail_sl = excursions["low"] * 1.015
                                    if trail_sl < trade['stop_loss'] or trade['stop_loss'] == 0: # handle missing SL
                                        logger.info(f"📉 {symbol} - [Chandelier Exit] Trailing SL down to {trail_sl:.4f} (MFE: {excursions['low']:.4f})")
                                        from database.timescaledb import update_signal_sl
                                        await update_signal_sl(trade['id'], trail_sl)
                                        trade['stop_loss'] = trail_sl
                        except Exception as trail_err:
                            logger.error(f"Error updating trailing SL: {trail_err}")

                        # Standard TP/SL logic (only if not already TIMEOUT)
                        if not status and trade['direction'] == 'LONG':
                            if recent_high >= trade['take_profit_1']:
                                status = 'WON'
                                pnl_pct = (trade['take_profit_1'] - trade['entry_price']) / trade['entry_price'] * 100
                            elif recent_low <= trade['stop_loss']:
                                pnl_pct = (trade['stop_loss'] - trade['entry_price']) / trade['entry_price'] * 100
                                status = 'WON_BREAKEVEN' if pnl_pct > 0 else 'LOST'

                        # SHORT logic (mirror of LONG)
                        elif not status and trade['direction'] == 'SHORT':
                            if recent_low <= trade['take_profit_1']:
                                status = 'WON'
                                pnl_pct = (trade['entry_price'] - trade['take_profit_1']) / trade['entry_price'] * 100
                            elif recent_high >= trade['stop_loss']:
                                pnl_pct = (trade['entry_price'] - trade['stop_loss']) / trade['entry_price'] * 100
                                status = 'WON_BREAKEVEN' if pnl_pct > 0 else 'LOST'
                                
                        if status in ['WON', 'LOST', 'WON_BREAKEVEN', 'TIMEOUT', 'TIMEOUT_BREAKEVEN', 'TIMEOUT_SMALL_WIN', 'TIMEOUT_SMALL_LOSS']:
                            duration_minutes = 0.0
                            if 'opened_at' in trade and trade['opened_at']:
                                try:
                                    from datetime import datetime
                                    dt_str = trade['opened_at'].replace(' ', 'T')
                                    if '.' in dt_str: dt_str = dt_str.split('.')[0]
                                    opened_dt = datetime.fromisoformat(dt_str)
                                    duration_minutes = (datetime.utcnow() - opened_dt).total_seconds() / 60.0
                                except Exception:
                                    pass

                            await update_shadow_trade(
                                trade['id'], 
                                status, 
                                max_profit_pct,
                                max_drawdown_pct,
                                int(duration_minutes)
                            )
                            # Cleanup memory
                            from shared.state import global_state
                            if trade['id'] in global_state.trade_excursions:
                                del global_state.trade_excursions[trade['id']]
                                
                            self.position_guard.on_trade_closed(symbol)
                                
                            logger.info(f"Trade {symbol} {status} at {current_price} ({pnl_pct:+.2f}%) | MFE: {max_profit_pct:+.2f}% | MAE: {max_drawdown_pct:+.2f}% | Dur: {duration_minutes:.1f}m")
                            if bot and not trade.get('is_shadow', True):
                                try:
                                    await send_trade_result_notification(bot, int(chat_id_str), trade, status, pnl_pct)
                                except Exception as e:
                                    logger.error(f"Failed to send result notification: {e}")
            except Exception as e:
                logger.error(f"Error in trade tracker: {e}")
                
            await asyncio.sleep(15)  # Check every 15 seconds (reduced from 60s for faster TP detection)

        if bot:
            await bot.session.close()

    async def mock_ai_auditor(self, package: FullSignalPackage) -> AIAuditResult:
        """
        Mocks the LLM Auditor response.
        In production, this calls OpenAI/Anthropic and parses the JSON response.
        """
        logger.info(f"Sending {package.signal.symbol} to AI Auditor...")
        await asyncio.sleep(1) # Simulate network delay
        
        return AIAuditResult(
            signal_id=package.signal.id,
            approved=True,
            confidence_score=0.9,
            reasoning="Mock AI: Macro is aligned. Confluence is exceptionally high. Proceeding with Trade.",
            parameter_adjustments=ParameterAdjustments(
                stop_loss_adjusted=None,
                tp1_adjusted=None,
                tp2_adjusted=None,
                remove_tp3=False,
                position_size_multiplier=1.0,
                execution_type_override=None
            ),
            audited_at=datetime.utcnow()
        )

    async def run_trading_pipeline(self):
        """Main loop that scans pairs every 5 minutes."""
        # Wait for first macro update
        while not self.macro_state:
            await asyncio.sleep(1)
            
        while self.running:
            if global_state.is_paused:
                logger.info("Bot is PAUSED. Resting for 60 seconds...")
                await asyncio.sleep(60)
                continue
                
            logger.info("=== STARTING SCAN CYCLE ===")
            
            # ─── V6.2 ON-CHAIN DATA BRIEFING ─────────────────────────────────────────
            try:
                from services.data.onchain import OnChainPipeline
                oc_pipeline = OnChainPipeline()
                oc_data = await oc_pipeline.get_smart_money_data()
                flow_val = oc_data.exchange_net_flow
                flow_type = "outflow, bullish" if flow_val < 0 else "inflow, bearish"
                logger.info(f"On-Chain Briefing | Exchange flow BTC: {flow_val:.0f} ({flow_type}) | SOPR: {oc_data.sopr_ratio:.2f}")
            except Exception as e:
                logger.debug(f"On-Chain fetch failed: {e}")
            
            # ─── MACRO BLACKOUT CHECK ────────────────────────────────────────────────
            is_blackout, blackout_reason = is_macro_blackout_window()
            if is_blackout:
                logger.info(f"🛑 MACRO BLACKOUT: {blackout_reason}. Pausing scan for 5 minutes.")
                await asyncio.sleep(300)
                continue
                
            # ─── CIRCUIT BREAKER V10.5 ───────────────────────────────────────────────
            # Re-fetch stats using TimescaleDB to determine PnL drawdowns for Circuit Breaker
            from database.timescaledb import get_stats_timescale
            current_stats = await get_stats_timescale()
            pnl_sum = current_stats.get('pnl_sum', 0.0)
            win_rate = current_stats.get('win_rate', 0.0)
            
            self.circuit_breaker.update_pnl(pnl_sum, 0.0)
            breaker_status = self.circuit_breaker.check()
            
            if not breaker_status.get('allowed', True):
                logger.warning(f"🚨 CIRCUIT BREAKER TRIPPED: {breaker_status.get('reason')}. Pausing operations.")
                await asyncio.sleep(300)
                continue
            
            exposure = await self.exposure_manager.get_current_exposure()
            open_trades = exposure["open_trades"]
            open_symbols = list(exposure["open_symbols"])
            active_pb = exposure["waiting_pullbacks"]
            active_pb_symbols = list(exposure["waiting_symbols"])
            open_and_pending_symbols = list(set(open_symbols + active_pb_symbols))
            
            # ─── PRE-FETCH CORRELATION DATA ──────────────────────────────────────────
            prices_30d = {}
            if open_and_pending_symbols:
                logger.info(f"Fetching 30d history for {len(open_and_pending_symbols)} open/pending positions for correlation matrix...")
                open_tasks = [self.fetch_market_data(sym, '1d', 35) for sym in open_and_pending_symbols]
                open_results = await asyncio.gather(*open_tasks, return_exceptions=True)
                for sym, df_1d_open in zip(open_and_pending_symbols, open_results):
                    if isinstance(df_1d_open, pd.DataFrame) and not df_1d_open.empty:
                        prices_30d[sym] = df_1d_open['close']

            # ─── RS MATRIX PRE-FILTER (Top 30 Only) ──────────────────────────────────
            top_rs_coins = rs_matrix_engine.get_top_n(30)
            scan_symbols = [c['symbol'] for c in top_rs_coins] if top_rs_coins else self.config.trading.symbols[:30]
            logger.info(f"Pre-filtered top {len(scan_symbols)} strongest coins for scanning.")

            # ─── GLOBAL REGIME CLASSIFICATION (BTC) ──────────────────────────────────
            try:
                logger.info("Calculating Global Market Regime (BTC-driven)...")
                btc_df = await self.fetch_market_data('BTC/USDT', '1h', 100)
                if isinstance(btc_df, pd.DataFrame) and not btc_df.empty:
                    if not self.ml_classifier.is_trained:
                        self.ml_classifier.train_hmm(btc_df)
                    btc_regime = self.ml_classifier.classify_current_regime(btc_df)
                    global_state.regime = btc_regime.value
                    logger.info(f"Global Market Regime updated: {global_state.regime}")
                else:
                    logger.warning("Could not fetch BTC data for global regime. Using default SIDEWAYS.")
                    global_state.regime = "SIDEWAYS"
            except Exception as e:
                logger.error(f"Failed to calculate global regime: {e}")
                global_state.regime = "SIDEWAYS"
                

            # ─── MARKET BREADTH ENGINE (EMA200) & DOMINANCE FLOW (CACHED) ─────────────────────
            import time
            current_time = time.time()
            if current_time - self.breadth_last_updated > 3600:  # 60 minutes cache
                macro_breadth_symbols = self.config.trading.symbols[:50] if len(self.config.trading.symbols) >= 50 else self.config.trading.symbols
                logger.info(f"Calculating Market Breadth ({len(macro_breadth_symbols)} Macro Coins vs EMA200)...")
                breadth_tasks = [self.fetch_market_data(sym, '1d', 210) for sym in macro_breadth_symbols]
                breadth_results = await asyncio.gather(*breadth_tasks, return_exceptions=True)
                
                coins_above_ema200 = 0
                valid_coins = 0
                btc_7d_return = 0.0
                alt_7d_returns = []
                
                for sym, df_breadth in zip(macro_breadth_symbols, breadth_results):
                    if isinstance(df_breadth, pd.DataFrame) and len(df_breadth) >= 200:
                        valid_coins += 1
                        current_close = df_breadth['close'].iloc[-1]
                        ema_200 = df_breadth['close'].rolling(200).mean().iloc[-1]
                        if current_close > ema_200:
                            coins_above_ema200 += 1
                            
                        if len(df_breadth) >= 8:
                            close_7d = df_breadth['close'].iloc[-8]
                            ret_7d = (current_close - close_7d) / close_7d * 100
                            if sym == 'BTC/USDT':
                                btc_7d_return = ret_7d
                            else:
                                alt_7d_returns.append(ret_7d)
                
                breadth_pct = (coins_above_ema200 / valid_coins * 100) if valid_coins > 0 else 50.0
                self.market_breadth = breadth_pct
                logger.info(f"Market Breadth updated: {breadth_pct:.1f}% of top coins are above 1D EMA200.")
                
                # DOMINANCE FLOW CALCULATION
                avg_alt_7d_return = sum(alt_7d_returns) / len(alt_7d_returns) if alt_7d_returns else 0.0
                dominance_7d_change = btc_7d_return - avg_alt_7d_return
                self.dominance_flow_bonus = 0.0
                
                if dominance_7d_change > 1.5:
                    self.dominance_flow_bonus = -5.0
                    logger.info(f"DOMINANCE FLOW updated: BTC ({btc_7d_return:+.1f}%) > Alts ({avg_alt_7d_return:+.1f}%). Flow to BTC. Alt Penalty: -5")
                elif dominance_7d_change < -1.5:
                    self.dominance_flow_bonus = 5.0
                    logger.info(f"DOMINANCE FLOW updated: BTC ({btc_7d_return:+.1f}%) < Alts ({avg_alt_7d_return:+.1f}%). Flow to Alts. Alt Bonus: +5")
                else:
                    logger.info(f"DOMINANCE FLOW updated: Neutral (Diff: {dominance_7d_change:.1f}%). No penalty/bonus.")
                    
                self.breadth_last_updated = current_time
            else:
                breadth_pct = self.market_breadth
                logger.info(f"Using cached Market Breadth: {breadth_pct:.1f}% (valid for {int((3600 - (current_time - self.breadth_last_updated)) / 60)} more mins)")
            
            # Dynamic config based on breadth (Calibrated for Isotonic Win Probabilities)
            # Dynamic config based on breadth (Calibrated for Isotonic Win Probabilities)
            dynamic_min_score = 45.0  # Slightly relaxed for testing
            if breadth_pct < 40.0:
                dynamic_min_score = 48.0
                logger.warning(f"RISK-OFF: Breadth < 40% ({breadth_pct:.1f}%). Raising min probability gate to 48.0%.")
            elif breadth_pct > 70.0:
                dynamic_min_score = 42.0
                logger.info(f"RISK-ON: Breadth > 70% ({breadth_pct:.1f}%). Lowering min probability gate to 48.0%.")

            for symbol in scan_symbols:
                if not self.running:
                    break
                    
                if symbol in open_symbols:
                    logger.debug(f"{symbol} already has an open trade. Skipping to avoid duplicate signals.")
                    continue
                    
                if self.position_guard.is_open(symbol):
                    logger.debug(f"{symbol} already has an open shadow trade. Skipping to avoid shadow duplication.")
                    continue
                    
                try:
                    global_state.current_symbol = symbol
                    global_state.last_scan_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                    logger.info(f"Scanning {symbol}...")
                    
                    # ─── LIQUIDATION CASCADE CHECK ───────────────────────────────────────────
                    if self.liquidation_detector.is_cascade_in_progress(symbol):
                        logger.warning(f"🚨 {symbol} - [BLOCKED] Liquidation Cascade in progress. Skipping.")
                        await insert_filter_block_record(symbol, "UNKNOWN", "Liquidation Cascade", 0.0)
                        continue
                    
                    # 1. Fetch Multi-Timeframe Data (ALL 5 TFs) concurrently
                    timeframes_to_fetch = ['1d', '4h', '1h', '15m', '5m']
                    limits = [100 if tf in ['1d', '4h'] else 200 for tf in timeframes_to_fetch]
                    
                    tasks = [self.fetch_market_data(symbol, tf, limit) for tf, limit in zip(timeframes_to_fetch, limits)]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    tf_data = {}
                    for tf, df_tf in zip(timeframes_to_fetch, results):
                        if isinstance(df_tf, pd.DataFrame) and not df_tf.empty:
                            tf_data[tf] = df_tf
                    
                    if '1h' not in tf_data:
                        logger.warning(f"{symbol} - Could not fetch 1h data. Skipping.")
                        continue
                        
                    # ─── ADVANCED INSTITUTIONAL FILTER: CORRELATION RISK ──────────────────
                    df_1d_sym = tf_data.get('1d', pd.DataFrame())
                    if not df_1d_sym.empty and open_and_pending_symbols:
                        prices_30d[symbol] = df_1d_sym['close']
                        corr_result = self.correlation_filter.check_correlation(symbol, open_and_pending_symbols, prices_30d)
                        if not corr_result.is_safe:
                            logger.info(f"{symbol} - [BLOCKED] Correlation Risk. Highly correlated ({corr_result.max_correlation_value:.2f}) with open/pending position {corr_result.correlated_with}. Skipping.")
                            await insert_filter_block_record(symbol, "UNKNOWN", "Correlation Risk", 0.0)
                            continue
                    
                    df_1h = tf_data['1h']
                    current_price = df_1h['close'].iloc[-1]
                    atr_1h = (df_1h['high'] - df_1h['low']).rolling(14).mean().iloc[-1]
                    
                    # --- RSI (Real) ---
                    delta = df_1h['close'].diff()
                    gain = delta.clip(lower=0).rolling(14).mean()
                    loss = (-delta.clip(upper=0)).rolling(14).mean()
                    rs = gain / loss.replace(0, 1e-9)
                    rsi_series = 100 - (100 / (1 + rs))
                    rsi_now = rsi_series.iloc[-1]
                    
                    # --- Volume Analysis ---
                    # Use a proxy for RVOL: Daily average volume divided by 24 for baseline
                    # Requires 1d timeframe to be loaded
                    df_1d = tf_data.get('1d', pd.DataFrame())
                    if not df_1d.empty and len(df_1d) >= 20:
                        daily_vol_avg_20 = df_1d['volume'].rolling(20).mean().iloc[-1]
                        baseline_hourly_vol = daily_vol_avg_20 / 24.0
                        daily_volume_usd = daily_vol_avg_20 * current_price
                    else:
                        baseline_hourly_vol = df_1h['volume'].rolling(24).mean().iloc[-1]
                        daily_volume_usd = baseline_hourly_vol * 24.0 * current_price
                        
                    avg_vol_3 = df_1h['volume'].iloc[-3:].mean()
                    vol_ratio = avg_vol_3 / baseline_hourly_vol if baseline_hourly_vol > 0 else 1.0
                    
                    logger.info(f"{symbol} | Price=${format_price(current_price)} | RSI={rsi_now:.1f} | Vol={vol_ratio:.2f}x | TFs loaded={list(tf_data.keys())}")

                    # ─── BUG 9 & 10: DATA VALIDATOR (HARD BLOCKS & SCORING) ────────────────
                    try:
                        ws_data = global_state.live_prices.get(symbol, {})
                        if not ws_data:
                            from shared.symbols import normalize_symbol
                            ws_data = global_state.live_prices.get(normalize_symbol(symbol), {})
                        last_ws_ts = ws_data.get("timestamp", 0)
                        last_ws_update = float(last_ws_ts) if last_ws_ts > 0 else None

                        ohlcv_vol_series = tf_data['15m']['volume'].values[-20:] if '15m' in tf_data and len(tf_data['15m']) >= 20 else None
                        curr_vol_15m = tf_data['15m']['volume'].iloc[-1] if '15m' in tf_data and not tf_data['15m'].empty else avg_vol_3
                        prev_1h_price = df_1h['close'].iloc[-2] if len(df_1h) >= 2 else None
                        
                        data_report = self.data_validator.validate(
                            symbol=symbol,
                            last_ws_update=last_ws_update,
                            ohlcv_volume_series=ohlcv_vol_series,
                            current_volume=curr_vol_15m,
                            daily_volume_usd=daily_volume_usd,
                            funding_rate=0.0, # Spot only
                            market_type="SPOT",
                            bid_price=current_price * 0.999, # Fallback
                            ask_price=current_price * 1.001, # Fallback
                            secondary_exchange_price=None,
                            current_price=current_price,
                            prev_hour_price=prev_1h_price
                        )
                        
                        if not data_report.is_tradeable():
                            reasons = "; ".join(data_report.block_reasons)
                            logger.warning(f"🛑 {symbol} - [BLOCKED] DataValidator: {reasons}")
                            await insert_filter_block_record(symbol, "UNKNOWN", f"Data Health: {reasons}", data_report.raw_score)
                            continue
                            
                        # Bug 10: Dynamic Penalty (Subtract score instead of hard -5)
                        v7_data_penalty = (1.0 - data_report.raw_score) * 10.0 # Up to -10 points based on health
                        
                    except Exception as e:
                        logger.error(f"{symbol} - DataValidator error: {e}")
                        v7_data_penalty = 0.0
                    # ─── FILTER 1: GLOBAL REGIME ASSIGNMENT ──────────────────────────────
                    regime_val = global_state.regime
                    current_regime = MarketRegime(regime_val)

                    # ─── FILTER 1.1: CIRCUIT BREAKER ──────────────────────────────────────
                    if not await self.exposure_manager.can_add_market_position(symbol, breadth_pct, regime_val):
                        continue

                    # ─── FILTER 1.2: COOLDOWN FILTER ──────────────────────────────────────
                    if await is_on_cooldown(symbol, cooldown_hours=4):
                        continue
                        
                    # ─── FILTER 1.2b: PULLBACK STRUCTURE COOLDOWN FILTER ──────────────────
                    if await is_pullback_on_structure_cooldown(symbol):
                        logger.info(f"{symbol} - [BLOCKED] Pullback Structure Cooldown: active EXPIRED_STRUCTURE within 120 min. Skipping.")
                        continue

                    # ─── FILTER 1.3: EXHAUSTION FILTER ────────────────────────────────────
                    price_change_4h = (
                        (df_1h['close'].iloc[-1] - df_1h['close'].iloc[-5]) /
                        df_1h['close'].iloc[-5] * 100
                    ) if len(df_1h) >= 5 else 0.0
                    
                    if price_change_4h > 5.0:
                        logger.info(f"{symbol} - [BLOCKED] Exhaustion Filter: Up {price_change_4h:.2f}% in 4h. Skipping LONG.")
                        continue

                    # ─── FILTER 2: SESSION FILTER ────────────────────────────────────────────
                    utc_hour = datetime.utcnow().hour
                    # if 22 <= utc_hour or utc_hour < 1:
                    #     logger.info(f"{symbol} - [BLOCKED] Session Filter: Dead zone {utc_hour}:00 UTC. Skipping.")
                    #     continue

                    # ─── FILTER 3: VOLUME GATE ────────────────────────────────────────────────
                    if avg_vol_3 < baseline_hourly_vol * 0.25:
                        logger.info(f"{symbol} - [BLOCKED] Volume Gate: Vol={avg_vol_3:.0f} < 25% of 24h baseline {baseline_hourly_vol:.0f}. Skipping.")
                        continue

                    # ─── CVD ANALYSIS ─────────────────────────────────────────────────────────
                    cvd_result = {"score": 0, "divergence": False, "cvd_signal": "NEUTRAL"}
                    df_5m_cvd = tf_data.get('5m', pd.DataFrame())
                    if not df_5m_cvd.empty:
                        cvd_result = calculate_cvd(df_5m_cvd, lookback=20)

                    cvd_signal = cvd_result.get("cvd_signal", "NEUTRAL")
                    cvd_score_val = cvd_result.get("score", 0)

                    # ─── REAL ORDER FLOW IMBALANCE (OFI) & SPREAD ───────────────────────────
                    spread_pct = 0.0
                    try:
                        orderbook = await self.exchange.fetch_order_book(symbol, limit=20)
                        from services.intelligence.ofi_engine import calculate_orderbook_imbalance
                        ofi_real = calculate_orderbook_imbalance(orderbook, depth=20)
                        if orderbook['asks'] and orderbook['bids']:
                            best_ask = orderbook['asks'][0][0]
                            best_bid = orderbook['bids'][0][0]
                            if best_bid > 0:
                                spread_pct = (best_ask - best_bid) / best_bid * 100
                    except Exception as e:
                        logger.warning(f"{symbol} - Failed to fetch orderbook for OFI/Spread: {e}")
                        from services.intelligence.ofi_engine import OFIResult
                        ofi_real = OFIResult(0.0, 0.0, 0.0)

                    # ─── GLOBAL VOLUME SPIKE & WICK RATIO ─────────────────────────────────────
                    lower_wick_ratio = 0.0
                    vol_ratio_15m = 0.0
                    df_15m_check = tf_data.get('15m', pd.DataFrame())
                    if not df_15m_check.empty and len(df_15m_check) >= 3:
                        last_closed = df_15m_check.iloc[-2]
                        candle_range = last_closed['high'] - last_closed['low']
                        if candle_range > 0:
                            lower_wick_ratio = (min(last_closed['open'], last_closed['close']) - last_closed['low']) / candle_range
                        
                        avg_vol_15m = df_15m_check['volume'].iloc[-12:-2].mean()
                        if avg_vol_15m > 0:
                            vol_ratio_15m = float(last_closed['volume'] / avg_vol_15m)

                    # ─── V11 MTF ALIGNMENT (WEIGHTED SCORE & BONUS) ───────────────────────────
                    # MTF is no longer a hard gate, it contributes to V7 score and DirectionSelector.
                    from services.engine.mtf_engine import compute_mtf_score, get_mtf_v7_bonus
                    tf_signals = {}
                    for tf in ['1d', '4h', '1h', '15m', '5m']:
                        if tf in tf_data and not tf_data[tf].empty:
                            tf_signals[tf] = self.mtf_engine.get_trend_direction(tf_data[tf])
                    
                    mtf_score_val, mtf_is_strong = compute_mtf_score(tf_signals)

                    # ─── DIRECTION & STRATEGY SELECTION (V11) ────────────────────────────────
                    regime_val = current_regime.value
                    
                    # Calculate Premium/Discount (1h 100-candle structural range)
                    df_1h_recent = df_1h.iloc[-100:] if len(df_1h) >= 100 else df_1h
                    high_100 = df_1h_recent['high'].max()
                    low_100 = df_1h_recent['low'].min()
                    premium_discount = (current_price - low_100) / (high_100 - low_100) if high_100 > low_100 else 0.5
                    
                    price_change_1h = (
                        (df_1h['close'].iloc[-1] - df_1h['close'].iloc[-5]) /
                        df_1h['close'].iloc[-5] * 100
                    ) if len(df_1h) >= 5 else 0.0
                    market_ctx = await get_market_context(symbol, price_change_1h)
                    
                    fear_greed_val = market_ctx.get('fear_greed', {}).get('value', 50)
                    
                    from services.engine.direction_selector import direction_selector
                    trade_direction = direction_selector.select_direction(
                        symbol=symbol,
                        mtf_score=mtf_score_val,
                        cvd_signal=cvd_signal,
                        regime=regime_val,
                        premium_discount=premium_discount,
                        fear_greed=fear_greed_val
                    )
                    
                    if not trade_direction:
                        logger.info(f"{symbol} - [BLOCKED] Direction Selector: No clear edge for Long/Short. MTF={mtf_score_val:.1f}, CVD={cvd_signal}, PD={premium_discount:.2f}")
                        continue
                        
                    from services.engine.strategy_router import strategy_router
                    trade_strategy = strategy_router.get_strategy(regime_val)
                    
                    if not trade_strategy:
                        logger.info(f"{symbol} - [BLOCKED] Strategy Router: Trading stopped for regime {regime_val}.")
                        continue
                        
                    # ─── DYNAMIC REGIME PARAMS ───────────────────────────────────────────────
                    regime_params = strategy_router.get_params(regime_val)
                    dynamic_min_score = regime_params.get("min_v7_score", 45.0)
                    
                    # Overrides based on extreme conditions
                    if trade_direction == "LONG" and regime_val == "BEAR":
                        # 1. Base Panic: RSI < 25 AND Volume spike > 1.5x
                        is_panic = rsi_now < 25 and vol_ratio_15m > 1.5
                        is_bought = ofi_real.ofi_score > 0 or lower_wick_ratio > 0.4
                        if is_panic and is_bought:
                            trade_strategy = "CAPITULATION"
                            logger.info(f"{symbol} - [CAPITULATION CATCHER] RSI={rsi_now:.1f} Vol={vol_ratio_15m:.1f}x Wick={lower_wick_ratio:.2f}")

                    directional_mtf = mtf_score_val if trade_direction == "LONG" else -mtf_score_val
                    mtf_v7_bonus = get_mtf_v7_bonus(directional_mtf)
                    logger.info(f"{symbol} - MTF Score: {mtf_score_val:.2f} ({trade_direction}) -> V7 Bonus: {mtf_v7_bonus:+.1f}")


                    # ─── ADVANCED INSTITUTIONAL FILTER 1: ATR MOMENTUM EXHAUSTION ─────────────
                    price_change_4h_pct = (
                        (df_1h['close'].iloc[-1] - df_1h['close'].iloc[-5]) /
                        df_1h['close'].iloc[-5] * 100
                    ) if len(df_1h) >= 5 else 0.0
                    
                    atr_1h_pct = (atr_1h / current_price) * 100 if current_price > 0 else 0.0
                    
                    # ─── ADVANCED INSTITUTIONAL FILTER 2: GRADIENT PREMIUM ZONE ────────────────
                    in_premium_zone = False
                    if len(df_1h) >= 96:
                        high_96h = df_1h['high'].iloc[-96:].max()
                        low_96h = df_1h['low'].iloc[-96:].min()
                    else:
                        high_96h = df_1h['high'].max()
                        low_96h = df_1h['low'].min()
                        
                    range_96h = high_96h - low_96h
                    premium_discount = 1.0
                    if range_96h > 0:
                        premium_discount = (current_price - low_96h) / range_96h
                        premium_threshold = high_96h - (range_96h * 0.30) # Top 30%
                        if current_price >= premium_threshold and trade_strategy == "TREND" and trade_direction == "LONG":
                            in_premium_zone = True
                            logger.info(f"{symbol} - Price in Premium Zone (Top 30% of 96h). Flagged for Overextension Index.")

                    # ─── ADVANCED INSTITUTIONAL FILTER 1: MOMENTUM EXHAUSTION ──────────────────
                    momentum_penalty = 0.0
                    if trade_direction == "LONG" and trade_strategy == "TREND":
                        is_bearish_cvd = cvd_score_val < 0
                        is_rsi_overbought = rsi_now > 78
                        is_premium_bearish = in_premium_zone and is_bearish_cvd
                        
                        # Extreme hard block conditions
                        if price_change_4h_pct > (4 * atr_1h_pct) or price_change_4h_pct > 8.0 or \
                           (price_change_4h_pct > (2 * atr_1h_pct) and is_bearish_cvd) or \
                           (price_change_4h_pct > (2 * atr_1h_pct) and is_rsi_overbought) or \
                           (price_change_4h_pct > (2 * atr_1h_pct) and is_premium_bearish):
                            logger.info(f"{symbol} - [BLOCKED] Momentum Exhaustion (Hard Block). Up {price_change_4h_pct:.2f}%. Late impulse trap. Skipping.")
                            await insert_filter_block_record(symbol, trade_strategy or "UNKNOWN", "Momentum Exhaustion", 0.0)
                            proxy_sl = current_price - (1.5 * atr_1h) if trade_direction == "LONG" else current_price + (1.5 * atr_1h)
                            proxy_tp = current_price + (3.0 * atr_1h) if trade_direction == "LONG" else current_price - (3.0 * atr_1h)
                            block_reason = "Momentum Exhaustion"
                            # We don't continue here to allow V7 calculation and logging
                        
                        # Penalty conditions instead of hard block
                        elif price_change_4h_pct > (3 * atr_1h_pct):
                            momentum_penalty = 15.0
                            logger.info(f"{symbol} - Momentum Growth > 3x ATR. Applying penalty -15 to final score.")
                        elif price_change_4h_pct > (2 * atr_1h_pct):
                            momentum_penalty = 10.0
                            logger.info(f"{symbol} - Momentum Growth > 2x ATR. Applying penalty -10 to final score.")

                    # ─── ADVANCED INSTITUTIONAL FILTER 3: ABSORPTION TRAP (FUNDING + RSI + CVD) ────
                    from services.indicators.market_data import get_funding_rate
                    funding_data = await get_funding_rate(symbol)
                    funding_pct = funding_data.get("rate_pct", 0.0)
                    funding_is_valid = funding_data.get("is_valid", False)
                    if funding_is_valid:
                        if funding_pct > 0.04 and rsi_now > 65 and cvd_score_val < 0 and trade_direction == "LONG":
                            logger.info(f"{symbol} - [BLOCKED] Absorption Trap! Retail FOMO (Funding: +{funding_pct:.3f}%, RSI: {rsi_now:.1f}) met with MM Limit Selling (CVD < 0). Squeeze imminent. Skipping.")
                            await insert_filter_block_record(symbol, trade_strategy or "UNKNOWN", "Absorption Trap", 0.0)
                            # Create shadow trade
                            proxy_sl = current_price - (1.5 * atr_1h) if trade_direction == "LONG" else current_price + (1.5 * atr_1h)
                            proxy_tp = current_price + (3.0 * atr_1h) if trade_direction == "LONG" else current_price - (3.0 * atr_1h)
                            block_reason = "Absorption Trap"
                            # We don't continue here to allow V7 calculation and logging
                    else:
                        logger.warning(f"{symbol} - Absorption Trap filter skipped due to funding rate data source failure.")

                    # ─── V11 DATA HEALTH & UNIVERSE TIERS ──────────────────────────────────────
                    from services.engine.data_health import check_data_health, DataHealthLevel
                    from services.engine.universe import DynamicUniverse
                    
                    # Ensure dynamic universe exists globally or instantiate
                    if not hasattr(self, "dynamic_universe"):
                        from database.timescaledb import get_pool
                        self.dynamic_universe = DynamicUniverse(None)
                    
                    ws_data = global_state.live_prices.get(symbol, {})
                    if not ws_data:
                        from shared.symbols import normalize_symbol
                        ws_data = global_state.live_prices.get(normalize_symbol(symbol), {})
                        
                    last_ws_ts = ws_data.get("timestamp", 0)
                    now_ts = datetime.utcnow().timestamp()
                    last_update_age_sec = now_ts - last_ws_ts if last_ws_ts else float('inf')
                    
                    ohlcv_row_1h = df_1h.iloc[-1].to_dict() if not df_1h.empty else {}
                    
                    health_result = check_data_health(
                        ohlcv_row=ohlcv_row_1h,
                        spread_pct=spread_pct,
                        volume_24h_usd=daily_volume_usd,
                        last_update_age_sec=last_update_age_sec,
                        asset_tier=1,  # Simplified to 1 for now
                        timeframe_seconds=3600
                    )
                    
                    # Backwards compatibility for health_score
                    if health_result.level == DataHealthLevel.OK:
                        health_score = 100.0
                    elif health_result.level == DataHealthLevel.SOFT_WARN:
                        health_score = 70.0
                    else:
                        health_score = 0.0
                    
                    # Get Universe multiplier
                    uni_multiplier = self.dynamic_universe.get_priority_multiplier(symbol)
                    
                    # Final size multiplier
                    size_multiplier = health_result.position_size_multiplier * uni_multiplier
                    
                    # We do NOT return early. We will decide at the very end.
                    block_reason = None
                    if health_result.level == DataHealthLevel.HARD_BLOCK:
                        block_reason = f"DATA_HEALTH_FAIL: {health_result.detail}"
                    elif uni_multiplier == 0.0:
                        block_reason = "UNIVERSE_BLACKLIST"


                    # ─── ADVANCED INSTITUTIONAL FILTER 4: Z-SCORE GRAVITY ─────────────────────
                    ema_100 = df_1h['close'].rolling(100).mean().iloc[-1] if len(df_1h) >= 100 else df_1h['close'].mean()
                    std_100 = df_1h['close'].rolling(100).std().iloc[-1] if len(df_1h) >= 100 else df_1h['close'].std()
                    z_score = (current_price - ema_100) / std_100 if std_100 > 0 else 0.0

                    if z_score > 3.0 and trade_direction == "LONG":
                        logger.info(f"{symbol} - [BLOCKED] Z-Score Gravity. Price is {z_score:.1f} std devs above mean. Mean reversion inevitable. Skipping.")
                        block_reason = "Z-Score Gravity"
                        # We don't continue here to allow V7 calculation and logging

                    # ─── SMC + INDICATORS ─────────────────────────────────────────────────────
                    smc_analysis = self.smc_core.analyze(df_1h, symbol=symbol, lookback=10)
                    indicators   = run_all_indicators(df_1h, symbol=symbol)
                    ind_score    = indicators.get("composite_score", 0)

                    # For SHORT, invert ind_score
                    if trade_direction == "SHORT":
                        ind_score = -ind_score

                    ctx_score  = market_ctx.get("total_context_score", 0)

                    if trade_direction == "SHORT":
                        ctx_score = -ctx_score  # invert context for shorts

                    # ─── CONFLUENCE SCORING ────────────────────────────────────────────────────
                    dir_enum  = Direction.LONG if trade_direction == "LONG" else Direction.SHORT

                    confluence = await self.confluence_engine.calculate_score(
                        symbol=symbol,
                        direction=dir_enum,
                        current_price=current_price,
                        df_1h=df_1h,
                        rsi_series=rsi_series,
                        smc=smc_analysis,
                        mtf_score=mtf_score_val,
                        ofi=ofi_real,
                        regime=current_regime,
                        macro_bias=self.macro_state.macro_bias.value,
                        rotation_signal=self.rotation_state
                    )

                    # ─── ULTRA SCORE ───────────────────────────────────────────────────────────
                    ind_bonus = max(-2.0, min(2.0, ind_score * 0.33))
                    ctx_bonus = max(-2.0, min(2.0, ctx_score * 0.25))

                    # Context bonus nerf for SIDEWAYS/BEAR trend-following only
                    fg_val = market_ctx['fear_greed']['value']
                    if trade_strategy == "TREND" and regime_val in ["SIDEWAYS", "BEAR"] and fg_val < 40:
                        df_5m_check    = tf_data.get('5m', pd.DataFrame())
                        prices_last_10m = df_5m_check['close'].tail(2).tolist() if not df_5m_check.empty else []
                        is_reversal     = self.liquidation_detector.is_post_cascade_reversal(symbol, current_price, prices_last_10m)
                        if not is_reversal:
                            ctx_bonus = min(ctx_bonus, 0.3)

                    cvd_bonus  = max(-1.0, min(1.0, cvd_score_val * 0.5))
                    if trade_direction == "SHORT":
                        cvd_bonus = -cvd_bonus  # negative CVD is GOOD for shorts

                    # Mean Reversion gets a bonus for deep oversold
                    mr_bonus = 0.5 if trade_strategy == "MEAN_REVERSION" and rsi_now < 30 else 0.0

                    # ─── MTF MULTIPLIER ────────────────────────────────────────────────────────
                    mtf_val = mtf_score_val if trade_direction == "LONG" else -mtf_score_val
                    if mtf_val >= 6.0:   mtf_mult = 1.20   # сильный тренд = бонус
                    elif mtf_val >= 3.0: mtf_mult = 1.10   # умеренный тренд
                    elif mtf_val >= 0.0: mtf_mult = 1.00   # нейтрально
                    elif mtf_val >= -2.0: mtf_mult = 0.50  # слабо против = сильный штраф
                    else:                 mtf_mult = 0.25  # явно против = жесткий блок

                    base_score = confluence.raw_score + ind_bonus + ctx_bonus + cvd_bonus + mr_bonus
                    ultra_score = max(0, min(10.0, base_score * mtf_mult))
                    
                    # ─── V7 ADAPTIVE SCORING (0-100) ───────────────────────────────────────────
                    v7_score = ultra_score * 10.0
                        
                    # ─── V9 QUANT INDICES (MULTICOLLINEARITY FIX) ─────────────────────────
                    # 1. OVEREXTENSION INDEX
                    overext_points = 0
                    
                    rsi_max = 80 if regime_val == "BULL" else 73
                    if rsi_now > rsi_max: overext_points += 2
                    if z_score > 2.0: overext_points += 2
                    if in_premium_zone: overext_points += 1
                    
                    fvg_count = len(smc_analysis.imbalance_zones)
                    if fvg_count > 12: overext_points += 3
                    elif fvg_count > 10: overext_points += 2
                    elif fvg_count > 8: overext_points += 1
                    
                    base_penalties = {
                        0: 0, 1: 5, 2: 8, 3: 10, 4: 12, 5: 15, 6: 18, 7: 20, 8: 22
                    }
                    overext_base = base_penalties.get(min(overext_points, 8), 22)
                    
                    if breadth_pct < 20.0:
                        overext_penalty = overext_base * 0.4
                    elif breadth_pct < 40.0:
                        overext_penalty = overext_base * 0.65
                    else:
                        overext_penalty = float(overext_base)
                    
                    if overext_penalty > 0:
                        logger.info(f"{symbol} - Overextension Index: {overext_points} pts. Base: {overext_base}, Final: -{overext_penalty:.1f}")

                    # 2. STRUCTURAL CHOP INDEX
                    chop_points = 0
                    
                    if cvd_result.get("divergence"): chop_points += 2
                    if cvd_signal == "BEARISH" and cvd_score_val <= -2: chop_points += 1
                    
                    sweep_count = len(smc_analysis.liquidity_sweeps)
                    if sweep_count > 65: chop_points += 3
                    elif sweep_count > 50: chop_points += 2
                    elif sweep_count > 40: chop_points += 1
                    
                    df_15m_check = tf_data.get('15m', pd.DataFrame())
                    if not df_15m_check.empty and len(df_15m_check) >= 3:
                        last3 = df_15m_check.iloc[-4:-1]
                        last1 = df_15m_check.iloc[-2]
                        if trade_strategy in ["MEAN_REVERSION", "CAPITULATION"]:
                            green_count = sum(1 for _, c in last3.iterrows() if c['close'] > c['open'])
                            if green_count == 0: chop_points += 1
                        elif regime_val == "SIDEWAYS":
                            green_count = sum(1 for _, c in last3.iterrows() if c['close'] > c['open'])
                            if green_count < 2: chop_points += 1
                        else:
                            if last1['close'] < last1['open']: chop_points += 1
                            
                    chop_penalty = 0
                    if chop_points >= 5: chop_penalty = 25
                    elif chop_points >= 3: chop_penalty = 15
                    elif chop_points == 2: chop_penalty = 10
                    
                    if chop_penalty > 0:
                        logger.info(f"{symbol} - Structural Chop Index: {chop_points} pts. Base penalty: -{chop_penalty}")

                    # 3. PENALTY AGGREGATION WITH CAP (-35 MAX)
                    total_penalty = momentum_penalty + overext_penalty + chop_penalty
                    MAX_TOTAL_PENALTY = 35.0
                    total_penalty = min(total_penalty, MAX_TOTAL_PENALTY)
                    
                    if total_penalty > 0:
                        v7_score -= total_penalty
                        logger.info(f"{symbol} - Total Structural/Momentum Penalty applied: -{total_penalty:.1f} (Capped at 35)")

                    # ─── MTF HARD CAP ──────────────────────────────────────────────────────────
                    if mtf_val < 0:
                        v7_score = min(v7_score, 50.0)  # Максимум 50/100 против тренда

                    # 3. INDEPENDENT MACRO PENALTY: SECTOR LEADER & COMPOSITE
                    btc_rsi = 50.0
                    macro_rsi = 50.0
                    
                    if 'BTC' not in symbol:
                        try:
                            sector = self.config.trading.token_sectors.get(symbol, "ALT")
                            leader_sym = self.config.trading.sector_leaders.get(sector, "BTC/USDT")
                            
                            btc_1h = await self.fetch_market_data('BTC/USDT', '1h', 50)
                            eth_1h = await self.fetch_market_data('ETH/USDT', '1h', 50)
                            
                            def calc_rsi(df):
                                if df.empty: return 50.0
                                delta = df['close'].diff()
                                gain = delta.clip(lower=0).rolling(14).mean()
                                loss = (-delta.clip(upper=0)).rolling(14).mean()
                                return (100 - (100 / (1 + gain / loss.replace(0, 1e-9)))).iloc[-1]
                                
                            btc_rsi = calc_rsi(btc_1h)
                            eth_rsi = calc_rsi(eth_1h)
                            
                            leader_df = btc_1h
                            if sector in ["L2", "DEFI"]:
                                leader_rsi = eth_rsi
                                leader_df = eth_1h
                            elif sector == "ALT":
                                leader_rsi = (btc_rsi * 0.6) + (eth_rsi * 0.4)
                            else:
                                leader_df = await self.fetch_market_data(leader_sym, '1h', 50)
                                leader_rsi = calc_rsi(leader_df)
                                
                            macro_rsi = leader_rsi

                            # Penalty based on leader/macro RSI
                            if trade_direction == "LONG" and macro_rsi < 42: v7_score -= 15
                            if trade_direction == "SHORT" and macro_rsi > 58: v7_score -= 15
                            
                            # Dominance Flow Bonus
                            v7_score += self.dominance_flow_bonus
                            
                            # ─── ADVANCED INSTITUTIONAL FILTER 5: INTRADAY RELATIVE STRENGTH (ALPHA) ──
                            if not leader_df.empty and len(leader_df) >= 5:
                                leader_return_4h = (leader_df['close'].iloc[-1] - leader_df['close'].iloc[-5]) / leader_df['close'].iloc[-5] * 100
                                sym_return_4h = price_change_4h_pct
                                
                                if leader_return_4h < -1.0 and sym_return_4h > 1.0 and trade_direction == "LONG" and cvd_score_val > 0:
                                    last_vol = df_15m_check['volume'].iloc[-2] if not df_15m_check.empty else 0
                                    avg_vol = df_15m_check['volume'].iloc[-12:-2].mean() if not df_15m_check.empty else 1
                                    if avg_vol > 0 and (last_vol / avg_vol) > 0.8:
                                        v7_score += 12.0
                                        logger.info(f"🌟 {symbol} INTRADAY ALPHA BONUS! Leader {leader_sym} dropping ({leader_return_4h:.2f}%), but {symbol} rising ({sym_return_4h:.2f}%). CVD is Bullish. Applying +12 points.")
                        except Exception as e:
                            logger.error(f"Error computing sector leader for {symbol}: {e}")
                    else:
                        btc_rsi = rsi_now
                        macro_rsi = rsi_now
                        v7_score += self.dominance_flow_bonus
                    
                    # ─── A+ SETUP BONUS (NO LONGER AN OVERRIDE) ────────────────────────────────
                    if trade_direction == "LONG" and rsi_now < 28 and cvd_score_val >= 0 and ofi_real.ofi_score > 0:
                        last_vol = df_15m_check['volume'].iloc[-2] if not df_15m_check.empty else 0
                        avg_vol = df_15m_check['volume'].iloc[-12:-2].mean() if not df_15m_check.empty else 1
                        if avg_vol > 0 and (last_vol / avg_vol) > 1.5:
                            v7_score += 35.0  # Massive bonus, but must still pass the gate
                            logger.info(f"🌟 {symbol} A+ SETUP BONUS! (RSI={rsi_now:.1f}, CVD+, OFI+, VOL+). Applying +35 points.")
                            
                    # ─── V11 FINAL V7 CALIBRATION & GATE ─────────────────────────────────────────
                    # Apply MTF bonus
                    v7_score += mtf_v7_bonus
                    
                    # Apply Data Health Penalty (Bug 10)
                    if v7_data_penalty > 0:
                        v7_score -= v7_data_penalty
                        logger.info(f"{symbol} - Data Health Penalty: -{v7_data_penalty:.1f} pts. Score down to {v7_score:.1f}")
                    
                    # Ensure v7_score is within 0-100 limits before gate check
                    v7_score = max(0.0, min(100.0, v7_score))
                    
                    from services.engine.dynamic_gate import compute_dynamic_gate
                    if not hasattr(self, 'v7_history_buffer'):
                        self.v7_history_buffer = []
                        
                    self.v7_history_buffer.append(v7_score)
                    if len(self.v7_history_buffer) > 500:
                        self.v7_history_buffer.pop(0)
                        
                    base_gate_value = dynamic_min_score # From Strategy Router
                    dynamic_min_score = compute_dynamic_gate(breadth_pct, self.v7_history_buffer, base_gate=base_gate_value)
                    
                    logger.info(f"{symbol} - Final V7: {v7_score:.1f}/100 | Dynamic Gate: {dynamic_min_score:.1f}")
                        
                    if v7_score < dynamic_min_score:
                        if block_reason is None:
                            block_reason = "V7_GATE_FAIL"
                            logger.info(f"{symbol} - [BLOCKED] V7 Score < Dynamic Gate. Insufficient edge.")
                            
                    # ─── DECISION & TELEMETRY ──────────────────────────────────────────────────
                    
                    is_approved = (block_reason is None)
                    status_str = "APPROVED" if is_approved else "REJECTED_BY_FILTER"
                    
                    # Bug 3: Symbol-Specific SL Calibration
                    sl_multiplier = 1.5
                    if "ETH" in symbol:
                        sl_multiplier = 2.0
                    elif "BTC" in symbol:
                        sl_multiplier = 1.5
                    elif "SOL" in symbol:
                        sl_multiplier = 2.2
                        
                    proxy_sl_dist = sl_multiplier * atr_1h
                    proxy_sl = current_price - proxy_sl_dist if trade_direction == "LONG" else current_price + proxy_sl_dist
                    
                    # Bug 5: Take Profit Ladder (R:R >= 1.5)
                    proxy_tp_rr = 1.5
                    proxy_tp_dist = proxy_sl_dist * proxy_tp_rr
                    proxy_tp = current_price + proxy_tp_dist if trade_direction == "LONG" else current_price - proxy_tp_dist
                    
                    proxy_tp2_rr = 2.5
                    proxy_tp2_dist = proxy_sl_dist * proxy_tp2_rr
                    proxy_tp2 = current_price + proxy_tp2_dist if trade_direction == "LONG" else current_price - proxy_tp2_dist
                    
                    proxy_tp3_rr = 4.0
                    proxy_tp3_dist = proxy_sl_dist * proxy_tp3_rr
                    proxy_tp3 = current_price + proxy_tp3_dist if trade_direction == "LONG" else current_price - proxy_tp3_dist
                    
                    # Create shadow trade for everything to collect stats
                    if is_approved:
                        await create_shadow_trade(
                            symbol=symbol,
                            direction=trade_direction,
                            strategy=trade_strategy or "TREND",
                            entry_price=current_price,
                            stop_loss=proxy_sl,
                            take_profit_1=proxy_tp,
                            take_profit_2=proxy_tp2,
                            take_profit_3=proxy_tp3,
                            v7_score=v7_score,
                            regime=regime_val,
                            breadth=breadth_pct,
                            cvd_score=cvd_score_val,
                            mtf_score=mtf_score_val
                        )
                        self.position_guard.on_trade_opened(symbol)
                    else:
                        await create_shadow_trade_blocked(
                            symbol=symbol,
                            direction=trade_direction,
                            strategy=trade_strategy or "TREND",
                            entry_price=current_price,
                            stop_loss=proxy_sl,
                            take_profit_1=proxy_tp,
                            take_profit_2=proxy_tp2,
                            take_profit_3=proxy_tp3,
                            primary_block_reason=block_reason or "None",
                            all_block_reasons=[f"V7={v7_score:.1f}", f"Gate={dynamic_min_score:.1f}"],
                            v7_score=v7_score,
                            regime=regime_val,
                            breadth=breadth_pct,
                            cvd_score=cvd_score_val,
                            mtf_score=mtf_score_val
                        )
                        continue
                        
                    # --- EXECUTION ---
                    logger.info(f"🚀 {symbol} PASSED V11 GATE! V7={v7_score:.1f} >= {dynamic_min_score:.1f}")

                    ultra_score = v7_score

                    strategy_label = f"[{trade_strategy}]" if trade_strategy != "TREND" else ""
                    logger.info(
                        f"{symbol} {strategy_label} | {trade_direction} | V7 Score={ultra_score:.1f}/100 "
                        f"(conf={confluence.raw_score:.1f} ind={ind_bonus:+.1f} ctx={ctx_bonus:+.1f}) "
                        f"| RSI={rsi_now:.0f} | EMA={indicators['ema_ribbon']['label']} "
                        f"| FG={market_ctx['fear_greed']['value']}"
                    )

                    # Hot coins tracking
                    if not hasattr(global_state, 'hot_coins'):
                        global_state.hot_coins = []
                    if ultra_score >= 5.0:
                        global_state.hot_coins = [c for c in global_state.hot_coins if c['symbol'] != symbol]
                        global_state.hot_coins.append({'symbol': symbol, 'score': ultra_score, 'rsi': f"{rsi_now:.0f}", 'regime': regime_val})
                        global_state.hot_coins.sort(key=lambda x: x['score'], reverse=True)
                        global_state.hot_coins = global_state.hot_coins[:10]


                    # ─── ADVERSARIAL CHECK ─────────────────────────────────────────────────────
                    all_swing_points = smc_analysis.swing_highs + smc_analysis.swing_lows

                    class _MockSignal:
                        def __init__(self, sym, price, direction_enum):
                            self.symbol     = sym
                            self.direction  = direction_enum
                            self.entry_low  = price * (0.999 if trade_direction == "LONG" else 1.001)
                            self.entry_high = price * (1.001 if trade_direction == "LONG" else 0.999)
                            self.stop_loss  = price * (0.97  if trade_direction == "LONG" else 1.03)
                            self.take_profit_1 = price * (1.03 if trade_direction == "LONG" else 0.97)
                            self.take_profit_2 = price * (1.05 if trade_direction == "LONG" else 0.95)
                            self.take_profit_3 = price * (1.08 if trade_direction == "LONG" else 0.92)

                    class _MockOrderBook:
                        def __init__(self):
                            self.bids = []
                            self.asks = []

                    class _MockSpoofing:
                        def __init__(self):
                            self.detected       = False
                            self.episodes_count = 0

                    try:
                        adv_res = self.adversarial_tester.run_adversarial_test(
                            signal=_MockSignal(symbol, current_price, dir_enum),
                            smc=smc_analysis,
                            orderbook=_MockOrderBook(),
                            df_15m=tf_data.get('15m', df_1h),
                            df_5m=tf_data.get('5m', df_1h),
                            spoofing=_MockSpoofing(),
                            news_context={},
                            copy_traders={},
                            social_data={}
                        )
                        if not adv_res.passed:
                            logger.warning(f"{symbol} - Adversarial blocked (score={adv_res.adversarial_score:.1f}).")
                            continue
                    except Exception as adv_err:
                        logger.debug(f"{symbol} - Adversarial check skipped: {adv_err}")

                    # Check active portfolio/sector/meme slot availability (APEX v10.3 / v10.4)
                    latest_active_pb = await get_pullback_items_by_status('WAITING')
                    sec = get_sector(symbol)
                    active_total = len(latest_active_pb)
                    active_sector = sum(1 for item in latest_active_pb if get_sector(item['symbol']) == sec)
                    active_meme = sum(1 for item in latest_active_pb if get_sector(item['symbol']) == "MEME")
                    slots_ok = (active_total < 5) and (active_sector < 2) and (not (sec == "MEME" and active_meme >= 1))
                    
                    # Total exposure slots: open trades + waiting pullbacks (APEX v10.4)
                    open_count = len(open_trades)
                    total_exposure = open_count + active_total
                    if trade_direction == "SHORT":
                        # For shorts, high breadth (>85%) is RISK-OFF
                        if breadth_pct > 90.0:
                            max_exposure = 0
                            logger.warning(f"HARD RISK-OFF (SHORT): Breadth {breadth_pct:.1f}% > 90%. ALL shorts BLOCKED.")
                        elif breadth_pct > 85.0:
                            if symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"] and ultra_score >= 80:
                                max_exposure = 1
                                logger.info(f"RISK-OFF EXCEPTION (SHORT): Breadth {breadth_pct:.1f}% (85-90%) but {symbol} is Major with score >= 80.")
                            else:
                                max_exposure = 0
                                logger.warning(f"HARD RISK-OFF (SHORT): Breadth {breadth_pct:.1f}% (85-90%). {symbol} blocked.")
                        elif breadth_pct > 60.0:
                            max_exposure = 3
                        elif regime_val == "SIDEWAYS" or breadth_pct >= 30.0:
                            max_exposure = 5
                        else:
                            max_exposure = 8
                    else:
                        if breadth_pct < 10.0:
                            max_exposure = 0
                            logger.warning(f"HARD RISK-OFF: Breadth {breadth_pct:.1f}% < 10%. ALL new limits BLOCKED for {symbol}.")
                        elif breadth_pct < 15.0:
                            if symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"] and ultra_score >= 80:
                                max_exposure = 1
                                logger.info(f"RISK-OFF EXCEPTION: Breadth {breadth_pct:.1f}% (10-15%) but {symbol} is Major with score {ultra_score:.1f} >= 80. Allowing limit.")
                            else:
                                max_exposure = 0
                                logger.warning(f"HARD RISK-OFF: Breadth {breadth_pct:.1f}% (10-15%). {symbol} blocked (Score < 80 or not major).")
                        elif breadth_pct < 40.0:
                            max_exposure = 3
                        elif regime_val == "SIDEWAYS" or breadth_pct <= 70.0:
                            max_exposure = 5
                        else:
                            max_exposure = 8
                        
                    exposure_slots_ok = total_exposure < max_exposure
                    slots_ok = slots_ok and exposure_slots_ok
                    
                    ema20_1h = df_1h['close'].ewm(span=20, adjust=False).mean().iloc[-1]
                    ema50_1h = df_1h['close'].ewm(span=50, adjust=False).mean().iloc[-1]

                    # ─── RISK ENGINE — SL/TP ──────────────────────────────────────────────────
                    sltp = self.risk_engine.calculate_sl_tp(
                        entry=current_price,
                        direction=trade_direction,
                        atr=atr_1h,
                        swing_points=all_swing_points,
                        imbalance_zones=smc_analysis.imbalance_zones,
                        volume_nodes=smc_analysis.volume_nodes,
                        key_levels=[],
                        regime=regime_val,
                        v7_score=v7_score,
                        mtf_score=mtf_val,
                        z_score=z_score,
                        rsi=rsi_now,
                        market_breadth=breadth_pct,
                        symbol=symbol,
                        ema20=float(ema20_1h),
                        ema50=float(ema50_1h),
                        pullback_slots_available=slots_ok
                    )

                    if sltp is None:
                        logger.info(f"{symbol} - Setup rejected by Risk Engine: did not meet min R:R ratio (> 1.5) or structural SL exceeded 8.0%.")
                        continue

                    if getattr(sltp, 'is_pullback', False):
                        logger.info(f"{symbol} - Pullback Watchlist item created ({sltp.pullback_status}). Sending Telegram alert...")
                        try:
                            token = self.config.alerts.telegram_bot_token.get_secret_value()
                            chat_id = self.config.alerts.telegram_chat_id
                            if token and chat_id:
                                from aiogram import Bot
                                bot = Bot(token=token)
                                
                                sl_pct = sltp.sl_buffer_pct
                                
                                if sltp.pullback_status == "WAITING_STRUCTURE":
                                    msg = (
                                        f"⏳ <b>WAITING STRUCTURE | {symbol}</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                        f"<b>Strategy:</b> PULLBACK / LIMIT (Observing)\n"
                                        f"<b>Ultra Score:</b> {v7_score:.1f}/100\n"
                                        f"🛑 <b>Stop Loss Target:</b> ${sltp.stop_loss:.4f}\n\n"
                                        f"<i>Структура идеальна, но лимитные слоты заняты либо нет глубокой зоны. "
                                        f"Система перевела монету в режим ожидания (Waiting Structure).</i>"
                                    )
                                else:
                                    msg = (
                                        f"📥 <b>LIMIT GRID CREATED | {symbol}</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                        f"<b>Strategy:</b> PULLBACK / LIMIT\n"
                                        f"<b>Ultra Score:</b> {v7_score:.1f}/100\n\n"
                                        f"📥 <b>Limit Entry 1:</b> ${sltp.pullback_limit_1:.4f} (40%)\n"
                                    )
                                    if sltp.pullback_limit_2 > 0:
                                        msg += f"📥 <b>Limit Entry 2:</b> ${sltp.pullback_limit_2:.4f} (60%)\n"
                                        
                                    msg += (
                                        f"🛑 <b>Stop Loss:</b> ${sltp.stop_loss:.4f} <i>(-{sl_pct:.2f}%)</i>\n"
                                        f"🏁 <b>TP Target 1:</b> ${sltp.pullback_tp_1:.4f}\n"
                                        f"🏁 <b>TP Target 2:</b> ${sltp.pullback_tp_2:.4f}\n"
                                        f"🏁 <b>TP Target 3:</b> ${sltp.pullback_tp_3:.4f}\n\n"
                                        f"<i>Сформирована лимитная сетка на основе Market Breadth. Ожидаем заполнения.</i>"
                                    )
                                    
                                await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                                await bot.session.close()
                        except Exception as tg_err:
                            logger.error(f"Failed to send pullback TG alert: {tg_err}")
                            
                        continue

                    # ATR Stop Cap
                    sl_pct_check = abs(current_price - sltp.stop_loss) / current_price
                    if sl_pct_check > 0.030:
                        df_15m_atr = tf_data.get('15m', pd.DataFrame())
                        if not df_15m_atr.empty:
                            atr_15m_val = (df_15m_atr['high'] - df_15m_atr['low']).rolling(14).mean().iloc[-1]
                            if trade_direction == "LONG":
                                tight_sl = current_price - (2.0 * atr_15m_val)
                                sltp.stop_loss = max(tight_sl, sltp.stop_loss)
                            else:
                                tight_sl = current_price + (2.0 * atr_15m_val)
                                sltp.stop_loss = min(tight_sl, sltp.stop_loss)
                            logger.info(f"{symbol} - ATR Stop Cap: structural SL {sl_pct_check:.1%} too wide → tightened.")

                    # ─── SQUEEZE ENGINE ────────────────────────────────────────────────────────
                    funding_rate_val = market_ctx["funding"]["rate_pct"]
                    funding_is_valid = market_ctx["funding"].get("is_valid", True)
                    oi_change_val    = market_ctx["open_interest"]["change_pct"]
                    oi_is_valid      = market_ctx["open_interest"].get("is_valid", True)
                    is_squeeze       = False

                    if trade_direction == "LONG" and funding_is_valid and funding_rate_val < -0.05 and oi_is_valid and oi_change_val > 2.0:
                        logger.info(f"🚨 SHORT SQUEEZE on {symbol}! Boosting TP targets.")
                        is_squeeze             = True
                        sltp.take_profit_1     = sltp.take_profit_3 * 0.9
                        sltp.take_profit_2     = sltp.take_profit_3 * 0.95
                        sltp.take_profit_3     = current_price * 1.20

                    if trade_strategy == "CAPITULATION":
                        logger.info(f"{symbol} - CAPITULATION trade: enforcing tight TP (max +5%).")
                        sltp.take_profit_1 = current_price * 1.02
                        sltp.take_profit_2 = current_price * 1.03
                        sltp.take_profit_3 = current_price * 1.05

                    # ─── KELLY SIZING ──────────────────────────────────────────────────────────
                    deposit  = self.config.trading.initial_deposit_usd
                    from shared.models import VolatilityRegime
                    vol_enum = VolatilityRegime.NORMAL
                    if regime_val == "BULL":              vol_enum = VolatilityRegime.LOW
                    elif regime_val == "BEAR":            vol_enum = VolatilityRegime.HIGH
                    elif regime_val == "CRISIS":          vol_enum = VolatilityRegime.CRISIS

                    kelly_result = self.risk_engine.calculate_position_size_kelly(
                        deposit=deposit,
                        win_rate_calibrated=0.55,
                        avg_win_pct=2.0,
                        avg_loss_pct=1.0,
                        volatility_regime=vol_enum,
                        current_drawdown_pct=0.0
                    )
                    risk_pct = kelly_result.final_size_pct
                    
                    if trade_strategy == "CAPITULATION":
                        logger.info(f"{symbol} - Capitulation trade: halving Kelly size.")
                        risk_pct *= 0.5
                    elif ultra_score < 85.0:
                        logger.info(f"{symbol} - V7 Score {ultra_score:.1f}/100 is borderline. Halving Kelly size.")
                        risk_pct *= 0.5
                        
                    risk_usd = deposit * risk_pct / 100

                    # Mean Reversion: reduce risk (shorter TP, tighter market)
                    if trade_strategy == "MEAN_REVERSION":
                        risk_usd *= 0.7
                        logger.info(f"{symbol} - Mean Reversion: Risk reduced to 70% Kelly.")

                    if is_squeeze or rsi_now >= 75:
                        risk_usd *= 0.7
                        logger.info(f"{symbol} - RSI/Squeeze Warning: Reduced risk size by 30%.")

                    sl_pct       = abs(current_price - sltp.stop_loss) / current_price if current_price > 0 else 0.03
                    position_usd = (risk_usd / sl_pct) if sl_pct > 0 else risk_usd * 10
                    position_usd = min(position_usd, deposit * 0.20)
                    rr_ratio     = abs(sltp.take_profit_1 - current_price) / abs(current_price - sltp.stop_loss) if abs(current_price - sltp.stop_loss) > 0 else 2.0

                    # ─── CONFIDENCE CALIBRATION (MOCKED FOR V10.5 DATA COLLECTION) ────────────────
                    features_vector = {
                        "ultra_score": ultra_score,
                        "btc_rsi": btc_rsi,
                        "cvd_score": cvd_score_val,
                        "mtf_score": mtf_score_val,
                        "funding_rate": market_ctx["funding"]["rate_pct"]
                    }
                    conf_winrate = isotonic_win_prob * 100.0  # fallback to isotonic
                    conf_samples = 100
                    confidence_data = {
                        "bucket": "HIGH_CONFIDENCE" if conf_winrate > 55 else "MEDIUM_CONFIDENCE",
                        "win_rate": conf_winrate,
                        "sample_size": conf_samples
                    }
                    logger.info(f"🧠 ML Confidence Score: {conf_winrate:.1f}% (Shadow Mode Mock)")

                    # ─── BUILD SIGNAL PACKAGE ─────────────────────────────────────────────────
                    dir_emoji   = "🚀" if trade_direction == "LONG" else "🔻"
                    strat_label = trade_strategy if trade_strategy else "TREND"

                    signal_data = {
                        "symbol":               symbol,
                        "direction":            trade_direction,
                        "strategy":             strat_label,
                        "is_squeeze":           is_squeeze,
                        "entry_low":            current_price * (0.999 if trade_direction == "LONG" else 1.001),
                        "entry_high":           current_price * (1.001 if trade_direction == "LONG" else 0.999),
                        "stop_loss":            sltp.stop_loss,
                        "tp1":                  sltp.take_profit_1,
                        "score":                ultra_score,
                        "regime":               regime_val,
                        "rsi":                  rsi_now,
                        "funding_rate":         market_ctx["funding"]["rate_pct"],
                        "oi_change":            market_ctx["open_interest"]["change_pct"],
                        "fear_greed":           market_ctx["fear_greed"]["value"],
                        "btc_dominance":        market_ctx["btc_dominance"]["value"],
                        "vwap_label":           indicators["vwap"]["label"],
                        "ema_label":            indicators["ema_ribbon"]["label"],
                        "rsi_divergence":       indicators["rsi_divergence"]["label"],
                        "bb_label":             indicators["bollinger"]["label"],
                        "fib_level":            indicators["fibonacci"].get("nearest_fib"),
                        "position_usd":         round(position_usd, 0),
                        "risk_usd":             round(risk_usd, 0),
                        "rr_ratio":             round(rr_ratio, 1),
                        "confidence_bucket":    confidence_data["bucket"],
                        "confidence_win_rate":  confidence_data["win_rate"],
                        "confidence_sample_size": confidence_data["sample_size"]
                    }

                    # ─── FEATURES DICT ────────────────────────────────────────────────────────
                    features_dict = {
                        "regime":               regime_val,
                        "ultra_score":          ultra_score,
                        "fvg_count":            len(smc_analysis.imbalance_zones),
                        "btc_rsi":              btc_rsi if 'btc_rsi' in locals() else 50.0,
                        "funding_rate":         market_ctx["funding"]["rate_pct"] if "funding" in market_ctx else 0.0,
                        "oi_change":            market_ctx["open_interest"]["change_pct"] if "open_interest" in market_ctx else 0.0,
                        "fg_index":             market_ctx["fear_greed"]["value"] if "fear_greed" in market_ctx else 50.0,
                        "mtf_score":            mtf_score_val,
                        "cvd_score":            cvd_score_val,
                        "strategy":             trade_strategy,
                        "direction":            trade_direction,
                        "slippage":             spread_pct / 2.0, 
                        "spread_at_entry":      spread_pct, 
                        "btc_trend_strength":   float(market_ctx.get("btc_dominance", {}).get("value", 55.0)), 
                        "volume_spike_score":   vol_ratio_15m,
                    }

                    # ─── DUAL SIGNAL PATH (MARKET vs LIMIT) ──────────────────────────────────
                    structural_sl_pct = abs(current_price - sltp.stop_loss) / current_price * 100
                    
                    # NOTE: funding.is_valid and oi.is_valid are always False on MEXC/Binance Spot.
                    # Removed them from gate — they were physically blocking ALL market orders.
                    # Funding/OI context is still used as scoring bonus/penalty via confluence engine.
                    is_market_entry = (
                        ultra_score >= 72.0 and
                        structural_sl_pct <= 3.0 and
                        premium_discount < 0.75 and
                        breadth_pct >= 20.0 and
                        health_data["market_allowed"]
                    )

                    if is_market_entry:
                        amount = position_usd / current_price
                        entry_price = current_price
                        sl_order_id = None
                        tp_order_id = None
                        execution_mode = "DEMO"
                        trade_status = "OPEN"
                        
                        if not self.config.trading.live_trading_enabled:
                            logger.info(f"[DEMO MODE] {symbol} Market entry simulated. No real order sent.")
                        else:
                            execution_mode = "LIVE"
                            # 1. Place Aggressive Limit Entry via OrderRouter
                            try:
                                from services.execution.order_router import ExecutionRequest
                                from services.execution.transaction_cost_model import OrderUrgency
                                
                                urg = OrderUrgency.HIGH if trade_strategy == "MEAN_REVERSION" else OrderUrgency.MEDIUM
                                req = ExecutionRequest(
                                    symbol=symbol,
                                    direction=trade_direction,
                                    amount=amount,
                                    current_price=current_price,
                                    urgency=urg,
                                    stop_loss=sltp.stop_loss,
                                    take_profit=sltp.take_profit_1
                                )
                                order = await self.order_router.submit_aggressive_entry(req)
                                entry_price = order.get('average', current_price) or current_price
                                filled_amount = order.get('amount', amount)
                                sl_order_id = "async_router_managed"
                                tp_order_id = "async_router_managed"
                                trade_status = "OPEN_ROUTER_MANAGED"
                                logger.info(f"LIVE Aggressive Limit routed for {symbol} at ~{entry_price}")
                                
                            except Exception as exec_err:
                                logger.error(f"Failed to execute Aggressive Limit Order for {symbol}: {exec_err}")
                                continue
                                
                        # Save to TimescaleDB
                        signal_dict = {
                            "timestamp": datetime.utcnow(),
                            "symbol": symbol,
                            "strategy": trade_strategy,
                            "direction": trade_direction,
                            "status": trade_status,
                            "entry_price": entry_price,
                            "sl_price": sltp.stop_loss,
                            "tp1_price": sltp.take_profit_1,
                            "tp2_price": sltp.take_profit_2,
                            "tp3_price": sltp.take_profit_3,
                            "v7_score_raw": ultra_score,
                            "mtf_score": mtf_score_val,
                            "regime": regime_val,
                            "session": SessionTagger.get_session(datetime.utcnow()),
                            "logic_version": "10.5.0",
                            "is_shadow": False
                        }
                        signal_id = await insert_signal_record(signal_dict)
                        await insert_shadow_trade(
                            signal_id=signal_id,
                            symbol=symbol,
                            session=signal_dict["session"],
                            regime=regime_val,
                            logic_version="10.5.0"
                        )
                        
                        logger.info(f"🟢 SIGNAL {signal_id} SAVED TO TIMESCALEDB (SHADOW MODE)")
                        
                        import structlog
                        struct_logger = structlog.get_logger("telemetry")
                        struct_logger.info(
                            "MARKET_ENTRY",
                            symbol=symbol,
                            avg_fill_price=entry_price,
                            sl_order_id=sl_order_id,
                            tp_order_id=tp_order_id,
                            source="MARKET",
                            mode=execution_mode,
                            status=trade_status
                        )
                        logger.info(f"MARKET Signal executed ({execution_mode}): {symbol} {trade_direction}")
                        signal_data["source"] = "MARKET"

                    else:
                        # ─── LIMIT / PULLBACK PATH ────────────────────────────────────────────────
                        from database.timescaledb import save_pullback_item
                        # Calculate a logical swing low. Assuming sltp.stop_loss is at or below it.
                        await save_pullback_item(
                            symbol=symbol,
                            direction=trade_direction,
                            score=ultra_score,
                            original_entry=current_price,
                            swing_low=sltp.stop_loss, 
                            limit_entries=[{"price": current_price, "size": 1.0}],
                            stop_loss=sltp.stop_loss,
                            take_profit_1=sltp.take_profit_1,
                            take_profit_2=sltp.take_profit_2,
                            take_profit_3=sltp.take_profit_3,
                            position_usd=position_usd,
                            ttl_minutes=120,
                            regime=regime_val,
                            original_breadth=breadth_pct,
                            original_mtf=mtf_score_val,
                            original_cvd=cvd_score_val
                        )
                        logger.info(f"Signal routed to LIMIT Watchlist: {symbol} {trade_direction} [{strat_label}]")
                        signal_data["source"] = "LIMIT"
                        
                    # ─── SEND TO TELEGRAM ─────────────────────────────────────────────────────
                    try:
                        from aiogram import Bot
                        token = self.config.alerts.telegram_bot_token.get_secret_value()
                        chat_id_str = self.config.alerts.telegram_chat_id
                        if token and chat_id_str:
                            bot = Bot(token=token)
                            from services.notifications.telegram_ui import send_signal
                            await send_signal(bot, int(chat_id_str), signal_data)
                            await bot.session.close()
                            global_state.signals_sent_today += 1
                    except Exception as send_err:
                        logger.error(f"Failed to send {signal_data['source']} signal: {send_err}")

                except Exception as e:
                    logger.error(f"Error processing {symbol}: {e}", exc_info=True)

                await asyncio.sleep(2)  # Prevent rate limiting between pairs
                
            logger.info("=== SCAN CYCLE COMPLETE ===")
            # Sleep for 5 minutes (300 seconds)
            await asyncio.sleep(300)

    async def background_missed_signals_tracker(self):
        """Periodically checks missed signals to calculate hypothetical outcome."""
        from database.timescaledb import get_unchecked_missed_signals, update_missed_signal_result
        while self.running:
            try:
                missed = await get_unchecked_missed_signals()
                for signal in missed:
                    symbol = signal['symbol']
                    entry_price = signal['entry_price']
                    direction = signal['direction']
                    
                    try:
                        ticker = await self.exchange.fetch_ticker(symbol)
                        current_price = ticker['last']
                        
                        pnl_pct = (current_price - entry_price) / entry_price * 100
                        if direction == "SHORT":
                            pnl_pct = -pnl_pct
                            
                        # If hypothetical PnL >= 1.0%, we consider it a missed win
                        outcome = "MISSED_WIN" if pnl_pct >= 1.0 else "CORRECT_BLOCK"
                        
                        await update_missed_signal_result(signal['id'], pnl_pct, outcome)
                        logger.info(f"[MISSED SIGNAL TRACKER] {symbol} {direction} (Score: {signal['score']}) -> {outcome} (PnL: {pnl_pct:+.2f}%)")
                    except Exception as e:
                        logger.warning(f"Failed to check missed signal {symbol}: {e}")
            except Exception as e:
                logger.error(f"Error in missed signals tracker: {e}")
                
            await asyncio.sleep(1800)  # Check every 30 minutes

    async def pre_route_gate_check(self, symbol: str, direction: str) -> bool:
        """
        Runs the pre-execution checks for the pullback limit order execution.
        Returns True if passed (safe to buy), False if cancelled (danger).
        """
        try:
            # 1. Fetch 1h dataframe for indicators/context
            df_1h = await self.fetch_market_data(symbol, '1h', limit=50)
            if df_1h.empty or len(df_1h) < 20:
                logger.warning(f"[PRE-ROUTE GATE] {symbol} - Empty/low historical data. Skipping execution.")
                return False

            from services.indicators.market_data import get_market_context
            from services.intelligence.cvd_engine import calculate_cvd
            
            price_change = (df_1h['close'].iloc[-1] - df_1h['close'].iloc[-5]) / df_1h['close'].iloc[-5] * 100
            market_ctx = await get_market_context(symbol, price_change)
            
            # Check BTC returns — only block extreme dumps (> -2.5% in 5m = cascade)
            btc_df = await self.fetch_market_data("BTC/USDT", "1m", limit=6)
            if not btc_df.empty and len(btc_df) >= 6:
                btc_change_5m = (btc_df['close'].iloc[-1] - btc_df['close'].iloc[-6]) / btc_df['close'].iloc[-6] * 100
                if btc_change_5m < -2.5:
                    logger.warning(f"[PRE-ROUTE GATE] {symbol} - [CANCELLED] BTC cascading dump ({btc_change_5m:+.2f}% in 5m).")
                    return False

            # Check CVD — only block extreme institutional selling (score <= -4)
            # NOTE: In BEAR market, CVD is typically -2 to -3 by default. Threshold raised to -4.
            df_5m = await self.fetch_market_data(symbol, '5m', limit=30)
            if not df_5m.empty:
                cvd_res = calculate_cvd(df_5m, lookback=20)
                cvd_score = cvd_res.get("score", 0)
                cvd_signal = cvd_res.get("cvd_signal", "NEUTRAL")
                if cvd_signal == "BEARISH" and cvd_score <= -4:
                    logger.warning(f"[PRE-ROUTE GATE] {symbol} - [CANCELLED] CVD extreme sell (Score={cvd_score}).")
                    return False

            # NOTE: EMA20 check removed. Limit orders are placed BELOW market by design.
            # Price will always be below EMA20 at fill point — checking this was blocking all limits.

            logger.info(f"[PRE-ROUTE GATE] {symbol} - [PASSED] Pre-execution checks OK. Executing pullback entry!")
            return True
        except Exception as e:
            logger.error(f"Error in Pre-Route Gate for {symbol}: {e}")
            return False

    async def background_pullback_tracker(self):
        """Continuously monitors pullback watchlist items for limit grid hits, audits score decays, and handles promotions."""
        from database.timescaledb import (
            get_active_pullback_items, 
            update_pullback_status, 
            expire_old_pullback_items, 
            save_trade,
            get_pullback_items_by_status,
            update_pullback_limit_entries
        )
        from services.indicators.market_data import get_market_context
        from services.intelligence.cvd_engine import calculate_cvd
        import json
        import aiosqlite
        import time
        
        last_score_audit_time = 0.0
        last_promotion_time = 0.0
        
        while self.running:
            try:
                # 1. Expire outdated watchlists
                try:
                    import aiosqlite
                    db_path = "apex_lite.db"
                    async with aiosqlite.connect(db_path) as db:
                        db.row_factory = aiosqlite.Row
                        async with db.execute('''
                            SELECT * FROM pullback_watchlist
                            WHERE (status = 'WAITING' OR status = 'WAITING_STRUCTURE') AND datetime(ttl_expiry) <= datetime('now')
                        ''') as cursor:
                            expiring_items = [dict(row) for row in await cursor.fetchall()]
                            
                    for exp_item in expiring_items:
                        exp_symbol = exp_item['symbol']
                        exp_status = 'EXPIRED' if exp_item['status'] == 'WAITING' else 'EXPIRED_STRUCTURE'
                        struct_logger.info(
                            exp_status,
                            symbol=exp_symbol,
                            original_score=exp_item['score'],
                            current_score=exp_item['score'],
                            original_breadth=exp_item.get('original_breadth', 50.0),
                            current_breadth=self.market_breadth,
                            original_mtf=exp_item.get('original_mtf', 0.0),
                            current_mtf=exp_item.get('original_mtf', 0.0),
                            original_cvd=exp_item.get('original_cvd', 0.0),
                            current_cvd=0.0,
                            reason="TTL expired. Lifespan exceeded."
                        )
                except Exception as exp_err:
                    logger.debug(f"Failed to log watchlist expiration telemetry: {exp_err}")
                
                await expire_old_pullback_items()
                
                current_time = time.time()
                
                # ─── SCORE DEGRADATION AUDIT TASK (Every 5 Minutes) ───────────────────
                if current_time - last_score_audit_time >= 300.0:
                    last_score_audit_time = current_time
                    logger.info("[PULLBACK TRACKER] Starting periodic score audit for active limit orders...")
                    active_items = await get_pullback_items_by_status('WAITING')
                    for item in active_items:
                        symbol = item['symbol']
                        df_1h = await self.fetch_market_data(symbol, '1h', limit=50)
                        if not df_1h.empty and len(df_1h) >= 20:
                            price_change = (df_1h['close'].iloc[-1] - df_1h['close'].iloc[-5]) / df_1h['close'].iloc[-5] * 100 if len(df_1h) >= 5 else 0.0
                            market_ctx = await get_market_context(symbol, price_change)
                            
                            # Compute current RSI
                            delta = df_1h['close'].diff()
                            gain = delta.clip(lower=0).rolling(14).mean()
                            loss = (-delta.clip(upper=0)).rolling(14).mean()
                            rs = gain / loss.replace(0, 1e-9)
                            rsi_val = (100 - (100 / (1 + rs))).iloc[-1]
                            
                            # Fetch multi-timeframe alignment score
                            df_15m = await self.fetch_market_data(symbol, '15m', limit=50)
                            df_5m = await self.fetch_market_data(symbol, '5m', limit=50)
                            tf_data = {'1h': df_1h, '15m': df_15m, '5m': df_5m}
                            
                            df_1d = await self.fetch_market_data(symbol, '1d', limit=50)
                            df_4h = await self.fetch_market_data(symbol, '4h', limit=50)
                            if not df_1d.empty: tf_data['1d'] = df_1d
                            if not df_4h.empty: tf_data['4h'] = df_4h
                            
                            mtf_score = self.mtf_engine.get_alignment_score(symbol, tf_data)
                            mtf_val = mtf_score.score
                            
                            # SMC analysis
                            smc_analysis = self.smc_core.analyze(df_1h, symbol)
                            
                            # Confluence V7 score re-calculation
                            from shared.models import Direction, MarketRegime
                            from services.intelligence.ofi_engine import OFIResult
                            
                            dir_enum = Direction.LONG if item.get('direction', 'LONG') == 'LONG' else Direction.SHORT
                            current_regime = MarketRegime(item.get('regime', 'BULL'))
                            ofi_mock = OFIResult(0.0, 0.0, 0.0)
                            
                            confluence = await self.confluence_engine.calculate_score(
                                symbol=symbol,
                                direction=dir_enum,
                                current_price=df_1h['close'].iloc[-1],
                                df_1h=df_1h,
                                rsi_series=100 - (100 / (1 + rs)),
                                smc=smc_analysis,
                                mtf_score=mtf_score,
                                ofi=ofi_mock,
                                regime=current_regime,
                                macro_bias=self.macro_state.macro_bias.value,
                                rotation_signal=self.rotation_state
                            )
                            
                            # Score decay is now handled by explicit CVD and MTF checks below
                            
                            # Enriched Cancellations Check (APEX v10.4)
                            current_price = df_1h['close'].iloc[-1]
                            stop_loss = item['stop_loss']
                            is_stop_hit = current_price <= stop_loss
                            is_breadth_weak = self.market_breadth < 15.0
                            
                            # Check BTC Dump
                            btc_dump = False
                            btc_change_5m = 0.0
                            try:
                                btc_df = await self.fetch_market_data("BTC/USDT", "1m", limit=6)
                                if not btc_df.empty and len(btc_df) >= 6:
                                    btc_change_5m = (btc_df['close'].iloc[-1] - btc_df['close'].iloc[-6]) / btc_df['close'].iloc[-6] * 100
                                    if btc_change_5m < -1.5:
                                        btc_dump = True
                            except Exception:
                                pass
                                
                            # Check CVD & trend decay
                            cvd_score = 0
                            cvd_signal = "NEUTRAL"
                            if not df_5m.empty:
                                cvd_res = calculate_cvd(df_5m, lookback=20)
                                cvd_score = cvd_res.get("score", 0)
                                cvd_signal = cvd_res.get("cvd_signal", "NEUTRAL")
                            is_trend_decay = cvd_signal == "BEARISH" and cvd_score <= -2 and mtf_val < 4.0
                            
                            cancel_reason = None
                            event_name = None
                            if is_stop_hit:
                                cancel_reason = f"Current price ${current_price:.4f} is at or below Stop Loss ${stop_loss:.4f}."
                                event_name = "CANCELLED"
                            elif is_breadth_weak:
                                cancel_reason = f"Systemic weakness: Market Breadth is {self.market_breadth:.1f}% (below 15% Risk-Off)."
                                event_name = "CANCELLED_BREADTH"
                            elif btc_dump:
                                cancel_reason = f"BTC cascading: BTC returned {btc_change_5m:+.2f}% in last 5m."
                                event_name = "CANCELLED_BTC_DUMP"
                            elif is_trend_decay:
                                cancel_reason = f"Trend breakdown: Bearish CVD Flow (Score={cvd_score}) and MTF score decay (Score={mtf_val:.1f})."
                                event_name = "CANCELLED_CVD_MTF"
                                
                            if cancel_reason:
                                original_score = item.get('score', 0.0)
                                new_score = 0.0
                                logger.warning(f"[PULLBACK TRACKER] {symbol} cancelled due to: {cancel_reason}. Removing active limit grid!")
                                await update_pullback_status(item['id'], event_name or 'CANCELLED')
                                
                                # Log enriched telemetry (APEX v10.4)
                                struct_logger.info(
                                    event_name or "CANCELLED",
                                    symbol=symbol,
                                    original_score=original_score,
                                    current_score=new_score,
                                    original_breadth=item.get('original_breadth', 50.0),
                                    current_breadth=self.market_breadth,
                                    original_mtf=item.get('original_mtf', 0.0),
                                    current_mtf=float(mtf_val),
                                    original_cvd=item.get('original_cvd', 0.0),
                                    current_cvd=float(cvd_score),
                                    reason=cancel_reason
                                )
                                
                                # Send Telegram cancellation notification
                                try:
                                    token = self.config.alerts.telegram_bot_token.get_secret_value()
                                    chat_id = self.config.alerts.telegram_chat_id
                                    if token and chat_id:
                                        from aiogram import Bot
                                        bot = Bot(token=token)
                                        msg = (
                                            f"⚠️ <b>LIMIT CANCELLED | {symbol}</b>\n"
                                            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                            f"📉 <b>Reason:</b> {cancel_reason}\n"
                                            f"📊 <b>Original Score:</b> {original_score:.1f}/100\n"
                                            f"📉 <b>Current Score:</b> {new_score:.1f}/100\n\n"
                                            f"<i>Лимитная сетка отменена для защиты капитала.</i>"
                                        )
                                        await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                                        await bot.session.close()
                                except Exception as tg_err:
                                    logger.error(f"Failed to send score cancellation TG alert: {tg_err}")
                
                # ─── WAITING_STRUCTURE PROMOTION TASK (Every 2 Minutes) ───────────────
                if current_time - last_promotion_time >= 120.0:
                    last_promotion_time = current_time
                    logger.info("[PULLBACK TRACKER] Starting periodic re-evaluation for WAITING_STRUCTURE items...")
                    structure_items = await get_pullback_items_by_status('WAITING_STRUCTURE')
                    if structure_items:
                        latest_active_pb = await get_pullback_items_by_status('WAITING')
                        active_total = len(latest_active_pb)
                        
                        for item in structure_items:
                            symbol = item['symbol']
                            sec = get_sector(symbol)
                            active_sector = sum(1 for active_item in latest_active_pb if get_sector(active_item['symbol']) == sec)
                            active_meme = sum(1 for active_item in latest_active_pb if get_sector(active_item['symbol']) == "MEME")
                            
                            slots_ok = (active_total < 5) and (active_sector < 2) and (not (sec == "MEME" and active_meme >= 1))
                            
                            # Total exposure slots: open trades + waiting pullbacks (APEX v10.4)
                            open_trades = await get_open_trades()
                            open_count = len(open_trades)
                            total_exposure = open_count + active_total
                            breadth_pct = self.market_breadth
                            if breadth_pct < 10.0:
                                max_exposure = 0
                                logger.warning(f"HARD RISK-OFF: Breadth {breadth_pct:.1f}% < 10%. ALL WAITING_STRUCTURE promotions BLOCKED for {symbol}.")
                            elif breadth_pct < 15.0:
                                if symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"] and item['score'] >= 80:
                                    max_exposure = 1
                                    logger.info(f"RISK-OFF EXCEPTION: Breadth {breadth_pct:.1f}% (10-15%) but {symbol} is Major with score {item['score']:.1f} >= 80. Allowing promotion.")
                                else:
                                    max_exposure = 0
                                    logger.warning(f"HARD RISK-OFF: Breadth {breadth_pct:.1f}% (10-15%). Promotion for {symbol} blocked (Score < 80 or not major).")
                            elif breadth_pct < 40.0:
                                max_exposure = 3
                            elif item['regime'] == "SIDEWAYS" or breadth_pct <= 70.0:
                                max_exposure = 5
                            else:
                                max_exposure = 8
                                
                            exposure_slots_ok = total_exposure < max_exposure
                            slots_ok = slots_ok and exposure_slots_ok
                            
                            if not slots_ok:
                                logger.info(f"[PULLBACK TRACKER] {symbol} (WAITING_STRUCTURE) - Active limit slots or total exposure limits full. Skipping promotion.")
                                continue
                                
                            # Fetch 1h data to check structure
                            df_1h = await self.fetch_market_data(symbol, '1h', limit=50)
                            if not df_1h.empty and len(df_1h) >= 20:
                                # Re-calculate EMAs
                                ema20_1h = df_1h['close'].ewm(span=20, adjust=False).mean().iloc[-1]
                                ema50_1h = df_1h['close'].ewm(span=50, adjust=False).mean().iloc[-1]
                                
                                # SMC Analysis
                                smc_analysis = self.smc_core.analyze(df_1h, symbol)
                                
                                limit_candidates = []
                                swing_low = item['swing_low']
                                entry = df_1h['close'].iloc[-1]
                                stop_loss = item['stop_loss']
                                
                                # 1. EMA20 Support
                                if swing_low < ema20_1h < entry:
                                    limit_candidates.append(ema20_1h)
                                # 2. EMA50 Support
                                if swing_low < ema50_1h < entry:
                                    limit_candidates.append(ema50_1h)
                                    
                                # 3. Swing Lows
                                for sp in smc_analysis.swing_lows:
                                    if swing_low < sp.price < entry:
                                        limit_candidates.append(sp.price)
                                        
                                # 4. Bullish FVG midpoints
                                for fvg in smc_analysis.imbalance_zones:
                                    if fvg.type == "BULLISH_FVG":
                                        fvg_mid = (fvg.low + fvg.high) / 2.0
                                        if swing_low < fvg_mid < entry:
                                            limit_candidates.append(fvg_mid)
                                            
                                # 5. HVN/POC Supports
                                for vn in smc_analysis.volume_nodes:
                                    if vn.type in ["HVN", "POC"] and swing_low < vn.price < entry:
                                        limit_candidates.append(vn.price)
                                        
                                # Safe candidates check
                                valid_candidates = list(set([p for p in limit_candidates if swing_low < p < entry]))
                                safe_candidates = []
                                for p in valid_candidates:
                                    sl_dist_pct = (p - stop_loss) / p * 100
                                    if 0.7 <= sl_dist_pct <= 2.5:
                                        safe_candidates.append(p)
                                        
                                if safe_candidates:
                                    best_limit = max(safe_candidates)
                                    deeper_limit = min(safe_candidates)
                                    
                                    # ─── PROXIMITY GATE (promotion check) ──────────────────────
                                    # Even during promotion, don't place limit if zone is too far
                                    regime_str = item.get('regime', 'SIDEWAYS')
                                    max_promo_dist_pct = 5.0 if regime_str == "BULL" else 4.0
                                    promo_dist_pct = (entry - best_limit) / entry * 100
                                    if promo_dist_pct > max_promo_dist_pct:
                                        logger.info(f"[PULLBACK TRACKER] {symbol} WAITING_STRUCTURE promotion blocked by Proximity Gate: zone is {promo_dist_pct:.1f}% away (max {max_promo_dist_pct}%). Keeping as WAITING_STRUCTURE.")
                                        continue
                                    # ───────────────────────────────────────────────────────────
                                    
                                    if abs(best_limit - deeper_limit) / best_limit * 100 < 0.1:
                                        limit_entries = [
                                            {"price": round(best_limit, 8), "size_pct": 100.0, "label": "Structural Support Grid"}
                                        ]
                                    else:
                                        limit_entries = [
                                            {"price": round(best_limit, 8), "size_pct": 40.0, "label": "Shallow Support Grid"},
                                            {"price": round(deeper_limit, 8), "size_pct": 60.0, "label": "Deep Support Grid"}
                                        ]
                                        
                                    new_sl_dist_pct = (best_limit - stop_loss) / best_limit * 100
                                    atr_1h = (df_1h['high'] - df_1h['low']).rolling(14).mean().iloc[-1]
                                    atr_pct = atr_1h / entry * 100
                                    
                                    regime = item['regime']
                                    if regime == "BULL":
                                        atr_target_pct = atr_pct * 2.0
                                        max_tp_pct = 8.0 if item['score'] >= 85 else 6.0
                                    else:
                                        atr_target_pct = atr_pct * 1.3
                                        max_tp_pct = 4.0 if item['score'] >= 85 else 3.5
                                        
                                    raw_tp_est = min(atr_target_pct, max_tp_pct)
                                    new_tp_pct = max(new_sl_dist_pct * 1.5, raw_tp_est)
                                    
                                    tp1_pb = best_limit * (1 + new_tp_pct / 100)
                                    tp2_pb = best_limit * (1 + new_tp_pct * 1.2 / 100)
                                    tp3_pb = best_limit * (1 + new_tp_pct * 1.5 / 100)
                                    
                                    # Perform promotion
                                    await update_pullback_limit_entries(
                                        item_id=item['id'],
                                        limit_entries=limit_entries,
                                        take_profit_1=tp1_pb,
                                        take_profit_2=tp2_pb,
                                        take_profit_3=tp3_pb,
                                        new_status='WAITING'
                                    )
                                    logger.info(f"[PULLBACK TRACKER] {symbol} promoted successfully from WAITING_STRUCTURE to WAITING! Limit grid placed.")
                                    
                                    # Log promotion telemetry (APEX v10.4)
                                    struct_logger.info(
                                        "PROMOTED_TO_WAITING",
                                        symbol=symbol,
                                        original_score=item['score'],
                                        current_score=item['score'],
                                        original_breadth=item.get('original_breadth', 50.0),
                                        current_breadth=self.market_breadth,
                                        original_mtf=item.get('original_mtf', 0.0),
                                        current_mtf=float(mtf_val),
                                        original_cvd=item.get('original_cvd', 0.0),
                                        current_cvd=0.0,
                                        reason="SMC structural zone detected, promoting to active waiting limits."
                                    )
                                    
                                    # Send Telegram notification
                                    try:
                                        token = self.config.alerts.telegram_bot_token.get_secret_value()
                                        chat_id = self.config.alerts.telegram_chat_id
                                        if token and chat_id:
                                            from aiogram import Bot
                                            bot = Bot(token=token)
                                            msg = (
                                                f"🚀 <b>LIMIT PLACED | {symbol}</b>\n"
                                                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                                f"📈 <b>Status:</b> Promoted from WAITING_STRUCTURE to active WAITING.\n"
                                                f"📥 <b>Limit Entry 1:</b> ${best_limit:.4f} (40%)\n"
                                                f"📥 <b>Limit Entry 2:</b> ${deeper_limit:.4f} (60%)\n"
                                                f"🛑 <b>Stop Loss:</b> ${stop_loss:.4f} <i>(-{new_sl_dist_pct:.2f}%)</i>\n"
                                                f"🏁 <b>TP Target (TP3):</b> ${tp3_pb:.4f} <i>(+{new_tp_pct * 1.5:.2f}%)</i>\n\n"
                                                f"<i>Структурная зона найдена. Лимитные ордера выставлены в стакан.</i>"
                                            )
                                            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                                            await bot.session.close()
                                    except Exception as tg_err:
                                        logger.error(f"Failed to send promotion TG alert: {tg_err}")
                                        
                                    # Update active limits state for next items
                                    latest_active_pb = await get_pullback_items_by_status('WAITING')
                                    active_total = len(latest_active_pb)
                
                # 3. Get active watchlists
                active_items = await get_active_pullback_items()
                if active_items:
                    for item in active_items:
                        symbol = item['symbol']
                        direction = item['direction']
                        
                        # Fetch MEXC price using fetch_ohlcv (1m)
                        ohlcv = await self.exchange.fetch_ohlcv(symbol, '1m', limit=2)
                        if not ohlcv:
                            continue
                        recent_high = max([c[2] for c in ohlcv])
                        recent_low = min([c[3] for c in ohlcv])
                        current_price = ohlcv[-1][4]
                        
                        # Parse limit order brackets from JSON
                        limit_entries = json.loads(item['limit_entries'])
                        
                        # We track if any bracket has been touched
                        filled_brackets_indices = []
                        for idx, bracket in enumerate(limit_entries):
                            if bracket.get("filled", False):
                                continue
                            bracket_price = float(bracket['price'])
                            
                            # Check if price hit limit during the last 2 minutes
                            hit_long = (direction == "LONG" and recent_low <= bracket_price)
                            hit_short = (direction == "SHORT" and recent_high >= bracket_price)
                            
                            if hit_long or hit_short:
                                # Candidate for fill! Run the Pre-Route Trigger check
                                logger.info(f"[PULLBACK TRACKER] {symbol} touched pullback limit price {bracket_price}. Running Pre-Route Gate...")
                                passed = await self.pre_route_gate_check(symbol, direction)
                                
                                if passed:
                                    # Execute paper entry!
                                    await save_trade(
                                        signal_id=f"pullback_{item['id']}_{idx}",
                                        symbol=symbol,
                                        direction=direction,
                                        entry_price=bracket_price,
                                        stop_loss=item['stop_loss'],
                                        take_profit_1=item['take_profit_1'],
                                        take_profit_2=item['take_profit_2'],
                                        take_profit_3=item['take_profit_3'],
                                        position_usd=item['position_usd'] * (bracket['size_pct'] / 100.0),
                                        strategy="PULLBACK"
                                    )
                                    bracket["filled"] = True
                                    filled_brackets_indices.append(idx)
                                    
                                    # Send Telegram notification
                                    try:
                                        token = self.config.alerts.telegram_bot_token.get_secret_value()
                                        chat_id = self.config.alerts.telegram_chat_id
                                        if token and chat_id:
                                            from aiogram import Bot
                                            bot = Bot(token=token)
                                            msg = (
                                                f"⚡ <b>LIMIT FILLED | {symbol}</b>\n"
                                                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                                f"📥 <b>Limit Entry Price:</b> ${bracket_price:.4f} ({bracket['label']})\n"
                                                f"🛑 <b>Stop Loss:</b> ${item['stop_loss']:.4f} <i>(-{abs(bracket_price - item['stop_loss']) / bracket_price * 100:.2f}%)</i>\n"
                                                f"🏁 <b>TP Target (TP3):</b> ${item['take_profit_3']:.4f}\n"
                                                f"⚖️ <b>Position Size:</b> ${item['position_usd'] * (bracket['size_pct'] / 100.0):.0f}\n\n"
                                                f"<i>Защитные фильтры пройдены. Сделка ведется в рынке.</i>"
                                            )
                                            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                                            await bot.session.close()
                                    except Exception as tg_err:
                                        logger.error(f"Failed to send pullback TG notification: {tg_err}")
                                else:
                                    # Pre-route gate failed, cancel the whole watchlist item to protect capital!
                                    await update_pullback_status(item['id'], 'CANCELLED')
                                    logger.warning(f"[PULLBACK TRACKER] {symbol} - Setup cancelled due to Pre-Route Gate failure.")
                                    break
                                    
                        if filled_brackets_indices:
                            # Update limit_entries in db
                            all_filled = all([b.get("filled", False) for b in limit_entries])
                            
                            # Log filled/partial fill telemetry (APEX v10.4)
                            fill_event = "FILLED" if all_filled else "PARTIAL_FILLED"
                            struct_logger.info(
                                fill_event,
                                symbol=symbol,
                                original_score=item['score'],
                                current_score=item['score'],
                                original_breadth=item.get('original_breadth', 50.0),
                                current_breadth=self.market_breadth,
                                original_mtf=item.get('original_mtf', 0.0),
                                current_mtf=item.get('original_mtf', 0.0),
                                original_cvd=item.get('original_cvd', 0.0),
                                current_cvd=0.0,
                                reason=f"Limit order hit at bracket indices {filled_brackets_indices}."
                            )
                            
                            if all_filled:
                                await update_pullback_status(item['id'], 'FILLED')
                            else:
                                # Partially filled, save updated brackets JSON
                                db_path = "apex_lite.db"
                                async with aiosqlite.connect(db_path) as db:
                                    await db.execute('''
                                        UPDATE pullback_watchlist
                                        SET limit_entries = ?
                                        WHERE id = ?
                                    ''', (json.dumps(limit_entries), item['id']))
                                    await db.commit()
            except Exception as e:
                logger.error(f"Error in background pullback tracker: {e}")
                
            await asyncio.sleep(10)  # Check every 10 seconds

    async def start(self):
        self.running = True
        logger.info("Starting APEX System v5.0...")
        
        await self.position_guard.load_from_db()
        
        # Start background tasks
        asyncio.create_task(self.background_macro_updater())
        asyncio.create_task(self.background_trade_tracker())
        # asyncio.create_task(self.background_missed_signals_tracker())
        # asyncio.create_task(self.background_pullback_tracker())
        asyncio.create_task(self.fill_monitor.start())
        asyncio.create_task(self.order_router.start_limit_dispatcher())
        asyncio.create_task(self.shadow_monitor.start())
        asyncio.create_task(self.ws_manager.start(self.config.trading.symbols))
        asyncio.create_task(rs_matrix_engine.fast_price_poller(self.config.trading.symbols))
        
        # Start main loop
        await self.run_trading_pipeline()

    async def stop(self):
        logger.info("Initiating graceful shutdown...")
        self.running = False
        await self.exchange.close()
        logger.info("APEX System shutdown complete.")

def handle_shutdown(sys_obj: ApexSystem):
    logger.info("Received termination signal!")
    asyncio.create_task(sys_obj.stop())

async def start_dashboard_server():
    """Runs the FastAPI dashboard on PORT env var (Railway compatibility)."""
    import uvicorn
    from dashboard.api import create_app
    port = int(os.getenv("PORT", "8080"))
    app = create_app()
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    logger.info(f"Dashboard server starting on port {port}...")
    await server.serve()

async def main():
    await init_timescaledb()
    
    # --- AUTO RUN DIAGNOSTICS ON STARTUP ---
    try:
        import subprocess
        import os
        logger.info("Running Shadow Analysis Script...")
        env = os.environ.copy()
        res1 = subprocess.run(["python3", "scripts/shadow_analysis.py"], capture_output=True, text=True, env=env)
        logger.info(f"SHADOW ANALYSIS STDOUT:\n{res1.stdout}\nSTDERR:\n{res1.stderr}")
        
        logger.info("Running V7 Diagnostic Script...")
        res2 = subprocess.run(["python3", "scripts/v7_diagnostic.py"], capture_output=True, text=True, env=env)
        logger.info(f"V7 DIAGNOSTIC STDOUT:\n{res2.stdout}\nSTDERR:\n{res2.stderr}")
    except Exception as e:
        logger.error(f"Failed to auto-run diagnostic scripts: {e}")
    # ---------------------------------------
    
    apex = ApexSystem()
    
    # Register graceful shutdown signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: handle_shutdown(apex))
        
    try:
        # Run system, telegram bot, and dashboard concurrently
        await asyncio.gather(
            apex.start(),
            start_telegram_bot(),
            start_dashboard_server(),
        )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        
if __name__ == "__main__":
    asyncio.run(main())
