"""
APEX Trading System v4.0
Database Layer — TimescaleDB (asyncpg) + Redis + ClickHouse connections.
All async, pooled, production-grade.
On Railway/lightweight deployments, falls back to SQLite via lite_db.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any

# Optional heavy DB drivers — graceful fallback if not installed
try:
    import asyncpg
    _HAS_ASYNCPG = True
except ImportError:
    asyncpg = None  # type: ignore
    _HAS_ASYNCPG = False

try:
    import redis.asyncio as aioredis
    _HAS_REDIS = True
except ImportError:
    aioredis = None  # type: ignore
    _HAS_REDIS = False

try:
    from clickhouse_driver import Client as ClickHouseClient
    _HAS_CLICKHOUSE = True
except ImportError:
    ClickHouseClient = None  # type: ignore
    _HAS_CLICKHOUSE = False

from shared.config import get_config

logger = logging.getLogger(__name__)

_config = get_config()

# ─────────────────────────────────────────────────────────────────────────────
# TIMESCALEDB (PostgreSQL)
# ─────────────────────────────────────────────────────────────────────────────

_pg_pool: asyncpg.Pool | None = None


async def init_timescaledb() -> asyncpg.Pool:
    """Initialize TimescaleDB connection pool."""
    global _pg_pool
    cfg = _config.database

    _pg_pool = await asyncpg.create_pool(
        host=cfg.timescale_host,
        port=cfg.timescale_port,
        database=cfg.timescale_name,
        user=cfg.timescale_user,
        password=cfg.timescale_password.get_secret_value(),
        min_size=5,
        max_size=cfg.timescale_pool_size,
        command_timeout=30,
        server_settings={
            "jit": "off",  # disable JIT for OLTP workloads
            "application_name": "apex-trading-v4",
        },
    )

    # Verify TimescaleDB extension
    async with _pg_pool.acquire() as conn:
        result = await conn.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
        )
        if result:
            logger.info(f"TimescaleDB connected — version {result}")
        else:
            logger.warning("TimescaleDB extension not found — running as plain PostgreSQL")

    return _pg_pool


async def close_timescaledb() -> None:
    global _pg_pool
    if _pg_pool:
        await _pg_pool.close()
        _pg_pool = None
        logger.info("TimescaleDB pool closed")


@asynccontextmanager
async def get_pg_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """Acquire a connection from the pool."""
    if _pg_pool is None:
        raise RuntimeError("TimescaleDB pool not initialized. Call init_timescaledb() first.")
    async with _pg_pool.acquire() as conn:
        yield conn


async def execute_pg(query: str, *args: Any) -> str:
    """Execute a write query."""
    async with get_pg_conn() as conn:
        return await conn.execute(query, *args)


async def fetch_pg(query: str, *args: Any) -> list[asyncpg.Record]:
    """Fetch multiple rows."""
    async with get_pg_conn() as conn:
        return await conn.fetch(query, *args)


async def fetchrow_pg(query: str, *args: Any) -> asyncpg.Record | None:
    """Fetch single row."""
    async with get_pg_conn() as conn:
        return await conn.fetchrow(query, *args)


async def fetchval_pg(query: str, *args: Any) -> Any:
    """Fetch single value."""
    async with get_pg_conn() as conn:
        return await conn.fetchval(query, *args)


# ─────────────────────────────────────────────────────────────────────────────
# REDIS
# ─────────────────────────────────────────────────────────────────────────────

_redis_client: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    """Initialize Redis connection."""
    global _redis_client
    cfg = _config.database

    _redis_client = await aioredis.from_url(
        cfg.redis_url,
        encoding="utf-8",
        decode_responses=True,
        max_connections=cfg.redis_max_connections,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
    )

    # Verify connection
    pong = await _redis_client.ping()
    if pong:
        logger.info("Redis connected")
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
        logger.info("Redis connection closed")


def get_redis() -> "aioredis.Redis | None":
    """Get Redis client instance. Returns None if Redis not initialized (graceful fallback)."""
    if _redis_client is None:
        return None
    return _redis_client


class RedisKeys:
    """Centralized Redis key definitions — prevents typos."""

    # Live prices (TTL: 5s)
    @staticmethod
    def ticker(exchange: str, symbol: str) -> str:
        return f"apex:ticker:{exchange}:{symbol}"

    # Orderbook snapshots (TTL: 3s)
    @staticmethod
    def orderbook(exchange: str, symbol: str) -> str:
        return f"apex:orderbook:{exchange}:{symbol}"

    # P2P orderbook cache (TTL: 30s)
    @staticmethod
    def p2p_cache(exchange: str, pair: str) -> str:
        return f"apex:p2p:{exchange}:{pair}"

    # Feature cache (TTL: 60s)
    @staticmethod
    def features(symbol: str, timeframe: str) -> str:
        return f"apex:features:{symbol}:{timeframe}"

    # Regime prediction (TTL: 3600s)
    @staticmethod
    def regime_prediction() -> str:
        return "apex:regime:current"

    # Dynamic confluence weights (TTL: 86400s = 1 day)
    @staticmethod
    def confluence_weights(regime: str) -> str:
        return f"apex:weights:confluence:{regime}"

    # Risk status (TTL: 300s)
    @staticmethod
    def risk_status() -> str:
        return "apex:risk:status"

    # Daily stats (TTL: 86400s)
    @staticmethod
    def daily_stats(date_str: str) -> str:
        return f"apex:stats:daily:{date_str}"

    # OFI data (TTL: 60s)
    @staticmethod
    def ofi(symbol: str) -> str:
        return f"apex:ofi:{symbol}"

    # Social data (TTL: 900s = 15min)
    @staticmethod
    def social(symbol: str) -> str:
        return f"apex:social:{symbol}"

    # Macro data (TTL: 3600s = 1h)
    @staticmethod
    def macro() -> str:
        return "apex:macro:current"

    # Temporal bias (TTL: 3600s)
    @staticmethod
    def temporal_bias(symbol: str) -> str:
        return f"apex:temporal:{symbol}"

    # Execution params from Learning Loop (TTL: 86400s)
    @staticmethod
    def execution_params(symbol: str, regime: str) -> str:
        return f"apex:execution:{symbol}:{regime}"

    # Drift detector state (TTL: 7200s)
    @staticmethod
    def drift_state() -> str:
        return "apex:drift:state"

    # Alert deduplication (TTL: custom)
    @staticmethod
    def alert_dedup(alert_type: str) -> str:
        return f"apex:alert:dedup:{alert_type}"

    # Shadow mode session (TTL: 1209600s = 14 days)
    @staticmethod
    def shadow_session(session_id: int) -> str:
        return f"apex:shadow:{session_id}"


# ─────────────────────────────────────────────────────────────────────────────
# CLICKHOUSE (OLAP — Feature Store, Analytics, Backtesting)
# ─────────────────────────────────────────────────────────────────────────────

_ch_client: ClickHouseClient | None = None


def init_clickhouse() -> ClickHouseClient:
    """Initialize ClickHouse client (synchronous — used in sync contexts)."""
    global _ch_client
    cfg = _config.database

    _ch_client = ClickHouseClient(
        host=cfg.clickhouse_host,
        port=cfg.clickhouse_port,
        database=cfg.clickhouse_name,
        user=cfg.clickhouse_user,
        password=cfg.clickhouse_password.get_secret_value(),
        settings={
            "use_numpy": True,
            "max_block_size": 65536,
        },
    )

    # Verify connection
    version = _ch_client.execute("SELECT version()")[0][0]
    logger.info(f"ClickHouse connected — version {version}")
    return _ch_client


def get_clickhouse() -> ClickHouseClient:
    """Get ClickHouse client instance."""
    if _ch_client is None:
        raise RuntimeError("ClickHouse not initialized. Call init_clickhouse() first.")
    return _ch_client


async def execute_ch_async(query: str, params: dict | None = None) -> list:
    """Execute ClickHouse query in async context (runs in thread pool)."""
    loop = asyncio.get_event_loop()
    ch = get_clickhouse()
    return await loop.run_in_executor(None, lambda: ch.execute(query, params or {}))


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE INITIALIZATION (TimescaleDB schema setup)
# ─────────────────────────────────────────────────────────────────────────────

TIMESCALEDB_SCHEMA = """
-- Enable TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- OHLCV data (hypertable)
CREATE TABLE IF NOT EXISTS ohlcv_data (
    timestamp       TIMESTAMPTZ NOT NULL,
    exchange        VARCHAR(20) NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    timeframe       VARCHAR(5) NOT NULL,
    open            DECIMAL(20,8) NOT NULL,
    high            DECIMAL(20,8) NOT NULL,
    low             DECIMAL(20,8) NOT NULL,
    close           DECIMAL(20,8) NOT NULL,
    volume          DECIMAL(20,8) NOT NULL
);

