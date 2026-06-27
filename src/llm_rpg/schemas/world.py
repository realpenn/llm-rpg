from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from llm_rpg.schemas.player import PlayerState


class NumericBound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: float
    max: float
    default: float | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> "NumericBound":
        if self.min > self.max:
            raise ValueError("min must be less than or equal to max")
        if self.default is not None and not self.min <= self.default <= self.max:
            raise ValueError("default must be within min and max")
        return self


class FlagSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern=r"^(boolean|string|number)$")
    description: str = Field(default="", max_length=500)
    default: bool | str | float | None = None

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        aliases = {
            "bool": "boolean",
            "str": "string",
            "integer": "number",
            "int": "number",
            "float": "number",
            "numeric": "number",
        }
        return aliases.get(normalized, normalized)


class PlayerStatSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vitals: dict[str, NumericBound] = Field(min_length=1)
    currency: dict[str, NumericBound] = Field(default_factory=dict)
    allowed_conditions: list[str] = Field(default_factory=list)
    allowed_flags: dict[str, FlagSpec] = Field(default_factory=dict)
    condition_cap: int = Field(default=16, ge=0)
    flag_cap: int = Field(default=64, ge=0)
    inventory_cap: int = Field(default=64, ge=0)

    @field_validator("allowed_conditions")
    @classmethod
    def conditions_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("allowed_conditions must be unique")
        return value


class FactionSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    ideology: str = Field(default="", max_length=1000)


class LocationSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)


class NpcSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=120)
    title: str = Field(default="", max_length=120)
    role: str = Field(min_length=1, max_length=120)
    faction: str | None = Field(default=None, max_length=80)
    location: str = Field(min_length=1, max_length=120)
    personality: str = Field(min_length=1, max_length=1000)
    desire: str = Field(min_length=1, max_length=1000)
    fear: str = Field(min_length=1, max_length=1000)
    secret: str = Field(default="", max_length=1000)
    goal: str = Field(min_length=1, max_length=1000)
    status: str = Field(default="active", max_length=120)
    revealed_to_player: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_revealed_alias(cls, value: object) -> object:
        if isinstance(value, dict) and "reveled_to_player" in value:
            normalized = dict(value)
            normalized["revealed_to_player"] = normalized.pop("reveled_to_player")
            return normalized
        return value


class WorldBible(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1, max_length=4000)
    language: str = Field(default="zh-CN", max_length=40)
    genre: str = Field(min_length=1, max_length=120)
    tone: str = Field(min_length=1, max_length=120)
    era_geography: str = Field(min_length=1, max_length=500)
    locked_laws: list[str] = Field(min_length=1, max_length=32)
    factions: list[FactionSeed] = Field(default_factory=list, max_length=32)
    locations: list[LocationSeed] = Field(default_factory=list, max_length=64)
    player_stat_schema: PlayerStatSchema
    initial_location: str = Field(min_length=1, max_length=120)
    initial_npcs: list[NpcSeed] = Field(default_factory=list, max_length=32)
    dangers: list[str] = Field(default_factory=list, max_length=32)
    available_roles: list[str] = Field(default_factory=list, max_length=32)
    narrative_style: str = Field(min_length=1, max_length=1000)
    taboos: list[str] = Field(default_factory=list, max_length=32)
    core_conflict: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "WorldBible":
        _ensure_unique("factions", [item.key for item in self.factions])
        _ensure_unique("locations", [item.key for item in self.locations])
        _ensure_unique("initial_npcs", [item.key for item in self.initial_npcs])
        known_locations = {item.key for item in self.locations}
        if known_locations and self.initial_location not in known_locations:
            raise ValueError("initial_location must reference a known location")
        return self


class WorldBuildOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    world: WorldBible
    opening_narration: str = Field(min_length=1, max_length=6000)
    player_state: PlayerState
    initial_suggested_actions: list[str] = Field(min_length=3, max_length=5)


def _ensure_unique(field_name: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} keys must be unique")
