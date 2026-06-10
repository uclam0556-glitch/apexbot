"""
Strategy Router for APEX.
Dynamically routes signals to different strategies based on global market regime.
"""

class StrategyRouter:
    
    REGIME_STRATEGY_MAP = {
        "BULL":     "TREND",           # в тренде — следуем тренду
        "BEAR":     "TREND",           # короткие позиции по тренду  
        "SIDEWAYS": "MEAN_REVERSION",  # в боковике — от уровней
        "PANIC":    None,              # стоп торговли
    }
    
    REGIME_PARAMS = {
        "BULL": {
            "min_v7_score": 45.0,
            "rsi_oversold": 40.0,
            "rsi_overbought": 70.0,
            "kelly_fraction": 0.5,     # агрессивнее
        },
        "SIDEWAYS": {
            "min_v7_score": 50.0,        # выше порог в боковике
            "rsi_oversold": 35.0,        # нужна реальная перепроданность
            "rsi_overbought": 65.0,
            "kelly_fraction": 0.25,    # консервативно
        },
        "BEAR": {
            "min_v7_score": 60.0,        # очень высокий порог для лонгов
            "rsi_oversold": 25.0,        # экстремальная перепроданность
            "rsi_overbought": 55.0,
            "kelly_fraction": 0.15,    # минимальный риск
        },
    }
    
    def get_strategy(self, regime: str) -> str | None:
        return self.REGIME_STRATEGY_MAP.get(regime, "TREND")
    
    def get_params(self, regime: str) -> dict:
        return self.REGIME_PARAMS.get(regime, self.REGIME_PARAMS["SIDEWAYS"])

strategy_router = StrategyRouter()
