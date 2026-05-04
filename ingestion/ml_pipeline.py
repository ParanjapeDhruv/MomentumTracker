"""
ml_pipeline.py — Phase 1 & 2: XGBoost Predictive Engine & Background Processor

Responsibilities:
  1. Extract aligned intraday (5m) price and sentiment data.
  2. Engineer lead/lag features (Sentiment_{t-1}, Sentiment_{t-2}, volatility, price momentum).
  3. Train an XGBoost classification model to predict Price_{t+1} direction.
  4. Upsert predictions into `predictions` table for O(1) API retrieval.
"""
import os
import logging
import psycopg
import pandas as pd
import xgboost as xgb
from datetime import timedelta

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/sentiment_tracker",
)

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Given a DataFrame with 'ts', 'close_price', and 'avg_compound',
    engineer the required ML features.
    """
    df = df.sort_values('ts').copy()
    
    # 1. Target: Price direction at t+1 (1 if UP, 0 if DOWN)
    df['next_close'] = df['close_price'].shift(-1)
    df['target_direction'] = (df['next_close'] > df['close_price']).astype(int)
    
    # 2. Features: Lagged Sentiment
    df['sentiment_t_1'] = df['avg_compound'].shift(1)
    df['sentiment_t_2'] = df['avg_compound'].shift(2)
    
    # 3. Features: Rolling sentiment volatility (e.g., 12 periods = 1 hour at 5m)
    df['sentiment_volatility'] = df['avg_compound'].rolling(12).std()
    
    # 4. Features: Price momentum (e.g., pct change over last 12 periods)
    df['price_momentum'] = df['close_price'].pct_change(12) * 100
    
    return df

def train_and_predict(ticker: str, conn: psycopg.Connection):
    logger.info("Extracting data for %s", ticker)
    
    query = """
    SELECT
        p.ts,
        p.close_price,
        COALESCE(s.avg_compound, 0) AS avg_compound
    FROM price_history p
    LEFT JOIN interval_sentiment_agg s 
        ON p.ticker = s.ticker AND p.ts = s.interval_ts
    WHERE p.ticker = %s
    ORDER BY p.ts ASC
    """
    
    with conn.cursor() as cur:
        cur.execute(query, (ticker,))
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        
    df = pd.DataFrame(rows, columns=columns)
    
    # Ensure numeric types (psycopg/Decimal → float)
    for col in ['close_price', 'avg_compound']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    if len(df) < 50:
        logger.warning("Not enough data to train for %s. Wait for more ingestion.", ticker)
        return
    
    df = build_features(df)
    
    # Separate training data and inference data (last known row)
    train_df = df.dropna(subset=['sentiment_t_1', 'sentiment_t_2', 'sentiment_volatility', 'price_momentum', 'next_close']).copy()
    inference_df = df.iloc[-1:].copy()
    
    if len(train_df) < 20:
        logger.warning("Not enough training samples after feature engineering for %s.", ticker)
        return

    features = ['sentiment_t_1', 'sentiment_t_2', 'sentiment_volatility', 'price_momentum']
    X = train_df[features]
    y = train_df['target_direction']
    
    logger.info("Training XGBoost on %d samples for %s", len(X), ticker)
    model = xgb.XGBClassifier(
        n_estimators=100, 
        max_depth=3, 
        learning_rate=0.05,
        random_state=42,
    )
    model.fit(X, y)
    
    # Inference for the latest timestamp
    X_infer = inference_df[features]
    pred_prob = model.predict_proba(X_infer)[0][1] # Probability of UP
    pred_dir = 1 if pred_prob > 0.5 else -1
    
    ts = inference_df['ts'].iloc[0]
    close_price = inference_df['close_price'].iloc[0]
    sent_t1 = inference_df['sentiment_t_1'].iloc[0]
    sent_t2 = inference_df['sentiment_t_2'].iloc[0]
    sent_vol = inference_df['sentiment_volatility'].iloc[0]
    price_mom = inference_df['price_momentum'].iloc[0]
    
    # Predict target ts (t+1, +5 minutes)
    target_ts = ts + timedelta(minutes=5)
    
    logger.info("[%s] Prediction for %s: Direction %d (Prob UP: %.2f)", ticker, ts, pred_dir, pred_prob)
    
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO predictions
                (ticker, ts, close_price, sentiment_t_1, sentiment_t_2, 
                 sentiment_volatility, price_momentum, 
                 prediction_target_ts, predicted_direction, predicted_probability)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, ts) DO UPDATE SET
                close_price = EXCLUDED.close_price,
                sentiment_t_1 = EXCLUDED.sentiment_t_1,
                sentiment_t_2 = EXCLUDED.sentiment_t_2,
                sentiment_volatility = EXCLUDED.sentiment_volatility,
                price_momentum = EXCLUDED.price_momentum,
                prediction_target_ts = EXCLUDED.prediction_target_ts,
                predicted_direction = EXCLUDED.predicted_direction,
                predicted_probability = EXCLUDED.predicted_probability
            """,
            (
                ticker, ts, float(close_price), float(sent_t1), float(sent_t2),
                float(sent_vol), float(price_mom), target_ts, 
                int(pred_dir), float(pred_prob)
            )
        )
    conn.commit()
    logger.info("Upserted prediction for %s at %s", ticker, ts)

def run_ml_pipeline():
    raw = os.getenv("TICKERS", "AAPL,TSLA,NVDA,MSFT,AMZN")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    
    with psycopg.connect(_DATABASE_URL) as conn:
        for ticker in tickers:
            try:
                train_and_predict(ticker, conn)
            except Exception as e:
                logger.error("Error processing %s: %s", ticker, e)

if __name__ == "__main__":
    run_ml_pipeline()
