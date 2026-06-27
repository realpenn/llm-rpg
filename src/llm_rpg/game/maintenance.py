from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_rpg.models import Game, LlmCall, SuggestedAction, Turn


async def sweep_suggested_actions(
    session: AsyncSession,
    *,
    game_id: str,
    retain_turns: int,
) -> int:
    turn_ids = (
        await session.scalars(
            select(Turn.id)
            .where(Turn.game_id == game_id)
            .order_by(Turn.sequence.desc())
            .limit(retain_turns)
        )
    ).all()
    result = await session.execute(
        delete(SuggestedAction).where(
            SuggestedAction.game_id == game_id,
            SuggestedAction.turn_id.not_in(turn_ids),
        )
    )
    return result.rowcount or 0


async def archive_game(session: AsyncSession, game: Game) -> None:
    game.archived_at = datetime.now(UTC)
    await session.execute(delete(SuggestedAction).where(SuggestedAction.game_id == game.id))
    await session.execute(delete(LlmCall).where(LlmCall.game_id == game.id))


async def sweep_llm_calls(
    session: AsyncSession,
    *,
    game_id: str,
    retain_count: int,
) -> int:
    keep_ids = (
        await session.scalars(
            select(LlmCall.id)
            .where(LlmCall.game_id == game_id)
            .order_by(LlmCall.created_at.desc())
            .limit(retain_count)
        )
    ).all()
    result = await session.execute(
        delete(LlmCall).where(LlmCall.game_id == game_id, LlmCall.id.not_in(keep_ids))
    )
    return result.rowcount or 0
