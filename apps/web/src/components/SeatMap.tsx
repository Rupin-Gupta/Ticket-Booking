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

export function SeatMap({ seats, selected, onToggle, disabled = false }: Props) {
  if (seats.length === 0) return null;

  const minX = Math.min(...seats.map((s) => s.posX));
  const minY = Math.min(...seats.map((s) => s.posY));
  const cols = Math.max(...seats.map((s) => s.posX)) - minX + 1;
  const rows = Math.max(...seats.map((s) => s.posY)) - minY + 1;

  return (
    <div className="seatmap">
      <div className="seatmap__scroll">
        {/*
          A grid of buttons rather than an SVG: each seat is then a real
          focusable control with a real accessible name, so the map is
          operable by keyboard and readable by a screen reader for free.
        */}
        <div
          className="seatmap__grid"
          role="group"
          aria-label="Seat map"
          style={{
            gridTemplateColumns: `repeat(${cols}, var(--seat-size))`,
            gridTemplateRows: `repeat(${rows}, var(--seat-size))`,
          }}
        >
          {seats.map((seat) => {
            const kind = kindOf(seat, selected);
            const label = `${seat.section} row ${seat.row} seat ${seat.number}`;
            return (
              <button
                key={seat.id}
                type="button"
                className={`seat seat--${kind}`}
                style={{ gridColumn: seat.posX - minX + 1, gridRow: seat.posY - minY + 1 }}
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
          })}
        </div>
        <p className="seatmap__screen">Screen</p>
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
