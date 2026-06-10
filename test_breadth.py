import asyncio
from services.indicators.market_data import get_ohlcv
async def main():
    df = await get_ohlcv("BTC/USDT", "1d", 250)
    print("BTC rows:", len(df))
    if len(df) > 0:
        print("Last dates:")
        print(df['timestamp'].tail())
        print("Current Close:", df['close'].iloc[-1])
        print("EMA 200:", df['close'].rolling(200).mean().iloc[-1])
    else:
        print("Empty DF!")
asyncio.run(main())
