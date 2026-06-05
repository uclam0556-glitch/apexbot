import asyncio
import asyncpg
import os

from dotenv import load_dotenv
load_dotenv()

async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("No DATABASE_URL found.")
        return
        
    conn = await asyncpg.connect(db_url)
    
    print("--- SIGNALS ---")
    count = await conn.fetchval("SELECT COUNT(*) FROM signals")
    print(f"Total Signals: {count}")
    status_counts = await conn.fetch("SELECT status, COUNT(*) FROM signals GROUP BY status")
    for r in status_counts:
        print(f"  {r['status']}: {r['count']}")
        
    print("\n--- SHADOW TRADES ---")
    st_count = await conn.fetchval("SELECT COUNT(*) FROM shadow_trades")
    print(f"Total Shadow Trades: {st_count}")
    outcome_counts = await conn.fetch("SELECT outcome, COUNT(*) FROM shadow_trades GROUP BY outcome")
    for r in outcome_counts:
        print(f"  {r['outcome']}: {r['count']}")
        
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
