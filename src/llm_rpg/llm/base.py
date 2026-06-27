from collections.abc import Sequence
from typing import Protocol, TypeVar

from pydantic import BaseModel

from llm_rpg.schemas.enums import LlmPurpose

ModelT = TypeVar("ModelT", bound=BaseModel)


class ChatMessage(BaseModel):
    role: str
    content: str


class Provider(Protocol):
    async def generate_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[ModelT],
        purpose: LlmPurpose,
        *,
        game_id: str | None = None,
        turn_id: str | None = None,
    ) -> ModelT:
        pass


class ProviderError(Exception):
    """Base class for recoverable provider-layer failures."""


class ProviderTimeoutError(ProviderError):
    """Raised when a provider call exceeds the configured hard timeout."""


class ProviderHTTPError(ProviderError):
    def __init__(self, status_code: int, text: str) -> None:
        super().__init__(f"provider returned HTTP {status_code}: {text}")
        self.status_code = status_code
        self.text = text


class StructuredOutputError(ProviderError):
    """Raised when provider output cannot be validated after one repair attempt."""
