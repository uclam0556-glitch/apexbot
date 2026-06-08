"""
APEX v11.0 — Trailing Stop Mechanics
"""

def compute_trailing_stop(entry_price:   float,
                           initial_sl:   float,
                           current_high: float,   # max price seen since entry (for LONG)
                           atr_14:       float,
                           direction:    str = 'LONG'
                           ) -> float:
    '''
    Advanced trailing stop logic.
    '''
    # We only care about LONG as per instruction
    if direction == 'LONG':
        mfe_abs = current_high - entry_price
        
        if mfe_abs < 1.0 * atr_14:
            # Phase 0: MFE < 1.0 ATR
            return initial_sl
            
        elif mfe_abs < 2.0 * atr_14:
            # Phase 1: MFE >= 1.0 ATR, < 2.0 ATR
            # Almost breakeven but with a tiny buffer
            new_sl = entry_price - 0.1 * atr_14
            return max(initial_sl, new_sl)
            
        elif mfe_abs < 3.0 * atr_14:
            # Phase 2: MFE >= 2.0 ATR
            # Standard trailing
            new_sl = current_high - 1.5 * atr_14
            return max(initial_sl, new_sl)
            
        else:
            # Phase 3: MFE >= 3.0 ATR
            # Tight trail to protect profit
            new_sl = current_high - 1.0 * atr_14
            return max(initial_sl, new_sl)
            
    return initial_sl


def should_activate_breakeven(mfe_pct:    float,
                               atr_pct:   float,
                               sl_dist_pct: float) -> bool:
    '''
    Breakeven activates ONLY if:
    - mfe_pct >= 1.0 * atr_pct
    - mfe_pct >= 0.5 * sl_dist_pct
    '''
    return mfe_pct >= max(1.0 * atr_pct, 0.5 * sl_dist_pct)
