from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from llm_rpg.db import Base
from llm_rpg.game.maintenance import archive_game, sweep_llm_calls, sweep_suggested_actions
from llm_rpg.models import Game, LlmCall, Player, SuggestedAction, TelegramUpdate, Turn
from llm_rpg.schemas.enums import DropReason, UpdateStatus
from llm_rpg.telegram.sender import answer_callback_payload, message_payload
from llm_rpg.worker.lifecycle import complete_update, schedule_retry_or_fail
from llm_rpg.worker.reconciler import reconcile_once


class Queue:
    def __init__(self) -> None:
        self.ids: list[int] = []

    async def enqueue_update(self, update_id: int) -> None:
        self.ids.append(update_id)


class Sender:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.next_id = 10

    async def send_reply_payload(self, reply_payload: list[dict[str, Any]]) -> list[int]:
        ids: list[int] = []
        for payload in reply_payload:
            self.messages.append(payload)
            ids.append(self.next_id)
            self.next_id += 1
        return ids


@pytest.mark.asyncio
async def test_reconciler_reenqueues_pending_and_reclaims_expired_processing() -> None:
    session_factory = await _session_factory()
    now = datetime.now(UTC)
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    _update(1, UpdateStatus.PENDING),
                    _update(
                        2,
                        UpdateStatus.PROCESSING,
                        lease_expires_at=now - timedelta(seconds=1),
                        lease_owner="old",
                        lease_token="old-token",
                    ),
                    _update(
                        3,
                        UpdateStatus.PROCESSING,
                        lease_expires_at=now + timedelta(minutes=1),
                        lease_owner="alive",
                        lease_token="alive-token",
                    ),
                ]
            )

    queue = Queue()
    result = await reconcile_once(session_factory=session_factory, queue=queue, sender=Sender())

    async with session_factory() as session:
        expired = await session.get(TelegramUpdate, 2)
        alive = await session.get(TelegramUpdate, 3)

    assert result.pending_reenqueued == 1
    assert result.processing_reclaimed == 1
    assert queue.ids == [1, 2]
    assert expired is not None
    assert expired.status == UpdateStatus.PENDING
    assert expired.lease_token is None
    assert alive is not None
    assert alive.status == UpdateStatus.PROCESSING


@pytest.mark.asyncio
async def test_reconciler_resends_completed_and_failed_but_not_dropped() -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    _update(
                        4,
                        UpdateStatus.COMPLETED,
                        reply_payload=[
                            message_payload(chat_id=1, text="sent"),
                            message_payload(chat_id=1, text="tail"),
                        ],
                        telegram_message_ids=[99],
                    ),
                    _update(
                        5,
                        UpdateStatus.FAILED,
                        reply_payload=[message_payload(chat_id=1, text="failed")],
                    ),
                    _update(
                        6,
                        UpdateStatus.DROPPED,
                        drop_reason=DropReason.IN_FLIGHT,
                        reply_payload=[message_payload(chat_id=1, text="dropped")],
                    ),
                ]
            )

    sender = Sender()
    result = await reconcile_once(session_factory=session_factory, queue=Queue(), sender=sender)

    async with session_factory() as session:
        completed = await session.get(TelegramUpdate, 4)
        failed = await session.get(TelegramUpdate, 5)
        dropped = await session.get(TelegramUpdate, 6)

    assert result.terminal_resent == 2
    assert [message["text"] for message in sender.messages] == ["tail", "failed"]
    assert completed is not None
    assert completed.telegram_message_ids == [99, 10]
    assert failed is not None
    assert failed.telegram_message_ids == [11]
    assert dropped is not None
    assert dropped.telegram_message_ids is None


@pytest.mark.asyncio
async def test_reconciler_treats_callback_marker_as_delivered_payload() -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            session.add(
                _update(
                    12,
                    UpdateStatus.COMPLETED,
                    reply_payload=[
                        answer_callback_payload(callback_query_id="callback-1"),
                        message_payload(chat_id=1, text="tail"),
                    ],
                    telegram_message_ids=[0],
                )
            )

    sender = Sender()
    result = await reconcile_once(session_factory=session_factory, queue=Queue(), sender=sender)

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 12)

    assert result.terminal_resent == 1
    assert [message["text"] for message in sender.messages] == ["tail"]
    assert update is not None
    assert update.telegram_message_ids == [0, 10]


