"""show no venue overlap

Revision ID: c6229b026039
Revises: f0b3a0dcced3
Create Date: 2026-08-23 20:42:23.864133

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6229b026039"
down_revision: str | Sequence[str] | None = "f0b3a0dcced3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    The guarantee that survives an application bug: no two SCHEDULED shows may
    occupy one venue at overlapping times.

    tsrange, NOT tstzrange — the columns are TIMESTAMP(3) WITHOUT TIME ZONE, and
    the range type has to match the column type or the constraint will not build.

    WHERE status = 'SCHEDULED' is the elegant part: a cancelled show stops
    blocking its slot automatically, with no cleanup code anywhere. Same house
    style as BookingSeat_showSeatId_live_key — guard the live rows, let the dead
    ones stay for history.
    """
    # Equality on a text column inside a GiST exclusion constraint needs this.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        ALTER TABLE "Show" ADD CONSTRAINT "show_no_venue_overlap"
          EXCLUDE USING gist (
            "venueId"                            WITH =,
            tsrange("startsAt", "occupiesUntil") WITH &&
          ) WHERE (status = 'SCHEDULED')
        """
    )


def downgrade() -> None:
    op.execute('ALTER TABLE "Show" DROP CONSTRAINT IF EXISTS "show_no_venue_overlap"')
    # btree_gist is left installed: dropping an extension another migration or
    # another application might rely on is not this migration's business.
