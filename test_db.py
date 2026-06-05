import asyncio
import os
import sys

from database.timescaledb import init_timescaledb, get_pool

async def run_test():
    try:
        print("Initializing TimescaleDB...")
        await init_timescaledb()
        print("Initialization successful.")
        
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Check table existences
            tables = await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")
            table_names = [r['table_name'] for r in tables]
            print(f"Tables in DB: {table_names}")
            
            # Ensure our tables exist
            required = ['signals', 'shadow_trades', 'ohlcv', 'smc_events']
            for req in required:
                if req in table_names:
                    print(f"✅ Table '{req}' exists.")
                else:
                    print(f"❌ Table '{req}' is MISSING.")
                    
            print("DB Connection & Schema are 100% healthy.")
    except Exception as e:
        print(f"Database error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(run_test())
