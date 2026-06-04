"""
V7 Score Component Diagnostic Script — APEX v10.4
Properly handles empty SL_Saved cohorts and provides context-aware diagnosis.
"""

import sqlite3
import pandas as pd
import os

DB_PATH = os.getenv('SQLITE_DB_PATH', 'apex_lite.db')

MIN_SAMPLE_SIZE = 10  # Minimum trades in a cohort to draw a valid conclusion

def main():
    try:
        conn = sqlite3.connect(DB_PATH)
    except Exception as e:
        print(f"Failed to connect to {DB_PATH}: {e}")
        return

    print("=========================================")
    print("      V7 SCORE COMPONENT DIAGNOSTIC      ")
    print("=========================================\n")

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
    WHERE status NOT IN ('OPEN', 'TRACKING') AND v7_score IS NOT NULL
    """

    try:
        df = pd.read_sql_query(query, conn)
    except Exception as e:
        print(f"Error executing query: {e}")
        conn.close()
        return

    if df.empty:
        print("No resolved shadow trades found. System needs more time to collect data.")
        print("Shadow Monitor resolves trades as WON/LOST/TIMEOUT every 60 seconds.")
        print("Typical wait: 2-6 hours after startup for meaningful statistics.\n")
        conn.close()
        return

    # Categorize outcomes
    # TP Missed: MFE > 1.0% (system blocked a trade that would have hit TP)
    # SL Saved: MAE < -1.0% (system blocked a trade that would have hit SL)
    tp_missed = df[df['mfe_pct'] > 1.0]
    sl_saved  = df[df['mae_pct'] < -1.0]
    timeouts  = df[df['status'] == 'TIMEOUT']
    
    # Outcome breakdown
    print(f"Total Resolved Shadow Trades:  {len(df)}")
    print(f"  TP Missed (False Negatives): {len(tp_missed)}  — Good trades we blocked")
    print(f"  SL Saved  (True Negatives):  {len(sl_saved)}  — Bad trades we correctly blocked")
    print(f"  TIMEOUT (Inconclusive):      {len(timeouts)}  — Trades that expired without resolution")
    
    if len(tp_missed) + len(sl_saved) > 0:
        precision_rate = len(sl_saved) / (len(tp_missed) + len(sl_saved)) * 100
        print(f"\n  🎯 Filter Precision: {precision_rate:.1f}% (% of blocks that were correct)")
    print()

    # Strategy breakdown
    if 'strategy' in df.columns:
        print("--- Strategy Breakdown ---")
        strat_summary = df.groupby('strategy').agg(
            total=('mfe_pct', 'count'),
            tp_missed=('mfe_pct', lambda x: (x > 1.0).sum()),
            sl_saved=('mae_pct', lambda x: (x < -1.0).sum()),
        )
        print(strat_summary.to_string())
        print()

    def print_component_stat(name, col):
        if col not in df.columns:
            print(f"❌ {name}: column not found in database — not yet tracked.")
            print()
            return

        avg_tp = tp_missed[col].mean() if not tp_missed.empty else None
        avg_sl = sl_saved[col].mean() if not sl_saved.empty else None

        print(f"--- {name} ---")
        print(f"  Avg in TP Missed (setups we blocked that were winners): {avg_tp:.2f}" if avg_tp is not None else "  Avg in TP Missed: N/A (no data)")
        print(f"  Avg in SL Saved  (setups we blocked that were losers):  {avg_sl:.2f}" if avg_sl is not None else "  Avg in SL Saved:  N/A — SL_Saved cohort is EMPTY")

        # Guard: only diagnose if both cohorts have enough data
        if avg_tp is None or avg_sl is None:
            if avg_tp is not None and avg_sl is None:
                print(f"  ⚠️  INSUFFICIENT DATA: SL_Saved cohort is empty.")
                print(f"     This is NOT necessarily an error — it may mean the market hasn't")
                print(f"     produced enough loser setups yet, or Shadow Monitor needs more time.")
                print(f"     Recommendation: wait 24-48h for meaningful SL_Saved data.\n")
            else:
                print(f"  ⚠️  INSUFFICIENT DATA: Both cohorts are empty. Wait for more resolved trades.\n")
            return

        if len(tp_missed) < MIN_SAMPLE_SIZE or len(sl_saved) < MIN_SAMPLE_SIZE:
            print(f"  ⚠️  LOW SAMPLE SIZE (TP_Missed={len(tp_missed)}, SL_Saved={len(sl_saved)}).")
            print(f"     Need at least {MIN_SAMPLE_SIZE} in each cohort for reliable diagnosis.\n")
            return

        diff = avg_tp - avg_sl
        if abs(diff) < 1.5:
            print(f"  ⚠️  NEUTRAL: {name} shows little separation ({diff:+.2f}). May be noise.\n")
        elif avg_tp > avg_sl:
            print(f"  ✅ CORRECT: {name} scores good setups higher (+{diff:.2f}). Weight is effective.\n")
        else:
            print(f"  🚨 INVERTED: {name} scores bad setups higher ({diff:+.2f}). Weight needs review.\n")

    print_component_stat("Overall V7 Score", "v7_score")
    print_component_stat("MTF Score", "mtf_score")
    print_component_stat("CVD Score", "cvd_score")
    print_component_stat("Market Breadth", "breadth")

    print("NOTE: FVG and RSI contributions must be analyzed from the `feature_store`")
    print("      table for historical executed trades, or added to shadow_trades schema.")
    
    # Regime breakdown
    if 'regime' in df.columns:
        print("\n--- Regime Breakdown ---")
        regime_summary = df.groupby('regime').agg(
            total=('mfe_pct', 'count'),
            tp_missed=('mfe_pct', lambda x: (x > 1.0).sum()),
            sl_saved=('mae_pct', lambda x: (x < -1.0).sum()),
        )
        print(regime_summary.to_string())

    conn.close()

if __name__ == "__main__":
    main()
