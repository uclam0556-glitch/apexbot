import asyncio
from database.timescaledb import get_pool
from shared.config import get_config

async def audit():
    pool = await get_pool()
    async with pool.acquire() as conn:
        print("--- OPEN SIGNALS ---")
        open_signals = await conn.fetch("SELECT id, symbol, status, is_shadow FROM signals WHERE status IN ('OPEN', 'BREAKEVEN')")
        for s in open_signals:
            print(dict(s))
            
        print("--- OPEN SHADOW TRADES ---")
        st = await conn.fetch("SELECT id, signal_id, symbol, outcome FROM shadow_trades WHERE outcome = 'OPEN'")
        for s in st:
            print(dict(s))
            
        print("--- OPEN BLOCKED SHADOW TRADES ---")
        stb = await conn.fetch("SELECT id, signal_id, symbol, outcome FROM shadow_trades_blocked WHERE outcome = 'OPEN'")
        for s in stb:
            print(dict(s))

asyncio.run(audit())
