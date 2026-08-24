import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api.js';
import { messageFor } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPrice } from '../lib/format.js';
import type { OwnEvent, VenueSection, VenueSummary } from '../lib/types.js';
import {
  Alert,
  Button,
  Card,
  EmptyState,
  Field,
  Select,
  Skeleton,
  Textarea,
} from '../components/ui.js';
import './manage.css';

export function OrganiserPage() {
  const events = useAsync(() => api.get<{ events: OwnEvent[] }>('/api/v1/events/mine'), []);
  const venues = useAsync(() => api.get<{ venues: VenueSummary[] }>('/api/v1/venues'), []);
  const [selected, setSelected] = useState<string | null>(null);

  const current = events.data?.events.find((e) => e.id === selected) ?? null;

  return (
    <div className="manage">
      <header className="manage__head">
        <h1 className="manage__title">Your events</h1>
        <p className="manage__sub prose">
          Create an event at a venue, price each of its sections, then schedule shows. Every show
          generates its own seat map the moment it is created.
        </p>
      </header>

      <div className="manage__grid">
        <div className="stack">
          <CreateEvent venues={venues.data?.venues ?? []} onCreated={events.reload} />

          <section aria-labelledby="own-events-heading">
            <h2 id="own-events-heading" className="manage__h2">
              All your events
            </h2>
            {events.loading && <Skeleton count={2} height={64} />}
            {events.error && <Alert>{events.error}</Alert>}
            {events.data?.events.length === 0 && (
              <EmptyState title="No events yet.">Create one above.</EmptyState>
            )}
            <ul className="rows">
              {events.data?.events.map((event) => (
                <li key={event.id}>
                  <button
                    type="button"
                    className={`row ${selected === event.id ? 'row--on' : ''}`}
                    onClick={() => setSelected(event.id)}
                    aria-pressed={selected === event.id}
                  >
                    <span className="row__main">
                      <span className="row__name">{event.title}</span>
                      <span className="row__note">
                        {event.venue.name} · {event._count.shows}{' '}
                        {event._count.shows === 1 ? 'show' : 'shows'}
                      </span>
                    </span>
                    <span className="row__count">{event.categories.length} priced</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>

        {current ? (
          <EventEditor event={current} onChanged={events.reload} />
        ) : (
          <Card className="pad">
            <EmptyState title="Select an event">
              Pick one on the left to price its sections and schedule shows.
            </EmptyState>
          </Card>
        )}
      </div>
    </div>
  );
}

function CreateEvent({ venues, onCreated }: { venues: VenueSummary[]; onCreated: () => void }) {
  const [form, setForm] = useState({ venueId: '', title: '', type: 'MOVIE', description: '' });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.post('/api/v1/events', {
        venueId: form.venueId,
        title: form.title,
        type: form.type,
        // Omit rather than send an empty string — the schema treats it as absent.
        ...(form.description ? { description: form.description } : {}),
      });
      setForm({ venueId: '', title: '', type: 'MOVIE', description: '' });
      onCreated();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  const noVenues = venues.length === 0;

  return (
    <Card className="pad">
      <h2 className="manage__h2">New event</h2>
      {noVenues ? (
        <EmptyState title="No venues exist yet.">
          An admin has to create a venue and its seats first.
        </EmptyState>
      ) : (
        <form className="stack" onSubmit={submit} noValidate>
          {error && <Alert>{error}</Alert>}
          <Field
            label="Title"
            required
            value={form.title}
            onChange={set('title')}
            placeholder="Interstellar (re-release)"
          />
          <div className="pair">
            <Select label="Venue" required value={form.venueId} onChange={set('venueId')}>
              <option value="">Choose a venue</option>
              {venues.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} ({v._count.seats} seats)
                </option>
              ))}
            </Select>
            <Select label="Type" value={form.type} onChange={set('type')}>
              <option value="MOVIE">Movie</option>
              <option value="CONCERT">Concert</option>
            </Select>
          </div>
          <Textarea
            label="Description"
            value={form.description}
            onChange={set('description')}
            placeholder="Back on the big screen, in 70mm."
          />
          <Button type="submit" loading={busy} disabled={!form.title || !form.venueId}>
            Create event
          </Button>
        </form>
      )}
    </Card>
  );
}

