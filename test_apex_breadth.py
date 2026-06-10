import asyncio
from main import ApexSystem

async def test():
    apex = ApexSystem()
    await apex.exchange.load_markets()
    syms = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT']
    valid = 0
    above = 0
    for sym in syms:
        df = await apex.fetch_market_data(sym, '1d', 250)
        if not df.empty and len(df) >= 200:
            valid += 1
            curr = df['close'].iloc[-1]
            ema200 = df['close'].rolling(200).mean().iloc[-1]
            if curr > ema200:
                above += 1
            print(f"{sym}: curr={curr:.2f}, ema200={ema200:.2f}, above={curr > ema200}")
        else:
            print(f"{sym}: empty or too short (len={len(df)})")
    if valid > 0:
        print(f"Breadth: {above/valid*100:.1f}%")
    await apex.exchange.close()

asyncio.run(test())
