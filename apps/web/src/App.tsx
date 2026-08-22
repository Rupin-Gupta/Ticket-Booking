import { useEffect, useState } from 'react';
import { api, ApiClientError } from './lib/api.js';

type Health = {
  ok: boolean;
  env: string;
  uptimeSeconds: number;
  configured: Record<string, boolean>;
  database: 'up' | 'unreachable' | 'not-configured';
};

/**
 * Phase 0 placeholder. It exists to prove the wiring end to end: vite proxy →
 * express → shared types. Real screens land in Phase 2, designed with the
 * ui-ux-pro-max skill.
 */
export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Health>('/health')
      .then(setHealth)
      .catch((e: unknown) =>
        setError(e instanceof ApiClientError ? e.message : 'API unreachable. Is it running?'),
      );
  }, []);

  return (
    <main>
      <p className="eyebrow">Phase 0 · foundations</p>
      <h1>Ticket Booking</h1>

      {error && <p className="bad">{error}</p>}

      {health && (
        <>
          <p className="ok">
            API reachable — {health.env}, up {health.uptimeSeconds}s
          </p>
          <ul className="checklist">
            {Object.entries(health.configured).map(([name, ready]) => (
              <li key={name} className={ready ? 'ok' : 'todo'}>
                <span aria-hidden="true">{ready ? '●' : '○'}</span>
                {name}
                <em>
                  {name === 'database' && ready
                    ? health.database === 'up'
                      ? 'connected'
                      : 'configured, unreachable'
                    : ready
                      ? 'configured'
                      : 'not configured'}
                </em>
              </li>
            ))}
          </ul>
        </>
      )}

      {!health && !error && <p className="muted">Checking the API…</p>}
    </main>
  );
}
