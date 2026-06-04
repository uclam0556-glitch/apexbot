"""
APEX Trading System v10.5
config/data_collection_mode.py
"""

DATA_COLLECTION_MODE = {
    # Включить: True = отключено на период сбора данных
    'DISABLE_CAPITULATION_STRATEGY': True,
    # Причина: требует точного определения экстремума.
    # Без статистики загрязняет выборку.

    'DISABLE_WEIGHTS_OPTIMIZER': True,
    'DISABLE_ISOTONIC_CALIBRATION': True,
    'DISABLE_DYNAMIC_THRESHOLDS': True,  # Пороги фиксированы

    # Реальная торговля отключена
    'REAL_TRADING_ENABLED': False,
    'PAPER_MODE': True,

    # Что включено
    'SHADOW_TRADE_MONITOR': True,
    'FULL_SIGNAL_LOGGING': True,
    'STATISTICAL_REPORTING': True,  # каждые 50 trades
    'ANOMALY_DETECTION': True,
}
