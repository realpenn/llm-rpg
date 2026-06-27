from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_rpg.config import Settings, get_settings
from llm_rpg.game.context import assemble_turn_context
from llm_rpg.game.memory import apply_memory_updates
from llm_rpg.game.moderation import ModerationService
from llm_rpg.game.reducers import apply_state_delta
from llm_rpg.llm.base import Provider
from llm_rpg.llm.prompts import turn_messages
from llm_rpg.models import Event, Game, Npc, Relationship, SuggestedAction, Turn
from llm_rpg.schemas.enums import LlmPurpose
from llm_rpg.schemas.moderation import SafetyFlagRecord
from llm_rpg.schemas.player import PlayerState
from llm_rpg.schemas.turn import RelationshipUpdate, TurnOutput
from llm_rpg.schemas.world import WorldBible


@dataclass(slots=True)
class TurnPersistResult:
    output: TurnOutput
    turn: Turn
    suggested_actions: list[SuggestedAction]
    delta_audit: dict[str, list[dict[str, Any]]]
    safety_flags: list[SafetyFlagRecord]


async def process_player_turn(
    session: AsyncSession,
    provider: Provider,
    game: Game,
    player_action: str,
    *,
    settings: Settings | None = None,
    moderation_service: ModerationService | None = None,
    initial_safety_flags: list[SafetyFlagRecord] | None = None,
) -> TurnPersistResult:
    settings = settings or get_settings()
    context = await assemble_turn_context(session, game, player_action, settings=settings)
    output = await provider.generate_structured(
        turn_messages(context.sections, player_action),
        TurnOutput,
        LlmPurpose.TURN,
        game_id=game.id,
    )
    safety_flags = list(initial_safety_flags or [])
    if moderation_service is not None:
        moderated = await moderation_service.moderate_output(output)
        safety_flags.extend(moderated.safety_flags)
        if moderated.refused:
            game.turn_number += 1
            turn = Turn(
                game_id=game.id,
                sequence=game.turn_number,
                player_input=player_action,
                narration=moderated.refusal_narration or "你决定暂时收手。",
                delta_audit={"accepted": [], "adjusted": [], "dropped": []},
                safety_flags=[flag.model_dump(mode="json") for flag in safety_flags],
                game_clock={"turn_number": game.turn_number, "time_of_day": game.time_of_day},
            )
            session.add(turn)
            await session.flush()
            return TurnPersistResult(
                output=output,
                turn=turn,
                suggested_actions=[],
                delta_audit=turn.delta_audit,
                safety_flags=safety_flags,
            )
        if moderated.output is not None:
            output = moderated.output

    world = WorldBible.model_validate(game.world_bible)
    player_state = PlayerState.model_validate(game.player_state)
    reducer_result = apply_state_delta(player_state, world, output.state_delta)
    game.player_state = reducer_result.player_state.model_dump(mode="json")
    game.turn_number += 1
    if output.time_advance is not None:
        game.time_of_day = output.time_advance.value

    turn = Turn(
        game_id=game.id,
        sequence=game.turn_number,
        player_input=player_action,
        narration=output.narration,
        delta_audit=reducer_result.delta_audit,
        safety_flags=[flag.model_dump(mode="json") for flag in safety_flags],
        game_clock={"turn_number": game.turn_number, "time_of_day": game.time_of_day},
    )
    session.add(turn)
    await session.flush()

    await _apply_npc_updates(session, game.id, output)
    await _apply_relationship_updates(session, game.id, output.relationship_updates)
    _persist_events(session, game.id, turn.id, output)
    suggested_actions = _persist_suggested_actions(session, game.id, turn.id, output)
    await apply_memory_updates(session, game, provider, output.memory_update, settings=settings)
    await session.flush()

    return TurnPersistResult(
        output=output,
        turn=turn,
        suggested_actions=suggested_actions,
        delta_audit=reducer_result.delta_audit,
        safety_flags=safety_flags,
    )


async def _apply_npc_updates(session: AsyncSession, game_id: str, output: TurnOutput) -> None:
    for update in output.npc_updates:
        npc = await session.scalar(select(Npc).where(Npc.game_id == game_id, Npc.key == update.key))
        if npc is None:
            npc = Npc(
                game_id=game_id,
                key=update.key,
                name=update.name or update.key,
                title=update.title or "",
                role=update.role or "unknown",
                faction=update.faction,
                location=update.location or "unknown",
                personality=update.personality or "unknown",
                desire=update.desire or "unknown",
                fear=update.fear or "unknown",
                secret=update.secret or "",
                goal=update.goal or "unknown",
                status=update.status or "active",
                revealed_to_player=bool(update.revealed_to_player),
                memory_log=[],
            )
            session.add(npc)
        for field in (
            "name",
            "title",
            "role",
            "faction",
            "location",
            "personality",
            "desire",
            "fear",
            "secret",
            "goal",
            "status",
        ):
            value = getattr(update, field)
            if value is not None:
                setattr(npc, field, value)
        if update.revealed_to_player is not None:
            npc.revealed_to_player = update.revealed_to_player
        if update.memory:
            npc.memory_log = [*(npc.memory_log or []), update.memory]


async def _apply_relationship_updates(
    session: AsyncSession,
    game_id: str,
    updates: list[RelationshipUpdate],
) -> None:
    for update in updates:
        relationship = await session.scalar(
            select(Relationship).where(
                Relationship.game_id == game_id,
                Relationship.source_key == update.source_key,
                Relationship.target_key == update.target_key,
                Relationship.edge_type == update.edge_type,
            )
        )
        if relationship is None:
            relationship = Relationship(
                game_id=game_id,
                source_key=update.source_key,
                target_key=update.target_key,
                edge_type=update.edge_type,
                standing=0,
                trust=0,
                memory_log=[],
            )
            session.add(relationship)
        if update.standing_delta is not None:
            relationship.standing = _clamp_relationship(
                relationship.standing + update.standing_delta
            )
        if update.trust_delta is not None:
            relationship.trust = _clamp_relationship(relationship.trust + update.trust_delta)
        if update.note:
            relationship.memory_log = [*(relationship.memory_log or []), update.note]


def _persist_events(session: AsyncSession, game_id: str, turn_id: str, output: TurnOutput) -> None:
    for event in output.events:
        session.add(
            Event(
                game_id=game_id,
                turn_id=turn_id,
                summary=event.summary,
                location=event.location,
                involved_entities=event.involved_entities,
            )
        )


def _persist_suggested_actions(
    session: AsyncSession,
    game_id: str,
    turn_id: str,
    output: TurnOutput,
) -> list[SuggestedAction]:
    actions = [
        SuggestedAction(
            game_id=game_id,
            turn_id=turn_id,
            callback_id=f"act_{index}_{turn_id[:8]}",
            label=action.label,
            action=action.action,
        )
        for index, action in enumerate(output.suggested_actions, start=1)
    ]
    session.add_all(actions)
    return actions


def _clamp_relationship(value: int) -> int:
    return min(max(value, -100), 100)
