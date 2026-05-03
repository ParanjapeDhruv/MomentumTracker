"""
database.py — psycopg3 async connection pool.

Uses psycopg_pool.AsyncConnectionPool (separate package: pip install psycopg-pool).
The pool is initialised inside FastAPI's lifespan handler so the event loop
is already running when we call pool.open().
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import psycopg
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Configuration — all sourced from env so no secrets in code.
# Full DSN: postgresql://user:pass@host:5432/dbname
# ------------------------------------------------------------------
_DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/sentiment_tracker",
)

_POOL_MIN_SIZE: int = int(os.environ.get("DB_POOL_MIN", "2"))
_POOL_MAX_SIZE: int = int(os.environ.get("DB_POOL_MAX", "10"))

# Global pool instance — assigned during lifespan startup.
_pool: AsyncConnectionPool | None = None


# ------------------------------------------------------------------
# Lifecycle helpers (called from FastAPI lifespan)
# ------------------------------------------------------------------

async def init_pool() -> None:
    """Open the connection pool.  Must be awaited inside a running event loop."""
    global _pool

    logger.info(
        "Opening DB pool: min=%d max=%d dsn=%s",
        _POOL_MIN_SIZE,
        _POOL_MAX_SIZE,
        _DATABASE_URL.split("@")[-1],  # log host/db only, not creds
    )

    _pool = AsyncConnectionPool(
        conninfo=_DATABASE_URL,
        min_size=_POOL_MIN_SIZE,
        max_size=_POOL_MAX_SIZE,
        # Don't open synchronously — we await open() below.
        open=False,
        # Recycle connections that have been idle > 5 min.
        max_idle=300,
        # If a connection goes bad, replace it automatically.
        reconnect_failed=_on_reconnect_failed,
    )

    await _pool.open(wait=True, timeout=15.0)
    logger.info("DB pool ready.")


async def close_pool() -> None:
    """Drain and close the pool.  Called on application shutdown."""
    global _pool
    if _pool is not None:
        logger.info("Closing DB pool...")
        await _pool.close()
        _pool = None
        logger.info("DB pool closed.")


def _on_reconnect_failed(pool: AsyncConnectionPool) -> None:
    """Callback invoked when the pool cannot reconnect to the DB."""
    logger.critical(
        "DB connection pool failed to reconnect — pool exhausted or DB is down. "
        "Size: %d/%d",
        pool.get_stats().get("pool_available", -1),
        pool.get_stats().get("pool_size", -1),
    )


# ------------------------------------------------------------------
# Dependency / context manager
# ------------------------------------------------------------------

@asynccontextmanager
async def get_db() -> AsyncGenerator[psycopg.AsyncConnection, None]:
    """
    Yield a checked-out async connection from the pool.

    Usage (inside a FastAPI endpoint):
        async with get_db() as conn:
            async with conn.cursor() as cur:
                await cur.execute(...)
    """
    if _pool is None:
        raise RuntimeError(
            "Database pool is not initialised. "
            "Ensure init_pool() is called during application startup."
        )

    async with _pool.connection() as conn:
        yield conn


# ------------------------------------------------------------------
# Utility: health check query
# ------------------------------------------------------------------

async def check_db_health() -> dict[str, object]:
    """Return pool stats + a quick SELECT 1 round-trip for health endpoints."""
    if _pool is None:
        return {"status": "error", "detail": "pool not initialised"}

    stats = _pool.get_stats()
    try:
        async with get_db() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
        return {
            "status": "ok",
            "pool_min": _POOL_MIN_SIZE,
            "pool_max": _POOL_MAX_SIZE,
            "pool_available": stats.get("pool_available"),
            "pool_size": stats.get("pool_size"),
            "requests_waiting": stats.get("requests_waiting"),
        }
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        return {"status": "error", "detail": str(exc)}
