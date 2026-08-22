import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, ApiClientError } from '../lib/api.js';
import { messageFor, useAuth } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPrice, formatShowDate, formatShowTime } from '../lib/format.js';
import { Alert, Button, Card, Skeleton } from '../components/ui.js';
import { HoldCountdown } from '../components/HoldCountdown.js';
import './offer.css';

type Offer = {
  showId: string;
  eventId: string;
  eventTitle: string;
  venue: string;
  startsAt: string;
  category: string;
  price: string;
  expiresAt: string;
};

/**
 * Where the offer email lands.
 *
 * Reading is public — the link is often opened on a phone that is not signed
 * in — but claiming needs the account the offer was made to. The countdown is
 * the point of the page: this seat is theirs and nobody else's, until it isn't.
 */
export function OfferPage() {
  const { token } = useParams<{ token: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();

  const { data, error, loading, reload } = useAsync(
    () => api.get<{ offer: Offer }>(`/api/v1/waitlist/offers/${token}`),
    [token],
  );

  const [claimError, setClaimError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expired, setExpired] = useState(false);

  async function claim() {
    if (!user) {
      navigate('/login', { state: { from: { pathname: `/offers/${token}` } } });
      return;
    }
    setClaimError(null);
    setBusy(true);
    try {
      const { booking } = await api.post<{ booking: { id: string } }>(
        `/api/v1/waitlist/offers/${token}/accept`,
      );
      navigate(`/bookings/${booking.id}`);
    } catch (err) {
      setClaimError(messageFor(err));
      // 410 means it went to the next person while this page sat open.
      if (err instanceof ApiClientError && err.status === 410) setExpired(true);
      reload();
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Skeleton count={1} height={320} />;

  if (error) {
    return (
      <Card className="offer offer--gone">
        <h1 className="offer__title">This offer has closed</h1>
        <p className="offer__body">{error}</p>
        <Link to="/" className="btn btn--ghost">
          Browse events
        </Link>
      </Card>
    );
  }

  if (!data) return null;
  const offer = data.offer;

  return (
    <Card className="offer">
      <p className="offer__eyebrow">A seat opened up</p>
      <h1 className="offer__title">{offer.eventTitle}</h1>

      <dl className="offer__facts">
        <div>
          <dt>When</dt>
          <dd>
            {formatShowDate(offer.startsAt)} · {formatShowTime(offer.startsAt)}
          </dd>
        </div>
        <div>
          <dt>Where</dt>
          <dd>{offer.venue}</dd>
        </div>
        <div>
          <dt>Category</dt>
          <dd>{offer.category}</dd>
        </div>
        <div>
          <dt>Price</dt>
          <dd>{formatPrice(offer.price)}</dd>
        </div>
      </dl>

      <p className="offer__timer">
        Yours for <HoldCountdown expiresAt={offer.expiresAt} onExpire={() => setExpired(true)} /> —
        after that it goes to the next person in line.
      </p>

      {claimError && <Alert>{claimError}</Alert>}

      <Button variant="cta" full loading={busy} disabled={expired} onClick={claim}>
        {expired ? 'Offer expired' : user ? 'Claim this seat' : 'Log in to claim'}
      </Button>

      {!user && (
        <p className="offer__note">
          Sign in with the account the offer was sent to. An offer can only be claimed by the person
          it was made to.
        </p>
      )}
    </Card>
  );
}
