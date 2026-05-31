import sqlite3
import datetime

db = sqlite3.connect('apex_lite.db')
cur = db.cursor()

# Find all stuck trades
cur.execute("SELECT id, symbol FROM trades WHERE status = 'BREAKEVEN'")
trades = cur.fetchall()

if trades:
    print(f"Found {len(trades)} stuck trades.")
    now = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    
    # Update trades table
    cur.execute("UPDATE trades SET status = 'WON', closed_at = ? WHERE status = 'BREAKEVEN'", (now,))
    
    # Update feature store
    for trade_id, symbol in trades:
        cur.execute("UPDATE feature_store SET outcome = 'WON' WHERE trade_id = ?", (trade_id,))
    
    db.commit()
    print("Fixed stuck trades and set status to WON.")
else:
    print("No stuck trades found.")

db.close()
