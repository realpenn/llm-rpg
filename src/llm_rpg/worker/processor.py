import asyncio
import logging
from collections.abc import Awaitable
from contextlib import suppress
from dataclasses import dataclass
from secrets import token_urlsafe
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_rpg.config import Settings, get_settings
from llm_rpg.game.maintenance import archive_game
from llm_rpg.game.moderation import ModerationService
from llm_rpg.game.turn import process_player_turn
from llm_rpg.game.worldbuilder import (
    ActiveGameExistsError,
    build_and_persist_world,
    get_or_create_player,
)
from llm_rpg.llm.base import Provider, ProviderError
from llm_rpg.models import Game, Npc, Player, SuggestedAction, TelegramUpdate, Turn
from llm_rpg.schemas.enums import DropReason, UpdateStatus
from llm_rpg.schemas.moderation import SafetyFlagRecord
from llm_rpg.telegram.sender import ReplySender, answer_callback_payload, message_payload
from llm_rpg.worker.lifecycle import (
    claim_next_update_for_processing,
    complete_update,
    fail_update,
    parse_recorded_update,
)

logger = logging.getLogger(__name__)
ModelT = TypeVar("ModelT")
TYPING_INTERVAL_SECONDS = 4.0


@dataclass(slots=True)
class HandledUpdate:
    reply_payload: list[dict[str, Any]]
    game_id: str | None = None
    turn_id: str | None = None
    safety_flags: list[SafetyFlagRecord] | None = None
    status: UpdateStatus = UpdateStatus.COMPLETED
    drop_reason: DropReason | None = None


