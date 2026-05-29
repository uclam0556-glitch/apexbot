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
                take_profit_2 REAL,
                take_profit_3 REAL,
                position_usd REAL,
                status TEXT, -- 'OPEN', 'WON', 'LOST'
                opened_at TIMESTAMP,
                closed_at TIMESTAMP,
                pnl_pct REAL,
                reasoning TEXT
            )
        ''')
        
        # In case table exists without take_profit_2, add it
        try:
            await db.execute("ALTER TABLE trades ADD COLUMN take_profit_2 REAL;")
        except aiosqlite.OperationalError:
            pass # Column already exists
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
        # V7 Institutional ML Features
        new_columns = [
            ("max_profit_pct", "REAL"),
            ("max_drawdown_pct", "REAL"),
            ("duration_minutes", "REAL"),
            ("slippage", "REAL"),
            ("spread_at_entry", "REAL"),
            ("btc_trend_strength", "REAL"),
            ("volume_spike_score", "REAL")
        ]
        for col_name, col_type in new_columns:
            try:
                await db.execute(f'ALTER TABLE feature_store ADD COLUMN {col_name} {col_type}')
            except Exception:
                pass
                
        await db.commit()
    logger.info("Lite DB (SQLite) initialized with Feature Store.")

async def save_trade(
    signal_id: str, 
    symbol: str, 
    direction: str, 
    entry_price: float, 
    stop_loss: float, 
    take_profit_1: float,
    take_profit_2: float,
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
                take_profit_1, take_profit_2, take_profit_3, position_usd, status, opened_at, reasoning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        ''', (
            signal_id, symbol, direction, strategy, entry_price, stop_loss, 
            take_profit_1, take_profit_2, take_profit_3, position_usd, datetime.utcnow(), reasoning
        ))
        trade_id = cursor.lastrowid
        
        # Save to Feature Store
        if features_dict and trade_id:
            await db.execute('''
                INSERT INTO feature_store (
                    trade_id, symbol, regime, ultra_score, fvg_count, btc_rsi,
                    funding_rate, oi_change, fg_index, mtf_score, cvd_score, 
                    slippage, spread_at_entry, btc_trend_strength, volume_spike_score,
                    outcome, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
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
                features_dict.get('slippage', 0.0),
                features_dict.get('spread_at_entry', 0.0),
                features_dict.get('btc_trend_strength', 0.0),
                features_dict.get('volume_spike_score', 0.0),
                datetime.utcnow()
            ))
            
        await db.commit()

async def get_stats():
    """Calculates win rate and PnL from SQLite."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT status, pnl_pct FROM trades WHERE status IN ("WON", "LOST", "WON_BREAKEVEN", "TIMEOUT", "BREAKEVEN")') as cursor:
            rows = await cursor.fetchall()
            
    total = len(rows)
    if total == 0:
        return {"total": 0, "win_rate": 0, "pnl_sum": 0, "won": 0, "lost": 0}
        
    won = sum(1 for r in rows if r[0] in ('WON', 'WON_BREAKEVEN', 'BREAKEVEN') or (r[0] == 'TIMEOUT' and r[1] and r[1] > 0))
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

async def close_trade(
    trade_id: int, 
    status: str, 
    pnl_pct: float,
    max_profit_pct: float = None,
    max_drawdown_pct: float = None,
    duration_minutes: float = None
):
    """Marks a trade as WON or LOST and records PnL along with institutional metrics."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            UPDATE trades 
            SET status = ?, pnl_pct = ?, closed_at = ?
            WHERE id = ?
        ''', (status, pnl_pct, datetime.utcnow(), trade_id))
        
        # V6.2 Feature Store Update (Now with V7 Institutional metrics)
        await db.execute('''
            UPDATE feature_store
            SET outcome = ?, pnl_pct = ?, max_profit_pct = ?, max_drawdown_pct = ?, duration_minutes = ?
            WHERE trade_id = ?
        ''', (status, pnl_pct, max_profit_pct, max_drawdown_pct, duration_minutes, trade_id))
        
        await db.commit()

