import os
import logging
import psycopg
import pandas as pd
import xgboost as xgb
from datetime import timedelta

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/sentiment_tracker",
)

def prep_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values('ts').copy()
    df['next_close'] = df['close_price'].shift(-1)
    df['target_direction'] = (df['next_close'] > df['close_price']).astype(int)
    df['sentiment_t_1'] = df['avg_compound'].shift(1)
    df['sentiment_t_2'] = df['avg_compound'].shift(2)
    df['sentiment_volatility'] = df['avg_compound'].rolling(12).std()
    df['price_momentum'] = df['close_price'].pct_change(12) * 100
    return df

def fit_and_infer(ticker: str, conn: psycopg.Connection):
    logger.info("[%s] fit and infer", ticker)
    query = """
    SELECT p.ts, p.close_price, COALESCE(s.avg_compound, 0) AS avg_compound
    FROM price_history p
    LEFT JOIN interval_sentiment_agg s ON p.ticker = s.ticker AND p.ts = s.interval_ts
    WHERE p.ticker = %s
    ORDER BY p.ts ASC
    """
    with conn.cursor() as cur:
        cur.execute(query, (ticker,))
        df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    
    for c in ['close_price', 'avg_compound']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    
    if len(df) < 50:
        logger.warning("[%s] insufficient data", ticker)
        return
    
    df = prep_features(df)
    train = df.dropna(subset=['sentiment_t_1', 'sentiment_t_2', 'sentiment_volatility', 'price_momentum', 'next_close']).copy()
    infer = df.iloc[-1:].copy()
    
    if len(train) < 20:
        logger.warning("[%s] insufficient train samples", ticker)
        return

    f = ['sentiment_t_1', 'sentiment_t_2', 'sentiment_volatility', 'price_momentum']
    model = xgb.XGBClassifier(n_estimators=100, max_depth=3, learning_rate=0.05, random_state=42)
    model.fit(train[f], train['target_direction'])
    
    prob = model.predict_proba(infer[f])[0][1]
    lbl = 1 if prob > 0.5 else -1
    row = infer.iloc[0]
    
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO predictions (ticker, ts, close_price, sentiment_t_1, sentiment_t_2, 
                 sentiment_volatility, price_momentum, prediction_target_ts, predicted_direction, predicted_probability)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, ts) DO UPDATE SET
                close_price = EXCLUDED.close_price, sentiment_t_1 = EXCLUDED.sentiment_t_1,
                sentiment_t_2 = EXCLUDED.sentiment_t_2, sentiment_volatility = EXCLUDED.sentiment_volatility,
                price_momentum = EXCLUDED.price_momentum, prediction_target_ts = EXCLUDED.prediction_target_ts,
                predicted_direction = EXCLUDED.predicted_direction, predicted_probability = EXCLUDED.predicted_probability
            """,
            (ticker, row['ts'], float(row['close_price']), float(row['sentiment_t_1']), float(row['sentiment_t_2']),
             float(row['sentiment_volatility']), float(row['price_momentum']), row['ts'] + timedelta(minutes=5), lbl, float(prob))
        )
    conn.commit()

def run():
    raw = os.getenv("TICKERS", "AAPL,TSLA,NVDA,MSFT,AMZN")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    with psycopg.connect(_URL) as conn:
        for t in tickers:
            try: fit_and_infer(t, conn)
            except Exception as e: logger.error("[%s] ml err: %s", t, e)

if __name__ == "__main__":
    run()
