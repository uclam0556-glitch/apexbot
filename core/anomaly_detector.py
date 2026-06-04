"""
APEX Trading System v10.5
core/anomaly_detector.py
"""

from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

class AnomalyDetector:
    """
    Детектор аномалий для системы.
    В первую очередь отслеживает 'аномальную тишину' (когда система
    долго не выдает сигналов при активном рынке).
    """
    
    def __init__(self, max_silence_minutes: int = 120):
        self.max_silence_minutes = max_silence_minutes
        self.last_signal_time = datetime.now(timezone.utc)
        
    def register_signal(self):
        """Вызывается при любом сгенерированном сигнале (даже отклоненном)."""
        self.last_signal_time = datetime.now(timezone.utc)
        
    def check_silence(self, current_market_regime: str) -> dict:
        """
        Проверяет, не молчит ли система слишком долго.
        В CHOP режиме тишина — это нормально.
        В TRENDING режиме долгая тишина подозрительна.
        """
        if current_market_regime == 'CHOP':
            return {'status': 'OK', 'reason': 'CHOP_MARKET_NORMAL_SILENCE'}
            
        now = datetime.now(timezone.utc)
        silence_minutes = (now - self.last_signal_time).total_seconds() / 60.0
        
        if silence_minutes > self.max_silence_minutes:
            msg = f"SYSTEM ANOMALY: No signals generated for {silence_minutes:.1f} minutes in {current_market_regime} regime."
            logger.error(msg)
            return {'status': 'ANOMALY', 'reason': msg, 'silence_minutes': silence_minutes}
            
        return {'status': 'OK', 'silence_minutes': silence_minutes}
