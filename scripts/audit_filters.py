import sys
import os
import asyncio
import logging
from datetime import datetime
import pandas as pd

# Add parent directory to path so we can import shared modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.lite_db import DB_PATH, get_unchecked_filter_blocks, update_filter_audit_result
import aiosqlite
import ccxt.async_support as ccxt

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FilterAudit")

async def update_outcomes():
    blocks = await get_unchecked_filter_blocks()
    if not blocks:
        logger.info("No unchecked filter blocks older than 24 hours.")
        return

    logger.info(f"Found {len(blocks)} unchecked filter blocks. Fetching market data...")
    exchange = ccxt.binance({'enableRateLimit': True})
    
    for row in blocks:
        audit_id = row['id']
        symbol = row['symbol']
        direction = row['direction']
        price_at_block = row['price_at_block']
        blocked_time = datetime.fromisoformat(row['created_at'])
        
        try:
            # Fetch 1h data to calculate 1h, 4h, 24h outcomes
            ohlcv = await exchange.fetch_ohlcv(symbol, '1h', limit=48)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            
            if df.empty:
                logger.warning(f"Could not fetch data for {symbol}")
                continue
                
            # Filter candles strictly after the block time
            df_future = df[df['timestamp'] > blocked_time]
            
            if len(df_future) < 24:
                logger.debug(f"Not enough future data for {symbol} yet (need 24h). Skipping.")
                continue
                
            price_1h = df_future.iloc[0]['close'] if len(df_future) > 0 else price_at_block
            price_4h = df_future.iloc[3]['close'] if len(df_future) >= 4 else price_1h
            price_24h = df_future.iloc[23]['close'] if len(df_future) >= 24 else price_4h
            
            def calc_pnl(future_price):
                if direction == "LONG":
                    return ((future_price - price_at_block) / price_at_block) * 100
                elif direction == "SHORT":
                    return ((price_at_block - future_price) / price_at_block) * 100
                return 0.0

            p_1h = calc_pnl(price_1h)
            p_4h = calc_pnl(price_4h)
            p_24h = calc_pnl(price_24h)
            
            await update_filter_audit_result(audit_id, p_1h, p_4h, p_24h)
            logger.info(f"Updated Audit #{audit_id} [{row['filter_name']} | {symbol}]: 1h={p_1h:.1f}%, 4h={p_4h:.1f}%, 24h={p_24h:.1f}%")
            
        except Exception as e:
            logger.error(f"Error updating audit for {symbol}: {e}")
            
    await exchange.close()

async def generate_report():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM filter_audit WHERE checked = 1') as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        print("\n📊 FILTER AUDIT REPORT")
        print("No audited data available yet. Let the bot run for at least 24 hours.")
        return
        
    df = pd.DataFrame([dict(r) for r in rows])
    
    print("\n" + "="*80)
    print("🛡️  APEX INSTITUTIONAL FILTER AUDIT REPORT")
    print("="*80)
    print("Показывает, сколько PnL мы 'упустили' из-за блокировки.")
    print("Отрицательный PnL значит, что фильтр СПАС НАС от убытка (ОТЛИЧНО).")
    print("Положительный PnL значит, что фильтр ОТРЕЗАЛ НАМ ПРИБЫЛЬ (ПЛОХО).\n")
    
    summary = df.groupby('filter_name').agg(
        blocks_count=('id', 'count'),
        avg_1h_pnl=('outcome_1h_pct', 'mean'),
        avg_4h_pnl=('outcome_4h_pct', 'mean'),
        avg_24h_pnl=('outcome_24h_pct', 'mean'),
    ).reset_index()
    
    for _, row in summary.iterrows():
        print(f"[{row['filter_name'].upper()}] - Blocked {row['blocks_count']} trades")
        print(f"  └─ 1h  Outcome: {row['avg_1h_pnl']:+6.2f}%")
        print(f"  └─ 4h  Outcome: {row['avg_4h_pnl']:+6.2f}%")
        print(f"  └─ 24h Outcome: {row['avg_24h_pnl']:+6.2f}%")
        
        # Verdict logic
        score = row['avg_24h_pnl']
        if score < -1.0:
            print("  ✅ VERDICT: EXCELLENT (Consistently saves capital)")
        elif score < 0.0:
            print("  ✅ VERDICT: GOOD (Minor capital protection)")
        elif score < 2.0:
            print("  ⚠️ VERDICT: NEUTRAL (Slightly restrictive, but safe)")
        else:
            print("  ❌ VERDICT: HARMFUL (Cutting too much profit, consider relaxing)")
        print("-" * 60)

async def main():
    logger.info("Starting Filter Audit Sync...")
    await update_outcomes()
    await generate_report()

if __name__ == "__main__":
    asyncio.run(main())
