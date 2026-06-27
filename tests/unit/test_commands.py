from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from llm_rpg.config import Settings
from llm_rpg.db import Base
from llm_rpg.llm import FakeProvider
from llm_rpg.models import Game, LlmCall, Npc, Player, SuggestedAction, TelegramUpdate, Turn
from llm_rpg.schemas.enums import DropReason, UpdateStatus
from llm_rpg.worker import WorkerProcessor, record_telegram_update


class Sender:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_reply_payload(self, reply_payload: list[dict]) -> list[int]:
        self.messages.extend(reply_payload)
        return list(range(1, len(reply_payload) + 1))


@pytest.mark.asyncio
async def test_state_commands_and_admin_gate() -> None:
    session_factory = await _session_factory()
    sender = Sender()
    async with session_factory() as session:
        async with session.begin():
            await _seed_active_game(session)
            for update_id, text in [
                (1, "/world"),
                (2, "/people"),
                (3, "/status"),
                (4, "/inventory"),
                (5, "/admin_stats"),
            ]:
                await record_telegram_update(session, _message_update(update_id, text))

    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(),
        sender=sender,
        settings=Settings(admin_user_ids=[42]),
    )
    for update_id in range(1, 6):
        await worker.process_update_id(update_id)

    texts = [message["text"] for message in sender.messages]
    assert "旧城被雨困住。" in texts[0]
    assert "闻鹤" in texts[1]
    assert "未露面者" not in texts[1]
    assert "位置：old_city" in texts[2]
    assert "铜制提灯" in texts[3]
    assert "players=1" in texts[4]


@pytest.mark.asyncio
async def test_reset_archives_active_game_and_archive_lists_summary() -> None:
    session_factory = await _session_factory()
    sender = Sender()
    async with session_factory() as session:
        async with session.begin():
            game = await _seed_active_game(session)
            session.add(
                LlmCall(
                    game_id=game.id,
                    purpose="turn",
                    provider="fake",
                    model="fake",
                    mode_used="json_object",
                    outcome="ok",
                    request_messages=[],
                    request_hash="hash",
                )
            )
            await record_telegram_update(session, _message_update(10, "/reset"))
            await record_telegram_update(session, _message_update(11, "/archive"))
            await record_telegram_update(session, _message_update(12, "/world"))

    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(),
        sender=sender,
        settings=Settings(),
    )
    for update_id in [10, 11, 12]:
        await worker.process_update_id(update_id)

    async with session_factory() as session:
        game = await session.scalar(select(Game))
        actions = (await session.scalars(select(SuggestedAction))).all()
        calls = (await session.scalars(select(LlmCall))).all()

    assert game is not None
    assert game.archived_at is not None
    assert actions == []
    assert calls == []
    assert sender.messages[0]["text"] == "当前游戏已归档。"
    assert "旧城被雨困住。" in sender.messages[1]["text"]
    assert sender.messages[2]["text"] == "还没有进行中的游戏。发送 /new 开始。"


@pytest.mark.asyncio
async def test_stale_callback_is_dropped() -> None:
    session_factory = await _session_factory()
    sender = Sender()
    async with session_factory() as session:
        async with session.begin():
            game = await _seed_active_game(session)
            old_turn = await session.scalar(select(Turn).where(Turn.sequence == 0))
            latest_turn = Turn(
                game_id=game.id,
                sequence=1,
                narration="latest",
                delta_audit={},
                safety_flags=[],
                game_clock={"turn_number": 1},
            )
            session.add(latest_turn)
            await session.flush()
            assert old_turn is not None
            action = SuggestedAction(
                game_id=game.id,
                turn_id=old_turn.id,
                callback_id="act_stale",
                label="旧行动",
                action="旧行动",
            )
            session.add(action)
            await record_telegram_update(session, _callback_update(20, "act_stale"))

    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(),
        sender=sender,
        settings=Settings(),
    )
    await worker.process_update_id(20)

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 20)

    assert update is not None
    assert update.status == UpdateStatus.DROPPED
    assert update.drop_reason == DropReason.STALE_CALLBACK
    assert sender.messages[0]["text"] == "这个选项已经过期。"


@pytest.mark.asyncio
async def test_archived_game_callback_is_rejected() -> None:
    session_factory = await _session_factory()
    sender = Sender()
    async with session_factory() as session:
        async with session.begin():
            game = await _seed_active_game(session)
            game.archived_at = datetime.now(UTC)
            turn = await session.scalar(select(Turn).where(Turn.sequence == 0))
            assert turn is not None
            session.add(
                SuggestedAction(
                    game_id=game.id,
                    turn_id=turn.id,
                    callback_id="act_archived",
                    label="归档行动",
                    action="归档行动",
                )
            )
            await record_telegram_update(session, _callback_update(30, "act_archived"))

    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(),
        sender=sender,
        settings=Settings(),
    )
    await worker.process_update_id(30)

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 30)

    assert update is not None
    assert update.status == UpdateStatus.DROPPED
    assert update.drop_reason == DropReason.ARCHIVED_GAME
    assert sender.messages[0]["text"] == "这局游戏已归档。"


async def _session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_active_game(session) -> Game:
    player = Player(telegram_user_id=42)
    game = Game(
        player=player,
        world_bible={"summary": "旧城被雨困住。"},
        player_state={
            "location": "old_city",
            "vitals": {"hp": 8},
            "inventory": {"lantern": {"key": "lantern", "name": "铜制提灯", "quantity": 1}},
        },
        rolling_summary="",
        turn_number=0,
    )
    session.add_all(
        [
            player,
            game,
            Npc(
                game=game,
                key="archivist",
                name="闻鹤",
                role="keeper",
                location="old_city",
                personality="谨慎",
                desire="保护档案",
                fear="档案被毁",
                goal="确认玩家可信",
                revealed_to_player=True,
                memory_log=[],
            ),
            Npc(
                game=game,
                key="hidden",
                name="未露面者",
                role="spy",
                location="old_city",
                personality="沉默",
                desire="观察",
                fear="暴露",
                goal="隐藏",
                revealed_to_player=False,
                memory_log=[],
            ),
        ]
    )
    await session.flush()
    turn = Turn(
        game_id=game.id,
        sequence=0,
        narration="opening",
        delta_audit={},
        safety_flags=[],
        game_clock={"turn_number": 0},
    )
    session.add(turn)
    await session.flush()
    session.add(
        SuggestedAction(
            game_id=game.id,
            turn_id=turn.id,
            callback_id="act_current",
            label="当前行动",
            action="当前行动",
        )
    )
    await session.flush()
    assert len(b"act_current") <= 64
    return game


def _message_update(update_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 10,
            "from": {"id": 42, "is_bot": False, "first_name": "玩家"},
            "chat": {"id": 420, "type": "private"},
            "text": text,
        },
    }


def _callback_update(update_id: int, callback_id: str) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "from": {"id": 42, "is_bot": False, "first_name": "玩家"},
            "message": {"message_id": update_id + 10, "chat": {"id": 420, "type": "private"}},
            "data": callback_id,
        },
    }
