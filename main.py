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
from services.macro.correlation import CrossAssetCorrelationEngine
from services.macro.rotation_engine import CapitalRotationEngine
from services.executor.order_executor import OrderExecutor
from shared.lite_db import init_lite_db, save_trade, get_open_trades, close_trade, get_confidence_calibration, can_open_new_position, is_on_cooldown
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

# 🌟 NEW: Ultra indicators
from services.indicators.technical import run_all_indicators
from services.indicators.market_data import get_market_context
from services.intelligence.ofi_engine import calculate_orderbook_imbalance

# Setup basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ApexMain")
_config = get_config()

def check_mtf_gate(symbol: str, mtf_score: float, direction: str, regime: str, strategy: str = "TREND") -> bool:
    """
    MTF Hard Gate: blocks trading against the trend.
    Adjusted: Mean Reversion and Capitulation are exempt from strict trend requirements.
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
        if regime == "BULL" and mtf_score < 0:
            logger.info(f"{symbol} - [BLOCKED] MTF Gate: score={mtf_score:.1f} < 0 for LONG in BULL. Trend is against us.")
            return False
        if regime in ("SIDEWAYS", "BEAR", "CRISIS") and mtf_score < 2.0:
            logger.info(f"{symbol} - [BLOCKED] MTF Gate: score={mtf_score:.1f} < 2.0 for LONG in {regime}. Need strong confirmation.")
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
        
        # Initialize Engines
        self.mtf_engine = MTFEngine()
        self.smc_core = FormalizedSMCCore()
        self.adversarial_tester = AdversarialSignalTester()
        self.confluence_engine = ConfluenceEngineV4()
        self.risk_engine = RiskEngine()
        self.macro_engine = CrossAssetCorrelationEngine()
        self.rotation_engine = CapitalRotationEngine()
        
        self.executor = OrderExecutor(self.exchange)
        
        # v5.0 Engines
        self.ws_manager = ExchangeWSManager()
        self.ml_classifier = MLRegimeClassifier()
        self.weights_optimizer = DynamicWeightsOptimizer()
        self.liquidation_detector = LiquidationCascadeDetector()
        
        # Global State
        self.macro_state = None
        self.rotation_state = None
        
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
                self.macro_state = await self.macro_engine.get_full_macro_result()
                try:
                    self.rotation_state = self.rotation_engine.get_rotation_multipliers(
                        self.macro_state.dominance,
                        self.macro_state.macro_bias
                    )
                    logger.info(f"Macro Bias: {self.macro_state.macro_bias.value}")
                except Exception as rot_err:
                    logger.warning(f"Rotation engine error (non-fatal): {rot_err}")
                    self.rotation_state = None
                    
                # Update RS Matrix
                await rs_matrix_engine.update_matrix(self.config.trading.symbols)
                
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
                open_trades = await get_open_trades()
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
                            global_state.trade_excursions[trade_id] = {
                                "high": recent_high,
                                "low": recent_low
                            }
                        
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

                        # Standard TP/SL logic (only if not already TIMEOUT)
                        if not status and trade['direction'] == 'LONG':
                            if recent_high >= trade['take_profit_1'] and trade['status'] == 'OPEN':
                                status = 'WON'
                                pnl_pct = (trade['take_profit_1'] - trade['entry_price']) / trade['entry_price'] * 100
                            elif recent_low <= trade['stop_loss']:
                                status = 'LOST'
                                pnl_pct = (trade['stop_loss'] - trade['entry_price']) / trade['entry_price'] * 100

                        # SHORT logic (mirror of LONG)
                        elif not status and trade['direction'] == 'SHORT':
                            if recent_low <= trade['take_profit_1'] and trade['status'] == 'OPEN':
                                status = 'WON'
                                pnl_pct = (trade['entry_price'] - trade['take_profit_1']) / trade['entry_price'] * 100
                            elif recent_high >= trade['stop_loss']:
                                status = 'LOST'
                                pnl_pct = (trade['entry_price'] - trade['stop_loss']) / trade['entry_price'] * 100
                                
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

                            await close_trade(
                                trade['id'], 
                                status, 
                                pnl_pct,
                                max_profit_pct=max_profit_pct,
                                max_drawdown_pct=max_drawdown_pct,
                                duration_minutes=duration_minutes
                            )
                            # Cleanup memory
                            from shared.state import global_state
                            if trade['id'] in global_state.trade_excursions:
                                del global_state.trade_excursions[trade['id']]
                                
                            logger.info(f"Trade {symbol} {status} at {current_price} ({pnl_pct:+.2f}%) | MFE: {max_profit_pct:+.2f}% | MAE: {max_drawdown_pct:+.2f}% | Dur: {duration_minutes:.1f}m")
                            if bot:
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
            
            open_trades = await get_open_trades()
            open_symbols = [t['symbol'] for t in open_trades]
            
            
            # ─── RS MATRIX PRE-FILTER (Top 30 Only) ──────────────────────────────────
            top_rs_coins = rs_matrix_engine.get_top_n(30)
            scan_symbols = [c['symbol'] for c in top_rs_coins] if top_rs_coins else self.config.trading.symbols[:30]
            logger.info(f"Pre-filtered top {len(scan_symbols)} strongest coins for scanning.")

            for symbol in scan_symbols:
                if not self.running:
                    break
                    
                if symbol in open_symbols:
                    logger.debug(f"{symbol} already has an open trade. Skipping to avoid duplicate signals.")
                    continue
                    
                try:
                    global_state.current_symbol = symbol
                    global_state.last_scan_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                    logger.info(f"Scanning {symbol}...")
                    
                    # ─── LIQUIDATION CASCADE CHECK ───────────────────────────────────────────
                    if self.liquidation_detector.is_cascade_in_progress(symbol):
                        logger.warning(f"🚨 {symbol} - [BLOCKED] Liquidation Cascade in progress. Skipping.")
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
                    else:
                        baseline_hourly_vol = df_1h['volume'].rolling(24).mean().iloc[-1]
                        
                    avg_vol_3 = df_1h['volume'].iloc[-3:].mean()
                    vol_ratio = avg_vol_3 / baseline_hourly_vol if baseline_hourly_vol > 0 else 1.0
                    
                    logger.info(f"{symbol} | Price=${format_price(current_price)} | RSI={rsi_now:.1f} | Vol={vol_ratio:.2f}x | TFs loaded={list(tf_data.keys())}")

                    # ─── FILTER 1: REGIME CLASSIFICATION ─────────────────────────────────
                    if not self.ml_classifier.is_trained:
                        self.ml_classifier.train_hmm(df_1h)
                    current_regime = self.ml_classifier.classify_current_regime(df_1h)
                    regime_val = current_regime.value
                    global_state.regime = regime_val

                    # ─── FILTER 1.1: CIRCUIT BREAKER ──────────────────────────────────────
                    if not await can_open_new_position(regime_val):
                        continue

                    # ─── FILTER 1.2: COOLDOWN FILTER ──────────────────────────────────────
                    if await is_on_cooldown(symbol, cooldown_hours=4):
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
                    if avg_vol_3 < baseline_hourly_vol * 0.50:
                        logger.info(f"{symbol} - [BLOCKED] Volume Gate: Vol={avg_vol_3:.0f} < 50% of 24h baseline {baseline_hourly_vol:.0f}. Skipping.")
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

                    # ─── SPOT ONLY: определяем стратегию (только LONG) ────────────────────────
                    trade_direction = "LONG"
                    trade_strategy  = None
                    regime_val      = current_regime.value
                    
                    # BLOCK ALL SHORTS TEMPORARILY AS PER ARCHITECTURAL PLAN
                    if trade_direction == "SHORT":
                        logger.info(f"{symbol} - [BLOCKED] System is in TREND LONG ONLY mode. Shorts are disabled.")
                        continue

                    if regime_val == "BEAR":
                        # Capitulation Catcher
                        # Only top assets
                        if symbol not in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
                            logger.info(f"{symbol} - [BLOCKED] BEAR regime: non-major asset. Skipping.")
                            continue

                        # 1. Base Panic: RSI < 25 AND Volume spike > 1.5x
                        is_panic = rsi_now < 25 and vol_ratio_15m > 1.5
                        
                        # 2. Reclaim/Absorption: Real buyers (OFI > 0) OR Technical wick (Wick > 0.4)
                        is_bought = ofi_real.ofi_score > 0 or lower_wick_ratio > 0.4
                        
                        if is_panic and is_bought:
                            trade_strategy = "CAPITULATION"
                            logger.info(f"{symbol} - [CAPITULATION CATCHER] RSI={rsi_now:.1f} Vol={vol_ratio_15m:.1f}x Wick={lower_wick_ratio:.2f} OFI={ofi_real.ofi_score:.2f}")
                        else:
                            logger.info(f"{symbol} - [BLOCKED] BEAR regime: No capitulation (Panic={is_panic}, Bought={is_bought}).")
                            continue

                    elif regime_val == "SIDEWAYS" and rsi_now < 35:
                        cvd_reversing = cvd_score_val >= -1
                        if cvd_reversing:
                            trade_strategy = "MEAN_REVERSION"
                            logger.info(f"{symbol} - [MEAN REVERSION LONG] SIDEWAYS + RSI={rsi_now:.1f} (oversold) | CVD={cvd_signal}")
                        else:
                            logger.info(f"{symbol} - [BLOCKED] Mean reversion blocked: CVD still strongly bearish ({cvd_score_val}). Skipping.")
                            continue

                    else:
                        trade_strategy = "TREND"
                        
                    # ─── MTF ALIGNMENT (HARD GATE) ────────────────────────────────────────────
                    weights = self.weights_optimizer.get_current_weights()
                    mtf_score = self.mtf_engine.get_alignment_score(symbol, tf_data)

                    if not check_mtf_gate(symbol, mtf_score.score, trade_direction, regime_val, trade_strategy):
                        continue

                    # ─── SMC + INDICATORS ─────────────────────────────────────────────────────
                    smc_analysis = self.smc_core.analyze(df_1h, symbol=symbol, lookback=10)
                    indicators   = run_all_indicators(df_1h, symbol=symbol)
                    ind_score    = indicators.get("composite_score", 0)

                    # For SHORT, invert ind_score
                    if trade_direction == "SHORT":
                        ind_score = -ind_score

                    price_change_1h = (
                        (df_1h['close'].iloc[-1] - df_1h['close'].iloc[-5]) /
                        df_1h['close'].iloc[-5] * 100
                    ) if len(df_1h) >= 5 else 0.0
                    market_ctx = await get_market_context(symbol, price_change_1h)
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
                        mtf_score=mtf_score,
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
                    mtf_val = mtf_score.score if trade_direction == "LONG" else -mtf_score.score
                    if mtf_val >= 6.0:   mtf_mult = 1.20   # сильный тренд = бонус
                    elif mtf_val >= 3.0: mtf_mult = 1.10   # умеренный тренд
                    elif mtf_val >= 0.0: mtf_mult = 1.00   # нейтрально
                    elif mtf_val >= -2.0: mtf_mult = 0.50  # слабо против = сильный штраф
                    else:                 mtf_mult = 0.25  # явно против = жесткий блок

                    base_score = confluence.raw_score + ind_bonus + ctx_bonus + cvd_bonus + mr_bonus
                    ultra_score = max(0, min(10.0, base_score * mtf_mult))
                    
                    # ─── V7 ADAPTIVE SCORING (0-100) ───────────────────────────────────────────
                    v7_score = ultra_score * 10.0
                    
                    # ─── MTF HARD CAP ──────────────────────────────────────────────────────────
                    if mtf_val < 0:
                        v7_score = min(v7_score, 50.0)  # Максимум 50/100 против тренда

                    # ─── SMC EXHAUSTION PENALTIES (Score Inflation Fix) ────────────────────────
                    fvg_count = len(smc_analysis.imbalance_zones)
                    sweep_count = len(smc_analysis.liquidity_sweeps)
                    
                    if fvg_count > 8:
                        v7_score -= 25.0
                        logger.info(f"{symbol} - SMC Penalty: Too many FVGs ({fvg_count} > 8). Trend likely exhausted.")
                    
                    if sweep_count > 40:
                        v7_score -= 25.0
                        logger.info(f"{symbol} - SMC Penalty: Too many sweeps ({sweep_count} > 40). Market choppy/exhausted.")

                    # 1. Entry Candle Penalty
                    df_15m_check = tf_data.get('15m', pd.DataFrame())
                    if not df_15m_check.empty and len(df_15m_check) >= 3:
                        last3 = df_15m_check.iloc[-4:-1]
                        last1 = df_15m_check.iloc[-2]
                        if trade_strategy in ["MEAN_REVERSION", "CAPITULATION"]:
                            green_count = sum(1 for _, c in last3.iterrows() if c['close'] > c['open'])
                            if green_count == 0: v7_score -= 15
                        elif regime_val == "SIDEWAYS":
                            green_count = sum(1 for _, c in last3.iterrows() if c['close'] > c['open'])
                            if green_count < 2: v7_score -= 10
                        else:
                            if last1['close'] < last1['open']: v7_score -= 10
                            
                    # 2. BTC Correlation Penalty
                    btc_rsi = 50.0
                    if 'BTC' not in symbol:
                        try:
                            btc_1h = await self.fetch_market_data('BTC/USDT', '1h', 50)
                            if not btc_1h.empty:
                                btc_delta = btc_1h['close'].diff()
                                btc_gain  = btc_delta.clip(lower=0).rolling(14).mean()
                                btc_loss  = (-btc_delta.clip(upper=0)).rolling(14).mean()
                                btc_rsi   = (100 - (100 / (1 + btc_gain / btc_loss.replace(0, 1e-9)))).iloc[-1]
                                if trade_direction == "LONG" and btc_rsi < 42: v7_score -= 15
                                if trade_direction == "SHORT" and btc_rsi > 58: v7_score -= 15
                        except Exception:
                            pass
                    else:
                        btc_rsi = rsi_now
                    
                    # 3. CVD Divergence & Bearishness Penalty
                    if cvd_result.get("divergence"): v7_score -= 25
                    if cvd_signal == "BEARISH" and cvd_score_val <= -2: v7_score -= 20
                    
                    # 4. Overheated RSI Penalty
                    rsi_max = 80 if regime_val == "BULL" else 73
                    if rsi_now > rsi_max: v7_score -= 20
                    
                    # ─── A+ SETUP OVERRIDE ─────────────────────────────────────────────────────
                    is_a_plus = False
                    if trade_direction == "LONG" and rsi_now < 28 and cvd_score_val >= 0 and ofi_real.ofi_score > 0:
                        last_vol = df_15m_check['volume'].iloc[-2] if not df_15m_check.empty else 0
                        avg_vol = df_15m_check['volume'].iloc[-12:-2].mean() if not df_15m_check.empty else 1
                        if avg_vol > 0 and (last_vol / avg_vol) > 1.5:
                            is_a_plus = True
                            v7_score = 100.0  # Force max score
                            logger.info(f"🌟 {symbol} A+ SETUP OVERRIDE ACTIVATED! (RSI={rsi_now:.1f}, CVD+, OFI+, VOL+)")
                            
                    # ─── FINAL V7 GATE ─────────────────────────────────────────────────────────
                    if v7_score < 70 and not is_a_plus:
                        logger.info(f"{symbol} - [BLOCKED] V7 Score: {v7_score:.1f}/100. Insufficient edge. Skipping.")
                        continue
                        
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

                    min_score = getattr(self.config.trading, 'min_score_for_signal', 6.0)
                    if ultra_score < min_score:
                        logger.info(f"{symbol} - Ultra score {ultra_score:.1f} < {min_score}. Skipping.")
                        continue

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

                    # ─── RISK ENGINE — SL/TP ──────────────────────────────────────────────────
                    sltp = self.risk_engine.calculate_sl_tp(
                        entry=current_price,
                        direction=trade_direction,
                        atr=atr_1h,
                        swing_points=all_swing_points,
                        imbalance_zones=smc_analysis.imbalance_zones,
                        volume_nodes=smc_analysis.volume_nodes,
                        key_levels=[]
                    )

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
                    oi_change_val    = market_ctx["open_interest"]["change_pct"]
                    is_squeeze       = False

                    if trade_direction == "LONG" and funding_rate_val < -0.05 and oi_change_val > 2.0:
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

                    # ─── CONFIDENCE CALIBRATION ────────────────────────────────────────────────
                    confidence_data = await get_confidence_calibration(ultra_score)

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

                    # ─── SEND TO TELEGRAM ─────────────────────────────────────────────────────
                    try:
                        from aiogram import Bot
                        token        = self.config.alerts.telegram_bot_token.get_secret_value()
                        chat_id_str  = self.config.alerts.telegram_chat_id
                        if token and chat_id_str:
                            bot = Bot(token=token)
                            await send_signal(bot, int(chat_id_str), signal_data)
                            await bot.session.close()
                            global_state.signals_sent_today += 1
                            logger.info(f"{dir_emoji} 🚀 SIGNAL SENT: {symbol} {trade_direction} [{strat_label}] | Score={ultra_score:.1f}/100 | Entry=${format_price(current_price)}")
                    except Exception as send_err:
                        logger.error(f"Failed to send signal: {send_err}")

                    # ─── SAVE TO DB ────────────────────────────────────────────────────────────
                    features_dict = {
                        "regime":               regime_val,
                        "ultra_score":          ultra_score,
                        "fvg_count":            len(smc_analysis.imbalance_zones),
                        "btc_rsi":              btc_rsi if 'btc_rsi' in locals() else 50.0,
                        "funding_rate":         market_ctx["funding"]["rate_pct"] if "funding" in market_ctx else 0.0,
                        "oi_change":            market_ctx["open_interest"]["change_pct"] if "open_interest" in market_ctx else 0.0,
                        "fg_index":             market_ctx["fear_greed"]["value"] if "fear_greed" in market_ctx else 50.0,
                        "mtf_score":            mtf_score.score,
                        "cvd_score":            cvd_score_val,
                        "strategy":             trade_strategy,
                        "direction":            trade_direction,
                        # V7 Institutional Metrics
                        "slippage":             spread_pct / 2.0, # Estimated half-spread as slippage
                        "spread_at_entry":      spread_pct, 
                        "btc_trend_strength":   float(market_ctx.get("btc_dominance", {}).get("value", 55.0)), # Re-mapped dominance
                        "volume_spike_score":   vol_ratio_15m,
                    }

                    await save_trade(
                        signal_id=str(int(datetime.utcnow().timestamp())),
                        symbol=symbol,
                        direction=trade_direction,
                        entry_price=current_price,
                        stop_loss=sltp.stop_loss,
                        take_profit_1=sltp.take_profit_1,
                        position_usd=position_usd,
                        reasoning=f"{strat_label} | Score {ultra_score:.1f}/100 | RSI {rsi_now:.0f} | {indicators['ema_ribbon']['label']} | FG={market_ctx['fear_greed']['value']}",
                        strategy=trade_strategy,
                        features_dict=features_dict
                    )
                    logger.info(f"Signal saved to DB: {symbol} {trade_direction} [{strat_label}]")

                except Exception as e:
                    logger.error(f"Error processing {symbol}: {e}", exc_info=True)

                await asyncio.sleep(2)  # Prevent rate limiting between pairs
                
            logger.info("=== SCAN CYCLE COMPLETE ===")
            # Sleep for 5 minutes (300 seconds)
            await asyncio.sleep(300)

    async def start(self):
        self.running = True
        logger.info("Starting APEX System v5.0...")
        
        # Start background tasks
        asyncio.create_task(self.background_macro_updater())
        asyncio.create_task(self.background_trade_tracker())
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
    await init_lite_db()
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
