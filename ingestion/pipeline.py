"""
ingestion/pipeline.py — Standalone data-ingestion script.

Responsibilities:
  1. Fetch OHLCV history via yfinance  →  price_history table
  2. Fetch Yahoo Finance News          →  run FinBERT  →  sentiment_logs table

Rate-limit handling strategy:
  • yfinance: No hard rate limit, but we add a polite inter-request delay and
              retry with exponential back-off on HTTP 429.
  • FinBERT: Local inference — no external rate limit, but we batch inputs to
             control GPU/CPU memory.

Run:
    TICKERS=AAPL,TSLA,NVDA python -m ingestion.pipeline
"""
from __future__ import annotations

import hashlib
import importlib
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
import yfinance as yf
from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/sentiment_tracker",
)

# ------------------------------------------------------------------
# Sentiment result container
# ------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class SentimentResult:
    positive: float   # FinBERT softmax probability for "positive"
    negative: float   # FinBERT softmax probability for "negative"
    neutral:  float   # FinBERT softmax probability for "neutral"
    compound: float   # Derived: positive - negative  ∈ [-1, 1]


# ------------------------------------------------------------------
# FinBERT wrapper
# ------------------------------------------------------------------

class FinBERTAnalyzer:
    """
    Wraps ProsusAI/finbert for single and batch inference.

    FinBERT label mapping (from model card):
        LABEL_0 → positive
        LABEL_1 → negative
        LABEL_2 → neutral

    Compound score = P(positive) − P(negative), which is a crisp
    directional float in [−1, 1] suitable for time-series aggregation.
    """

    MODEL_ID = "ProsusAI/finbert"
    # FinBERT was trained on financial texts up to 512 tokens.
    MAX_TOKENS = 512

    def __init__(self) -> None:
        logger.info("Loading FinBERT from %s …", self.MODEL_ID)
        tokenizer = AutoTokenizer.from_pretrained(self.MODEL_ID)
        model = AutoModelForSequenceClassification.from_pretrained(self.MODEL_ID)

        # pipeline() handles device placement automatically.
        self._pipe = pipeline(
            task="text-classification",
            model=model,
            tokenizer=tokenizer,
            top_k=None,  # Return all scores
            truncation=True,
            max_length=self.MAX_TOKENS,
            # device=0 for GPU; -1 forces CPU.  Auto-detect:
            device=_get_device_id(),
        )
        logger.info("FinBERT ready.")

    # ------------------------------------------------------------------

    def _parse_scores(self, raw_output: list[dict] | dict) -> SentimentResult:
        """Helper to extract scores from various pipeline output formats."""
        # If output is a single dict (some pipeline configs), wrap it
        items = raw_output if isinstance(raw_output, list) else [raw_output]
        
        scores = {item["label"].lower(): float(item["score"]) for item in items}
        pos = scores.get("positive", 0.0)
        neg = scores.get("negative", 0.0)
        neu = scores.get("neutral",  0.0)

        return SentimentResult(
            positive=round(pos, 5),
            negative=round(neg, 5),
            neutral= round(neu, 5),
            compound=round(pos - neg, 5),
        )

    def analyze(self, text: str) -> SentimentResult:
        """Single-item inference.  Returns neutral(0) for empty/short text."""
        text = text.strip()
        if len(text) < 15:
            return SentimentResult(0.0, 0.0, 1.0, 0.0)

        try:
            raw = self._pipe(text)
            # Pipeline for single text usually returns [[{...}, ...]] or [{...}, ...]
            return self._parse_scores(raw[0] if isinstance(raw, list) else raw)
        except Exception as exc:
            logger.warning("FinBERT inference failed: %s", exc)
            return SentimentResult(0.0, 0.0, 1.0, 0.0)

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        """
        Batch inference.  Skips empty texts and returns neutral placeholders.
        Falls back to single-item inference on any batch error.
        """
        if not texts:
            return []
            
        try:
            raw_batch = self._pipe(
                [t[:2000] for t in texts],  # hard-cap chars to avoid OOM
                batch_size=16,
            )
            # raw_batch is usually [[{...}, ...], ...]
            return [self._parse_scores(raw) for raw in raw_batch]
        except Exception as exc:
            logger.warning("Batch inference failed (%s), falling back to single.", exc)
            return [self.analyze(t) for t in texts]


# ------------------------------------------------------------------
# Token-bucket rate limiter (simple, no external deps)
# ------------------------------------------------------------------

class RateLimiter:
    """Ensures at most `calls_per_minute` API calls per minute."""

    def __init__(self, calls_per_minute: int) -> None:
        self._interval = 60.0 / calls_per_minute
        self._last     = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last = time.monotonic()


# ------------------------------------------------------------------
# Exponential back-off decorator
# ------------------------------------------------------------------

def with_exponential_backoff(
    func,
    *args,
    max_retries: int = 5,
    base_delay:  float = 1.0,
    **kwargs,
):
    """
    Call func(*args, **kwargs), retrying on Exception up to max_retries times.
    Delay doubles each attempt: 1s, 2s, 4s, 8s, 16s.
    """
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(
                "Attempt %d/%d failed (%s). Retrying in %.1fs …",
                attempt + 1, max_retries, exc, delay,
            )
            time.sleep(delay)


# ------------------------------------------------------------------
# yfinance: fetch and upsert OHLCV
# ------------------------------------------------------------------

