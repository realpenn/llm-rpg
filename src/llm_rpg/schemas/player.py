from typing import Any

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, field_validator, model_validator

ScalarValue = bool | str | int | float


class InventoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    quantity: NonNegativeInt = 1
    tags: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("tags")
    @classmethod
    def tags_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("inventory item tags must be unique")
        return value


class PlayerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=120)
    profession: str | None = Field(default=None, max_length=120)
    location: str = Field(min_length=1, max_length=120)
    vitals: dict[str, float] = Field(default_factory=dict)
    currency: dict[str, float] = Field(default_factory=dict)
    conditions: list[str] = Field(default_factory=list)
    flags: dict[str, ScalarValue] = Field(default_factory=dict)
    inventory: dict[str, InventoryItem] = Field(default_factory=dict)
    condition_cap: int = Field(default=16, ge=0)
    flag_cap: int = Field(default=64, ge=0)
    inventory_cap: int = Field(default=64, ge=0)

    @field_validator("conditions")
    @classmethod
    def conditions_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("conditions must be unique")
        return value

    @model_validator(mode="after")
    def enforce_caps_and_keys(self) -> "PlayerState":
        if len(self.conditions) > self.condition_cap:
            raise ValueError("conditions exceed condition_cap")
        if len(self.flags) > self.flag_cap:
            raise ValueError("flags exceed flag_cap")
        if len(self.inventory) > self.inventory_cap:
            raise ValueError("inventory exceeds inventory_cap")
        for key, item in self.inventory.items():
            if key != item.key:
                raise ValueError("inventory dict key must match item key")
        return self


def scalar_value(value: Any) -> ScalarValue:
    if isinstance(value, bool | str | int | float):
        return value
    raise TypeError("value is not a supported scalar")