async def can_open_new_position(regime: str) -> bool:
    """
    Circuit breaker for maximum open positions based on regime.
    BULL: 15, SIDEWAYS: 8, BEAR: 5, CRISIS: 0
    """
    limits = {
        "BULL": 20,
        "SIDEWAYS": 8,
        "BEAR": 5,
        "CRISIS": 0
    }
    max_positions = limits.get(regime, 0)
    if max_positions == 0:
        logger.warning(f"Position limit reached for {regime}: 0 allowed. Blocking new signal.")
        return False
        
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM trades WHERE status IN ('OPEN', 'BREAKEVEN')") as cursor:
            count = (await cursor.fetchone())[0]
            
    if count >= max_positions:
        logger.warning(f"Position limit reached for {regime}: {count}/{max_positions}. Blocking new signal.")
        return False
    return True


async def update_trade_sl(trade_id: int, new_sl: float, new_status: str = "OPEN", pnl_pct: float = None):
    """Updates stop loss for a trailing stop and optionally records PnL."""
    async with aiosqlite.connect(DB_PATH) as db:
        if pnl_pct is not None:
            await db.execute('''
                UPDATE trades 
                SET stop_loss = ?, status = ?, pnl_pct = ?
                WHERE id = ?
            ''', (new_sl, new_status, pnl_pct, trade_id))
        else:
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

async def factory_reset_db():
    """Wipes closed trades and ML features, keeping OPEN trades."""
    async with aiosqlite.connect(DB_PATH, isolation_level=None) as db:
        await db.execute("DELETE FROM trades WHERE status != 'OPEN'")
        await db.execute("DELETE FROM feature_store WHERE outcome != 'OPEN'")
        await db.execute('VACUUM')
    logger.warning("FACTORY RESET: Historical stats, trades, and ML data wiped (Open trades kept).")

async def get_confidence_calibration(ultra_score: float) -> dict:
    """Calculates historical win rate probability using Isotonic Regression (ML)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Fetch all closed trades from feature store
        async with db.execute('''
            SELECT ultra_score, outcome
            FROM feature_store
            WHERE outcome IN ('WON', 'WON_BREAKEVEN', 'LOST')
        ''') as cursor:
            rows = await cursor.fetchall()

    sample_size = len(rows)
    
    # Fallback to simple bucket if not enough data for ML
    if sample_size < 30:
        bucket_min = float(int(ultra_score))
        bucket_max = bucket_min + 0.99
        won_count = sum(1 for r in rows if r['outcome'] in ('WON', 'WON_BREAKEVEN') and bucket_min <= r['ultra_score'] <= bucket_max)
        bucket_size = sum(1 for r in rows if bucket_min <= r['ultra_score'] <= bucket_max)
        win_rate = (won_count / bucket_size * 100) if bucket_size > 0 else 0.0
        return {
            "bucket": f"{bucket_min:.1f}-{bucket_max:.1f}",
            "sample_size": sample_size,
            "win_rate": win_rate,
            "ml_calibrated": False
        }

    try:
        import numpy as np
        from sklearn.isotonic import IsotonicRegression
        
        # Prepare training data
        X = np.array([r['ultra_score'] for r in rows])
        # WON and WON_BREAKEVEN are considered positive outcomes (1), LOST is 0
        y = np.array([1 if r['outcome'] in ('WON', 'WON_BREAKEVEN') else 0 for r in rows])
        
        # Train Isotonic Regression model (out-of-core bounds [0, 1])
        iso_reg = IsotonicRegression(y_min=0, y_max=1, out_of_bounds='clip')
        iso_reg.fit(X, y)
        
        # Predict probability for the current score
        predicted_prob = iso_reg.predict([ultra_score])[0]
        win_rate = predicted_prob * 100.0
        
        return {
            "bucket": "ML_Isotonic",
            "sample_size": sample_size,
            "win_rate": win_rate,
            "ml_calibrated": True
        }
    except Exception as e:
        logger.error(f"ML Calibration failed: {e}")
        # Ultimate fallback
        return {
            "bucket": "Fallback",
            "sample_size": sample_size,
            "win_rate": 50.0,
            "ml_calibrated": False
        }

async def get_recent_features(limit: int = 20):
    """Fetches recent ML feature store records."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM feature_store ORDER BY created_at DESC LIMIT ?', (limit,)) as cursor:
            return await cursor.fetchall()

