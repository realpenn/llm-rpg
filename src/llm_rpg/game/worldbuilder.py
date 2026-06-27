from dataclasses import dataclass
from secrets import token_urlsafe

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_rpg.llm.base import Provider
from llm_rpg.llm.prompts import world_build_messages
from llm_rpg.models import Faction, Game, Npc, Player, SuggestedAction, Turn
from llm_rpg.schemas.enums import LlmPurpose
from llm_rpg.schemas.world import WorldBuildOutput


class ActiveGameExistsError(RuntimeError):
    pass


@dataclass(slots=True)
class WorldBuildPersistResult:
    output: WorldBuildOutput
    game: Game
    opening_turn: Turn
    suggested_actions: list[SuggestedAction]


async def build_and_persist_world(
    session: AsyncSession,
    provider: Provider,
    player: Player,
    seed: str,
) -> WorldBuildPersistResult:
    existing = await session.scalar(
        select(Game).where(Game.player_id == player.id, Game.archived_at.is_(None))
    )
    if existing is not None:
        raise ActiveGameExistsError("player already has an active game")

    output = await provider.generate_structured(
        world_build_messages(seed),
        WorldBuildOutput,
        LlmPurpose.WORLD_BUILD,
    )
    return await persist_world_build_output(session, player, output)


async def persist_world_build_output(
    session: AsyncSession,
    player: Player,
    output: WorldBuildOutput,
) -> WorldBuildPersistResult:
    world = output.world
    player_state = output.player_state.model_copy(
        update={
            "condition_cap": world.player_stat_schema.condition_cap,
            "flag_cap": world.player_stat_schema.flag_cap,
            "inventory_cap": world.player_stat_schema.inventory_cap,
        }
    )
    game = Game(
        player=player,
        world_bible=world.model_dump(mode="json"),
        player_state=player_state.model_dump(mode="json"),
        rolling_summary=output.opening_narration,
        turn_number=0,
    )
    session.add(game)
    await session.flush()

    session.add_all(
        [
            Faction(
                game_id=game.id,
                key=faction.key,
                name=faction.name,
                description=faction.description,
                ideology=faction.ideology,
                memory_log=[],
            )
            for faction in world.factions
        ]
    )
    session.add_all(
        [
            Npc(
                game_id=game.id,
                key=npc.key,
                name=npc.name,
                title=npc.title,
                role=npc.role,
                faction=npc.faction,
                location=npc.location,
                personality=npc.personality,
                desire=npc.desire,
                fear=npc.fear,
                secret=npc.secret,
                goal=npc.goal,
                status=npc.status,
                revealed_to_player=npc.revealed_to_player,
                memory_log=[],
            )
            for npc in world.initial_npcs
        ]
    )

    opening_turn = Turn(
        game_id=game.id,
        sequence=0,
        player_input=None,
        narration=output.opening_narration,
        delta_audit={"accepted": [], "adjusted": [], "dropped": []},
        safety_flags=[],
        game_clock={"turn_number": 0, "time_of_day": None},
    )
    session.add(opening_turn)
    await session.flush()

    suggested_actions = [
        SuggestedAction(
            game_id=game.id,
            turn_id=opening_turn.id,
            callback_id=_callback_id(),
            label=action,
            action=action,
        )
        for action in output.initial_suggested_actions
    ]
    session.add_all(suggested_actions)
    await session.flush()

    return WorldBuildPersistResult(
        output=output,
        game=game,
        opening_turn=opening_turn,
        suggested_actions=suggested_actions,
    )


async def get_or_create_player(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    username: str | None = None,
    display_name: str | None = None,
) -> Player:
    player = await session.scalar(select(Player).where(Player.telegram_user_id == telegram_user_id))
    if player is not None:
        return player
    player = Player(
        telegram_user_id=telegram_user_id,
        username=username,
        display_name=display_name,
    )
    session.add(player)
    await session.flush()
    return player


def _callback_id() -> str:
    return f"act_{token_urlsafe(12)}"
