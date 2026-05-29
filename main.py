"""
APEX Trading System v5.0
Main Orchestrator Loop — Ultra World-Class Edition

40 coins × 5 timeframes — 12 indicators — Beautiful Telegram signals
"""

from __future__ import annotations

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
from shared.lite_db import init_lite_db, save_trade, get_open_trades, close_trade
from services.notifications.telegram_ui import start_telegram_bot, send_signal, build_signal_card, send_trade_result_notification
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

# Setup basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ApexMain")
_config = get_config()

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
                        # Fetch latest ticker
                        ticker = await self.exchange.fetch_ticker(symbol)
                        current_price = ticker.get('last')
                        
                        if not current_price:
                            continue
                            
                        status = None
                        pnl_pct = 0.0
                        
                        # LONG logic
                        if trade['direction'] == 'LONG':
                            # Breakeven / TP1 logic
                            if current_price >= trade['take_profit_1'] and trade['status'] == 'OPEN':
                                # Reached TP1 -> Move SL to Breakeven
                                status = 'BREAKEVEN'
                                pnl_pct = (current_price - trade['entry_price']) / trade['entry_price'] * 100
                                new_sl = trade['entry_price'] * 1.001 # slightly above breakeven
                                from shared.lite_db import update_trade_sl
                                await update_trade_sl(trade['id'], new_sl, status)
                                logger.info(f"Trade {symbol} hit TP1. SL moved to BREAKEVEN ({new_sl:.4f}).")
                                if bot:
                                    # We can send a partial close notification here later, for now just notify.
                                    pass
                            
                            # Final TP3 logic (Trailing ATR could go here, for now it's static TP3)
                            elif trade.get('take_profit_3') and current_price >= trade['take_profit_3']:
                                status = 'WON'
                                pnl_pct = (current_price - trade['entry_price']) / trade['entry_price'] * 100
                                
                            # Stop Loss hit (either original SL or Breakeven SL)
                            elif current_price <= trade['stop_loss']:
                                status = 'LOST' if trade['status'] == 'OPEN' else 'WON_BREAKEVEN'
                                pnl_pct = (current_price - trade['entry_price']) / trade['entry_price'] * 100
                                
                        if status in ['WON', 'LOST', 'WON_BREAKEVEN']:
                            await close_trade(trade['id'], status, pnl_pct)
                            logger.info(f"Trade {symbol} {status} at {current_price} ({pnl_pct:+.2f}%)")
                            if bot:
                                try:
                                    await send_trade_result_notification(bot, int(chat_id_str), trade, status, pnl_pct)
                                except Exception as e:
                                    logger.error(f"Failed to send result notification: {e}")
            except Exception as e:
                logger.error(f"Error in trade tracker: {e}")
                
            await asyncio.sleep(60)  # Check every 60 seconds

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
            
            # ─── MACRO BLACKOUT CHECK ────────────────────────────────────────────────
            is_blackout, blackout_reason = is_macro_blackout_window()
            if is_blackout:
                logger.info(f"🛑 MACRO BLACKOUT: {blackout_reason}. Pausing scan for 5 minutes.")
                await asyncio.sleep(300)
                continue
            
            open_trades = await get_open_trades()
            open_symbols = [t['symbol'] for t in open_trades]
            
            if len(open_trades) >= 7:
                logger.info(f"Portfolio limit reached (7 open trades). Resting until a trade closes...")
                await asyncio.sleep(60)
                continue
            
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
                    
                    # 1. Fetch Multi-Timeframe Data (ALL 5 TFs)
                    timeframes_to_fetch = ['1d', '4h', '1h', '15m', '5m']
                    tf_data = {}
                    
                    for tf in timeframes_to_fetch:
                        limit = 100 if tf in ['1d', '4h'] else 200
                        df_tf = await self.fetch_market_data(symbol, tf, limit)
                        if not df_tf.empty:
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
                    
                    logger.info(f"{symbol} | Price=${current_price:,.4f} | RSI={rsi_now:.1f} | Vol={vol_ratio:.2f}x | TFs loaded={list(tf_data.keys())}")

                    # ─── FILTER 1: ADAPTIVE RSI HARD GATE ────────────────────────────────────
                    if not self.ml_classifier.is_trained:
                        self.ml_classifier.train_hmm(df_1h)
                    current_regime = self.ml_classifier.classify_current_regime(df_1h)
                    
                    # In BULL regime, allow up to RSI 80 (trend following). Else cap at 73.
                    rsi_max = 80 if current_regime.value == "BULL" else 73
                    if rsi_now > rsi_max:
                        logger.info(f"{symbol} - [BLOCKED] Adaptive RSI Gate: RSI={rsi_now:.1f} > {rsi_max} (overheated for {current_regime.value}). Skipping.")
                        continue

                    # ─── FILTER 2: SESSION FILTER ────────────────────────────────────────────
                    # Dead zone 22:00-01:00 UTC: low liquidity, high fakeout risk
                    utc_hour = datetime.utcnow().hour
                    if 22 <= utc_hour or utc_hour < 1:
                        logger.info(f"{symbol} - [BLOCKED] Session Filter: Dead zone {utc_hour}:00 UTC. Skipping.")
                        continue

                    # ─── FILTER 3: VOLUME GATE (FIXED) ───────────────────────────────────────
                    # Block entry if recent volume is below 50% of the daily hourly average (avoids night false blocks)
                    if avg_vol_3 < baseline_hourly_vol * 0.50:
                        logger.info(f"{symbol} - [BLOCKED] Volume Gate: Vol={avg_vol_3:.0f} < 50% of 24h baseline {baseline_hourly_vol:.0f}. Skipping.")
                        continue

                    # ─── FILTER 4: ENTRY CANDLE CONFIRMATION (15m) ───────────────────────────
                    # Do not enter LONG if the last closed 15m candle is bearish
                    df_15m_check = tf_data.get('15m', pd.DataFrame())
                    if not df_15m_check.empty and len(df_15m_check) >= 2:
                        last_15m = df_15m_check.iloc[-2]  # Last CLOSED candle
                        if last_15m['close'] < last_15m['open']:  # Bearish candle
                            logger.info(f"{symbol} - [BLOCKED] Entry Candle: Last 15m candle is bearish. Waiting for confirmation.")
                            continue

                    # ─── FILTER 7: CVD ORDERFLOW (Citadel-Style) ─────────────────────────────
                    # Block LONG if Cumulative Volume Delta shows bearish divergence
                    cvd_result = {"score": 0, "divergence": False, "cvd_signal": "NEUTRAL"}
                    df_5m_cvd = tf_data.get('5m', pd.DataFrame())
                    if not df_5m_cvd.empty:
                        cvd_result = calculate_cvd(df_5m_cvd, lookback=20)
                        if cvd_result["divergence"]:
                            logger.info(f"{symbol} - [BLOCKED] CVD Divergence: Price rising but net selling detected. Skipping.")
                            continue
                        if cvd_result["cvd_signal"] == "BEARISH" and cvd_result["score"] <= -2:
                            logger.info(f"{symbol} - [BLOCKED] CVD Bearish: Strong net selling pressure. Skipping.")
                            continue
                    
                    global_state.regime = current_regime.value
                    
                    # Get dynamically optimized weights
                    weights = self.weights_optimizer.get_current_weights()
                    
                    # 2. MTF Alignment — pass ALL loaded timeframes
                    mtf_score = self.mtf_engine.get_alignment_score(symbol, tf_data)
                    
                    direction = None
                    if mtf_score.signal == "STRONG_LONG":
                        direction = Direction.LONG
                    # Spot-only: we skip STRONG_SHORT
                    
                    if not direction:
                        logger.debug(f"{symbol} - MTF: {mtf_score.signal} (score={mtf_score.score:.2f}). Skipping.")
                        continue
                        
                    # 3. SMC Structure
                    smc_analysis = self.smc_core.analyze(df_1h, symbol=symbol, lookback=5)

                    # 4. NEW: Run all technical indicators
                    indicators = run_all_indicators(df_1h, symbol=symbol)
                    ind_score = indicators.get("composite_score", 0)  # -6 to +6

                    # 4b. NEW: Fetch market context (Funding, OI, F&G, BTC.D)
                    price_change_1h = (
                        (df_1h['close'].iloc[-1] - df_1h['close'].iloc[-5]) /
                        df_1h['close'].iloc[-5] * 100
                    ) if len(df_1h) >= 5 else 0.0
                    market_ctx = await get_market_context(symbol, price_change_1h)
                    ctx_score = market_ctx.get("total_context_score", 0)  # -8 to +8

                    # 5. Confluence Scoring
                    ofi_mock = type('obj', (object,), {'ofi_score': min(vol_ratio / 2, 1.0), 'delta_usd': 50000})()

                    confluence = await self.confluence_engine.calculate_score(
                        symbol=symbol,
                        direction=direction,
                        current_price=current_price,
                        df_1h=df_1h,
                        rsi_series=rsi_series,
                        smc=smc_analysis,
                        mtf_score=mtf_score,
                        ofi=ofi_mock,
                        regime=current_regime,
                        macro_bias=self.macro_state.macro_bias.value,
                        rotation_signal=self.rotation_state
                    )

                    # 6. Compute final ultra-score (0-10)
                    # base: confluence.raw_score (0-10)
                    # bonus: indicators (+/- up to 2) + context (+/- up to 2) + CVD (+/- up to 1)
                    ind_bonus = max(-2.0, min(2.0, ind_score * 0.33))
                    ctx_bonus = max(-2.0, min(2.0, ctx_score * 0.25))
                    cvd_bonus = max(-1.0, min(1.0, cvd_result.get("score", 0) * 0.5))
                    ultra_score = max(0, min(10.0, confluence.raw_score + ind_bonus + ctx_bonus + cvd_bonus))

                    logger.info(
                        f"{symbol} | Score={ultra_score:.1f}/10 "
                        f"(conf={confluence.raw_score:.1f} ind={ind_bonus:+.1f} ctx={ctx_bonus:+.1f}) "
                        f"| RSI={rsi_now:.0f} | EMA={indicators['ema_ribbon']['label']} "
                        f"| FG={market_ctx['fear_greed']['value']}"
                    )

                    # Update hot coins tracking
                    if not hasattr(global_state, 'hot_coins'):
                        global_state.hot_coins = []
                    if ultra_score >= 5.0:
                        global_state.hot_coins = [
                            c for c in global_state.hot_coins if c['symbol'] != symbol
                        ]
                        global_state.hot_coins.append({
                            'symbol': symbol,
                            'score': ultra_score,
                            'rsi': f"{rsi_now:.0f}",
                            'regime': current_regime.value,
                        })
                        global_state.hot_coins.sort(key=lambda x: x['score'], reverse=True)
                        global_state.hot_coins = global_state.hot_coins[:10]

                    # Check minimum score threshold
                    min_score = getattr(self.config.trading, 'min_score_for_signal', 6.0)
                    if ultra_score < min_score:
                        logger.info(f"{symbol} - Ultra score {ultra_score:.1f} < {min_score}. Skipping.")
                        continue
                        
                    # 7. Adversarial Check
                    # Combine swing_highs + swing_lows for functions expecting swing_points
                    all_swing_points = smc_analysis.swing_highs + smc_analysis.swing_lows

                    # Build lightweight mock signal with only fields adversarial tester reads
                    class _MockSignal:
                        def __init__(self, sym, price):
                            self.symbol = sym
                            self.direction = Direction.LONG
                            self.entry_low = price * 0.999
                            self.entry_high = price * 1.001
                            self.stop_loss = price * 0.97
                            self.take_profit_1 = price * 1.03
                            self.take_profit_2 = price * 1.05
                            self.take_profit_3 = price * 1.08

                    class _MockOrderBook:
                        def __init__(self):
                            self.bids = []
                            self.asks = []

                    class _MockSpoofing:
                        def __init__(self):
                            self.detected = False
                            self.episodes_count = 0

                    try:
                        adv_res = self.adversarial_tester.run_adversarial_test(
                            signal=_MockSignal(symbol, current_price),
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

                    # ─── FILTER 5: BTC CORRELATION GATE ─────────────────────────────────────
                    # Block altcoin longs if BTC is showing bearish RSI pressure (< 40)
                    if 'BTC' not in symbol:
                        btc_df = tf_data.get('1h', df_1h)  # Use current symbol 1h as fallback
                        try:
                            btc_1h = await self.fetch_market_data('BTC/USDT', '1h', 50)
                            if not btc_1h.empty:
                                btc_delta = btc_1h['close'].diff()
                                btc_gain = btc_delta.clip(lower=0).rolling(14).mean()
                                btc_loss = (-btc_delta.clip(upper=0)).rolling(14).mean()
                                btc_rs = btc_gain / btc_loss.replace(0, 1e-9)
                                btc_rsi = (100 - (100 / (1 + btc_rs))).iloc[-1]
                                if btc_rsi < 42:
                                    logger.info(f"{symbol} - [BLOCKED] BTC Correlation Gate: BTC RSI={btc_rsi:.1f} < 42 (bearish). Skipping alts.")
                                    continue
                        except Exception as btc_err:
                            logger.debug(f"BTC correlation check failed (non-fatal): {btc_err}")

                    # 8. Risk Engine — SL/TP
                    sltp = self.risk_engine.calculate_sl_tp(
                        entry=current_price,
                        direction="LONG",
                        atr=atr_1h,
                        swing_points=all_swing_points,
                        imbalance_zones=smc_analysis.imbalance_zones,
                        volume_nodes=smc_analysis.volume_nodes,
                        key_levels=[]
                    )

                    # ─── FILTER 6: TIGHT ATR STOP CAP ───────────────────────────────────────
                    # If structural SL is too far (> 3%), cap it at entry - 2.0 * ATR_15m
                    sl_pct_check = abs(current_price - sltp.stop_loss) / current_price
                    if sl_pct_check > 0.030:
                        atr_15m = pd.DataFrame()
                        df_15m_atr = tf_data.get('15m', pd.DataFrame())
                        if not df_15m_atr.empty:
                            atr_15m_val = (df_15m_atr['high'] - df_15m_atr['low']).rolling(14).mean().iloc[-1]
                            tight_sl = current_price - (2.0 * atr_15m_val)
                            logger.info(f"{symbol} - ATR Stop Cap: structural SL {sl_pct_check:.1%} too wide → tightened to ATR-based SL.")
                            sltp.stop_loss = max(tight_sl, sltp.stop_loss)  # Use the tighter (higher) of the two
                    
                    # 8.5 Squeeze Engine Override
                    funding_rate_val = market_ctx["funding"]["rate_pct"]
                    oi_change_val = market_ctx["open_interest"]["change_pct"]
                    is_squeeze = False
                    
                    if funding_rate_val < -0.05 and oi_change_val > 2.0:
                        logger.info(f"🚨 SHORT SQUEEZE DETECTED on {symbol}! Overriding TP limits.")
                        is_squeeze = True
                        sltp.take_profit_1 = sltp.take_profit_3 * 0.9 # Move TP1 near TP3
                        sltp.take_profit_2 = sltp.take_profit_3 * 0.95
                        sltp.take_profit_3 = current_price * 1.20 # +20% Moonbag

                    # 9. Kelly Criterion Sizing
                    deposit = self.config.trading.initial_deposit_usd
                    # Map regime string to VolatilityRegime enum for risk engine
                    from shared.models import VolatilityRegime
                    vol_enum = VolatilityRegime.NORMAL
                    if current_regime.value == "BULL": vol_enum = VolatilityRegime.LOW # Calm up-only
                    elif current_regime.value == "BEAR": vol_enum = VolatilityRegime.HIGH # Volatile drops
                    
                    # Calculate kelly size
                    # Defaults: WR 55%, R:R 2.0 (avg_win=2%, avg_loss=1%), 0% DD
                    kelly_result = self.risk_engine.calculate_position_size_kelly(
                        deposit=deposit,
                        win_rate_calibrated=0.55,
                        avg_win_pct=2.0,
                        avg_loss_pct=1.0,
                        volatility_regime=vol_enum,
                        current_drawdown_pct=0.0
                    )
                    
                    # Convert percentage to USD allocation
                    # Kelly returns % of capital to RISK.
                    # Position Size USD = Risk USD / Stop Loss %
                    risk_pct = kelly_result.final_size_pct
                    risk_usd = deposit * risk_pct / 100
                    
                    # Squeeze Engine modifier: reduce risk if RSI is extreme
                    if is_squeeze or rsi_now >= 75:
                        risk_usd *= 0.7  # Reduce risk by 30% for over-extended/squeeze setups
                        logger.info(f"{symbol} - RSI/Squeeze Warning: Reduced risk size by 30%.")

                    sl_pct = abs(current_price - sltp.stop_loss) / current_price if current_price > 0 else 0.03
                    position_usd = (risk_usd / sl_pct) if sl_pct > 0 else risk_usd * 10
                    position_usd = min(position_usd, deposit * 0.20)  # Max 20% of deposit
                    rr_ratio = abs(sltp.take_profit_2 - current_price) / abs(current_price - sltp.stop_loss) if abs(current_price - sltp.stop_loss) > 0 else 2.0

                    # 9. Build signal package
                    signal_data = {
                        "symbol": symbol,
                        "direction": "LONG",
                        "is_squeeze": is_squeeze,
                        "entry_low": current_price * 0.999,
                        "entry_high": current_price * 1.001,
                        "stop_loss": sltp.stop_loss,
                        "tp1": sltp.take_profit_1,
                        "tp2": sltp.take_profit_2,
                        "tp3": sltp.take_profit_3,
                        "score": ultra_score,
                        "regime": current_regime.value,
                        "rsi": rsi_now,
                        "funding_rate": market_ctx["funding"]["rate_pct"],
                        "oi_change": market_ctx["open_interest"]["change_pct"],
                        "fear_greed": market_ctx["fear_greed"]["value"],
                        "btc_dominance": market_ctx["btc_dominance"]["value"],
                        "vwap_label": indicators["vwap"]["label"],
                        "ema_label": indicators["ema_ribbon"]["label"],
                        "rsi_divergence": indicators["rsi_divergence"]["label"],
                        "bb_label": indicators["bollinger"]["label"],
                        "fib_level": indicators["fibonacci"].get("nearest_fib"),
                        "position_usd": round(position_usd, 0),
                        "risk_usd": round(risk_usd, 0),
                        "rr_ratio": round(rr_ratio, 1),
                    }

                    # 10. Send beautiful signal card to Telegram
                    try:
                        from aiogram import Bot
                        token = self.config.alerts.telegram_bot_token.get_secret_value()
                        chat_id_str = self.config.alerts.telegram_chat_id
                        if token and chat_id_str:
                            bot = Bot(token=token)
                            await send_signal(bot, int(chat_id_str), signal_data)
                            await bot.session.close()
                            global_state.signals_sent_today += 1
                            logger.info(f"🚀 SIGNAL SENT: {symbol} | Score={ultra_score:.1f}/10 | Entry=${current_price:.4f}")
                    except Exception as send_err:
                        logger.error(f"Failed to send signal: {send_err}")

                    # 11. Save to DB (paper trading)
                    await save_trade(
                        signal_id=str(int(datetime.utcnow().timestamp())),
                        symbol=symbol,
                        direction="LONG",
                        entry_price=current_price,
                        stop_loss=sltp.stop_loss,
                        take_profit_1=sltp.take_profit_1,
                        take_profit_3=sltp.take_profit_3,
                        position_usd=position_usd,
                        reasoning=f"Ultra Score {ultra_score:.1f}/10 | RSI {rsi_now:.0f} | {indicators['ema_ribbon']['label']} | FG={market_ctx['fear_greed']['value']} | ML_FEATURES: {{\"fvg_count\": {len(smc_analysis.imbalance_zones)}, \"volatility\": {atr_1h/current_price:.4f}}}"
                    )
                    logger.info(f"Signal saved to DB for {symbol}")
                    
                except Exception as e:
                    logger.error(f"Error processing {symbol}: {e}", exc_info=True)
                    
                await asyncio.sleep(2) # Prevent rate limiting between pairs
                
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
