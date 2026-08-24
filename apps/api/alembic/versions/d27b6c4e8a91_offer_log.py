"""offer log

Revision ID: d27b6c4e8a91
Revises: c91a4f28d3b7
Create Date: 2026-08-24 21:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d27b6c4e8a91"
down_revision: str | Sequence[str] | None = "c91a4f28d3b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    The hash-chained offer log.

    No foreign keys, deliberately: this outlives the rows it describes. A log
    that vanished when its show was deleted would be no evidence at all.
    """
    op.create_table(
        "OfferLog",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("showId", sa.Text(), nullable=False),
        sa.Column("categoryId", sa.Text(), nullable=False),
        sa.Column("entryId", sa.Text(), nullable=False),
        sa.Column("showSeatId", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "at",
            postgresql.TIMESTAMP(precision=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("prevHash", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.UniqueConstraint("showId", "seq", name="OfferLog_show_seq_key"),
    )
    op.create_index("OfferLog_showId_seq_idx", "OfferLog", ["showId", "seq"])


def downgrade() -> None:
    op.drop_index("OfferLog_showId_seq_idx", table_name="OfferLog")
    op.drop_table("OfferLog")
