"""
APEX v6.0 — Macro Calendar Blackout Engine
Detects high-impact macroeconomic news (FOMC, CPI, NFP) to pause trading.
"""
import logging
from datetime import datetime
from typing import Tuple

logger = logging.getLogger(__name__)

def is_macro_blackout_window() -> Tuple[bool, str]:
    """
    Checks if current time is within a blackout window for high-impact news.
    High-impact news usually occurs on Wednesdays (FOMC) and Fridays (NFP).
    Standard release times: 
    - 12:30 UTC (CPI, NFP, GDP)
    - 18:00 UTC (FOMC Rate Decision)
    
    Blackout window: 30 minutes before, 15 minutes after.
    
    Returns:
        (is_blackout: bool, reason: str)
    """
    now = datetime.utcnow()
    weekday = now.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    hour = now.hour
    minute = now.minute
    
    # Time in minutes since midnight
    current_minutes = hour * 60 + minute
    
    # 1. CPI / NFP / GDP Window (12:30 UTC) -> 12:00 to 12:45
    news_1_time = 12 * 60 + 30
    if news_1_time - 30 <= current_minutes <= news_1_time + 15:
        # Usually Tuesdays, Wednesdays, Thursdays, Fridays
        if weekday in [1, 2, 3, 4]: 
            return True, "MACRO_NEWS_1230_UTC (CPI/NFP/GDP)"
            
    # 2. FOMC Window (18:00 UTC) -> 17:30 to 18:15
    news_2_time = 18 * 60 + 0
    if news_2_time - 30 <= current_minutes <= news_2_time + 15:
        # Usually Wednesdays
        if weekday == 2:
            return True, "MACRO_NEWS_1800_UTC (FOMC)"

    return False, ""
