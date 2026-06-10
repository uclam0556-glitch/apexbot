import ccxt
import pandas as pd

exchange = ccxt.binanceusdm()
symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT', 'LINK/USDT', 'DOT/USDT', 'MATIC/USDT']
above = 0
valid = 0
for sym in symbols:
    try:
        ohlcv = exchange.fetch_ohlcv(sym, '1d', limit=250)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        if len(df) >= 200:
            valid += 1
            current = df['close'].iloc[-1]
            ema200 = df['close'].ewm(span=200, adjust=False).mean().iloc[-1]
            if current > ema200:
                above += 1
            print(f"{sym}: len={len(df)}, current={current:.2f}, ema200={ema200:.2f}, above={current>ema200}")
    except Exception as e:
        print(f"Error {sym}: {e}")

if valid > 0:
    print(f"Breadth: {above/valid*100:.1f}%")
