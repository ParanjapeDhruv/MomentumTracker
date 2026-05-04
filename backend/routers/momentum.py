from __future__ import annotations
from datetime import date, timedelta
from typing import Annotated
from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row
from ..database import db_conn
from ..schemas import Point, Response, Summary

router = APIRouter(tags=["momentum"])

MNT_SQL = """
WITH
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
),
joined_data AS (
    SELECT
        p.trade_date,
        p.open_price  AS open,
        p.high_price  AS high,
        p.low_price   AS low,
        p.close_price AS close,
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
)
SELECT
    *,
    COALESCE(ROUND(CORR(pct_change, daily_sentiment) OVER ()::NUMERIC, 5), 0) AS sentiment_price_correlation
FROM joined_data
ORDER BY trade_date ASC
"""

@router.get(
    "/momentum/{ticker}",
    response_model=Response,
    summary="7d rolling sentiment x price mnt",
)
async def fetch(
    ticker: str,
    days: Annotated[int, Query(ge=7, le=365)] = 30,
    end_date: Annotated[date | None, Query()] = None,
) -> Response:
    ticker = ticker.strip().upper()
    _end = end_date or date.today()
    _start = _end - timedelta(days=days)

    async with db_conn() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, company_name, sector FROM assets WHERE ticker = %s",
                (ticker,),
            )
            asset = await cur.fetchone()

        if not asset:
            raise HTTPException(404, f"ticker {ticker} not found")

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                MNT_SQL,
                {"ticker": ticker, "start_date": _start, "end_date": _end},
            )
            rows = await cur.fetchall()

    if not rows:
        raise HTTPException(404, "no data found")

    points = [Point(**r) for r in rows]
    sent_series = [d.daily_sentiment for d in points if d.mention_count > 0]
    pct_changes = [d.pct_change for d in points if d.pct_change != 0.0]

    summary = Summary(
        ticker=ticker,
        company_name=asset["company_name"],
        sector=asset["sector"],
        period_start=_start,
        period_end=_end,
        avg_sentiment=(sum(sent_series) / len(sent_series) if sent_series else 0.0),
        total_mentions=sum(d.mention_count for d in points),
        price_momentum=(sum(pct_changes) / len(pct_changes) if pct_changes else 0.0),
        latest_close=points[-1].close if points else 0.0,
        sentiment_price_correlation=float(rows[0]["sentiment_price_correlation"]),
    )

    return Response(summary=summary, data=points)
