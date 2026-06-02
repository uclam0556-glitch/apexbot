"""
Shadow Trade Analyst Script
Run this script in the production environment (e.g. Railway) to extract insights
from the historical shadow trades database.
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

    # 1. Best time of day for TP missed vs SL saved
    query_time = """
    SELECT strftime('%H', created_at) as hour,
           SUM(CASE WHEN mfe_pct > 1.0 THEN 1 ELSE 0 END) as tp_missed,
           SUM(CASE WHEN mae_pct < -1.0 THEN 1 ELSE 0 END) as sl_saved
    FROM shadow_trades
    WHERE status != 'OPEN' AND status != 'TRACKING'
    GROUP BY hour
    ORDER BY hour
    """
    try:
        df_time = pd.read_sql_query(query_time, conn)
        df_time['ratio (saved/missed)'] = df_time['sl_saved'] / df_time['tp_missed'].replace(0, 1)
        print("--- 1. Time of Day Analysis (UTC) ---")
        print(df_time.to_string(index=False))
        print("\n")
    except Exception as e:
        print(f"Error executing time analysis: {e}")

    # 2. Symbols with highest TP missed under MTF block
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
        print("--- 2. MTF Blocked Symbols (Highest TP Missed) ---")
        print(df_symbols.to_string(index=False))
        print("\n")
    except Exception as e:
        print(f"Error executing symbols analysis: {e}")

    # 3. Best regime for shadow trades
    query_regime = """
    SELECT regime,
           SUM(CASE WHEN mfe_pct > 1.0 THEN 1 ELSE 0 END) as tp_missed,
           SUM(CASE WHEN mae_pct < -1.0 THEN 1 ELSE 0 END) as sl_saved,
           COUNT(*) as total_trades
    FROM shadow_trades
    WHERE regime IS NOT NULL AND status != 'OPEN' AND status != 'TRACKING'
    GROUP BY regime
    """
    try:
        df_regime = pd.read_sql_query(query_regime, conn)
        df_regime['ratio (saved/missed)'] = df_regime['sl_saved'] / df_regime['tp_missed'].replace(0, 1)
        print("--- 3. Regime Analysis (BULL vs SIDEWAYS vs BEAR) ---")
        print(df_regime.to_string(index=False))
        print("\n")
    except Exception as e:
        print(f"Error executing regime analysis: {e}")

    conn.close()

if __name__ == "__main__":
    main()
