import asyncio
import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from types import UnionType
from typing import Any, Union, get_args, get_origin

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from llm_rpg.config import Settings, get_settings
from llm_rpg.db import async_session_factory
from llm_rpg.llm.base import (
    ChatMessage,
    ModelT,
    ProviderHTTPError,
    ProviderTimeoutError,
    StructuredOutputError,
)
from llm_rpg.models import LlmCall
from llm_rpg.schemas.enums import LlmOutcome, LlmPurpose, ModeUsed


@dataclass(slots=True)
class LlmCallRecord:
    purpose: LlmPurpose
    mode_used: ModeUsed
    outcome: LlmOutcome
    provider: str
    model: str
    request_messages: list[dict[str, str]]
    request_hash: str
    raw_response_text: str | None
    parsed_payload: dict[str, Any] | None
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int
    error_text: str | None = None
    game_id: str | None = None
    turn_id: str | None = None
    delta_dropped: dict[str, Any] | None = None


class LlmCallRecorder:
    async def record(self, record: LlmCallRecord) -> None:
        raise NotImplementedError


class NullLlmCallRecorder(LlmCallRecorder):
    async def record(self, record: LlmCallRecord) -> None:
        return None


class DatabaseLlmCallRecorder(LlmCallRecorder):
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def record(self, record: LlmCallRecord) -> None:
        async with self._session_factory() as session:
            session.add(
                LlmCall(
                    game_id=record.game_id,
                    turn_id=record.turn_id,
                    purpose=record.purpose,
                    provider=record.provider,
                    model=record.model,
                    mode_used=record.mode_used,
                    request_messages=record.request_messages,
                    request_hash=record.request_hash,
                    raw_response_text=record.raw_response_text,
                    parsed_payload=record.parsed_payload,
                    prompt_tokens=record.prompt_tokens,
                    completion_tokens=record.completion_tokens,
                    latency_ms=record.latency_ms,
                    outcome=record.outcome,
                    delta_dropped=record.delta_dropped,
                    error_text=record.error_text,
                )
            )
            await session.commit()


