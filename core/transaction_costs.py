"""
APEX Trading System v10.5
core/transaction_costs.py
"""

class TransactionCostModel:
    """
    Реалистичная модель издержек для каждой монеты.
    Без этого расчёт R:R — фикция.
    """

    # Категории по ликвидности
    LIQUIDITY_TIERS = {
        'TIER_1':  {'volume_min': 100_000_000, 'slippage_pct': 0.05, 'spread_pct': 0.03},  # BTC, ETH
        'TIER_2':  {'volume_min': 50_000_000,  'slippage_pct': 0.10, 'spread_pct': 0.06},  # SOL, BNB
        'TIER_3':  {'volume_min': 10_000_000,  'slippage_pct': 0.20, 'spread_pct': 0.12},  # Средние
        'TIER_4':  {'volume_min': 1_000_000,   'slippage_pct': 0.40, 'spread_pct': 0.25},  # Малые
        'TIER_5':  {'volume_min': 0,            'slippage_pct': 0.80, 'spread_pct': 0.50},  # Микро
    }

    # Комиссии биржи (Binance spot)
    MAKER_FEE = 0.001  # 0.1%
    TAKER_FEE = 0.001  # 0.1%

    def get_tier(self, symbol: str, volume_24h_usd: float) -> str:
        for tier, params in self.LIQUIDITY_TIERS.items():
            if volume_24h_usd >= params['volume_min']:
                return tier
        return 'TIER_5'

    def calculate_round_trip_cost(
        self,
        symbol: str,
        entry_price: float,
        position_size_usd: float,
        volume_24h_usd: float,
        order_type: str = 'LIMIT'  # 'LIMIT' или 'MARKET'
    ) -> dict:
        """
        Возвращает полный расчёт издержек для входа + выхода.
        """
        tier = self.get_tier(symbol, volume_24h_usd)
        params = self.LIQUIDITY_TIERS[tier]

        # Рыночное воздействие (market impact)
        # Prevent division by zero
        market_impact = (position_size_usd / volume_24h_usd) * 0.1 if volume_24h_usd > 0 else 0.0

        # Slippage
        slippage = params['slippage_pct'] / 100
        if order_type == 'MARKET':
            slippage *= 2  # маркет-ордер двойной slippage

        # Спред
        spread = params['spread_pct'] / 100

        # Комиссия (вход + выход)
        fee = (self.MAKER_FEE if order_type == 'LIMIT' else self.TAKER_FEE) * 2

        total_cost_pct = slippage + spread + fee + market_impact

        return {
            'tier': tier,
            'slippage_pct': slippage * 100,
            'spread_pct': spread * 100,
            'fee_pct': fee * 100,
            'market_impact_pct': market_impact * 100,
            'total_cost_pct': total_cost_pct * 100,
            'breakeven_move_pct': total_cost_pct * 100,
        }

    def adjust_rr(self, raw_rr: float, total_cost_pct: float,
                  sl_distance_pct: float) -> float:
        """
        Корректирует R:R с учётом реальных издержек.
        При R:R=1.5, SL=2%, costs=0.3%:
        fee_adjusted_rr = (3% - 0.3%) / (2% + 0.3%) = 1.17
        """
        tp_distance_pct = raw_rr * sl_distance_pct
        net_tp = tp_distance_pct - total_cost_pct
        net_sl = sl_distance_pct + total_cost_pct
        if net_sl <= 0:
            return 0.0
        return round(net_tp / net_sl, 3)

# Минимальный объём для включения монеты в мониторинг:
MINIMUM_VOLUME_24H_USD = 500_000  # $500k/сутки
# Ниже этого порога SMC и CVD теряют смысл
