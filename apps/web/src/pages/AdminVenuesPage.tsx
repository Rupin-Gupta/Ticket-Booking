import { useState } from 'react';
import { api } from '../lib/api.js';
import { messageFor } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import type { EventType } from '@ticket/shared';
import type { StageLayout, VenueDetail, VenueSummary } from '../lib/types.js';
import { Alert, Button, Card, EmptyState, Field, Select, Skeleton } from '../components/ui.js';
import './manage.css';

export function AdminVenuesPage() {
  const venues = useAsync(() => api.get<{ venues: VenueSummary[] }>('/api/v1/venues'), []);
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div className="manage">
      <header className="manage__head">
        <h1 className="manage__title">Venues</h1>
        <p className="manage__sub prose">
          A venue owns its seats. Organisers then price those sections per event, so one venue can
          host many events without its layout being rebuilt.
        </p>
      </header>

      <div className="manage__grid">
        <div className="stack">
          <CreateVenue onCreated={venues.reload} />

          <section aria-labelledby="venue-list-heading">
            <h2 id="venue-list-heading" className="manage__h2">
              All venues
            </h2>
            {venues.loading && <Skeleton count={2} height={64} />}
            {venues.error && <Alert>{venues.error}</Alert>}
            {venues.data?.venues.length === 0 && (
              <EmptyState title="No venues yet.">Create one to get started.</EmptyState>
            )}
            <ul className="rows">
              {venues.data?.venues.map((venue) => (
                <li key={venue.id}>
                  <button
                    type="button"
                    className={`row ${selected === venue.id ? 'row--on' : ''}`}
                    onClick={() => setSelected(venue.id)}
                    aria-pressed={selected === venue.id}
                  >
                    <span className="row__main">
                      <span className="row__name">{venue.name}</span>
                      <span className="row__note">{venue.address}</span>
                    </span>
                    <span className="row__count">
                      {venue._count.seats} {venue._count.seats === 1 ? 'seat' : 'seats'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>

        {selected ? (
          <VenueEditor
            venueId={selected}
            onChanged={venues.reload}
            onDeleted={() => {
              setSelected(null);
              venues.reload();
            }}
          />
        ) : (
          <Card className="pad">
            <EmptyState title="Select a venue">
              Pick one on the left to add seat blocks and see its layout.
            </EmptyState>
          </Card>
        )}
      </div>
    </div>
  );
}

function CreateVenue({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState('');
  const [address, setAddress] = useState('');
  const [stageLayout, setStageLayout] = useState<StageLayout>('END_STAGE');
  const [allowsMovies, setAllowsMovies] = useState(true);
  const [allowsConcerts, setAllowsConcerts] = useState(true);
  const [turnaroundMinutes, setTurnaroundMinutes] = useState(15);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // A hall in the round cannot show a film, and the API refuses the pair. Say so
  // here rather than letting the server be the first to mention it.
  const centreStage = stageLayout === 'CENTRE_STAGE';
  const allowedEventTypes: EventType[] = [
    ...(allowsMovies && !centreStage ? (['MOVIE'] as const) : []),
    ...(allowsConcerts ? (['CONCERT'] as const) : []),
  ];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.post('/api/v1/venues', {
        name,
        address,
        stageLayout,
        allowedEventTypes,
        turnaroundMinutes,
      });
      setName('');
      setAddress('');
      onCreated();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="pad">
      <h2 className="manage__h2">New venue</h2>
      <form className="stack" onSubmit={submit} noValidate>
        {error && <Alert>{error}</Alert>}
        <Field
          label="Name"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="The Regal"
        />
        <Field
          label="Address"
          required
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder="12 Marine Drive, Mumbai"
        />
        <Select
          label="Stage layout"
          value={stageLayout}
          onChange={(e) => setStageLayout(e.target.value as StageLayout)}
          hint="Decides how seats are generated. It cannot be changed once seats exist — build a second venue instead."
        >
          <option value="END_STAGE">End stage — rows facing a screen or stage</option>
          <option value="CENTRE_STAGE">Centre stage — seating in the round</option>
        </Select>

        <fieldset className="checks">
          <legend>Event types allowed</legend>
          <label className="check">
            <input
              type="checkbox"
              checked={allowsMovies && !centreStage}
              disabled={centreStage}
              onChange={(e) => setAllowsMovies(e.target.checked)}
            />
            Movies
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={allowsConcerts}
              onChange={(e) => setAllowsConcerts(e.target.checked)}
            />
            Concerts
          </label>
          {centreStage && (
            <p className="manage__hint">
              Nobody projects a film in the round, so a centre-stage venue hosts concerts only.
            </p>
          )}
        </fieldset>

        <Field
          label="Turnaround (minutes)"
          type="number"
          min={0}
          max={240}
          value={turnaroundMinutes}
          onChange={(e) => setTurnaroundMinutes(Number(e.target.value))}
          hint="How long the room needs between shows — emptying, cleaning, resetting. It blocks the next booking of this venue."
        />

        <Button
          type="submit"
          loading={busy}
          disabled={!name || !address || allowedEventTypes.length === 0}
        >
          Create venue
        </Button>
      </form>
    </Card>
  );
}

function VenueEditor({
  venueId,
  onChanged,
  onDeleted,
}: {
  venueId: string;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const venue = useAsync(
    () => api.get<{ venue: VenueDetail }>(`/api/v1/venues/${venueId}`),
    [venueId],
  );

  const [section, setSection] = useState('');
  const [rows, setRows] = useState(3);
  const [seatsPerRow, setSeatsPerRow] = useState(10);
  const [arcStartDegrees, setArcStartDegrees] = useState(0);
  const [arcSpanDegrees, setArcSpanDegrees] = useState(360);
  const [error, setError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const centreStage = venue.data?.venue.stageLayout === 'CENTRE_STAGE';

  async function addBlock(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/venues/${venueId}/seats`, {
        section,
        rows,
        seatsPerRow,
        // Only meaningful in the round; the API defaults them for a grid.
        ...(centreStage ? { arcStartDegrees, arcSpanDegrees } : {}),
      });
      setSection('');
      venue.reload();
      onChanged();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    const name = venue.data?.venue.name ?? 'this venue';
    if (!confirm(`Delete ${name} and its seats? This cannot be undone.`)) return;
    setDeleteError(null);
    setBusy(true);
    try {
      await api.del(`/api/v1/venues/${venueId}`);
      onDeleted();
    } catch (err) {
      // The API refuses while events still point here, and names how many.
      setDeleteError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <Card className="pad">
        <h2 className="manage__h2">Add a seat block</h2>
        <p className="manage__hint">
          {centreStage
            ? 'Rows become rings around the stage, labelled A outwards from the centre. Each block starts outside the ones already there.'
            : 'Rows are labelled A onwards. Each block is placed below the ones already there, so sections stack instead of overlapping.'}
        </p>
        <form className="stack" onSubmit={addBlock} noValidate>
          {error && <Alert>{error}</Alert>}
          <Field
            label="Section name"
            required
            value={section}
            onChange={(e) => setSection(e.target.value)}
            placeholder="Premium"
            hint="Organisers price seats by section."
          />
          <div className="pair">
            <Field
              label="Rows"
              type="number"
              min={1}
              max={26}
              required
              value={rows}
              onChange={(e) => setRows(Number(e.target.value))}
            />
            <Field
              label="Seats per row"
              type="number"
              min={1}
              max={60}
              required
              value={seatsPerRow}
              onChange={(e) => setSeatsPerRow(Number(e.target.value))}
            />
          </div>
          {centreStage && (
            <div className="pair">
              <Field
                label="Arc starts at (°)"
                type="number"
                min={0}
                max={360}
                value={arcStartDegrees}
                onChange={(e) => setArcStartDegrees(Number(e.target.value))}
                hint="0° is due right of the stage."
              />
              <Field
                label="Arc spans (°)"
                type="number"
                min={1}
                max={360}
                value={arcSpanDegrees}
                onChange={(e) => setArcSpanDegrees(Number(e.target.value))}
                hint="360° wraps the stage completely."
              />
            </div>
          )}
          <p className="manage__total">
            Adds <strong>{rows * seatsPerRow}</strong> seats.
          </p>
          <Button type="submit" loading={busy} disabled={!section}>
            Add block
          </Button>
        </form>
      </Card>

      <Card className="pad">
        <h2 className="manage__h2">Layout</h2>
        {venue.loading && <Skeleton count={1} height={140} />}
        {venue.error && <Alert>{venue.error}</Alert>}
        {venue.data && (
          <>
            <p className="manage__hint">
              {centreStage ? 'Seating in the round' : 'End stage'} ·{' '}
              {venue.data.venue.allowedEventTypes
                .map((t) => (t === 'MOVIE' ? 'movies' : 'concerts'))
                .join(' and ')}{' '}
              · {venue.data.venue.turnaroundMinutes} min turnaround
            </p>
            <SeatPreview seats={venue.data.venue.seats} />
          </>
        )}
      </Card>

      <Card className="pad">
        <h2 className="manage__h2">Danger zone</h2>
        {deleteError && <Alert>{deleteError}</Alert>}
        <p className="manage__hint">
          Deleting a venue removes its seats too. It is refused while any event still uses it — an
          event whose venue vanished cannot tell a customer where to turn up.
        </p>
        <Button variant="quiet" loading={busy} onClick={remove}>
          Delete this venue
        </Button>
      </Card>
    </div>
  );
}

/**
 * Static preview of the stored posX / posY grid. Not the bookable seat map —
 * that arrives in Phase 3 with live status — but it renders the same
 * coordinates, so a layout that looks wrong here is wrong there too.
 */
function SeatPreview({ seats }: { seats: VenueDetail['seats'] }) {
  if (seats.length === 0) {
    return <EmptyState title="No seats yet.">Add a block above to build the layout.</EmptyState>;
  }

  const xs = seats.map((s) => s.posX);
  const ys = seats.map((s) => s.posY);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const cols = Math.max(...xs) - minX + 1;
  const rowCount = Math.max(...ys) - minY + 1;

  // Centre-stage seats are radius·cos/sin, so they are fractional and often
  // negative. A CSS grid cannot place them; absolute offsets can place both.
  const radial = seats.some(
    (s) => !Number.isInteger(s.posX) || !Number.isInteger(s.posY) || s.posX < 0 || s.posY < 0,
  );

  const sections = [...new Set(seats.map((s) => s.section))];

  return (
    <>
      <div className="preview__scroll">
        <div
          className={radial ? 'preview preview--round' : 'preview'}
          style={
            radial
              ? { width: `${cols * 14}px`, height: `${rowCount * 14}px` }
              : {
                  gridTemplateColumns: `repeat(${cols}, 14px)`,
                  gridTemplateRows: `repeat(${rowCount}, 14px)`,
                }
          }
        >
          {seats.map((seat) => (
            <span
              key={seat.id}
              className="preview__seat"
              style={{
                ...(radial
                  ? {
                      position: 'absolute' as const,
                      left: `${(seat.posX - minX) * 14}px`,
                      top: `${(seat.posY - minY) * 14}px`,
                    }
                  : { gridColumn: seat.posX - minX + 1, gridRow: seat.posY - minY + 1 }),
                // Cycles the section palette; the legend below names them, so
                // colour is a convenience rather than the only signal.
                background: `var(--seat-${sections.indexOf(seat.section) % 2 === 0 ? 'free' : 'offered'}-bg)`,
                borderColor: `var(--seat-${sections.indexOf(seat.section) % 2 === 0 ? 'free' : 'offered'})`,
              }}
              title={`${seat.section} ${seat.row}${seat.number}`}
            />
          ))}
        </div>
        <p className="preview__screen">{radial ? 'Stage at the centre' : 'Screen'}</p>
      </div>
      <ul className="legend">
        {sections.map((name, i) => (
          <li key={name}>
            <i
              style={{
                background: `var(--seat-${i % 2 === 0 ? 'free' : 'offered'}-bg)`,
                borderColor: `var(--seat-${i % 2 === 0 ? 'free' : 'offered'})`,
              }}
            />
            {name} · {seats.filter((s) => s.section === name).length} seats
          </li>
        ))}
      </ul>
    </>
  );
}
