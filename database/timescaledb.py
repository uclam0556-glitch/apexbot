import os
import asyncpg
import logging

logger = logging.getLogger(__name__)

# Railway injects DATABASE_URL automatically for connected Postgres services
DATABASE_URL = os.getenv('DATABASE_URL')

DATABASE_CONFIG = {
    'host': os.getenv('TIMESCALE_HOST', os.getenv('PGHOST', 'localhost')),
    'port': int(os.getenv('TIMESCALE_PORT', os.getenv('PGPORT', 5432))),
    'database': os.getenv('TIMESCALE_DB', os.getenv('PGDATABASE', 'apex_v10')),
    'user': os.getenv('TIMESCALE_USER', os.getenv('PGUSER', 'apex')),
    'password': os.getenv('TIMESCALE_PASSWORD', os.getenv('PGPASSWORD', 'apexpass')),
    'min_size': 5,
    'max_size': 20,  # pool для 95 symbols + analytics
    'command_timeout': 30.0,
}

_pool = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        try:
            if DATABASE_URL:
                _pool = await asyncpg.create_pool(
                    dsn=DATABASE_URL,
                    min_size=DATABASE_CONFIG['min_size'],
                    max_size=DATABASE_CONFIG['max_size'],
                    command_timeout=DATABASE_CONFIG['command_timeout']
                )
            else:
                _pool = await asyncpg.create_pool(**DATABASE_CONFIG)
            logger.info("PostgreSQL pool created successfully.")
        except Exception as e:
            logger.error(f"Failed to create TimescaleDB pool: {e}")
            raise
    return _pool

