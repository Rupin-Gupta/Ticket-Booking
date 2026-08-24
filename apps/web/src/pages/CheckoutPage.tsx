import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import type { SeatView } from '@ticket/shared';
import { api } from '../lib/api.js';
import { messageFor, useAuth } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPrice, formatShowDate, formatShowTime } from '../lib/format.js';
import { Alert, Button, Card, Skeleton } from '../components/ui.js';
import { HoldCountdown } from '../components/HoldCountdown.js';
import './checkout.css';

type ShowDetail = {
  id: string;
  startsAt: string;
  event: { id: string; title: string; venue: { name: string } };
};

/**
 * Page 2 of 3. The seats are already held by the time this renders — Continue
 * on page 1 acquired the lock.
 *
 * Leaving does not delete the hold, it shortens it to a grace window, so a
 * customer who bounces back and forward can reclaim their seats rather than
 * losing them to somebody faster.
 */
export function CheckoutPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();

  const show = useAsync(() => api.get<{ show: ShowDetail }>(`/api/v1/shows/${id}`), [id]);
  const seats = useAsync(() => api.get<{ seats: SeatView[] }>(`/api/v1/shows/${id}/seats`), [id]);

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mine = (seats.data?.seats ?? []).filter((s) => s.heldByMe);
  const total = mine.reduce((sum, s) => sum + Number(s.price), 0);
  const expiresAt = mine.find((s) => s.holdExpiresAt)?.holdExpiresAt ?? null;

  // Arriving here with nothing held means the hold lapsed, or the URL was opened
  // directly. Send them back rather than showing an empty checkout.
  useEffect(() => {
    if (!seats.loading && seats.data && mine.length === 0) {
      navigate(`/shows/${id}`, { replace: true });
    }
  }, [seats.loading, seats.data, mine.length, navigate, id]);

  // ponytail: no unload handler. sendBeacon cannot send an Authorization header,
  // so a beacon release would need an unauthenticated endpoint that frees seats —
  // a worse problem than the one it solves. A closed tab is handled by the
  // five-minute TTL, which is exactly why lazy expiry exists: the client is an
  // optimisation, the server's clock is the truth.

  const goBack = useCallback(async () => {
    setBusy(true);
    try {
      await api.del(`/api/v1/shows/${id}/holds`);
    } catch {
      // Even if this fails the hold expires on its own; never block the exit.
    } finally {
      navigate(`/shows/${id}`);
    }
  }, [id, navigate]);

  async function confirm() {
    if (!user) {
      navigate('/login', { state: { from: { pathname: `/shows/${id}/checkout` } } });
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const { booking } = await api.post<{ booking: { id: string } }>('/api/v1/bookings', {
        showId: id,
        seatIds: mine.map((s) => s.id),
      });
      navigate(`/bookings/${booking.id}`);
    } catch (err) {
      setError(messageFor(err));
      seats.reload();
    } finally {
      setBusy(false);
    }
  }

  if (show.loading || seats.loading) return <Skeleton count={1} height={320} />;
  // Both fetches matter. Without the seat list there is nothing to check out —
  // rendering the page anyway shows a £0 basket with a live Confirm button.
  if (show.error || seats.error) return <Alert>{show.error ?? seats.error}</Alert>;
  if (!show.data) return null;

  const detail = show.data.show;

  return (
    <div className="checkout">
      <nav aria-label="Breadcrumb" className="detail__crumbs">
        <Link to={`/events/${detail.event.id}`}>{detail.event.title}</Link>
        <span aria-hidden="true">/</span>
        <Link to={`/shows/${id}`}>Seats</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">Checkout</span>
      </nav>

      <ol className="steps" aria-label="Booking progress">
        <li>Choose seats</li>
        <li aria-current="step">Checkout</li>
        <li>Ticket</li>
      </ol>

      <Card className="checkout__card">
        <h1 className="checkout__title">{detail.event.title}</h1>
        <p className="checkout__meta">
          {formatShowDate(detail.startsAt)} at {formatShowTime(detail.startsAt)} ·{' '}
          {detail.event.venue.name}
        </p>

        {expiresAt && (
          <p className="checkout__timer">
            Your seats are held for{' '}
            <HoldCountdown expiresAt={expiresAt} onExpire={() => navigate(`/shows/${id}`)} />
          </p>
        )}

        {error && <Alert>{error}</Alert>}

        <ul className="checkout__seats">
          {mine.map((s) => (
            <li key={s.id}>
              <span>
                {s.row}
                {s.number} · {s.categoryName}
              </span>
              <span>{formatPrice(s.price)}</span>
            </li>
          ))}
        </ul>

        <p className="checkout__total">
          <span>Total</span>
          <strong>{formatPrice(total)}</strong>
        </p>

        <Button variant="cta" full loading={busy} onClick={confirm}>
          {user ? 'Confirm booking' : 'Log in to confirm'}
        </Button>
        <Button variant="quiet" full disabled={busy} onClick={goBack}>
          Back to seats
        </Button>
        <p className="checkout__note">
          Going back keeps your seats for a few more seconds in case you change your mind.
        </p>
      </Card>
    </div>
  );
}