SELECT create_hypertable('ohlcv_data', 'timestamp', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol ON ohlcv_data (exchange, symbol, timeframe, timestamp DESC);

-- Trade ticks (hypertable)
CREATE TABLE IF NOT EXISTS trade_ticks (
    timestamp       TIMESTAMPTZ NOT NULL,
    exchange        VARCHAR(20) NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    price           DECIMAL(20,8) NOT NULL,
    size            DECIMAL(20,8) NOT NULL,
    side            VARCHAR(4) NOT NULL,
    is_buyer_maker  BOOLEAN NOT NULL
);

SELECT create_hypertable('trade_ticks', 'timestamp', if_not_exists => TRUE);

-- Signals table
CREATE TABLE IF NOT EXISTS signals (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol              VARCHAR(20) NOT NULL,
    direction           VARCHAR(10) NOT NULL,
    entry_low           DECIMAL(20,8),
    entry_high          DECIMAL(20,8),
    stop_loss           DECIMAL(20,8),
    tp1                 DECIMAL(20,8),
    tp2                 DECIMAL(20,8),
    tp3                 DECIMAL(20,8),
    confluence_score    DECIMAL(6,2),
    confluence_weighted BOOLEAN DEFAULT TRUE,
    confluence_json     JSONB,
    mtf_score           DECIMAL(4,2),
    market_regime       VARCHAR(20),
    regime_confidence   DECIMAL(4,2),
    volatility_regime   VARCHAR(20),
    fear_greed          INT,
    news_sentiment      DECIMAL(4,2),
    ofi_score           DECIMAL(4,2),
    liquidation_status  VARCHAR(30),
    calibrated_winrate  DECIMAL(5,2),
    kelly_size_pct      DECIMAL(5,2),
    slippage_estimate   DECIMAL(6,4),
    anomaly_flags       JSONB,
    adversarial_score   DECIMAL(4,2),
    adversarial_risk    VARCHAR(20),
    divergence_strength VARCHAR(30),
    macro_bias          VARCHAR(30),
    temporal_bias_score DECIMAL(4,2),
    ai_approved         BOOLEAN,
    ai_confidence       DECIMAL(4,2),
    ai_audit_json       JSONB,
    ai_param_adjustments JSONB,
    executed            BOOLEAN DEFAULT FALSE,
    shadow_session_id   INT
);

-- Trades table
CREATE TABLE IF NOT EXISTS trades (
    id                          BIGSERIAL PRIMARY KEY,
    signal_id                   BIGINT REFERENCES signals(id),
    opened_at                   TIMESTAMPTZ NOT NULL,
    closed_at                   TIMESTAMPTZ,
    symbol                      VARCHAR(20) NOT NULL,
    direction                   VARCHAR(10) NOT NULL,
    entry_price                 DECIMAL(20,8),
    exit_price                  DECIMAL(20,8),
    position_size               DECIMAL(20,8),
    stop_loss                   DECIMAL(20,8),
    take_profits                JSONB,
    pnl_usd                     DECIMAL(12,4),
    pnl_pct                     DECIMAL(8,4),
    close_reason                VARCHAR(50),
    slippage_pct                DECIMAL(8,4),
    fill_quality                VARCHAR(20),
    fees_usd                    DECIMAL(12,4),
    max_adverse_excursion       DECIMAL(8,4),
    max_favorable_excursion     DECIMAL(8,4)
);

-- P2P operations
CREATE TABLE IF NOT EXISTS p2p_operations (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    operation_type  VARCHAR(20),
    buy_exchange    VARCHAR(20),
    sell_exchange   VARCHAR(20),
    buy_price       DECIMAL(12,4),
    sell_price      DECIMAL(12,4),
    volume_usdt     DECIMAL(12,2),
    gross_margin    DECIMAL(6,4),
    net_margin      DECIMAL(6,4),
    adjusted_margin DECIMAL(6,4),
    profit_rub      DECIMAL(12,2),
    execution_min   INT,
    status          VARCHAR(20),
    data_source     VARCHAR(20),
    fx_slippage_pct DECIMAL(6,4)
);

-- Execution history (NEW v4)
CREATE TABLE IF NOT EXISTS execution_history (
    id                      BIGSERIAL PRIMARY KEY,
    signal_id               BIGINT REFERENCES signals(id),
    executed_at             TIMESTAMPTZ NOT NULL,
    exchange                VARCHAR(20),
    execution_type          VARCHAR(20),
    planned_entry           DECIMAL(20,8),
    actual_entry            DECIMAL(20,8),
    slippage_pct            DECIMAL(8,4),
    fill_quality            VARCHAR(20),
    ofi_at_entry            DECIMAL(4,2),
    regime                  VARCHAR(20),
    time_of_day_utc         SMALLINT,
    recommendation_used     VARCHAR(20)
);

-- Shadow sessions
CREATE TABLE IF NOT EXISTS shadow_sessions (
    id              BIGSERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    version         VARCHAR(50),
    status          VARCHAR(20),
    comparison_json JSONB
);

-- On-chain snapshots (hypertable)
CREATE TABLE IF NOT EXISTS onchain_snapshots (
    timestamp           TIMESTAMPTZ NOT NULL,
    symbol              VARCHAR(20) NOT NULL,
    exchange_inflow_btc DECIMAL(12,2),
    exchange_outflow_btc DECIMAL(12,2),
    sopr                DECIMAL(8,6),
    mvrv_z_score        DECIMAL(6,2),
    funding_rate_pct    DECIMAL(8,4),
    open_interest_usd   DECIMAL(20,2),
    liquidations_1h_usd DECIMAL(20,2)
);

SELECT create_hypertable('onchain_snapshots', 'timestamp', if_not_exists => TRUE);

-- Social snapshots (NEW v4)
CREATE TABLE IF NOT EXISTS social_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    captured_at         TIMESTAMPTZ NOT NULL,
    symbol              VARCHAR(20),
    fear_greed          SMALLINT,
    lunarcrush_score    DECIMAL(6,2),
    social_volume       INTEGER,
    crowd_score         DECIMAL(5,2),
    smart_money_score   DECIMAL(5,2),
    divergence_type     VARCHAR(30),
    divergence_strength VARCHAR(20)
);

