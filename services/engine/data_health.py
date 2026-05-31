import logging
from datetime import datetime

logger = logging.getLogger("DataHealth")

def compute_data_health(
    symbol: str, 
    last_ws_update: float, 
    avg_vol_3: float, 
    baseline_hourly_vol: float,
    funding_rate: float
) -> float:
    """
    Computes a Data Health Score (0.0 to 100.0) based on input quality.
    
    Factors:
    1. WebSocket Latency (Freshness of price data)
    2. Volume anomalies (Zero volume or extreme drop)
    3. Funding Rate validity (None or NaN)
    """
    score = 100.0
    
    # 1. WebSocket Freshness (penalty if no updates in the last 15 seconds)
    if last_ws_update:
        delay = datetime.utcnow().timestamp() - last_ws_update
        if delay > 15.0:
            logger.warning(f"[DATA HEALTH] {symbol} WS data is stale (delay: {delay:.1f}s). Penalty -30.")
            score -= 30.0
        elif delay > 5.0:
            score -= 10.0
    else:
        logger.warning(f"[DATA HEALTH] {symbol} WS data missing completely. Penalty -50.")
        score -= 50.0
        
    # 2. Volume Anomalies
    if avg_vol_3 == 0:
        logger.warning(f"[DATA HEALTH] {symbol} has 0 volume in last 3h. Penalty -40.")
        score -= 40.0
    elif baseline_hourly_vol > 0 and avg_vol_3 < baseline_hourly_vol * 0.1:
        logger.warning(f"[DATA HEALTH] {symbol} volume critically low (<10% of baseline). Penalty -20.")
        score -= 20.0
        
    # 3. Funding Rate Validity
    if funding_rate is None or funding_rate == 0.0:
        logger.warning(f"[DATA HEALTH] {symbol} Funding Rate missing or exact 0.0. Penalty -20.")
        score -= 20.0
        
    return max(0.0, score)
