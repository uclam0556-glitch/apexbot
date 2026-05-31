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
    """Initializes the SQLite tables and enables WAL mode and performance pragmas."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute('PRAGMA journal_mode=WAL;')
        await db.execute('PRAGMA synchronous=NORMAL;')
        await db.execute('PRAGMA temp_store=MEMORY;')
        await db.execute('PRAGMA mmap_size=3000000000;')
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
                slippage REAL,
                spread_at_entry REAL,
                btc_trend_strength REAL,
                volume_spike_score REAL,
                outcome TEXT,
                pnl_pct REAL,
                max_profit_pct REAL,
                max_drawdown_pct REAL,
                duration_minutes REAL,
                created_at TIMESTAMP
            )
        ''')
        
        # V7.1 Missed Signals Tracker
        await db.execute('''
            CREATE TABLE IF NOT EXISTS missed_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                direction TEXT,
                score REAL,
                entry_price REAL,
                created_at TIMESTAMP,
                checked INTEGER DEFAULT 0,
                max_profit_pct REAL DEFAULT 0.0,
                max_drawdown_pct REAL DEFAULT 0.0,
                outcome TEXT
            )
        ''')

        # V8 Institutional Filter Audit
        await db.execute('''
            CREATE TABLE IF NOT EXISTS filter_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                direction TEXT,
                filter_name TEXT,
                price_at_block REAL,
                created_at TIMESTAMP,
                checked INTEGER DEFAULT 0,
                outcome_1h_pct REAL DEFAULT 0.0,
                outcome_4h_pct REAL DEFAULT 0.0,
                outcome_24h_pct REAL DEFAULT 0.0
            )
        ''')

        # V10 pullback watchlist table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS pullback_watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                direction TEXT,
                score REAL,
                original_entry REAL,
                swing_low REAL,
                limit_entries TEXT,
                stop_loss REAL,
                take_profit_1 REAL,
                take_profit_2 REAL,
                take_profit_3 REAL,
                position_usd REAL,
                ttl_expiry TIMESTAMP,
                regime TEXT,
                status TEXT,
                created_at TIMESTAMP,
                original_breadth REAL,
                original_mtf REAL,
                original_cvd REAL
            )
        ''')

        # V6.0 Shadow Trades Table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS shadow_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                direction TEXT,
                strategy TEXT,
                entry_price REAL,
                stop_loss REAL,
                take_profit_1 REAL,
                primary_block_reason TEXT,
                all_block_reasons TEXT,
                v7_score REAL,
                status TEXT DEFAULT 'TRACKING',
                mfe_pct REAL DEFAULT 0.0,
                mae_pct REAL DEFAULT 0.0,
                created_at TIMESTAMP,
                resolved_at TIMESTAMP
            )
        ''')

        # Add columns to pullback_watchlist if they don't exist
        pb_new_columns = [
            ("original_breadth", "REAL"),
            ("original_mtf", "REAL"),
            ("original_cvd", "REAL"),
            ("exchange_order_id", "TEXT")
        ]
        for col_name, col_type in pb_new_columns:
            try:
                await db.execute(f'ALTER TABLE pullback_watchlist ADD COLUMN {col_name} {col_type}')
            except aiosqlite.OperationalError:
                pass # Column already exists
                
        # Add source column to trades
        try:
            await db.execute('ALTER TABLE trades ADD COLUMN source VARCHAR(20) DEFAULT "MARKET"')
        except aiosqlite.OperationalError:
            pass # Column already exists

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
                
        # Fix for orphaned BREAKEVEN trades from legacy multi-take-profit logic & granular recategorization
        try:
            # First, any true OPEN trades that got stuck at TP1 (legacy BREAKEVEN) and have no closed_at date are technically still running? 
            # Actually, just recategorize all historical breakevens based on actual PnL
            
            # 1. Update Small Wins (>= 0.4)
            await db.execute("UPDATE trades SET status = 'TIMEOUT_SMALL_WIN' WHERE status IN ('WON_BREAKEVEN', 'BREAKEVEN', 'TIMEOUT_BREAKEVEN') AND pnl_pct >= 0.4")
            await db.execute("UPDATE feature_store SET outcome = 'TIMEOUT_SMALL_WIN' WHERE outcome IN ('WON_BREAKEVEN', 'BREAKEVEN', 'TIMEOUT_BREAKEVEN') AND pnl_pct >= 0.4")
            
            # 2. Update Small Losses (<= -0.4)
            await db.execute("UPDATE trades SET status = 'TIMEOUT_SMALL_LOSS' WHERE status IN ('WON_BREAKEVEN', 'BREAKEVEN', 'TIMEOUT_BREAKEVEN') AND pnl_pct <= -0.4")
            await db.execute("UPDATE feature_store SET outcome = 'TIMEOUT_SMALL_LOSS' WHERE outcome IN ('WON_BREAKEVEN', 'BREAKEVEN', 'TIMEOUT_BREAKEVEN') AND pnl_pct <= -0.4")
            
            # 3. Update True Breakevens (-0.4 < pnl < 0.4)
            await db.execute("UPDATE trades SET status = 'TIMEOUT_BREAKEVEN' WHERE status IN ('WON_BREAKEVEN', 'BREAKEVEN') AND pnl_pct > -0.4 AND pnl_pct < 0.4")
            await db.execute("UPDATE feature_store SET outcome = 'TIMEOUT_BREAKEVEN' WHERE outcome IN ('WON_BREAKEVEN', 'BREAKEVEN') AND pnl_pct > -0.4 AND pnl_pct < 0.4")
            
        except Exception as e:
            logger.error(f"Migration error: {e}")
            
        await db.commit()
    logger.info("Lite DB (SQLite) initialized with Feature Store.")

async def save_trade(
    signal_id: str, 
    symbol: str, 
    direction: str, 
    entry_price: float, 
    stop_loss: float, 
    take_profit_1: float,
    position_usd: float,
    reasoning: str,
    strategy: str = "TREND",
    features_dict: dict = None,
    source: str = "MARKET"
):
    """Saves a new open trade to SQLite."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        cursor = await db.execute('''
            INSERT INTO trades (
                signal_id, symbol, direction, strategy, entry_price, stop_loss, 
                take_profit_1, take_profit_2, take_profit_3, position_usd, status, opened_at, reasoning, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
        ''', (
            signal_id, symbol, direction, strategy, entry_price, stop_loss, 
            take_profit_1, None, None, position_usd, datetime.utcnow(), reasoning, source
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
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        async with db.execute('SELECT status, pnl_pct FROM trades WHERE status IN ("WON", "LOST", "WON_BREAKEVEN", "TIMEOUT", "TIMEOUT_BREAKEVEN", "BREAKEVEN", "TIMEOUT_SMALL_WIN", "TIMEOUT_SMALL_LOSS")') as cursor:
            rows = await cursor.fetchall()
            
    total = len(rows)
    if total == 0:
        return {"total": 0, "win_rate": 0, "pnl_sum": 0, "won": 0, "lost": 0, "small_win": 0, "small_loss": 0, "breakeven": 0}
        
    won = sum(1 for r in rows if r[0] in ('WON', 'WON_BREAKEVEN') or (r[0] in ('TIMEOUT', 'TIMEOUT_SMALL_WIN', 'TIMEOUT_BREAKEVEN', 'TIMEOUT_SMALL_LOSS') and r[1] and r[1] >= 1.0))
    small_win = sum(1 for r in rows if (r[0] == 'TIMEOUT_SMALL_WIN' and (not r[1] or r[1] < 1.0)) or (r[0] == 'TIMEOUT' and r[1] and 0.4 <= r[1] < 1.0))
    breakeven = sum(1 for r in rows if (r[0] in ('BREAKEVEN', 'TIMEOUT_BREAKEVEN') and (not r[1] or -0.4 <= r[1] < 0.4)) or (r[0] == 'TIMEOUT' and r[1] and -0.4 <= r[1] < 0.4))
    small_loss = sum(1 for r in rows if (r[0] == 'TIMEOUT_SMALL_LOSS' and (not r[1] or r[1] > -1.0)) or (r[0] == 'TIMEOUT' and r[1] and -1.0 < r[1] <= -0.4))
    lost = sum(1 for r in rows if r[0] == 'LOST' or (r[0] in ('TIMEOUT', 'TIMEOUT_SMALL_WIN', 'TIMEOUT_BREAKEVEN', 'TIMEOUT_SMALL_LOSS') and r[1] and r[1] <= -1.0))
    
    pnl_sum = sum(r[1] for r in rows if r[1] is not None)
    
    # Win rate strictly ignores ALL micro and breakeven trades
    active_trades = won + lost
    win_rate = (won / active_trades * 100) if active_trades > 0 else 0.0
    
    return {
        "total": total,
        "win_rate": win_rate,
        "pnl_sum": pnl_sum,
        "won": won,
        "small_win": small_win,
        "lost": lost,
        "small_loss": small_loss,
        "breakeven": breakeven
    }

async def get_recent_trades(limit: int = 5):
    """Fetches recent trades for history."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?', (limit,)) as cursor:
            return await cursor.fetchall()

async def get_open_trades():
    """Fetches all currently OPEN or BREAKEVEN trades."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM trades WHERE status IN ("OPEN", "BREAKEVEN")') as cursor:
            return await cursor.fetchall()

async def get_trade_by_signal_id(signal_id: str) -> dict:
    """Checks if a trade already exists by its signal_id."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM trades WHERE signal_id = ?', (signal_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

async def close_trade(
    trade_id: int, 
    status: str, 
    pnl_pct: float,
    max_profit_pct: float = None,
    max_drawdown_pct: float = None,
    duration_minutes: float = None
):
    """Marks a trade as WON or LOST and records PnL along with institutional metrics."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
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

async def update_trade_sl(trade_id: int, new_sl: float):
    """Updates the stop loss of an open trade (Trailing SL)."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute('''
            UPDATE trades 
            SET stop_loss = ?
            WHERE id = ?
        ''', (new_sl, trade_id))
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
        
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        async with db.execute("SELECT COUNT(*) FROM trades WHERE status IN ('OPEN', 'BREAKEVEN')") as cursor:
            count = (await cursor.fetchone())[0]
            
    if count >= max_positions:
        logger.warning(f"Position limit reached for {regime}: {count}/{max_positions}. Blocking new signal.")
        return False
    return True


async def is_on_cooldown(symbol: str, cooldown_hours: int = 4) -> bool:
    """Checks if the symbol was recently closed within the cooldown window."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        async with db.execute(
            "SELECT closed_at FROM trades WHERE symbol = ? AND status != 'OPEN' AND closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 1", 
            (symbol,)
        ) as cursor:
            row = await cursor.fetchone()
            
    if not row:
        return False
        
    try:
        last_closed = datetime.fromisoformat(row[0])
        hours_since = (datetime.utcnow() - last_closed).total_seconds() / 3600
        if hours_since < cooldown_hours:
            logger.info(f"COOLDOWN: {symbol} was traded {hours_since:.1f} hours ago (Cooldown: {cooldown_hours}h). Blocking entry.")
            return True
    except Exception as e:
        logger.error(f"Cooldown parse error for {symbol}: {e}")
        
    return False

async def update_trade_sl(trade_id: int, new_sl: float, new_status: str = "OPEN", pnl_pct: float = None):
    """Updates stop loss for a trailing stop and optionally records PnL."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
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
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute('''
            UPDATE trades
            SET status = 'CANCELLED', closed_at = ?, pnl_pct = 0
            WHERE status = 'OPEN'
        ''', (datetime.utcnow(),))
        await db.commit()
    logger.info("All open trades have been reset (CANCELLED).")

async def factory_reset_db():
    """Wipes closed trades and ML features, keeping OPEN trades."""
    # Create database backup before wiping
    import shutil
    if os.path.exists(DB_PATH):
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{DB_PATH}.backup_{timestamp}"
            shutil.copy2(DB_PATH, backup_path)
            logger.info(f"Database backup created successfully at {backup_path} before factory reset.")
        except Exception as e:
            logger.error(f"Failed to create database backup: {e}")

    async with aiosqlite.connect(DB_PATH, isolation_level=None) as db:
        await db.execute("DELETE FROM trades WHERE status != 'OPEN'")
        await db.execute("DELETE FROM feature_store WHERE outcome != 'OPEN'")
        await db.execute('VACUUM')
    logger.warning("FACTORY RESET: Historical stats, trades, and ML data wiped (Open trades kept).")

async def get_confidence_calibration(features: dict) -> dict:
    """Calculates historical win rate probability using K-Nearest Neighbors (ML) on a feature vector."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT ultra_score, btc_rsi, cvd_score, mtf_score, funding_rate, outcome
            FROM feature_store
            WHERE outcome IN ('WON', 'WON_BREAKEVEN', 'LOST', 'TIMEOUT', 'TIMEOUT_BREAKEVEN', 'BREAKEVEN')
        ''') as cursor:
            rows = await cursor.fetchall()

    sample_size = len(rows)
    
    # Fallback if extremely low data
    if sample_size < 5:
        return {
            "bucket": "Fallback_LowData",
            "sample_size": sample_size,
            "win_rate": 50.0,
            "ml_calibrated": False
        }

    try:
        import numpy as np
        from sklearn.neighbors import KNeighborsClassifier
        from sklearn.preprocessing import StandardScaler
        
        def safe_float(val):
            return float(val) if val is not None else 0.0

        # Prepare training data
        X = np.array([[
            safe_float(r['ultra_score']), 
            safe_float(r['btc_rsi']), 
            safe_float(r['cvd_score']), 
            safe_float(r['mtf_score']), 
            safe_float(r['funding_rate'])
        ] for r in rows])
        
        # WON and WON_BREAKEVEN are positive outcomes (1), everything else is 0
        y = np.array([1 if r['outcome'] in ('WON', 'WON_BREAKEVEN') else 0 for r in rows])
        
        # Standardize features (KNN is distance-based, so scaling is critical)
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        n_neighbors = min(15, sample_size)
        knn = KNeighborsClassifier(n_neighbors=n_neighbors, weights='distance')
        knn.fit(X_scaled, y)
        
        # Current feature vector
        current_X = np.array([[
            safe_float(features.get('ultra_score', 0)),
            safe_float(features.get('btc_rsi', 50)),
            safe_float(features.get('cvd_score', 0)),
            safe_float(features.get('mtf_score', 0)),
            safe_float(features.get('funding_rate', 0))
        ]])
        
        current_X_scaled = scaler.transform(current_X)
        
        # Predict probability of class 1 (WON)
        probs = knn.predict_proba(current_X_scaled)
        if probs.shape[1] > 1:
            win_rate = probs[0][1] * 100.0
        else:
            win_rate = 100.0 if knn.classes_[0] == 1 else 0.0
            
        return {
            "bucket": f"KNN_{n_neighbors}",
            "sample_size": sample_size,
            "win_rate": win_rate,
            "ml_calibrated": True
        }
    except Exception as e:
        logger.error(f"KNN ML Calibration failed: {e}")
        return {
            "bucket": "Fallback_Error",
            "sample_size": sample_size,
            "win_rate": 50.0,
            "ml_calibrated": False
        }

