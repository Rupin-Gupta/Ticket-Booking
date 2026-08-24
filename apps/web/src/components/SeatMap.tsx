import type { SeatView } from '@ticket/shared';
import { formatPrice } from '../lib/format.js';
import './SeatMap.css';

type Props = {
  seats: SeatView[];
  selected: Set<string>;
  onToggle: (seat: SeatView) => void;
  disabled?: boolean;
};

/**
 * Access is conveyed by shape and mark, never by colour alone — colour is
 * already carrying status, and a wheelchair space that differs only in hue is
 * invisible to the people most likely to need it.
 */
const ACCESS_MARK: Record<string, string> = {
  WHEELCHAIR_SPACE: '\u267F',
  COMPANION: '+',
  STEP_FREE: '\u2191',
};

const ACCESS_LABEL: Record<string, string> = {
  WHEELCHAIR_SPACE: 'wheelchair space, booked with its companion seat',
  COMPANION: 'companion seat, booked with its wheelchair space',
  STEP_FREE: 'step-free access',
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
 * Tier by price rank, never by category name.
 *
 * "Premium" is a word an organiser chose; £450 is a fact. Ranking by price
 * means the most expensive band always reads as tier 1 whatever it is called,
 * and a venue that names its bands Gold/Silver/Bronze or A/B/C tiers correctly
 * without the map knowing any of those words.
 */
function priceRanks(seats: SeatView[]): Map<string, number> {
  const byCategory = new Map<string, number>();
  for (const s of seats) byCategory.set(s.categoryId, Number(s.price));
  const ordered = [...byCategory.entries()].sort((a, b) => b[1] - a[1]);
  return new Map(ordered.map(([categoryId], i) => [categoryId, i + 1]));
}

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

  const ranks = priceRanks(seats);

  // One band per section, naming what it costs. A seat map without prices makes
  // people click seats to discover them.
  const bands = [...new Map(seats.map((s) => [s.section, s])).values()]
    .map((s) => ({
      section: s.section,
      categoryName: s.categoryName,
      price: formatPrice(s.price),
      tier: ranks.get(s.categoryId) ?? 1,
      seats: seats.filter((x) => x.section === s.section).length,
    }))
    .sort((a, b) => a.tier - b.tier || a.section.localeCompare(b.section));

  const seatButton = (seat: SeatView) => {
    const kind = kindOf(seat, selected);
    const label = `${seat.section} row ${seat.row} seat ${seat.number}`;
    const access = ACCESS_LABEL[seat.accessType] ? `, ${ACCESS_LABEL[seat.accessType]}` : '';
    return (
      <button
        key={seat.id}
        type="button"
        className={[
          'seat',
          `seat--${kind}`,
          `seat--tier${ranks.get(seat.categoryId) ?? 1}`,
          seat.hesitation ? 'seat--passedover' : '',
          seat.accessType !== 'STANDARD'
            ? `seat--access seat--${seat.accessType.toLowerCase()}`
            : '',
        ]
          .filter(Boolean)
          .join(' ')}
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
        aria-label={
          seat.hesitation
            ? `${label}, ${seat.categoryName}, ${LABEL[kind]}, passed over ${seat.hesitation.rowMultiple} times more often than others in row ${seat.row}${access}`
            : `${label}, ${seat.categoryName}, ${LABEL[kind]}${access}`
        }
        title={
          seat.hesitation
            ? // Never a cause. "Passed over more often" is what the data
              // supports; "obstructed view" would be a guess dressed as a fact.
              `${seat.row}${seat.number} · ${seat.categoryName} — passed over ${seat.hesitation.rowMultiple}× more often than other seats in row ${seat.row} (${seat.hesitation.sample} holds)`
            : `${seat.row}${seat.number} · ${seat.categoryName}`
        }
        onClick={() => onToggle(seat)}
      >
        <span aria-hidden="true">{ACCESS_MARK[seat.accessType] ?? seat.number}</span>
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
            {/* The same labels again on the right. In a wide hall the left
                gutter is off-screen by the time you reach the far aisle, which
                is exactly where somebody is hunting for their row. */}
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
          </div>
        )}
        {!radial && <p className="seatmap__screen">Screen</p>}
      </div>

      <ul className="seatmap__bands">
        {bands.map((b) => (
          <li key={b.section} className={`seatmap__band seatmap__band--tier${b.tier}`}>
            <span className="seatmap__bandname">{b.section}</span>
            <span className="seatmap__bandmeta">
              {b.categoryName} · {b.price} · {b.seats} {b.seats === 1 ? 'seat' : 'seats'}
            </span>
          </li>
        ))}
      </ul>

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
