"""seat signals

Revision ID: b83f5e1d70aa
Revises: a4d17c2b91e0
Create Date: 2026-08-24 20:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b83f5e1d70aa"
down_revision: str | Sequence[str] | None = "a4d17c2b91e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEAT_EVENT_KIND = postgresql.ENUM(
    "HELD", "RELEASED", "EXPIRED", "BOOKED", name="SeatEventKind", create_type=False
)


def upgrade() -> None:
    """
    Append-only seat outcomes, plus the per-event publish toggle.

    The table is deliberately not a set of counters on Seat: a counter there
    would be locked inside the hold transaction, and a physical seat is shared
    by every show at its venue, so two customers holding A12 on different nights
    would serialise against each other.
    """
    SEAT_EVENT_KIND.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "SeatEvent",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("seatId", sa.Text(), sa.ForeignKey("Seat.id"), nullable=False),
        # Not a foreign key on purpose: these outlive the show they describe.
        # Deleting an event must not take the venue's accumulated signal with it.
        sa.Column("showId", sa.Text(), nullable=False),
        sa.Column("kind", SEAT_EVENT_KIND, nullable=False),
        sa.Column(
            "at",
            postgresql.TIMESTAMP(precision=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("SeatEvent_seatId_kind_idx", "SeatEvent", ["seatId", "kind"])
    op.create_index("SeatEvent_showId_at_idx", "SeatEvent", ["showId", "at"])

    op.add_column(
        "Event",
        sa.Column(
            "publishSeatSignals",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("Event", "publishSeatSignals")
    op.drop_index("SeatEvent_showId_at_idx", table_name="SeatEvent")
    op.drop_index("SeatEvent_seatId_kind_idx", table_name="SeatEvent")
    op.drop_table("SeatEvent")
    SEAT_EVENT_KIND.drop(op.get_bind(), checkfirst=True)
