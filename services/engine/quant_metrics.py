"""
Institutional Quant Metrics Library
Calculates risk-adjusted metrics like Sortino Ratio and Time-Decay weights.
"""

import numpy as np
import pandas as pd
from datetime import datetime

def calculate_time_decay_weights(dates: pd.Series, half_life_days: float = 14.0) -> np.ndarray:
    """
    Calculates exponential time-decay weights for a series of timestamps.
    Older trades receive lower weights.
    
    half_life_days = 14.0 means a trade from 14 days ago has 50% the weight of a trade today.
    """
    if len(dates) == 0:
        return np.array([])
        
    now = pd.Timestamp.utcnow()
    # Convert dates to pandas datetime if they aren't already
    dates_dt = pd.to_datetime(dates)
    
    # Calculate age in days
    try:
        age_days = (now - dates_dt).dt.total_seconds() / (24 * 3600)
    except TypeError:
        # Fallback if tz-aware/naive mismatch
        if dates_dt.dt.tz is None:
            dates_dt = dates_dt.dt.tz_localize('UTC')
        age_days = (now - dates_dt).dt.total_seconds() / (24 * 3600)
    
    # Exponential decay formula: W = e^(-lambda * t)
    # lambda = ln(2) / half_life
    decay_constant = np.log(2) / half_life_days
    
    weights = np.exp(-decay_constant * age_days)
    return weights.values

def calculate_sortino_ratio(returns: np.ndarray, target_return: float = 0.0) -> float:
    """
    Calculates the Sortino Ratio of a returns array.
    Sortino = (Expected Return - Target Return) / Downside Deviation
    """
    if len(returns) == 0:
        return 0.0
        
    mean_return = np.mean(returns)
    
    # Isolate downside returns
    downside_returns = returns[returns < target_return]
    
    if len(downside_returns) == 0:
        # If there is no downside risk, the ratio approaches infinity. 
        # We cap it or return a high placeholder if return is positive.
        return 99.9 if mean_return > 0 else 0.0
        
    downside_variance = np.mean(np.square(downside_returns - target_return))
    downside_deviation = np.sqrt(downside_variance)
    
    if downside_deviation == 0:
        return 99.9 if mean_return > 0 else 0.0
        
    return (mean_return - target_return) / downside_deviation