class WorkerProcessor:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        provider: Provider,
        sender: ReplySender,
        settings: Settings | None = None,
        moderation_service: ModerationService | None = None,
        lease_owner: str = "worker",
    ) -> None:
        self.session_factory = session_factory
        self.provider = provider
        self.sender = sender
        self.settings = settings or get_settings()
        self.moderation_service = moderation_service
        self.lease_owner = lease_owner

    async def process_update_id(self, popped_update_id: int) -> int | None:
        lease_token = token_urlsafe(16)
        async with self.session_factory() as session:
            async with session.begin():
                update = await claim_next_update_for_processing(
                    session,
                    popped_update_id,
                    lease_owner=self.lease_owner,
                    lease_token=lease_token,
                    lease_ttl_seconds=self.settings.lease_ttl_seconds,
                )
                if update is None:
                    return None
                claimed_update_id = update.update_id

        try:
            async with self.session_factory() as session:
                async with session.begin():
                    update = await session.get(TelegramUpdate, claimed_update_id)
                    if update is None or update.status != UpdateStatus.PROCESSING:
                        return None
                    handled = await self._handle_update(update, session)
                    update.safety_flags = [
                        flag.model_dump(mode="json") for flag in (handled.safety_flags or [])
                    ]
                    if handled.status == UpdateStatus.DROPPED:
                        if (
                            update.lease_owner != self.lease_owner
                            or update.lease_token != lease_token
                        ):
                            return None
                        update.status = UpdateStatus.DROPPED
                        update.drop_reason = handled.drop_reason
                        update.reply_payload = handled.reply_payload
                        update.game_id = handled.game_id
                        update.turn_id = handled.turn_id
                    else:
                        completed = await complete_update(
                            session,
                            update,
                            reply_payload=handled.reply_payload,
                            game_id=handled.game_id,
                            turn_id=handled.turn_id,
                            lease_owner=self.lease_owner,
                            lease_token=lease_token,
                        )
                        if not completed:
                            return None
        except ProviderError as exc:
            handled = await self._fail_provider_error(claimed_update_id, lease_token, exc)
            if handled is None:
                return None

        message_ids = await self.sender.send_reply_payload(handled.reply_payload)
        async with self.session_factory() as session:
            async with session.begin():
                update = await session.get(TelegramUpdate, claimed_update_id)
                if update is not None:
                    update.telegram_message_ids = message_ids
        return claimed_update_id

    async def _fail_provider_error(
        self,
        claimed_update_id: int,
        lease_token: str,
        exc: ProviderError,
    ) -> HandledUpdate | None:
        async with self.session_factory() as session:
            async with session.begin():
                update = await session.get(TelegramUpdate, claimed_update_id)
                if update is None or update.status != UpdateStatus.PROCESSING:
                    return None
                parsed = parse_recorded_update(update)
                reply_payload = [
                    message_payload(
                        chat_id=parsed.telegram_chat_id,
                        text="这次行动暂时处理失败，可以稍后再试。",
                    )
                ]
                failed = await fail_update(
                    session,
                    update,
                    error_text=str(exc),
                    reply_payload=reply_payload,
                    lease_owner=self.lease_owner,
                    lease_token=lease_token,
                )
                if not failed:
                    return None
                return HandledUpdate(reply_payload=reply_payload, status=UpdateStatus.FAILED)

    async def _handle_update(
        self,
        update: TelegramUpdate,
        session: AsyncSession,
    ) -> HandledUpdate:
        parsed = parse_recorded_update(update)
        if parsed.update_kind == "callback_query":
            return await self._handle_callback(update, session)
        if parsed.command in {"/start", "/help"}:
            return HandledUpdate(
                reply_payload=[message_payload(chat_id=parsed.telegram_chat_id, text=_help_text())]
            )
        if parsed.command == "/worlds":
            return HandledUpdate(
                reply_payload=[
                    message_payload(
                        chat_id=parsed.telegram_chat_id,
                        text="可选世界：默认悬疑旧城；也可以发送 /new <你的世界种子>。",
                    )
                ]
            )
        if parsed.command == "/new":
            seed = _seed_from_text(parsed.text)
            player = await get_or_create_player(session, telegram_user_id=parsed.telegram_user_id)
            try:
                result = await self._with_typing(
                    parsed.telegram_chat_id,
                    build_and_persist_world(session, self.provider, player, seed),
                )
            except ActiveGameExistsError:
                return HandledUpdate(
                    reply_payload=[
                        message_payload(
                            chat_id=parsed.telegram_chat_id, text="你已经有一局进行中的游戏。"
                        )
                    ],
                )
            return HandledUpdate(
                reply_payload=[
                    message_payload(
                        chat_id=parsed.telegram_chat_id,
                        text=result.output.opening_narration,
                        reply_markup={
                            "inline_keyboard": [
                                [{"text": action.label, "callback_data": action.callback_id}]
                                for action in result.suggested_actions
                            ]
                        },
                    )
                ],
                game_id=result.game.id,
                turn_id=result.opening_turn.id,
            )
        player = await get_or_create_player(session, telegram_user_id=parsed.telegram_user_id)
        game = await session.scalar(
            select(Game).where(Game.player_id == player.id, Game.archived_at.is_(None))
        )
        if parsed.command in {
            "/world",
            "/people",
            "/status",
            "/inventory",
            "/archive",
            "/admin_stats",
            "/reset",
        }:
            return await self._handle_command(parsed, session, player, game)
        if game is None:
            return HandledUpdate(
                reply_payload=[
                    message_payload(
                        chat_id=parsed.telegram_chat_id,
                        text="还没有进行中的游戏。发送 /new 开始。",
                    )
                ],
            )
        input_flags: list[SafetyFlagRecord] = []
        if self.moderation_service is not None:
            input_result = await self.moderation_service.moderate_input(parsed.text or "")
            input_flags = input_result.safety_flags
            if input_result.refused:
                return HandledUpdate(
                    reply_payload=[
                        message_payload(
                            chat_id=parsed.telegram_chat_id,
                            text=input_result.refusal_text or "这个行动暂时不能继续。",
                        )
                    ],
                    safety_flags=input_flags,
                )
        result = await self._with_typing(
            parsed.telegram_chat_id,
            process_player_turn(
                session,
                self.provider,
                game,
                parsed.text or "",
                moderation_service=self.moderation_service,
                initial_safety_flags=input_flags,
            ),
        )
        return HandledUpdate(
            reply_payload=[
                message_payload(
                    chat_id=parsed.telegram_chat_id,
                    text=result.turn.narration,
                    reply_markup=_actions_markup(result.suggested_actions),
                ),
            ],
            game_id=game.id,
            turn_id=result.turn.id,
            safety_flags=result.safety_flags,
        )

    async def _handle_command(
        self,
        parsed: Any,
        session: AsyncSession,
        player: Player,
        game: Game | None,
    ) -> HandledUpdate:
        if parsed.command == "/admin_stats":
            if parsed.telegram_user_id not in self.settings.admin_user_ids:
                return HandledUpdate(
                    reply_payload=[
                        message_payload(chat_id=parsed.telegram_chat_id, text="无权访问。")
                    ]
                )
            player_count = await session.scalar(select(func.count()).select_from(Player))
            game_count = await session.scalar(select(func.count()).select_from(Game))
            update_count = await session.scalar(select(func.count()).select_from(TelegramUpdate))
            return HandledUpdate(
                reply_payload=[
                    message_payload(
                        chat_id=parsed.telegram_chat_id,
                        text=f"players={player_count} games={game_count} updates={update_count}",
                    )
                ]
            )
        if parsed.command == "/archive":
            games = (
                await session.scalars(
                    select(Game)
                    .where(Game.player_id == player.id, Game.archived_at.is_not(None))
                    .order_by(Game.archived_at.desc())
                )
            ).all()
            if not games:
                text = "暂无归档游戏。"
            else:
                text = "\n".join(
                    f"- {item.world_bible.get('summary', '未命名世界')}" for item in games
                )
            return HandledUpdate(
                reply_payload=[message_payload(chat_id=parsed.telegram_chat_id, text=text)]
            )
        if game is None:
            return HandledUpdate(
                reply_payload=[
                    message_payload(
                        chat_id=parsed.telegram_chat_id, text="还没有进行中的游戏。发送 /new 开始。"
                    )
                ]
            )
        if parsed.command == "/reset":
            await archive_game(session, game)
            return HandledUpdate(
                reply_payload=[
                    message_payload(chat_id=parsed.telegram_chat_id, text="当前游戏已归档。")
                ]
            )
        if parsed.command == "/world":
            text = game.world_bible.get("summary", "当前世界暂无摘要。")
        elif parsed.command == "/people":
            npcs = (
                await session.scalars(
                    select(Npc).where(
                        Npc.game_id == game.id,
                        Npc.revealed_to_player.is_(True),
                    )
                )
            ).all()
            text = (
                "已知人物：\n" + "\n".join(f"- {npc.name}：{npc.role}" for npc in npcs)
                if npcs
                else "暂无已知人物。"
            )
        elif parsed.command == "/status":
            state = game.player_state
            text = f"位置：{state.get('location', '未知')}\n状态：{state.get('vitals', {})}"
        elif parsed.command == "/inventory":
            inventory = game.player_state.get("inventory") or {}
            text = (
                "物品：\n"
                + "\n".join(
                    f"- {item.get('name', key)} x{item.get('quantity', 1)}"
                    for key, item in inventory.items()
                )
                if inventory
                else "背包为空。"
            )
        else:
            text = _help_text()
        return HandledUpdate(
            reply_payload=[message_payload(chat_id=parsed.telegram_chat_id, text=text)]
        )

    async def _handle_callback(
        self, update: TelegramUpdate, session: AsyncSession
    ) -> HandledUpdate:
        parsed = parse_recorded_update(update)
        action = await session.scalar(
            select(SuggestedAction).where(SuggestedAction.callback_id == (parsed.text or ""))
        )
        if action is None:
            return _dropped_callback(
                parsed.telegram_chat_id,
                DropReason.STALE_CALLBACK,
                callback_query_id=parsed.callback_query_id,
            )
        game = await session.get(Game, action.game_id)
        if game is None or game.archived_at is not None:
            return _dropped_callback(
                parsed.telegram_chat_id,
                DropReason.ARCHIVED_GAME,
                callback_query_id=parsed.callback_query_id,
                game_id=action.game_id,
            )
        latest_turn = await session.scalar(
            select(Turn).where(Turn.game_id == game.id).order_by(Turn.sequence.desc()).limit(1)
        )
        if latest_turn is None or latest_turn.id != action.turn_id:
            return _dropped_callback(
                parsed.telegram_chat_id,
                DropReason.STALE_CALLBACK,
                callback_query_id=parsed.callback_query_id,
                game_id=game.id,
            )
        await self._answer_callback_now(parsed.callback_query_id)
        result = await self._with_typing(
            parsed.telegram_chat_id,
            process_player_turn(
                session,
                self.provider,
                game,
                action.action,
                moderation_service=self.moderation_service,
            ),
        )
        return HandledUpdate(
            reply_payload=[
                message_payload(
                    chat_id=parsed.telegram_chat_id,
                    text=result.turn.narration,
                    reply_markup=_actions_markup(result.suggested_actions),
                ),
            ],
            game_id=game.id,
            turn_id=result.turn.id,
            safety_flags=result.safety_flags,
        )

    async def _answer_callback_now(self, callback_query_id: str | None) -> None:
        if not callback_query_id:
            return
        try:
            await self.sender.send_reply_payload(
                [answer_callback_payload(callback_query_id=callback_query_id, text="处理中...")]
            )
        except Exception:
            logger.warning("callback_ack_failed", exc_info=True)

    async def _with_typing(self, chat_id: int, operation: Awaitable[ModelT]) -> ModelT:
        typing_task = asyncio.create_task(self._typing_loop(chat_id))
        try:
            return await operation
        finally:
            typing_task.cancel()
            with suppress(asyncio.CancelledError):
                await typing_task

    async def _typing_loop(self, chat_id: int) -> None:
        while True:
            await self._send_typing_action(chat_id)
            await asyncio.sleep(TYPING_INTERVAL_SECONDS)

    async def _send_typing_action(self, chat_id: int) -> None:
        send_chat_action = getattr(self.sender, "send_chat_action", None)
        if send_chat_action is None:
            return
        try:
            await send_chat_action(chat_id, "typing")
        except Exception as exc:
            logger.warning(
                "typing_action_failed",
                extra={"chat_id": chat_id, "error_type": type(exc).__name__},
            )


def _seed_from_text(text: str | None) -> str:
    if not text:
        return "一个适合中文文字 RPG 的悬疑世界。"
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip()
    return "一个适合中文文字 RPG 的悬疑世界。"


def _help_text() -> str:
    return "可用命令：/new /world /people /status /inventory /reset /archive /help"


def _actions_markup(actions: list[Any]) -> dict[str, Any] | None:
    if not actions:
        return None
    return {
        "inline_keyboard": [
            [{"text": action.label, "callback_data": action.callback_id}] for action in actions
        ]
    }


def _dropped_callback(
    chat_id: int,
    reason: DropReason,
    *,
    callback_query_id: str | None = None,
    game_id: str | None = None,
) -> HandledUpdate:
    text = "这个选项已经过期。" if reason == DropReason.STALE_CALLBACK else "这局游戏已归档。"
    payload = []
    if callback_query_id:
        payload.append(answer_callback_payload(callback_query_id=callback_query_id, text=text))
    payload.append(message_payload(chat_id=chat_id, text=text))
    return HandledUpdate(
        reply_payload=payload,
        game_id=game_id,
        status=UpdateStatus.DROPPED,
        drop_reason=reason,
    )
