import { useState } from 'react';
import { api } from '../lib/api.js';
import { messageFor } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { formatShowDate, formatShowTime } from '../lib/format.js';
import { Alert, Button, Card, EmptyState, Field, Select, Skeleton } from '../components/ui.js';
import type { EventSummary } from '../lib/types.js';
import './lab.css';

type Race = {
  seatId: string;
  attempts: number;
  elapsedMs: number;
  outcome: { won: number; rejected: number; errors: number };
  errorCodes: string[];
  holdsGranted: number;
  passed: boolean;
};

/**
 * The concurrency guarantee, demonstrated rather than asserted.
 *
 * Every other page shows the system working when nothing is contended. This one
 * fires N simultaneous holds at a single seat and shows the tally: one winner,
 * everyone else refused, no errors. It calls the same `hold_seats()` the public
 * endpoint calls — a lab that exercised a copy of the locking would prove
 * nothing about the real thing.
 */
export function LabPage() {
  const events = useAsync(() => api.get<{ events: EventSummary[] }>('/api/v1/events?limit=50'), []);

  const [showId, setShowId] = useState('');
  const [attempts, setAttempts] = useState(50);
  const [race, setRace] = useState<Race | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const shows = (events.data?.events ?? []).flatMap((e) =>
    e.shows.map((s) => ({
      id: s.id,
      label: `${e.title} · ${formatShowDate(s.startsAt)} ${formatShowTime(s.startsAt)}`,
    })),
  );

  async function run() {
    setError(null);
    setRace(null);
    setBusy(true);
    try {
      const { race: result } = await api.post<{ race: Race }>('/api/v1/lab/race', {
        showId: showId || shows[0]?.id,
        attempts,
      });
      setRace(result);
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="lab">
      <header className="lab__head">
        <h1 className="lab__title">Concurrency lab</h1>
        <p className="lab__sub prose">
          Fires simultaneous hold requests at one seat and reports what happened. Exactly one should
          win; every other contender should be refused with <code>SEAT_UNAVAILABLE</code>, and
          nothing should error. The winner's hold is released afterwards, so this is safe to run on
          a live show.
        </p>
      </header>

      <Card className="pad">
        {events.loading && <Skeleton count={1} height={80} />}
        {events.error && <Alert>{events.error}</Alert>}
        {!events.loading && shows.length === 0 && (
          <EmptyState title="No shows to race on.">Schedule a show first.</EmptyState>
        )}

        {shows.length > 0 && (
          <div className="stack">
            {error && <Alert>{error}</Alert>}
            <Select
              label="Show"
              value={showId || (shows[0]?.id ?? '')}
              onChange={(e) => setShowId(e.target.value)}
            >
              {shows.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </Select>
            <Field
              label="Contenders"
              type="number"
              min={2}
              max={100}
              value={attempts}
              onChange={(e) => setAttempts(Number(e.target.value))}
              hint="Between 2 and 100. The server picks a free seat and they all go for it at once."
            />
            <Button variant="cta" loading={busy} onClick={run}>
              Run the race
            </Button>
          </div>
        )}
      </Card>

      {race && (
        <Card
          className={`pad lab__result ${race.passed ? 'lab__result--pass' : 'lab__result--fail'}`}
        >
          <h2 className="lab__verdict">
            {race.passed ? 'One winner. Guarantee held.' : 'Guarantee did not hold.'}
          </h2>

          <ul className="lab__tally">
            <li>
              <strong>{race.outcome.won}</strong>
              <span>hold granted</span>
            </li>
            <li>
              <strong>{race.outcome.rejected}</strong>
              <span>refused</span>
            </li>
            <li className={race.outcome.errors > 0 ? 'lab__bad' : undefined}>
              <strong>{race.outcome.errors}</strong>
              <span>errors</span>
            </li>
            <li>
              <strong>{race.elapsedMs} ms</strong>
              <span>for {race.attempts} contenders</span>
            </li>
          </ul>

          {race.errorCodes.length > 0 && (
            <Alert>Unexpected failures: {race.errorCodes.join(', ')}</Alert>
          )}

          <p className="lab__note">
            Seat <code>{race.seatId}</code>. Every contender ran the same locked transaction the
            booking flow uses: <code>SELECT … ORDER BY id FOR UPDATE</code>, re-read under the lock,
            then write. The losers were refused cleanly rather than deadlocking or erroring — that
            is the difference between a race that is handled and one that is merely rare.
          </p>
        </Card>
      )}
    </div>
  );
}
