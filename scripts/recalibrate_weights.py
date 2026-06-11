"""
Confluence weight recalibration from realized outcomes (the learning loop).

For every (regime, factor) pair, compares the realized PnL of trades where the
factor was TRUE at entry vs trades where it was FALSE:

    lift = E[pnl | factor=true] - E[pnl | factor=false]

The new weight is the seeded prior scaled by a bounded lift multiplier, so a
factor with consistently positive lift gains influence and a noise factor
decays toward zero. Requires >= 50 decided trades per factor per regime —
below that the seeded prior stays in force.

Run weekly (Railway cron):  railway run python3 scripts/recalibrate_weights.py
ConfluenceEngineV4 picks the result up automatically (1h cache).
"""

import asyncio
import json
import os
import sys
from collections import defaultdict

import asyncpg

MIN_SAMPLES = 50          # per factor per regime
MAX_MULT = 2.0            # weight can at most double...
MIN_MULT = 0.25           # ...or decay to a quarter of the prior
LIFT_SCALE = 0.5          # multiplier = 1 + lift_pct * LIFT_SCALE (1% lift -> 1.5x)

SEEDED_FALLBACK_WEIGHT = 1.0


async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is not set.")
        sys.exit(1)

    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch("""
            SELECT s.regime, s.v7_components, st.pnl_pct
            FROM signals s
            JOIN shadow_trades st ON st.signal_id = s.id
            WHERE s.v7_components IS NOT NULL
              AND st.pnl_pct IS NOT NULL
              AND st.resolved_at IS NOT NULL
              AND s.is_shadow = FALSE
        """)
        print(f"Resolved trades with factor vectors: {len(rows)}")
        if not rows:
            print("Nothing to calibrate yet — let the system trade first.")
            return

        # (regime, factor) -> {"true": [pnl...], "false": [pnl...], "prior": w}
        buckets = defaultdict(lambda: {"true": [], "false": [], "prior": SEEDED_FALLBACK_WEIGHT})
        for r in rows:
            comp = r['v7_components']
            if isinstance(comp, str):
                try:
                    comp = json.loads(comp)
                except json.JSONDecodeError:
                    continue
            factors = (comp or {}).get("confluence_factors", {})
            regime = r['regime'] or "UNKNOWN"
            pnl = float(r['pnl_pct'])
            for name, meta in factors.items():
                b = buckets[(regime, name)]
                b["true" if meta.get("v") else "false"].append(pnl)
                if meta.get("w"):
                    b["prior"] = float(meta["w"])

        updated = 0
        for (regime, factor), b in sorted(buckets.items()):
            n_true, n_false = len(b["true"]), len(b["false"])
            n = n_true + n_false
            if n < MIN_SAMPLES or n_true < 10 or n_false < 10:
                continue
            avg_true = sum(b["true"]) / n_true
            avg_false = sum(b["false"]) / n_false
            lift = avg_true - avg_false                      # in pnl %
            mult = max(MIN_MULT, min(MAX_MULT, 1.0 + lift * LIFT_SCALE))
            new_weight = round(b["prior"] * mult, 4)

            await conn.execute("""
                INSERT INTO confluence_weights (regime, factor, weight, lift, sample_size, updated_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (regime, factor)
                DO UPDATE SET weight = $3, lift = $4, sample_size = $5, updated_at = NOW()
            """, regime, factor, new_weight, round(lift, 4), n)
            updated += 1
            print(f"  {regime:10s} {factor:28s} n={n:5d} lift={lift:+.3f}% -> weight {b['prior']:.2f} x {mult:.2f} = {new_weight:.2f}")

        print(f"\nUpdated {updated} (regime, factor) weights. "
              f"ConfluenceV4 reloads them within 1 hour (source='outcome_trained_db').")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
