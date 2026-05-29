"""
APEX v6.5 — Order Flow Imbalance (OFI) Engine
Replaces the mock OFI with real Order Book depth analysis.
Analyzes the Bid/Ask imbalance to detect spoofing and real liquidity.
"""
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

class OFIResult:
    def __init__(self, ofi_score: float, delta_usd: float, imbalance_pct: float):
        self.ofi_score = ofi_score
        self.delta_usd = delta_usd
        self.imbalance_pct = imbalance_pct

def calculate_orderbook_imbalance(orderbook: dict, depth: int = 20) -> OFIResult:
    """
    Calculates the Order Flow Imbalance (OFI) from a snapshot of the Order Book.
    
    Args:
        orderbook: The orderbook dictionary returned by ccxt (has 'bids' and 'asks')
        depth: How many levels deep to analyze (default 20 for top-of-book pressure)
        
    Returns:
        OFIResult containing the score (-1.0 to 1.0), the delta in USD, and imbalance percentage.
    """
    try:
        bids = orderbook.get('bids', [])[:depth]
        asks = orderbook.get('asks', [])[:depth]
        
        if not bids or not asks:
            return OFIResult(0.0, 0.0, 0.0)

        # Calculate total USD volume on bids and asks
        # CCXT orderbook format: [[price, size], [price, size], ...]
        bid_vol_usd = sum(price * size for price, size in bids)
        ask_vol_usd = sum(price * size for price, size in asks)
        
        total_vol = bid_vol_usd + ask_vol_usd
        if total_vol == 0:
            return OFIResult(0.0, 0.0, 0.0)
            
        delta_usd = bid_vol_usd - ask_vol_usd
        imbalance_pct = delta_usd / total_vol  # Range: -1.0 to +1.0
        
        # Scale to OFI score (-1.0 to 1.0)
        # If bids are 60% and asks 40%, imbalance_pct is (60-40)/100 = +0.20
        # Let's map > 0.3 to 1.0, < -0.3 to -1.0
        ofi_score = max(-1.0, min(1.0, imbalance_pct / 0.30))
        
        return OFIResult(ofi_score, delta_usd, imbalance_pct)
        
    except Exception as e:
        logger.error(f"Error calculating OFI: {e}")
        return OFIResult(0.0, 0.0, 0.0)
