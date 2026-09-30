import asyncpg
import os

PG_HOST = os.environ.get("PG_HOST")
PG_PORT = os.environ.get("PG_PORT")
PG_USER = os.environ.get("PG_USER")
PG_PASS = os.environ.get("PG_PASS")
PG_DB = os.environ.get("PG_DB")

_pool: asyncpg.Pool | None = None

async def connect() -> None:
    global _pool

    _pool = await asyncpg.create_pool(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASS,
        database=PG_DB
    )


async def disconnect() -> None:
    global _pool

    if _pool is not None:
        await _pool.close()
        _pool = None

async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Postgres pool has not been initialised")
    return _pool

