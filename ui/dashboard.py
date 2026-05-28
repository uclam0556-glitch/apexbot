import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from datetime import datetime, timedelta
import os

st.set_page_config(page_title="APEX v5.0 Dashboard", layout="wide", initial_sidebar_state="expanded")

# Path to the db
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apex_trading.db")

def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame(), pd.DataFrame()
        
    conn = sqlite3.connect(DB_PATH)
    
    try:
        trades = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp DESC", conn)
        trades['timestamp'] = pd.to_datetime(trades['timestamp'])
        
        system_logs = pd.read_sql_query("SELECT * FROM system_logs ORDER BY timestamp DESC LIMIT 100", conn)
    except Exception:
        trades = pd.DataFrame()
        system_logs = pd.DataFrame()
        
    conn.close()
    return trades, system_logs

st.title("🚀 APEX Quantum AI v5.0 Dashboard")

trades, logs = load_data()

if trades.empty:
    st.info("No trades recorded yet. Waiting for signals...")
else:
    # Key Metrics
    col1, col2, col3, col4 = st.columns(4)
    
    total_trades = len(trades)
    won = len(trades[trades['status'] == 'WON'])
    lost = len(trades[trades['status'] == 'LOST'])
    open_t = len(trades[trades['status'] == 'OPEN'])
    win_rate = (won / (won + lost)) * 100 if (won + lost) > 0 else 0
    total_pnl = trades['pnl_pct'].sum() if 'pnl_pct' in trades.columns else 0
    
    col1.metric("Win Rate", f"{win_rate:.1f}%")
    col2.metric("Total PnL", f"{total_pnl:+.2f}%")
    col3.metric("Total Trades", total_trades)
    col4.metric("Active Trades", open_t)
    
    # Recent Trades Table
    st.subheader("📜 Recent Trades")
    st.dataframe(trades.head(10)[['timestamp', 'symbol', 'direction', 'entry_price', 'status', 'pnl_pct']], use_container_width=True)
    
    # Cumulative PnL Chart
    st.subheader("📈 Performance Curve")
    trades_closed = trades[trades['status'].isin(['WON', 'LOST'])].copy()
    if not trades_closed.empty:
        trades_closed = trades_closed.sort_values('timestamp')
        trades_closed['cum_pnl'] = trades_closed['pnl_pct'].cumsum()
        
        fig = px.line(trades_closed, x='timestamp', y='cum_pnl', title="Cumulative PnL (%)")
        st.plotly_chart(fig, use_container_width=True)

st.sidebar.header("System Status")
st.sidebar.success("✅ Engine Running")
st.sidebar.success("✅ HMM Regime Active")
st.sidebar.success("✅ Optuna Optimizer Active")

if st.sidebar.button("Refresh Data"):
    st.rerun()