async def init_timescaledb():
    """Initializes the TimescaleDB schema as defined in APEX v10.5 upgrade."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        logger.info("Initializing PostgreSQL schema (with TimescaleDB fallback)...")
        
        # Try to enable TimescaleDB extension, gracefully fallback to vanilla Postgres
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        except Exception as e:
            logger.warning(f"TimescaleDB extension not available on this server. Falling back to vanilla PostgreSQL. Reason: {e}")
            
        # TABLE 1: OHLCV
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                time            TIMESTAMPTZ NOT NULL,
                symbol          TEXT NOT NULL,
                timeframe       TEXT NOT NULL,
                open            DOUBLE PRECISION,
                high            DOUBLE PRECISION,
                low             DOUBLE PRECISION,
                close           DOUBLE PRECISION,
                volume          DOUBLE PRECISION,
                data_source     TEXT,
                bar_index       BIGINT,
                is_closed       BOOLEAN DEFAULT TRUE,
                UNIQUE (symbol, timeframe, time)
            );
        """)
        # Create hypertable if it doesn't exist (only if extension is active)
        try:
            await conn.execute("SELECT create_hypertable('ohlcv', 'time');")
        except asyncpg.exceptions.ObjectInUseError:
            pass
        except Exception as e:
            logger.debug(f"Skipped hypertable creation: {e}")
            pass # already a hypertable
        except Exception as e:
            logger.debug(f"Skipped hypertable creation for ohlcv: {e}")
            
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_sym_tf_time ON ohlcv (symbol, timeframe, time DESC);")

        # TABLE 2: SMC Events
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS smc_events (
                id              BIGSERIAL PRIMARY KEY,
                detected_at     TIMESTAMPTZ NOT NULL,
                bar_index_locked BIGINT NOT NULL,
                candle_close_ts TIMESTAMPTZ NOT NULL,
                symbol          TEXT NOT NULL,
                timeframe       TEXT NOT NULL,
                event_type      TEXT NOT NULL,
                price_low       DOUBLE PRECISION,
                price_high      DOUBLE PRECISION,
                strength        DOUBLE PRECISION,
                is_active       BOOLEAN DEFAULT TRUE,
                logic_version   TEXT NOT NULL
            );
        """)

        # TABLE 3: Signals
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id              BIGSERIAL PRIMARY KEY,
                created_at      TIMESTAMPTZ NOT NULL,
                symbol          TEXT NOT NULL,
                strategy        TEXT NOT NULL,
                direction       TEXT NOT NULL,
                status          TEXT NOT NULL,
                block_reason    TEXT,
                entry_price     DOUBLE PRECISION,
                sl_price        DOUBLE PRECISION,
                tp1_price       DOUBLE PRECISION,
                tp2_price       DOUBLE PRECISION,
                tp3_price       DOUBLE PRECISION,
                rr_ratio        DOUBLE PRECISION,
                rr_fee_adjusted DOUBLE PRECISION,
                v7_score_raw    DOUBLE PRECISION,
                v7_components   JSONB,
                mtf_score       DOUBLE PRECISION,
                regime          TEXT,
                breadth_pct     DOUBLE PRECISION,
                session_tag     TEXT,
                logic_version   TEXT NOT NULL,
                is_shadow       BOOLEAN DEFAULT TRUE,
                UNIQUE(id, created_at)
            );
        """)
        
        # APEX v11.0 schema migrations for signals table
        try:
            await conn.execute("ALTER TABLE signals ADD COLUMN IF NOT EXISTS gate_margin DOUBLE PRECISION;")
            await conn.execute("ALTER TABLE signals ADD COLUMN IF NOT EXISTS dynamic_gate DOUBLE PRECISION;")
            await conn.execute("ALTER TABLE signals ADD COLUMN IF NOT EXISTS size_multiplier DOUBLE PRECISION DEFAULT 1.0;")
            # mtf_score_weighted can be added if needed, but mtf_score is already there
        except Exception as e:
            logger.warning(f"Failed to add v11 columns to signals: {e}")

        try:
            await conn.execute("ALTER TABLE shadow_trades ADD COLUMN IF NOT EXISTS pnl_pct DOUBLE PRECISION;")
        except Exception as e:
            logger.warning(f"Failed to add pnl_pct column to shadow_trades: {e}")

        try:
            await conn.execute("SELECT create_hypertable('signals', 'created_at');")
        except asyncpg.exceptions.ObjectInUseError:
            pass
        except Exception as e:
            logger.debug(f"Skipped hypertable creation for signals: {e}")
            
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_sym_time ON signals (symbol, created_at DESC);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_status_time ON signals (status, created_at DESC);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_reg_str_stat ON signals (regime, strategy, status);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_sess_stat ON signals (session_tag, status);")
        
        # APEX v11.0: gate_calibration_log
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS gate_calibration_log (
                time TIMESTAMPTZ NOT NULL,
                v7_threshold DOUBLE PRECISION,
                p95_v7_100 DOUBLE PRECISION,
                p95_v7_500 DOUBLE PRECISION,
                signals_passed INT,
                signals_blocked INT
            );
        """)
        try:
            await conn.execute("SELECT create_hypertable('gate_calibration_log', 'time');")
        except Exception as e:
            logger.debug(f"Skipped hypertable creation for gate_calibration_log: {e}")


        # TABLE 4: Shadow Trades
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_trades (
                id              BIGSERIAL,
                signal_id       BIGINT,
                symbol          TEXT NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL,
                resolved_at     TIMESTAMPTZ,
                outcome         TEXT,
                mfe_pct         DOUBLE PRECISION,
                mae_pct         DOUBLE PRECISION,
                pnl_pct         DOUBLE PRECISION,
                bars_to_outcome INTEGER,
                session_tag     TEXT,
                regime_at_entry TEXT,
                logic_version   TEXT NOT NULL,
                PRIMARY KEY(id, created_at)
            );
        """)
        
        # TABLE 4b: Shadow Trades Blocked (For AI Auto-Tuner)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_trades_blocked (
                id              BIGSERIAL,
                signal_id       BIGINT,
                symbol          TEXT NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL,
                resolved_at     TIMESTAMPTZ,
                outcome         TEXT,
                mfe_pct         DOUBLE PRECISION,
                mae_pct         DOUBLE PRECISION,
                bars_to_outcome INTEGER,
                session_tag     TEXT,
                regime_at_entry TEXT,
                logic_version   TEXT NOT NULL,
                PRIMARY KEY(id, created_at)
            );
        """)
        
        try:
            await conn.execute("SELECT create_hypertable('shadow_trades', 'created_at');")
            await conn.execute("SELECT create_hypertable('shadow_trades_blocked', 'created_at');")
        except asyncpg.exceptions.ObjectInUseError:
            pass
        except Exception as e:
            logger.debug(f"Skipped hypertable creation: {e}")
            
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_st_signal ON shadow_trades (signal_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_st_outcome_time ON shadow_trades (outcome, created_at DESC);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_stb_signal ON shadow_trades_blocked (signal_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_stb_outcome_time ON shadow_trades_blocked (outcome, created_at DESC);")

        # TABLE 5: System Health
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS system_health (
                time            TIMESTAMPTZ NOT NULL,
                ws_latency_ms   DOUBLE PRECISION,
                symbols_healthy INTEGER,
                symbols_blocked INTEGER,
                cycle_duration_ms DOUBLE PRECISION,
                regime          TEXT,
                breadth_pct     DOUBLE PRECISION,
                active_signals  INTEGER,
                queue_size      INTEGER,
                UNIQUE (time)
            );
        """)
        try:
            await conn.execute("SELECT create_hypertable('system_health', 'time');")
        except asyncpg.exceptions.ObjectInUseError:
            pass
        except Exception as e:
            logger.debug(f"Skipped hypertable creation: {e}")
            pass

        # TABLE 6: Logic Versions
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS logic_versions (
                version         TEXT PRIMARY KEY,
                deployed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                changelog       JSONB,
                is_active       BOOLEAN DEFAULT TRUE,
                signals_count   INTEGER DEFAULT 0
            );
        """)

        await setup_missing_tables(conn)
        logger.info("PostgreSQL schema initialized successfully.")

async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed.")

import json
from datetime import datetime

async def insert_signal_record(signal_record_dict: dict) -> int:
    pool = await get_pool()
    query = """
        INSERT INTO signals (
            created_at, symbol, strategy, direction, status, block_reason,
            entry_price, sl_price, tp1_price, tp2_price, tp3_price,
            v7_score_raw, mtf_score, regime, session_tag, logic_version, is_shadow
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17
        ) RETURNING id;
    """
    async with pool.acquire() as conn:
        signal_id = await conn.fetchval(
            query,
            signal_record_dict.get('timestamp', datetime.utcnow()),
            signal_record_dict.get('symbol', 'UNKNOWN'),
            signal_record_dict.get('strategy', 'TREND'),
            signal_record_dict.get('direction', 'LONG'),
            signal_record_dict.get('status', 'ACCEPTED'),
            signal_record_dict.get('block_reason'),
            signal_record_dict.get('entry_price', 0.0),
            signal_record_dict.get('sl_price', 0.0),
            signal_record_dict.get('tp1_price', 0.0),
            signal_record_dict.get('tp2_price', 0.0),
            signal_record_dict.get('tp3_price', 0.0),
            signal_record_dict.get('v7_score_raw', 0.0),
            signal_record_dict.get('mtf_score', 0.0),
            signal_record_dict.get('regime', 'UNKNOWN'),
            signal_record_dict.get('session', 'UNKNOWN'),
            signal_record_dict.get('logic_version', '10.5.0'),
            True # is_shadow always true in v10.5 data collection
        )
        return signal_id

async def insert_shadow_trade(signal_id: int, symbol: str, session: str, regime: str, logic_version: str):
    pool = await get_pool()
    query = """
        INSERT INTO shadow_trades (
            signal_id, symbol, created_at, outcome, mfe_pct, mae_pct, session_tag, regime_at_entry, logic_version
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9
        );
    """
    async with pool.acquire() as conn:
        await conn.execute(
            query,
            signal_id,
            symbol,
            datetime.utcnow(),
            'OPEN', # Initial state
            0.0,
            0.0,
            session,
            regime,
            logic_version
        )

async def update_shadow_trade(signal_id: int, outcome: str, mfe_pct: float, mae_pct: float, bars: int):
    pool = await get_pool()
    query1 = """
        UPDATE shadow_trades
        SET outcome = $1, mfe_pct = $2, mae_pct = $3, bars_to_outcome = $4, resolved_at = $5
        WHERE signal_id = $6;
    """
    query2 = """
        UPDATE shadow_trades_blocked
        SET outcome = $1, mfe_pct = $2, mae_pct = $3, bars_to_outcome = $4, resolved_at = $5
        WHERE signal_id = $6;
    """
    async with pool.acquire() as conn:
        await conn.execute(query1, outcome, mfe_pct, mae_pct, bars, datetime.utcnow(), signal_id)
        await conn.execute(query2, outcome, mfe_pct, mae_pct, bars, datetime.utcnow(), signal_id)

async def update_signal_status(signal_id: int, status: str):
    pool = await get_pool()
    query = "UPDATE signals SET status = $1 WHERE id = $2"
    async with pool.acquire() as conn:
        await conn.execute(query, status, signal_id)

async def get_open_shadow_trades() -> list:
    pool = await get_pool()
    query = '''
        SELECT st.signal_id as id, st.symbol, st.created_at as opened_at, st.mfe_pct, st.mae_pct,
               s.entry_price, s.sl_price as stop_loss, s.tp1_price as take_profit_1, 
               s.tp2_price as take_profit_2, s.tp3_price as take_profit_3, s.direction, s.strategy, s.status, s.is_shadow
        FROM shadow_trades st
        JOIN signals s ON st.signal_id = s.id
        WHERE st.outcome = 'OPEN'
        UNION ALL
        SELECT st.signal_id as id, st.symbol, st.created_at as opened_at, st.mfe_pct, st.mae_pct,
               s.entry_price, s.sl_price as stop_loss, s.tp1_price as take_profit_1, 
               s.tp2_price as take_profit_2, s.tp3_price as take_profit_3, s.direction, s.strategy, s.status, s.is_shadow
        FROM shadow_trades_blocked st
        JOIN signals s ON st.signal_id = s.id
        WHERE st.outcome = 'OPEN';
    '''
    async with pool.acquire() as conn:
        records = await conn.fetch(query)
        return [dict(r) for r in records]

async def get_stats_timescale() -> dict:
    pool = await get_pool()
    query = """
        SELECT outcome, COUNT(*) as cnt, SUM(pnl_pct) as sum_pnl
        FROM shadow_trades 
        WHERE outcome != 'OPEN'
        GROUP BY outcome;
    """
    async with pool.acquire() as conn:
        records = await conn.fetch(query)
        
    stats = {
        'total': 0, 'won': 0, 'lost': 0, 'breakeven': 0,
        'small_win': 0, 'small_loss': 0, 'pnl_sum': 0.0, 'win_rate': 0.0
    }
    
    wins = ['WON', 'WON_BREAKEVEN']
    for r in records:
        out = r['outcome']
        cnt = r['cnt']
        stats['total'] += cnt
        
        if out in wins: stats['won'] += cnt
        elif out == 'LOST': stats['lost'] += cnt
        elif out == 'BREAKEVEN': stats['breakeven'] += cnt
        elif out == 'TIMEOUT_SMALL_WIN': stats['small_win'] += cnt
        elif out == 'TIMEOUT_SMALL_LOSS': stats['small_loss'] += cnt
        elif out == 'TIMEOUT': stats['breakeven'] += cnt # approximate
        
        # calculate true PnL using pnl_pct instead of MFE
        if r.get('sum_pnl'):
            stats['pnl_sum'] += r['sum_pnl']
            
    if stats['total'] > 0:
        stats['win_rate'] = (stats['won'] + stats['small_win']) / stats['total'] * 100
        
    return stats

async def update_signal_sl(signal_id: int, trail_sl: float):
    pool = await get_pool()
    query = "UPDATE signals SET sl_price = $1 WHERE id = $2;"
    async with pool.acquire() as conn:
        await conn.execute(query, trail_sl, signal_id)

async def insert_filter_block_record(symbol: str, strategy: str, block_reason: str, score: float = 0.0):
    pool = await get_pool()
    query = """
        INSERT INTO signals (
            created_at, symbol, strategy, direction, status, block_reason, v7_score_raw, logic_version, is_shadow
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9
        );
    """
    async with pool.acquire() as conn:
        await conn.execute(
            query,
            datetime.utcnow(),
            symbol,
            strategy,
            "LONG", # Defaulting direction
            "REJECTED_BY_FILTER",
            block_reason,
            score,
            "10.5.0",
            True
        )

async def is_on_cooldown(symbol: str, cooldown_hours: int = 4) -> bool:
    try:
        pool = await get_pool()
        query = """
            SELECT COUNT(*) FROM signals 
            WHERE symbol = $1 
            AND created_at >= NOW() - INTERVAL '1 hour' * $2
            AND status IN ('ACCEPTED', 'ENTERED')
        """
        async with pool.acquire() as conn:
            count = await conn.fetchval(query, symbol, cooldown_hours)
        return count > 0
    except Exception:
        return False

async def is_pullback_on_structure_cooldown(symbol: str) -> bool:
    try:
        pool = await get_pool()
        query = """
            SELECT COUNT(*) FROM smc_events 
            WHERE symbol = $1 
            AND detected_at >= NOW() - INTERVAL '2 hours'
            AND event_type = 'EXPIRED_STRUCTURE'
        """
        async with pool.acquire() as conn:
            count = await conn.fetchval(query, symbol)
        return count > 0
    except Exception:
        return False

async def create_shadow_trade(
    symbol: str,
    direction: str,
    strategy: str,
    entry_price: float,
    stop_loss: float,
    take_profit_1: float,
    take_profit_2: float,
    take_profit_3: float,
    v7_score: float,
    regime: str = "UNKNOWN",
    breadth: float = 0.0,
    cvd_score: float = 0.0,
    mtf_score: float = 0.0
):
    """Wrapper to maintain backwards compatibility with main.py shadow trades (APPROVED ONLY)"""
    try:
        signal_dict = {
            'symbol': symbol,
            'strategy': strategy,
            'direction': direction,
            'status': 'OPEN',
            'block_reason': 'None',
            'entry_price': entry_price,
            'sl_price': stop_loss,
            'tp1_price': take_profit_1,
            'tp2_price': take_profit_2,
            'tp3_price': take_profit_3,
            'v7_score_raw': v7_score,
            'mtf_score': mtf_score,
            'regime': regime,
            'is_shadow': False
        }
        signal_id = await insert_signal_record(signal_dict)
        if signal_id:
            await insert_shadow_trade(signal_id, symbol, "UNKNOWN", regime, "11.0.0")
    except Exception as e:
        logger.error(f"Failed to create shadow trade wrapper: {e}")

async def create_shadow_trade_blocked(
    symbol: str,
    direction: str,
    strategy: str,
    entry_price: float,
    stop_loss: float,
    take_profit_1: float,
    take_profit_2: float,
    take_profit_3: float,
    primary_block_reason: str,
    all_block_reasons: list,
    v7_score: float,
    regime: str = "UNKNOWN",
    breadth: float = 0.0,
    cvd_score: float = 0.0,
    mtf_score: float = 0.0
):
    """Saves blocked trades explicitly into the shadow_trades_blocked table for the Auto-Tuner"""
    try:
        signal_dict = {
            'symbol': symbol,
            'strategy': strategy,
            'direction': direction,
            'status': 'BLOCKED',
            'block_reason': primary_block_reason,
            'entry_price': entry_price,
            'sl_price': stop_loss,
            'tp1_price': take_profit_1,
            'tp2_price': take_profit_2,
            'tp3_price': take_profit_3,
            'v7_score_raw': v7_score,
            'mtf_score': mtf_score,
            'regime': regime,
            'is_shadow': True
        }
        signal_id = await insert_signal_record(signal_dict)
        if signal_id:
            await insert_shadow_trade_blocked(signal_id, symbol, "UNKNOWN", regime, "11.0.0")
    except Exception as e:
        logger.error(f"Failed to create shadow trade blocked wrapper: {e}")

async def insert_shadow_trade_blocked(signal_id: int, symbol: str, session: str, regime: str, logic_version: str):
    pool = await get_pool()
    query = """
        INSERT INTO shadow_trades_blocked (signal_id, symbol, created_at, outcome, session_tag, regime_at_entry, logic_version)
        VALUES ($1, $2, $3, 'OPEN', $4, $5, $6);
    """
    async with pool.acquire() as conn:
        await conn.execute(query, signal_id, symbol, datetime.utcnow(), session, regime, logic_version)



# ─── V10.5 Missing Tables ───────────────────────────────────────────────────────
async def setup_missing_tables(conn):
    logger.info("Initializing remaining legacy tables in PostgreSQL...")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS missed_signals (
            id SERIAL PRIMARY KEY,
            symbol TEXT,
            direction TEXT,
            score DOUBLE PRECISION,
            entry_price DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL,
            checked INTEGER DEFAULT 0,
            max_profit_pct DOUBLE PRECISION DEFAULT 0.0,
            max_drawdown_pct DOUBLE PRECISION DEFAULT 0.0,
            outcome TEXT
        );
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS filter_audit (
            id SERIAL PRIMARY KEY,
            symbol TEXT,
            direction TEXT,
            filter_name TEXT,
            price_at_block DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL,
            checked INTEGER DEFAULT 0,
            outcome_1h_pct DOUBLE PRECISION DEFAULT 0.0,
            outcome_4h_pct DOUBLE PRECISION DEFAULT 0.0,
            outcome_24h_pct DOUBLE PRECISION DEFAULT 0.0
        );
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS pullback_watchlist (
            id SERIAL PRIMARY KEY,
            symbol TEXT,
            direction TEXT,
            score DOUBLE PRECISION,
            original_entry DOUBLE PRECISION,
            swing_low DOUBLE PRECISION,
            limit_entries TEXT,
            stop_loss DOUBLE PRECISION,
            take_profit_1 DOUBLE PRECISION,
            take_profit_2 DOUBLE PRECISION,
            take_profit_3 DOUBLE PRECISION,
            position_usd DOUBLE PRECISION,
            ttl_expiry TIMESTAMPTZ,
            regime TEXT,
            status TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            original_breadth DOUBLE PRECISION,
            original_mtf DOUBLE PRECISION,
            original_cvd DOUBLE PRECISION,
            exchange_order_id TEXT
        );
    """)

