import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../lib/api.js';
import { messageFor, useAuth } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { formatShowDate, formatShowTime } from '../lib/format.js';
import { AlertIcon, CheckIcon } from '../components/icons.js';
import { Alert, Button, Card, Skeleton } from '../components/ui.js';
import './ticket.css';

type Ticket = {
  valid: boolean;
  status: 'CONFIRMED' | 'CANCELLED';
  reference: string;
  eventTitle: string;
  venue: string;
  startsAt: string;
  seats: string[];
  checkedInAt: string | null;
};

/**
 * Where a scanned QR lands. Public — the person on the door is not logged in —
 * and deliberately blunt: someone glancing at a phone in a queue needs the
 * verdict in one glance, not a layout to read.
 *
 * Shows nothing about the customer. A QR code gets photographed and forwarded.
 */
export function VerifyPage() {
  const { token } = useParams<{ token: string }>();
  const { user } = useAuth();
  const { data, error, loading, reload } = useAsync(
    () => api.get<{ ticket: Ticket }>(`/api/v1/verify/${token}`),
    [token],
  );
  const [admitError, setAdmitError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Reading a ticket is public; admitting one is not. Door staff sign in, so a
  // photographed QR cannot be burned by a stranger before its owner arrives.
  const canAdmit = user?.role === 'ORGANISER' || user?.role === 'ADMIN';

  async function admit() {
    setAdmitError(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/verify/${token}/check-in`, {});
      reload();
    } catch (err) {
      // "Already admitted at 19:42" arrives here, and is the whole point.
      setAdmitError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Skeleton count={1} height={260} />;

  // A token that resolves to nothing is a forgery or a typo — same verdict.
  if (error || !data) {
    return (
      <Card className="verify verify--bad">
        <AlertIcon size={40} />
        <h1 className="verify__verdict">Not a valid ticket</h1>
        <p className="verify__detail">This code is not recognised.</p>
      </Card>
    );
  }

  const t = data.ticket;
  const used = t.checkedInAt !== null;
  // A ticket already through the door is not a valid one to admit again, and
  // the door needs that as the headline rather than as small print.
  const verdict = !t.valid ? 'bad' : used ? 'used' : 'good';

  return (
    <Card className={`verify verify--${verdict === 'good' ? 'good' : 'bad'}`}>
      {verdict === 'good' ? <CheckIcon size={40} /> : <AlertIcon size={40} />}
      <h1 className="verify__verdict">
        {verdict === 'good'
          ? 'Valid ticket'
          : verdict === 'used'
            ? 'Already admitted'
            : 'Cancelled'}
      </h1>
      <p className="verify__detail">
        {verdict === 'good'
          ? 'Admit the holder.'
          : verdict === 'used'
            ? `Admitted at ${formatShowTime(t.checkedInAt as string)}. Do not admit again.`
            : 'This booking was cancelled. Do not admit.'}
      </p>

      {canAdmit && t.valid && !used && (
        <>
          {admitError && <Alert>{admitError}</Alert>}
          <Button variant="cta" full loading={busy} onClick={admit}>
            Admit
          </Button>
        </>
      )}

      <dl className="verify__facts">
        <div>
          <dt>Event</dt>
          <dd>{t.eventTitle}</dd>
        </div>
        <div>
          <dt>When</dt>
          <dd>
            {formatShowDate(t.startsAt)} · {formatShowTime(t.startsAt)}
          </dd>
        </div>
        <div>
          <dt>Venue</dt>
          <dd>{t.venue}</dd>
        </div>
        <div>
          <dt>Seats</dt>
          <dd>{t.seats.join(', ')}</dd>
        </div>
        <div>
          <dt>Reference</dt>
          <dd className="mono">{t.reference}</dd>
        </div>
      </dl>
    </Card>
  );
}
