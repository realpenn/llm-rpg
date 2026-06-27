import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from llm_rpg.config import Settings
from llm_rpg.db import Base
from llm_rpg.game.moderation import ModerationService
from llm_rpg.game.turn import process_player_turn
from llm_rpg.llm import FakeProvider
from llm_rpg.models import Event, Game, Player, SuggestedAction, TelegramUpdate, Turn
from llm_rpg.schemas.enums import ModerationAction, ModerationStage, UpdateStatus
from llm_rpg.schemas.turn import TurnOutput
from llm_rpg.worker import WorkerProcessor, record_telegram_update


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_reply_payload(self, reply_payload: list[dict]) -> list[int]:
        self.messages.extend(reply_payload)
        return list(range(1, len(reply_payload) + 1))


@pytest.fixture
def fake_telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.mark.asyncio
async def test_input_refuse_short_circuits_worker_without_turn(fake_telegram) -> None:
    session_factory = await _session_factory()
    turn_provider = FakeProvider(responses=[])
    async with session_factory() as session:
        async with session.begin():
            await _seed_active_game(session)
            await record_telegram_update(session, _message_update(1, "危险输入"))

    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=turn_provider,
        sender=fake_telegram,
        settings=Settings(),
        moderation_service=ModerationService(
            FakeProvider(
                responses=[{"action": "refuse", "flag": "unsafe", "message": "换个做法。"}]
            )
        ),
    )
    await worker.process_update_id(1)

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 1)
        turn_count = await session.scalar(select(func.count()).select_from(Turn))

    assert update is not None
    assert update.status == UpdateStatus.COMPLETED
    assert update.safety_flags[0]["stage"] == ModerationStage.INPUT
    assert update.safety_flags[0]["action"] == ModerationAction.REFUSE
    assert turn_count == 0
    assert turn_provider.calls == []
    assert fake_telegram.messages[0]["text"] == "换个做法。"


@pytest.mark.asyncio
async def test_input_warn_continues_and_is_recorded_on_turn_and_update(fake_telegram) -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            await _seed_active_game(session)
            await record_telegram_update(session, _message_update(2, "冒险但允许"))

    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(responses=[_turn_output()]),
        sender=fake_telegram,
        settings=Settings(),
        moderation_service=ModerationService(
            FakeProvider(
                responses=[
                    {"action": "warn", "flag": "risky"},
                    {"action": "allow", "flag": "none"},
                ]
            )
        ),
    )
    await worker.process_update_id(2)

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 2)
        turn = await session.scalar(select(Turn))
        game = await session.scalar(select(Game))

    assert update is not None
    assert update.safety_flags[0]["flag"] == "risky"
    assert turn is not None
    assert turn.safety_flags[0]["stage"] == "input"
    assert game is not None
    assert game.player_state["vitals"]["hp"] == 7


@pytest.mark.asyncio
async def test_output_refuse_drops_all_proposed_mutations() -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            _, game = await _seed_active_game(session)
            result = await process_player_turn(
                session,
                FakeProvider(responses=[_turn_output()]),
                game,
                "我行动",
                moderation_service=ModerationService(
                    FakeProvider(
                        responses=[
                            {
                                "action": "refuse",
                                "flag": "unsafe_output",
                                "message": "你决定先停下来。",
                            }
                        ]
                    )
                ),
            )
            turn_id = result.turn.id

    async with session_factory() as session:
        game = await session.scalar(select(Game))
        turn = await session.get(Turn, turn_id)
        event_count = await session.scalar(select(func.count()).select_from(Event))
        action_count = await session.scalar(select(func.count()).select_from(SuggestedAction))

    assert game is not None
    assert game.player_state["vitals"]["hp"] == 8
    assert event_count == 0
    assert action_count == 0
    assert turn is not None
    assert turn.narration == "你决定先停下来。"
    assert turn.safety_flags[0]["action"] == "refuse"


@pytest.mark.asyncio
async def test_output_soften_rewrites_once_and_keeps_softened_turn() -> None:
    service = ModerationService(
        FakeProvider(
            responses=[
                {"action": "soften", "flag": "tone"},
                {**_turn_output(), "narration": "柔化后的叙述"},
                {"action": "allow", "flag": "none"},
            ]
        )
    )

    result = await service.moderate_output(TurnOutput.model_validate(_turn_output()))

    assert result.refused is False
    assert result.output is not None
    assert result.output.narration == "柔化后的叙述"
    assert result.safety_flags[0].action == ModerationAction.SOFTEN
    assert result.safety_flags[0].rewrites == 1


async def _session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_active_game(session) -> tuple[Player, Game]:
    player = Player(telegram_user_id=42)
    game = Game(
        player=player,
        world_bible=_world_bible(),
        player_state={
            "name": "阿岚",
            "profession": "runner",
            "location": "old_city",
            "vitals": {"hp": 8, "energy": 5},
            "currency": {"coin": 2},
            "conditions": [],
            "flags": {},
            "inventory": {},
        },
        rolling_summary="开场",
        turn_number=0,
    )
    session.add_all([player, game])
    await session.flush()
    return player, game


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


def _world_bible() -> dict:
    return {
        "summary": "旧城被雨困住。",
        "language": "zh-CN",
        "genre": "mystery fantasy",
        "tone": "冷静",
        "era_geography": "近代海港旧城",
        "locked_laws": ["雨会记录谎言"],
        "locations": [
            {"key": "old_city", "name": "旧城", "description": "潮湿的街区"},
        ],
        "player_stat_schema": {
            "vitals": {
                "hp": {"min": 0, "max": 10, "default": 8},
                "energy": {"min": 0, "max": 10, "default": 5},
            },
            "currency": {"coin": {"min": 0, "max": 999, "default": 2}},
            "allowed_conditions": ["watched", "wounded"],
            "allowed_flags": {},
        },
        "initial_location": "old_city",
        "narrative_style": "第二人称",
        "core_conflict": "议会掩盖停钟真相。",
    }


def _turn_output() -> dict:
    return {
        "narration": "原始叙述",
        "state_delta": [{"path": "vitals.hp", "op": "add", "value": -1}],
        "npc_updates": [],
        "relationship_updates": [],
        "events": [{"summary": "事件", "location": "old_city"}],
        "suggested_actions": [
            {"label": "一", "action": "一"},
            {"label": "二", "action": "二"},
            {"label": "三", "action": "三"},
        ],
        "memory_update": [{"scope": "world", "content": "摘要"}],
        "time_advance": "minutes",
    }