class OpenAICompatibleProvider:
    provider_name = "openai-compatible"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        recorder: LlmCallRecorder | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client or httpx.AsyncClient(
            timeout=self.settings.llm_timeout_seconds,
            trust_env=False,
        )
        self._owns_client = client is None
        self._recorder = recorder or DatabaseLlmCallRecorder(async_session_factory)
        self._strict_supported: bool | None = None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[ModelT],
        purpose: LlmPurpose,
        *,
        game_id: str | None = None,
        turn_id: str | None = None,
    ) -> ModelT:
        if self.settings.llm_structured_mode == "strict":
            return await self._attempt_with_repair(
                messages,
                schema,
                purpose,
                ModeUsed.STRICT,
                game_id=game_id,
                turn_id=turn_id,
            )
        if self.settings.llm_structured_mode == "json_object":
            return await self._attempt_with_repair(
                messages,
                schema,
                purpose,
                ModeUsed.JSON_OBJECT,
                game_id=game_id,
                turn_id=turn_id,
            )

        if self._strict_supported is not False:
            try:
                parsed = await self._attempt_with_repair(
                    messages,
                    schema,
                    purpose,
                    ModeUsed.STRICT,
                    game_id=game_id,
                    turn_id=turn_id,
                )
                self._strict_supported = True
                return parsed
            except ProviderHTTPError as exc:
                if not _is_strict_feature_error(exc):
                    raise
                self._strict_supported = False

        return await self._attempt_with_repair(
            messages,
            schema,
            purpose,
            ModeUsed.JSON_OBJECT,
            game_id=game_id,
            turn_id=turn_id,
        )

    async def _attempt_with_repair(
        self,
        messages: Sequence[ChatMessage],
        schema: type[ModelT],
        purpose: LlmPurpose,
        mode: ModeUsed,
        *,
        game_id: str | None,
        turn_id: str | None,
    ) -> ModelT:
        try:
            return await self._attempt_once(
                messages,
                schema,
                purpose,
                mode,
                game_id=game_id,
                turn_id=turn_id,
            )
        except StructuredOutputError as exc:
            repair_messages = _repair_messages(messages, exc)
            try:
                return await self._attempt_once(
                    repair_messages,
                    schema,
                    LlmPurpose.REPAIR,
                    ModeUsed.REPAIR,
                    game_id=game_id,
                    turn_id=turn_id,
                )
            except StructuredOutputError as repair_exc:
                raise StructuredOutputError(
                    "provider output failed validation after repair"
                ) from repair_exc

    async def _attempt_once(
        self,
        messages: Sequence[ChatMessage],
        schema: type[ModelT],
        purpose: LlmPurpose,
        mode: ModeUsed,
        *,
        game_id: str | None,
        turn_id: str | None,
    ) -> ModelT:
        request_messages = _request_messages(messages, schema, mode)
        started = time.perf_counter()
        raw_text: str | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        try:
            response_json = await self._post_chat_completions(request_messages, schema, mode)
            latency_ms = _elapsed_ms(started)
            raw_text = _extract_content(response_json)
            usage = response_json.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            parsed_payload = _loads_json_object(raw_text)
            parsed = schema.model_validate(parsed_payload)
        except TimeoutError as exc:
            latency_ms = _elapsed_ms(started)
            await self._record(
                request_messages,
                purpose,
                mode,
                LlmOutcome.PROVIDER_TIMEOUT,
                latency_ms,
                raw_response_text=raw_text,
                error_text=str(exc) or "provider timeout",
                game_id=game_id,
                turn_id=turn_id,
            )
            raise ProviderTimeoutError("provider call exceeded hard timeout") from exc
        except ProviderHTTPError as exc:
            latency_ms = _elapsed_ms(started)
            await self._record(
                request_messages,
                purpose,
                mode,
                LlmOutcome.PROVIDER_ERROR,
                latency_ms,
                raw_response_text=exc.text,
                error_text=str(exc),
                game_id=game_id,
                turn_id=turn_id,
            )
            raise
        except (json.JSONDecodeError, ValidationError, ValueError, KeyError) as exc:
            latency_ms = _elapsed_ms(started)
            await self._record(
                request_messages,
                purpose,
                mode,
                LlmOutcome.SCHEMA_INVALID if mode != ModeUsed.REPAIR else LlmOutcome.REPAIR_FAILED,
                latency_ms,
                raw_response_text=raw_text,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error_text=str(exc),
                game_id=game_id,
                turn_id=turn_id,
            )
            raise StructuredOutputError(str(exc)) from exc

        await self._record(
            request_messages,
            purpose,
            mode,
            LlmOutcome.OK,
            latency_ms,
            raw_response_text=raw_text,
            parsed_payload=parsed.model_dump(mode="json"),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            game_id=game_id,
            turn_id=turn_id,
        )
        return parsed

    async def _post_chat_completions(
        self,
        request_messages: list[dict[str, str]],
        schema: type[BaseModel],
        mode: ModeUsed,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": request_messages,
            "response_format": _response_format(schema, mode),
        }
        if self.settings.llm_disable_thinking:
            body["thinking"] = {"type": "disabled"}
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        try:
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                response = await self._client.post(
                    _chat_completions_url(self.settings), json=body, headers=headers
                )
        except TimeoutError:
            raise
        if response.status_code >= 400:
            raise ProviderHTTPError(response.status_code, response.text)
        return response.json()

    async def _record(
        self,
        request_messages: list[dict[str, str]],
        purpose: LlmPurpose,
        mode: ModeUsed,
        outcome: LlmOutcome,
        latency_ms: int,
        *,
        raw_response_text: str | None = None,
        parsed_payload: dict[str, Any] | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        error_text: str | None = None,
        game_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        await self._recorder.record(
            LlmCallRecord(
                purpose=purpose,
                mode_used=mode,
                outcome=outcome,
                provider=self.provider_name,
                model=self.settings.llm_model,
                request_messages=request_messages,
                request_hash=_hash_messages(request_messages),
                raw_response_text=raw_response_text,
                parsed_payload=parsed_payload,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                error_text=error_text,
                game_id=game_id,
                turn_id=turn_id,
            )
        )


def create_provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider()


def _response_format(schema: type[BaseModel], mode: ModeUsed) -> dict[str, Any]:
    if mode == ModeUsed.STRICT:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
                "strict": True,
            },
        }
    return {"type": "json_object"}


