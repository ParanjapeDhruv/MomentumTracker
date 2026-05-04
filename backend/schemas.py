from __future__ import annotations
from datetime import date
from typing import Annotated
from pydantic import BaseModel, Field, field_validator

class Point(BaseModel):
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    pct_change: float
    daily_sentiment: Annotated[float, Field(ge=-1.0, le=1.0)]
    rolling_7d_sentiment: Annotated[float, Field(ge=-1.0, le=1.0)]
    mention_count: int = Field(..., ge=0)
    rolling_7d_mentions: int = Field(..., ge=0)
    positive_count: int = Field(..., ge=0)
    negative_count: int = Field(..., ge=0)
    neutral_count: int = Field(..., ge=0)
    sentiment_volatility: float = Field(..., ge=0.0)

    @field_validator("pct_change", "daily_sentiment", "rolling_7d_sentiment", mode="before")
    @classmethod
    def coerce_float(cls, v: object) -> float:
        return float(v) if v is not None else 0.0

class Summary(BaseModel):
    ticker: str
    company_name: str | None
    sector: str | None
    period_start: date
    period_end: date
    avg_sentiment: float
    total_mentions: int
    price_momentum: float
    latest_close: float
    sentiment_price_correlation: float

    @property
    def label(self) -> str:
        if self.avg_sentiment > 0.15: return "BULLISH"
        if self.avg_sentiment < -0.15: return "BEARISH"
        return "NEUTRAL"

class Response(BaseModel):
    summary: Summary
    data: list[Point]

class Health(BaseModel):
    status: str
    pool_min: int | None = None
    pool_max: int | None = None
    pool_available: int | None = None
    pool_size: int | None = None
    requests_waiting: int | None = None
    detail: str | None = None
