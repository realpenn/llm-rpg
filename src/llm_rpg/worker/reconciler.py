from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from llm_rpg.config import Settings, get_settings
from llm_rpg.models import TelegramUpdate
from llm_rpg.redis import UpdateQueue
from llm_rpg.schemas.enums import UpdateStatus
from llm_rpg.telegram.sender import ReplySender


@dataclass(slots=True)
class ReconcileResult:
    pending_reenqueued: int = 0
    processing_reclaimed: int = 0
    terminal_resent: int = 0


async def reconcile_once(
    *,
    session_factory: async_sessionmaker,
    queue: UpdateQueue,
    sender: ReplySender,
    settings: Settings | None = None,
) -> ReconcileResult:
    settings = settings or get_settings()
    del settings
    now = datetime.now(UTC)
    result = ReconcileResult()

    async with session_factory() as session:
        async with session.begin():
            pending_ids = (
                await session.scalars(
                    select(TelegramUpdate.update_id).where(
                        TelegramUpdate.status == UpdateStatus.PENDING
                    )
                )
            ).all()
            expired = (
                await session.scalars(
                    select(TelegramUpdate).where(
                        TelegramUpdate.status == UpdateStatus.PROCESSING,
                        TelegramUpdate.lease_expires_at.is_not(None),
                        TelegramUpdate.lease_expires_at < now,
                    )
                )
            ).all()
            expired_ids: list[int] = []
            for update in expired:
                update.status = UpdateStatus.PENDING
                update.lease_owner = None
                update.lease_token = None
                update.lease_expires_at = None
                expired_ids.append(update.update_id)
            terminal = (
                await session.scalars(
                    select(TelegramUpdate)
                    .where(
                        TelegramUpdate.status.in_([UpdateStatus.COMPLETED, UpdateStatus.FAILED]),
                        TelegramUpdate.reply_payload.is_not(None),
                    )
                    .order_by(TelegramUpdate.update_id)
                )
            ).all()
            resend_jobs = [
                (update.update_id, _missing_reply_tail(update))
                for update in terminal
                if _missing_reply_tail(update)
            ]

    for update_id in [*pending_ids, *expired_ids]:
        await queue.enqueue_update(update_id)
    result.pending_reenqueued = len(pending_ids)
    result.processing_reclaimed = len(expired_ids)

    for update_id, tail in resend_jobs:
        message_ids = await sender.send_reply_payload(tail)
        async with session_factory() as session:
            async with session.begin():
                update = await session.get(TelegramUpdate, update_id)
                if update is None:
                    continue
                existing = list(update.telegram_message_ids or [])
                update.telegram_message_ids = [*existing, *message_ids]
        result.terminal_resent += 1

    return result


def _missing_reply_tail(update: TelegramUpdate) -> list[dict[str, Any]]:
    reply_payload = list(update.reply_payload or [])
    sent_count = len(update.telegram_message_ids or [])
    if sent_count >= len(reply_payload):
        return []
    return reply_payload[sent_count:]
