import asyncio
import logging
from datetime import datetime, timedelta
import pandas as pd
import ccxt
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.engine.smc_core import FormalizedSMCCore
from services.engine.risk_engine import RiskEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("Backtester")

def fetch_historical_data(symbol: str, timeframe: str, limit: int = 1000) -> pd.DataFrame:
    exchange = ccxt.mexc()
    try:
        # Fetch data
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    finally:
        pass

def run_backtest(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 500):
    logger.info(f"Starting backtest for {symbol} on {timeframe}...")
    df = fetch_historical_data(symbol, timeframe, limit)
    
    if df.empty:
        logger.error("No data fetched.")
        return
        
    smc_core = FormalizedSMCCore()
    risk_engine = RiskEngine()
    
    trades = []
    
    # We simulate a rolling window
    window_size = 200
    for i in range(window_size, len(df)):
        window = df.iloc[i-window_size:i]
        current_candle = window.iloc[-1]
        
        # Analyze SMC on this window
        analysis = smc_core.analyze_market_structure(window)
        
        # Simple entry condition (mock): Premium/Discount < 0.3 means deeply discounted
        if analysis.premium_discount < 0.3:
            current_price = current_candle['close']
            atr = (window['high'] - window['low']).mean()
            
            # Calculate SL/TP
            sltp = risk_engine.calculate_sl_tp(
                entry=current_price,
                direction="LONG",
                atr=atr,
                swing_points={"highs": [], "lows": []}, # Simplified
                imbalance_zones=analysis.imbalance_zones,
                volume_nodes=analysis.volume_nodes,
                key_levels=[]
            )
            
            trades.append({
                'entry_idx': i,
                'entry_price': current_price,
                'sl': sltp.stop_loss,
                'tp1': sltp.take_profit_1,
                'tp3': sltp.take_profit_3,
                'status': 'OPEN'
            })
            
    # Simulate execution
    won = 0
    lost = 0
    breakeven = 0
    pnl_sum = 0.0
    
    logger.info(f"Simulating execution of {len(trades)} potential setups...")
    for t in trades:
        entry_idx = t['entry_idx']
        # Look forward
        future = df.iloc[entry_idx:]
        
        sl = t['sl']
        tp1 = t['tp1']
        tp3 = t['tp3']
        entry = t['entry_price']
        status = t['status']
        
        for _, row in future.iterrows():
            low = row['low']
            high = row['high']
            
            if status == 'OPEN':
                if low <= sl:
                    status = 'LOST'
                    pnl_sum += (sl - entry) / entry * 100
                    lost += 1
                    break
                elif high >= tp1:
                    status = 'BREAKEVEN'
                    sl = entry * 1.001 # Move to breakeven
                    
            if status == 'BREAKEVEN':
                if low <= sl:
                    status = 'WON_BREAKEVEN'
                    breakeven += 1
                    break
                elif high >= tp3:
                    status = 'WON'
                    pnl_sum += (tp3 - entry) / entry * 100
                    won += 1
                    break

    total = won + lost + breakeven
    win_rate = (won / total * 100) if total > 0 else 0
    
    logger.info("=== BACKTEST RESULTS ===")
    logger.info(f"Symbol: {symbol}")
    logger.info(f"Total Setups: {total}")
    logger.info(f"Won (TP3 Hit): {won}")
    logger.info(f"Breakeven (Hit TP1 then stopped out): {breakeven}")
    logger.info(f"Lost (Direct SL Hit): {lost}")
    logger.info(f"Win Rate (Strict TP3): {win_rate:.2f}%")
    logger.info(f"Total PnL % (Unleveraged): {pnl_sum:.2f}%")

if __name__ == "__main__":
    run_backtest("FET/USDT")