async def get_recent_features(limit: int = 20):
    """Fetches recent ML feature store records."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM feature_store ORDER BY created_at DESC LIMIT ?', (limit,)) as cursor:
            return await cursor.fetchall()

# ─── V7.1 MISSED SIGNALS TRACKER ─────────────────────────────────────────────

async def save_missed_signal(symbol: str, direction: str, score: float, entry_price: float):
    """Saves a blocked signal to evaluate later if penalties are too strict."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute('''
            INSERT INTO missed_signals (symbol, direction, score, entry_price, created_at, checked)
            VALUES (?, ?, ?, ?, ?, 0)
        ''', (symbol, direction, score, entry_price, datetime.utcnow()))
        await db.commit()

async def get_unchecked_missed_signals():
    """Fetches missed signals older than 2 hours that haven't been evaluated."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT * FROM missed_signals 
            WHERE checked = 0 AND datetime(created_at, '+2 hours') <= datetime('now')
        ''') as cursor:
            return await cursor.fetchall()

async def update_missed_signal_result(signal_id: int, pnl_pct: float, outcome: str):
    """Marks a missed signal as checked and records the hypothetical PnL."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute('''
            UPDATE missed_signals 
            SET checked = 1, max_profit_pct = ?, outcome = ?
            WHERE id = ?
        ''', (pnl_pct, outcome, signal_id))
        await db.commit()

# ─── V8 INSTITUTIONAL FILTER AUDIT ───────────────────────────────────────────

async def save_filter_block(symbol: str, direction: str, filter_name: str, price: float):
    """Saves a hard block event to audit the filter's performance later."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute('''
            INSERT INTO filter_audit (symbol, direction, filter_name, price_at_block, created_at, checked)
            VALUES (?, ?, ?, ?, ?, 0)
        ''', (symbol, direction, filter_name, price, datetime.utcnow()))
        await db.commit()

async def get_unchecked_filter_blocks():
    """Fetches filter blocks older than 24 hours that haven't been audited."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT * FROM filter_audit 
            WHERE checked = 0 AND datetime(created_at, '+24 hours') <= datetime('now')
        ''') as cursor:
            return await cursor.fetchall()

