from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from llm_rpg.db import Base


class FakeTelegramServer:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._next_message_id = 1

    async def send_reply_payload(self, reply_payload: list[dict[str, Any]]) -> list[int]:
        message_ids: list[int] = []
        for payload in reply_payload:
            message_id = self._next_message_id
            self._next_message_id += 1
            self.messages.append({**payload, "message_id": message_id})
            message_ids.append(message_id)
        return message_ids


class InMemoryQueue:
    def __init__(self) -> None:
        self.update_ids: list[int] = []

    async def enqueue_update(self, update_id: int) -> None:
        if update_id not in self.update_ids:
            self.update_ids.append(update_id)


@pytest.fixture
async def session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def fake_telegram() -> FakeTelegramServer:
    return FakeTelegramServer()


@pytest.fixture
def in_memory_queue() -> InMemoryQueue:
    return InMemoryQueue()
