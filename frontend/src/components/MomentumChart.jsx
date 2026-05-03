/**
 * components/MomentumChart.jsx
 *
 * Composite chart:
 *   ┌───────────────────────────────────────────────────┐
 *   │  Price action — custom candlestick layer           │  60% height
 *   ├───────────────────────────────────────────────────┤
 *   │  Daily sentiment bars + rolling 7d line            │  40% height
 *   └───────────────────────────────────────────────────┘
 *
 * Candlestick rendering uses Recharts' <Customized> component which
 * injects xAxisMap / yAxisMap with live d3-scale objects — this is the
 * only reliable way to convert OHLC prices to pixel coordinates without
 * a third-party library.
 */
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Customized,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useMemo } from 'react';

// ─── Palette constants (mirrored from tailwind.config.js) ─────────────────────
const C = {
  BULL:     '#9AB17A',
  BEAR:     '#C0392B',
  SAGE:     '#C3CC9B',
  LINE:     '#5B7A3A',
  GRID:     '#D6D0A0',
  TEXT:     '#3D3D3D',
  MUTED:    '#6B6963',
  CREAM:    '#FBE8CE',
  NEUTRAL:  '#A09C93',
};

// ─── Candlestick layer ─────────────────────────────────────────────────────────

/**
 * Rendered via <Customized>.  Receives the full Recharts internal context
 * including axis scale functions.
 */
function CandlestickLayer({ xAxisMap, yAxisMap, data }) {
  const xAxis = xAxisMap?.[0];
  const yAxis = yAxisMap?.[0];

  if (!xAxis || !yAxis || !data?.length) return null;

  // For a band scale (category xAxis), scale(val) → left edge of band
  const bw     = xAxis.bandSize ?? xAxis.bandwidth?.() ?? 10;
  const candleW = Math.max(bw * 0.65, 2);

  return (
    <g className="candlestick-layer">
      {data.map((d, i) => {
        if (!d.trade_date) return null;

        const xLeft  = xAxis.scale(d.trade_date);
        const cx     = xLeft + bw / 2;
        const highPx = yAxis.scale(d.high);
        const lowPx  = yAxis.scale(d.low);
        const openPx = yAxis.scale(d.open);
        const closePx = yAxis.scale(d.close);

        const isUp   = d.close >= d.open;
        const color  = isUp ? C.BULL : C.BEAR;
        const bodyTop  = Math.min(openPx, closePx);
        const bodyH    = Math.max(Math.abs(closePx - openPx), 1.5);

        return (
          <g key={d.trade_date}>
            {/* Wick — full high-low range */}
            <line
              x1={cx} y1={highPx}
              x2={cx} y2={lowPx}
              stroke={color}
              strokeWidth={1}
            />
            {/* Body — open to close */}
            <rect
              x={cx - candleW / 2}
              y={bodyTop}
              width={candleW}
              height={bodyH}
              fill={isUp ? color : 'transparent'}
              stroke={color}
              strokeWidth={1.5}
              rx={0.5}
            />
          </g>
        );
      })}
    </g>
  );
}

// ─── Custom Tooltip ───────────────────────────────────────────────────────────

function PriceTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;

  return (
    <div className="bg-surface-panel border border-border-default rounded-md shadow-card p-3 text-xs font-mono min-w-[160px]">
      <p className="text-ink-secondary font-semibold mb-2">{label}</p>
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-ink-muted">
        <span>O</span><span className="text-ink-primary text-right">${d.open?.toFixed(2)}</span>
        <span>H</span><span className="text-ink-primary text-right">${d.high?.toFixed(2)}</span>
        <span>L</span><span className="text-ink-primary text-right">${d.low?.toFixed(2)}</span>
        <span>C</span>
        <span className={`text-right font-bold ${d.close >= d.open ? 'text-chart-bull' : 'text-chart-bear'}`}>
          ${d.close?.toFixed(2)}
        </span>
        <span>Vol</span><span className="text-ink-primary text-right">{d.volume?.toLocaleString()}</span>
      </div>
    </div>
  );
}

function SentimentTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;

  const s = d.daily_sentiment ?? 0;
  const label_text = s > 0.05 ? 'POSITIVE' : s < -0.05 ? 'NEGATIVE' : 'NEUTRAL';
  const color = s > 0.05 ? C.BULL : s < -0.05 ? C.BEAR : C.NEUTRAL;

  return (
    <div className="bg-surface-panel border border-border-default rounded-md shadow-card p-3 text-xs font-mono min-w-[170px]">
      <p className="text-ink-secondary font-semibold mb-2">{label}</p>
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-ink-muted">
        <span>Sentiment</span>
        <span style={{ color }} className="text-right font-bold">{s.toFixed(4)} ({label_text})</span>
        <span>7D Avg</span>
        <span className="text-ink-primary text-right">{d.rolling_7d_sentiment?.toFixed(4)}</span>
        <span>Mentions</span>
        <span className="text-ink-primary text-right">{d.mention_count?.toLocaleString()}</span>
        <span>+/-/=</span>
        <span className="text-ink-primary text-right">
          {d.positive_count}/{d.negative_count}/{d.neutral_count}
        </span>
      </div>
    </div>
  );
}

