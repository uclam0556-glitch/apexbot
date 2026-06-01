"""
APEX Trading System v4.0
Shared Pydantic v2 Models — Single Source of Truth for all data types.

All inter-service communication uses these schemas.
Backend computes. Features calibrate. ML classifies.
Adversarial tests. AI audits & modifies. Execution learns.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional, List, Dict, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────────────────────

class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderExecutionType(str, Enum):
    MARKET = "MARKET"
    TWAP = "TWAP"
    LIMIT_FORCED = "LIMIT_FORCED"


class ExecutionMetrics(BaseModel):
    order_id: str
    symbol: str
    execution_type: OrderExecutionType
    intended_price: float
    avg_fill_price: float
    slippage_bps: float
    total_fees_usd: float
    fill_rate_pct: float
    execution_time_ms: int
    orderbook_depth_usd: float


class ExecutionRoute(BaseModel):
    execution_type: OrderExecutionType
    reasoning: str
    expected_slippage_bps: float
    recommended_chunks: int


class MarketRegime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"
    CRISIS = "CRISIS"


class VolatilityRegime(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRISIS = "CRISIS"


class LiquidationStatus(str, Enum):
    NORMAL = "NORMAL"
    CASCADE_IN_PROGRESS = "CASCADE_IN_PROGRESS"
    POST_CASCADE_REVERSAL = "POST_CASCADE_REVERSAL"
    ELEVATED = "ELEVATED"


class OpportunityGrade(str, Enum):
    PREMIUM = "PREMIUM"
    GOOD = "GOOD"
    WEAK = "WEAK"
    SKIP = "SKIP"


class ApprovalType(str, Enum):
    FULL = "FULL"
    CAUTIOUS = "CAUTIOUS"
    REJECTED = "REJECTED"


class ExecutionType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    TWAP = "TWAP"
    LIMIT_FORCED = "LIMIT_FORCED"  # AI-forced limit (e.g. spoofing detected)


class FillQuality(str, Enum):
    GOOD = "GOOD"
    NEUTRAL = "NEUTRAL"
    POOR = "POOR"


class AuditCheckResult(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class DivergenceType(str, Enum):
    SMART_MONEY_VS_CROWD = "SMART_MONEY_VS_CROWD"
    CROWD_VS_SMART_MONEY = "CROWD_VS_SMART_MONEY"
    NEUTRAL = "NEUTRAL"


class DivergenceStrength(str, Enum):
    STRONG_BULL = "STRONG_BULL"
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"
    STRONG_BEAR = "STRONG_BEAR"


class MacroBias(str, Enum):
    STRONG_BULLISH = "STRONG_BULLISH"
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
    STRONG_BEARISH = "STRONG_BEARISH"


class TemporalBiasLabel(str, Enum):
    STRONG_POSITIVE = "STRONG_POSITIVE"
    MILD_POSITIVE = "MILD_POSITIVE"
    NEUTRAL = "NEUTRAL"
    MILD_NEGATIVE = "MILD_NEGATIVE"
    STRONG_NEGATIVE = "STRONG_NEGATIVE"
    CAUTION = "CAUTION"


class AdversarialRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class AnomалySeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


# ─────────────────────────────────────────────────────────────────────────────
# MARKET DATA PRIMITIVES
# ─────────────────────────────────────────────────────────────────────────────

class TickerData(BaseModel):
    exchange: str
    symbol: str
    bid: float
    ask: float
    last: float
    volume_24h: float
    timestamp: datetime


class OHLCV(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class OrderBookLevel(BaseModel):
    price: float
    size: float


class OrderBook(BaseModel):
    exchange: str
    symbol: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime

    @property
    def mid_price(self) -> float:
        return (self.bids[0].price + self.asks[0].price) / 2 if self.bids and self.asks else 0.0

    @property
    def spread_pct(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return (self.asks[0].price - self.bids[0].price) / self.bids[0].price * 100


class Trade(BaseModel):
    exchange: str
    symbol: str
    price: float
    size: float
    side: str  # "buy" | "sell"
    timestamp: datetime


class AggrTrade(BaseModel):
    """Aggregated trade for Order Flow Imbalance calculation"""
    exchange: str
    symbol: str
    price: float
    size: float
    is_buyer_maker: bool
    timestamp: datetime


class Liquidation(BaseModel):
    exchange: str
    symbol: str
    side: str
    price: float
    size: float
    size_usd: float
    timestamp: datetime


# ─────────────────────────────────────────────────────────────────────────────
# SMC CORE STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

class SwingPoint(BaseModel):
    price: float
    timestamp: datetime
    timeframe: str
    type: str  # "HIGH" | "LOW"
    strength: int = Field(..., description="Number of confirming candles")


class ImbalanceZone(BaseModel):
    type: str  # "BULLISH_FVG" | "BEARISH_FVG"
    low: float
    high: float
    timeframe: str
    created_at: datetime
    filled: bool = False
    fill_pct: float = 0.0  # 0.0 = unfilled, 1.0 = fully filled


class VolumeNode(BaseModel):
    price: float
    volume: float
    type: str  # "HVN" | "LVN"
    percentile: float  # where in volume profile (0-100)


class StructureEvent(BaseModel):
    event_type: str  # "BOS" | "CHOCH"
    direction: str   # "BULLISH" | "BEARISH"
    price: float
    timestamp: datetime
    timeframe: str
    confirmed: bool


class LiquiditySweep(BaseModel):
    swept_price: float      # the swing point that was swept
    sweep_high: float       # candle high during sweep
    close_price: float      # close back below/above swept price
    direction: str          # "LONG_SWEEP" (swept highs) | "SHORT_SWEEP" (swept lows)
    timestamp: datetime
    timeframe: str


class SMCAnalysis(BaseModel):
    symbol: str
    timeframe: str
    swing_highs: list[SwingPoint]
    swing_lows: list[SwingPoint]
    imbalance_zones: list[ImbalanceZone]
    volume_nodes: list[VolumeNode]
    structure_events: list[StructureEvent]
    liquidity_sweeps: list[LiquiditySweep]
    premium_discount_ratio: float  # 0.0 = at low, 0.5 = 50%, 1.0 = at high
    analyzed_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-TIMEFRAME ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class TimeframeTrend(BaseModel):
    timeframe: str
    direction: int  # -1 (bearish), 0 (neutral), +1 (bullish)
    weight: float
    weighted_score: float


class MTFScore(BaseModel):
    symbol: str
    score: float               # weighted sum across timeframes
    signal: str                # "STRONG_LONG" | "NO_SIGNAL" (SPOT ONLY)
    timeframes: list[TimeframeTrend]
    computed_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# CONFLUENCE ENGINE v4 — DYNAMIC WEIGHTED
# ─────────────────────────────────────────────────────────────────────────────

class ConfluenceFactor(BaseModel):
    name: str
    value: bool
    weight: float             # dynamic weight from Feature Store (v4)
    contribution: float       # weight × value (0 if False)
    detail: str = ""          # human-readable explanation


class WeightedConfluenceScore(BaseModel):
    """v4: Dynamic weighted confluence instead of simple count"""
    symbol: str
    direction: Direction
    raw_score: float          # Σ(weight × value)
    normalized_score: float   # 0-10 scale
    max_possible_score: float # Σ(all weights)
    factors: list[ConfluenceFactor]
    active_count: int         # how many factors are True
    total_factors: int
    weights_source: str       # "feature_store_trained" | "equal_fallback"
    regime: MarketRegime
    passed_threshold: bool
    top_3_factors: list[str]  # for AI audit explanation
    computed_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# MICROSTRUCTURE
# ─────────────────────────────────────────────────────────────────────────────

class OFIResult(BaseModel):
    symbol: str
    ofi_score: float        # 0-1 (0.6+ = buyers dominate)
    delta_usd: float        # net buy - sell in USD
    trend: str              # "bullish" | "bearish" | "neutral"
    window_seconds: int
    computed_at: datetime


class CumulativeDeltaResult(BaseModel):
    symbol: str
    cd_trend: str           # "bullish" | "bearish" | "divergent_bearish" | "divergent_bullish"
    cd_value: float
    price_cd_divergence: bool
    lookback_candles: int
    computed_at: datetime


class SlippageEstimate(BaseModel):
    symbol: str
    order_size_usd: float
    direction: str
    estimated_slippage_pct: float
    recommended_execution: ExecutionType
    warn: bool              # True if slippage > 0.5%
    computed_at: datetime


class SpoofingAlert(BaseModel):
    symbol: str
    detected: bool
    episodes_count: int
    time_window_seconds: int
    severity: str           # "NONE" | "LOW" | "HIGH" | "COORDINATED"
    largest_order_usd: float
    computed_at: datetime


class MicrostructureResult(BaseModel):
    ofi: OFIResult
    cumulative_delta: CumulativeDeltaResult
    slippage_estimate: SlippageEstimate
    spoofing: SpoofingAlert


# ─────────────────────────────────────────────────────────────────────────────
# LIQUIDATION CASCADE
# ─────────────────────────────────────────────────────────────────────────────

class LiquidationAnalysis(BaseModel):
    symbol: str
    liquidations_1h_usd: float
    liquidations_direction: str    # "LONG" | "SHORT"
    risk_level: str                # "LOW" | "MEDIUM" | "HIGH" | "EXTREME"
    status: LiquidationStatus
    opportunity_type: str | None   # "REVERSAL" | None
    recommended_action: str
    cascade_in_progress: bool
    post_cascade_reversal: bool
    computed_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# ANOMALY FLAGS
# ─────────────────────────────────────────────────────────────────────────────

class AnomalyFlag(BaseModel):
    flag: str
    severity: AnomалySeverity
    value: str
    threshold: str | None = None
    description: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# RISK ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class PositionSizeResult(BaseModel):
    kelly_fraction: float
    half_kelly: float
    volatility_multiplier: float
    drawdown_multiplier: float
    final_size_pct: float     # % of deposit
    final_size_usd: float
    regime: VolatilityRegime
    current_drawdown_pct: float
    capped_at: str | None     # "min" | "max" | None (if not capped)


class VaRResult(BaseModel):
    var_95_pct: float         # 95% VaR
    var_99_pct: float         # 99% VaR
    horizon_days: int
    open_positions: int
    exceeds_threshold: bool   # if var_95 > 5%


class SLTPResult(BaseModel):
    stop_loss: float
    take_profit_1: float
    rr_ratio_tp1: float
    sl_buffer_pct: float
    sl_near_round_number: bool  # adversarial warning
    structure_target_type: Optional[str] = "ATR"
    structure_target_pct: Optional[float] = 0.0
    first_barrier_pct: Optional[float] = 0.0
    min_required_tp_pct: Optional[float] = 0.0
    tp_rejected_reason: Optional[str] = ""
    sl_distance_pct: Optional[float] = 0.0
    atr_pct: Optional[float] = 0.0
    atr_target_pct: Optional[float] = 0.0
    max_tp_pct: Optional[float] = 0.0
    raw_tp_pct: Optional[float] = 0.0
    risk_pct: Optional[float] = 0.0
    swing_low: Optional[float] = 0.0
    is_pullback: bool = False
    pullback_status: str = ""
    pullback_limit_1: float = 0.0
    pullback_limit_2: float = 0.0
    pullback_tp_3: float = 0.0


class CorrelationResult(BaseModel):
    new_symbol: str
    open_positions: list[str]
    max_correlation: float
    correlated_with: str | None
    correlation_ok: bool
    deribit_vol_warn: bool      # v3: options skew warning


class RiskStatus(BaseModel):
    can_trade: bool
    stop_reason: str | None
    daily_signals_used: int
    daily_pnl_pct: float
    drawdown_from_peak_pct: float
    consecutive_losses: int
    var_portfolio_pct: float


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE STORE v4
# ─────────────────────────────────────────────────────────────────────────────

class SignalFeatures(BaseModel):
    """Complete feature vector for a signal — stored in Feature Store"""
    confluence_score: float
    mtf_score: float
    regime: MarketRegime
    volatility_regime: VolatilityRegime
    funding_rate: float
    fear_greed: int
    oi_change_pct: float
    ofi_score: float
    news_sentiment: float
    onchain_direction: str      # "inflow" | "outflow" | "neutral"
    copy_trader_signal: str     # "CONFIRMING" | "AGAINST" | "NEUTRAL"
    liquidation_status: LiquidationStatus
    temporal_bias_score: float  # NEW v4
    macro_bias: MacroBias       # NEW v4
    divergence_strength: DivergenceStrength  # NEW v4


class SimilarSetupResult(BaseModel):
    """v4: Regime-weighted historical lookup result"""
    win_rate_historical: float
    avg_pnl_pct: float
    avg_mae: float              # mean adverse excursion
    avg_time_to_tp1_hours: float
    sample_size: int
    confidence_interval: tuple[float, float]  # 95% CI
    regime_match_pct: float     # NEW v4: % of results from same regime
    weights_used: str           # "regime_weighted" | "equal"
    insufficient_data: bool


class CalibratedScore(BaseModel):
    raw_score: float
    winrate_estimate: float
    confidence_interval: tuple[float, float]
    sample_size: int
    regime: MarketRegime
    regime_specific: bool       # NEW v4: calibrated per regime


# ─────────────────────────────────────────────────────────────────────────────
# ML REGIME CLASSIFIER v4
# ─────────────────────────────────────────────────────────────────────────────

class RegimePrediction(BaseModel):
    regime: MarketRegime
    confidence: float           # 0-1
    hmm_regime: MarketRegime
    xgb_regime: MarketRegime
    rule_regime: MarketRegime
    source: str                 # "ml_ensemble" | "rule_based_fallback"
    ensemble_weights: dict[str, float]  # NEW v4: dynamic weights
    drift_hours: int            # NEW v4: hours of ML/rule disagreement
    computed_at: datetime


class RegimeThresholds(BaseModel):
    regime: MarketRegime
    confluence_min: float       # weighted score minimum
    daily_signals_max: int
    risk_pct_max: float


class DriftStatus(BaseModel):
    """NEW v4: Online Regime Drift Detector result"""
    drift_detected: bool
    ml_rule_agreement_pct: float   # last 48h
    disagreement_hours: int
    action_taken: str           # "none" | "weights_adjusted" | "retraining_triggered"
    ml_weight: float            # current ML weight in ensemble
    rule_weight: float          # current rule-based weight
    alert_sent: bool


# ─────────────────────────────────────────────────────────────────────────────
# ADVERSARIAL SIGNAL TESTER (NEW v4)
# ─────────────────────────────────────────────────────────────────────────────

class AdversarialCheck(BaseModel):
    check_name: str
    passed: bool
    score_contribution: float   # 0-10
    detail: str
    evidence: dict[str, Any] = {}


class AdversarialResult(BaseModel):
    """Result of pre-execution adversarial test"""
    adversarial_score: float    # 0-10
    risk_level: AdversarialRisk
    manipulation_probability: str   # "LOW" | "MEDIUM" | "HIGH" | "EXTREME"
    passed: bool                # True if score < 7
    checks: list[AdversarialCheck]
    key_concerns: list[str]
    auto_rejected: bool         # True if score >= 10
    confluence_min_adjustment: int  # +0, +1, or +2 based on risk
    computed_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# SENTIMENT DIVERGENCE DETECTOR (NEW v4)
# ─────────────────────────────────────────────────────────────────────────────

class SocialData(BaseModel):
    symbol: str
    fear_greed_index: int
    lunarcrush_social_score: float | None
    lunarcrush_social_volume: int | None
    funding_rate_pct: float
    captured_at: datetime


class SmartMoneyData(BaseModel):
    exchange_outflow_btc_24h: float   # negative = outflow (bullish)
    exchange_flow_direction: str       # "inflow" | "outflow" | "neutral"
    whale_transactions_24h: int
    whale_net_direction: str          # "accumulation" | "distribution" | "neutral"
    sopr: float                        # < 1.0 = realized losses (potential bottom)
    stablecoin_inflow_24h_usd: float


class DivergenceResult(BaseModel):
    crowd_score: float              # 0-100, 100 = max euphoria
    smart_money_score: float        # 0-100, 100 = max accumulation
    divergence_raw: float           # smart_money - crowd
    divergence_type: DivergenceType
    divergence_strength: DivergenceStrength
    crowd_sentiment: str            # "FEAR" | "NEUTRAL" | "GREED" | "EXTREME_GREED"
    smart_money_action: str         # "ACCUMULATING" | "NEUTRAL" | "DISTRIBUTING"
    historical_accuracy_pct: float | None  # from Feature Store
    historical_sample_size: int | None
    computed_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# CROSS-ASSET CORRELATION (NEW v4)
# ─────────────────────────────────────────────────────────────────────────────

class CorrelationSnapshot(BaseModel):
    asset_a: str
    asset_b: str
    correlation_24h: float
    correlation_7d: float
    correlation_30d: float
    computed_at: datetime


class DominanceSignal(BaseModel):
    btc_dominance: float
    dominance_trend: str    # "rising" | "falling" | "stable"
    season: str             # "BTC_SEASON" | "ALT_SEASON" | "NEUTRAL"


class MacroCorrelationResult(BaseModel):
    dxy_value: float
    dxy_1h_change_pct: float
    dxy_trend_24h: str          # "strengthening" | "weakening" | "stable"
    dxy_btc_correlation: CorrelationSnapshot
    gold_1h_change_pct: float
    gold_btc_correlation: CorrelationSnapshot
    dominance: DominanceSignal
    macro_bias: MacroBias
    correlation_regime: str     # "INVERSE_DXY" | "RISK_OFF" | "UNCORRELATED"
    computed_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# TEMPORAL PATTERN RECOGNITION (NEW v4)
# ─────────────────────────────────────────────────────────────────────────────

class DayOfWeekBias(BaseModel):
    weekday: int            # 0=Monday, 6=Sunday
    weekday_name: str
    historical_return_pct: float
    sample_size: int
    statistically_significant: bool


class FOMCBias(BaseModel):
    hours_to_fomc: float | None
    hours_after_fomc: float | None
    fomc_pre_window_active: bool    # < 24h before
    fomc_caution_active: bool       # < 2h before
    fomc_post_boost_active: bool    # 1-24h after
    confluence_min_adjustment: int  # +0 or +1


class HalvingBias(BaseModel):
    days_to_halving: int | None
    days_after_halving: int | None
    cycle_phase: str        # "PRE_HALVING_EARLY" | "PRE_HALVING_LATE" | "POST_HALVING" | "BULL_RUN" | "NONE"
    bias_strength: float    # -1.0 to +1.0
    confidence: str         # "LOW" (only 3 halvings of data)


class TemporalBiasResult(BaseModel):
    day_of_week: DayOfWeekBias
    fomc: FOMCBias
    halving: HalvingBias
    monthly_expiry_days: int | None  # days to monthly Deribit expiry
    combined_score: float            # -2.0 to +2.0
    label: TemporalBiasLabel
    computed_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# P2P MODELS
# ─────────────────────────────────────────────────────────────────────────────

class P2PCounterparty(BaseModel):
    rating: float
    trades: int
    avg_time_min: float
    account_age_days: int
    front_running_score: float = 0.0  # NEW v4


class FXSlippageEstimate(BaseModel):
    """NEW v4: FX drift model for P2P"""
    pair: str
    execution_minutes: float
    fx_drift_expected_pct: float
    btc_impact_pct: float
    total_expected_slippage_pct: float
    time_of_day_multiplier: float
    adjusted_net_margin_pct: float
    grade_adjusted: OpportunityGrade
    computed_at: datetime


class P2POrderbook(BaseModel):
    exchange: str
    pair: str
    best_buy_price: float
    best_sell_price: float
    depth_buy: list[tuple[float, float]]   # (price, volume) pairs
    depth_sell: list[tuple[float, float]]
    data_source: str    # "official_api" | "scraper"
    captured_at: datetime


class ArbitrageOpportunity(BaseModel):
    pair: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    gross_margin_pct: float
    fees_pct: float
    net_margin_pct: float
    volume_usdt: float
    profit_rub: float
    execution_minutes_estimate: float
    grade: OpportunityGrade
    data_source: str
    counterparty_buy: P2PCounterparty
    counterparty_sell: P2PCounterparty
    fx_slippage: FXSlippageEstimate | None  # NEW v4
    found_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL — CORE OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

class SignalCore(BaseModel):
    symbol: str
    direction: Direction
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float | None = None
    take_profit_3: float | None = None
    tp_allocation: list[float] = [1.0]
    position_size_pct: float
    position_size_usd: float
    risk_pct: float
    signal_valid_hours: int
    generated_at: datetime

    @model_validator(mode="after")
    def validate_rr(self) -> "SignalCore":
        entry_mid = (self.entry_low + self.entry_high) / 2
        rr = (self.take_profit_1 - entry_mid) / (entry_mid - self.stop_loss)
        if rr < 1.5:
            raise ValueError(f"RR ratio {rr:.2f} below minimum 1.5")
        return self


class FullSignalPackage(BaseModel):
    """Complete signal package sent to AI Audit Layer"""
    signal: SignalCore
    confluence: WeightedConfluenceScore
    microstructure: MicrostructureResult
    liquidation: LiquidationAnalysis
    regime: RegimePrediction
    regime_thresholds: RegimeThresholds
    calibrated_winrate: CalibratedScore
    similar_setups: SimilarSetupResult
    adversarial: AdversarialResult          # NEW v4
    sentiment_divergence: DivergenceResult  # NEW v4
    macro_correlation: MacroCorrelationResult  # NEW v4
    temporal_bias: TemporalBiasResult       # NEW v4
    anomaly_flags: list[AnomalyFlag]
    risk_status: RiskStatus
    position_size: PositionSizeResult
    var_result: VaRResult
    news_sentiment: float
    news_headlines: list[dict[str, str]]
    onchain_data: SmartMoneyData
    copy_trader_data: dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────────
# AI AUDIT OUTPUT v4 — WITH PARAMETER MODIFICATION
# ─────────────────────────────────────────────────────────────────────────────

class ParameterAdjustments(BaseModel):
    """NEW v4: AI can actively modify signal parameters"""
    entry_range_adjusted: tuple[float, float] | None = None
    stop_loss_adjusted: float | None = None
    position_size_multiplier: float = 1.0   # 0.5-1.0 reduction
    execution_type_override: ExecutionType | None = None
    tp1_adjusted: float | None = None
    adjustment_reason: str = ""

    @field_validator("position_size_multiplier")
    @classmethod
    def validate_multiplier(cls, v: float) -> float:
        if not 0.1 <= v <= 1.0:
            raise ValueError("position_size_multiplier must be between 0.1 and 1.0")
        return v


class AnomalyInterpretation(BaseModel):
    flag: str
    value: str
    interpretation: str
    impact: str     # "WARN_retained" | "WARN_managed" | "ACTION_REQUIRED" | "FAIL"
    action: str | None = None


class AuditChecks(BaseModel):
    narrative_technical: str      # "PASS — ..." | "WARN — ..." | "FAIL — ..."
    onchain_narrative: str
    anomaly_interpretation: str
    calibrated_confidence: str
    sentiment_positioning: str
    risk_reward: str
    portfolio_context: str
    adversarial_check: str        # NEW v4
    smart_money_divergence: str   # NEW v4
    macro_alignment: str          # NEW v4
    temporal_bias: str            # NEW v4


class AIAuditResult(BaseModel):
    """Complete AI audit output v4 with parameter modification"""
    approved: bool
    approval_type: ApprovalType
    final_confidence: float = Field(..., ge=0, le=10)
    confidence_modifier: float = 0.0

    parameter_adjustments: ParameterAdjustments  # NEW v4 — AI modifies params

    audit_checks: AuditChecks
    anomaly_interpretation: list[AnomalyInterpretation]

    audit_summary: str
    strengths: list[str]
    risks: list[str]
    do_not_enter_if: list[str]
    execution_note: str
    monitoring_notes: str

    position_adjustment: dict[str, Any] | None = None  # for CAUTIOUS approval
    rejection_reason: str | None = None

    audit_duration_ms: float
    audited_at: datetime

    @model_validator(mode="after")
    def validate_rejection(self) -> "AIAuditResult":
        if not self.approved and not self.rejection_reason:
            raise ValueError("rejection_reason required when not approved")
        return self


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION MODELS
# ─────────────────────────────────────────────────────────────────────────────

class ExecutionRecommendation(BaseModel):
    """NEW v4: From Execution Learning Loop"""
    recommended_type: ExecutionType
    confidence: float           # 0-1
    historical_avg_slippage_pct: float
    sample_size: int
    based_on: str               # "learning_loop" | "rule_based_fallback"


class ExecutionResult(BaseModel):
    signal_id: int
    exchange: str
    execution_type: ExecutionType
    planned_entry: float
    actual_entry: float
    slippage_pct: float
    fill_quality: FillQuality
    size_executed_pct: float    # what % of order was filled
    orders_placed: list[dict[str, Any]]
    fees_usd: float
    executed_at: datetime


class TWAPResult(BaseModel):
    symbol: str
    total_size_usd: float
    executed_size_usd: float
    n_slices: int
    completed_slices: int
    avg_price: float
    duration_seconds: int
    cancelled: bool
    cancel_reason: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# ON-CHAIN DATA
# ─────────────────────────────────────────────────────────────────────────────

class OnChainMetrics(BaseModel):
    symbol: str
    exchange_inflow_btc_24h: float
    exchange_outflow_btc_24h: float
    exchange_flow_direction: str
    sopr: float
    mvrv_z_score: float
    whale_transactions_24h: int
    whale_net_direction: str
    stablecoin_inflow_24h_usd: float
    open_interest_change_24h_pct: float
    funding_rate_pct: float
    liquidations_1h_usd: float
    deribit_max_pain: float | None
    options_iv_percentile: float | None
    captured_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# MONITORING & REPORTING
# ─────────────────────────────────────────────────────────────────────────────

class TradeOutcome(BaseModel):
    signal_id: int
    win: bool
    actual_pnl_pct: float
    close_reason: str       # "TP1" | "TP2" | "TP3" | "SL" | "MANUAL" | "EXPIRY"
    max_adverse_excursion: float
    max_favorable_excursion: float
    time_to_close_hours: float
    closed_at: datetime


class WeeklyPerformanceStats(BaseModel):
    period_start: datetime
    period_end: datetime
    signals_generated: int
    signals_approved: int
    approval_rate_pct: float
    trades_executed: int
    wins: int
    losses: int
    win_rate_pct: float
    total_pnl_pct: float
    sharpe_weekly: float
    max_drawdown_pct: float
    avg_kelly_size_pct: float
    predicted_winrate_avg: float
    actual_winrate: float
    calibration_drift_pct: float
    calibration_status: str     # "HEALTHY" | "DRIFT_WARNING" | "RECALIBRATION_NEEDED"
    p2p_profit_total: float
    avg_slippage_pct: float


class CalibrationHealth(BaseModel):
    predicted_winrate: float
    actual_winrate: float
    drift_pct: float
    status: str
    last_calibrated: datetime
    next_recalibration: datetime
    requires_immediate_recalibration: bool


# ─────────────────────────────────────────────────────────────────────────────
# SHADOW MODE
# ─────────────────────────────────────────────────────────────────────────────

class ShadowComparisonReport(BaseModel):
    shadow_version: str
    production_version: str
    period_days: int
    shadow_pnl_pct: float
    production_pnl_pct: float
    shadow_win_rate: float
    production_win_rate: float
    shadow_approval_rate: float
    production_approval_rate: float
    deploy_recommended: bool
    deploy_criteria_met: dict[str, bool]
    comparison_date: datetime


# ─────────────────────────────────────────────────────────────────────────────
# COPY TRADING v4 — WITH FRONT-RUNNING DETECTION
# ─────────────────────────────────────────────────────────────────────────────

class CopyTraderPosition(BaseModel):
    trader_id: str
    exchange: str
    symbol: str
    direction: Direction
    entry_price: float
    entry_time: datetime
    win_rate_90d: float
    total_trades: float
    max_drawdown_pct: float
    follower_count: int
    follower_count_24h_change_pct: float  # NEW v4
    front_running_score: float            # NEW v4 (0-1)


class CopyTradingSignal(BaseModel):
    symbol: str
    matching_long: list[CopyTraderPosition]
    matching_short: list[CopyTraderPosition]
    signal: str     # "CONFIRMING" | "AGAINST" | "NEUTRAL"
    top_trader_avg_entry: float | None
    front_running_detected: bool  # NEW v4
    effective_weight: float       # reduced if front-running detected


# ─────────────────────────────────────────────────────────────────────────────
# ADVANCED SPOT-ONLY STRATEGIES (v4.0)
# ─────────────────────────────────────────────────────────────────────────────

class FlashCrashTarget(BaseModel):
    symbol: str
    target_price: float
    discount_pct: float           # How deep is this from current price
    reasoning: str                # e.g., "Deep Liquidity Vacuum + Untested Swing Low"
    recommended_size_usd: float
    placed_at: datetime


class RotationSignal(BaseModel):
    dominance_signal: DominanceSignal
    macro_bias: MacroBias
    altcoin_multipliers: dict[str, float]  # e.g., {"SOL/USDT": 1.2, "ETH/USDT": 1.1}
    generated_at: datetime

