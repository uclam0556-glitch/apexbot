import sqlite3
import pandas as pd
db = sqlite3.connect('apex_lite.db')
df = pd.read_sql_query("SELECT id, symbol, status, pnl_pct, opened_at FROM trades ORDER BY opened_at DESC", db)
print(df)
