"""
APEX v11.0 — Institutional Factor Library (Phase 2.1)
======================================================
Evidence-based cross-sectional and time-series alpha factors.
No magic numbers without academic justification.

Factors implemented:
1. Cross-sectional momentum (Jegadeesh & Titman 1993)
2. Time-series momentum (Moskowitz, Ooi & Pedersen 2012)
3. Short-term reversal (Lehmann 1990)
4. Volume surprise (Gervais et al. 2001)
5. CVD divergence
6. Order book imbalance
7. Liquidation cascade pressure
8. Volatility regime ratio
9. Garman-Klass volatility (Garman & Klass 1980)
10. Vol premium
11. Funding rate z-score
12. OI Delta 4-state matrix

All computations must be strictly out-of-sample (no future lookahead).
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict

import numpy as np
import pandas as pd
from scipy.stats import rankdata
from scipy.stats.mstats import winsorize

logger = logging.getLogger(__name__)

@dataclass
class FactorScores:
    """Dataclass holding factor scores for a single asset at a point in time."""
    symbol: str
    timestamp: float
    cross_sectional_momentum: float
    time_series_momentum: float
    short_term_reversal: float
    volume_surprise: float
    cvd_divergence: float
    order_book_imbalance: float
    liquidation_cascade_pressure: float
    volatility_regime_ratio: float
    garman_klass_vol: float
    vol_premium: float
    funding_rate_zscore: float
    oi_delta_4state: float
    logic_version: str = '11.0.0'

class FactorEngine:
    """
    Computes 12 institutional factors for crypto assets.
    """

    def __init__(self):
        pass

    def compute_all_assets(self, asset_data: Dict[str, pd.DataFrame], current_time: pd.Timestamp = None) -> pd.DataFrame:
        """
        Compute factors for all assets cross-sectionally.
        Input: Dict of symbol -> DataFrame with OHLCV + features (funding, oi, etc)
        Returns: DataFrame [n_assets x 12 factors] after normalization and winsorization.
        """
        rows = []
        for symbol, df in asset_data.items():
            if current_time:
                df_slice = df[df.index <= current_time]
            else:
                df_slice = df
            
            if df_slice.empty or len(df_slice) < 63:  # Need 63 days for TSMOM
                continue
            
            try:
                scores = self.compute_single_asset(symbol, df_slice)
                row = {
                    'symbol': scores.symbol,
                    'cross_sectional_momentum_raw': scores.cross_sectional_momentum,
                    'time_series_momentum': scores.time_series_momentum,
                    'short_term_reversal': scores.short_term_reversal,
                    'volume_surprise': scores.volume_surprise,
                    'cvd_divergence': scores.cvd_divergence,
                    'order_book_imbalance': scores.order_book_imbalance,
                    'liquidation_cascade_pressure': scores.liquidation_cascade_pressure,
                    'volatility_regime_ratio': scores.volatility_regime_ratio,
                    'garman_klass_vol': scores.garman_klass_vol,
                    'vol_premium': scores.vol_premium,
                    'funding_rate_zscore': scores.funding_rate_zscore,
                    'oi_delta_4state': scores.oi_delta_4state,
                }
                rows.append(row)
            except Exception as e:
                logger.debug(f"Failed to compute factors for {symbol}: {e}")
                continue
                
        if not rows:
            return pd.DataFrame()
            
        factor_df = pd.DataFrame(rows).set_index('symbol')
        
        # 1. Cross-sectional normalizations (Momentum)
        if 'cross_sectional_momentum_raw' in factor_df.columns:
            # Rank scale: [0, 1] -> [-1, 1]
            ranks = rankdata(factor_df['cross_sectional_momentum_raw'], method='average')
            # Normalize to [-1, 1]
            if len(ranks) > 1:
                scaled_ranks = (ranks - 1) / (len(ranks) - 1) * 2 - 1
            else:
                scaled_ranks = np.zeros(len(ranks))
            factor_df['cross_sectional_momentum'] = scaled_ranks
            factor_df = factor_df.drop(columns=['cross_sectional_momentum_raw'])
        
        # Fill NA with 0.0 before winsorization
        factor_df = factor_df.fillna(0.0)
        
        # Winsorize and Check Correlations
        factor_df = self.winsorize(factor_df)
        self.factor_correlations(factor_df)
        
        return factor_df

    def compute_single_asset(self, symbol: str, ohlcv: pd.DataFrame) -> FactorScores:
        """
        Compute factors for a single asset.
        Returns raw values (cross-sectional metrics need further processing).
        """
        df = ohlcv.copy()
        
        # Basic derived metrics
        df['ret_1d'] = df['close'].pct_change(1)
        df['ret_3d'] = df['close'].pct_change(3)
        df['ret_7d'] = df['close'].pct_change(7)
        df['ret_30d'] = df['close'].pct_change(30)
        
        # Realized volatility (rolling std of daily returns)
        df['realized_vol_5d'] = df['ret_1d'].rolling(5).std() * np.sqrt(365)
        df['realized_vol_21d'] = df['ret_1d'].rolling(21).std() * np.sqrt(365)
        df['realized_vol_hist'] = df['ret_1d'].rolling(63).std() * np.sqrt(365)
        
        # ATR (14-day standard)
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['atr_3d'] = true_range.rolling(3).mean()
        
        # Latest values
        last = df.iloc[-1]
        
        # 1. Cross Sectional Momentum (Raw component, ranked across assets later)
        # mom_combined = 0.2*mom_1d + 0.3*mom_7d + 0.5*mom_30d
        # Here we just compute the raw weighted sum, ranking is done cross-sectionally.
        cs_mom_raw = 0.2 * (last['ret_1d'] if pd.notna(last['ret_1d']) else 0) + \
                     0.3 * (last['ret_7d'] if pd.notna(last['ret_7d']) else 0) + \
                     0.5 * (last['ret_30d'] if pd.notna(last['ret_30d']) else 0)
                     
        # 2. Time Series Momentum
        ts_mom = 0.0
        lookbacks = [5, 10, 21, 63]
        valid_lbs = 0
        for lb in lookbacks:
            if len(df) > lb:
                ret_lb = (df['close'].iloc[-1] / df['close'].iloc[-1-lb]) - 1
                vol_lb = df['ret_1d'].iloc[-lb:].std() * np.sqrt(365)
                if pd.notna(ret_lb) and pd.notna(vol_lb) and vol_lb > 0:
                    ts_mom += ret_lb / vol_lb
                    valid_lbs += 1
        if valid_lbs > 0:
            ts_mom /= valid_lbs
        
        # Volatility penalty
        if pd.notna(last['realized_vol_5d']) and pd.notna(last['realized_vol_hist']) and last['realized_vol_hist'] > 0:
            if last['realized_vol_5d'] > 2 * last['realized_vol_hist']:
                ts_mom *= 0.5
                
        # 3. Short Term Reversal
        st_rev = 0.0
        if pd.notna(last['ret_3d']) and pd.notna(last['atr_3d']) and last['close'] > 0:
            ret_3d_abs_val = abs(last['close'] - df['close'].iloc[-4])
            if ret_3d_abs_val > 1.5 * last['atr_3d']:
                st_rev = -np.sign(last['ret_3d'])
                
        # 4. Volume Surprise
        vol_surp = 0.0
        if len(df) >= 20:
            mean_vol_20d = df['volume'].rolling(20).mean().iloc[-1]
            if mean_vol_20d > 0 and last['volume'] > 0 and pd.notna(last['ret_1d']):
                vol_surp = np.log(last['volume'] / mean_vol_20d) * np.sign(last['ret_1d'])
                
        # 5. CVD Divergence
        cvd_div = 0.0
        if 'cvd' in df.columns and len(df) >= 20:
            cvd_20d_mean = df['cvd'].rolling(20).mean().iloc[-1]
            cvd_20d_std = df['cvd'].rolling(20).std().iloc[-1]
            price_20d_mean = df['close'].rolling(20).mean().iloc[-1]
            price_20d_std = df['close'].rolling(20).std().iloc[-1]
            
            if cvd_20d_std > 0 and price_20d_std > 0:
                cvd_z = (last['cvd'] - cvd_20d_mean) / cvd_20d_std
                price_z = (last['close'] - price_20d_mean) / price_20d_std
                div = price_z - cvd_z
                if abs(div) > 1.5:
                    cvd_div = div

        # 6. Order Book Imbalance
        obi = 0.0
        if 'bid_vol' in df.columns and 'ask_vol' in df.columns:
            b_vol = last['bid_vol']
            a_vol = last['ask_vol']
            if pd.notna(b_vol) and pd.notna(a_vol) and (b_vol + a_vol) > 0:
                obi = (b_vol - a_vol) / (b_vol + a_vol)
                
        # 7. Liquidation Cascade Pressure
        liq_press = 0.0
        if 'liquidation_pressure' in df.columns and 'open_interest' in df.columns:
            liq = last['liquidation_pressure']
            oi = last['open_interest']
            if pd.notna(liq) and pd.notna(oi) and oi > 0:
                liq_press = liq / oi
                # Simple bounded normalization -> assume max normal is 5% OI liquidated
                liq_press = np.clip(liq_press / 0.05, -1.0, 1.0)
                
        # 8. Volatility Regime Ratio
        vol_reg_ratio_scaled = 0.0
        if pd.notna(last['realized_vol_5d']) and pd.notna(last['realized_vol_21d']) and last['realized_vol_21d'] > 0:
            ratio = last['realized_vol_5d'] / last['realized_vol_21d']
            ratio = np.clip(ratio, 0.2, 3.0)
            # scale: 1.0 -> 0. <1 -> negative, >1 -> positive. 
            # if 0.2 -> -1.0. if 3.0 -> +1.0 (approximate scaling)
            if ratio >= 1.0:
                vol_reg_ratio_scaled = (ratio - 1.0) / 2.0
            else:
                vol_reg_ratio_scaled = (ratio - 1.0) / 0.8
                
        # 9. Garman-Klass Volatility
        gk_vol_zscore = 0.0
        if len(df) >= 20:
            # log(H/L)^2
            hl = np.log(df['high'] / df['low'])**2
            # log(C/O)^2
            co = np.log(df['close'] / df['open'])**2
            gk_var = 0.5 * hl - (2 * np.log(2) - 1) * co
            gk_var = gk_var.clip(lower=0) # ensure positive
            df['gk_vol'] = np.sqrt(gk_var)
            
            gk_5d = df['gk_vol'].rolling(5).mean()
            gk_20d_mean = gk_5d.rolling(20).mean().iloc[-1]
            gk_20d_std = gk_5d.rolling(20).std().iloc[-1]
            if gk_20d_std > 0 and pd.notna(gk_5d.iloc[-1]):
                gk_vol_zscore = (gk_5d.iloc[-1] - gk_20d_mean) / gk_20d_std
                
        # 10. Vol Premium
        vol_prem = 0.0
        if 'implied_vol' in df.columns:
            iv = last['implied_vol']
            rv = last['realized_vol_21d']
            if pd.notna(iv) and pd.notna(rv):
                vol_prem = iv - rv
                
        # 11. Funding Rate Z-score
        fund_z = 0.0
        if 'funding_rate' in df.columns and len(df) >= 20:
            fund_mean = df['funding_rate'].rolling(20).mean().iloc[-1]
            fund_std = df['funding_rate'].rolling(20).std().iloc[-1]
            if fund_std > 0 and pd.notna(last['funding_rate']):
                z = (last['funding_rate'] - fund_mean) / fund_std
                if abs(z) > 2.0:
                    fund_z = z
                    
        # 12. OI Delta 4-State
        oi_4state = 0.0
        if 'open_interest' in df.columns and len(df) >= 2:
            oi_change = (last['open_interest'] / df['open_interest'].iloc[-2]) - 1
            if pd.notna(last['ret_1d']) and pd.notna(oi_change):
                if abs(last['ret_1d']) < 0.001:
                    oi_4state = 0.0
                elif last['ret_1d'] > 0:
                    oi_4state = 1.0 if oi_change > 0 else 0.5
                else:
                    oi_4state = -1.0 if oi_change > 0 else -0.5

        # Return parsed factors
        # Handle nan just in case
        return FactorScores(
            symbol=symbol,
            timestamp=float(df.index[-1].timestamp() if hasattr(df.index[-1], 'timestamp') else 0.0),
            cross_sectional_momentum=float(cs_mom_raw) if pd.notna(cs_mom_raw) else 0.0,
            time_series_momentum=float(ts_mom) if pd.notna(ts_mom) else 0.0,
            short_term_reversal=float(st_rev) if pd.notna(st_rev) else 0.0,
            volume_surprise=float(vol_surp) if pd.notna(vol_surp) else 0.0,
            cvd_divergence=float(cvd_div) if pd.notna(cvd_div) else 0.0,
            order_book_imbalance=float(obi) if pd.notna(obi) else 0.0,
            liquidation_cascade_pressure=float(liq_press) if pd.notna(liq_press) else 0.0,
            volatility_regime_ratio=float(vol_reg_ratio_scaled) if pd.notna(vol_reg_ratio_scaled) else 0.0,
            garman_klass_vol=float(gk_vol_zscore) if pd.notna(gk_vol_zscore) else 0.0,
            vol_premium=float(vol_prem) if pd.notna(vol_prem) else 0.0,
            funding_rate_zscore=float(fund_z) if pd.notna(fund_z) else 0.0,
            oi_delta_4state=float(oi_4state) if pd.notna(oi_4state) else 0.0,
        )

    def normalize_cross_sectional(self, factor_matrix: pd.DataFrame) -> pd.DataFrame:
        """Normalize cross-sectional ranks to [-1, 1] per column."""
        if factor_matrix.empty:
            return factor_matrix
        df = factor_matrix.copy()
        for col in df.columns:
            if df[col].dtype.kind in 'bifc':
                ranks = rankdata(df[col], method='average')
                if len(ranks) > 1:
                    df[col] = (ranks - 1) / (len(ranks) - 1) * 2 - 1
                else:
                    df[col] = 0.0
        return df

    def winsorize(self, factor_matrix: pd.DataFrame, limits=(0.025, 0.025)) -> pd.DataFrame:
        """Winsorize columns to remove extreme outliers."""
        if factor_matrix.empty:
            return factor_matrix
        df = factor_matrix.copy()
        for col in df.columns:
            if df[col].dtype.kind in 'bifc':
                # winsorize requires 1D array
                win_arr = winsorize(df[col].values, limits=limits)
                if isinstance(win_arr, np.ma.MaskedArray):
                    df[col] = win_arr.filled()
                else:
                    df[col] = win_arr
        return df

    def factor_correlations(self, factor_matrix: pd.DataFrame) -> pd.DataFrame:
        """Compute factor correlation matrix, log alerts if abs(corr) > 0.7."""
        if factor_matrix.empty or len(factor_matrix.columns) < 2 or len(factor_matrix) < 2:
            return pd.DataFrame()
        
        corr_matrix = factor_matrix.select_dtypes(include=[np.number]).corr()
        
        # Check upper triangle
        upper = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
        high_corr = []
        for i in range(len(upper.columns)):
            for j in range(i):
                val = upper.iloc[j, i]
                if pd.notna(val) and abs(val) > 0.7:
                    col_i = upper.columns[i]
                    col_j = upper.index[j]
                    high_corr.append((col_j, col_i, val))
                    
        if high_corr:
            logger.warning(f"[FactorEngine] HIGH CORRELATION DETECTED: {high_corr}")
            
        return corr_matrix
