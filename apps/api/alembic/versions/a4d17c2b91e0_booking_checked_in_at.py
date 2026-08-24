"""booking checked in at

Revision ID: a4d17c2b91e0
Revises: c6229b026039
Create Date: 2026-08-24 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a4d17c2b91e0"
down_revision: str | Sequence[str] | None = "c6229b026039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    Admission time on the booking.

    Nullable, and it stays nullable: "not yet admitted" is a real state, not a
    missing value, and a default would claim every historical ticket walked
    through the door.

    postgresql.TIMESTAMP(precision=3), matching every other timestamp in this
    schema — core sa.TIMESTAMP has no `precision` argument at all.
    """
    op.add_column(
        "Booking",
        sa.Column("checkedInAt", postgresql.TIMESTAMP(precision=3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("Booking", "checkedInAt")
