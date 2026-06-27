import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_rpg.config import Settings, get_settings
from llm_rpg.models import Event, Game, Npc, Turn


@dataclass(slots=True)
class TurnContext:
    sections: list[str]
    char_count: int


async def assemble_turn_context(
    session: AsyncSession,
    game: Game,
    player_action: str,
    *,
    settings: Settings | None = None,
) -> TurnContext:
    settings = settings or get_settings()
    budget = settings.llm_context_token_budget
    player_state = game.player_state
    recent_turns_section = await _recent_turns_section(session, game.id)
    sections = [
        _world_essentials(game.world_bible),
        _player_state_section(player_state, player_action, game.turn_number, game.time_of_day),
        recent_turns_section,
        await _npc_section(
            session,
            game.id,
            player_state.get("location"),
            player_action,
            recent_narration=recent_turns_section,
        ),
        await _events_section(session, game.id, player_state.get("location")),
        _rolling_summary_section(game.rolling_summary),
    ]
    selected: list[str] = []
    used = 0
    for section in sections:
        if not section:
            continue
        remaining = budget - used
        if remaining <= 0:
            break
        if len(section) > remaining:
            section = section[:remaining]
        selected.append(section)
        used += len(section)
    return TurnContext(sections=selected, char_count=used)


def _world_essentials(world_bible: dict[str, Any]) -> str:
    essentials = {
        "summary": world_bible.get("summary"),
        "language": world_bible.get("language"),
        "tone": world_bible.get("tone"),
        "locked_laws": world_bible.get("locked_laws", []),
        "taboos": world_bible.get("taboos", []),
        "core_conflict": world_bible.get("core_conflict"),
    }
    return "WORLD\n" + json.dumps(essentials, ensure_ascii=False, sort_keys=True)


def _player_state_section(
    player_state: dict[str, Any],
    player_action: str,
    turn_number: int,
    time_of_day: str | None,
) -> str:
    payload = {
        "player_state": player_state,
        "player_action": player_action,
        "game_clock": {"turn_number": turn_number, "time_of_day": time_of_day},
    }
    return "PLAYER\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)


async def _recent_turns_section(session: AsyncSession, game_id: str) -> str:
    turns = (
        await session.scalars(
            select(Turn).where(Turn.game_id == game_id).order_by(Turn.sequence.desc()).limit(5)
        )
    ).all()
    if not turns:
        return ""
    lines = [
        f"#{turn.sequence} 玩家:{turn.player_input or '<opening>'}\n叙述:{turn.narration}"
        for turn in reversed(turns)
    ]
    return "RECENT_TURNS\n" + "\n".join(lines)


async def _npc_section(
    session: AsyncSession,
    game_id: str,
    location: str | None,
    player_action: str,
    recent_narration: str = "",
) -> str:
    npcs = (await session.scalars(select(Npc).where(Npc.game_id == game_id))).all()
    search_text = player_action + recent_narration
    recalled = [
        npc
        for npc in npcs
        if npc.location == location or npc.name in search_text or npc.key in search_text
    ]
    if not recalled:
        return ""
    payload = [
        {
            "key": npc.key,
            "name": npc.name,
            "role": npc.role,
            "faction": npc.faction,
            "location": npc.location,
            "goal": npc.goal,
            "status": npc.status,
            "revealed_to_player": npc.revealed_to_player,
            "memory": npc.memory_log[-3:],
        }
        for npc in recalled
    ]
    return "NPCS\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)


async def _events_section(session: AsyncSession, game_id: str, location: str | None) -> str:
    query = select(Event).where(Event.game_id == game_id)
    if location:
        query = query.where(Event.location == location)
    events = (await session.scalars(query.order_by(Event.created_at.desc()).limit(8))).all()
    if not events:
        return ""
    payload = [
        {
            "summary": event.summary,
            "location": event.location,
            "involved_entities": event.involved_entities,
        }
        for event in reversed(events)
    ]
    return "EVENTS\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _rolling_summary_section(rolling_summary: str) -> str:
    if not rolling_summary:
        return ""
    return f"ROLLING_SUMMARY\n{rolling_summary}"
