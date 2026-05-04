/**
 * Dashboard.jsx — Root page component.
 *
 * Layout:
 *   ┌── Header ─────────────────────────────────────────────┐
 *   │  Logo  |  Ticker Tabs  |  Window Selector  |  Refresh │
 *   ├── Summary Bar ─────────────────────────────────────────┤
 *   │  Last Close  |  Avg Sentiment  |  Mentions  |  Corr   │
 *   ├── Chart Area ──────────────────────────────────────────┤
 *   │  [Price Candlestick]                                   │
 *   │  [Sentiment Bars + 7D Rolling Line]                    │
 *   ├── Legend ──────────────────────────────────────────────┤
 *   │  ■ Bullish  ■ Bearish  ── 7D Avg                       │
 *   └────────────────────────────────────────────────────────┘
 */
import { useState } from 'react';
import {
  ActivitySquare,
  AlertCircle,
  BarChart2,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { MomentumChart } from './components/MomentumChart';
import { useMomentumData } from './hooks/useMomentumData';

// ─── Config ───────────────────────────────────────────────────────────────────

const DEFAULT_TICKERS = ['AAPL', 'TSLA', 'NVDA', 'MSFT', 'AMZN'];
const WINDOW_OPTIONS  = [
  { label: '7D',  value: 7  },
  { label: '30D', value: 30 },
  { label: '90D', value: 90 },
];

// ─── Sub-components ───────────────────────────────────────────────────────────

function MetricCard({ label, value, sub, trend, icon: Icon }) {
  const trendColor =
    trend === 'up'   ? 'text-chart-bull' :
    trend === 'down' ? 'text-chart-bear' : 'text-ink-muted';

  return (
    <div className="bg-surface-cream border border-border-default rounded-md px-4 py-3 flex flex-col gap-0.5 shadow-card">
      <div className="flex items-center gap-1.5 text-ink-muted text-xxs uppercase tracking-widest font-mono">
        {Icon && <Icon size={11} strokeWidth={1.8} />}
        {label}
      </div>
      <div className={`text-xl font-mono font-semibold text-ink-primary leading-tight ${trendColor}`}>
        {value}
      </div>
      {sub && (
        <div className="text-xxs font-mono text-ink-muted truncate">{sub}</div>
      )}
    </div>
  );
}

function SentimentBadge({ score }) {
  if (score >  0.15) return (
    <span className="inline-flex items-center gap-1 text-xs font-mono px-2 py-0.5 rounded-sm bg-accent-green/20 text-chart-bull border border-chart-bull/30">
      <TrendingUp size={11} /> BULLISH
    </span>
  );
  if (score < -0.15) return (
    <span className="inline-flex items-center gap-1 text-xs font-mono px-2 py-0.5 rounded-sm bg-chart-bear/10 text-chart-bear border border-chart-bear/30">
      <TrendingDown size={11} /> BEARISH
    </span>
  );
  return (
    <span className="inline-flex items-center gap-1 text-xs font-mono px-2 py-0.5 rounded-sm bg-ink-disabled/10 text-ink-muted border border-border-default">
      NEUTRAL
    </span>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-3">
      <div className="w-6 h-6 border-2 border-accent-green border-t-transparent rounded-full animate-spin" />
      <p className="text-ink-muted text-sm font-mono">Fetching momentum data …</p>
    </div>
  );
}

function ErrorState({ message, onRetry }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-3">
      <AlertCircle size={28} className="text-chart-bear" strokeWidth={1.5} />
      <p className="text-ink-secondary text-sm font-mono">{message}</p>
      <button
        onClick={onRetry}
        className="text-xs font-mono px-3 py-1.5 rounded border border-border-strong text-ink-secondary hover:bg-surface-panel transition-colors"
      >
        Retry
      </button>
    </div>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [activeTicker, setActiveTicker]  = useState(DEFAULT_TICKERS[0]);
  const [customTicker, setCustomTicker]  = useState('');
  const [window, setWindow]              = useState(30);
  const { status, data, error, refetch } = useMomentumData(activeTicker, window);

  const summary = data?.summary ?? null;
  const series  = data?.data    ?? [];

  // Derived values for metric cards
  const latestClose   = summary?.latest_close;
  const prevClose     = series.length > 1 ? series[series.length - 2]?.close : null;
  const closeChange   = latestClose && prevClose ? latestClose - prevClose : null;
  const closeChangePct = closeChange && prevClose ? (closeChange / prevClose * 100) : null;

  const handleCustomSearch = (e) => {
    e.preventDefault();
    const t = customTicker.trim().toUpperCase();
    if (t.length < 1 || t.length > 10) return;
    setActiveTicker(t);
    setCustomTicker('');
  };

  return (
    <div className="min-h-screen bg-surface-parchment text-ink-primary font-sans">

      {/* ── Navigation header ──────────────────────────────── */}
      <header className="border-b border-border-default bg-surface-cream/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-screen-xl mx-auto px-4 h-12 flex items-center justify-between gap-4">

          {/* Brand */}
          <div className="flex items-center gap-2 shrink-0">
            <ActivitySquare size={18} className="text-accent-green" strokeWidth={1.8} />
            <span className="text-sm font-mono font-semibold text-ink-secondary tracking-tight">
              MOMENTUM<span className="text-accent-green">TRACK</span>
            </span>
          </div>

          {/* Ticker quick-switch */}
          <nav className="flex items-center gap-0.5 overflow-x-auto hide-scrollbar">
            {DEFAULT_TICKERS.map((t) => (
              <button
                key={t}
                onClick={() => setActiveTicker(t)}
                className={`
                  text-xs font-mono px-3 py-1 rounded-sm transition-colors whitespace-nowrap
                  ${activeTicker === t
                    ? 'bg-accent-green text-white font-semibold'
                    : 'text-ink-muted hover:text-ink-primary hover:bg-surface-panel'}
                `}
              >
                {t}
              </button>
            ))}
          </nav>

          {/* Custom ticker search */}
          <form onSubmit={handleCustomSearch} className="flex items-center gap-1.5 shrink-0">
            <input
              type="text"
              value={customTicker}
              onChange={(e) => setCustomTicker(e.target.value.toUpperCase())}
              placeholder="TICKER"
              maxLength={10}
              className="w-20 h-7 px-2 text-xs font-mono bg-surface-panel border border-border-default rounded-sm
                         text-ink-primary placeholder-ink-disabled focus:outline-none focus:border-accent-green
                         uppercase tracking-widest"
            />
            <button
              type="submit"
              className="h-7 px-2.5 text-xs font-mono bg-accent-green text-white rounded-sm
                         hover:bg-chart-line transition-colors"
            >
              GO
            </button>
          </form>

          {/* Window selector + refresh */}
          <div className="flex items-center gap-1 shrink-0">
            {WINDOW_OPTIONS.map(({ label, value }) => (
              <button
                key={value}
                onClick={() => setWindow(value)}
                className={`
                  text-xxs font-mono px-2 py-1 rounded-sm transition-colors
                  ${window === value
                    ? 'bg-accent-sage text-ink-secondary font-semibold'
                    : 'text-ink-muted hover:text-ink-primary'}
                `}
              >
                {label}
              </button>
            ))}
            <button
              onClick={refetch}
              disabled={status === 'loading'}
              className="ml-1 p-1.5 text-ink-muted hover:text-ink-primary disabled:opacity-40 transition-colors"
              title="Refresh"
            >
              <RefreshCw size={13} className={status === 'loading' ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>
      </header>

      {/* ── Main content ────────────────────────────────────── */}
      <main className="max-w-screen-xl mx-auto px-4 py-6 space-y-5">

        {/* Title row */}
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-mono font-bold text-ink-primary">{activeTicker}</h1>
          {summary?.company_name && (
            <span className="text-sm text-ink-muted font-mono">{summary.company_name}</span>
          )}
          {summary?.sector && (
            <span className="text-xs font-mono px-2 py-0.5 rounded-sm bg-surface-panel border border-border-default text-ink-muted">
              {summary.sector}
            </span>
          )}
          {summary && <SentimentBadge score={summary.avg_sentiment} />}
          <span className="ml-auto text-xxs font-mono text-ink-disabled">
            {summary ? `${summary.period_start} → ${summary.period_end}` : ''}
          </span>
        </div>

        {/* Summary metrics */}
        {summary && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <MetricCard
              label="Last Close"
              icon={BarChart2}
              value={`$${latestClose?.toFixed(2) ?? '—'}`}
              sub={
                closeChangePct != null
                  ? `${closeChangePct >= 0 ? '+' : ''}${closeChangePct.toFixed(2)}% vs prev`
                  : undefined
              }
              trend={closeChange == null ? null : closeChange >= 0 ? 'up' : 'down'}
            />
            <MetricCard
              label="Avg Sentiment"
              icon={Zap}
              value={summary.avg_sentiment.toFixed(4)}
              sub={`${summary.total_mentions.toLocaleString()} total mentions`}
              trend={summary.avg_sentiment > 0.05 ? 'up' : summary.avg_sentiment < -0.05 ? 'down' : null}
            />
            <MetricCard
              label="Price Momentum"
              icon={TrendingUp}
              value={`${summary.price_momentum >= 0 ? '+' : ''}${summary.price_momentum.toFixed(3)}%`}
              sub={`${window}D mean daily return`}
              trend={summary.price_momentum >= 0 ? 'up' : 'down'}
            />
            <MetricCard
              label="Sent/Price Corr"
              icon={ActivitySquare}
              value={summary.sentiment_price_correlation.toFixed(4)}
              sub="Pearson r(sentiment, Δ%)"
              trend={
                Math.abs(summary.sentiment_price_correlation) > 0.4 ? 'up' : null
              }
            />
          </div>
        )}

        {/* Chart panel */}
        <div className="bg-surface-panel border border-border-default rounded-lg shadow-card overflow-hidden">

          {/* Chart header */}
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-default">
            <div className="flex items-center gap-4">
              <span className="text-xs font-mono font-semibold text-ink-secondary uppercase tracking-wider">
                Price Action
              </span>
              <span className="text-xs font-mono text-ink-muted">
                Candlestick · OHLC
              </span>
            </div>
            <span className="text-xxs font-mono text-ink-disabled">
              {series.length} trading days
            </span>
          </div>

          <div className="px-2 pt-4 pb-0">
            {status === 'loading' && <LoadingState />}
            {status === 'error'   && <ErrorState message={error} onRetry={refetch} />}
            {status === 'success' && <MomentumChart data={series} />}
          </div>

          {/* Chart sub-header: Sentiment */}
          <div className="flex items-center gap-4 px-4 py-2 border-t border-border-default mt-1">
            <span className="text-xs font-mono font-semibold text-ink-secondary uppercase tracking-wider">
              Sentiment Signal
            </span>
            <span className="text-xs font-mono text-ink-muted">
              FinBERT · Yahoo Finance · 7D Rolling Avg
            </span>
          </div>

          {/* Legend */}
          <div className="flex items-center gap-5 px-4 pb-3 text-xxs font-mono text-ink-muted">
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm" style={{ background: '#9AB17A' }} />
              Bullish sentiment
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm" style={{ background: '#C0392B' }} />
              Bearish sentiment
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-4 h-px" style={{ background: '#5B7A3A', display: 'inline-block', verticalAlign: 'middle' }} />
              7D rolling average
            </span>
            <span className="flex items-center gap-1.5 ml-auto">
              <span className="w-2.5 h-2.5 rounded-sm border" style={{ background: '#9AB17A', borderColor: '#9AB17A' }} />
              Bullish candle
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm border-2" style={{ background: 'transparent', borderColor: '#C0392B' }} />
              Bearish candle
            </span>
          </div>
        </div>

        {/* Data quality footer */}
        {summary && (
          <p className="text-xxs font-mono text-ink-disabled text-right">
            Data: yfinance (OHLCV + News) · NLP: ProsusAI/FinBERT ·
            Last updated: {new Date().toLocaleTimeString()}
          </p>
        )}
      </main>
    </div>
  );
}
