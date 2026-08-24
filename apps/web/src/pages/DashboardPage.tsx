import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../lib/api.js';
import { messageFor } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPrice, formatShowDate, formatShowTime, isoDate } from '../lib/format.js';
import { Alert, Button, Card, EmptyState, Skeleton } from '../components/ui.js';
import './dashboard.css';

type Summary = {
  event: { id: string; title: string; type: string; venue: string };
  totals: {
    revenue: string;
    capacity: number;
    seatsSold: number;
    percentSold: number;
    bookings: number;
    cancelled: number;
    waiting: number;
  };
  categories: {
    id: string;
    name: string;
    currentPrice: string;
    capacity: number;
    seatsSold: number;
    revenue: string;
    waiting: number;
  }[];
  shows: {
    id: string;
    startsAt: string;
    status: 'SCHEDULED' | 'CANCELLED';
    capacity: number;
    seatsSold: number;
    revenue: string;
    bookings: number;
    cancelled: number;
  }[];
};

/**
 * A dashboard is scanned, not read, so the summary comes before the detail and
 * the numbers that need attention carry shape as well as value.
 */
export function DashboardPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, loading, reload } = useAsync(
    () => api.get<Summary>(`/api/v1/organiser/events/${id}/summary`),
    [id],
  );

  if (loading) return <Skeleton count={2} height={120} />;
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;

  const { event, totals, categories, shows } = data;

  return (
    <div className="dash">
      <nav aria-label="Breadcrumb" className="detail__crumbs">
        <Link to="/manage">Your events</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">{event.title}</span>
      </nav>

      <header className="dash__head">
        <h1 className="dash__title">{event.title}</h1>
        <p className="dash__sub">{event.venue}</p>
      </header>

      {/* Summary first — the four numbers an organiser actually opens this for. */}
      <ul className="stats">
        <Stat label="Revenue" value={formatPrice(totals.revenue)} note="Confirmed bookings only" />
        <Stat
          label="Seats sold"
          value={`${totals.seatsSold} / ${totals.capacity}`}
          note={`${totals.percentSold}% of capacity`}
          meter={totals.percentSold}
        />
        <Stat
          label="Bookings"
          value={String(totals.bookings)}
          note={totals.cancelled > 0 ? `${totals.cancelled} cancelled` : 'none cancelled'}
        />
        {/* Spread rather than `tone={... : undefined}` — under
            exactOptionalPropertyTypes an explicit undefined is not the same as
            an absent prop. */}
        <Stat
          label="Waiting"
          value={String(totals.waiting)}
          note={totals.waiting > 0 ? 'people queued for a seat' : 'no queue'}
          {...(totals.waiting > 0 ? { tone: 'warn' as const } : {})}
        />
      </ul>

      <Card className="pad">
        <h2 className="dash__h2">By category</h2>
        {categories.length === 0 ? (
          <EmptyState title="Nothing priced yet.">
            Price the venue&rsquo;s sections before scheduling a show.
          </EmptyState>
        ) : (
          <ul className="bars">
            {categories.map((c) => {
              const pct = c.capacity === 0 ? 0 : Math.round((c.seatsSold / c.capacity) * 100);
              return (
                <li key={c.id} className="bar">
                  <div className="bar__head">
                    <span className="bar__name">
                      {c.name}
                      <small>now {formatPrice(c.currentPrice)}</small>
                    </span>
                    <span className="bar__value">{formatPrice(c.revenue)}</span>
                  </div>
                  {/* The number is the fact; the bar is a shortcut to comparing
                      them. Both are present, so neither carries it alone. */}
                  <div
                    className="bar__track"
                    role="img"
                    aria-label={`${c.name}: ${c.seatsSold} of ${c.capacity} seats sold, ${pct} percent`}
                  >
                    <div className="bar__fill" style={{ width: `${pct}%` }} />
                  </div>
                  <p className="bar__note">
                    {c.seatsSold} / {c.capacity} seats · {pct}%
                    {c.waiting > 0 && <strong> · {c.waiting} waiting</strong>}
                  </p>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card className="pad">
        <h2 className="dash__h2">By show</h2>
        {shows.length === 0 ? (
          <EmptyState title="No shows scheduled.">
            <Link to="/manage">Schedule one</Link> to start selling.
          </EmptyState>
        ) : (
          /* Wide table scrolls in its own container so the page body never
             scrolls sideways on a phone. */
          <div className="tablewrap">
            <table className="dash__table">
              <thead>
                <tr>
                  <th scope="col">Show</th>
                  <th scope="col" className="num">
                    Sold
                  </th>
                  <th scope="col" className="num">
                    Bookings
                  </th>
                  <th scope="col" className="num">
                    Cancelled
                  </th>
                  <th scope="col" className="num">
                    Revenue
                  </th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {shows.map((s) => (
                  <tr key={s.id}>
                    <th scope="row">
                      <Link to={`/shows/${s.id}`}>
                        <time dateTime={isoDate(s.startsAt)}>
                          {formatShowDate(s.startsAt)} · {formatShowTime(s.startsAt)}
                        </time>
                      </Link>
                    </th>
                    <td className="num">
                      {s.seatsSold} / {s.capacity}
                    </td>
                    <td className="num">{s.bookings}</td>
                    <td className="num">{s.cancelled || '—'}</td>
                    <td className="num strong">{formatPrice(s.revenue)}</td>
                    <td>
                      <CancelShow show={s} onCancelled={reload} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <p className="dash__foot">
        Revenue is summed from the price each seat was actually sold at, so re-pricing a category
        never changes what past bookings were worth. Cancelled bookings are excluded.
      </p>
    </div>
  );
}

/**
 * Cancelling lives here rather than on the event editor because this is the
 * only screen that shows what is at stake: how many seats are sold and what
 * they were worth. The confirm names both.
 */
function CancelShow({
  show,
  onCancelled,
}: {
  show: Summary['shows'][number];
  onCancelled: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (show.status === 'CANCELLED') {
    return <span className="dash__cancelled">Cancelled</span>;
  }

  async function cancel() {
    const stake =
      show.seatsSold > 0
        ? `${show.seatsSold} sold ${show.seatsSold === 1 ? 'seat' : 'seats'} worth ${formatPrice(show.revenue)} will be refunded and every affected customer emailed.`
        : 'Nothing is sold yet.';
    if (!confirm(`Cancel the show on ${formatShowDate(show.startsAt)}?\n\n${stake}`)) return;
    setError(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/shows/${show.id}/cancel`, {});
      onCancelled();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant="quiet" loading={busy} onClick={cancel}>
        Cancel
      </Button>
      {error && <Alert>{error}</Alert>}
    </>
  );
}

function Stat({
  label,
  value,
  note,
  meter,
  tone,
}: {
  label: string;
  value: string;
  note: string;
  meter?: number;
  tone?: 'warn';
}) {
  return (
    <li className={`stat ${tone ? `stat--${tone}` : ''}`}>
      <p className="stat__label">{label}</p>
      <p className="stat__value">{value}</p>
      {meter !== undefined && (
        <div className="stat__meter" aria-hidden="true">
          <div style={{ width: `${meter}%` }} />
        </div>
      )}
      <p className="stat__note">{note}</p>
    </li>
  );
}
