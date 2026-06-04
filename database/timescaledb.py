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
            logger.info("TimescaleDB pool created successfully.")
        except Exception as e:
            logger.error(f"Failed to create TimescaleDB pool: {e}")
            raise
    return _pool

async def init_timescaledb():
    """Initializes the TimescaleDB schema as defined in APEX v10.5 upgrade."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        logger.info("Initializing TimescaleDB schema...")
        
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
        try:
            await conn.execute("SELECT create_hypertable('signals', 'created_at');")
        except asyncpg.exceptions.ObjectInUseError:
            pass
        except Exception as e:
            logger.debug(f"Skipped hypertable creation: {e}")
            pass # already a hypertable
        except Exception as e:
            logger.debug(f"Skipped hypertable creation for signals: {e}")
            
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_sym_time ON signals (symbol, created_at DESC);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_status_time ON signals (status, created_at DESC);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_reg_str_stat ON signals (regime, strategy, status);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_sess_stat ON signals (session_tag, status);")

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
                bars_to_outcome INTEGER,
                session_tag     TEXT,
                regime_at_entry TEXT,
                logic_version   TEXT NOT NULL,
                PRIMARY KEY(id, created_at)
            );
        """)
        try:
            await conn.execute("SELECT create_hypertable('shadow_trades', 'created_at');")
        except asyncpg.exceptions.ObjectInUseError:
            pass
        except Exception as e:
            logger.debug(f"Skipped hypertable creation: {e}")
            pass # already a hypertable
        except Exception as e:
            logger.debug(f"Skipped hypertable creation for shadow_trades: {e}")
            
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_st_signal ON shadow_trades (signal_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_st_outcome_time ON shadow_trades (outcome, created_at DESC);")

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

        logger.info("TimescaleDB schema initialized successfully.")

async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("TimescaleDB pool closed.")

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
    query = """
        UPDATE shadow_trades
        SET outcome = $1, mfe_pct = $2, mae_pct = $3, bars_to_outcome = $4, resolved_at = $5
        WHERE signal_id = $6;
    """
    async with pool.acquire() as conn:
        await conn.execute(query, outcome, mfe_pct, mae_pct, bars, datetime.utcnow(), signal_id)

async def get_open_shadow_trades() -> list:
    pool = await get_pool()
    query = '''
        SELECT st.signal_id as id, st.symbol, st.created_at as opened_at, st.mfe_pct, st.mae_pct,
               s.entry_price, s.sl_price as stop_loss, s.tp1_price as take_profit_1, 
               s.tp2_price as take_profit_2, s.tp3_price as take_profit_3, s.direction, s.strategy, s.status
        FROM shadow_trades st
        JOIN signals s ON st.signal_id = s.id
        WHERE st.outcome = 'OPEN';
    '''
    async with pool.acquire() as conn:
        records = await conn.fetch(query)
        return [dict(r) for r in records]

async def get_stats_timescale() -> dict:
    pool = await get_pool()
    query = """
        SELECT outcome, COUNT(*) as cnt, SUM(mfe_pct) as sum_mfe
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
        elif 'BREAKEVEN' in out: stats['breakeven'] += cnt
        elif out == 'TIMEOUT_SMALL_WIN': stats['small_win'] += cnt
        elif out == 'TIMEOUT_SMALL_LOSS': stats['small_loss'] += cnt
        elif out == 'TIMEOUT': stats['breakeven'] += cnt # approximate
        
        # approximate PnL sum using MFE for tracking purposes
        if r['sum_mfe']:
            stats['pnl_sum'] += r['sum_mfe']
            
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
    primary_block_reason: str,
    all_block_reasons: list,
    v7_score: float,
    regime: str = "UNKNOWN",
    breadth: float = 0.0,
    cvd_score: float = 0.0,
    mtf_score: float = 0.0
):
    """Wrapper to maintain backwards compatibility with main.py shadow trades"""
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
            'v7_score_raw': v7_score,
            'mtf_score': mtf_score,
            'regime': regime,
            'is_shadow': True
        }
        signal_id = await insert_signal_record(signal_dict)
        if signal_id:
            await insert_shadow_trade(signal_id, symbol, "UNKNOWN", regime, "v10.5")
    except Exception as e:
        logger.error(f"Failed to create shadow trade wrapper: {e}")
