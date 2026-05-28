"""
APEX Trading System v4.0
shared/lite_db.py

Lightweight SQLite database for storing signals, trades, and stats 
when running locally without TimescaleDB/ClickHouse.
"""

import aiosqlite
import logging
import json
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = "apex_lite.db"

async def init_lite_db():
    """Initializes the SQLite tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT,
                symbol TEXT,
                direction TEXT,
                entry_price REAL,
                stop_loss REAL,
                take_profit_1 REAL,
                take_profit_3 REAL,
                position_usd REAL,
                status TEXT, -- 'OPEN', 'WON', 'LOST'
                opened_at TIMESTAMP,
                closed_at TIMESTAMP,
                pnl_pct REAL,
                reasoning TEXT
            )
        ''')
        await db.commit()
    logger.info("Lite DB (SQLite) initialized.")

async def save_trade(
    signal_id: str, 
    symbol: str, 
    direction: str, 
    entry_price: float, 
    stop_loss: float, 
    take_profit_1: float,
    take_profit_3: float,
    position_usd: float,
    reasoning: str
):
    """Saves a new open trade to SQLite."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO trades (
                signal_id, symbol, direction, entry_price, stop_loss, 
                take_profit_1, take_profit_3, position_usd, status, opened_at, reasoning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        ''', (
            signal_id, symbol, direction, entry_price, stop_loss, 
            take_profit_1, take_profit_3, position_usd, datetime.utcnow(), reasoning
        ))
        await db.commit()

async def get_stats():
    """Calculates win rate and PnL from SQLite."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT status, pnl_pct FROM trades WHERE status != "OPEN"') as cursor:
            rows = await cursor.fetchall()
            
    total = len(rows)
    if total == 0:
        return {"total": 0, "win_rate": 0, "pnl_sum": 0, "won": 0, "lost": 0}
        
    won = sum(1 for r in rows if r[0] == 'WON')
    lost = sum(1 for r in rows if r[0] == 'LOST')
    pnl_sum = sum(r[1] for r in rows if r[1] is not None)
    
    return {
        "total": total,
        "win_rate": (won / total) * 100,
        "pnl_sum": pnl_sum,
        "won": won,
        "lost": lost
    }

async def get_recent_trades(limit: int = 5):
    """Fetches recent trades for history."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?', (limit,)) as cursor:
            return await cursor.fetchall()

async def get_open_trades():
    """Fetches all currently OPEN trades."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM trades WHERE status = "OPEN"') as cursor:
            return await cursor.fetchall()

async def close_trade(trade_id: int, status: str, pnl_pct: float):
    """Marks a trade as WON or LOST and records PnL."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            UPDATE trades 
            SET status = ?, pnl_pct = ?, closed_at = ?
            WHERE id = ?
        ''', (status, pnl_pct, datetime.utcnow(), trade_id))
        await db.commit()

async def update_trade_sl(trade_id: int, new_sl: float, new_status: str = "OPEN"):
    """Updates stop loss for a trailing stop."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            UPDATE trades 
            SET stop_loss = ?, status = ?
            WHERE id = ?
        ''', (new_sl, new_status, trade_id))
        await db.commit()
