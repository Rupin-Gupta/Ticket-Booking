import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import QRCode from 'qrcode';
import { api } from '../lib/api.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPrice, formatShowDate, formatShowTime, isoDate } from '../lib/format.js';
import { Alert, Card, Skeleton } from '../components/ui.js';
import type { BookingView } from './BookingsPage.js';
import './ticket.css';

/**
 * The ticket the customer shows at the door — the same QR the email carries,
 * rendered from the same token so the two can never disagree.
 */
export function TicketPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, loading } = useAsync(
    () => api.get<{ booking: BookingView }>(`/api/v1/bookings/${id}`),
    [id],
  );
  const [qr, setQr] = useState<string | null>(null);

  const token = data?.booking.qrToken;

  useEffect(() => {
    if (!token) return;
    // Encodes the verification URL, never the booking's own data: a QR
    // carrying raw details is forgeable by anyone with a QR generator.
    QRCode.toDataURL(`${window.location.origin}/verify/${token}`, {
      errorCorrectionLevel: 'M',
      margin: 1,
      width: 320,
    })
      .then(setQr)
      .catch(() => setQr(null));
  }, [token]);

  if (loading) return <Skeleton count={1} height={420} />;
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;

  const b = data.booking;

  return (
    <div className="ticketpage">
      <nav aria-label="Breadcrumb" className="detail__crumbs">
        <Link to="/bookings">Your bookings</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">{b.reference}</span>
      </nav>

      <Card className="ticket">
        <div className="ticket__top">
          <p className="ticket__eyebrow">{b.status === 'CONFIRMED' ? 'Admit' : 'Cancelled'}</p>
          <h1 className="ticket__title">{b.show.title}</h1>
          <p className="ticket__where">
            <time dateTime={isoDate(b.show.startsAt)}>
              {formatShowDate(b.show.startsAt)} · {formatShowTime(b.show.startsAt)}
            </time>
            <br />
            {b.show.venue}, {b.show.address}
          </p>
        </div>

        {/* The perforation. Decorative, so it is hidden from assistive tech. */}
        <div className="ticket__rip" aria-hidden="true">
          <span />
          <span />
        </div>

        <div className="ticket__bottom">
          {b.status === 'CONFIRMED' ? (
            qr ? (
              <img
                className="ticket__qr"
                src={qr}
                width={220}
                height={220}
                alt={`QR code for booking ${b.reference}`}
              />
            ) : (
              <div className="ticket__qr ticket__qr--pending" aria-hidden="true" />
            )
          ) : (
            <p className="ticket__void">This booking was cancelled. The code no longer works.</p>
          )}

          <dl className="ticket__facts">
            <div>
              <dt>Reference</dt>
              <dd className="mono">{b.reference}</dd>
            </div>
            <div>
              <dt>Seats</dt>
              <dd>
                {b.seats.map((s) => (
                  <span key={s.showSeatId} className="ticket__seat">
                    {s.label}
                    <small>{s.section}</small>
                  </span>
                ))}
              </dd>
            </div>
            <div>
              <dt>Total paid</dt>
              <dd>{formatPrice(b.total)}</dd>
            </div>
          </dl>
        </div>
      </Card>

      <p className="ticketpage__note">
        A copy was emailed to you. Either the email or this page gets you in.
      </p>
    </div>
  );
}
