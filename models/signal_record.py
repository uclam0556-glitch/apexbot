"""
APEX Trading System v10.5
models/signal_record.py
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime

class SignalRecord(BaseModel):
    """
    Абсолютно каждый сигнал должен логироваться в таком формате
    до того, как он будет передан в исполнение или отклонён.
    """
    timestamp: datetime
    symbol: str
    direction: str  # "LONG" | "SHORT"
    logic_version: str = "10.5.0"
    
    # 1. Структура рынка (SMC)
    swing_high: float
    swing_low: float
    fvg_zone_low: float
    fvg_zone_high: float
    
    # 2. Уровни ордера
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    
    # 3. Индикаторы на момент формирования
    rsi_1h: float
    rsi_4h: float
    cvd_trend: float  # +1 / -1
    ofi_imbalance: float
    volume_3h_usd: float
    
    # 4. Внешняя среда
    btc_dominance: float
    market_breadth_pct: float
    regime: str  # "TRENDING" | "CHOP" | "CAPITULATION"
    session: str # "ASIA" | "LONDON" | "NY"
    
    # 5. Результат скоринга
    v7_score_raw: float
    mtf_score: float
    
    # 6. Вердикт системы
    status: str  # "ACCEPTED" | "REJECTED_BY_FILTER" | "REJECTED_BY_RISK"
    block_reason: Optional[str] = None
    
    # 7. Исполнение (заполняется позже)
    executed_size: Optional[float] = None
    fill_price: Optional[float] = None
    slippage_pct: Optional[float] = None
