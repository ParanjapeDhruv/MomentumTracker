from __future__ import annotations
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import psycopg
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/sentiment_tracker",
)
_MIN = int(os.environ.get("DB_POOL_MIN", "2"))
_MAX = int(os.environ.get("DB_POOL_MAX", "10"))
_pool: AsyncConnectionPool | None = None

async def pool_init() -> None:
    global _pool
    logger.info("init pool: %s", _URL.split("@")[-1])
    _pool = AsyncConnectionPool(
        conninfo=_URL,
        min_size=_MIN,
        max_size=_MAX,
        open=False,
        max_idle=300,
        reconnect_failed=_on_reconnect_fail,
    )
    await _pool.open(wait=True, timeout=15.0)

async def pool_close() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

def _on_reconnect_fail(pool: AsyncConnectionPool) -> None:
    logger.critical("pool reconnect failed")

@asynccontextmanager
async def db_conn() -> AsyncGenerator[psycopg.AsyncConnection, None]:
    if not _pool:
        raise RuntimeError("pool not init")
    async with _pool.connection() as conn:
        yield conn

async def db_health() -> dict[str, object]:
    if not _pool:
        return {"status": "error", "detail": "not init"}
    stats = _pool.get_stats()
    try:
        async with db_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
        return {
            "status": "ok",
            "pool_min": _MIN,
            "pool_max": _MAX,
            "pool_available": stats.get("pool_available"),
            "pool_size": stats.get("pool_size"),
            "requests_waiting": stats.get("requests_waiting"),
        }
    except Exception as e:
        logger.error("health check fail: %s", e)
        return {"status": "error", "detail": str(e)}