function EventEditor({ event, onChanged }: { event: OwnEvent; onChanged: () => void }) {
  const sections = useAsync(
    () => api.get<{ sections: VenueSection[] }>(`/api/v1/venues/${event.venue.id}/sections`),
    [event.venue.id],
  );

  const priced = new Set(event.categories.flatMap((c) => c.sections));
  const unpriced = (sections.data?.sections ?? []).filter((s) => !priced.has(s.name));

  return (
    <div className="stack">
      <Card className="pad">
        <div className="manage__rowhead">
          <h2 className="manage__h2">{event.title}</h2>
          <span className="manage__links">
            <Link to={`/manage/${event.id}`} className="btn btn--ghost">
              Sales &amp; revenue
            </Link>
            <Link to={`/events/${event.id}`} className="btn btn--quiet">
              Public page
            </Link>
          </span>
        </div>

        <h3 className="manage__h3">Pricing</h3>
        {event.categories.length === 0 ? (
          <p className="manage__hint">Nothing priced yet.</p>
        ) : (
          <ul className="rows rows--flat">
            {event.categories.map((c) => (
              <li key={c.id} className="row row--static">
                <span className="row__main">
                  <span className="row__name">{c.name}</span>
                  <span className="row__note">{c.sections.join(', ')}</span>
                </span>
                <span className="row__count">{formatPrice(c.price)}</span>
              </li>
            ))}
          </ul>
        )}

        {/* The show form is unreachable until every section has a price, because
            the server refuses to generate a partial seat map anyway. Saying so
            here beats letting someone hit the 400. */}
        {unpriced.length > 0 && (
          <p className="manage__warn">
            Still unpriced: <strong>{unpriced.map((s) => s.name).join(', ')}</strong>. A show
            cannot be created until every section has a price.
          </p>
        )}

        <AddCategory eventId={event.id} available={unpriced} onAdded={onChanged} />
      </Card>

      <Card className="pad">
        <h2 className="manage__h2">Schedule a show</h2>
        <AddShow eventId={event.id} blocked={unpriced.length > 0} onAdded={onChanged} />
      </Card>
    </div>
  );
}

function AddCategory({
  eventId,
  available,
  onAdded,
}: {
  eventId: string;
  available: VenueSection[];
  onAdded: () => void;
}) {
  const [name, setName] = useState('');
  const [price, setPrice] = useState('');
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const toggle = (section: string) =>
    setChosen((c) => (c.includes(section) ? c.filter((s) => s !== section) : [...c, section]));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/events/${eventId}/categories`, { name, price, sections: chosen });
      setName('');
      setPrice('');
      setChosen([]);
      onAdded();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  if (available.length === 0) {
    return <p className="manage__ok">Every section in this venue has a price.</p>;
  }

  return (
    <form className="stack manage__subform" onSubmit={submit} noValidate>
      <h3 className="manage__h3">Price a section</h3>
      {error && <Alert>{error}</Alert>}
      <div className="pair">
        <Field
          label="Category name"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Premium"
        />
        <Field
          label="Price"
          type="number"
          min={0}
          step="1"
          required
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          placeholder="450"
        />
      </div>

      <fieldset className="checks">
        <legend className="field__label">Sections this covers</legend>
        {available.map((section) => (
          <label key={section.name} className="check">
            <input
              type="checkbox"
              checked={chosen.includes(section.name)}
              onChange={() => toggle(section.name)}
            />
            {section.name}
            {/* The seat count is the point: pricing a section blind is how an
                organiser finds out at show-creation time that it was 400 seats. */}
            <small>{section.seatCount} seats</small>
          </label>
        ))}
      </fieldset>

      <Button type="submit" loading={busy} disabled={!name || price === '' || chosen.length === 0}>
        Add category
      </Button>
    </form>
  );
}

function AddShow({
  eventId,
  blocked,
  onAdded,
}: {
  eventId: string;
  blocked: boolean;
  onAdded: () => void;
}) {
  const [startsAt, setStartsAt] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setDone(null);
    setBusy(true);
    try {
      const res = await api.post<{ show: { seatCount: number } }>(
        `/api/v1/events/${eventId}/shows`,
        {
          startsAt: new Date(startsAt).toISOString(),
        },
      );
      setDone(`Show created with ${res.show.seatCount} seats.`);
      setStartsAt('');
      onAdded();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      {error && <Alert>{error}</Alert>}
      {done && <Alert tone="success">{done}</Alert>}
      <Field
        label="Starts at"
        type="datetime-local"
        required
        value={startsAt}
        onChange={(e) => setStartsAt(e.target.value)}
        hint="Must be in the future."
        disabled={blocked}
      />
      <Button type="submit" loading={busy} disabled={blocked || !startsAt}>
        Create show and generate seats
      </Button>
    </form>
  );
}
