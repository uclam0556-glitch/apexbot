import asyncio
async def test():
    pool = await __import__('database.timescaledb').timescaledb.get_pool()
    print("Pool acquired:", pool)
asyncio.run(test())
