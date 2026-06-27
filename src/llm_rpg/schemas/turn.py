from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from llm_rpg.schemas.enums import DeltaOp, EdgeType, MemoryScope, TimeAdvance
from llm_rpg.schemas.player import InventoryItem

DeltaValue = bool | str | int | float | InventoryItem | None


class StateDeltaEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=160)
    op: DeltaOp
    value: Any = None


class NpcUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    faction: str | None = Field(default=None, max_length=80)
    location: str | None = Field(default=None, max_length=120)
    personality: str | None = Field(default=None, max_length=1000)
    desire: str | None = Field(default=None, max_length=1000)
    fear: str | None = Field(default=None, max_length=1000)
    secret: str | None = Field(default=None, max_length=1000)
    goal: str | None = Field(default=None, max_length=1000)
    status: str | None = Field(default=None, max_length=120)
    memory: str | None = Field(default=None, max_length=1000)
    revealed_to_player: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_revealed_alias(cls, value: object) -> object:
        if isinstance(value, dict) and "reveled_to_player" in value:
            normalized = dict(value)
            normalized["revealed_to_player"] = normalized.pop("reveled_to_player")
            return normalized
        return value

    @field_validator("memory", mode="before")
    @classmethod
    def normalize_memory(cls, value: object) -> object:
        return _coerce_optional_text(value, max_length=1000)


class RelationshipUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1, max_length=80)
    target_key: str = Field(min_length=1, max_length=80)
    edge_type: EdgeType
    standing_delta: int | None = Field(default=None, ge=-100, le=100)
    trust_delta: int | None = Field(default=None, ge=-100, le=100)
    note: str = Field(default="", max_length=1000)


class EventEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1000)
    location: str | None = Field(default=None, max_length=120)
    involved_entities: list[str] = Field(default_factory=list, max_length=16)


class SuggestedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=500)


class MemoryUpdateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=1000)

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: str) -> str:
        if value == MemoryScope.WORLD:
            return value
        if value.startswith(f"{MemoryScope.NPC}:") and value.removeprefix("npc:"):
            return value
        if value.startswith(f"{MemoryScope.FACTION}:") and value.removeprefix("faction:"):
            return value
        if value not in {MemoryScope.NPC, MemoryScope.FACTION}:
            return MemoryScope.WORLD
        raise ValueError("scope must be world, npc:<key>, or faction:<key>")

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: object) -> object:
        return _coerce_text(value, max_length=1000)


class TurnOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narration: str = Field(min_length=1, max_length=6000)
    state_delta: list[StateDeltaEntry] = Field(default_factory=list, max_length=32)
    npc_updates: list[NpcUpdate] = Field(default_factory=list, max_length=32)
    relationship_updates: list[RelationshipUpdate] = Field(default_factory=list, max_length=32)
    events: list[EventEntry] = Field(default_factory=list, max_length=32)
    suggested_actions: list[SuggestedAction] = Field(min_length=3, max_length=5)
    memory_update: list[MemoryUpdateEntry] = Field(default_factory=list, max_length=16)
    time_advance: TimeAdvance | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_list_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for field_name in (
            "state_delta",
            "npc_updates",
            "relationship_updates",
            "events",
            "suggested_actions",
            "memory_update",
        ):
            item = normalized.get(field_name)
            if isinstance(item, dict):
                normalized[field_name] = [item]
        return normalized

    @field_validator("time_advance", mode="before")
    @classmethod
    def normalize_time_advance(cls, value: object) -> object:
        if value is None or isinstance(value, TimeAdvance):
            return value
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        if not normalized:
            return None
        if normalized in {"minute", "minutes", "几分钟", "数分钟", "分钟", "片刻", "短时间"}:
            return TimeAdvance.MINUTES
        if normalized in {"hour", "hours", "几小时", "数小时", "小时", "一小时"}:
            return TimeAdvance.HOURS
        if normalized in {"overnight", "night", "过夜", "一夜", "整夜", "隔夜"}:
            return TimeAdvance.OVERNIGHT
        return None


def _coerce_optional_text(value: object, *, max_length: int) -> str | None | object:
    if value is None:
        return None
    return _coerce_text(value, max_length=max_length)


def _coerce_text(value: object, *, max_length: int) -> str | object:
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        text = "\n".join(str(item) for item in value if item is not None)
    elif isinstance(value, dict):
        text = "; ".join(f"{key}: {item}" for key, item in value.items() if item is not None)
    else:
        return value
    return text[:max_length]
