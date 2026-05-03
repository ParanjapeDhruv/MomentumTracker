/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,jsx,ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // ─── Brand / Accent ───────────────────────────────
        'accent-green': '#9AB17A',   // positive sentiment, up candles, CTA
        'accent-sage':  '#C3CC9B',   // secondary accents, volume bars

        // ─── Surface / Background ─────────────────────────
        'surface-parchment': '#E4DFB5',  // page background
        'surface-cream':     '#FBE8CE',  // card surface, negative sentiment fill
        'surface-panel':     '#EDE9C7',  // elevated panels (charts, modals)

        // ─── Semantic chart colors ─────────────────────────
        'chart-bull':   '#9AB17A',   // bullish candle fill
        'chart-bear':   '#C0392B',   // bearish candle fill / negative sentiment
        'chart-vol':    '#C3CC9B',   // volume bar fill
        'chart-line':   '#5B7A3A',   // rolling 7d sentiment line
        'chart-grid':   '#D6D0A0',   // axis gridlines

        // ─── Text ─────────────────────────────────────────
        'ink-primary':   '#1A1A1A',   // body text
        'ink-secondary': '#3D3D3D',   // labels, headings
        'ink-muted':     '#6B6963',   // captions, timestamps
        'ink-disabled':  '#A09C93',   // disabled state

        // ─── Borders ──────────────────────────────────────
        'border-default': '#CEC9A4',
        'border-strong':  '#B3AC86',
      },

      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'Consolas', 'monospace'],
      },

      fontSize: {
        'xxs': ['0.625rem', { lineHeight: '0.875rem' }],
      },

      boxShadow: {
        'card': '0 1px 3px rgba(26,26,26,0.06), 0 1px 2px rgba(26,26,26,0.08)',
        'card-hover': '0 4px 8px rgba(26,26,26,0.10), 0 2px 4px rgba(26,26,26,0.08)',
      },

      borderRadius: {
        'sm': '3px',
        DEFAULT: '4px',
        'md': '6px',
        'lg': '8px',
      },
    },
  },
  plugins: [],
};
