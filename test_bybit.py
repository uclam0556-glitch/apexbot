import asyncio
import aiohttp

async def fetch():
    url = "https://api.bybit.com/v5/market/tickers?category=linear"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=5, ssl=False) as resp:
            print("Status:", resp.status)
            data = await resp.json()
            if data.get("retCode") == 0 and "result" in data and "list" in data["result"]:
                lst = data["result"]["list"]
                print("Count:", len(lst))
                if len(lst) > 0:
                    print("Sample:", lst[0])

asyncio.run(fetch())
