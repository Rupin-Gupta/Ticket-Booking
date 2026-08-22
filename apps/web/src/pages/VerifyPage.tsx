import { useParams } from 'react-router-dom';
import { api } from '../lib/api.js';
import { useAsync } from '../lib/useAsync.js';
import { formatShowDate, formatShowTime } from '../lib/format.js';
import { AlertIcon, CheckIcon } from '../components/icons.js';
import { Card, Skeleton } from '../components/ui.js';
import './ticket.css';

type Ticket = {
  valid: boolean;
  status: 'CONFIRMED' | 'CANCELLED';
  reference: string;
  eventTitle: string;
  venue: string;
  startsAt: string;
  seats: string[];
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
  const { data, error, loading } = useAsync(
    () => api.get<{ ticket: Ticket }>(`/api/v1/verify/${token}`),
    [token],
  );

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

  return (
    <Card className={`verify ${t.valid ? 'verify--good' : 'verify--bad'}`}>
      {t.valid ? <CheckIcon size={40} /> : <AlertIcon size={40} />}
      <h1 className="verify__verdict">{t.valid ? 'Valid ticket' : 'Cancelled'}</h1>
      <p className="verify__detail">
        {t.valid ? 'Admit the holder.' : 'This booking was cancelled. Do not admit.'}
      </p>

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
