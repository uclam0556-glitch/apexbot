"""
One-shot production DB repair (audit-fixes-v11.1).

Fixes the consequences of the signals.status sync bug:
  1. Re-syncs signals.status from shadow_trades.outcome for already-closed trades
     (phantom OPEN rows were saturating ExposureManager slots -> "max slots 1/1").
  2. Closes untracked OPEN trades older than 48h as EXPIRED_UNTRACKED
     (in both shadow_trades and shadow_trades_blocked).
  3. Adds partial indexes used by ExitEngine v2 hot queries.

Safe to run multiple times (idempotent). Does NOT delete anything.

Usage (Railway):  DATABASE_URL=postgres://... python3 scripts/fix_stuck_trades.py
"""

import asyncio
import os
import sys

import asyncpg

OPEN_STATES = ('OPEN', 'PARTIAL_TP', 'BREAKEVEN', 'TRAILING')


async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is not set. On Railway: railway run python3 scripts/fix_stuck_trades.py")
        sys.exit(1)

    conn = await asyncpg.connect(db_url)
    try:
        phantom = await conn.fetchval("""
            SELECT COUNT(*) FROM signals s
            JOIN shadow_trades st ON st.signal_id = s.id
            WHERE s.status = ANY($1::text[])
              AND st.outcome IS NOT NULL
              AND NOT (st.outcome = ANY($1::text[]))
        """, list(OPEN_STATES))
        print(f"[1] Phantom open signals (closed in shadow_trades, OPEN in signals): {phantom}")

        result = await conn.execute("""
            UPDATE signals s SET status = st.outcome
            FROM shadow_trades st
            WHERE st.signal_id = s.id
              AND s.status = ANY($1::text[])
              AND st.outcome IS NOT NULL
              AND NOT (st.outcome = ANY($1::text[]))
        """, list(OPEN_STATES))
        print(f"    -> synced: {result}")

        for table in ("shadow_trades", "shadow_trades_blocked"):
            stale = await conn.execute(f"""
                UPDATE {table}
                SET outcome = 'EXPIRED_UNTRACKED', resolved_at = NOW()
                WHERE outcome = 'OPEN' AND created_at < NOW() - INTERVAL '48 hours'
            """)
            print(f"[2] {table}: stale OPEN >48h -> EXPIRED_UNTRACKED: {stale}")

        stale_sig = await conn.execute("""
            UPDATE signals SET status = 'EXPIRED_UNTRACKED'
            WHERE status = ANY($1::text[]) AND created_at < NOW() - INTERVAL '48 hours'
        """, list(OPEN_STATES))
        print(f"[2] signals: stale open >48h -> EXPIRED_UNTRACKED: {stale_sig}")

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_st_open ON shadow_trades (outcome)
            WHERE outcome IN ('OPEN','PARTIAL_TP','BREAKEVEN','TRAILING')
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_open ON signals (status)
            WHERE status IN ('OPEN','PARTIAL_TP','BREAKEVEN','TRAILING')
        """)
        print("[3] Partial indexes created.")

        remaining = await conn.fetchval(
            "SELECT COUNT(*) FROM signals WHERE status = ANY($1::text[]) AND is_shadow = FALSE",
            list(OPEN_STATES)
        )
        print(f"\nDone. Genuinely open real trades remaining: {remaining}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
