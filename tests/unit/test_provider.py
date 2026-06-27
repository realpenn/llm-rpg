import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from llm_rpg.config import Settings
from llm_rpg.llm import (
    ChatMessage,
    FakeProvider,
    LlmCallRecord,
    LlmCallRecorder,
    OpenAICompatibleProvider,
    ProviderTimeoutError,
    StructuredOutputError,
)
from llm_rpg.schemas.enums import LlmOutcome, LlmPurpose, ModeUsed


class SampleOutput(BaseModel):
    answer: str


@dataclass
class MemoryRecorder(LlmCallRecorder):
    records: list[LlmCallRecord] = field(default_factory=list)

    async def record(self, record: LlmCallRecord) -> None:
        self.records.append(record)


@pytest.mark.asyncio
async def test_json_object_validates_response_and_records_ok() -> None:
    recorder = MemoryRecorder()
    provider, requests = _provider(
        [{"answer": "ok"}],
        structured_mode="json_object",
        recorder=recorder,
    )

    parsed = await provider.generate_structured(_messages(), SampleOutput, LlmPurpose.TURN)

    assert parsed.answer == "ok"
    assert requests[0]["response_format"] == {"type": "json_object"}
    schema_hint = requests[0]["messages"][-1]
    assert schema_hint["role"] == "system"
    assert "Schema outline" in schema_hint["content"]
    assert '"answer"' in schema_hint["content"]
    assert recorder.records[0].outcome == LlmOutcome.OK


@pytest.mark.asyncio
async def test_auto_downgrades_when_strict_schema_is_unsupported() -> None:
    recorder = MemoryRecorder()
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, text="unsupported response_format json_schema")
        return _chat_response({"answer": "fallback"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            _settings(structured_mode="auto"),
            client=client,
            recorder=recorder,
        )
        parsed = await provider.generate_structured(_messages(), SampleOutput, LlmPurpose.TURN)

    assert parsed.answer == "fallback"
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert requests[1]["response_format"] == {"type": "json_object"}
    assert "Schema outline" in requests[1]["messages"][-1]["content"]
    assert [record.outcome for record in recorder.records] == [
        LlmOutcome.PROVIDER_ERROR,
        LlmOutcome.OK,
    ]


@pytest.mark.asyncio
async def test_repair_success_after_invalid_json() -> None:
    recorder = MemoryRecorder()
    provider, _ = _provider(["not-json", {"answer": "repaired"}], recorder=recorder)

    parsed = await provider.generate_structured(_messages(), SampleOutput, LlmPurpose.TURN)

    assert parsed.answer == "repaired"
    assert [(record.purpose, record.mode_used, record.outcome) for record in recorder.records] == [
        (LlmPurpose.TURN, ModeUsed.JSON_OBJECT, LlmOutcome.SCHEMA_INVALID),
        (LlmPurpose.REPAIR, ModeUsed.REPAIR, LlmOutcome.OK),
    ]


@pytest.mark.asyncio
async def test_repair_failure_raises() -> None:
    recorder = MemoryRecorder()
    provider, _ = _provider(["not-json", {"wrong": "shape"}], recorder=recorder)

    with pytest.raises(StructuredOutputError):
        await provider.generate_structured(_messages(), SampleOutput, LlmPurpose.TURN)

    assert recorder.records[-1].outcome == LlmOutcome.REPAIR_FAILED


@pytest.mark.asyncio
async def test_timeout_records_provider_timeout() -> None:
    recorder = MemoryRecorder()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        await asyncio.sleep(0.05)
        return _chat_response({"answer": "late"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            _settings(timeout=0.001),
            client=client,
            recorder=recorder,
        )
        with pytest.raises(ProviderTimeoutError):
            await provider.generate_structured(_messages(), SampleOutput, LlmPurpose.TURN)

    assert recorder.records[-1].outcome == LlmOutcome.PROVIDER_TIMEOUT


@pytest.mark.asyncio
async def test_fake_provider_is_deterministic_and_validates_schema() -> None:
    provider = FakeProvider(responses=[{"answer": "scripted"}])

    parsed = await provider.generate_structured(_messages(), SampleOutput, LlmPurpose.WORLD_BUILD)

    assert parsed.answer == "scripted"
    assert len(provider.calls) == 1


def _provider(
    outputs: list[dict[str, Any] | str],
    *,
    structured_mode: str = "json_object",
    recorder: LlmCallRecorder | None = None,
) -> tuple[OpenAICompatibleProvider, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []
    queue = list(outputs)

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        output = queue.pop(0)
        return _chat_response(output)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        _settings(structured_mode=structured_mode),
        client=client,
        recorder=recorder,
    )
    return provider, requests


def _settings(structured_mode: str = "json_object", timeout: float = 1.0) -> Settings:
    return Settings(
        llm_base_url="https://llm.test/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        llm_structured_mode=structured_mode,
        llm_timeout_seconds=timeout,
    )


def _messages() -> list[ChatMessage]:
    return [ChatMessage(role="user", content="hello")]


def _chat_response(output: dict[str, Any] | str) -> httpx.Response:
    content = output if isinstance(output, str) else json.dumps(output)
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5},
        },
    )
