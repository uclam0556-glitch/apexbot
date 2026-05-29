import aiohttp
import structlog
from datetime import datetime
from typing import List, Dict

logger = structlog.get_logger(__name__)

class RSMatrix:
    """
    Relative Strength Matrix
    Fetches 24h price change for all Binance Futures pairs.
    Calculates Relative Strength vs BTC.
    Ranks the coins.
    """
    def __init__(self):
        self.matrix: List[Dict] = []
        self.last_updated = None
        self.btc_change = 0.0

    async def update_matrix(self, symbol_list: List[str]):
        """
        Fetches 24h ticker data and ranks the provided symbols.
        """
        try:
            url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status}")
                    data = await resp.json()
                    
            # Create a lookup dictionary
            ticker_map = {item["symbol"]: {
                "change": float(item["priceChangePercent"]),
                "price": float(item["lastPrice"])
            } for item in data}
            
            # Get BTC change
            btc_data = ticker_map.get("BTCUSDT", {"change": 0.0})
            self.btc_change = btc_data["change"]
            
            # Filter and calculate RS
            scored_symbols = []
            for symbol in symbol_list:
                binance_symbol = symbol.replace("/", "")
                data_point = ticker_map.get(binance_symbol, {"change": 0.0, "price": 0.0})
                change_24h = data_point["change"]
                price = data_point["price"]
                
                # Relative Strength: How much it outperformed BTC
                rs_score = change_24h - self.btc_change
                
                scored_symbols.append({
                    "symbol": symbol,
                    "change_24h": change_24h,
                    "price": price,
                    "rs_score": rs_score
                })
                
            # Sort by RS Score descending (Strongest first)
            scored_symbols.sort(key=lambda x: x["rs_score"], reverse=True)
            
            # Assign ranks
            for i, item in enumerate(scored_symbols):
                item["rank"] = i + 1
                
            self.matrix = scored_symbols
            self.last_updated = datetime.utcnow()
            
            logger.info(f"RS Matrix updated. BTC: {self.btc_change:+.2f}%. Top: {scored_symbols[0]['symbol']} (+{scored_symbols[0]['change_24h']:+.2f}%)")
            
        except Exception as e:
            logger.error(f"Failed to update RS Matrix: {e}")

    def get_rank(self, symbol: str) -> int:
        """Returns the rank of a symbol. 1 = Strongest."""
        if not self.matrix:
            return 1 # Fallback if not loaded
            
        for item in self.matrix:
            if item["symbol"] == symbol:
                return item["rank"]
        return 999
        
    def get_top_n(self, n: int = 5) -> List[Dict]:
        """Returns the Top N strongest coins."""
        return self.matrix[:n]
        
    async def fast_price_poller(self, symbol_list: List[str]):
        """
        Ultra-fast background poller for live dashboard updates.
        Fetches prices from Binance REST API every 3 seconds.
        This is extremely lightweight (Weight: 2) and 100% reliable.
        """
        from shared.state import global_state
        url = "https://fapi.binance.com/fapi/v1/ticker/price"
        
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            prices = {item["symbol"]: float(item["price"]) for item in data}
                            
                            for symbol in symbol_list:
                                binance_symbol = symbol.replace("/", "")
                                if binance_symbol in prices:
                                    # Update global state directly
                                    if symbol not in global_state.live_prices:
                                        global_state.live_prices[symbol] = {}
                                    global_state.live_prices[symbol]["price"] = prices[binance_symbol]
                                    
            except Exception as e:
                logger.debug(f"Fast poller error: {e}")
                
            await asyncio.sleep(3)

# Global instance
rs_matrix_engine = RSMatrix()
