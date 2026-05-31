import logging
import time

logger = logging.getLogger("DataHealth")

START_TIME = time.time()

def compute_data_health(
    symbol: str, 
    last_ws_update: float, 
    avg_vol_3: float, 
    baseline_hourly_vol: float,
    funding_rate: float
) -> dict:
    """
    Computes a Data Health Score (0.0 to 100.0) based on input quality.
    """
    score = 100.0
    reasons = []
    
    import time
    
    # 1. WebSocket Freshness (penalty if no updates in the last 15 seconds)
    uptime = time.time() - START_TIME
    if last_ws_update:
        delay = time.time() - last_ws_update
        if delay > 15.0:
            reasons.append(f"WS delay {delay:.1f}s")
            logger.warning(f"[DATA HEALTH] {symbol} WS data is stale (delay: {delay:.1f}s). Penalty -30.")
            score -= 30.0
        elif delay > 5.0:
            score -= 10.0
    else:
        if uptime < 60.0:
            logger.info(f"[DATA HEALTH] {symbol} WS missing but in 60s startup grace period. No penalty.")
        else:
            reasons.append("WS missing completely")
            logger.warning(f"[DATA HEALTH] {symbol} WS data missing completely. Penalty -50.")
            score -= 50.0
        
    # 2. Volume Anomalies
    if avg_vol_3 == 0:
        reasons.append("0 volume in 3h")
        logger.warning(f"[DATA HEALTH] {symbol} has 0 volume in last 3h. Penalty -40.")
        score -= 40.0
    elif baseline_hourly_vol > 0 and avg_vol_3 < baseline_hourly_vol * 0.1:
        reasons.append("Volume < 10% baseline")
        logger.warning(f"[DATA HEALTH] {symbol} volume critically low (<10% of baseline). Penalty -20.")
        score -= 20.0
        
    # 3. Funding Rate Validity
    if funding_rate is None or funding_rate == 0.0:
        reasons.append("Funding rate invalid")
        logger.warning(f"[DATA HEALTH] {symbol} Funding Rate missing or exact 0.0. Penalty -20.")
        score -= 20.0
        
    score = max(0.0, score)
    
    status = "OK"
    market_allowed = True
    limit_allowed = True
    
    if score >= 90:
        status = "OK"
    elif score >= 75:
        status = "DEGRADED"
        market_allowed = False
    elif score >= 60:
        status = "DEGRADED_SEVERE"
        market_allowed = False
    else:
        status = "BAD"
        market_allowed = False
        limit_allowed = False
        
    if "WS missing completely" in reasons:
        market_allowed = False

    return {
        "score": score,
        "status": status,
        "reasons": reasons,
        "market_allowed": market_allowed,
        "limit_allowed": limit_allowed,
        "market_disabled": not market_allowed
    }

