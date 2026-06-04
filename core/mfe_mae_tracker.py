"""
APEX Trading System v10.5
core/mfe_mae_tracker.py
"""

from typing import Dict, Optional

class MFEMAETracker:
    """
    Отслеживает Maximum Favorable Excursion (MFE) и
    Maximum Adverse Excursion (MAE) для активных сделок
    в реальном времени, а не постфактум.
    """
    
    def __init__(self):
        # map: signal_id -> dict
        self.active_trades: Dict[str, dict] = {}
        
    def register_trade(self, signal_id: str, entry_price: float, direction: str):
        self.active_trades[signal_id] = {
            'entry_price': entry_price,
            'direction': direction,
            'max_price': entry_price,
            'min_price': entry_price,
            'mfe_pct': 0.0,
            'mae_pct': 0.0,
        }
        
    def update_price(self, signal_id: str, current_price: float):
        if signal_id not in self.active_trades:
            return
            
        trade = self.active_trades[signal_id]
        
        # Обновляем экстремумы
        if current_price > trade['max_price']:
            trade['max_price'] = current_price
        if current_price < trade['min_price']:
            trade['min_price'] = current_price
            
        # Расчет MFE/MAE (MFE всегда положительный, MAE всегда отрицательный)
        entry = trade['entry_price']
        
        if trade['direction'] == 'LONG':
            trade['mfe_pct'] = (trade['max_price'] - entry) / entry * 100.0
            trade['mae_pct'] = (trade['min_price'] - entry) / entry * 100.0
        else:
            trade['mfe_pct'] = (entry - trade['min_price']) / entry * 100.0
            trade['mae_pct'] = (entry - trade['max_price']) / entry * 100.0
            
    def get_excursions(self, signal_id: str) -> Optional[dict]:
        return self.active_trades.get(signal_id)
        
    def remove_trade(self, signal_id: str):
        if signal_id in self.active_trades:
            del self.active_trades[signal_id]
