-- ============================================================
-- Sentiment-Driven Stock Momentum Tracker — Database Schema
-- Phase 3 Refactor: Intraday Support & Pre-computed Predictions
-- ============================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE IF NOT EXISTS assets (
    id           SERIAL PRIMARY KEY,
    ticker       VARCHAR(10)  NOT NULL,
    company_name TEXT,
    sector       TEXT,
    market_cap   BIGINT,
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT assets_ticker_uq UNIQUE (ticker),
    CONSTRAINT assets_ticker_format CHECK (ticker ~ '^[A-Z0-9.\-]{1,10}$')
);

CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS assets_touch_updated_at ON assets;

CREATE TRIGGER assets_touch_updated_at
BEFORE UPDATE ON assets
FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ─── Phase 3: Intraday High-Frequency OHLCV Data ────────────────────

CREATE TABLE IF NOT EXISTS price_history (
    id          BIGSERIAL    PRIMARY KEY,
    asset_id    INTEGER      NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    ticker      VARCHAR(10)  NOT NULL,
    ts          TIMESTAMPTZ  NOT NULL, -- Intraday timestamp (e.g., 5m intervals)
    open_price  NUMERIC(14, 4) NOT NULL,
    high_price  NUMERIC(14, 4) NOT NULL,
    low_price   NUMERIC(14, 4) NOT NULL,
    close_price NUMERIC(14, 4) NOT NULL,
    adj_close   NUMERIC(14, 4),
    volume      BIGINT       NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT price_history_ticker_ts_uq  UNIQUE (ticker, ts),
    CONSTRAINT price_history_ohlc_sane CHECK (
        low_price  <= open_price  AND
        low_price  <= close_price AND
        low_price  <= high_price  AND
        high_price >= open_price  AND
        high_price >= close_price
    )
);

CREATE INDEX IF NOT EXISTS idx_ph_ticker_ts_desc
    ON price_history (ticker, ts DESC);

CREATE INDEX IF NOT EXISTS idx_ph_asset_id
    ON price_history (asset_id);

CREATE INDEX IF NOT EXISTS idx_ph_ts_brin
    ON price_history USING BRIN (ts)
    WITH (pages_per_range = 32);


-- ─── Phase 1 & 3: Sentiment Logs ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS sentiment_logs (
    id              BIGSERIAL    PRIMARY KEY,
    ticker          VARCHAR(10)  NOT NULL,
    source          VARCHAR(50)  NOT NULL,
    source_id       VARCHAR(128),
    post_url        TEXT,
    author          TEXT,
    raw_text        TEXT         NOT NULL,
    processed_text  TEXT,
    positive_score  NUMERIC(6, 4) NOT NULL DEFAULT 0,
    negative_score  NUMERIC(6, 4) NOT NULL DEFAULT 0,
    neutral_score   NUMERIC(6, 4) NOT NULL DEFAULT 0,
    compound_score  NUMERIC(6, 4) NOT NULL, -- Modified compound logic
    created_at      TIMESTAMPTZ  NOT NULL,
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT sl_compound_range CHECK (compound_score BETWEEN -1.0 AND 1.0),
    CONSTRAINT sl_prob_range CHECK (
        positive_score BETWEEN 0 AND 1 AND
        negative_score BETWEEN 0 AND 1 AND
        neutral_score  BETWEEN 0 AND 1
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sl_source_id_dedup
    ON sentiment_logs (source_id)
    WHERE source_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sl_ticker_created_asc
    ON sentiment_logs (ticker, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_sl_ingested_at_brin
    ON sentiment_logs USING BRIN (ingested_at)
    WITH (pages_per_range = 64);

CREATE INDEX IF NOT EXISTS idx_sl_source_ticker
    ON sentiment_logs (source, ticker);


-- ─── Phase 2 & 3: Materialized View for Intraday Aggregation ──────────

CREATE MATERIALIZED VIEW IF NOT EXISTS interval_sentiment_agg AS
SELECT
    ticker,
    -- Align to 5-minute intervals
    to_timestamp(floor((extract('epoch' from created_at) / 300 )) * 300) AT TIME ZONE 'UTC' AS interval_ts,
    COUNT(*)                                      AS mention_count,
    ROUND(AVG(compound_score)::NUMERIC, 5)        AS avg_compound,
    ROUND(STDDEV(compound_score)::NUMERIC, 5)     AS compound_stddev,
    SUM(CASE WHEN compound_score >  0.05 THEN 1 ELSE 0 END) AS positive_count,
    SUM(CASE WHEN compound_score < -0.05 THEN 1 ELSE 0 END) AS negative_count,
    SUM(CASE WHEN ABS(compound_score) <= 0.05 THEN 1 ELSE 0 END) AS neutral_count
FROM sentiment_logs
GROUP BY ticker, interval_ts
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_isa_ticker_ts
    ON interval_sentiment_agg (ticker, interval_ts DESC);


-- ─── Phase 2: O(1) Pre-Computed Predictions Table ─────────────────────

CREATE TABLE IF NOT EXISTS predictions (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    ts TIMESTAMPTZ NOT NULL, -- The time of the data point used for prediction
    
    close_price NUMERIC(14, 4),
    
    -- Feature Engineering
    sentiment_t_1 NUMERIC(6, 4),
    sentiment_t_2 NUMERIC(6, 4),
    sentiment_volatility NUMERIC(6, 4),
    price_momentum NUMERIC(6, 4),
    
    -- Outputs
    prediction_target_ts TIMESTAMPTZ NOT NULL, -- Target time (t+1)
    predicted_direction INTEGER NOT NULL,      -- 1 = UP, -1 = DOWN
    predicted_probability NUMERIC(6, 4),       -- Probability of UP
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT predictions_ticker_ts_uq UNIQUE (ticker, ts)
);

CREATE INDEX IF NOT EXISTS idx_pred_ticker_ts_desc ON predictions (ticker, ts DESC);

-- ─── Seed Data ────────────────────────────────────────────────────────

INSERT INTO assets (ticker, company_name, sector) VALUES
    ('AAPL',  'Apple Inc.',             'Technology'),
    ('TSLA',  'Tesla Inc.',             'Consumer Cyclical'),
    ('NVDA',  'NVIDIA Corporation',     'Technology'),
    ('MSFT',  'Microsoft Corporation',  'Technology'),
    ('AMZN',  'Amazon.com Inc.',        'Consumer Cyclical')
ON CONFLICT (ticker) DO NOTHING;
