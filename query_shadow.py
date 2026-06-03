import sqlite3
import pandas as pd

conn = sqlite3.connect('apex_lite.db')

# 1. Best time of day for TP missed vs SL saved
query_time = """
SELECT strftime('%H', created_at) as hour,
       SUM(CASE WHEN mfe_pct > 0 THEN 1 ELSE 0 END) as tp_missed,
       SUM(CASE WHEN mae_pct < 0 THEN 1 ELSE 0 END) as sl_saved
FROM shadow_trades
WHERE status != 'OPEN' AND status != 'TRACKING'
GROUP BY hour
ORDER BY hour
"""
print("--- Time of Day Analysis ---")
df_time = pd.read_sql_query(query_time, conn)
print(df_time.to_string())

# 2. Symbols with highest TP missed under MTF block
query_symbols = """
SELECT symbol, COUNT(*) as tp_missed_count
FROM shadow_trades
WHERE primary_block_reason LIKE '%MTF%' AND mfe_pct > 0
GROUP BY symbol
ORDER BY tp_missed_count DESC
LIMIT 10
"""
print("\n--- MTF Blocked Symbols (TP Missed) ---")
df_symbols = pd.read_sql_query(query_symbols, conn)
print(df_symbols.to_string())

# 3. Best regime for shadow trades
query_regime = """
SELECT regime,
       SUM(CASE WHEN mfe_pct > 0 THEN 1 ELSE 0 END) as tp_missed,
       SUM(CASE WHEN mae_pct < 0 THEN 1 ELSE 0 END) as sl_saved
FROM shadow_trades
WHERE regime IS NOT NULL AND status != 'OPEN' AND status != 'TRACKING'
GROUP BY regime
"""
print("\n--- Regime Analysis ---")
df_regime = pd.read_sql_query(query_regime, conn)
print(df_regime.to_string())

conn.close()
