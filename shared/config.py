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

    # Monitored symbols — Top 60 spot pairs by volume & narrative
    symbols: list[str] = [
        # Majors
        "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
        # Large Caps
        "AVAX/USDT", "ADA/USDT", "DOT/USDT", "POL/USDT", "LINK/USDT",
        # DeFi
        "UNI/USDT", "AAVE/USDT", "ARB/USDT", "OP/USDT", "INJ/USDT",
        # L1/L2 Trending
        "TON/USDT", "SUI/USDT", "APT/USDT", "NEAR/USDT", "SEI/USDT",
        # AI & Data
        "FET/USDT", "RENDER/USDT", "AR/USDT", "WLD/USDT", "TAO/USDT",
        # Memes
        "DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "WIF/USDT", "BONK/USDT",
        # DeFi 2.0 & DEX
        "PENDLE/USDT", "GMX/USDT", "LDO/USDT", "RUNE/USDT", "JUP/USDT",
        # New Narratives
        "STX/USDT", "TIA/USDT", "STRK/USDT", "BLUR/USDT", "MANTA/USDT",
        # Real World Assets (RWA) & Infra (NEW)
        "ONDO/USDT", "LTC/USDT", "PYTH/USDT", "ARKM/USDT", "GRT/USDT",
        # Gaming & Metaverse (NEW)
        "IMX/USDT", "GALA/USDT", "SAND/USDT", "BEAMX/USDT", "MANA/USDT",
        # Fast L1s & Classics (NEW)
        "KAS/USDT", "XLM/USDT", "MNT/USDT", "ATOM/USDT", "ICP/USDT",
        # DePIN & Storage (NEW)
        "FIL/USDT", "HNT/USDT", "IOTX/USDT", "VET/USDT", "ETC/USDT",
    ]

    # Timeframes
    timeframes: list[str] = ["1d", "4h", "1h", "15m", "5m"]

    # Risk parameters
    initial_deposit_usd: float = 3_000.0
    risk_per_trade_pct: float = 1.0          # 1% = $30 per trade
    min_score_for_signal: float = 60.0       # Minimum confluence score (out of 100)
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
