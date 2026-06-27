import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from llm_rpg.config import Settings
from llm_rpg.models import TelegramUpdate
from llm_rpg.schemas.enums import UpdateStatus
from llm_rpg.telegram.sender import telegram_method_url
from llm_rpg.worker.lifecycle import record_telegram_update
from llm_rpg.worker.processor import WorkerProcessor


@dataclass(slots=True)
class PollingResult:
    next_offset: int | None
    received: int
    processed: int


class TelegramPollingClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=40, trust_env=False)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        response = await self._client.post(
            telegram_method_url(self.settings, "deleteWebhook"),
            json={"drop_pending_updates": drop_pending_updates},
        )
        response.raise_for_status()

    async def get_updates(
        self,
        *,
        offset: int | None,
        long_poll_timeout: int = 30,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": long_poll_timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        response = await self._client.post(
            telegram_method_url(self.settings, "getUpdates"),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok", False):
            raise RuntimeError(f"Telegram getUpdates failed: {data}")
        return list(data.get("result") or [])


async def poll_once(
    *,
    session_factory: async_sessionmaker,
    processor: WorkerProcessor,
    client: TelegramPollingClient,
    offset: int | None,
) -> PollingResult:
    updates = await client.get_updates(offset=offset)
    return await process_polled_updates(
        session_factory=session_factory,
        processor=processor,
        updates=updates,
        offset=offset,
    )


async def process_polled_updates(
    *,
    session_factory: async_sessionmaker,
    processor: WorkerProcessor,
    updates: Sequence[dict[str, Any]],
    offset: int | None,
) -> PollingResult:
    processed = 0
    next_offset = offset
    for raw_update in updates:
        update_id = raw_update["update_id"]
        async with session_factory() as session:
            async with session.begin():
                update, _ = await record_telegram_update(session, raw_update)
                should_process = update.status == UpdateStatus.PENDING
        if should_process:
            await processor.process_update_id(update_id)
            processed += 1
        next_offset = max(next_offset or 0, update_id + 1)
    return PollingResult(next_offset=next_offset, received=len(updates), processed=processed)


async def process_recoverable_updates(
    *,
    session_factory: async_sessionmaker,
    processor: WorkerProcessor,
) -> int:
    now = datetime.now(UTC)
    async with session_factory() as session:
        async with session.begin():
            expired = (
                await session.scalars(
                    select(TelegramUpdate)
                    .where(
                        TelegramUpdate.status == UpdateStatus.PROCESSING,
                        TelegramUpdate.lease_expires_at.is_not(None),
                        TelegramUpdate.lease_expires_at < now,
                    )
                    .order_by(TelegramUpdate.update_id)
                )
            ).all()
            for update in expired:
                update.status = UpdateStatus.PENDING
                update.lease_owner = None
                update.lease_token = None
                update.lease_expires_at = None
            pending_ids = (
                await session.scalars(
                    select(TelegramUpdate.update_id)
                    .where(TelegramUpdate.status == UpdateStatus.PENDING)
                    .order_by(TelegramUpdate.update_id)
                )
            ).all()

    processed = 0
    for update_id in pending_ids:
        if await processor.process_update_id(update_id) is not None:
            processed += 1
    return processed


async def run_polling_forever(
    *,
    session_factory: async_sessionmaker,
    processor: WorkerProcessor,
    settings: Settings,
    client: TelegramPollingClient | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_polls: int | None = None,
) -> None:
    logger = logging.getLogger(__name__)
    polling_client = client or TelegramPollingClient(settings)
    should_close_client = client is None
    offset: int | None = None
    successful_polls = 0
    consecutive_failures = 0
    try:
        await polling_client.delete_webhook(drop_pending_updates=False)
        logger.info("telegram_polling_started")
        while max_polls is None or successful_polls < max_polls:
            recovered = await process_recoverable_updates(
                session_factory=session_factory,
                processor=processor,
            )
            if recovered:
                logger.info("telegram_polling_recovered_updates", extra={"count": recovered})
            try:
                updates = await polling_client.get_updates(offset=offset)
            except Exception as exc:
                if not _is_retryable_polling_error(exc):
                    raise
                consecutive_failures += 1
                retry_after = _retry_after_seconds(settings, consecutive_failures)
                logger.warning(
                    "telegram_polling_retryable_error",
                    extra={
                        "error_type": type(exc).__name__,
                        "retry_after_seconds": retry_after,
                        "consecutive_failures": consecutive_failures,
                    },
                    exc_info=True,
                )
                await sleep(retry_after)
                continue

            result = await process_polled_updates(
                session_factory=session_factory,
                processor=processor,
                updates=updates,
                offset=offset,
            )
            successful_polls += 1
            consecutive_failures = 0
            offset = result.next_offset
            if result.received == 0:
                await sleep(0)
    finally:
        if should_close_client:
            await polling_client.close()


def _is_retryable_polling_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return False


def _retry_after_seconds(settings: Settings, consecutive_failures: int) -> float:
    initial = max(0.0, settings.telegram_polling_retry_initial_seconds)
    maximum = max(initial, settings.telegram_polling_retry_max_seconds)
    return min(maximum, initial * (2 ** max(0, consecutive_failures - 1)))
