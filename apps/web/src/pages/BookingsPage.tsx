import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api.js';
import { messageFor } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPrice, formatShowDate, formatShowTime, isoDate } from '../lib/format.js';
import { Alert, Button, Card, EmptyState, Skeleton } from '../components/ui.js';
import './bookings.css';

export type BookingView = {
  id: string;
  reference: string;
  status: 'CONFIRMED' | 'CANCELLED';
  createdAt: string;
  cancelledAt: string | null;
  show: {
    id: string;
    startsAt: string;
    eventId: string;
    title: string;
    venue: string;
    address: string;
  };
  seats: { showSeatId: string; label: string; section: string; price: string }[];
  total: string;
  qrToken?: string;
};

export function BookingsPage() {
  const { data, error, loading, reload } = useAsync(
    () => api.get<{ bookings: BookingView[] }>('/api/v1/bookings'),
    [],
  );

  if (loading) return <Skeleton count={2} height={150} />;
  if (error) return <Alert>{error}</Alert>;

  const bookings = data?.bookings ?? [];

  return (
    <div className="bookings">
      <header className="bookings__head">
        <h1 className="bookings__title">Your bookings</h1>
      </header>

      {bookings.length === 0 ? (
        <EmptyState title="No bookings yet.">
          <Link to="/">Find an event</Link> and pick your seats.
        </EmptyState>
      ) : (
        <ul className="bookings__list">
          {bookings.map((booking) => (
            <li key={booking.id}>
              <BookingCard booking={booking} onChanged={reload} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function BookingCard({ booking, onChanged }: { booking: BookingView; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Cancelling is irreversible and frees the seat to someone else, so it asks
  // once rather than firing on the first click.
  const [confirming, setConfirming] = useState(false);

  const cancelled = booking.status === 'CANCELLED';
  const past = new Date(booking.show.startsAt).getTime() <= Date.now();

  async function cancel() {
    setError(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/bookings/${booking.id}/cancel`);
      setConfirming(false);
      onChanged();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className={`bcard ${cancelled ? 'bcard--cancelled' : ''}`}>
      <div className="bcard__head">
        <div>
          <h2 className="bcard__title">
            <Link to={`/events/${booking.show.eventId}`}>{booking.show.title}</Link>
          </h2>
          <p className="bcard__when">
            <time dateTime={isoDate(booking.show.startsAt)}>
              {formatShowDate(booking.show.startsAt)} at {formatShowTime(booking.show.startsAt)}
            </time>{' '}
            · {booking.show.venue}
          </p>
        </div>
        {/* Status as a word, not a colour — and the whole card dims when
            cancelled, so it reads at a glance without relying on hue. */}
        <span className={`badge badge--${booking.status.toLowerCase()}`}>
          {cancelled ? 'Cancelled' : 'Confirmed'}
        </span>
      </div>

      <dl className="bcard__facts">
        <div>
          <dt>Reference</dt>
          <dd className="mono">{booking.reference}</dd>
        </div>
        <div>
          <dt>Seats</dt>
          <dd>{booking.seats.map((s) => s.label).join(', ')}</dd>
        </div>
        <div>
          <dt>Total</dt>
          <dd>{formatPrice(booking.total)}</dd>
        </div>
      </dl>

      {error && <Alert>{error}</Alert>}

      <div className="bcard__actions">
        {!cancelled && (
          <Link to={`/bookings/${booking.id}`} className="btn btn--ghost">
            View ticket
          </Link>
        )}

        {!cancelled &&
          !past &&
          (confirming ? (
            <>
              <Button variant="ghost" onClick={() => setConfirming(false)} disabled={busy}>
                Keep it
              </Button>
              <Button variant="cta" loading={busy} onClick={cancel}>
                Yes, cancel
              </Button>
            </>
          ) : (
            <Button variant="quiet" onClick={() => setConfirming(true)}>
              Cancel booking
            </Button>
          ))}

        {past && !cancelled && <span className="bcard__note">This show has already started.</span>}
      </div>

      {confirming && (
        <p className="bcard__warn" role="alert">
          Cancelling releases {booking.seats.length === 1 ? 'this seat' : 'these seats'} for someone
          else and cannot be undone.
        </p>
      )}
    </Card>
  );
}
