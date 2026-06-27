from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from llm_rpg.llm.base import ChatMessage, ModelT
from llm_rpg.schemas.enums import LlmPurpose

FakeResponse = (
    BaseModel | dict[str, Any] | Exception | Callable[[type[BaseModel]], BaseModel | dict[str, Any]]
)


@dataclass
class FakeProvider:
    responses: list[FakeResponse] = field(default_factory=list)
    calls: list[tuple[list[ChatMessage], type[BaseModel], LlmPurpose]] = field(default_factory=list)

    async def generate_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[ModelT],
        purpose: LlmPurpose,
        *,
        game_id: str | None = None,
        turn_id: str | None = None,
    ) -> ModelT:
        del game_id, turn_id
        self.calls.append((list(messages), schema, purpose))
        if not self.responses:
            raise AssertionError("FakeProvider has no queued response")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(schema)
        if isinstance(response, schema):
            return response
        if isinstance(response, BaseModel):
            return schema.model_validate(response.model_dump())
        return schema.model_validate(response)
