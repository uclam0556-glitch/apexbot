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

import os
DB_PATH = os.getenv("SQLITE_DB_PATH", "apex_lite.db")

async def init_lite_db():
    """Initializes the SQLite tables and enables WAL mode."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('PRAGMA journal_mode=WAL;')
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
        try:
            await db.execute('ALTER TABLE trades ADD COLUMN strategy TEXT DEFAULT "TREND"')
        except Exception:
            pass

        # V6.2 Feature Store
        await db.execute('''
            CREATE TABLE IF NOT EXISTS feature_store (
                trade_id INTEGER PRIMARY KEY,
                symbol TEXT,
                regime TEXT,
                ultra_score REAL,
                fvg_count INTEGER,
                btc_rsi REAL,
                funding_rate REAL,
                oi_change REAL,
                fg_index REAL,
                mtf_score REAL,
                cvd_score REAL,
                outcome TEXT,
                pnl_pct REAL,
                created_at TIMESTAMP
            )
        ''')
        await db.commit()
    logger.info("Lite DB (SQLite) initialized with Feature Store.")

async def save_trade(
    signal_id: str, 
    symbol: str, 
    direction: str, 
    entry_price: float, 
    stop_loss: float, 
    take_profit_1: float,
    take_profit_3: float,
    position_usd: float,
    reasoning: str,
    strategy: str = "TREND",
    features_dict: dict = None
):
    """Saves a new open trade to SQLite."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            INSERT INTO trades (
                signal_id, symbol, direction, strategy, entry_price, stop_loss, 
                take_profit_1, take_profit_3, position_usd, status, opened_at, reasoning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        ''', (
            signal_id, symbol, direction, strategy, entry_price, stop_loss, 
            take_profit_1, take_profit_3, position_usd, datetime.utcnow(), reasoning
        ))
        trade_id = cursor.lastrowid
        
        # Save to Feature Store
        if features_dict and trade_id:
            await db.execute('''
                INSERT INTO feature_store (
                    trade_id, symbol, regime, ultra_score, fvg_count, btc_rsi,
                    funding_rate, oi_change, fg_index, mtf_score, cvd_score, outcome, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            ''', (
                trade_id,
                symbol,
                features_dict.get('regime', 'UNKNOWN'),
                features_dict.get('ultra_score', 0.0),
                features_dict.get('fvg_count', 0),
                features_dict.get('btc_rsi', 50.0),
                features_dict.get('funding_rate', 0.0),
                features_dict.get('oi_change', 0.0),
                features_dict.get('fg_index', 50.0),
                features_dict.get('mtf_score', 0.0),
                features_dict.get('cvd_score', 0.0),
                datetime.utcnow()
            ))
            
        await db.commit()

async def get_stats():
    """Calculates win rate and PnL from SQLite."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT status, pnl_pct FROM trades WHERE status IN ("WON", "LOST", "WON_BREAKEVEN", "TIMEOUT")') as cursor:
            rows = await cursor.fetchall()
            
    total = len(rows)
    if total == 0:
        return {"total": 0, "win_rate": 0, "pnl_sum": 0, "won": 0, "lost": 0}
        
    won = sum(1 for r in rows if r[0] in ('WON', 'WON_BREAKEVEN') or (r[0] == 'TIMEOUT' and r[1] and r[1] > 0))
    lost = sum(1 for r in rows if r[0] == 'LOST' or (r[0] == 'TIMEOUT' and r[1] and r[1] <= 0))
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
    """Fetches all currently OPEN or BREAKEVEN trades."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM trades WHERE status IN ("OPEN", "BREAKEVEN")') as cursor:
            return await cursor.fetchall()

async def close_trade(trade_id: int, status: str, pnl_pct: float):
    """Marks a trade as WON or LOST and records PnL."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            UPDATE trades 
            SET status = ?, pnl_pct = ?, closed_at = ?
            WHERE id = ?
        ''', (status, pnl_pct, datetime.utcnow(), trade_id))
        
        # V6.2 Feature Store Update
        await db.execute('''
            UPDATE feature_store
            SET outcome = ?, pnl_pct = ?
            WHERE trade_id = ?
        ''', (status, pnl_pct, trade_id))
        
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

async def reset_open_trades():
    """Closes all OPEN trades as CANCELLED (for manual reset via Telegram)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            UPDATE trades
            SET status = 'CANCELLED', closed_at = ?, pnl_pct = 0
            WHERE status = 'OPEN'
        ''', (datetime.utcnow(),))
        await db.commit()
    logger.info("All open trades have been reset (CANCELLED).")

async def get_confidence_calibration(ultra_score: float) -> dict:
    """Calculates historical win rate for the score's bucket."""
    # Buckets: 5.0-5.9, 6.0-6.9, 7.0-7.9, 8.0-8.9, 9.0+
    bucket_min = float(int(ultra_score))
    bucket_max = bucket_min + 0.99
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT 
                COUNT(*) as sample_size,
                SUM(CASE WHEN outcome = 'WON' THEN 1 ELSE 0 END) as won_count
            FROM feature_store
            WHERE ultra_score >= ? AND ultra_score <= ? AND outcome != 'OPEN'
        ''', (bucket_min, bucket_max)) as cursor:
            row = await cursor.fetchone()
            
    sample_size = row['sample_size'] if row and row['sample_size'] else 0
    won_count = row['won_count'] if row and row['won_count'] else 0
    
    win_rate = (won_count / sample_size * 100) if sample_size > 0 else 0.0
    
    return {
        "bucket": f"{bucket_min:.1f}-{bucket_max:.1f}",
        "sample_size": sample_size,
        "win_rate": win_rate
    }

async def get_recent_features(limit: int = 20):
    """Fetches recent ML feature store records."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM feature_store ORDER BY created_at DESC LIMIT ?', (limit,)) as cursor:
            return await cursor.fetchall()