async def update_filter_audit_result(audit_id: int, p_1h: float, p_4h: float, p_24h: float):
    """Marks a filter block as audited and records future price changes."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute('''
            UPDATE filter_audit 
            SET checked = 1, outcome_1h_pct = ?, outcome_4h_pct = ?, outcome_24h_pct = ?
            WHERE id = ?
        ''', (p_1h, p_4h, p_24h, audit_id))
        await db.commit()

# ─── V10 PULLBACK WATCHLIST ENGINE ───────────────────────────────────────────

async def save_pullback_item(
    symbol: str,
    direction: str,
    score: float,
    original_entry: float,
    swing_low: float,
    limit_entries: list[dict],
    stop_loss: float,
    take_profit_1: float,
    take_profit_2: float,
    take_profit_3: float,
    position_usd: float,
    ttl_minutes: int,
    regime: str,
    status: str = 'WAITING',
    original_breadth: float = 50.0,
    original_mtf: float = 0.0,
    original_cvd: float = 0.0
):
    """Saves a pullback watchlist setup to track limit entries with specified status."""
    from datetime import timedelta
    expiry = datetime.utcnow() + timedelta(minutes=ttl_minutes)
    limit_json = json.dumps(limit_entries)
    
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute('''
            INSERT INTO pullback_watchlist (
                symbol, direction, score, original_entry, swing_low,
                limit_entries, stop_loss, take_profit_1, take_profit_2, take_profit_3,
                position_usd, ttl_expiry, regime, status, created_at,
                original_breadth, original_mtf, original_cvd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            symbol, direction, score, original_entry, swing_low,
            limit_json, stop_loss, take_profit_1, take_profit_2, take_profit_3,
            position_usd, expiry, regime, status, datetime.utcnow(),
            original_breadth, original_mtf, original_cvd
        ))
        await db.commit()

async def get_active_pullback_items() -> list[dict]:
    """Fetches all pullback watchlist setups currently waiting and not expired."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT * FROM pullback_watchlist
            WHERE status = 'WAITING' AND datetime(ttl_expiry) > datetime('now')
        ''') as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def get_pullback_items_by_status(status: str) -> list[dict]:
    """Fetches all pullback watchlist setups with a specific status and not expired."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT * FROM pullback_watchlist
            WHERE status = ? AND datetime(ttl_expiry) > datetime('now')
        ''', (status,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def update_pullback_status(item_id: int, new_status: str):
    """Updates the status of a watchlist setup (e.g. FILLED, EXPIRED, CANCELLED)."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute('''
            UPDATE pullback_watchlist
            SET status = ?
            WHERE id = ?
        ''', (new_status, item_id))
        await db.commit()

async def update_pullback_limit_entries(
    item_id: int,
    limit_entries: list[dict],
    take_profit_1: float,
    take_profit_2: float,
    take_profit_3: float,
    new_status: str,
    exchange_order_id: str = None
):
    """Promotes a pullback watchlist item by adding limit entries and setting new status."""
    limit_json = json.dumps(limit_entries)
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute('''
            UPDATE pullback_watchlist
            SET limit_entries = ?, take_profit_1 = ?, take_profit_2 = ?, take_profit_3 = ?, status = ?, exchange_order_id = ?
            WHERE id = ?
        ''', (limit_json, take_profit_1, take_profit_2, take_profit_3, new_status, exchange_order_id, item_id))
        await db.commit()

async def expire_old_pullback_items():
    """Finds all WAITING and WAITING_STRUCTURE pullback items that have passed their TTL and marks them EXPIRED/EXPIRED_STRUCTURE."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        # Expire active limit orders to EXPIRED
        await db.execute('''
            UPDATE pullback_watchlist
            SET status = 'EXPIRED'
            WHERE status = 'WAITING' AND datetime(ttl_expiry) <= datetime('now')
        ''')
        # Expire watchlist structures to EXPIRED_STRUCTURE
        await db.execute('''
            UPDATE pullback_watchlist
            SET status = 'EXPIRED_STRUCTURE'
            WHERE status = 'WAITING_STRUCTURE' AND datetime(ttl_expiry) <= datetime('now')
        ''')
        await db.commit()

async def is_pullback_on_structure_cooldown(symbol: str) -> bool:
    """Checks if a symbol has an active 120-minute cooldown after expiring as EXPIRED_STRUCTURE."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        async with db.execute('''
            SELECT id FROM pullback_watchlist
            WHERE symbol = ? AND status = 'EXPIRED_STRUCTURE'
              AND datetime(ttl_expiry, '+120 minutes') > datetime('now')
        ''', (symbol,)) as cursor:
            row = await cursor.fetchone()
            return row is not None

async def create_shadow_trade(
    symbol: str,
    direction: str,
    strategy: str,
    entry_price: float,
    stop_loss: float,
    take_profit_1: float,
    primary_block_reason: str,
    all_block_reasons: list,
    v7_score: float
):
    """Creates a new shadow trade, ensuring no duplicates within 15 minutes."""
    if entry_price <= 0 or stop_loss <= 0 or take_profit_1 <= 0:
        return
        
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        # Check 15-minute duplicate
        async with db.execute('''
            SELECT id FROM shadow_trades
            WHERE symbol = ? AND direction = ? AND primary_block_reason = ?
              AND datetime(created_at, '+15 minutes') > datetime('now')
        ''', (symbol, direction, primary_block_reason)) as cursor:
            if await cursor.fetchone():
                return  # Duplicate prevented
                
        import json
        reasons_json = json.dumps(all_block_reasons)
        
        await db.execute('''
            INSERT INTO shadow_trades (
                symbol, direction, strategy, entry_price, stop_loss, take_profit_1, 
                primary_block_reason, all_block_reasons, v7_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ''', (symbol, direction, strategy, entry_price, stop_loss, take_profit_1, primary_block_reason, reasons_json, v7_score))
        await db.commit()
        
        import logging
        logging.getLogger("ShadowTrade").info(f"[SHADOW TRADE CREATED] {symbol} {direction} [{strategy}] - Blocked by: {primary_block_reason} - Score: {v7_score:.1f}")

async def get_tracking_shadow_trades():
    """Fetches all shadow trades that are currently TRACKING."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT * FROM shadow_trades
            WHERE status = 'TRACKING'
        ''') as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def update_shadow_trade_status(trade_id: int, new_status: str, mfe: float = 0.0, mae: float = 0.0):
    """Updates the status of a shadow trade (WON, LOST, TIMEOUT)."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute('''
            UPDATE shadow_trades
            SET status = ?, mfe_pct = ?, mae_pct = ?, resolved_at = datetime('now')
            WHERE id = ?
        ''', (new_status, mfe, mae, trade_id))
        await db.commit()
