import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import type { SeatView } from '@ticket/shared';
import { api } from '../lib/api.js';
import { messageFor, useAuth } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { useLiveSeats } from '../lib/useLiveSeats.js';
import { formatPrice, formatShowDate, formatShowTime } from '../lib/format.js';
import { Alert, Button, Card, Skeleton } from '../components/ui.js';
import { SeatMap } from '../components/SeatMap.js';
import { HoldCountdown } from '../components/HoldCountdown.js';
import { WaitlistPanel } from '../components/WaitlistPanel.js';
import './offer.css';
import './show.css';

type ShowDetail = {
  id: string;
  startsAt: string;
  status: 'SCHEDULED' | 'CANCELLED';
  event: {
    id: string;
    title: string;
    venue: { name: string; address: string };
    categories: { id: string; name: string; price: string }[];
  };
};

type Hold = { showId: string; seatIds: string[]; holdExpiresAt: string };

/** Stable identity: a fresh [] each render would restart the hook's effect. */
const EMPTY_SEATS: SeatView[] = [];

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

  // Socket.IO keeps the map live; the hook falls back to polling if the socket
  // cannot connect, so the page degrades rather than freezing.
  const {
    seats: seatList,
    live,
    viewers,
  } = useLiveSeats({
    showId: id!,
    initial: seats.data?.seats ?? EMPTY_SEATS,
    refetch: reloadSeats,
  });

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
      // A wheelchair space and its companion are held and booked as one unit —
      // the server enforces it, and the map should not pretend otherwise by
      // letting somebody select half a pair.
      if (seat.pairedWith) {
        if (next.has(seat.id)) next.add(seat.pairedWith);
        else next.delete(seat.pairedWith);
      }
      return next;
    });
  }, []);

  const selectedSeats = seatList.filter((s) => selected.has(s.id));
  const total = selectedSeats.reduce((sum, s) => sum + Number(s.price), 0);

  async function placeHold() {
    if (!user) {
      navigate('/login', { state: { from: { pathname: `/shows/${id}` } } });
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/shows/${id}/holds`, { seatIds: [...selected] });
      // Page 2. The lock is acquired here and nowhere earlier — clicking a seat
      // is browsing, and locking on browse freezes a row for everybody else.
      navigate(`/shows/${id}/checkout`);
    } catch (err) {
      setError(messageFor(err));
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

  // A cancelled show keeps its seat map — cancelling resets every seat to
  // AVAILABLE — so without this the page would look like a normal, entirely
  // bookable performance. The API refuses the hold, but nobody should get that
  // far to find out.
  if (detail.status === 'CANCELLED') {
    return (
      <div className="showpage">
        <nav aria-label="Breadcrumb" className="detail__crumbs">
          <Link to="/">Events</Link>
          <span aria-hidden="true">/</span>
          <Link to={`/events/${detail.event.id}`}>{detail.event.title}</Link>
          <span aria-hidden="true">/</span>
          <span aria-current="page">Cancelled</span>
        </nav>
        <Card className="pad">
          <h1 className="showpage__title">{detail.event.title}</h1>
          <p className="showpage__meta">
            {formatShowDate(detail.startsAt)} at {formatShowTime(detail.startsAt)} ·{' '}
            {detail.event.venue.name}
          </p>
          <Alert>
            This performance has been cancelled by the organiser. Any bookings for it have been
            cancelled and refunded, and the customers emailed.
          </Alert>
          <Link to={`/events/${detail.event.id}`} className="btn btn--cta">
            See other dates
          </Link>
        </Card>
      </div>
    );
  }

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
          <p className={`livedot ${live ? 'livedot--on' : ''}`} role="status">
            <span aria-hidden="true" />
            {live ? 'Live — updates as others book' : 'Reconnecting…'}
            {live && viewers > 1 && (
              <span className="livedot__viewers">
                {' · '}
                {viewers} watching
              </span>
            )}
          </p>
          {seats.loading && seatList.length === 0 ? (
            <Skeleton count={1} height={260} />
          ) : seats.error ? (
            <Alert>{seats.error}</Alert>
          ) : (
            <SeatMap seats={seatList} selected={selected} onToggle={toggle} disabled={busy} />
          )}
          {seatList.length > 0 && <WaitlistPanel showId={id!} seats={seatList} />}
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
                {user ? 'Continue' : 'Log in to continue'}
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
