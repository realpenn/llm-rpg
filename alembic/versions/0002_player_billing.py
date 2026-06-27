"""add player billing and recharge codes

Revision ID: 0002_player_billing
Revises: 0001_initial
Create Date: 2026-06-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_player_billing"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("remaining_turns", sa.Integer(), server_default="10", nullable=False),
    )
    op.add_column(
        "players",
        sa.Column(
            "has_unlimited_turns",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_table(
        "recharge_codes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("turn_amount", sa.Integer(), nullable=True),
        sa.Column("unlimited", sa.Boolean(), nullable=False),
        sa.Column("created_by_telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("used_by_player_id", sa.String(length=36), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["used_by_player_id"], ["players.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_recharge_codes_code"),
    )
    op.create_index("ix_recharge_codes_used_at", "recharge_codes", ["used_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_recharge_codes_used_at", table_name="recharge_codes")
    op.drop_table("recharge_codes")
    op.drop_column("players", "has_unlimited_turns")
    op.drop_column("players", "remaining_turns")
