import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import type { SeatView } from '@ticket/shared';
import { api } from '../lib/api.js';
import { messageFor, useAuth } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPrice, formatShowDate, formatShowTime } from '../lib/format.js';
import { Alert, Button, Card, Skeleton } from '../components/ui.js';
import { SeatMap } from '../components/SeatMap.js';
import { HoldCountdown } from '../components/HoldCountdown.js';
import './show.css';

type ShowDetail = {
  id: string;
  startsAt: string;
  event: {
    id: string;
    title: string;
    venue: { name: string; address: string };
    categories: { id: string; name: string; price: string }[];
  };
};

type Hold = { showId: string; seatIds: string[]; holdExpiresAt: string };

/** Until Phase 6 brings Socket.IO, the map is polled. Slow enough to be cheap,
 *  fast enough that a seat someone else took does not sit stale for long. */
const POLL_MS = 8000;

export function ShowPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();

  const show = useAsync(() => api.get<{ show: ShowDetail }>(`/api/v1/shows/${id}`), [id]);
  const seats = useAsync(() => api.get<{ seats: SeatView[] }>(`/api/v1/shows/${id}/seats`), [id]);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [hold, setHold] = useState<Hold | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reloadSeats = seats.reload;

  // Poll while nothing is in flight. Refreshing mid-request would fight the
  // user's own selection.
  useEffect(() => {
    if (busy) return;
    const timer = setInterval(reloadSeats, POLL_MS);
    return () => clearInterval(timer);
  }, [busy, reloadSeats]);

  const seatList = seats.data?.seats ?? [];

  // A seat already held by this viewer counts as theirs — refreshing the page
  // mid-checkout must not lose the hold they are still paying for.
  const myHeldSeats = useMemo(() => seatList.filter((s) => s.heldByMe), [seatList]);

  useEffect(() => {
    if (hold || myHeldSeats.length === 0) return;
    const expiry = myHeldSeats.find((s) => s.holdExpiresAt)?.holdExpiresAt;
    if (expiry) {
      setHold({ showId: id!, seatIds: myHeldSeats.map((s) => s.id), holdExpiresAt: expiry });
    }
  }, [hold, myHeldSeats, id]);

  const toggle = useCallback((seat: SeatView) => {
    setError(null);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(seat.id)) next.delete(seat.id);
      else next.add(seat.id);
      return next;
    });
  }, []);

  const selectedSeats = seatList.filter((s) => selected.has(s.id));
  const total = selectedSeats.reduce((sum, s) => sum + Number(s.price), 0);

  async function placeHold() {
    if (!user) {
      // Send them to log in, and back here afterwards.
      navigate('/login', { state: { from: { pathname: `/shows/${id}` } } });
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const result = await api.post<Hold>(`/api/v1/shows/${id}/holds`, {
        seatIds: [...selected],
      });
      setHold(result);
      setSelected(new Set());
      reloadSeats();
    } catch (err) {
      setError(messageFor(err));
      // Someone else took a seat — reload so the map stops lying about it.
      reloadSeats();
    } finally {
      setBusy(false);
    }
  }

  async function release() {
    setBusy(true);
    try {
      await api.del(`/api/v1/shows/${id}/holds`);
      setHold(null);
      reloadSeats();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  const heldTotal = myHeldSeats.reduce((sum, s) => sum + Number(s.price), 0);

  async function confirmBooking() {
    setError(null);
    setBusy(true);
    try {
      const { booking } = await api.post<{ booking: { id: string } }>('/api/v1/bookings', {
        showId: id,
        seatIds: myHeldSeats.map((s) => s.id),
      });
      // Straight to the ticket. The email is queued and may land a moment
      // later; the customer should not have to wait for it to see the QR.
      navigate(`/bookings/${booking.id}`);
    } catch (err) {
      setError(messageFor(err));
      reloadSeats();
    } finally {
      setBusy(false);
    }
  }

  const onHoldExpired = useCallback(() => {
    setHold(null);
    setError('Your hold expired and the seats were released.');
    reloadSeats();
  }, [reloadSeats]);

  if (show.loading) return <Skeleton count={2} height={200} />;
  if (show.error) return <Alert>{show.error}</Alert>;
  if (!show.data) return null;

  const detail = show.data.show;

  return (
    <div className="showpage">
      <nav aria-label="Breadcrumb" className="detail__crumbs">
        <Link to="/">Events</Link>
        <span aria-hidden="true">/</span>
        <Link to={`/events/${detail.event.id}`}>{detail.event.title}</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">{formatShowDate(detail.startsAt)}</span>
      </nav>

      <header className="showpage__head">
        <h1 className="showpage__title">{detail.event.title}</h1>
        <p className="showpage__meta">
          {formatShowDate(detail.startsAt)} at {formatShowTime(detail.startsAt)} ·{' '}
          {detail.event.venue.name}
        </p>
      </header>

      <div className="showpage__grid">
        <Card className="showpage__map">
          {seats.loading && seatList.length === 0 ? (
            <Skeleton count={1} height={260} />
          ) : seats.error ? (
            <Alert>{seats.error}</Alert>
          ) : (
            <SeatMap seats={seatList} selected={selected} onToggle={toggle} disabled={busy} />
          )}
        </Card>

        <Card className="basket">
          {hold ? (
            <>
              <h2 className="basket__title">Seats held</h2>
              {error && <Alert>{error}</Alert>}
              <p className="basket__timer">
                Releasing in{' '}
                <HoldCountdown expiresAt={hold.holdExpiresAt} onExpire={onHoldExpired} />
              </p>
              <ul className="basket__seats">
                {myHeldSeats.map((s) => (
                  <li key={s.id}>
                    <span>
                      {s.row}
                      {s.number} · {s.categoryName}
                    </span>
                    <span>{formatPrice(s.price)}</span>
                  </li>
                ))}
              </ul>
              <p className="basket__total">
                <span>Total</span>
                <strong>{formatPrice(heldTotal)}</strong>
              </p>
              <Button variant="cta" full loading={busy} onClick={confirmBooking}>
                Confirm booking
              </Button>
              <Button variant="quiet" full loading={busy} onClick={release}>
                Release seats
              </Button>
              <p className="basket__note">
                Your QR ticket is emailed as soon as the booking is confirmed.
              </p>
            </>
          ) : (
            <>
              <h2 className="basket__title">Your selection</h2>
              {error && <Alert>{error}</Alert>}

              {selectedSeats.length === 0 ? (
                <p className="basket__empty">Pick seats from the map to hold them.</p>
              ) : (
                <>
                  <ul className="basket__seats">
                    {selectedSeats.map((s) => (
                      <li key={s.id}>
                        <span>
                          {s.row}
                          {s.number} · {s.categoryName}
                        </span>
                        <span>{formatPrice(s.price)}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="basket__total">
                    <span>Total</span>
                    <strong>{formatPrice(total)}</strong>
                  </p>
                </>
              )}

              <Button
                variant="cta"
                full
                loading={busy}
                disabled={selectedSeats.length === 0}
                onClick={placeHold}
              >
                {user ? 'Hold these seats' : 'Log in to hold seats'}
              </Button>
              <p className="basket__note">
                Held seats are yours for a few minutes while you check out, then released
                automatically.
              </p>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
