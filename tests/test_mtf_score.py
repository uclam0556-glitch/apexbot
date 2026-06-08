import pytest
from services.engine.mtf_engine import compute_mtf_score

def test_mtf_score():
    # Все бычьи = max bonus
    score, strong = compute_mtf_score({'1d': 1, '4h': 1, '1h': 1, '15m': 1, '5m': 1})
    assert score == 1.0 and strong == True
    
    # Смешанные = умеренный score
    score, strong = compute_mtf_score({'1d': 1, '4h': 1, '1h': -1, '15m': 1, '5m': -1})
    assert 0.0 < score < 1.0 and strong == False
    
    # Медвежьи = отрицательный
    score, strong = compute_mtf_score({'1d': -1, '4h': -1, '1h': -1, '15m': 1, '5m': 1})
    assert score < 0
