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
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/sentiment_tracker",
)

@dataclass(slots=True, frozen=True)
class Score:
    positive: float
    negative: float
    neutral: float
    compound: float

class FinBERT:
    MODEL_ID = "ProsusAI/finbert"
    MAX_TOKENS = 512

    def __init__(self) -> None:
        logger.info("loading finbert")
        tokenizer = AutoTokenizer.from_pretrained(self.MODEL_ID)
        model = AutoModelForSequenceClassification.from_pretrained(self.MODEL_ID)
        self._pipe = pipeline(
            task="text-classification",
            model=model,
            tokenizer=tokenizer,
            top_k=None,
            truncation=True,
            max_length=self.MAX_TOKENS,
            device=self._get_dev(),
        )

    def _get_dev(self) -> int:
        try:
            torch = importlib.import_module("torch")
            return 0 if torch.cuda.is_available() else -1
        except ImportError:
            return -1

    def _parse(self, raw: list[dict] | dict) -> Score:
        items = raw if isinstance(raw, list) else [raw]
        scores = {item["label"].lower(): float(item["score"]) for item in items}
        pos, neg, neu = scores.get("positive", 0.0), scores.get("negative", 0.0), scores.get("neutral", 0.0)
        return Score(round(pos, 5), round(neg, 5), round(neu, 5), round(pos - neg, 5))

    def score(self, text: str) -> Score:
        if len(text.strip()) < 15: return Score(0.0, 0.0, 1.0, 0.0)
        try:
            raw = self._pipe(text.strip())
            return self._parse(raw[0] if isinstance(raw, list) else raw)
        except Exception:
            return Score(0.0, 0.0, 1.0, 0.0)

    def score_batch(self, texts: list[str]) -> list[Score]:
        if not texts: return []
        try:
            raw = self._pipe([t[:2000] for t in texts], batch_size=16)
            return [self._parse(r) for r in raw]
        except Exception:
            return [self.score(t) for t in texts]

class Limiter:
    def __init__(self, cpm: int) -> None:
        self._int = 60.0 / cpm
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self._int:
            time.sleep(self._int - elapsed)
        self._last = time.monotonic()

def retry(func, *args, retries=5, delay=1.0, **kwargs):
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if i == retries - 1: raise
            t = delay * (2 ** i)
            logger.warning("retry %d/%d: %s", i+1, retries, e)
            time.sleep(t)

def ingest_ohlcv(ticker: str, conn: psycopg.Connection, period: str = "60d") -> int:
    logger.info("[%s] ingest ohlcv", ticker)
    def _fetch():
        s = yf.Ticker(ticker)
        return s.history(period=period, interval="5m", auto_adjust=True), s.info
    hist, info = retry(_fetch)
    if hist.empty: return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO assets (ticker, company_name, sector, market_cap)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (ticker) DO UPDATE SET
                company_name = EXCLUDED.company_name,
                sector = EXCLUDED.sector,
                market_cap = EXCLUDED.market_cap,
                updated_at = NOW()
            RETURNING id
            """,
            (ticker, info.get("longName", ticker), info.get("sector", "Unknown"), info.get("marketCap")),
        )
        aid = cur.fetchone()[0]
    written = 0
    with conn.cursor() as cur:
        for ts, row in hist.iterrows():
            cur.execute(
                """
                INSERT INTO price_history (asset_id, ticker, ts, open_price, high_price, low_price, close_price, volume, adj_close)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, ts) DO UPDATE SET
                    open_price = EXCLUDED.open_price, high_price = EXCLUDED.high_price, low_price = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price, volume = EXCLUDED.volume, adj_close = EXCLUDED.adj_close
                """,
                (aid, ticker, ts.to_pydatetime().astimezone(IST), float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"]), int(row["Volume"]), float(row["Close"])),
            )
            written += 1
    conn.commit()
    return written

def ingest_news(ticker: str, model: FinBERT, conn: psycopg.Connection) -> int:
    logger.info("[%s] ingest news", ticker)
    news = retry(lambda: yf.Ticker(ticker).news)
    if not news: return 0
    texts, items = [], []
    for n in news:
        c = n.get("content", {})
        txt = f"{c.get('title','')}. {c.get('summary','')}".strip()
        if len(txt) >= 15:
            texts.append(txt[:512])
            items.append(n)
    if not texts: return 0
    scores = model.score_batch(texts)
    new = 0
    with conn.cursor() as cur:
        for it, tx, s in zip(items, texts, scores):
            c = it.get("content", {})
            sid = it.get("id") or hashlib.md5(tx.encode()).hexdigest()
            dt_str = c.get("pubDate")
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(IST) if dt_str else datetime.now(IST)
            cur.execute(
                """
                INSERT INTO sentiment_logs (ticker, source, source_id, raw_text, positive_score, negative_score, neutral_score, compound_score, author, post_url, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_id) DO NOTHING RETURNING id
                """,
                (ticker.upper(), "yfinance", sid, tx[:2000], s.positive, s.negative, s.neutral, s.compound, c.get("provider", {}).get("displayName", "Yahoo Finance"), c.get("canonicalUrl", {}).get("url"), dt),
            )
            if cur.fetchone(): new += 1
    conn.commit()
    return new

def run(tickers: list[str]) -> None:
    model = FinBERT()
    with psycopg.connect(_DB_URL) as conn:
        for t in tickers:
            logger.info("run: %s", t)
            try: ingest_ohlcv(t, conn)
            except Exception as e: logger.error("[%s] ohlcv err: %s", t, e)
            try: ingest_news(t, model, conn)
            except Exception as e: logger.error("[%s] news err: %s", t, e)

if __name__ == "__main__":
    raw = os.getenv("TICKERS", "AAPL,TSLA,NVDA,MSFT,AMZN")
    run([t.strip().upper() for t in raw.split(",") if t.strip()])
