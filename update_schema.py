import asyncio
import os
import logging
from database.timescaledb import get_pool

logging.basicConfig(level=logging.INFO)

async def run():
    pool = await get_pool()
    async with pool.acquire() as conn:
        print("Creating missed_signals table...")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS missed_signals (
                id SERIAL PRIMARY KEY,
                symbol TEXT,
                direction TEXT,
                score DOUBLE PRECISION,
                entry_price DOUBLE PRECISION,
                created_at TIMESTAMPTZ NOT NULL,
                checked INTEGER DEFAULT 0,
                max_profit_pct DOUBLE PRECISION DEFAULT 0.0,
                max_drawdown_pct DOUBLE PRECISION DEFAULT 0.0,
                outcome TEXT
            );
        """)

        print("Creating filter_audit table...")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS filter_audit (
                id SERIAL PRIMARY KEY,
                symbol TEXT,
                direction TEXT,
                filter_name TEXT,
                price_at_block DOUBLE PRECISION,
                created_at TIMESTAMPTZ NOT NULL,
                checked INTEGER DEFAULT 0,
                outcome_1h_pct DOUBLE PRECISION DEFAULT 0.0,
                outcome_4h_pct DOUBLE PRECISION DEFAULT 0.0,
                outcome_24h_pct DOUBLE PRECISION DEFAULT 0.0
            );
        """)

        print("Creating pullback_watchlist table...")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS pullback_watchlist (
                id SERIAL PRIMARY KEY,
                symbol TEXT,
                direction TEXT,
                score DOUBLE PRECISION,
                original_entry DOUBLE PRECISION,
                swing_low DOUBLE PRECISION,
                limit_entries TEXT,
                stop_loss DOUBLE PRECISION,
                take_profit_1 DOUBLE PRECISION,
                take_profit_2 DOUBLE PRECISION,
                take_profit_3 DOUBLE PRECISION,
                position_usd DOUBLE PRECISION,
                ttl_expiry TIMESTAMPTZ,
                regime TEXT,
                status TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                original_breadth DOUBLE PRECISION,
                original_mtf DOUBLE PRECISION,
                original_cvd DOUBLE PRECISION,
                exchange_order_id TEXT
            );
        """)
        
        print("Schema update complete.")

if __name__ == '__main__':
    asyncio.run(run())
