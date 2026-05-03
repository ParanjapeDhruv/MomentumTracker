"""
main.py — FastAPI application entry point.

Run locally:
    uvicorn backend.main:app --reload --port 8000

Production:
    gunicorn backend.main:app -k uvicorn.workers.UvicornWorker -w 4
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import check_db_health, close_pool, init_pool
from .routers import momentum
from .schemas import HealthResponse

# ------------------------------------------------------------------
# Logging — structured, levelled
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Application lifespan — pool init / teardown
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Application startup — initialising DB pool...")
    await init_pool()
    logger.info("Startup complete.")
    yield
    logger.info("Application shutdown — draining DB pool...")
    await close_pool()
    logger.info("Shutdown complete.")


# ------------------------------------------------------------------
# Application factory
# ------------------------------------------------------------------

app = FastAPI(
    title="Sentiment Momentum Tracker API",
    description=(
        "Ingests social/news sentiment, applies FinBERT scoring, "
        "and surfaces directional momentum probabilities per ticker."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS — tighten origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:4173",  # Vite preview
        "http://127.0.0.1:5173",  # Vite dev server via loopback IP
        "http://127.0.0.1:4173",  # Vite preview via loopback IP
    ],
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# Request timing middleware
# ------------------------------------------------------------------

@app.middleware("http")
async def add_process_time_header(request: Request, call_next) -> Response:
    start   = time.perf_counter()
    response = await call_next(request)
    elapsed = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Process-Time-Ms"] = str(elapsed)
    return response


# ------------------------------------------------------------------
# Global exception handler — never leak stack traces to clients
# ------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Check server logs."},
    )


# ------------------------------------------------------------------
# Health / readiness
# ------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health() -> HealthResponse:
    stats = await check_db_health()
    return HealthResponse(**stats)


# ------------------------------------------------------------------
# Routers
# ------------------------------------------------------------------

app.include_router(momentum.router, prefix="/api/v1")
