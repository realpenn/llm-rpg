import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from llm_rpg.config import Settings
from llm_rpg.db import Base
from llm_rpg.game.context import assemble_turn_context
from llm_rpg.game.memory import apply_memory_updates
from llm_rpg.game.turn import process_player_turn
from llm_rpg.llm import FakeProvider, StructuredOutputError
from llm_rpg.models import Event, Faction, Game, Npc, Player, Relationship, SuggestedAction, Turn
from llm_rpg.schemas.turn import MemoryUpdateEntry


@pytest.mark.asyncio
async def test_context_assembly_stays_within_budget() -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            _, game = await _seed_game(session, rolling_summary="旧事" * 500)
            session.add_all(
                [
                    Turn(
                        game=game,
                        sequence=index,
                        player_input="调查" * 50,
                        narration="叙述" * 100,
                        delta_audit={},
                        safety_flags=[],
                        game_clock={"turn_number": index},
                    )
                    for index in range(1, 8)
                ]
            )
            session.add(
                Event(
                    game_id=game.id,
                    summary="事件" * 100,
                    location="old_city",
                    involved_entities=["player"],
                )
            )
        context = await assemble_turn_context(
            session,
            game,
            "我询问闻鹤",
            settings=Settings(llm_context_token_budget=500),
        )

    assert context.char_count <= 500
    assert sum(len(section) for section in context.sections) <= 500


@pytest.mark.asyncio
async def test_process_player_turn_persists_state_entities_memory_and_clock() -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            _, game = await _seed_game(session)
            result = await process_player_turn(
                session,
                FakeProvider(responses=[_turn_output()]),
                game,
                "我询问闻鹤钟楼的秘密",
            )
            turn_id = result.turn.id
            game_id = game.id

    async with session_factory() as session:
        game = await session.get(Game, game_id)
        npc = await session.scalar(select(Npc).where(Npc.key == "archivist"))
        relationship = await session.scalar(select(Relationship))
        event = await session.scalar(select(Event).where(Event.turn_id == turn_id))
        actions = (await session.scalars(select(SuggestedAction))).all()

    assert game is not None
    assert game.turn_number == 1
    assert game.time_of_day == "hours"
    assert game.player_state["vitals"]["hp"] == 5
    assert "玩家得知闻鹤听过停钟后的声音" in game.rolling_summary
    assert npc is not None
    assert npc.revealed_to_player is True
    assert npc.faction == "rebels"
    assert "闻鹤承认自己害怕雨水毁掉禁档" in npc.memory_log
    assert relationship is not None
    assert relationship.edge_type == "player_npc"
    assert relationship.trust == 2
    assert event is not None
    assert event.summary == "闻鹤透露停钟线索"
    assert len(actions) == 3
    assert all(action.turn_id == turn_id for action in actions)


@pytest.mark.asyncio
async def test_memory_resummarization_failure_preserves_prior_entity_store() -> None:
    session_factory = await _session_factory()
    async with session_factory() as session:
        async with session.begin():
            _, game = await _seed_game(session, rolling_summary="旧摘要")
            npc = await session.scalar(select(Npc).where(Npc.key == "archivist"))
            assert npc is not None
            npc.memory_log = ["旧记忆"]
            await apply_memory_updates(
                session,
                game,
                FakeProvider(responses=[StructuredOutputError("summary failed")]),
                [MemoryUpdateEntry(scope="npc:archivist", content="新记忆")],
                settings=Settings(memory_entry_cap=1),
            )

    async with session_factory() as session:
        npc = await session.scalar(select(Npc).where(Npc.key == "archivist"))
        game = await session.scalar(select(Game))

    assert npc is not None
    assert npc.memory_log == ["旧记忆"]
    assert game is not None
    assert game.rolling_summary == "旧摘要"


async def _session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_game(session, rolling_summary: str = "开场") -> tuple[Player, Game]:
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
        rolling_summary=rolling_summary,
        turn_number=0,
    )
    session.add_all(
        [
            player,
            game,
            Faction(
                game=game,
                key="council",
                name="旧城议会",
                description="管理旧城",
                ideology="秩序",
                memory_log=[],
            ),
            Npc(
                game=game,
                key="archivist",
                name="闻鹤",
                role="keeper",
                faction="council",
                location="old_city",
                personality="谨慎",
                desire="保护档案",
                fear="档案被毁",
                goal="确认玩家可信",
                memory_log=[],
            ),
        ]
    )
    await session.flush()
    return player, game


def _world_bible() -> dict:
    return {
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
        "narrative_style": "第二人称",
        "core_conflict": "议会掩盖停钟真相。",
    }


def _turn_output() -> dict:
    return {
        "narration": "闻鹤沉默许久，承认钟楼停摆那夜他听见了亡者。",
        "state_delta": [{"path": "vitals.hp", "op": "add", "value": -3}],
        "npc_updates": [
            {
                "key": "archivist",
                "faction": "rebels",
                "revealed_to_player": True,
                "memory": "闻鹤承认自己害怕雨水毁掉禁档",
            }
        ],
        "relationship_updates": [
            {
                "source_key": "player",
                "target_key": "archivist",
                "edge_type": "player_npc",
                "trust_delta": 2,
                "note": "玩家认真听完了闻鹤的证词",
            }
        ],
        "events": [
            {
                "summary": "闻鹤透露停钟线索",
                "location": "old_city",
                "involved_entities": ["player", "archivist"],
            }
        ],
        "suggested_actions": [
            {"label": "追问亡者", "action": "追问亡者说了什么"},
            {"label": "检查钟楼", "action": "前往钟楼"},
            {"label": "安抚闻鹤", "action": "让闻鹤冷静下来"},
        ],
        "memory_update": [
            {"scope": "world", "content": "玩家得知闻鹤听过停钟后的声音"},
            {"scope": "npc:archivist", "content": "闻鹤向玩家透露停钟线索"},
        ],
        "time_advance": "hours",
    }
