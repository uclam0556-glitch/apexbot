import sys

def rewrite_scoring():
    with open('main.py', 'r') as f:
        lines = f.readlines()
        
    start_idx = -1
    end_idx = -1
    
    for i, line in enumerate(lines):
        if "if premium_penalty > 0:" in line and "v7_score -= premium_penalty" in lines[i+1]:
            start_idx = i
            break
            
    for i in range(start_idx, len(lines)):
        if "# ─── A+ SETUP BONUS (NO LONGER AN OVERRIDE) ────────────────────────────────" in lines[i]:
            end_idx = i
            break
            
    if start_idx == -1 or end_idx == -1:
        print("Could not find boundaries")
        return
        
    print(f"Replacing lines {start_idx} to {end_idx}")
    
    new_code = """                    # ─── V9 QUANT INDICES (MULTICOLLINEARITY FIX) ─────────────────────────
                    # 1. OVEREXTENSION INDEX
                    overext_points = 0
                    
                    rsi_max = 80 if regime_val == "BULL" else 73
                    if rsi_now > rsi_max: overext_points += 2
                    if z_score > 2.0: overext_points += 2
                    if premium_penalty > 0: overext_points += 1
                    
                    fvg_count = len(smc_analysis.imbalance_zones)
                    if fvg_count > 12: overext_points += 3
                    elif fvg_count > 10: overext_points += 2
                    elif fvg_count > 8: overext_points += 1
                    
                    overext_penalty = 0
                    if overext_points >= 6: overext_penalty = 30
                    elif overext_points >= 4: overext_penalty = 20
                    elif overext_points == 3: overext_penalty = 10
                    
                    if overext_penalty > 0:
                        v7_score -= overext_penalty
                        logger.info(f"{symbol} - Overextension Index: {overext_points} pts. Applied penalty: -{overext_penalty}")

                    # 2. STRUCTURAL CHOP INDEX
                    chop_points = 0
                    
                    if cvd_result.get("divergence"): chop_points += 2
                    if cvd_signal == "BEARISH" and cvd_score_val <= -2: chop_points += 1
                    
                    sweep_count = len(smc_analysis.liquidity_sweeps)
                    if sweep_count > 65: chop_points += 3
                    elif sweep_count > 50: chop_points += 2
                    elif sweep_count > 40: chop_points += 1
                    
                    df_15m_check = tf_data.get('15m', pd.DataFrame())
                    if not df_15m_check.empty and len(df_15m_check) >= 3:
                        last3 = df_15m_check.iloc[-4:-1]
                        last1 = df_15m_check.iloc[-2]
                        if trade_strategy in ["MEAN_REVERSION", "CAPITULATION"]:
                            green_count = sum(1 for _, c in last3.iterrows() if c['close'] > c['open'])
                            if green_count == 0: chop_points += 1
                        elif regime_val == "SIDEWAYS":
                            green_count = sum(1 for _, c in last3.iterrows() if c['close'] > c['open'])
                            if green_count < 2: chop_points += 1
                        else:
                            if last1['close'] < last1['open']: chop_points += 1
                            
                    chop_penalty = 0
                    if chop_points >= 5: chop_penalty = 25
                    elif chop_points >= 3: chop_penalty = 15
                    elif chop_points == 2: chop_penalty = 10
                    
                    if chop_penalty > 0:
                        v7_score -= chop_penalty
                        logger.info(f"{symbol} - Structural Chop Index: {chop_points} pts. Applied penalty: -{chop_penalty}")

                    # ─── MTF HARD CAP ──────────────────────────────────────────────────────────
                    if mtf_val < 0:
                        v7_score = min(v7_score, 50.0)  # Максимум 50/100 против тренда

                    # 3. INDEPENDENT MACRO PENALTY: BTC CORRELATION
                    btc_rsi = 50.0
                    if 'BTC' not in symbol:
                        try:
                            btc_1h = await self.fetch_market_data('BTC/USDT', '1h', 50)
                            if not btc_1h.empty:
                                btc_delta = btc_1h['close'].diff()
                                btc_gain  = btc_delta.clip(lower=0).rolling(14).mean()
                                btc_loss  = (-btc_delta.clip(upper=0)).rolling(14).mean()
                                btc_rsi   = (100 - (100 / (1 + btc_gain / btc_loss.replace(0, 1e-9)))).iloc[-1]
                                if trade_direction == "LONG" and btc_rsi < 42: v7_score -= 15
                                if trade_direction == "SHORT" and btc_rsi > 58: v7_score -= 15
                                
                                # ─── ADVANCED INSTITUTIONAL FILTER 5: INTRADAY RELATIVE STRENGTH (ALPHA) ──
                                if len(btc_1h) >= 5:
                                    btc_return_4h = (btc_1h['close'].iloc[-1] - btc_1h['close'].iloc[-5]) / btc_1h['close'].iloc[-5] * 100
                                    sym_return_4h = price_change_4h_pct
                                    if btc_return_4h < -1.0 and sym_return_4h > 1.0 and trade_direction == "LONG":
                                        v7_score += 20.0
                                        logger.info(f"🌟 {symbol} INTRADAY ALPHA BONUS! BTC is dropping ({btc_return_4h:.2f}%), but {symbol} is rising ({sym_return_4h:.2f}%). Strong relative strength.")
                        except Exception:
                            pass
                    else:
                        btc_rsi = rsi_now
                    
"""
    
    new_lines = lines[:start_idx] + [new_code] + lines[end_idx:]
    with open('main.py', 'w') as f:
        f.writelines(new_lines)
        
    print("Done")

rewrite_scoring()
