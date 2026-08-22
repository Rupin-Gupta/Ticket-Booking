import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPriceRange, formatShowDate, formatShowTime, isoDate } from '../lib/format.js';
import type { EventSummary, VenueSummary } from '../lib/types.js';
import { Alert, EmptyState, Select, Skeleton } from '../components/ui.js';
import './events.css';

type Filters = { q: string; type: string; venueId: string; from: string };

const EMPTY: Filters = { q: '', type: '', venueId: '', from: '' };

export function EventsPage() {
  const [filters, setFilters] = useState<Filters>(EMPTY);

  const venues = useAsync(() => api.get<{ venues: VenueSummary[] }>('/api/v1/venues'), []);

  const query = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v !== '') as [string, string][],
  ).toString();

  const events = useAsync(
    () => api.get<{ events: EventSummary[]; total: number }>(`/api/v1/events?${query}`),
    [query],
  );

  const set = (key: keyof Filters) => (e: { target: { value: string } }) =>
    setFilters((f) => ({ ...f, [key]: e.target.value }));

  const filtered = query.length > 0;

  return (
    <div className="events">
      <section className="events__hero">
        <p className="events__eyebrow">Movies and concerts</p>
        <h1 className="events__title">Find your seat</h1>
        <p className="events__sub prose">
          Pick from a live seat map, hold your seats while you check out, and get a QR ticket by
          email.
        </p>
      </section>

      {/* A form, so Enter submits and a screen reader announces the region */}
      <form className="filters" role="search" onSubmit={(e) => e.preventDefault()}>
        <div className="field filters__search">
          <label className="sr-only" htmlFor="event-search">
            Search events by title
          </label>
          <input
            id="event-search"
            className="field__input"
            type="search"
            placeholder="Search by title"
            value={filters.q}
            onChange={set('q')}
          />
        </div>

        <Select label="Type" showLabel={false} value={filters.type} onChange={set('type')}>
          <option value="">All types</option>
          <option value="MOVIE">Movies</option>
          <option value="CONCERT">Concerts</option>
        </Select>

        <Select label="Venue" showLabel={false} value={filters.venueId} onChange={set('venueId')}>
          <option value="">All venues</option>
          {venues.data?.venues.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </Select>

        <div className="field">
          <label className="sr-only" htmlFor="event-from">
            Showing on or after
          </label>
          {/* Native date input — no picker dependency, and it is keyboard
              accessible and localised for free. */}
          <input
            id="event-from"
            className="field__input"
            type="date"
            value={filters.from}
            onChange={set('from')}
            aria-label="Showing on or after"
          />
        </div>

        {filtered && (
          <button type="button" className="btn btn--quiet" onClick={() => setFilters(EMPTY)}>
            Clear
          </button>
        )}
      </form>

      <div aria-busy={events.loading} aria-live="polite">
        {events.loading && <Skeleton count={3} height={132} />}

        {events.error && <Alert>{events.error}</Alert>}

        {events.data && events.data.events.length === 0 && (
          <EmptyState title="No events match those filters.">
            {filtered
              ? 'Try clearing the filters, or widening the date.'
              : 'Once an organiser publishes an event it will show up here.'}
          </EmptyState>
        )}

        {events.data && events.data.events.length > 0 && (
          <>
            <p className="events__count">
              {events.data.total} {events.data.total === 1 ? 'event' : 'events'}
            </p>
            <ul className="cards">
              {events.data.events.map((event) => (
                <li key={event.id}>
                  <EventCard event={event} />
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

function EventCard({ event }: { event: EventSummary }) {
  const price = formatPriceRange(event.categories.map((c) => c.price));

  return (
    <article className="ecard">
      <div className="ecard__head">
        <span className={`tag tag--${event.type.toLowerCase()}`}>
          {event.type === 'MOVIE' ? 'Movie' : 'Concert'}
        </span>
        {price && <span className="ecard__price">{price}</span>}
      </div>

      <h2 className="ecard__title">
        {/* The whole card is not a link — that would swallow the show buttons.
            The title is, and it stretches its hit area over the card via ::after. */}
        <Link to={`/events/${event.id}`} className="ecard__link">
          {event.title}
        </Link>
      </h2>

      <p className="ecard__venue">{event.venue.name}</p>
      {event.description && <p className="ecard__desc">{event.description}</p>}

      {event.shows.length > 0 ? (
        <ul className="showlist">
          {event.shows.slice(0, 3).map((show) => (
            <li key={show.id}>
              <time className="showchip" dateTime={isoDate(show.startsAt)}>
                <span className="showchip__date">{formatShowDate(show.startsAt)}</span>
                <span className="showchip__time">{formatShowTime(show.startsAt)}</span>
              </time>
            </li>
          ))}
          {event.shows.length > 3 && (
            <li className="showlist__more">+{event.shows.length - 3} more</li>
          )}
        </ul>
      ) : (
        <p className="ecard__none">No upcoming shows.</p>
      )}
    </article>
  );
}
