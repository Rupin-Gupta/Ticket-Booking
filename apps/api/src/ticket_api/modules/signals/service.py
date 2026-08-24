"""
Seat signals: how often a seat is picked up and put back down.

A seat repeatedly chosen and then un-chosen is one people consider and reject.
That is different information from a cancellation, which is regret *after*
paying — and different again from a seat nobody ever clicks.

Two rules govern this whole module:

1. **Never inside the lock.** Capture happens after the transaction commits,
   alongside the existing broadcasts. The hold path stays at the two round trips
   it was tuned to, and the concurrency guarantee is untouched.
2. **Never state a cause.** "Passed over more often than others in row F" is
   what the data supports. "Obstructed view" is a guess wearing a fact's
   clothes.
"""

from __future__ import annotations

from sqlalchemy import func, select

from ...db import Session
from ...models import Seat, SeatEvent, SeatEventKind

#: Below this many outcomes a seat says nothing. One abandonment is not
#: "100% rejected", and a number computed from three data points invites a
#: confidence nobody should have.
MIN_SAMPLE = 5

#: How much worse than its own row a seat must be before it is worth saying.
SURFACE_ABOVE = 1.5


async def record(events: list[tuple[str, str, SeatEventKind]]) -> None:
    """
    Append seat outcomes. `events` is a list of (seat_id, show_id, kind).

    **Call this after the transaction commits, never within it.**

    Errors are swallowed on purpose. A lost event costs one data point in a
    statistical measure; a raised exception here would cost a customer a
    confirmed booking. Those are not the same, and the ordering of importance
    should be visible in the code rather than assumed.

    The insert is awaited rather than fired into a background task: it happens
    after the commit either way, so the lock is not held any longer, and an
    awaited write is one that tests can actually observe.
    """
    if not events:
        return
    try:
        async with Session() as session:
            session.add_all(
                [
                    SeatEvent(seat_id=seat_id, show_id=show_id, kind=kind)
                    for seat_id, show_id, kind in events
                ]
            )
            await session.commit()
    except Exception as err:  # noqa: BLE001 - a signal must never break a booking
        print(f"[signals] dropped {len(events)} event(s): {type(err).__name__}: {err}")


async def hesitation_by_seat(venue_id: str) -> dict[str, dict[str, float | int]]:
    """
    Hesitation per *physical* seat, across every show at the venue.

    Per venue rather than per show because one show rarely produces enough
    outcomes on one seat to say anything. The physical seat is the thing with a
    view, a pillar in front of it, or a draught.

        hesitation = (released + expired) / (released + expired + booked)

    Returns only seats past `MIN_SAMPLE`, each with its ratio, its sample size,
    and how it compares to the mean of its own row. The row comparison is what
    keeps this honest: a popular show produces abandonments everywhere, and an
    unpopular section produces them in every seat. A seat only stands out if it
    stands out among its neighbours.
    """
    async with Session() as session:
        rows = (
            await session.execute(
                select(SeatEvent.seat_id, SeatEvent.kind, func.count(SeatEvent.id))
                .join(Seat, Seat.id == SeatEvent.seat_id)
                .where(Seat.venue_id == venue_id)
                .group_by(SeatEvent.seat_id, SeatEvent.kind)
            )
        ).all()

        seats = (
            (await session.execute(select(Seat).where(Seat.venue_id == venue_id))).scalars().all()
        )

    tally: dict[str, dict[str, int]] = {}
    for seat_id, kind, count in rows:
        bucket = tally.setdefault(seat_id, {})
        bucket[str(kind)] = int(count)

    row_of = {s.id: f"{s.section}|{s.row}" for s in seats}

    # Two different populations, deliberately.
    #
    # `ratios` is what may be REPORTED: only seats with enough outcomes to carry
    # a number. `baseline` is what they are COMPARED AGAINST: every seat in the
    # row with any data at all.
    #
    # Using the reportable set for both was the first version and it is subtly
    # broken — the only qualifying seat in a row ends up compared against
    # itself, scores exactly 1.0, and can never be surfaced however badly it
    # performs. The comparison needs peers, and a peer with three outcomes is
    # still evidence about the row even though it is not evidence about itself.
    ratios: dict[str, tuple[float, int]] = {}
    baseline: dict[str, tuple[float, int]] = {}
    for seat_id, counts in tally.items():
        passed = counts.get(SeatEventKind.RELEASED, 0) + counts.get(SeatEventKind.EXPIRED, 0)
        booked = counts.get(SeatEventKind.BOOKED, 0)
        sample = passed + booked
        if sample == 0:
            continue
        baseline[seat_id] = (passed / sample, sample)
        if sample >= MIN_SAMPLE:
            ratios[seat_id] = (passed / sample, sample)

    by_row: dict[str, list[float]] = {}
    for seat_id, (ratio, _) in baseline.items():
        by_row.setdefault(row_of.get(seat_id, "?"), []).append(ratio)
    row_mean = {row: sum(vals) / len(vals) for row, vals in by_row.items()}

    out: dict[str, dict[str, float | int]] = {}
    for seat_id, (ratio, sample) in ratios.items():
        mean = row_mean.get(row_of.get(seat_id, "?"), 0.0)
        # A row whose seats are all equally unloved has no outlier in it.
        multiple = round(ratio / mean, 2) if mean > 0 else 1.0
        out[seat_id] = {"ratio": round(ratio, 3), "sample": sample, "rowMultiple": multiple}
    return out


def worth_surfacing(signal: dict[str, float | int]) -> bool:
    """A seat is only worth mentioning if it is clearly worse than its own row."""
    return float(signal.get("rowMultiple", 1.0)) >= SURFACE_ABOVE
