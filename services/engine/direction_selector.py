"""
Direction Selector for APEX.
Determines whether a signal should be LONG, SHORT, or ignored based on confluence.
"""

class DirectionSelector:
    
    def select_direction(
        self,
        symbol: str,
        mtf_score: float,
        cvd_signal: str,
        regime: str,
        premium_discount: float,
        fear_greed: int,
        symbol_change_1h: float = 0.0,
        btc_change_1h: float = 0.0,
        funding_bias: str = "NEUTRAL",
        lsr_bias: str = "NEUTRAL"
    ) -> str | None:
        
        # LONG условия
        long_score = 0
        if mtf_score > 0.3:          long_score += 2
        if cvd_signal == "BULLISH":  long_score += 1
        if premium_discount < 0.4:   long_score += 2  # discount zone
        if fear_greed < 30:          long_score += 1  # покупаем страх
        if funding_bias == "EXTREME_SHORT": long_score += 2
        if lsr_bias == "EXTREME_SHORT": long_score += 2
        
        # SHORT условия
        short_score = 0
        if mtf_score < -0.3:         short_score += 2
        if cvd_signal == "BEARISH":  short_score += 1
        if premium_discount > 0.7:   short_score += 2  # premium zone
        if fear_greed > 75:          short_score += 1  # продаём жадность
        if funding_bias == "EXTREME_LONG": short_score += 3  # Leverage Squeeze potential
        elif funding_bias == "HIGH_LONG": short_score += 1
        if lsr_bias == "EXTREME_LONG": short_score += 3      # High retail crowding
        elif lsr_bias == "HIGH_LONG": short_score += 1
        
        # Relative Weakness (если BTC растет, а монета падает — это сильный шорт)
        relative_performance = symbol_change_1h - btc_change_1h
        if btc_change_1h > 1.0 and symbol_change_1h < -0.5:
            short_score += 3  # Extreme weakness
        elif btc_change_1h < -0.5 and symbol_change_1h < btc_change_1h * 2:
            short_score += 1  # Beta amplification
        elif abs(btc_change_1h) < 0.3 and symbol_change_1h < -1.5:
            short_score += 2  # Independent selling pressure
        
        # Блокировка глупых шортов
        if symbol_change_1h > 2.0 and btc_change_1h < 1.0:
            short_score = 0  # Монета невероятно сильная, шортить нельзя
        
        # Режимные правила
        if regime == "BULL" and short_score < 4:
            # в бычьем рынке шортим только при сильном сигнале
            pass
            
        if regime == "BEAR" and long_score < 4:
            # в медвежьем лонги только при сильном сигнале
            pass
            
        if regime == "BULL" and short_score < 4 and short_score > long_score:
            return None
            
        if regime == "BEAR" and long_score < 4 and long_score > short_score:
            return None
        
        if long_score >= 3 and long_score > short_score:
            return "LONG"
        
        if short_score >= 3 and short_score > long_score:
            return "SHORT"
        
        return None  # нет чёткого направления

direction_selector = DirectionSelector()
