import type { SeatView } from '@ticket/shared';
import './SeatMap.css';

type Props = {
  seats: SeatView[];
  selected: Set<string>;
  onToggle: (seat: SeatView) => void;
  disabled?: boolean;
};

/** What a seat can be, from this viewer's point of view. */
type Kind = 'available' | 'selected' | 'mine' | 'held' | 'offered' | 'booked';

function kindOf(seat: SeatView, selected: Set<string>): Kind {
  if (seat.heldByMe) return 'mine';
  if (selected.has(seat.id)) return 'selected';
  if (seat.status === 'AVAILABLE') return 'available';
  if (seat.status === 'HELD') return 'held';
  if (seat.status === 'OFFERED') return 'offered';
  return 'booked';
}

const LABEL: Record<Kind, string> = {
  available: 'available',
  selected: 'selected',
  mine: 'held by you',
  held: 'held by someone else',
  offered: 'offered to a waitlisted customer',
  booked: 'booked',
};

const takeable = (k: Kind) => k === 'available' || k === 'selected';

/**
 * A centre-stage venue stores radial coordinates — `radius · cos θ` — so its
 * seats land on fractional, often negative, positions. An end-stage venue is
 * always a non-negative integer grid.
 *
 * ponytail: read the layout off the coordinates rather than plumbing
 * `stageLayout` through the seat-map endpoint. If a third layout ever emits
 * integers without being a grid, pass the layout in explicitly instead.
 */
const isRadial = (seats: SeatView[]) =>
  seats.some(
    (s) => !Number.isInteger(s.posX) || !Number.isInteger(s.posY) || s.posX < 0 || s.posY < 0,
  );

export function SeatMap({ seats, selected, onToggle, disabled = false }: Props) {
  if (seats.length === 0) return null;

  const minX = Math.min(...seats.map((s) => s.posX));
  const minY = Math.min(...seats.map((s) => s.posY));
  const cols = Math.max(...seats.map((s) => s.posX)) - minX + 1;
  const rows = Math.max(...seats.map((s) => s.posY)) - minY + 1;
  const radial = isRadial(seats);

  // One label per grid row, taken from the seats sitting on it. Customers read
  // their ticket as "row D", so the map has to say where row D is.
  const rowLabels = radial
    ? []
    : [...new Map(seats.map((s) => [s.posY, s.row])).entries()].sort((a, b) => a[0] - b[0]);

  const seatButton = (seat: SeatView) => {
    const kind = kindOf(seat, selected);
    const label = `${seat.section} row ${seat.row} seat ${seat.number}`;
    return (
      <button
        key={seat.id}
        type="button"
        className={`seat seat--${kind}`}
        style={
          radial
            ? {
                position: 'absolute',
                left: `calc(${seat.posX - minX} * var(--seat-pitch))`,
                top: `calc(${seat.posY - minY} * var(--seat-pitch))`,
              }
            : { gridColumn: seat.posX - minX + 1, gridRow: seat.posY - minY + 1 }
        }
        // A booked seat is not a control. Disabling it keeps it out of
        // the tab order instead of offering a dead target.
        disabled={disabled || !takeable(kind)}
        aria-pressed={kind === 'selected'}
        // Status is in the name, not conveyed by colour alone.
        aria-label={`${label}, ${seat.categoryName}, ${LABEL[kind]}`}
        title={`${seat.row}${seat.number} · ${seat.categoryName}`}
        onClick={() => onToggle(seat)}
      >
        <span aria-hidden="true">{seat.number}</span>
      </button>
    );
  };

  return (
    <div className="seatmap">
      <div className="seatmap__scroll">
        {/*
          Buttons rather than an SVG: each seat is then a real focusable control
          with a real accessible name, so the map is operable by keyboard and
          readable by a screen reader for free. That holds in both layouts —
          only the positioning rule differs.
        */}
        {radial ? (
          <div
            className="seatmap__round"
            role="group"
            aria-label="Seat map, seating in the round"
            style={{
              width: `calc(${cols} * var(--seat-pitch))`,
              height: `calc(${rows} * var(--seat-pitch))`,
            }}
          >
            {seats.map(seatButton)}
            <p className="seatmap__stage" aria-hidden="true">
              Stage
            </p>
          </div>
        ) : (
          <div className="seatmap__withrows">
            <div
              className="seatmap__rowlabels"
              aria-hidden="true"
              style={{ gridTemplateRows: `repeat(${rows}, var(--seat-size))` }}
            >
              {rowLabels.map(([y, row]) => (
                <span key={y} style={{ gridRow: y - minY + 1 }}>
                  {row}
                </span>
              ))}
            </div>
            <div
              className="seatmap__grid"
              role="group"
              aria-label="Seat map"
              style={{
                gridTemplateColumns: `repeat(${cols}, var(--seat-size))`,
                gridTemplateRows: `repeat(${rows}, var(--seat-size))`,
              }}
            >
              {seats.map(seatButton)}
            </div>
          </div>
        )}
        {!radial && <p className="seatmap__screen">Screen</p>}
      </div>

      <ul className="seatmap__legend">
        {(['available', 'selected', 'mine', 'held', 'booked'] as const).map((kind) => (
          <li key={kind}>
            <span className={`seat seat--${kind} seat--chip`} aria-hidden="true" />
            {LABEL[kind]}
          </li>
        ))}
      </ul>
    </div>
  );
}
