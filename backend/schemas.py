"""
schemas.py — Pydantic v2 response / request models.

All numeric fields that come from NUMERIC(14,4) columns are typed as float
after explicit Python-side conversion.  No ORM magic — rows are cast
manually in the router.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


# ------------------------------------------------------------------
# Daily data point — one row from the momentum composite SQL query
# ------------------------------------------------------------------

class DailyMomentumPoint(BaseModel):
    trade_date: date

    # OHLCV
    open:   float = Field(..., description="Opening price")
    high:   float = Field(..., description="Intraday high")
    low:    float = Field(..., description="Intraday low")
    close:  float = Field(..., description="Closing price")
    volume: int

    # Price dynamics
    pct_change: float = Field(..., description="Daily close-to-close % change")

    # Sentiment — all in [-1, 1] or [0, ∞) for counts
    daily_sentiment:       Annotated[float, Field(ge=-1.0, le=1.0)]
    rolling_7d_sentiment:  Annotated[float, Field(ge=-1.0, le=1.0)]
    mention_count:         int = Field(..., ge=0)
    rolling_7d_mentions:   int = Field(..., ge=0)
    positive_count:        int = Field(..., ge=0)
    negative_count:        int = Field(..., ge=0)
    neutral_count:         int = Field(..., ge=0)
    sentiment_volatility:  float = Field(..., ge=0.0)

    @field_validator("pct_change", "daily_sentiment", "rolling_7d_sentiment", mode="before")
    @classmethod
    def coerce_none_to_zero(cls, v: object) -> float:
        return float(v) if v is not None else 0.0


# ------------------------------------------------------------------
# Summary header — computed from the full series
# ------------------------------------------------------------------

class MomentumSummary(BaseModel):
    ticker:       str
    company_name: str | None
    sector:       str | None

    period_start: date
    period_end:   date

    avg_sentiment:               float
    total_mentions:              int
    price_momentum:              float  # mean daily pct_change over the window
    latest_close:                float
    sentiment_price_correlation: float  # Pearson r(sentiment_t, pct_change_t)

    # Derived signal buckets — useful for frontend colour coding
    @property
    def sentiment_label(self) -> str:
        if self.avg_sentiment >  0.15:
            return "BULLISH"
        if self.avg_sentiment < -0.15:
            return "BEARISH"
        return "NEUTRAL"


# ------------------------------------------------------------------
# Top-level API response
# ------------------------------------------------------------------

class MomentumResponse(BaseModel):
    summary: MomentumSummary
    data:    list[DailyMomentumPoint]


# ------------------------------------------------------------------
# Health check response
# ------------------------------------------------------------------

class HealthResponse(BaseModel):
    status:            str
    pool_min:          int   | None = None
    pool_max:          int   | None = None
    pool_available:    int   | None = None
    pool_size:         int   | None = None
    requests_waiting:  int   | None = None
    detail:            str   | None = None