# ─── New Functions to replace lite_db.py ───────────────────────────────────────

async def get_recent_features(limit: int = 20):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM signals ORDER BY created_at DESC LIMIT $1", limit)
        return rows

async def factory_reset_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE signals CASCADE")
        await conn.execute("TRUNCATE TABLE shadow_trades CASCADE")
    logger.warning("FACTORY RESET: All tables truncated. Total wipe complete.")

async def get_open_trades():
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM signals WHERE status IN ('OPEN', 'BREAKEVEN')")

async def save_pullback_item(
    symbol: str, direction: str, score: float, original_entry: float, swing_low: float,
    limit_entries: list, stop_loss: float, take_profit_1: float, take_profit_2: float, take_profit_3: float,
    position_usd: float, ttl_minutes: int, regime: str, status: str = 'WAITING',
    original_breadth: float = 50.0, original_mtf: float = 0.0, original_cvd: float = 0.0
):
    from datetime import timedelta, datetime
    expiry = datetime.utcnow() + timedelta(minutes=ttl_minutes)
    limit_json = json.dumps(limit_entries) if limit_entries else "[]"
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO pullback_watchlist (
                symbol, direction, score, original_entry, swing_low,
                limit_entries, stop_loss, take_profit_1, take_profit_2, take_profit_3,
                position_usd, ttl_expiry, regime, status, created_at,
                original_breadth, original_mtf, original_cvd
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
        ''', symbol, direction, score, original_entry, swing_low, limit_json, stop_loss,
             take_profit_1, take_profit_2, take_profit_3, position_usd, expiry, regime, status,
             datetime.utcnow(), original_breadth, original_mtf, original_cvd)

async def get_active_pullback_items():
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM pullback_watchlist WHERE status = 'WAITING' AND ttl_expiry > NOW()")

async def get_pullback_items_by_status(status: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM pullback_watchlist WHERE status = $1 AND ttl_expiry > NOW()", status)

async def update_pullback_status(item_id: int, new_status: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE pullback_watchlist SET status = $1 WHERE id = $2", new_status, item_id)

async def update_pullback_limit_entries(
    item_id: int, limit_entries: list, take_profit_1: float, take_profit_2: float,
    take_profit_3: float, new_status: str, exchange_order_id: str = None
):
    limit_json = json.dumps(limit_entries)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            UPDATE pullback_watchlist
            SET limit_entries = $1, take_profit_1 = $2, take_profit_2 = $3, take_profit_3 = $4, status = $5, exchange_order_id = $6
            WHERE id = $7
        ''', limit_json, take_profit_1, take_profit_2, take_profit_3, new_status, exchange_order_id, item_id)

