import asyncio
import pandas as pd
from database.timescaledb import get_pool
import os

async def export_csv():
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = """
        SELECT 
            s.id,
            s.created_at,
            s.symbol,
            s.strategy,
            s.direction,
            s.entry_price,
            s.sl_price as stop_loss,
            s.tp1_price as take_profit_1,
            s.tp2_price as take_profit_2,
            s.tp3_price as take_profit_3,
            s.v7_score_raw as v7_score,
            s.block_reason,
            s.status,
            st.mfe_pct,
            st.mae_pct
        FROM signals s
        JOIN shadow_trades st ON s.id = st.signal_id
        """
        records = await conn.fetch(query)
        if not records:
            print("No records found in DB.")
            return
            
        data = [dict(r) for r in records]
        df = pd.DataFrame(data)
        df.to_csv("shadow_trades_database.csv", index=False)
        print(f"Exported {len(df)} rows to shadow_trades_database.csv")

if __name__ == "__main__":
    asyncio.run(export_csv())
