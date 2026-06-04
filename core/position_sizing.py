"""
APEX Trading System v10.5
core/position_sizing.py
"""

class KellyPositionSizer:
    """
    Математически обоснованный сайзинг вместо фиксированного риска.
    Использует Quarter-Kelly для защиты от ошибок оценки.
    """

    def __init__(self, kelly_fraction: float = 0.25, max_risk_pct: float = 0.05):
        self.kelly_fraction = kelly_fraction
        self.max_risk_pct = max_risk_pct

    def calculate_size(
        self,
        capital: float,
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        stop_loss_pct: float,
        is_bootstrap: bool = False
    ) -> float:
        """
        capital: текущий капитал
        win_rate: 0.0 to 1.0
        avg_win_pct: средний профит (в процентах, > 0)
        avg_loss_pct: средний убыток (в процентах, > 0)
        stop_loss_pct: размер стоп лосса текущей сделки (в процентах, > 0)
        """
        # Если мало данных (фаза сбора) — минимальный безопасный лот
        if is_bootstrap or win_rate == 0 or avg_loss_pct == 0:
            risk_amount = capital * 0.005  # 0.5% risk
            size = risk_amount / stop_loss_pct if stop_loss_pct > 0 else 0
            return size

        # W = win_rate
        # R = avg_win / avg_loss (Reward to Risk ratio)
        r = avg_win_pct / avg_loss_pct
        if r <= 0:
            return 0.0

        # Full Kelly % = W - ((1 - W) / R)
        full_kelly_pct = win_rate - ((1.0 - win_rate) / r)

        if full_kelly_pct <= 0:
            # Стратегия имеет отрицательное матожидание!
            return 0.0

        # Fractional Kelly
        fractional_kelly = full_kelly_pct * self.kelly_fraction

        # Ограничение максимального риска на одну сделку
        fractional_kelly = min(fractional_kelly, self.max_risk_pct)

        # fractional_kelly — это процент капитала, который мы можем РИСКОВАТЬ в сделке.
        # Размер позиции = Риск / СтопЛос
        risk_amount = capital * fractional_kelly
        position_size = risk_amount / stop_loss_pct if stop_loss_pct > 0 else 0

        return position_size
