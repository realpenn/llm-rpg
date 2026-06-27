from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_rpg.config import Settings, get_settings
from llm_rpg.llm.base import ChatMessage, Provider
from llm_rpg.models import Faction, Game, Npc
from llm_rpg.schemas.enums import LlmPurpose
from llm_rpg.schemas.turn import MemoryUpdateEntry


class MemorySummary(BaseModel):
    entries: list[str] = Field(min_length=1, max_length=32)


async def apply_memory_updates(
    session: AsyncSession,
    game: Game,
    provider: Provider,
    updates: list[MemoryUpdateEntry],
    *,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    for update in updates:
        scope_type, key = _split_scope(update.scope)
        content = update.content[: settings.memory_entry_char_cap]
        if scope_type == "world":
            await _append_world_memory(game, provider, content, settings)
        elif scope_type == "npc":
            npc = await session.scalar(select(Npc).where(Npc.game_id == game.id, Npc.key == key))
            if npc is not None:
                if await _append_entity_memory(npc, provider, content, settings):
                    await _append_world_digest(game, provider, f"{npc.name}: {content}", settings)
        elif scope_type == "faction":
            faction = await session.scalar(
                select(Faction).where(Faction.game_id == game.id, Faction.key == key)
            )
            if faction is not None:
                if await _append_entity_memory(faction, provider, content, settings):
                    await _append_world_digest(
                        game, provider, f"{faction.name}: {content}", settings
                    )


async def _append_world_memory(
    game: Game,
    provider: Provider,
    content: str,
    settings: Settings,
) -> bool:
    lines = [line for line in game.rolling_summary.splitlines() if line]
    new_lines = [*lines, content]
    if len(new_lines) > settings.memory_entry_cap:
        summary = await _try_resummarize(provider, new_lines)
        if summary is None:
            return False
        new_lines = summary
    game.rolling_summary = "\n".join(new_lines)
    return True


async def _append_world_digest(
    game: Game,
    provider: Provider,
    content: str,
    settings: Settings,
) -> bool:
    return await _append_world_memory(
        game, provider, content[: settings.memory_entry_char_cap], settings
    )


async def _append_entity_memory(
    entity: Any,
    provider: Provider,
    content: str,
    settings: Settings,
) -> bool:
    current = list(entity.memory_log or [])
    new_log = [*current, content]
    if len(new_log) > settings.memory_entry_cap:
        summary = await _try_resummarize(provider, new_log)
        if summary is None:
            return False
        new_log = summary
    entity.memory_log = new_log
    return True


async def _try_resummarize(provider: Provider, entries: list[str]) -> list[str] | None:
    try:
        summary = await provider.generate_structured(
            [
                ChatMessage(
                    role="system",
                    content="将同一记忆 store 压缩成少量高信息量条目，只返回 JSON object。",
                ),
                ChatMessage(role="user", content="\n".join(entries)),
            ],
            MemorySummary,
            LlmPurpose.RESUMMARIZE,
        )
    except Exception:
        return None
    return summary.entries


def _split_scope(scope: str) -> tuple[str, str | None]:
    if scope == "world":
        return "world", None
    scope_type, key = scope.split(":", maxsplit=1)
    return scope_type, key
