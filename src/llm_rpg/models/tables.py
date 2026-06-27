from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm_rpg.db import Base, JsonDict, JsonList, json_document


def uuid_str() -> str:
    return str(uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Player(TimestampMixin, Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    username: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))

    games: Mapped[list["Game"]] = relationship(back_populates="player")


class Game(TimestampMixin, Base):
    __tablename__ = "games"
    __table_args__ = (
        Index(
            "ux_games_one_active_per_player",
            "player_id",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
            sqlite_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    player_id: Mapped[str] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    world_bible: Mapped[JsonDict] = mapped_column(json_document, nullable=False)
    player_state: Mapped[JsonDict] = mapped_column(json_document, nullable=False)
    rolling_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    turn_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    time_of_day: Mapped[str | None] = mapped_column(String(120))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    player: Mapped[Player] = relationship(back_populates="games")
    factions: Mapped[list["Faction"]] = relationship(
        back_populates="game", cascade="all, delete-orphan"
    )
    npcs: Mapped[list["Npc"]] = relationship(back_populates="game", cascade="all, delete-orphan")
    turns: Mapped[list["Turn"]] = relationship(back_populates="game", cascade="all, delete-orphan")


class Faction(TimestampMixin, Base):
    __tablename__ = "factions"
    __table_args__ = (UniqueConstraint("game_id", "key", name="uq_factions_game_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    ideology: Mapped[str] = mapped_column(Text, default="", nullable=False)
    memory_log: Mapped[JsonList] = mapped_column(json_document, default=list, nullable=False)

    game: Mapped[Game] = relationship(back_populates="factions")


class Npc(TimestampMixin, Base):
    __tablename__ = "npcs"
    __table_args__ = (UniqueConstraint("game_id", "key", name="uq_npcs_game_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(120), nullable=False)
    faction: Mapped[str | None] = mapped_column(String(80))
    location: Mapped[str] = mapped_column(String(120), nullable=False)
    personality: Mapped[str] = mapped_column(Text, nullable=False)
    desire: Mapped[str] = mapped_column(Text, nullable=False)
    fear: Mapped[str] = mapped_column(Text, nullable=False)
    secret: Mapped[str] = mapped_column(Text, default="", nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(120), default="active", nullable=False)
    revealed_to_player: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    memory_log: Mapped[JsonList] = mapped_column(json_document, default=list, nullable=False)

    game: Mapped[Game] = relationship(back_populates="npcs")


class Relationship(TimestampMixin, Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint(
            "game_id",
            "source_key",
            "target_key",
            "edge_type",
            name="uq_relationships_edge",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    source_key: Mapped[str] = mapped_column(String(80), nullable=False)
    target_key: Mapped[str] = mapped_column(String(80), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(40), nullable=False)
    standing: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trust: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    memory_log: Mapped[JsonList] = mapped_column(json_document, default=list, nullable=False)


class Turn(TimestampMixin, Base):
    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint("game_id", "sequence", name="uq_turns_game_sequence"),
        Index("ix_turns_game_sequence", "game_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    player_input: Mapped[str | None] = mapped_column(Text)
    narration: Mapped[str] = mapped_column(Text, nullable=False)
    delta_audit: Mapped[JsonDict] = mapped_column(json_document, default=dict, nullable=False)
    safety_flags: Mapped[JsonList] = mapped_column(json_document, default=list, nullable=False)
    game_clock: Mapped[JsonDict] = mapped_column(json_document, default=dict, nullable=False)

    game: Mapped[Game] = relationship(back_populates="turns")


class Event(TimestampMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_game_location_created", "game_id", "location", "created_at"),
        Index("ix_events_game_created", "game_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(String(120))
    involved_entities: Mapped[JsonList] = mapped_column(json_document, default=list, nullable=False)


class SuggestedAction(TimestampMixin, Base):
    __tablename__ = "suggested_actions"
    __table_args__ = (
        Index("ix_suggested_actions_game_turn", "game_id", "turn_id"),
        UniqueConstraint("callback_id", name="uq_suggested_actions_callback_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id", ondelete="CASCADE"), nullable=False)
    callback_id: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    game_id: Mapped[str | None] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    mode_used: Mapped[str] = mapped_column(String(40), nullable=False)
    request_messages: Mapped[JsonList | None] = mapped_column(json_document)
    request_hash: Mapped[str | None] = mapped_column(String(128))
    raw_response_text: Mapped[str | None] = mapped_column(Text)
    parsed_payload: Mapped[JsonDict | None] = mapped_column(json_document)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    delta_dropped: Mapped[JsonDict | None] = mapped_column(json_document)
    error_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TelegramUpdate(TimestampMixin, Base):
    __tablename__ = "telegram_updates"
    __table_args__ = (
        Index("ix_telegram_updates_status", "status"),
        Index("ix_telegram_updates_lease_expires_at", "lease_expires_at"),
        Index("ix_telegram_updates_next_retry_at", "next_retry_at"),
        Index("ix_telegram_updates_user_status_update", "telegram_user_id", "status", "update_id"),
    )

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    update_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    turn_producing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    drop_reason: Mapped[str | None] = mapped_column(String(40))
    blocked_by_update_id: Mapped[int | None] = mapped_column(BigInteger)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_token: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_update: Mapped[JsonDict] = mapped_column(json_document, nullable=False)
    game_id: Mapped[str | None] = mapped_column(ForeignKey("games.id", ondelete="SET NULL"))
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_text: Mapped[str | None] = mapped_column(Text)
    safety_flags: Mapped[JsonList] = mapped_column(json_document, default=list, nullable=False)
    reply_payload: Mapped[JsonList | None] = mapped_column(json_document)
    telegram_message_ids: Mapped[JsonList | None] = mapped_column(json_document)
