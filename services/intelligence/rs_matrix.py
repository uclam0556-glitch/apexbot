import asyncio
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
            url = "https://api.bybit.com/v5/market/tickers?category=linear"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status} from Bybit")
                    data = await resp.json()
                    
            if data.get("retCode") != 0 or "result" not in data or "list" not in data["result"]:
                raise Exception("Invalid Bybit response format")
                
            # Create a lookup dictionary
            ticker_map = {item["symbol"]: {
                "change": float(item["price24hPcnt"]) * 100, # Bybit returns 0.02 for 2%
                "price": float(item["lastPrice"])
            } for item in data["result"]["list"]}
            
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
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        
        loop_count = 0
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("retCode") == 0 and "result" in data and "list" in data["result"]:
                                prices = {}
                                for item in data["result"]["list"]:
                                    try:
                                        if item.get("lastPrice"):
                                            prices[item["symbol"]] = float(item["lastPrice"])
                                    except ValueError:
                                        continue
                                
                                for bybit_symbol, price in prices.items():
                                    if bybit_symbol.endswith("USDT"):
                                        sym = bybit_symbol.replace("USDT", "/USDT")
                                        if sym not in global_state.live_prices:
                                            global_state.live_prices[sym] = {}
                                        global_state.live_prices[sym]["price"] = price
                                        
                                loop_count += 1
                                if loop_count % 10 == 0:
                                    btc_price = global_state.live_prices.get("BTC/USDT", {}).get("price", 0)
                                    logger.info(f"Fast poller heartbeat. BTC: {btc_price}, Tracking {len(prices)} pairs.")
                        else:
                            logger.error(f"Fast poller received HTTP {resp.status} from Bybit")
                            
            except Exception as e:
                logger.error(f"Fast poller error (Bybit): {e}")
                
            await asyncio.sleep(2)

# Global instance
rs_matrix_engine = RSMatrix()
