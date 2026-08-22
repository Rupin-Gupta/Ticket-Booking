import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { SeatView } from '@ticket/shared';
import { api } from '../lib/api.js';
import { messageFor, useAuth } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPrice } from '../lib/format.js';
import { Alert, Button } from './ui.js';
import { HoldCountdown } from './HoldCountdown.js';

type Entry = {
  id: string;
  status: 'WAITING' | 'OFFERED';
  showId: string;
  category: string;
  offerToken: string | null;
  offerExpiresAt: string | null;
  position: number | null;
};

/**
 * Shown under the seat map for every category that has sold out.
 *
 * Only appears when there is genuinely nothing to buy — a join is refused
 * server-side while seats remain, so offering the button early would just
 * produce a 409 the customer cannot act on.
 */
export function WaitlistPanel({ showId, seats }: { showId: string; seats: SeatView[] }) {
  const { user } = useAuth();
  const mine = useAsync(
    () => api.get<{ entries: Entry[] }>('/api/v1/waitlist/me').catch(() => ({ entries: [] })),
    [user?.id ?? ''],
  );

  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A category is sold out when nothing in it is takeable right now. The server
  // decides for real; this only controls whether to show the option.
  const categories = new Map<string, { name: string; price: string; free: number }>();
  for (const seat of seats) {
    const entry = categories.get(seat.categoryId) ?? {
      name: seat.categoryName,
      price: seat.price,
      free: 0,
    };
    if (seat.status === 'AVAILABLE') entry.free += 1;
    categories.set(seat.categoryId, entry);
  }

  const soldOut = [...categories.entries()].filter(([, c]) => c.free === 0);
  const entries = mine.data?.entries.filter((e) => e.showId === showId) ?? [];

  if (soldOut.length === 0 && entries.length === 0) return null;

  async function join(categoryId: string) {
    setError(null);
    setBusy(categoryId);
    try {
      await api.post(`/api/v1/shows/${showId}/waitlist`, { categoryId });
      mine.reload();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(null);
    }
  }

  async function leave(id: string) {
    setBusy(id);
    try {
      await api.del(`/api/v1/waitlist/${id}`);
      mine.reload();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="waitlist" aria-labelledby="waitlist-heading">
      <h2 className="waitlist__title" id="waitlist-heading">
        Sold out
      </h2>
      <p className="waitlist__intro">
        Join the queue and the next cancelled seat is offered to you automatically, in the order
        people joined.
      </p>

      {error && <Alert>{error}</Alert>}

      <ul className="waitlist__list">
        {soldOut.map(([categoryId, category]) => {
          const entry = entries.find((e) => e.category === category.name);
          return (
            <li key={categoryId} className="waitlist__row">
              <span className="waitlist__cat">
                <strong>{category.name}</strong>
                <small>{formatPrice(category.price)}</small>
              </span>

              {entry?.status === 'OFFERED' && entry.offerToken ? (
                <Link to={`/offers/${entry.offerToken}`} className="btn btn--cta">
                  Claim your seat
                  {entry.offerExpiresAt && (
                    <>
                      {' · '}
                      <HoldCountdown expiresAt={entry.offerExpiresAt} />
                    </>
                  )}
                </Link>
              ) : entry ? (
                <span className="waitlist__queued">
                  <span className="waitlist__pos">#{entry.position}</span> in the queue
                  <Button
                    variant="quiet"
                    loading={busy === entry.id}
                    onClick={() => leave(entry.id)}
                  >
                    Leave
                  </Button>
                </span>
              ) : (
                <Button
                  variant="ghost"
                  loading={busy === categoryId}
                  onClick={() => join(categoryId)}
                >
                  {user ? 'Join the waitlist' : 'Log in to join'}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
