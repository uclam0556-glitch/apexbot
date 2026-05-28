"""
APEX Trading System v4.0
Main Orchestrator Loop

Connects all engines into a single asynchronous pipeline.
Runs continuously, scanning the market every 5 minutes.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
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

# Engines
from services.engine.mtf_engine import MTFEngine
from services.engine.smc_core import FormalizedSMCCore
from services.adversarial.tester import AdversarialSignalTester
from services.engine.confluence_v4 import ConfluenceEngineV4
from services.engine.risk_engine import RiskEngine
from services.macro.correlation import CrossAssetCorrelationEngine
from services.macro.rotation_engine import CapitalRotationEngine
from services.executor.order_executor import OrderExecutor
from services.notifications.telegram_ui import start_telegram_bot
from shared.lite_db import init_lite_db, save_trade

# v5.0 Imports
from services.data.ws_manager import ExchangeWSManager
from services.intelligence.ml_regime import MLRegimeClassifier
from services.optimization.dynamic_weights import DynamicWeightsOptimizer
from shared.state import global_state

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
        self.exchange = ccxt.binance({
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
                self.rotation_state = self.rotation_engine.get_rotation_multipliers(
                    self.macro_state.dominance_signal, 
                    self.macro_state.macro_bias
                )
                logger.info(f"Macro Bias: {self.macro_state.macro_bias.value} | Alt Season: {self.macro_state.dominance_signal.season}")
            except Exception as e:
                logger.error(f"Error in macro updater: {e}")
            
            await asyncio.sleep(3600)  # 1 hour

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
            logger.info("=== STARTING SCAN CYCLE ===")
            
            for symbol in self.config.trading.symbols:
                if not self.running:
                    break
                    
                try:
                    global_state.current_symbol = symbol
                    global_state.last_scan_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                    logger.info(f"Scanning {symbol}...")
                    
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
                    vol_sma20 = df_1h['volume'].rolling(20).mean()
                    vol_ratio = df_1h['volume'].iloc[-1] / vol_sma20.iloc[-1] if vol_sma20.iloc[-1] > 0 else 1.0
                    
                    logger.info(f"{symbol} | Price=${current_price:,.4f} | RSI={rsi_now:.1f} | Vol={vol_ratio:.2f}x | TFs loaded={list(tf_data.keys())}")
                    
                    # v5.0: Dynamically classify regime
                    if not self.ml_classifier.is_trained:
                        self.ml_classifier.train_hmm(df_1h)
                        
                    current_regime = self.ml_classifier.classify_current_regime(df_1h)
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
                    
                    # 4. Confluence Scoring — now with REAL RSI
                    ofi_mock = type('obj', (object,), {'ofi_score': min(vol_ratio / 2, 1.0), 'delta_usd': 50000})()
                    
                    confluence = await self.confluence_engine.calculate_score(
                        symbol=symbol,
                        direction=direction,
                        current_price=current_price,
                        df_1h=df_1h,
                        rsi_series=rsi_mock,
                        smc=smc_analysis,
                        mtf_score=mtf_score,
                        ofi=ofi_mock,
                        regime=current_regime,
                        macro_bias=self.macro_state.macro_bias.value,
                        rotation_signal=self.rotation_state
                    )
                    # Override confluence weights based on optuna optimization
                    # ... in a fully integrated version, we would pass these weights to confluence_engine
                    
                    if not confluence.passed_threshold:
                        logger.info(f"{symbol} - Confluence score {confluence.raw_score:.2f} below threshold. Skipping.")
                        continue
                        
                    # 5. Adversarial Check
                    # Mock signal dict for tester
                    mock_sig = {"entry": current_price, "sl": current_price * 0.95, "tp1": current_price * 1.05}
                    adv_res = self.adversarial_tester.run_adversarial_test(
                        mock_sig, smc_analysis.swing_points, {}, df_1h, [], {}
                    )
                    if not adv_res.passed:
                        logger.warning(f"{symbol} - Blocked by Adversarial Tester (Score: {adv_res.risk_score}).")
                        continue
                        
                    # 6. Risk Engine
                    # Calculate SL/TP
                    sltp = self.risk_engine.calculate_sl_tp(
                        entry=current_price,
                        direction="LONG",
                        atr=atr_1h,
                        swing_points=smc_analysis.swing_points,
                        imbalance_zones=smc_analysis.imbalance_zones,
                        volume_nodes=smc_analysis.volume_nodes,
                        key_levels=[]
                    )
                    
                    # Calculate size
                    size_res = self.risk_engine.calculate_position_size_kelly(
                        deposit=self.config.trading.initial_deposit_usd,
                        win_rate_calibrated=0.55,
                        avg_win_pct=2.0,
                        avg_loss_pct=1.0,
                        volatility_regime="NORMAL",
                        current_drawdown_pct=0.0
                    )
                    
                    # 7. Package Signal
                    base_signal = SignalCore(
                        id=str(int(datetime.utcnow().timestamp())),
                        symbol=symbol,
                        direction=direction,
                        entry_price=current_price,
                        stop_loss=sltp.sl,
                        take_profit_1=sltp.tp1,
                        take_profit_2=sltp.tp2,
                        take_profit_3=sltp.tp3,
                        tp_allocation=[0.4, 0.3, 0.3],
                        risk_pct=size_res.final_risk_pct,
                        confluence=confluence,
                        generated_at=datetime.utcnow()
                    )
                    
                    package = FullSignalPackage(
                        signal=base_signal,
                        macro_context=self.macro_state,
                        onchain_context=None,
                        social_context=None,
                        adversarial_result=adv_res
                    )
                    
                    # 8. AI Audit
                    audit = await self.mock_ai_auditor(package)
                    
                    # 9. Execution
                    if audit.approved:
                        await self.executor.execute_signal(package, audit)
                        await save_trade(
                            signal_id=base_signal.id,
                            symbol=base_signal.symbol,
                            direction=base_signal.direction.value,
                            entry_price=current_price,
                            stop_loss=base_signal.stop_loss,
                            take_profit_1=base_signal.take_profit_1,
                            position_usd=size_res.position_size_usd,
                            reasoning=audit.reasoning
                        )
                        logger.info(f"Signal executed and saved for {symbol}")
                    
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

async def main():
    await init_lite_db()
    apex = ApexSystem()
    
    # Register graceful shutdown signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: handle_shutdown(apex))
        
    try:
        # Run system and telegram bot concurrently
        await asyncio.gather(
            apex.start(),
            start_telegram_bot()
        )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        
if __name__ == "__main__":
    asyncio.run(main())
