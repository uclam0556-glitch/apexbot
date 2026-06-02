"""
V7 Score Component Diagnostic Script
Run this script in the production environment (Railway) to dissect the V7 Score components
and identify which factors contribute to 'TP Missed' vs 'SL Saved'.
"""

import sqlite3
import pandas as pd
import json
import os

DB_PATH = os.getenv('SQLITE_DB_PATH', 'apex_lite.db')

def main():
    try:
        conn = sqlite3.connect(DB_PATH)
    except Exception as e:
        print(f"Failed to connect to {DB_PATH}: {e}")
        return

    print("=========================================")
    print("      V7 SCORE COMPONENT DIAGNOSTIC      ")
    print("=========================================\n")

    # We need to extract the component values (cvd_score, mtf_score, etc.)
    # Since some columns were added dynamically, we use PRAGMA to check what exists
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(shadow_trades)")
    columns = [row[1] for row in cursor.fetchall()]

    select_cols = "status, mfe_pct, mae_pct, v7_score"
    has_cvd = "cvd_score" in columns
    has_mtf = "mtf_score" in columns
    has_breadth = "breadth" in columns

    if has_cvd: select_cols += ", cvd_score"
    if has_mtf: select_cols += ", mtf_score"
    if has_breadth: select_cols += ", breadth"

    query = f"""
    SELECT {select_cols}
    FROM shadow_trades
    WHERE status != 'OPEN' AND status != 'TRACKING' AND v7_score IS NOT NULL
    """

    try:
        df = pd.read_sql_query(query, conn)
    except Exception as e:
        print(f"Error executing query: {e}")
        conn.close()
        return

    if df.empty:
        print("No resolved shadow trades found for analysis.")
        conn.close()
        return

    # Categorize outcomes
    # TP Missed: MFE > 1.0% (system blocked a trade that would have hit TP)
    # SL Saved: MAE < -1.0% (system blocked a trade that would have hit SL)
    tp_missed = df[df['mfe_pct'] > 1.0]
    sl_saved = df[df['mae_pct'] < -1.0]

    print(f"Total Evaluated Shadow Trades: {len(df)}")
    print(f"TP Missed (False Negatives): {len(tp_missed)}")
    print(f"SL Saved (True Negatives): {len(sl_saved)}\n")

    def print_component_stat(name, col):
        if col not in df.columns:
            print(f"❌ {name} column not found in database.")
            return
        
        avg_tp = tp_missed[col].mean() if not tp_missed.empty else 0
        avg_sl = sl_saved[col].mean() if not sl_saved.empty else 0
        diff = avg_tp - avg_sl
        
        print(f"--- {name} ---")
        print(f"Avg in TP Missed (Good setups we blocked): {avg_tp:.2f}")
        print(f"Avg in SL Saved (Bad setups we blocked) : {avg_sl:.2f}")
        
        if abs(diff) < 2.0:
            print(f"⚠️ DIAGNOSIS: {name} shows almost NO difference ({diff:+.2f}). It is neutralizing the score!")
        else:
            if avg_tp > avg_sl:
                print(f"✅ DIAGNOSIS: {name} correctly scores good setups higher ({diff:+.2f}).")
            else:
                print(f"🚨 DIAGNOSIS: {name} scores BAD setups HIGHER than good ones ({diff:+.2f}). Weight must be reversed!")
        print("")

    print_component_stat("Overall V7 Score", "v7_score")
    print_component_stat("MTF Score", "mtf_score")
    print_component_stat("CVD Score", "cvd_score")
    print_component_stat("Market Breadth", "breadth")

    print("NOTE: FVG and RSI contributions must be analyzed from the `feature_store` table for historical executed trades, or added to shadow_trades schema for future tracking.")

    conn.close()

if __name__ == "__main__":
    main()
