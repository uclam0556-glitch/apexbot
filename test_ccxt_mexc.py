import asyncio
import ccxt.async_support as ccxt_async

async def fetch():
    exchange = ccxt_async.mexc()
    tickers = await exchange.fetch_tickers()
    print("Keys count:", len(tickers.keys()))
    print("BTC/USDT:", tickers.get("BTC/USDT", {}).get("last"))
    await exchange.close()

asyncio.run(fetch())
