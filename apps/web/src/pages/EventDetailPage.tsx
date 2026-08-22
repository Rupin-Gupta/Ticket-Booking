import { Link, useParams } from 'react-router-dom';
import { api } from '../lib/api.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPrice, formatShowDate, formatShowTime, isoDate } from '../lib/format.js';
import type { EventDetail } from '../lib/types.js';
import { Alert, Card, EmptyState, Skeleton } from '../components/ui.js';
import './events.css';
import './eventDetail.css';

export function EventDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, loading } = useAsync(
    () => api.get<{ event: EventDetail }>(`/api/v1/events/${id}`),
    [id],
  );

  if (loading) return <Skeleton count={2} height={200} />;
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;

  const { event } = data;

  return (
    <article className="detail">
      <nav aria-label="Breadcrumb" className="detail__crumbs">
        <Link to="/">Events</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">{event.title}</span>
      </nav>

      <header className="detail__head">
        <span className={`tag tag--${event.type.toLowerCase()}`}>
          {event.type === 'MOVIE' ? 'Movie' : 'Concert'}
        </span>
        <h1 className="detail__title">{event.title}</h1>
        <p className="detail__venue">
          {event.venue.name} · {event.venue.address}
        </p>
        <p className="detail__by">Presented by {event.organiser.name}</p>
        {event.description && <p className="detail__desc prose">{event.description}</p>}
      </header>

      <div className="detail__grid">
        <section aria-labelledby="shows-heading">
          <h2 id="shows-heading" className="detail__h2">
            Pick a show
          </h2>

          {event.shows.length === 0 ? (
            <EmptyState title="No upcoming shows.">
              The organiser has not scheduled a date for this event yet.
            </EmptyState>
          ) : (
            <ul className="shows">
              {event.shows.map((show) => (
                <li key={show.id}>
                  <Link to={`/shows/${show.id}`} className="showrow showrow--link">
                    <time className="showrow__when" dateTime={isoDate(show.startsAt)}>
                      <span className="showrow__date">{formatShowDate(show.startsAt)}</span>
                      <span className="showrow__time">{formatShowTime(show.startsAt)}</span>
                    </time>
                    <span className="showrow__seats">{show._count?.showSeats ?? 0} seats</span>
                    <span className="showrow__soon">Choose seats →</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <Card className="pricing">
          <h2 className="detail__h2">Pricing</h2>
          <dl className="pricing__list">
            {event.categories.map((category) => (
              <div key={category.id} className="pricing__row">
                <dt>
                  {category.name}
                  <span className="pricing__sections">{category.sections.join(', ')}</span>
                </dt>
                <dd>{formatPrice(category.price)}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>
    </article>
  );
}