-- Macro snapshots (NEW v4)
CREATE TABLE IF NOT EXISTS macro_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    captured_at     TIMESTAMPTZ NOT NULL,
    dxy_value       DECIMAL(8,4),
    dxy_1h_change   DECIMAL(6,4),
    gold_value      DECIMAL(10,2),
    gold_1h_change  DECIMAL(6,4),
    btc_dominance   DECIMAL(5,2),
    macro_bias      VARCHAR(30)
);

-- Temporal bias history (NEW v4)
CREATE TABLE IF NOT EXISTS temporal_bias_history (
    id              BIGSERIAL PRIMARY KEY,
    signal_id       BIGINT,
    captured_at     TIMESTAMPTZ NOT NULL,
    day_of_week     SMALLINT,
    temporal_score  DECIMAL(4,2),
    fomc_hours      DECIMAL(8,2),
    halving_days    INTEGER,
    outcome         VARCHAR(10)
);
"""

CLICKHOUSE_SCHEMA = """
-- Feature Store (main analytics table)
CREATE TABLE IF NOT EXISTS feature_store_signals (
    signal_id           UInt64,
    created_at          DateTime,
    symbol              String,
    direction           String,
    confluence_score    Float32,
    mtf_score           Float32,
    regime              String,
    volatility_regime   String,
    funding_rate        Float32,
    fear_greed          UInt8,
    oi_change_pct       Float32,
    ofi_score           Float32,
    news_sentiment      Float32,
    onchain_direction   String,
    copy_trader_signal  String,
    liquidation_status  String,
    adversarial_score   Float32,
    divergence_strength String,
    macro_bias          String,
    temporal_bias_score Float32,
    -- outcome (filled after trade closes)
    outcome             Nullable(String),
    pnl_pct             Nullable(Float32),
    mae                 Nullable(Float32),
    mfe                 Nullable(Float32),
    time_to_close_hours Nullable(Float32)
) ENGINE = MergeTree()
ORDER BY (created_at, signal_id);