def _request_messages(
    messages: Sequence[ChatMessage],
    schema: type[BaseModel],
    mode: ModeUsed,
) -> list[dict[str, str]]:
    request_messages = list(messages)
    if mode != ModeUsed.STRICT:
        request_messages.append(_schema_hint_message(schema))
    return [message.model_dump() for message in request_messages]


def _schema_hint_message(schema: type[BaseModel]) -> ChatMessage:
    schema_outline = json.dumps(
        _schema_outline(schema),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return ChatMessage(
        role="system",
        content=(
            "目标输出必须是一个 JSON object，且必须符合下面的字段结构。"
            "结构中的字符串值是类型说明，最终输出必须替换成真实 JSON 值。"
            "不要输出结构之外的字段，不要重命名字段，不要添加解释或 Markdown。"
            f"\nSchema outline:\n{schema_outline}"
        ),
    )


def _schema_outline(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        field_name: _field_outline(schema, field_name, field_info.annotation)
        for field_name, field_info in schema.model_fields.items()
    }


def _field_outline(schema: type[BaseModel], field_name: str, annotation: Any) -> Any:
    if schema.__name__ == "FlagSpec" and field_name == "type":
        return "boolean|string|number"
    if schema.__name__ == "StateDeltaEntry" and field_name == "path":
        return (
            "ASCII dotted path: vitals.<key>|currency.<key>|conditions|"
            "inventory.<key>|inventory.<key>.quantity|flags.<declared_key>"
        )
    return _type_outline(annotation)


def _type_outline(annotation: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {UnionType, Union}:
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            return _type_outline(non_none_args[0])
        return "|".join(_type_label(arg) for arg in non_none_args)
    if origin is list:
        item_type = args[0] if args else Any
        return [_type_outline(item_type)]
    if origin is dict:
        value_type = args[1] if len(args) > 1 else Any
        return {"<key>": _type_outline(value_type)}
    if _is_base_model_type(annotation):
        return _schema_outline(annotation)
    if _is_enum_type(annotation):
        return "|".join(str(item.value) for item in annotation)
    return _type_label(annotation)


def _type_label(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is not None:
        return "object"
    if annotation is str:
        return "string"
    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is Any:
        return "any"
    if annotation is type(None):
        return "null"
    if _is_base_model_type(annotation):
        return "object"
    if _is_enum_type(annotation):
        return "|".join(str(item.value) for item in annotation)
    return "string"


def _is_base_model_type(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _is_enum_type(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, Enum)


def _chat_completions_url(settings: Settings) -> str:
    base_url = settings.llm_base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def _extract_content(response_json: dict[str, Any]) -> str:
    return response_json["choices"][0]["message"]["content"]


def _loads_json_object(raw_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        # Models sometimes use typographic quotes (U+201C/U+201D) as JSON string delimiters
        # instead of ASCII quotes, leaving strings unclosed. Only sanitize on failure so that
        # legitimate inner dialogue quotes like "吓我一跳" are preserved when the JSON is valid.
        sanitized = raw_text.replace("\u201c", '"').replace("\u201d", '"')
        payload = json.loads(sanitized)
    if not isinstance(payload, dict):
        raise ValueError("provider response must be a JSON object")
    return payload


def _repair_messages(
    messages: Sequence[ChatMessage], exc: StructuredOutputError
) -> list[ChatMessage]:
    return [
        *messages,
        ChatMessage(
            role="system",
            content=(
                "上一次输出未通过 JSON/Pydantic 校验。"
                "请只返回一个符合目标 schema 的 JSON object，不要包含解释。"
            ),
        ),
        ChatMessage(role="user", content=f"校验错误: {exc}"),
    ]


def _is_strict_feature_error(exc: ProviderHTTPError) -> bool:
    text = exc.text.lower()
    return exc.status_code in {400, 404, 422} and (
        "json_schema" in text or "response_format" in text or "unsupported" in text
    )


def _hash_messages(messages: list[dict[str, str]]) -> str:
    encoded = json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
