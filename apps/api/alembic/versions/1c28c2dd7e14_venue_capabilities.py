"""venue capabilities

Revision ID: 1c28c2dd7e14
Revises: 9bfb11a52e4a
Create Date: 2026-08-23 19:35:39.391037

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c28c2dd7e14"
down_revision: str | Sequence[str] | None = "9bfb11a52e4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    Venues become admin-owned infrastructure with capabilities, rather than a
    name and an address.

    Existing venues keep working: END_STAGE allowing both event types is exactly
    what they implicitly were, so the server defaults backfill them for free.
    """
    stage_layout = postgresql.ENUM("END_STAGE", "CENTRE_STAGE", name="StageLayout")
    # checkfirst so re-running against a partially migrated database is safe.
    stage_layout.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "Venue",
        sa.Column(
            "stageLayout",
            postgresql.ENUM("END_STAGE", "CENTRE_STAGE", name="StageLayout", create_type=False),
            nullable=False,
            server_default="END_STAGE",
        ),
    )
    op.add_column(
        "Venue",
        sa.Column(
            "allowedEventTypes",
            postgresql.ARRAY(
                postgresql.ENUM("MOVIE", "CONCERT", name="EventType", create_type=False)
            ),
            nullable=False,
            server_default=sa.text("ARRAY['MOVIE','CONCERT']::\"EventType\"[]"),
        ),
    )
    op.add_column(
        "Venue",
        sa.Column("turnaroundMinutes", sa.Integer(), nullable=False, server_default="15"),
    )


def downgrade() -> None:
    op.drop_column("Venue", "turnaroundMinutes")
    op.drop_column("Venue", "allowedEventTypes")
    op.drop_column("Venue", "stageLayout")
    postgresql.ENUM(name="StageLayout").drop(op.get_bind(), checkfirst=True)
