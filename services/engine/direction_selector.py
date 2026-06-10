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
        fear_greed: int
    ) -> str | None:
        
        # LONG условия
        long_score = 0
        if mtf_score > 0.3:          long_score += 2
        if cvd_signal == "BULLISH":  long_score += 1
        if premium_discount < 0.4:   long_score += 2  # discount zone
        if fear_greed < 30:          long_score += 1  # покупаем страх
        
        # SHORT условия
        short_score = 0
        if mtf_score < -0.3:         short_score += 2
        if cvd_signal == "BEARISH":  short_score += 1
        if premium_discount > 0.7:   short_score += 2  # premium zone
        if fear_greed > 75:          short_score += 1  # продаём жадность
        
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
        
        if long_score >= 4 and long_score > short_score:
            return "LONG"
        
        if short_score >= 4 and short_score > long_score:
            return "SHORT"
        
        return None  # нет чёткого направления

direction_selector = DirectionSelector()
