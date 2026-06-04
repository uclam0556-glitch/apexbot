"""
APEX Trading System v10.5
analytics/statistical_validator.py
"""

import scipy.stats as stats
import numpy as np
import logging

logger = logging.getLogger(__name__)

class StatisticalValidator:
    """
    Математически доказывает, что торговая стратегия работает.
    Генерирует T-Statistic, Profit Factor и Sharpe.
    """
    
    @staticmethod
    def run_validation(trades: list[dict]) -> dict:
        """
        trades: список словарей с ключом 'pnl_pct'
        """
        if len(trades) < 30:
            return {'valid': False, 'reason': 'INSUFFICIENT_DATA', 'n': len(trades)}
            
        pnls = [t['pnl_pct'] for t in trades]
        pnl_array = np.array(pnls)
        
        # 1. Expectancy
        win_trades = pnl_array[pnl_array > 0]
        loss_trades = pnl_array[pnl_array <= 0]
        
        win_rate = len(win_trades) / len(pnl_array)
        avg_win = np.mean(win_trades) if len(win_trades) > 0 else 0
        avg_loss = abs(np.mean(loss_trades)) if len(loss_trades) > 0 else 0
        
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        
        # 2. Profit Factor
        gross_profit = np.sum(win_trades) if len(win_trades) > 0 else 0
        gross_loss = abs(np.sum(loss_trades)) if len(loss_trades) > 0 else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # 3. T-Test (Отличается ли средний PnL от 0 статистически значимо?)
        t_stat, p_value = stats.ttest_1samp(pnl_array, 0.0)
        
        # 4. Sharpe Ratio (Daily simplified)
        # Упрощенно: средний PnL на сделку / StdDev PnL
        std_pnl = np.std(pnl_array)
        sharpe = (np.mean(pnl_array) / std_pnl) if std_pnl > 0 else 0
        
        is_valid = (
            p_value < 0.05 and 
            t_stat > 2.0 and 
            expectancy > 0 and 
            profit_factor > 1.2
        )
        
        return {
            'valid': is_valid,
            'n_trades': len(trades),
            'win_rate': round(win_rate * 100, 2),
            'expectancy': round(expectancy, 3),
            'profit_factor': round(profit_factor, 2),
            't_stat': round(t_stat, 2),
            'p_value': round(p_value, 4),
            'sharpe': round(sharpe, 2)
        }