@pytest.mark.asyncio
async def test_complete_update_cas_prevents_stale_commit() -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            update = _update(
                7,
                UpdateStatus.PROCESSING,
                lease_owner="worker",
                lease_token="new-token",
            )
            session.add(update)
            ok = await complete_update(
                session,
                update,
                reply_payload=[message_payload(chat_id=1, text="late")],
                lease_owner="worker",
                lease_token="old-token",
            )

    async with session_factory() as session:
        saved = await session.get(TelegramUpdate, 7)

    assert ok is False
    assert saved is not None
    assert saved.status == UpdateStatus.PROCESSING
    assert saved.reply_payload is None


@pytest.mark.asyncio
async def test_retry_backoff_then_terminal_failed() -> None:
    update = _update(8, UpdateStatus.PROCESSING, retry_count=0)

    await schedule_retry_or_fail(
        update,
        error_text="timeout",
        chat_id=1,
        max_retries=1,
        retry_backoff_seconds=5,
        lease_ttl_seconds=30,
    )
    assert update.status == UpdateStatus.PROCESSING
    assert update.retry_count == 1
    assert update.next_retry_at is not None

    await schedule_retry_or_fail(
        update,
        error_text="timeout again",
        chat_id=1,
        max_retries=1,
        retry_backoff_seconds=5,
        lease_ttl_seconds=30,
    )
    assert update.status == UpdateStatus.FAILED
    assert update.reply_payload[0]["text"] == "这次行动暂时处理失败，可以稍后再试。"


@pytest.mark.asyncio
async def test_sweepers_prune_actions_and_archive_deletes_transient_rows() -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            game = await _seed_game_with_transients(session)
            game_id = game.id
            removed = await sweep_suggested_actions(session, game_id=game_id, retain_turns=2)
            llm_removed = await sweep_llm_calls(session, game_id=game_id, retain_count=1)
            await archive_game(session, game)

    async with session_factory() as session:
        game = await session.get(Game, game_id)
        action_count = await session.scalar(select(func.count()).select_from(SuggestedAction))
        call_count = await session.scalar(select(func.count()).select_from(LlmCall))

    assert removed == 3
    assert llm_removed == 2
    assert game is not None
    assert game.archived_at is not None
    assert action_count == 0
    assert call_count == 0


async def _session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _update(update_id: int, status: UpdateStatus, **kwargs: Any) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=update_id,
        telegram_user_id=42,
        telegram_chat_id=420,
        update_kind="message",
        turn_producing=True,
        status=status,
        raw_update={
            "update_id": update_id,
            "message": {
                "from": {"id": 42},
                "chat": {"id": 420},
                "text": "行动",
            },
        },
        **kwargs,
    )


async def _seed_game_with_transients(session) -> Game:
    player = Player(telegram_user_id=99)
    game = Game(
        player=player,
        world_bible={"summary": "world"},
        player_state={"location": "old_city"},
        rolling_summary="",
        turn_number=4,
    )
    session.add_all([player, game])
    await session.flush()
    for sequence in range(5):
        turn = Turn(
            game_id=game.id,
            sequence=sequence,
            narration=f"turn {sequence}",
            delta_audit={},
            safety_flags=[],
            game_clock={"turn_number": sequence},
        )
        session.add(turn)
        await session.flush()
        session.add(
            SuggestedAction(
                game_id=game.id,
                turn_id=turn.id,
                callback_id=f"act_{sequence}",
                label="行动",
                action="行动",
            )
        )
    for index in range(3):
        session.add(
            LlmCall(
                game_id=game.id,
                purpose="turn",
                provider="fake",
                model="fake",
                mode_used="json_object",
                outcome="ok",
                request_messages=[],
                request_hash=f"hash-{index}",
            )
        )
    await session.flush()
    return game
