"""
Shadow Trade Analyst Script — APEX v10.4
Extracts actionable insights from historical shadow trades database.
"""

import sqlite3
import pandas as pd
import os

DB_PATH = os.getenv('SQLITE_DB_PATH', 'apex_lite.db')

def main():
    try:
        conn = sqlite3.connect(DB_PATH)
    except Exception as e:
        print(f"Failed to connect to {DB_PATH}: {e}")
        return

    print("=========================================")
    print("      SHADOW TRADE ANALYSIS REPORT       ")
    print("=========================================\n")

    # ── 0. Overall Health Check ───────────────────────────────────────────────
    try:
        df_all = pd.read_sql_query("""
            SELECT status, COUNT(*) as count
            FROM shadow_trades
            GROUP BY status
        """, conn)
        print("--- 0. Shadow Trade Status Overview ---")
        print(df_all.to_string(index=False))
        
        total = df_all['count'].sum()
        resolved = df_all[df_all['status'].isin(['WON', 'LOST', 'TIMEOUT', 'BREAKEVEN'])]['count'].sum()
        tracking = df_all[df_all['status'] == 'TRACKING']['count'].sum()
        print(f"\nTotal: {total} | Resolved: {resolved} | Still Tracking: {tracking}")
        
        if resolved == 0:
            print("\n⚠️  No resolved trades yet. Shadow Monitor needs 1-6 hours of runtime.")
            print("   Analyses below will be based on MFE/MAE from path-dependency checks.\n")
        print()
    except Exception as e:
        print(f"Error executing status overview: {e}\n")

    # ── 1. Time of Day Analysis ────────────────────────────────────────────────
    query_time = """
    SELECT strftime('%H', created_at) as hour,
           SUM(CASE WHEN mfe_pct > 1.0 THEN 1 ELSE 0 END) as tp_missed,
           SUM(CASE WHEN mae_pct < -1.0 THEN 1 ELSE 0 END) as sl_saved
    FROM shadow_trades
    WHERE status NOT IN ('OPEN', 'TRACKING')
    GROUP BY hour
    ORDER BY hour
    """
    try:
        df_time = pd.read_sql_query(query_time, conn)
        if not df_time.empty:
            df_time['ratio (saved/missed)'] = df_time['sl_saved'] / df_time['tp_missed'].replace(0, 1)
            print("--- 1. Time of Day Analysis (UTC) ---")
            print(df_time.to_string(index=False))
            print()
    except Exception as e:
        print(f"Error executing time analysis: {e}")

    # ── 2. MTF-Blocked Symbols with Highest TP Missed ─────────────────────────
    query_symbols = """
    SELECT symbol, COUNT(*) as tp_missed_count, 
           MAX(mfe_pct) as max_profit_seen
    FROM shadow_trades
    WHERE primary_block_reason LIKE '%MTF%' AND mfe_pct > 1.0
    GROUP BY symbol
    ORDER BY tp_missed_count DESC
    LIMIT 10
    """
    try:
        df_symbols = pd.read_sql_query(query_symbols, conn)
        if not df_symbols.empty:
            print("--- 2. MTF Blocked Symbols (Highest TP Missed) ---")
            print(df_symbols.to_string(index=False))
            print()
    except Exception as e:
        print(f"Error executing symbols analysis: {e}")

    # ── 3. Regime Analysis ────────────────────────────────────────────────────
    query_regime = """
    SELECT regime,
           SUM(CASE WHEN mfe_pct > 1.0 THEN 1 ELSE 0 END) as tp_missed,
           SUM(CASE WHEN mae_pct < -1.0 THEN 1 ELSE 0 END) as sl_saved,
           COUNT(*) as total_trades,
           ROUND(AVG(mfe_pct), 2) as avg_mfe,
           ROUND(AVG(mae_pct), 2) as avg_mae
    FROM shadow_trades
    WHERE regime IS NOT NULL AND status NOT IN ('OPEN', 'TRACKING')
    GROUP BY regime
    """
    try:
        df_regime = pd.read_sql_query(query_regime, conn)
        if not df_regime.empty:
            df_regime['ratio (saved/missed)'] = df_regime['sl_saved'] / df_regime['tp_missed'].replace(0, 1)
            print("--- 3. Regime Analysis (BULL vs SIDEWAYS vs BEAR) ---")
            print(df_regime.to_string(index=False))
            print()
    except Exception as e:
        print(f"Error executing regime analysis: {e}")

    # ── 4. Strategy Analysis ──────────────────────────────────────────────────
    query_strategy = """
    SELECT strategy,
           SUM(CASE WHEN mfe_pct > 1.0 THEN 1 ELSE 0 END) as tp_missed,
           SUM(CASE WHEN mae_pct < -1.0 THEN 1 ELSE 0 END) as sl_saved,
           COUNT(*) as total_trades,
           ROUND(AVG(mfe_pct), 2) as avg_mfe,
           ROUND(AVG(mae_pct), 2) as avg_mae
    FROM shadow_trades
    WHERE status NOT IN ('OPEN', 'TRACKING')
    GROUP BY strategy
    ORDER BY tp_missed DESC
    """
    try:
        df_strategy = pd.read_sql_query(query_strategy, conn)
        if not df_strategy.empty:
            df_strategy['ratio (saved/missed)'] = df_strategy['sl_saved'] / df_strategy['tp_missed'].replace(0, 1)
            print("--- 4. Strategy Analysis (TREND vs MEAN_REVERSION vs CAPITULATION) ---")
            print(df_strategy.to_string(index=False))
            print()
    except Exception as e:
        print(f"Error executing strategy analysis: {e}")

    # ── 5. Block Reason Effectiveness ─────────────────────────────────────────
    query_block = """
    SELECT primary_block_reason,
           COUNT(*) as total_blocked,
           SUM(CASE WHEN mfe_pct > 1.0 THEN 1 ELSE 0 END) as tp_missed,
           SUM(CASE WHEN mae_pct < -1.0 THEN 1 ELSE 0 END) as sl_saved,
           ROUND(100.0 * SUM(CASE WHEN mae_pct < -1.0 THEN 1 ELSE 0 END) / COUNT(*), 1) as precision_pct
    FROM shadow_trades
    WHERE status NOT IN ('OPEN', 'TRACKING')
    GROUP BY primary_block_reason
    ORDER BY total_blocked DESC
    """
    try:
        df_block = pd.read_sql_query(query_block, conn)
        if not df_block.empty:
            print("--- 5. Filter Effectiveness by Block Reason ---")
            print(df_block.to_string(index=False))
            print()
    except Exception as e:
        print(f"Error executing block reason analysis: {e}")

    conn.close()

if __name__ == "__main__":
    main()
