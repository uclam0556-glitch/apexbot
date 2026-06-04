"""
APEX Trading System v10.5
core/correlation_filter.py
"""

import numpy as np

class CorrelationFilter:
    """
    Блокирует открытие новой позиции если она высококоррелирована
    с уже открытыми позициями.
    """

    LOOKBACK_DAYS = 30
    MAX_CORRELATION = 0.75  # Выше — блокировать
    MAX_PORTFOLIO_BETA = 2.5  # Суммарная бета к BTC

    async def check(
        self,
        candidate_symbol: str,
        open_positions: list[str],
        ohlcv_data: dict
    ) -> dict:
        """
        Returns: {'allowed': bool, 'reason': str, 'max_correlation': float}
        """
        if not open_positions:
            return {'allowed': True, 'reason': 'NO_OPEN_POSITIONS', 'max_correlation': 0.0}

        # Рассчитать дневные доходности за 30 дней
        candidate_returns = self._get_returns(candidate_symbol, ohlcv_data, '1d')

        max_corr = 0.0
        most_correlated = None

        for existing_symbol in open_positions:
            existing_returns = self._get_returns(existing_symbol, ohlcv_data, '1d')

            # Pearson correlation
            min_len = min(len(candidate_returns), len(existing_returns), self.LOOKBACK_DAYS)
            if min_len < 10:
                continue  # недостаточно данных

            # np.corrcoef returns a 2x2 matrix, [0,1] is the correlation
            corr_matrix = np.corrcoef(
                candidate_returns[-min_len:],
                existing_returns[-min_len:]
            )
            
            # check if calculation returned valid numbers
            if np.isnan(corr_matrix).any():
                continue
                
            corr = corr_matrix[0, 1]

            if abs(corr) > max_corr:
                max_corr = abs(corr)
                most_correlated = existing_symbol

        if max_corr > self.MAX_CORRELATION:
            return {
                'allowed': False,
                'reason': f'HIGH_CORRELATION_{most_correlated}_{max_corr:.2f}',
                'max_correlation': max_corr
            }

        # Проверить суммарную бета к BTC
        portfolio_beta = self._calculate_portfolio_beta(
            [candidate_symbol] + open_positions, ohlcv_data
        )
        if portfolio_beta > self.MAX_PORTFOLIO_BETA:
            return {
                'allowed': False,
                'reason': f'PORTFOLIO_BETA_TOO_HIGH_{portfolio_beta:.2f}',
                'max_correlation': max_corr
            }

        return {'allowed': True, 'reason': 'CORRELATION_OK', 'max_correlation': max_corr}

    def _get_returns(self, symbol: str, data: dict, tf: str) -> np.ndarray:
        if symbol not in data or tf not in data[symbol]:
            return np.array([])
        closes = np.array([c['close'] for c in data[symbol][tf]])
        if len(closes) < 2:
            return np.array([])
        return np.diff(closes) / closes[:-1]

    def _calculate_portfolio_beta(self, symbols: list, data: dict) -> float:
        btc_returns = self._get_returns('BTCUSDT', data, '1d')
        if len(btc_returns) < 10:
            return 1.0 * len(symbols) # Default 1 beta per symbol
            
        btc_var = np.var(btc_returns)
        if btc_var == 0:
            return 1.0 * len(symbols)

        betas = []
        for symbol in symbols:
            if symbol == 'BTCUSDT':
                betas.append(1.0)
                continue
            sym_returns = self._get_returns(symbol, data, '1d')
            min_len = min(len(btc_returns), len(sym_returns))
            if min_len < 10:
                betas.append(1.0)
                continue
                
            cov_matrix = np.cov(sym_returns[-min_len:], btc_returns[-min_len:])
            if np.isnan(cov_matrix).any():
                betas.append(1.0)
                continue
                
            cov = cov_matrix[0, 1]
            betas.append(cov / btc_var)
            
        return sum(betas)
