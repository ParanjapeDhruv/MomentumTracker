"""
routers/momentum.py

/api/v1/momentum/{ticker}  — the core analytical endpoint.

Architecture decision: the heavy lifting is done in a single, optimised
SQL query using CTEs rather than pulling raw rows into Python for aggregation.
This keeps network traffic low and leverages PostgreSQL's window functions.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from ..database import get_db
from ..schemas import DailyMomentumPoint, MomentumResponse, MomentumSummary

router = APIRouter(tags=["momentum"])

# ------------------------------------------------------------------
# Core SQL — composite CTE that merges price + rolling sentiment.
#
# Why raw SQL instead of an ORM:
#   • Window functions (LAG, rolling AVG OVER …) are idiomatic SQL.
#   • CTEs keep each concern isolated and readable.
#   • Zero N+1 queries — everything is one round-trip.
# ------------------------------------------------------------------

_MOMENTUM_SQL = """
WITH
-- ── 1. Aggregate intraday price to daily OHLCV ──────────────────
daily_price_raw AS (
    SELECT
        DATE(ts AT TIME ZONE 'Asia/Kolkata') AS trade_date,
        (array_agg(open_price ORDER BY ts ASC))[1] AS open_price,
        MAX(high_price) AS high_price,
        MIN(low_price) AS low_price,
        (array_agg(close_price ORDER BY ts DESC))[1] AS close_price,
        SUM(volume) AS volume
    FROM price_history
    WHERE ticker = %(ticker)s
      AND ts >= %(start_date)s::TIMESTAMPTZ
      AND ts < (%(end_date)s::DATE + INTERVAL '1 day')::TIMESTAMPTZ
    GROUP BY DATE(ts AT TIME ZONE 'Asia/Kolkata')
),

price_data AS (
    SELECT
        trade_date,
        open_price,
        high_price,
        low_price,
        close_price,
        volume,
        ROUND(
            (
                close_price
                - LAG(close_price) OVER (ORDER BY trade_date)
            )
            / NULLIF(LAG(close_price) OVER (ORDER BY trade_date), 0)
            * 100,
            4
        ) AS pct_change
    FROM daily_price_raw
),

-- ── 2. Daily sentiment aggregates ───────────────────────────────
sentiment_daily AS (
    SELECT
        DATE(created_at AT TIME ZONE 'Asia/Kolkata')                              AS sentiment_date,
        COUNT(*)                                                          AS mention_count,
        ROUND(AVG(compound_score)::NUMERIC,   5)                         AS avg_compound,
        ROUND(STDDEV(compound_score)::NUMERIC, 5)                        AS compound_stddev,
        SUM(CASE WHEN compound_score >  0.05 THEN 1 ELSE 0 END)         AS pos_count,
        SUM(CASE WHEN compound_score < -0.05 THEN 1 ELSE 0 END)         AS neg_count,
        SUM(CASE WHEN ABS(compound_score) <= 0.05 THEN 1 ELSE 0 END)    AS neu_count
    FROM sentiment_logs
    WHERE ticker     = %(ticker)s
      AND created_at >= %(start_date)s::TIMESTAMPTZ
      AND created_at <  (%(end_date)s::DATE + INTERVAL '1 day')::TIMESTAMPTZ
    GROUP BY DATE(created_at AT TIME ZONE 'Asia/Kolkata')
),

-- ── 3. 7-day rolling windows on sentiment ───────────────────────
rolling_sentiment AS (
    SELECT
        sentiment_date,
        avg_compound,
        compound_stddev,
        mention_count,
        pos_count,
        neg_count,
        neu_count,
        ROUND(
            AVG(avg_compound) OVER (
                ORDER BY sentiment_date
                ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
            )::NUMERIC,
            5
        ) AS rolling_7d_sentiment,
        SUM(mention_count) OVER (
            ORDER BY sentiment_date
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        )                  AS rolling_7d_mentions
    FROM sentiment_daily
)

-- ── 4. Final join ────────────────────────────────────────────────
SELECT
    p.trade_date,
    p.open_price,
    p.high_price,
    p.low_price,
    p.close_price,
    p.volume,
    COALESCE(p.pct_change,              0)  AS pct_change,
    COALESCE(s.avg_compound,            0)  AS daily_sentiment,
    COALESCE(s.rolling_7d_sentiment,    0)  AS rolling_7d_sentiment,
    COALESCE(s.mention_count,           0)  AS mention_count,
    COALESCE(s.rolling_7d_mentions,     0)  AS rolling_7d_mentions,
    COALESCE(s.pos_count,               0)  AS positive_count,
    COALESCE(s.neg_count,               0)  AS negative_count,
    COALESCE(s.neu_count,               0)  AS neutral_count,
    COALESCE(s.compound_stddev,         0)  AS sentiment_volatility
