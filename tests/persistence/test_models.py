import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from llm_rpg.db import Base
from llm_rpg.models import (
    Event,
    Faction,
    Game,
    Npc,
    Player,
    SuggestedAction,
    TelegramUpdate,
    Turn,
)
from llm_rpg.schemas.enums import UpdateStatus


@pytest.mark.asyncio
async def test_model_round_trip() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        player = Player(telegram_user_id=42, username="player")
        game = Game(
            player=player,
            world_bible={"summary": "locked"},
            player_state={"location": "old_city", "vitals": {"hp": 8}},
            rolling_summary="opening",
            turn_number=0,
        )
        faction = Faction(
            game=game,
            key="council",
            name="旧城议会",
            description="管理旧城",
            ideology="秩序",
            memory_log=[],
        )
        npc = Npc(
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
        )
        turn = Turn(
            game=game,
            sequence=0,
            narration="开场",
            delta_audit={"accepted": []},
            safety_flags=[],
            game_clock={"turn_number": 0},
        )
        session.add_all([player, game, faction, npc, turn])
        await session.flush()

        action = SuggestedAction(
            game_id=game.id,
            turn_id=turn.id,
            callback_id="act_1",
            label="查看档案",
            action="查看档案",
        )
        event = Event(
            game_id=game.id,
            turn_id=turn.id,
            summary="玩家进入旧城",
            location="old_city",
            involved_entities=["player", "archivist"],
        )
        update = TelegramUpdate(
            update_id=1001,
            telegram_user_id=42,
            telegram_chat_id=420,
            update_kind="message",
            turn_producing=True,
            status=UpdateStatus.COMPLETED,
            raw_update={"message": {"text": "/new"}},
            game_id=game.id,
            turn_id=turn.id,
            reply_payload=[{"text": "开场"}],
            telegram_message_ids=[1],
        )
        session.add_all([action, event, update])
        await session.commit()

    async with session_factory() as session:
        saved_game = await session.scalar(select(Game).where(Game.player_id == player.id))
        saved_update = await session.scalar(
            select(TelegramUpdate).where(TelegramUpdate.update_id == 1001)
        )

    assert saved_game is not None
    assert saved_game.world_bible["summary"] == "locked"
    assert saved_game.player_state["vitals"]["hp"] == 8
    assert saved_update is not None
    assert saved_update.status == UpdateStatus.COMPLETED
    assert saved_update.reply_payload == [{"text": "开场"}]

    await engine.dispose()
