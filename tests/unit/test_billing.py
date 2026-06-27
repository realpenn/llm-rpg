import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from llm_rpg.db import Base
from llm_rpg.game.billing import consume_turn_credit, redeem_recharge_code
from llm_rpg.models import Player, RechargeCode


@pytest.mark.asyncio
async def test_consume_turn_credit_uses_database_current_value() -> None:
    session_factory = await _session_factory()

    async with session_factory() as session:
        async with session.begin():
            player = Player(telegram_user_id=42, remaining_turns=1)
            session.add(player)
            await session.flush()

            await session.execute(
                update(Player)
                .where(Player.id == player.id)
                .values(remaining_turns=Player.remaining_turns + 10)
                .execution_options(synchronize_session=False)
            )
            assert player.remaining_turns == 1

            consumed = await consume_turn_credit(session, player)

            assert consumed
            assert player.remaining_turns == 10

    async with session_factory() as session:
        saved = await session.scalar(select(Player).where(Player.telegram_user_id == 42))

    assert saved is not None
    assert saved.remaining_turns == 10


@pytest.mark.asyncio
async def test_redeem_recharge_code_uses_atomic_player_increment() -> None:
    session_factory = await _session_factory()

    async with session_factory() as session:
        async with session.begin():
            player = Player(telegram_user_id=42, remaining_turns=1)
            code = RechargeCode(
                code="RPG-TEST-ATOM-0001",
                turn_amount=10,
                unlimited=False,
                created_by_telegram_user_id=99,
            )
            session.add_all([player, code])
            await session.flush()

            await session.execute(
                update(Player)
                .where(Player.id == player.id)
                .values(remaining_turns=Player.remaining_turns + 5)
                .execution_options(synchronize_session=False)
            )
            assert player.remaining_turns == 1

            result = await redeem_recharge_code(
                session,
                player=player,
                raw_code="RPG-TEST-ATOM-0001",
            )

            assert result.status == "ok"
            assert player.remaining_turns == 16

    async with session_factory() as session:
        saved = await session.scalar(select(Player).where(Player.telegram_user_id == 42))

    assert saved is not None
    assert saved.remaining_turns == 16


async def _session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)
