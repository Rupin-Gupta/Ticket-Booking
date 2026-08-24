"""accessible seating

Revision ID: c91a4f28d3b7
Revises: b83f5e1d70aa
Create Date: 2026-08-24 20:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c91a4f28d3b7"
down_revision: str | Sequence[str] | None = "b83f5e1d70aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCESS_TYPE = postgresql.ENUM(
    "STANDARD",
    "WHEELCHAIR_SPACE",
    "COMPANION",
    "STEP_FREE",
    name="SeatAccessType",
    create_type=False,
)


def upgrade() -> None:
    """
    What kind of space each seat is, and which companion belongs to which
    wheelchair space.

    companionOfId is a self-referencing foreign key on Seat: the companion
    points at its space. Nullable, because most seats are neither.
    """
    ACCESS_TYPE.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "Seat",
        sa.Column("accessType", ACCESS_TYPE, nullable=False, server_default="STANDARD"),
    )
    op.add_column("Seat", sa.Column("companionOfId", sa.Text(), nullable=True))
    op.create_foreign_key(
        "Seat_companionOfId_fkey", "Seat", "Seat", ["companionOfId"], ["id"]
    )
    op.create_index("Seat_companionOfId_idx", "Seat", ["companionOfId"])


def downgrade() -> None:
    op.drop_index("Seat_companionOfId_idx", table_name="Seat")
    op.drop_constraint("Seat_companionOfId_fkey", "Seat", type_="foreignkey")
    op.drop_column("Seat", "companionOfId")
    op.drop_column("Seat", "accessType")
    ACCESS_TYPE.drop(op.get_bind(), checkfirst=True)
