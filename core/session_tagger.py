"""
APEX Trading System v10.5
core/session_tagger.py
"""

from datetime import datetime

class SessionTagger:
    """
    Теггирование торговых сессий для дальнейшего анализа.
    """
    
    @staticmethod
    def get_session(dt: datetime) -> str:
        """
        Определяет сессию по времени UTC.
        ASIA: 00:00 - 08:00 UTC
        LONDON: 08:00 - 13:00 UTC
        NY: 13:00 - 22:00 UTC
        """
        hour = dt.hour
        if 0 <= hour < 8:
            return "ASIA"
        elif 8 <= hour < 13:
            return "LONDON"
        elif 13 <= hour < 22:
            return "NY"
        else:
            return "POST_NY"
