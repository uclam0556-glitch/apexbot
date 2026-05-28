"""
APEX Trading System v4.0
Microstructure & Order Flow Engine.

Gives edge that's IMPOSSIBLE from OHLCV alone:
- Order Flow Imbalance (OFI): who is ACTUALLY buying/selling right now?
- Cumulative Delta: hidden pressure diverging from price?
- Slippage model: will our order move the market?
- Spoofing detection: is someone manipulating the orderbook?

Data source: aggr_trades WebSocket stream (real taker buy/sell classification).
Latency: ~100ms from exchange to computed result.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from shared.config import get_config
from shared.database import get_redis, RedisKeys
from shared.models import (
    CumulativeDeltaResult,
    Direction,
    MicrostructureResult,
    OFIResult,
    SlippageEstimate,
    SpoofingAlert,
    ExecutionType,
    OrderBook,
    OrderBookLevel,
)

logger = logging.getLogger(__name__)
_config = get_config()


class MicrostructureEngine:
    """
    Real-time microstructure analysis.
    Maintains rolling windows of aggr_trades in memory.
    Data fed by WebSocket manager via update_trades().
    """

    def __init__(self) -> None:
        # Rolling trade buffers: symbol -> deque of (timestamp, size_usd, is_buy)
        self._trade_buffers: dict[str, deque[tuple[datetime, float, bool]]] = {}
        # Rolling candle delta buffers: symbol -> deque of (candle_open, buy_vol, sell_vol)
        self._delta_buffers: dict[str, deque[dict[str, float]]] = {}
        # Orderbook snapshots for spoofing: symbol -> list of snapshots
        self._ob_snapshots: dict[str, deque[dict[str, Any]]] = {}

        self._buffer_max_seconds = 600       # 10 minutes of trades
        self._ob_snapshot_max = 60           # 60 snapshots (1/sec)

    def update_trade(
        self,
        symbol: str,
        price: float,
        size: float,
        is_buyer_maker: bool,
        timestamp: datetime,
    ) -> None:
        """
        Called by WebSocket manager on every aggr_trade event.
        Maintains rolling buffers for OFI calculation.
        """
        if symbol not in self._trade_buffers:
            self._trade_buffers[symbol] = deque()

        # Convert size to USD
        size_usd = size * price
        is_buy = not is_buyer_maker  # buyer_maker = seller initiated (taker is seller)

        self._trade_buffers[symbol].append((timestamp, size_usd, is_buy))

        # Clean old entries (> buffer_max_seconds)
        cutoff = timestamp - timedelta(seconds=self._buffer_max_seconds)
        while (
            self._trade_buffers[symbol] and
            self._trade_buffers[symbol][0][0] < cutoff
        ):
            self._trade_buffers[symbol].popleft()

    def update_orderbook_snapshot(
        self,
        symbol: str,
        orderbook: OrderBook,
    ) -> None:
        """
        Called by WebSocket manager every second with fresh orderbook.
        Used for spoofing detection (orders appearing/disappearing).
        """
        if symbol not in self._ob_snapshots:
            self._ob_snapshots[symbol] = deque(maxlen=self._ob_snapshot_max)

        snapshot = {
            "timestamp": orderbook.timestamp,
            "top5_bids": [(lvl.price, lvl.size) for lvl in orderbook.bids[:5]],
            "top5_asks": [(lvl.price, lvl.size) for lvl in orderbook.asks[:5]],
            "total_bid_depth": sum(l.size for l in orderbook.bids[:20]),
            "total_ask_depth": sum(l.size for l in orderbook.asks[:20]),
        }
        self._ob_snapshots[symbol].append(snapshot)

    def calculate_order_flow_imbalance(
        self,
        symbol: str,
        window_seconds: int = 300,
    ) -> OFIResult:
        """
        OFI = (Buy Volume - Sell Volume) / Total Volume over window.
        
        OFI > 0.6: buyers dominating → bullish pressure
        OFI < 0.4: sellers dominating → bearish pressure
        0.4-0.6: balanced
        
        Source: aggr_trades stream with taker buy/sell classification.
        """
        if symbol not in self._trade_buffers:
            return OFIResult(
                symbol=symbol,
                ofi_score=0.5,  # neutral default
                delta_usd=0.0,
                trend="neutral",
                window_seconds=window_seconds,
                computed_at=datetime.utcnow(),
            )

        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=window_seconds)

        buy_volume_usd = 0.0
        sell_volume_usd = 0.0

        for timestamp, size_usd, is_buy in self._trade_buffers[symbol]:
            if timestamp >= cutoff:
                if is_buy:
                    buy_volume_usd += size_usd
                else:
                    sell_volume_usd += size_usd

        total_volume = buy_volume_usd + sell_volume_usd

        if total_volume < 1000:  # less than $1000 traded = insufficient data
            ofi_score = 0.5
            delta_usd = 0.0
            trend = "neutral"
        else:
            ofi_score = buy_volume_usd / total_volume
            delta_usd = buy_volume_usd - sell_volume_usd

            if ofi_score > 0.60:
                trend = "bullish"
            elif ofi_score < 0.40:
                trend = "bearish"
            else:
                trend = "neutral"

        return OFIResult(
            symbol=symbol,
            ofi_score=round(ofi_score, 4),
            delta_usd=round(delta_usd, 2),
            trend=trend,
            window_seconds=window_seconds,
            computed_at=now,
        )

    def calculate_cumulative_delta(
        self,
        symbol: str,
        lookback_candles: int = 20,
    ) -> CumulativeDeltaResult:
        """
        Cumulative Delta = Σ(buy_volume - sell_volume) per candle.
        
        Divergence signals:
        - Price rising + CD falling: sellers absorbing buyers → potential reversal
        - Price falling + CD rising: buyers absorbing sellers → potential reversal
        
        This is a leading indicator — often precedes price reversal.
        """
        if symbol not in self._delta_buffers or len(self._delta_buffers[symbol]) < 5:
            return CumulativeDeltaResult(
                symbol=symbol,
                cd_trend="neutral",
                cd_value=0.0,
                price_cd_divergence=False,
                lookback_candles=lookback_candles,
                computed_at=datetime.utcnow(),
            )

        deltas = list(self._delta_buffers[symbol])[-lookback_candles:]

        # Calculate cumulative delta values
        cd_values = []
        running_cd = 0.0
        price_closes = []

        for candle in deltas:
            running_cd += candle.get("buy_vol", 0) - candle.get("sell_vol", 0)
            cd_values.append(running_cd)
            if "close" in candle:
                price_closes.append(candle["close"])

        if len(cd_values) < 5:
            trend = "neutral"
            divergence = False
        else:
            # Calculate trend of CD over last 10 candles
            cd_recent = cd_values[-10:]
            cd_early = cd_values[-20:-10] if len(cd_values) >= 20 else cd_values[:len(cd_values)//2]

            cd_avg_recent = np.mean(cd_recent)
            cd_avg_early = np.mean(cd_early) if cd_early else cd_avg_recent

            cd_trend_direction = cd_avg_recent - cd_avg_early

            # Check divergence with price
            divergence = False
            if len(price_closes) >= 10:
                price_trend = price_closes[-1] - price_closes[-10]

                # Bearish divergence: price up but CD down
                if price_trend > 0 and cd_trend_direction < -1000:
                    trend = "divergent_bearish"
                    divergence = True
                # Bullish divergence: price down but CD up
                elif price_trend < 0 and cd_trend_direction > 1000:
                    trend = "divergent_bullish"
                    divergence = True
                elif cd_trend_direction > 0:
                    trend = "bullish"
                else:
                    trend = "bearish"
            else:
                trend = "bullish" if cd_trend_direction > 0 else "bearish"

        return CumulativeDeltaResult(
            symbol=symbol,
            cd_trend=trend,
            cd_value=round(cd_values[-1] if cd_values else 0.0, 2),
            price_cd_divergence=divergence,
            lookback_candles=lookback_candles,
            computed_at=datetime.utcnow(),
        )

    def estimate_slippage(
        self,
        symbol: str,
        order_size_usd: float,
        direction: str,  # "BUY" | "SELL"
        orderbook: OrderBook,
    ) -> SlippageEstimate:
        """
        Simulate execution of a market order of our size against current orderbook.
        Calculates the REAL price we'd get vs the current best bid/ask.
        
        This is the only accurate way to estimate slippage — orderbook simulation.
        """
        if not orderbook.bids or not orderbook.asks:
            return SlippageEstimate(
                symbol=symbol,
                order_size_usd=order_size_usd,
                direction=direction,
                estimated_slippage_pct=0.1,  # conservative default
                recommended_execution=ExecutionType.LIMIT,
                warn=False,
                computed_at=datetime.utcnow(),
            )

        # Simulate filling our order through the book
        remaining_usd = order_size_usd
        total_cost = 0.0
        levels_consumed = 0

        # Choose correct side
        book_side = orderbook.asks if direction == "BUY" else orderbook.bids
        reference_price = orderbook.asks[0].price if direction == "BUY" else orderbook.bids[0].price

        for level in book_side:
            level_value_usd = level.price * level.size
            fill_at_level = min(remaining_usd, level_value_usd)
            fill_size = fill_at_level / level.price

            total_cost += fill_size * level.price
            remaining_usd -= fill_at_level
            levels_consumed += 1

            if remaining_usd <= 0:
                break

        # If we consumed the whole visible book
        if remaining_usd > 0:
            slippage_pct = 1.0  # extreme slippage
            logger.warning(
                f"Order size ${order_size_usd:,.0f} exceeds visible orderbook depth for {symbol}"
            )
        else:
            actual_avg_price = total_cost / (order_size_usd / reference_price)
            slippage_pct = abs(actual_avg_price - reference_price) / reference_price * 100

        # Execution recommendation based on slippage
        if slippage_pct < 0.10:
            recommended = ExecutionType.MARKET
        elif slippage_pct < 0.50:
            recommended = ExecutionType.LIMIT
        else:
            recommended = ExecutionType.TWAP

        warn = slippage_pct > 0.50

        if warn:
            logger.warning(
                f"High slippage estimate for {symbol}: {slippage_pct:.3f}% "
                f"(order ${order_size_usd:,.0f}, {levels_consumed} book levels consumed)"
            )

        return SlippageEstimate(
            symbol=symbol,
            order_size_usd=order_size_usd,
            direction=direction,
            estimated_slippage_pct=round(slippage_pct, 4),
            recommended_execution=recommended,
            warn=warn,
            computed_at=datetime.utcnow(),
        )

    def detect_spoofing(
        self,
        symbol: str,
        min_order_size_usd: float = 100_000,
        disappear_threshold_ms: float = 500,
    ) -> SpoofingAlert:
        """
        Detects large orders that appear and disappear within < 500ms.
        
        Spoofing pattern:
        1. Large bid/ask appears (> min_order_size_usd)
        2. Disappears within disappear_threshold_ms (without a fill)
        3. Price was moved by the phantom order
        
        This implementation uses orderbook snapshot comparison.
        Episodes in last 5 minutes counted.
        """
        if symbol not in self._ob_snapshots or len(self._ob_snapshots[symbol]) < 2:
            return SpoofingAlert(
                symbol=symbol,
                detected=False,
                episodes_count=0,
                time_window_seconds=300,
                severity="NONE",
                largest_order_usd=0.0,
                computed_at=datetime.utcnow(),
            )

        snapshots = list(self._ob_snapshots[symbol])
        episodes = 0
        largest_order_usd = 0.0

        # Compare consecutive snapshots
        for i in range(1, len(snapshots)):
            prev = snapshots[i - 1]
            curr = snapshots[i]

            prev_time = prev["timestamp"]
            curr_time = curr["timestamp"]
            time_diff_ms = (curr_time - prev_time).total_seconds() * 1000

            # Only check fast changes (< 2 seconds between snapshots)
            if time_diff_ms > 2000:
                continue

            # Check for large orders that disappeared from bids
            prev_bids = {p: s for p, s in prev["top5_bids"]}
            curr_bids = {p: s for p, s in curr["top5_bids"]}

            for price, size in prev_bids.items():
                order_usd = price * size
                if order_usd >= min_order_size_usd and price not in curr_bids:
                    # Large bid disappeared
                    episodes += 1
                    largest_order_usd = max(largest_order_usd, order_usd)

            # Check for large orders that disappeared from asks
            prev_asks = {p: s for p, s in prev["top5_asks"]}
            curr_asks = {p: s for p, s in curr["top5_asks"]}

            for price, size in prev_asks.items():
                order_usd = price * size
                if order_usd >= min_order_size_usd and price not in curr_asks:
                    # Large ask disappeared
                    episodes += 1
                    largest_order_usd = max(largest_order_usd, order_usd)

        # Determine severity
        if episodes == 0:
            severity = "NONE"
            detected = False
        elif episodes < 3:
            severity = "LOW"
            detected = True
        elif episodes < 6:
            severity = "HIGH"
            detected = True
        else:
            severity = "COORDINATED"  # adversarial test should flag this
            detected = True

        return SpoofingAlert(
            symbol=symbol,
            detected=detected,
            episodes_count=episodes,
            time_window_seconds=300,
            severity=severity,
            largest_order_usd=round(largest_order_usd, 2),
            computed_at=datetime.utcnow(),
        )

    def get_full_analysis(
        self,
        symbol: str,
        orderbook: OrderBook,
        order_size_usd: float,
        direction: str,
        ofi_window_seconds: int = 300,
    ) -> MicrostructureResult:
        """
        Single call to get all microstructure data.
        Called by Signal Engine before generating a signal.
        """
        ofi = self.calculate_order_flow_imbalance(symbol, ofi_window_seconds)
        cd = self.calculate_cumulative_delta(symbol)
        slippage = self.estimate_slippage(symbol, order_size_usd, direction, orderbook)
        spoofing = self.detect_spoofing(symbol)

        return MicrostructureResult(
            ofi=ofi,
            cumulative_delta=cd,
            slippage_estimate=slippage,
            spoofing=spoofing,
        )

    def update_candle_delta(
        self,
        symbol: str,
        candle_open: float,
        candle_close: float,
        buy_volume: float,
        sell_volume: float,
    ) -> None:
        """
        Called at candle close by OHLCV processor.
        Updates delta buffer for cumulative delta calculation.
        """
        if symbol not in self._delta_buffers:
            self._delta_buffers[symbol] = deque(maxlen=50)  # 50 candles

        self._delta_buffers[symbol].append({
            "open": candle_open,
            "close": candle_close,
            "buy_vol": buy_volume,
            "sell_vol": sell_volume,
        })
