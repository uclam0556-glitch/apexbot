"""
Tests for Phase 8 - OrderRouter
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from services.execution.order_router import OrderRouter, ExecutionRequest
from services.execution.transaction_cost_model import OrderUrgency

@pytest.fixture
def mock_exchange():
    exchange = MagicMock()
    # AsyncMock for CCXT methods
    exchange.create_order = AsyncMock(return_value={"id": "order_123", "status": "open", "average": 100.5})
    return exchange

@pytest.mark.asyncio
async def test_calculate_aggressive_limit(mock_exchange):
    router = OrderRouter(mock_exchange, is_live=True)
    
    # 15 bps aggressive for HIGH urgency LONG
    price_long = router._calculate_aggressive_limit(100.0, "LONG", OrderUrgency.HIGH)
    assert price_long == 100.15 # 100 * (1 + 0.0015)
    
    # 15 bps aggressive for HIGH urgency SHORT
    price_short = router._calculate_aggressive_limit(100.0, "SHORT", OrderUrgency.HIGH)
    assert price_short == pytest.approx(99.85)
    
    # 5 bps aggressive for MEDIUM
    price_med = router._calculate_aggressive_limit(100.0, "LONG", OrderUrgency.MEDIUM)
    assert price_med == 100.05
    
    # 0 bps passive for LOW
    price_low = router._calculate_aggressive_limit(100.0, "LONG", OrderUrgency.LOW)
    assert price_low == 100.0

@pytest.mark.asyncio
async def test_submit_aggressive_entry(mock_exchange):
    router = OrderRouter(mock_exchange, is_live=True)
    req = ExecutionRequest(
        symbol="BTC/USDT",
        direction="LONG",
        amount=1.0,
        current_price=100.0,
        urgency=OrderUrgency.HIGH,
        stop_loss=90.0,
        take_profit=120.0
    )
    
    order = await router.submit_aggressive_entry(req)
    
    # Verify create_order was called with Limit and the offset price
    mock_exchange.create_order.assert_called_once_with(
        symbol="BTC/USDT",
        type="limit",
        side="buy",
        amount=1.0,
        price=100.15
    )
    assert order["id"] == "order_123"

@pytest.mark.asyncio
async def test_demo_mode_does_not_call_exchange(mock_exchange):
    router = OrderRouter(mock_exchange, is_live=False)
    req = ExecutionRequest(
        symbol="BTC/USDT",
        direction="LONG",
        amount=1.0,
        current_price=100.0,
        urgency=OrderUrgency.HIGH,
        stop_loss=90.0,
        take_profit=120.0
    )
    
    order = await router.submit_aggressive_entry(req)
    
    # Verify create_order was NOT called
    mock_exchange.create_order.assert_not_called()
    assert order["id"] == "demo_order_123"
