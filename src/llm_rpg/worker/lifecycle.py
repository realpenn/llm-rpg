from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_rpg.models import TelegramUpdate
from llm_rpg.schemas.enums import DropReason, UpdateStatus
from llm_rpg.telegram.router import ParsedTelegramUpdate, parse_telegram_update
from llm_rpg.telegram.sender import message_payload

TERMINAL_STATUSES = {
    UpdateStatus.COMPLETED,
    UpdateStatus.DROPPED,
    UpdateStatus.FAILED,
}


async def record_telegram_update(
    session: AsyncSession,
    raw_update: dict[str, Any],
) -> tuple[TelegramUpdate, bool]:
    parsed = parse_telegram_update(raw_update)
    existing = await session.get(TelegramUpdate, parsed.update_id)
    if existing is not None:
        return existing, False

    processing = await _current_processing_turn(session, parsed)
    if parsed.turn_producing and processing is not None:
        update = _new_update(parsed)
        update.status = UpdateStatus.DROPPED
        update.drop_reason = DropReason.IN_FLIGHT
        update.blocked_by_update_id = processing.update_id
        update.reply_payload = [_still_acting_payload(parsed.telegram_chat_id)]
        session.add(update)
        return update, True

    update = _new_update(parsed)
    session.add(update)
    return update, True


async def claim_next_update_for_processing(
    session: AsyncSession,
    popped_update_id: int,
    *,
    lease_owner: str,
    lease_token: str,
    lease_ttl_seconds: int,
) -> TelegramUpdate | None:
    popped = await session.get(TelegramUpdate, popped_update_id)
    if popped is None or popped.status != UpdateStatus.PENDING:
        return None

    if popped.turn_producing:
        target = await session.scalar(
            select(TelegramUpdate)
            .where(
                TelegramUpdate.telegram_user_id == popped.telegram_user_id,
                TelegramUpdate.turn_producing.is_(True),
                TelegramUpdate.status == UpdateStatus.PENDING,
            )
            .order_by(TelegramUpdate.update_id)
            .limit(1)
        )
    else:
        target = popped
    if target is None:
        return None

    target.status = UpdateStatus.PROCESSING
    target.lease_owner = lease_owner
    target.lease_token = lease_token
    target.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_ttl_seconds)

    if target.turn_producing:
        await _drop_higher_pending_turns(session, target)
    return target


async def complete_update(
    session: AsyncSession,
    update: TelegramUpdate,
    *,
    reply_payload: list[dict[str, Any]],
    game_id: str | None = None,
    turn_id: str | None = None,
    lease_owner: str | None = None,
    lease_token: str | None = None,
) -> bool:
    if not _lease_matches(update, lease_owner, lease_token):
        return False
    update.status = UpdateStatus.COMPLETED
    update.reply_payload = reply_payload
    update.game_id = game_id
    update.turn_id = turn_id
    return True


async def fail_update(
    session: AsyncSession,
    update: TelegramUpdate,
    *,
    error_text: str,
    reply_payload: list[dict[str, Any]],
    lease_owner: str | None = None,
    lease_token: str | None = None,
) -> bool:
    if not _lease_matches(update, lease_owner, lease_token):
        return False
    update.status = UpdateStatus.FAILED
    update.error_text = error_text
    update.reply_payload = reply_payload
    return True


async def renew_lease(
    update: TelegramUpdate,
    *,
    lease_owner: str,
    lease_token: str,
    lease_ttl_seconds: int,
) -> bool:
    if not _lease_matches(update, lease_owner, lease_token):
        return False
    update.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_ttl_seconds)
    return True


async def schedule_retry_or_fail(
    update: TelegramUpdate,
    *,
    error_text: str,
    chat_id: int,
    max_retries: int,
    retry_backoff_seconds: int,
    lease_ttl_seconds: int,
) -> None:
    update.error_text = error_text
    if update.retry_count < max_retries:
        update.retry_count += 1
        update.next_retry_at = datetime.now(UTC) + timedelta(seconds=retry_backoff_seconds)
        update.lease_expires_at = update.next_retry_at + timedelta(seconds=lease_ttl_seconds)
        return
    update.status = UpdateStatus.FAILED
    update.reply_payload = [
        message_payload(chat_id=chat_id, text="这次行动暂时处理失败，可以稍后再试。")
    ]


def parse_recorded_update(update: TelegramUpdate) -> ParsedTelegramUpdate:
    return parse_telegram_update(update.raw_update)


def _lease_matches(
    update: TelegramUpdate,
    lease_owner: str | None,
    lease_token: str | None,
) -> bool:
    if lease_owner is None and lease_token is None:
        return True
    return update.lease_owner == lease_owner and update.lease_token == lease_token


async def _current_processing_turn(
    session: AsyncSession,
    parsed: ParsedTelegramUpdate,
) -> TelegramUpdate | None:
    return await session.scalar(
        select(TelegramUpdate)
        .where(
            TelegramUpdate.telegram_user_id == parsed.telegram_user_id,
            TelegramUpdate.turn_producing.is_(True),
            TelegramUpdate.status == UpdateStatus.PROCESSING,
        )
        .order_by(TelegramUpdate.update_id)
        .limit(1)
    )


async def _drop_higher_pending_turns(session: AsyncSession, target: TelegramUpdate) -> None:
    updates = await session.scalars(
        select(TelegramUpdate).where(
            TelegramUpdate.telegram_user_id == target.telegram_user_id,
            TelegramUpdate.turn_producing.is_(True),
            TelegramUpdate.status == UpdateStatus.PENDING,
            TelegramUpdate.update_id > target.update_id,
        )
    )
    for update in updates:
        update.status = UpdateStatus.DROPPED
        update.drop_reason = DropReason.IN_FLIGHT
        update.blocked_by_update_id = target.update_id
        update.reply_payload = [_still_acting_payload(update.telegram_chat_id)]


def _new_update(parsed: ParsedTelegramUpdate) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=parsed.update_id,
        telegram_user_id=parsed.telegram_user_id,
        telegram_chat_id=parsed.telegram_chat_id,
        update_kind=parsed.update_kind,
        turn_producing=parsed.turn_producing,
        status=UpdateStatus.PENDING,
        raw_update=parsed.raw_update,
    )


def _still_acting_payload(chat_id: int) -> dict[str, Any]:
    return message_payload(chat_id=chat_id, text="你还在处理上一段行动，稍等片刻。")
