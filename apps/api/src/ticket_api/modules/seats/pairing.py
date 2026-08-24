"""
Wheelchair spaces and their companion seats are one unit.

**A wheelchair space and its companion are held, booked and released together.
Neither half is separately obtainable.**

That is the part missing from real platforms: they sell accessible seats, but
they do not guarantee that the person who needs assistance cannot be seated
apart from the person providing it. Here that is not a UI convention — it is
enforced server-side, so it holds however the request was made.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Seat, SeatAccessType, ShowSeat


async def expand_pairs(session: AsyncSession, show_id: str, seat_ids: list[str]) -> list[str]:
    """
    Widens a set of requested `ShowSeat` ids to include every paired partner.

    **Called before the lock, never inside it.** This reads static venue
    geometry — which chair is beside which — not contended state, so it costs
    nothing that other contenders are waiting on. The caller still sorts the
    resulting set by id before locking, so the deadlock guarantee is untouched:
    a wider set is still an ordered set.

    Requesting either half gets you both. Requesting both gets you both once.
    """
    rows = (
        await session.execute(
            select(ShowSeat.id, Seat.id, Seat.access_type, Seat.companion_of_id)
            .join(Seat, Seat.id == ShowSeat.seat_id)
            .where(ShowSeat.id.in_(seat_ids))
        )
    ).all()

    wanted: set[str] = set(seat_ids)
    partners: list[str] = []
    for _show_seat_id, physical_id, access_type, companion_of_id in rows:
        if access_type is SeatAccessType.COMPANION and companion_of_id:
            partners.append(companion_of_id)  # the space this companion belongs to
        elif access_type is SeatAccessType.WHEELCHAIR_SPACE:
            partners.append(physical_id)  # find whoever companions it

    if not partners:
        return sorted(wanted)

    # One query for both directions: the space a companion points at, and the
    # companions pointing at a space.
    extra = (
        (
            await session.execute(
                select(ShowSeat.id)
                .join(Seat, Seat.id == ShowSeat.seat_id)
                .where(
                    ShowSeat.show_id == show_id,
                    or_(Seat.id.in_(partners), Seat.companion_of_id.in_(partners)),
                )
            )
        )
        .scalars()
        .all()
    )

    wanted.update(extra)
    return sorted(wanted)
