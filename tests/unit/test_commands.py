from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from llm_rpg.config import Settings
from llm_rpg.db import Base
from llm_rpg.llm import FakeProvider
from llm_rpg.models import (
    Game,
    LlmCall,
    Npc,
    Player,
    RechargeCode,
    SuggestedAction,
    TelegramUpdate,
    Turn,
)
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


@pytest.mark.asyncio
async def test_new_game_requires_quota_before_calling_provider() -> None:
    session_factory = await _session_factory()
    sender = Sender()
    provider = FakeProvider()
    async with session_factory() as session:
        async with session.begin():
            session.add(Player(telegram_user_id=42, remaining_turns=0))
            await record_telegram_update(session, _message_update(40, "/new 旧城雨夜"))

    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=provider,
        sender=sender,
        settings=Settings(),
    )
    await worker.process_update_id(40)

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 40)
        game = await session.scalar(select(Game))

    assert provider.calls == []
    assert game is None
    assert update is not None
    assert update.status == UpdateStatus.DROPPED
    assert update.drop_reason == DropReason.QUOTA_EXHAUSTED
    assert sender.messages[0]["text"] == "剩余额度不足。请使用 /recharge <充值码> 充值后继续。"


@pytest.mark.asyncio
async def test_admin_generates_recharge_code_and_player_redeems_once() -> None:
    session_factory = await _session_factory()
    sender = Sender()
    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(),
        sender=sender,
        settings=Settings(admin_user_ids=[42]),
    )
    async with session_factory() as session:
        async with session.begin():
            await record_telegram_update(session, _message_update(50, "/admin_code 10 2"))
    await worker.process_update_id(50)

    async with session_factory() as session:
        codes = (await session.scalars(select(RechargeCode).order_by(RechargeCode.code))).all()
    assert len(codes) == 2
    assert {code.turn_amount for code in codes} == {10}
    assert all(not code.unlimited for code in codes)
    assert sender.messages[0]["text"].startswith("已生成 2 个10 回合充值码：")

    code = codes[0].code
    async with session_factory() as session:
        async with session.begin():
            await record_telegram_update(session, _message_update(51, f"/recharge {code}"))
            await record_telegram_update(session, _message_update(52, f"/recharge {code}"))
    await worker.process_update_id(51)
    await worker.process_update_id(52)

    async with session_factory() as session:
        player = await session.scalar(select(Player).where(Player.telegram_user_id == 42))
        used_code = await session.scalar(select(RechargeCode).where(RechargeCode.code == code))

    assert player is not None
    assert player.remaining_turns == 20
    assert used_code is not None
    assert used_code.used_by_player_id == player.id
    assert used_code.used_at is not None
    assert sender.messages[1]["text"] == "充值成功，增加 10 回合。当前剩余额度：20 回合。"
    assert sender.messages[2]["text"] == "这个充值码已经被使用。"


@pytest.mark.asyncio
async def test_unlimited_recharge_code_sets_unlimited_quota() -> None:
    session_factory = await _session_factory()
    sender = Sender()
    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(),
        sender=sender,
        settings=Settings(admin_user_ids=[42]),
    )
    async with session_factory() as session:
        async with session.begin():
            await record_telegram_update(session, _message_update(60, "/admin_code unlimited"))
    await worker.process_update_id(60)

    async with session_factory() as session:
        code = await session.scalar(select(RechargeCode))
    assert code is not None
    assert code.unlimited
    assert code.turn_amount is None

    async with session_factory() as session:
        async with session.begin():
            await record_telegram_update(session, _message_update(61, f"/recharge {code.code}"))
            await record_telegram_update(session, _message_update(62, "/quota"))
    await worker.process_update_id(61)
    await worker.process_update_id(62)

    async with session_factory() as session:
        player = await session.scalar(select(Player).where(Player.telegram_user_id == 42))

    assert player is not None
    assert player.has_unlimited_turns
    assert sender.messages[1]["text"] == "充值成功，已解锁无限回合。"
    assert sender.messages[2]["text"] == "当前剩余额度：无限回合。"


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
