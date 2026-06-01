"""
APEX Trading System v4.0
Configuration — pydantic-settings with full environment variable support.
All secrets loaded from environment / HashiCorp Vault in production.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExchangeConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXCHANGE_", env_file=".env", extra="ignore")

    # Binance
    binance_api_key: SecretStr = Field(default=SecretStr(""))
    binance_api_secret: SecretStr = Field(default=SecretStr(""))
    binance_testnet: bool = False

    # Bybit
    bybit_api_key: SecretStr = Field(default=SecretStr(""))
    bybit_api_secret: SecretStr = Field(default=SecretStr(""))
    bybit_testnet: bool = False

    # OKX
    okx_api_key: SecretStr = Field(default=SecretStr(""))
    okx_api_secret: SecretStr = Field(default=SecretStr(""))
    okx_passphrase: SecretStr = Field(default=SecretStr(""))
    okx_testnet: bool = False

    # HTX (Huobi)
    htx_api_key: SecretStr = Field(default=SecretStr(""))
    htx_api_secret: SecretStr = Field(default=SecretStr(""))


class DatabaseConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_", env_file=".env", extra="ignore")

    # TimescaleDB (PostgreSQL)
    timescale_host: str = "localhost"
    timescale_port: int = 5432
    timescale_name: str = "apex"
    timescale_user: str = "apex"
    timescale_password: SecretStr = Field(default=SecretStr("apex"))
    timescale_pool_size: int = 20
    timescale_max_overflow: int = 10

    @property
    def timescale_dsn(self) -> str:
        pw = self.timescale_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.timescale_user}:{pw}"
            f"@{self.timescale_host}:{self.timescale_port}/{self.timescale_name}"
        )

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: SecretStr = Field(default=SecretStr(""))
    redis_db: int = 0
    redis_max_connections: int = 50

    @property
    def redis_url(self) -> str:
        pw = self.redis_password.get_secret_value()
        auth = f":{pw}@" if pw else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ClickHouse
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 9000
    clickhouse_name: str = "apex"
    clickhouse_user: str = "default"
    clickhouse_password: SecretStr = Field(default=SecretStr(""))

    # MinIO (S3-compatible)
    minio_endpoint: str = "localhost:9000"
    minio_access_key: SecretStr = Field(default=SecretStr(""))
    minio_secret_key: SecretStr = Field(default=SecretStr(""))
    minio_bucket: str = "apex-models"
    minio_secure: bool = False


class AIConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_", extra="ignore")

    anthropic_api_key: SecretStr = Field(default=SecretStr(""))
    openai_api_key: SecretStr = Field(default=SecretStr(""))

    primary_model: str = "claude-sonnet-4-20250514"
    fallback_model: str = "gpt-4o"

    max_tokens: int = 2000
    temperature: float = 0.1          # low temperature for consistency (90/10 rule)
    timeout_seconds: int = 8          # audit must complete in < 8 seconds
    max_retries: int = 2


class DataSourceConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATASOURCE_", env_file=".env", extra="ignore")

    # On-chain
    glassnode_api_key: SecretStr = Field(default=SecretStr(""))
    cryptoquant_api_key: SecretStr = Field(default=SecretStr(""))
    coinglass_api_key: SecretStr = Field(default=SecretStr(""))
    defi_llama_api_key: SecretStr = Field(default=SecretStr(""))   # usually free
    deribit_api_key: SecretStr = Field(default=SecretStr(""))
    whale_alert_api_key: SecretStr = Field(default=SecretStr(""))  # NEW v4

    # Social (NEW v4)
    lunarcrush_api_key: SecretStr = Field(default=SecretStr(""))

    # Macro (NEW v4)
    fred_api_key: SecretStr = Field(default=SecretStr(""))

    # FX
    fixer_api_key: SecretStr = Field(default=SecretStr(""))
    exchangerate_api_key: SecretStr = Field(default=SecretStr(""))

    # P2P Official APIs
    bybit_p2p_api_key: SecretStr = Field(default=SecretStr(""))
    okx_p2p_api_key: SecretStr = Field(default=SecretStr(""))


class TradingConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRADING_", env_file=".env", extra="ignore")

    # LIVE TRADING GUARD
    live_trading_enabled: bool = False

    # Monitored symbols — Top 100 spot pairs by volume & narrative
    symbols: list[str] = [
        "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
        "TRX/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT",

        "TON/USDT", "DOT/USDT", "LTC/USDT", "BCH/USDT", "ETC/USDT",
        "ATOM/USDT", "FIL/USDT", "ICP/USDT", "NEAR/USDT", "APT/USDT",

        "SUI/USDT", "SEI/USDT", "INJ/USDT", "TIA/USDT", "KAS/USDT",
        "ALGO/USDT", "HBAR/USDT", "VET/USDT", "EGLD/USDT", "XTZ/USDT",

        "ARB/USDT", "OP/USDT", "STRK/USDT", "POL/USDT", "MANTA/USDT",
        "METIS/USDT", "ZK/USDT", "ZRO/USDT",

        "RENDER/USDT", "FET/USDT", "WLD/USDT", "TAO/USDT", "ARKM/USDT",
        "GRT/USDT", "IO/USDT", "AIOZ/USDT", "AKT/USDT", "ATH/USDT",

        "AAVE/USDT", "UNI/USDT", "LDO/USDT", "PENDLE/USDT", "GMX/USDT",
        "RUNE/USDT", "MKR/USDT", "SNX/USDT", "COMP/USDT", "CRV/USDT",
        "DYDX/USDT", "1INCH/USDT", "CAKE/USDT", "ENA/USDT", "ETHFI/USDT",

        "ONDO/USDT", "PYTH/USDT", "JUP/USDT", "JTO/USDT", "W/USDT",
        "AEVO/USDT", "ALT/USDT", "DYM/USDT", "OM/USDT", "QNT/USDT",

        "PEPE/USDT", "WIF/USDT", "BONK/USDT", "SHIB/USDT", "FLOKI/USDT",
        "BRETT/USDT", "MEW/USDT", "TURBO/USDT", "BOME/USDT", "NOT/USDT",

        "SAND/USDT", "MANA/USDT", "GALA/USDT", "IMX/USDT", "AXS/USDT",
        "APE/USDT", "PIXEL/USDT", "PORTAL/USDT", "BIGTIME/USDT", "GMT/USDT",

        "STX/USDT", "BLUR/USDT", "IOTX/USDT", "XLM/USDT", "XMR/USDT",
        "THETA/USDT", "KAVA/USDT", "FLOW/USDT", "CHZ/USDT", "ZIL/USDT"
    ]

    sector_leaders: dict[str, str] = {
        "DEFI": "UNI/USDT",
        "L2":   "ARB/USDT",
        "AI":   "FET/USDT",
        "MEME": "DOGE/USDT",
        "L1":   "SOL/USDT",
        "MAJORS": "BTC/USDT",
        "GAMING": "IMX/USDT",
        "RWA": "ONDO/USDT"
    }

    token_sectors: dict[str, str] = {
        # L2
        "ARB/USDT": "L2", "OP/USDT": "L2", "STRK/USDT": "L2", "POL/USDT": "L2", "MANTA/USDT": "L2", "METIS/USDT": "L2", "ZK/USDT": "L2", "ZRO/USDT": "L2",
        # DEFI
        "AAVE/USDT": "DEFI", "UNI/USDT": "DEFI", "LDO/USDT": "DEFI", "GMX/USDT": "DEFI", "RUNE/USDT": "DEFI", "PENDLE/USDT": "DEFI", 
        "MKR/USDT": "DEFI", "SNX/USDT": "DEFI", "COMP/USDT": "DEFI", "CRV/USDT": "DEFI", "DYDX/USDT": "DEFI", "1INCH/USDT": "DEFI", "CAKE/USDT": "DEFI", "ENA/USDT": "DEFI", "ETHFI/USDT": "DEFI", "JUP/USDT": "DEFI", "JTO/USDT": "DEFI",
        # AI
        "RENDER/USDT": "AI", "FET/USDT": "AI", "WLD/USDT": "AI", "TAO/USDT": "AI", "ARKM/USDT": "AI", "GRT/USDT": "AI", "IO/USDT": "AI", "AIOZ/USDT": "AI", "AKT/USDT": "AI", "ATH/USDT": "AI",
        # MEME
        "PEPE/USDT": "MEME", "WIF/USDT": "MEME", "BONK/USDT": "MEME", "SHIB/USDT": "MEME", "FLOKI/USDT": "MEME", "DOGE/USDT": "MEME", "BRETT/USDT": "MEME", "MEW/USDT": "MEME", "TURBO/USDT": "MEME", "BOME/USDT": "MEME", "NOT/USDT": "MEME",
        # L1
        "SOL/USDT": "L1", "SUI/USDT": "L1", "APT/USDT": "L1", "NEAR/USDT": "L1", "SEI/USDT": "L1", "INJ/USDT": "L1", "AVAX/USDT": "L1", "ADA/USDT": "L1", "TON/USDT": "L1", "TIA/USDT": "L1", "KAS/USDT": "L1", "ALGO/USDT": "L1", "HBAR/USDT": "L1", "VET/USDT": "L1", "EGLD/USDT": "L1", "XTZ/USDT": "L1", "ATOM/USDT": "L1", "FIL/USDT": "L1", "ICP/USDT": "L1",
        # RWA / Oracles / Restaking
        "ONDO/USDT": "RWA", "PYTH/USDT": "RWA", "W/USDT": "RWA", "AEVO/USDT": "RWA", "ALT/USDT": "RWA", "DYM/USDT": "RWA", "OM/USDT": "RWA", "QNT/USDT": "RWA",
        # GAMING
        "SAND/USDT": "GAMING", "MANA/USDT": "GAMING", "GALA/USDT": "GAMING", "IMX/USDT": "GAMING", "AXS/USDT": "GAMING", "APE/USDT": "GAMING", "PIXEL/USDT": "GAMING", "PORTAL/USDT": "GAMING", "BIGTIME/USDT": "GAMING", "GMT/USDT": "GAMING",
        # Others
        "STX/USDT": "L2", "BLUR/USDT": "GAMING", "IOTX/USDT": "L1", "XLM/USDT": "L1", "XMR/USDT": "L1", "THETA/USDT": "L1", "KAVA/USDT": "DEFI", "FLOW/USDT": "L1", "CHZ/USDT": "GAMING", "ZIL/USDT": "L1"
    }

    # Extended list cleared out since we consolidated to 100 in the main list
    symbols_extended: list[str] = []

    # Timeframes
    timeframes: list[str] = ["1d", "4h", "1h", "15m", "5m"]

    # Risk parameters
    initial_deposit_usd: float = 3_000.0
    risk_per_trade_pct: float = 1.0          # 1% = $30 per trade
    min_score_for_signal: float = 50.0       # Minimum confluence score (out of 100)
    min_risk_pct: float = 0.5
    max_risk_pct: float = 1.0
    half_kelly: bool = True
    paper_trading_mode: bool = True

    # Circuit breakers
    daily_loss_stop_pct: float = 30.0
    drawdown_stop_pct: float = 50.0
    max_open_positions: int = 20
    max_daily_signals: int = 50
    max_position_pct: float = 5.0
    btc_crash_stop_pct: float = -10.0
    consecutive_losses_stop: int = 4
    var_portfolio_stop_pct: float = 8.0
    ml_model_max_age_days: int = 45
    calibration_drift_stop_pct: float = 15.0

    # Regime-specific thresholds
    bull_confluence_min: float = 65.0    # v4: weighted score
    bull_signals_max: int = 5
    bull_risk_max_pct: float = 1.5

    sideways_confluence_min: float = 80.0
    sideways_signals_max: int = 3
    sideways_risk_max_pct: float = 0.75

    bear_confluence_min: float = 95.0
    bear_signals_max: int = 2
    bear_risk_max_pct: float = 0.5

    # Signal validation
    min_rr_ratio: float = 1.5
    sl_buffer_pct: float = 0.3
    signal_valid_hours: int = 4

    # P2P settings
    p2p_min_margin_pct: float = 1.5
    p2p_min_counterparty_rating: float = 95.0
    p2p_min_counterparty_trades: int = 100
    p2p_min_account_age_days: int = 30
    p2p_max_single_trade_usd: float = 5000.0
    p2p_cache_ttl_seconds: int = 30

    # Market making
    mm_peak_spread_pct: float = 1.5
    mm_offpeak_spread_pct: float = 3.0
    mm_blackout_start: int = 2    # 02:00 MSK
    mm_blackout_end: int = 8      # 08:00 MSK


class MLConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ML_", extra="ignore")

    # Regime classifier
    retrain_interval_days: int = 30
    retrain_window_months: int = 12
    hmm_weight: float = 0.4
    xgb_weight: float = 0.6
    min_confidence_for_ml: float = 0.65

    # Drift detector (NEW v4)
    drift_check_interval_hours: int = 1
    drift_alert_threshold_hours: int = 24    # alert after 24h disagreement
    drift_retrain_threshold_hours: int = 72  # force retrain after 72h

    # Feature Store calibration
    calibration_min_samples: int = 100
    calibration_window_months: int = 6
    calibration_recalibrate_days: int = 30

    # Dynamic confluence weights (NEW v4)
    weight_training_min_samples: int = 50
    weight_training_window_months: int = 6
    weight_training_interval_days: int = 30

    # SHAP (NEW v4)
    shap_background_samples: int = 100


class AlertConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALERT_", env_file=".env", extra="ignore")

    telegram_bot_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("ALERTS_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "ALERT_TELEGRAM_BOT_TOKEN")
    )
    telegram_chat_id: str = Field(
        default="",
        validation_alias=AliasChoices("ALERTS_TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID", "ALERT_TELEGRAM_CHAT_ID")
    )
    telegram_error_chat_id: str = ""   # separate chat for errors

    discord_webhook_url: SecretStr = Field(default=SecretStr(""))

    sentry_dsn: SecretStr = Field(default=SecretStr(""))

    # Alert thresholds
    drawdown_critical_pct: float = 10.0
    var_critical_pct: float = 8.0
    daily_loss_warning_pct: float = 1.5
    ai_rejection_rate_warning_pct: float = 50.0
    calibration_drift_critical_pct: float = 15.0
    shadow_pnl_delta_warning_pct: float = -5.0


class AppConfig(BaseSettings):
    """Master config — aggregates all sub-configs"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"

    # Service ports
    api_port: int = 8000
    metrics_port: int = 9090

    # Sub-configs (loaded from environment prefixes)
    exchanges: ExchangeConfig = Field(default_factory=ExchangeConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    data_sources: DataSourceConfig = Field(default_factory=DataSourceConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    ml: MLConfig = Field(default_factory=MLConfig)
    alerts: AlertConfig = Field(default_factory=AlertConfig)

    # Vault (production)
    vault_url: str = "http://localhost:8200"
    vault_token: SecretStr = Field(default=SecretStr(""))
    use_vault: bool = False

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Singleton config instance — call this everywhere."""
    return AppConfig()
