"""initial durable tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def json_document():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "players",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id"),
    )
    op.create_table(
        "games",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("player_id", sa.String(length=36), nullable=False),
        sa.Column("world_bible", json_document(), nullable=False),
        sa.Column("player_state", json_document(), nullable=False),
        sa.Column("rolling_summary", sa.Text(), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("time_of_day", sa.String(length=120), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_games_one_active_per_player",
        "games",
        ["player_id"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
        sqlite_where=sa.text("archived_at IS NULL"),
    )
    op.create_table(
        "factions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("game_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("ideology", sa.Text(), nullable=False),
        sa.Column("memory_log", json_document(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "key", name="uq_factions_game_key"),
    )
    op.create_table(
        "npcs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("game_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=120), nullable=False),
        sa.Column("faction", sa.String(length=80), nullable=True),
        sa.Column("location", sa.String(length=120), nullable=False),
        sa.Column("personality", sa.Text(), nullable=False),
        sa.Column("desire", sa.Text(), nullable=False),
        sa.Column("fear", sa.Text(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=120), nullable=False),
        sa.Column("revealed_to_player", sa.Boolean(), nullable=False),
        sa.Column("memory_log", json_document(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "key", name="uq_npcs_game_key"),
    )
    op.create_table(
        "relationships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("game_id", sa.String(length=36), nullable=False),
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column("target_key", sa.String(length=80), nullable=False),
        sa.Column("edge_type", sa.String(length=40), nullable=False),
        sa.Column("standing", sa.Integer(), nullable=False),
        sa.Column("trust", sa.Integer(), nullable=False),
        sa.Column("memory_log", json_document(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id", "source_key", "target_key", "edge_type", name="uq_relationships_edge"
        ),
    )
    op.create_table(
        "turns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("game_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("player_input", sa.Text(), nullable=True),
        sa.Column("narration", sa.Text(), nullable=False),
        sa.Column("delta_audit", json_document(), nullable=False),
        sa.Column("safety_flags", json_document(), nullable=False),
        sa.Column("game_clock", json_document(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "sequence", name="uq_turns_game_sequence"),
    )
    op.create_index("ix_turns_game_sequence", "turns", ["game_id", "sequence"], unique=False)
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("game_id", sa.String(length=36), nullable=False),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("location", sa.String(length=120), nullable=True),
        sa.Column("involved_entities", json_document(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_events_game_location_created",
        "events",
        ["game_id", "location", "created_at"],
        unique=False,
    )
    op.create_index("ix_events_game_created", "events", ["game_id", "created_at"], unique=False)
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "ix_events_involved_entities_gin",
            "events",
            ["involved_entities"],
            unique=False,
            postgresql_using="gin",
        )
    op.create_table(
        "suggested_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("game_id", sa.String(length=36), nullable=False),
        sa.Column("turn_id", sa.String(length=36), nullable=False),
        sa.Column("callback_id", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("callback_id", name="uq_suggested_actions_callback_id"),
    )
    op.create_index(
        "ix_suggested_actions_game_turn",
        "suggested_actions",
        ["game_id", "turn_id"],
        unique=False,
    )
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("game_id", sa.String(length=36), nullable=True),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("mode_used", sa.String(length=40), nullable=False),
        sa.Column("request_messages", json_document(), nullable=True),
        sa.Column("request_hash", sa.String(length=128), nullable=True),
        sa.Column("raw_response_text", sa.Text(), nullable=True),
        sa.Column("parsed_payload", json_document(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("delta_dropped", json_document(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "telegram_updates",
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("update_kind", sa.String(length=80), nullable=False),
        sa.Column("turn_producing", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("drop_reason", sa.String(length=40), nullable=True),
        sa.Column("blocked_by_update_id", sa.BigInteger(), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_token", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_update", json_document(), nullable=False),
        sa.Column("game_id", sa.String(length=36), nullable=True),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("safety_flags", json_document(), nullable=False),
        sa.Column("reply_payload", json_document(), nullable=True),
        sa.Column("telegram_message_ids", json_document(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("update_id"),
    )
    op.create_index("ix_telegram_updates_status", "telegram_updates", ["status"], unique=False)
    op.create_index(
        "ix_telegram_updates_lease_expires_at",
        "telegram_updates",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_updates_next_retry_at",
        "telegram_updates",
        ["next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_updates_user_status_update",
        "telegram_updates",
        ["telegram_user_id", "status", "update_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_updates_user_status_update", table_name="telegram_updates")
    op.drop_index("ix_telegram_updates_next_retry_at", table_name="telegram_updates")
    op.drop_index("ix_telegram_updates_lease_expires_at", table_name="telegram_updates")
    op.drop_index("ix_telegram_updates_status", table_name="telegram_updates")
    op.drop_table("telegram_updates")
    op.drop_table("llm_calls")
    op.drop_index("ix_suggested_actions_game_turn", table_name="suggested_actions")
    op.drop_table("suggested_actions")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("ix_events_involved_entities_gin", table_name="events")
    op.drop_index("ix_events_game_created", table_name="events")
    op.drop_index("ix_events_game_location_created", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_turns_game_sequence", table_name="turns")
    op.drop_table("turns")
    op.drop_table("relationships")
    op.drop_table("npcs")
    op.drop_table("factions")
    op.drop_index("ux_games_one_active_per_player", table_name="games")
    op.drop_table("games")
    op.drop_table("players")
