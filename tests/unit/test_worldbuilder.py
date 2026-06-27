import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from llm_rpg.db import Base
from llm_rpg.game.worldbuilder import build_and_persist_world
from llm_rpg.llm import FakeProvider, StructuredOutputError
from llm_rpg.models import Faction, Game, Npc, Player, SuggestedAction, Turn


@pytest.mark.asyncio
async def test_worldbuilder_persists_world_opening_turn_and_actions() -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            player = Player(telegram_user_id=42)
            session.add(player)
            await session.flush()
            result = await build_and_persist_world(
                session,
                FakeProvider(responses=[_world_build_output()]),
                player,
                "旧城雨夜",
            )

        game_id = result.game.id
        turn_id = result.opening_turn.id

    async with session_factory() as session:
        game = await session.get(Game, game_id)
        faction_count = await session.scalar(select(func.count()).select_from(Faction))
        npc = await session.scalar(select(Npc).where(Npc.key == "archivist"))
        opening_turn = await session.get(Turn, turn_id)
        actions = (await session.scalars(select(SuggestedAction))).all()

    assert game is not None
    assert game.world_bible["summary"] == "旧城被雨困住。"
    assert game.turn_number == 0
    assert faction_count == 1
    assert npc is not None
    assert npc.revealed_to_player is False
    assert opening_turn is not None
    assert opening_turn.sequence == 0
    assert opening_turn.narration == "你在旧城档案馆门前醒来。"
    assert len(actions) == 3
    assert all(action.turn_id == turn_id for action in actions)
    assert all(len(action.callback_id.encode("utf-8")) <= 64 for action in actions)


@pytest.mark.asyncio
async def test_worldbuilder_does_not_write_game_when_provider_fails() -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            player = Player(telegram_user_id=43)
            session.add(player)
            await session.flush()
            with pytest.raises(StructuredOutputError):
                await build_and_persist_world(
                    session,
                    FakeProvider(responses=[StructuredOutputError("bad json")]),
                    player,
                    "坏输出",
                )

    async with session_factory() as session:
        game_count = await session.scalar(select(func.count()).select_from(Game))

    assert game_count == 0


async def _session_factory() -> async_sessionmaker:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _world_build_output() -> dict:
    return {
        "world": {
            "summary": "旧城被雨困住。",
            "language": "zh-CN",
            "genre": "mystery fantasy",
            "tone": "冷静",
            "era_geography": "近代海港旧城",
            "locked_laws": ["雨会记录谎言"],
            "factions": [
                {
                    "key": "council",
                    "name": "旧城议会",
                    "description": "管理旧城",
                    "ideology": "秩序",
                }
            ],
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
                "allowed_flags": {
                    "knows_clock_secret": {"type": "boolean"},
                },
            },
            "initial_location": "old_city",
            "initial_npcs": [
                {
                    "key": "archivist",
                    "name": "闻鹤",
                    "role": "keeper",
                    "faction": "council",
                    "location": "old_city",
                    "personality": "谨慎",
                    "desire": "保护档案",
                    "fear": "档案被毁",
                    "goal": "确认玩家可信",
                }
            ],
            "dangers": ["宵禁"],
            "available_roles": ["runner"],
            "narrative_style": "第二人称",
            "taboos": ["不改写雨的规则"],
            "core_conflict": "议会掩盖停钟真相。",
        },
        "opening_narration": "你在旧城档案馆门前醒来。",
        "player_state": {
            "name": "阿岚",
            "profession": "runner",
            "location": "old_city",
            "vitals": {"hp": 8, "energy": 5},
            "currency": {"coin": 2},
            "conditions": [],
            "flags": {},
            "inventory": {},
        },
        "initial_suggested_actions": ["查看档案馆", "寻找巡夜人", "检查随身物品"],
    }
