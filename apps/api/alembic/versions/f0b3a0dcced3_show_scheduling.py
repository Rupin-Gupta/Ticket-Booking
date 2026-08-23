"""show scheduling

Revision ID: f0b3a0dcced3
Revises: 1c28c2dd7e14
Create Date: 2026-08-23 20:29:15.236742

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0b3a0dcced3"
down_revision: str | Sequence[str] | None = "1c28c2dd7e14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    A show becomes a booking of a venue for a window of time, so two organisers
    can no longer schedule overlapping shows in one room.

    Columns are added nullable, backfilled, then made NOT NULL, so existing rows
    survive. Existing shows get a 120-minute duration: there is no way to recover
    the real value, and two hours is defensible for both a film and a gig.
    """
    show_status = postgresql.ENUM("SCHEDULED", "CANCELLED", name="ShowStatus")
    show_status.create(op.get_bind(), checkfirst=True)

    op.add_column("Show", sa.Column("venueId", sa.Text(), nullable=True))
    op.add_column("Show", sa.Column("durationMinutes", sa.Integer(), nullable=True))
    op.add_column("Show", sa.Column("endsAt", postgresql.TIMESTAMP(precision=3), nullable=True))
    op.add_column(
        "Show", sa.Column("occupiesUntil", postgresql.TIMESTAMP(precision=3), nullable=True)
    )
    op.add_column(
        "Show",
        sa.Column(
            "status",
            postgresql.ENUM("SCHEDULED", "CANCELLED", name="ShowStatus", create_type=False),
            nullable=False,
            server_default="SCHEDULED",
        ),
    )

    op.execute(
        """
        UPDATE "Show" s
        SET "venueId"         = e."venueId",
            "durationMinutes" = 120,
            "endsAt"          = s."startsAt" + INTERVAL '120 minutes',
            "occupiesUntil"   = s."startsAt" + INTERVAL '120 minutes'
                                + (v."turnaroundMinutes" * INTERVAL '1 minute')
        FROM "Event" e
        JOIN "Venue" v ON v.id = e."venueId"
        WHERE e.id = s."eventId"
        """
    )

    for column in ("venueId", "durationMinutes", "endsAt", "occupiesUntil"):
        op.alter_column("Show", column, nullable=False)

    op.create_index("Show_venueId_startsAt_idx", "Show", ["venueId", "startsAt"])


def downgrade() -> None:
    op.drop_index("Show_venueId_startsAt_idx", table_name="Show")
    for column in ("status", "occupiesUntil", "endsAt", "durationMinutes", "venueId"):
        op.drop_column("Show", column)
    postgresql.ENUM(name="ShowStatus").drop(op.get_bind(), checkfirst=True)
