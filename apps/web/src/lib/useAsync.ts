import { useCallback, useEffect, useState } from 'react';
import { ApiClientError } from './api.js';

type State<T> = { data: T | null; error: string | null; loading: boolean };

/**
 * ponytail: twenty lines instead of TanStack Query. This app fetches, shows a
 * loading state, and shows an error — it does not need a cache, background
 * refetching or optimistic updates. Reach for the library in Phase 6 if the
 * live seat map wants real cache invalidation.
 *
 * `deps` drives refetching; `reload` is for after a mutation.
 */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[],
): State<T> & { reload: () => void } {
  const [state, setState] = useState<State<T>>({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fn, deps);

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));

    run()
      .then((data) => {
        // Guards against a slow first request landing after a faster second one
        // and overwriting it with stale data.
        if (!cancelled) setState({ data, error: null, loading: false });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({
          data: null,
          loading: false,
          error:
            err instanceof ApiClientError
              ? err.message
              : 'Could not reach the server. Check your connection and try again.',
        });
      });

    return () => {
      cancelled = true;
    };
  }, [run, nonce]);

  return { ...state, reload: () => setNonce((n) => n + 1) };
}