-- Execution history analytics (NEW v4)
CREATE TABLE IF NOT EXISTS execution_analytics (
    execution_id        UInt64,
    signal_id           UInt64,
    executed_at         DateTime,
    exchange            String,
    execution_type      String,
    slippage_pct        Float32,
    fill_quality        String,
    ofi_at_entry        Float32,
    regime              String,
    time_of_day         UInt8
) ENGINE = MergeTree()
ORDER BY (executed_at, signal_id);

-- Day-of-week returns for temporal analysis
CREATE TABLE IF NOT EXISTS temporal_returns (
    date            Date,
    symbol          String,
    weekday         UInt8,
    daily_return_pct Float32
) ENGINE = MergeTree()
ORDER BY (symbol, date);
"""


async def initialize_all_databases() -> None:
    """
    Full database initialization sequence.
    Call on application startup.
    """
    logger.info("Initializing all database connections...")

    # TimescaleDB
    await init_timescaledb()
    async with get_pg_conn() as conn:
        # Execute schema in statements
        statements = [s.strip() for s in TIMESCALEDB_SCHEMA.split(";") if s.strip()]
        for stmt in statements:
            try:
                await conn.execute(stmt)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.error(f"Schema error: {e}\nStatement: {stmt[:100]}")

    logger.info("TimescaleDB schema initialized")

    # Redis
    await init_redis()

    # ClickHouse
    try:
        init_clickhouse()
        ch = get_clickhouse()
        statements = [s.strip() for s in CLICKHOUSE_SCHEMA.split(";") if s.strip()]
        for stmt in statements:
            try:
                ch.execute(stmt)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.error(f"ClickHouse schema error: {e}")
        logger.info("ClickHouse schema initialized")
    except Exception as e:
        logger.warning(f"ClickHouse not available: {e} — analytics will be disabled")

    logger.info("All databases initialized successfully")


async def close_all_databases() -> None:
    """Close all database connections — call on shutdown."""
    await close_timescaledb()
    await close_redis()
    logger.info("All database connections closed")
