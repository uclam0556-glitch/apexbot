import asyncio
import os
import ccxt.async_support as ccxt
import structlog

logger = structlog.get_logger("sandbox")

async def test_stop_market():
    logger.info("Initializing Sandbox for Spot Stop-Market Parity Checks")
    
    # NOTE: Set keys in ENV to test against real paper/live accounts
    mexc = ccxt.mexc({
        'apiKey': os.getenv('MEXC_API_KEY', 'TEST'),
        'secret': os.getenv('MEXC_API_SECRET', 'TEST'),
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
    
    bybit = ccxt.bybit({
        'apiKey': os.getenv('BYBIT_API_KEY', 'TEST'),
        'secret': os.getenv('BYBIT_API_SECRET', 'TEST'),
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })

    symbol = 'BTC/USDT'
    test_amount = 0.001
    test_stop_price = 60000.0

    logger.info("--- MEXC SPOT STOP-MARKET TEST ---")
    try:
        if os.getenv('MEXC_API_KEY'):
            # MEXC often requires stopPrice for stop market
            order = await mexc.create_order(
                symbol, "stop", "sell", test_amount, None,
                params={'stopPrice': test_stop_price}
            )
            logger.info("MEXC Stop Market created successfully", order=order)
        else:
            logger.info("MEXC Skipped: No API Key provided.")
    except Exception as e:
        logger.error("MEXC Error", error=str(e))
        
    logger.info("--- BYBIT SPOT STOP-MARKET TEST ---")
    try:
        if os.getenv('BYBIT_API_KEY'):
            # Bybit often requires triggerPrice for stop market
            order = await bybit.create_order(
                symbol, "stop", "sell", test_amount, None,
                params={'triggerPrice': test_stop_price}
            )
            logger.info("BYBIT Stop Market created successfully", order=order)
        else:
            logger.info("BYBIT Skipped: No API Key provided.")
    except Exception as e:
        logger.error("BYBIT Error", error=str(e))

    await mexc.close()
    await bybit.close()

if __name__ == "__main__":
    asyncio.run(test_stop_market())