async def get_tracking_shadow_trades():
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("""
            SELECT st.*, s.direction, s.entry_price, s.sl_price as stop_loss, s.tp1_price as take_profit_1, s.strategy 
            FROM shadow_trades st 
            JOIN signals s ON st.signal_id = s.id 
            WHERE st.outcome = 'OPEN'
        """)

async def update_shadow_trade_status(trade_id: int, outcome: str, mfe_pct: float, mae_pct: float, duration: int, pnl_pct: float = 0.0):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            UPDATE shadow_trades 
            SET outcome = $1, mfe_pct = $2, mae_pct = $3, bars_to_outcome = $4, pnl_pct = $5, resolved_at = NOW()
            WHERE id = $6
        ''', outcome, mfe_pct, mae_pct, duration, pnl_pct, trade_id)

async def get_unchecked_missed_signals():
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM missed_signals WHERE checked = 0 AND created_at <= NOW() - INTERVAL '2 hours'")

async def update_missed_signal_result(signal_id: int, pnl_pct: float, outcome: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE missed_signals SET checked = 1, max_profit_pct = $1, outcome = $2 WHERE id = $3", pnl_pct, outcome, signal_id)


async def get_trade_by_signal_id(signal_id: str) -> dict:
    # Since we dropped 'trades' and use 'signals', but signals primary key is 'id'
    # In legacy, 'signal_id' was a string like 'live_pb_123'. 
    # Let's map this string to 'session_tag' or similar, or just check 'pullback_watchlist' if it was already filled.
    # We will query 'signals' by checking if session_tag = signal_id
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM signals WHERE session_tag = $1 LIMIT 1", signal_id)
        return dict(row) if row else None

async def save_trade(
    signal_id: str, symbol: str, direction: str, entry_price: float, stop_loss: float,
    take_profit_1: float, position_usd: float, reasoning: str, strategy: str = "TREND",
    features_dict: dict = None, source: str = "MARKET", status: str = "OPEN"
):
    signal_dict = {
        "timestamp": datetime.utcnow(),
        "symbol": symbol,
        "strategy": strategy,
        "direction": direction,
        "status": status,
        "block_reason": reasoning,
        "entry_price": entry_price,
        "sl_price": stop_loss,
        "tp1_price": take_profit_1,
        "tp2_price": 0.0,
        "tp3_price": 0.0,
        "v7_score_raw": 0.0,
        "mtf_score": 0.0,
        "regime": "UNKNOWN",
        "session": signal_id, # Using session to store the string signal_id
        "logic_version": "10.5.0",
        "is_shadow": False
    }
    
    # Save to signals
    s_id = await insert_signal_record(signal_dict)
    
    # Save to shadow trades for MFE/MAE tracking
    await insert_shadow_trade(
        signal_id=s_id,
        symbol=symbol,
        session=signal_id,
        regime="UNKNOWN",
        logic_version="10.5.0"
    )

async def get_recent_trades(limit: int = 1000):
    pool = await get_pool()
    async with pool.acquire() as conn:
        records = await conn.fetch("SELECT * FROM signals WHERE status NOT IN ('WAITING', 'WAITING_STRUCTURE') ORDER BY created_at DESC LIMIT $1", limit)
        return [dict(r) for r in records]

async def reset_open_trades():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE signals SET status = 'CANCELLED' WHERE status IN ('OPEN', 'BREAKEVEN')")
        await conn.execute("UPDATE shadow_trades SET outcome = 'CANCELLED' WHERE outcome = 'OPEN'")