FROM      price_data      p
LEFT JOIN rolling_sentiment s ON p.trade_date = s.sentiment_date
ORDER BY  p.trade_date ASC
"""


# ------------------------------------------------------------------
# Endpoint
# ------------------------------------------------------------------

@router.get(
    "/momentum/{ticker}",
    response_model=MomentumResponse,
    summary="7-day rolling sentiment × price momentum for a ticker",
)
async def get_momentum(
    ticker: str,
    days:     Annotated[int, Query(ge=7, le=365, description="Look-back window in trading days")] = 30,
    end_date: Annotated[date | None, Query(description="Inclusive end date (defaults to today)")] = None,
) -> MomentumResponse:
    ticker  = ticker.strip().upper()
    _end    = end_date or date.today()
    _start  = _end - timedelta(days=days)

    async with get_db() as conn:
        # ── Verify the ticker is registered ──────────────────────
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, company_name, sector FROM assets WHERE ticker = %s",
                (ticker,),
            )
            asset_row = await cur.fetchone()

        if not asset_row:
            raise HTTPException(
                status_code=404,
                detail=f"Ticker '{ticker}' not found. Ingest it first via the pipeline.",
            )

        # ── Execute the composite query ───────────────────────────
        async with conn.cursor() as cur:
            await cur.execute(
                _MOMENTUM_SQL,
                {"ticker": ticker, "start_date": _start, "end_date": _end},
            )
            col_names = [desc[0] for desc in cur.description]
            rows      = await cur.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No price history found for {ticker} "
                f"between {_start} and {_end}. "
                "Run the ingestion pipeline to populate data."
            ),
        )

    # ── Deserialise rows → Pydantic models ────────────────────────
    data_points: list[DailyMomentumPoint] = []
    for row in rows:
        r = dict(zip(col_names, row))
        data_points.append(
            DailyMomentumPoint(
                trade_date=r["trade_date"],
                open=float(r["open_price"]),
                high=float(r["high_price"]),
                low=float(r["low_price"]),
                close=float(r["close_price"]),
                volume=int(r["volume"]),
                pct_change=float(r["pct_change"]),
                daily_sentiment=float(r["daily_sentiment"]),
                rolling_7d_sentiment=float(r["rolling_7d_sentiment"]),
                mention_count=int(r["mention_count"]),
                rolling_7d_mentions=int(r["rolling_7d_mentions"]),
                positive_count=int(r["positive_count"]),
                negative_count=int(r["negative_count"]),
                neutral_count=int(r["neutral_count"]),
                sentiment_volatility=float(r["sentiment_volatility"]),
            )
        )

    # ── Compute summary statistics ────────────────────────────────
    sentiment_series = [d.daily_sentiment for d in data_points if d.mention_count > 0]
    pct_changes      = [d.pct_change      for d in data_points if d.pct_change != 0.0]

    summary = MomentumSummary(
        ticker=ticker,
        company_name=asset_row[1],
        sector=asset_row[2],
        period_start=_start,
        period_end=_end,
        avg_sentiment=(
            round(sum(sentiment_series) / len(sentiment_series), 5)
            if sentiment_series else 0.0
        ),
        total_mentions=sum(d.mention_count for d in data_points),
        price_momentum=(
            round(sum(pct_changes) / len(pct_changes), 5)
            if pct_changes else 0.0
        ),
        latest_close=data_points[-1].close if data_points else 0.0,
        sentiment_price_correlation=_pearson(
            [d.daily_sentiment for d in data_points],
            [d.pct_change      for d in data_points],
        ),
    )

    return MomentumResponse(summary=summary, data=data_points)


# ------------------------------------------------------------------
# Helper — Pearson r without NumPy / SciPy dependency
# ------------------------------------------------------------------

def _pearson(x: list[float], y: list[float]) -> float:
    """
    Pure-Python Pearson correlation coefficient.
    Returns 0.0 on degenerate input (n < 2, zero variance).
    """
    n = len(x)
    if n < 2 or len(y) != n:
        return 0.0

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    dx = [xi - mean_x for xi in x]
    dy = [yi - mean_y for yi in y]

    numerator = sum(a * b for a, b in zip(dx, dy))
    denom     = math.sqrt(sum(a * a for a in dx)) * math.sqrt(sum(b * b for b in dy))

    return round(numerator / denom, 5) if denom != 0.0 else 0.0