// ─── X-axis label formatter ───────────────────────────────────────────────────

const formatDate = (val) => {
  if (!val) return '';
  const d = new Date(val);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

// ─── Main component ────────────────────────────────────────────────────────────

export function MomentumChart({ data }) {
  // Derive price domain with 3% padding so candles don't hug the edges
  const [priceDomainMin, priceDomainMax] = useMemo(() => {
    if (!data?.length) return [0, 100];
    const allLows   = data.map(d => d.low);
    const allHighs  = data.map(d => d.high);
    const minLow    = Math.min(...allLows);
    const maxHigh   = Math.max(...allHighs);
    const pad       = (maxHigh - minLow) * 0.04;
    return [Math.floor((minLow - pad) * 100) / 100, Math.ceil((maxHigh + pad) * 100) / 100];
  }, [data]);

  const sentimentDomain = useMemo(() => {
    if (!data?.length) return [-1, 1];
    const vals   = data.map(d => d.daily_sentiment);
    const maxAbs = Math.min(1, Math.max(0.3, ...vals.map(Math.abs)) * 1.3);
    return [-maxAbs, maxAbs];
  }, [data]);

  if (!data?.length) {
    return (
      <div className="flex items-center justify-center h-64 text-ink-muted text-sm font-mono">
        No data to display.
      </div>
    );
  }

  return (
    <div className="w-full space-y-0">

      {/* ── Price chart ──────────────────────────────────────── */}
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={C.GRID} vertical={false} />

          <XAxis
            dataKey="trade_date"
            tickFormatter={formatDate}
            tick={{ fontSize: 10, fontFamily: 'monospace', fill: C.MUTED }}
            tickLine={false}
            axisLine={{ stroke: C.GRID }}
            interval="preserveStartEnd"
          />

          <YAxis
            domain={[priceDomainMin, priceDomainMax]}
            tickFormatter={(v) => `$${v.toFixed(0)}`}
            tick={{ fontSize: 10, fontFamily: 'monospace', fill: C.MUTED }}
            tickLine={false}
            axisLine={false}
            width={52}
            orientation="right"
          />

          <Tooltip content={<PriceTooltip />} cursor={{ stroke: C.GRID, strokeWidth: 1 }} />

          {/*
            Invisible Bar used solely to make Recharts build the xAxis band scale.
            Without at least one Bar/Line, the xAxisMap won't have bandSize set.
            We render it fully transparent.
          */}
          <Bar dataKey="close" fill="transparent" stroke="transparent" isAnimationActive={false} />

          {/* Candlestick layer — uses Customized for access to axis scales */}
          <Customized component={CandlestickLayer} />
        </ComposedChart>
      </ResponsiveContainer>

      {/* ── Sentiment chart ───────────────────────────────────── */}
      <ResponsiveContainer width="100%" height={160}>
        <ComposedChart data={data} margin={{ top: 0, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={C.GRID} vertical={false} />

          <XAxis
            dataKey="trade_date"
            tickFormatter={formatDate}
            tick={{ fontSize: 10, fontFamily: 'monospace', fill: C.MUTED }}
            tickLine={false}
            axisLine={{ stroke: C.GRID }}
            interval="preserveStartEnd"
          />

          <YAxis
            domain={sentimentDomain}
            tickFormatter={(v) => v.toFixed(2)}
            tick={{ fontSize: 10, fontFamily: 'monospace', fill: C.MUTED }}
            tickLine={false}
            axisLine={false}
            width={52}
            orientation="right"
          />

          <Tooltip content={<SentimentTooltip />} cursor={{ stroke: C.GRID, strokeWidth: 1 }} />

          {/* Zero sentiment line */}
          <ReferenceLine y={0} stroke={C.MUTED} strokeWidth={1} strokeDasharray="4 2" />

          {/* Daily sentiment bars */}
          <Bar
            dataKey="daily_sentiment"
            isAnimationActive={false}
            radius={[2, 2, 0, 0]}
            maxBarSize={20}
          >
            {data.map((entry, index) => (
              <Cell
                key={`cell-${index}`}
                fill={
                  entry.daily_sentiment > 0.05
                    ? C.BULL
                    : entry.daily_sentiment < -0.05
                    ? C.BEAR
                    : C.NEUTRAL
                }
                fillOpacity={0.75}
              />
            ))}
          </Bar>

          {/* Rolling 7d sentiment line */}
          <Line
            type="monotone"
            dataKey="rolling_7d_sentiment"
            stroke={C.LINE}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
