import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from llm_rpg.config import Settings
from llm_rpg.db import Base
from llm_rpg.llm import FakeProvider
from llm_rpg.models import TelegramUpdate
from llm_rpg.schemas.enums import UpdateStatus
from llm_rpg.worker.lifecycle import record_telegram_update
from llm_rpg.worker.polling import (
    TelegramPollingClient,
    poll_once,
    process_recoverable_updates,
    run_polling_forever,
)
from llm_rpg.worker.processor import WorkerProcessor


class Sender:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_reply_payload(self, reply_payload: list[dict[str, Any]]) -> list[int]:
        self.messages.extend(reply_payload)
        return [1]


@pytest.mark.asyncio
async def test_poll_once_records_and_processes_update() -> None:
    session_factory = await _session_factory()
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"ok": True, "result": [_message_update(501, "/help")]})

    sender = Sender()
    settings = Settings(
        telegram_bot_token="token",
        telegram_mode="polling",
        telegram_api_base_url="https://telegram.test",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        polling_client = TelegramPollingClient(settings, client=http_client)
        processor = WorkerProcessor(
            session_factory=session_factory,
            provider=FakeProvider(),
            sender=sender,
            settings=settings,
        )
        result = await poll_once(
            session_factory=session_factory,
            processor=processor,
            client=polling_client,
            offset=None,
        )

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 501)

    assert requests[0]["timeout"] == 30
    assert result.next_offset == 502
    assert result.received == 1
    assert result.processed == 1
    assert update is not None
    assert update.status == UpdateStatus.COMPLETED
    assert "可用命令" in sender.messages[0]["text"]


@pytest.mark.asyncio
async def test_polling_retries_after_transient_get_updates_disconnect() -> None:
    session_factory = await _session_factory()
    sender = Sender()
    settings = Settings(
        telegram_bot_token="token",
        telegram_mode="polling",
        telegram_api_base_url="https://telegram.test",
        telegram_polling_retry_initial_seconds=0.25,
    )
    processor = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(),
        sender=sender,
        settings=settings,
    )
    client = FlakyPollingClient()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    await run_polling_forever(
        session_factory=session_factory,
        processor=processor,
        settings=settings,
        client=client,
        sleep=fake_sleep,
        max_polls=1,
    )

    assert client.deleted_webhook is True
    assert client.get_updates_calls == 2
    assert sleeps == [0.25, 0]


@pytest.mark.asyncio
async def test_process_recoverable_updates_reclaims_expired_processing() -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            update, _ = await record_telegram_update(session, _message_update(601, "/help"))
            update.status = UpdateStatus.PROCESSING
            update.lease_owner = "old"
            update.lease_token = "old-token"
            update.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    sender = Sender()
    settings = Settings()
    processor = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(),
        sender=sender,
        settings=settings,
    )

    processed = await process_recoverable_updates(
        session_factory=session_factory,
        processor=processor,
    )

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 601)

    assert processed == 1
    assert update is not None
    assert update.status == UpdateStatus.COMPLETED
    assert "可用命令" in sender.messages[0]["text"]


class FlakyPollingClient:
    def __init__(self) -> None:
        self.deleted_webhook = False
        self.get_updates_calls = 0

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        del drop_pending_updates
        self.deleted_webhook = True

    async def get_updates(self, *, offset: int | None) -> list[dict[str, Any]]:
        del offset
        self.get_updates_calls += 1
        if self.get_updates_calls == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return []


async def _session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _message_update(update_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 10,
            "from": {"id": 42, "is_bot": False, "first_name": "玩家"},
            "chat": {"id": 420, "type": "private"},
            "text": text,
        },
    }