def fetch_and_store_price_history(
    ticker: str,
    conn:   psycopg.Connection,
    period: str = "60d",
) -> int:
    """
    Download OHLCV history for `ticker` and upsert into price_history.
    Phase 3: Fetches high-frequency 5m intervals instead of daily.
    """
    logger.info("[%s] Fetching price history (period=%s, interval=5m) …", ticker, period)

    def _download():
        stock = yf.Ticker(ticker)
        hist  = stock.history(period=period, interval="5m", auto_adjust=True)
        info  = {}
        try:
            info = stock.info  # may fail for delisted tickers
        except Exception:
            pass
        return hist, info

    hist, info = with_exponential_backoff(_download, max_retries=4, base_delay=2.0)

    if hist.empty:
        logger.warning("[%s] yfinance returned empty DataFrame.", ticker)
        return 0

    # Ensure asset row exists
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO assets (ticker, company_name, sector, market_cap)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (ticker)
            DO UPDATE SET
                company_name = EXCLUDED.company_name,
                sector       = EXCLUDED.sector,
                market_cap   = EXCLUDED.market_cap,
                updated_at   = NOW()
            RETURNING id
            """,
            (
                ticker,
                info.get("longName")       or ticker,
                info.get("sector")         or "Unknown",
                info.get("marketCap"),
            ),
        )
        asset_id = cur.fetchone()[0]

    rows_written = 0
    with conn.cursor() as cur:
        for ts, row in hist.iterrows():
            # ts is a timezone-aware pandas Timestamp. Convert to standard UTC datetime.
            trade_ts = ts.to_pydatetime().astimezone(IST)

            cur.execute(
                """
                INSERT INTO price_history
                    (asset_id, ticker, ts,
                     open_price, high_price, low_price, close_price,
                     volume, adj_close)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, ts) DO UPDATE SET
                    open_price  = EXCLUDED.open_price,
                    high_price  = EXCLUDED.high_price,
                    low_price   = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price,
                    volume      = EXCLUDED.volume,
                    adj_close   = EXCLUDED.adj_close
                """,
                (
                    asset_id,
                    ticker,
                    trade_ts,
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                    int(row["Volume"]),
                    float(row["Close"]),  # yfinance already adjusts Close
                ),
            )
            rows_written += 1

    conn.commit()
    logger.info("[%s] Upserted %d price rows.", ticker, rows_written)
    return rows_written


# ------------------------------------------------------------------
# Yahoo Finance: fetch news and store sentiment
# ------------------------------------------------------------------

def fetch_yfinance_news_sentiment(
    ticker:   str,
    analyzer: FinBERTAnalyzer,
    conn:     psycopg.Connection,
) -> int:
    """
    Fetch Yahoo Finance news for `ticker`, run FinBERT, store results.
    Returns the number of NEW rows inserted.
    """
    logger.info("[%s] Fetching Yahoo Finance news …", ticker)
    
    def _get_news():
        t = yf.Ticker(ticker)
        return t.news
        
    news = with_exponential_backoff(_get_news, max_retries=3, base_delay=1.0)
    
    if not news:
        logger.info("[%s] No news found.", ticker)
        return 0

    logger.info("[%s] Running FinBERT on %d news items …", ticker, len(news))

    # Prepare texts: Title + Summary
    texts = []
    processed_news = []
    for item in news:
        content = item.get("content", {})
        title = content.get("title", "")
        summary = content.get("summary", "")
        full_text = f"{title}. {summary}".strip()
        if len(full_text) >= 15:
            texts.append(full_text[:512])
            processed_news.append(item)

    if not texts:
        return 0

    sentiments = analyzer.analyze_batch(texts)

    inserted = 0
    with conn.cursor() as cur:
        for item, text, s in zip(processed_news, texts, sentiments):
            content = item.get("content", {})
            source_id = item.get("id") or hashlib.md5(text.encode()).hexdigest()
            
            # Parse publication date
            pub_date_str = content.get("pubDate")
            if pub_date_str:
                try:
                    # Parse UTC string and convert to IST
                    pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00")).astimezone(IST)
                except ValueError:
                    pub_date = datetime.now(IST)
            else:
                pub_date = datetime.now(IST)

            cur.execute(
                """
                INSERT INTO sentiment_logs
                    (ticker, source, source_id,
                     raw_text, positive_score, negative_score,
                     neutral_score, compound_score,
                     author, post_url, created_at)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_id) DO NOTHING
                RETURNING id
                """,
                (
                    ticker.upper(),
                    "yfinance",
                    source_id,
                    text[:2000],
                    s.positive,
                    s.negative,
                    s.neutral,
                    s.compound,
                    content.get("provider", {}).get("displayName", "Yahoo Finance"),
                    content.get("canonicalUrl", {}).get("url"),
                    pub_date,
                ),
            )
            if cur.fetchone():
                inserted += 1

    conn.commit()
    logger.info("[%s] Inserted %d new sentiment records.", ticker, inserted)
    return inserted


# ------------------------------------------------------------------
# Helper — torch device
# ------------------------------------------------------------------

def _get_device_id() -> int:
    try:
        torch = importlib.import_module("torch")
        return 0 if torch.cuda.is_available() else -1
    except ImportError:
        return -1


# ------------------------------------------------------------------
# Main pipeline runner
# ------------------------------------------------------------------

def run_pipeline(tickers: list[str]) -> None:
    analyzer = FinBERTAnalyzer()

    with psycopg.connect(_DATABASE_URL) as conn:
        for ticker in tickers:
            logger.info("══ Processing %s ══", ticker)

            try:
                fetch_and_store_price_history(ticker, conn)
            except Exception as exc:
                logger.error("[%s] Price pipeline error: %s", ticker, exc)

            try:
                fetch_yfinance_news_sentiment(ticker, analyzer, conn)
            except Exception as exc:
                logger.error("[%s] Sentiment pipeline error: %s", ticker, exc)


if __name__ == "__main__":
    raw = os.getenv("TICKERS", "AAPL,TSLA,NVDA,MSFT")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    run_pipeline(tickers)
