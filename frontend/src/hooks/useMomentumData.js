import { useCallback, useEffect, useState } from 'react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export function useMomentumData(ticker, days = 30) {
  const [status, setStatus] = useState('loading');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

  const refetch = useCallback(() => {
    setReloadKey((value) => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    async function loadMomentum() {
      setStatus('loading');
      setError('');

      try {
        const response = await fetch(
          `${API_BASE_URL}/api/v1/momentum/${encodeURIComponent(ticker)}?days=${days}`,
          { signal: controller.signal },
        );

        if (!response.ok) {
          let detail = `Request failed with status ${response.status}`;

          try {
            const payload = await response.json();
            if (typeof payload?.detail === 'string' && payload.detail.trim()) {
              detail = payload.detail;
            }
          } catch {
            // Ignore JSON parsing errors and fall back to the default message.
          }

          throw new Error(detail);
        }

        const payload = await response.json();
        setData(payload);
        setStatus('success');
      } catch (err) {
        if (controller.signal.aborted) {
          return;
        }

        setData(null);
        setError(err instanceof Error ? err.message : 'Unable to load momentum data.');
        setStatus('error');
      }
    }

    loadMomentum();

    return () => controller.abort();
  }, [days, reloadKey, ticker]);

  return { status, data, error, refetch };
}
