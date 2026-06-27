import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from llm_rpg.api.main import create_app
from llm_rpg.config import Settings
from llm_rpg.llm import FakeProvider, StructuredOutputError
from llm_rpg.models import Game, Player, TelegramUpdate
from llm_rpg.schemas.enums import DropReason, UpdateStatus
from llm_rpg.worker import WorkerProcessor, record_telegram_update


class TypingTelegram:
    def __init__(self, typing_seen: asyncio.Event) -> None:
        self.typing_seen = typing_seen
        self.chat_actions: list[tuple[int, str]] = []
        self.messages: list[dict[str, Any]] = []
        self._next_message_id = 1

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        self.chat_actions.append((chat_id, action))
        self.typing_seen.set()

    async def send_reply_payload(self, reply_payload: list[dict[str, Any]]) -> list[int]:
        message_ids: list[int] = []
        for payload in reply_payload:
            message_id = self._next_message_id
            self._next_message_id += 1
            self.messages.append({**payload, "message_id": message_id})
            message_ids.append(message_id)
        return message_ids


class TypingAwareProvider:
    def __init__(self, typing_seen: asyncio.Event, response: dict[str, Any]) -> None:
        self.typing_seen = typing_seen
        self.response = response

    async def generate_structured(
        self,
        messages,
        schema,
        purpose,
        *,
        game_id: str | None = None,
        turn_id: str | None = None,
    ):
        del messages, purpose, game_id, turn_id
        await asyncio.wait_for(self.typing_seen.wait(), timeout=0.5)
        return schema.model_validate(self.response)


@pytest.mark.asyncio
async def test_webhook_records_update_idempotently_and_worker_runs_new_game(
    session_factory,
    fake_telegram,
    in_memory_queue,
) -> None:
    app = create_app(
        settings=Settings(telegram_webhook_secret="secret"),
        session_factory=session_factory,
        queue=in_memory_queue,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            json=_message_update(1001, "/new 旧城雨夜"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        )
        duplicate = await client.post(
            "/telegram/webhook",
            json=_message_update(1001, "/new 旧城雨夜"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        )

    assert response.status_code == 200
    assert duplicate.status_code == 200
    assert in_memory_queue.update_ids == [1001]

    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(responses=[_world_build_output()]),
        sender=fake_telegram,
        settings=Settings(),
    )
    claimed_id = await worker.process_update_id(1001)

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 1001)
        game = await session.scalar(select(Game))

    assert claimed_id == 1001
    assert update is not None
    assert update.status == UpdateStatus.COMPLETED
    assert update.telegram_message_ids == [1]
    assert game is not None
    assert game.turn_number == 0
    assert fake_telegram.messages[0]["text"] == "你在旧城档案馆门前醒来。"
    keyboard = fake_telegram.messages[0]["reply_markup"]["inline_keyboard"]
    assert len(keyboard) == 3


@pytest.mark.asyncio
async def test_worker_drops_higher_pending_turns_for_same_user(
    session_factory,
    fake_telegram,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await record_telegram_update(session, _message_update(2001, "/new 旧城雨夜"))
            await record_telegram_update(session, _message_update(2002, "我立刻冲进去"))

    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(responses=[_world_build_output()]),
        sender=fake_telegram,
        settings=Settings(),
    )
    await worker.process_update_id(2001)

    async with session_factory() as session:
        first = await session.get(TelegramUpdate, 2001)
        second = await session.get(TelegramUpdate, 2002)

    assert first is not None
    assert first.status == UpdateStatus.COMPLETED
    assert second is not None
    assert second.status == UpdateStatus.DROPPED
    assert second.drop_reason == DropReason.IN_FLIGHT
    assert second.blocked_by_update_id == 2001
    assert second.reply_payload[0]["text"] == "你还在处理上一段行动，稍等片刻。"


@pytest.mark.asyncio
async def test_worker_marks_update_failed_when_provider_fails_without_writing_game(
    session_factory,
    fake_telegram,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await record_telegram_update(session, _message_update(2101, "/new 旧城雨夜"))

    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=FakeProvider(responses=[StructuredOutputError("bad model output")]),
        sender=fake_telegram,
        settings=Settings(),
    )
    claimed_id = await worker.process_update_id(2101)

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 2101)
        game = await session.scalar(select(Game))

    assert claimed_id == 2101
    assert update is not None
    assert update.status == UpdateStatus.FAILED
    assert update.error_text == "bad model output"
    assert update.telegram_message_ids == [1]
    assert game is None
    assert fake_telegram.messages[0]["text"] == "这次行动暂时处理失败，可以稍后再试。"


@pytest.mark.asyncio
async def test_worker_sends_typing_action_while_waiting_for_worldbuild(
    session_factory,
) -> None:
    typing_seen = asyncio.Event()
    sender = TypingTelegram(typing_seen)
    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=TypingAwareProvider(typing_seen, _world_build_output()),
        sender=sender,
        settings=Settings(),
    )
    async with session_factory() as session:
        async with session.begin():
            await record_telegram_update(session, _message_update(2201, "/new 旧城雨夜"))

    await asyncio.wait_for(worker.process_update_id(2201), timeout=1)

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 2201)

    assert sender.chat_actions == [(420, "typing")]
    assert sender.messages[0]["text"] == "你在旧城档案馆门前醒来。"
    assert update is not None
    assert update.status == UpdateStatus.COMPLETED


@pytest.mark.asyncio
async def test_webhook_rejects_bad_secret(session_factory, in_memory_queue) -> None:
    app = create_app(
        settings=Settings(telegram_webhook_secret="secret"),
        session_factory=session_factory,
        queue=in_memory_queue,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            json=_message_update(3001, "/help"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_new_game_to_three_turns_with_fake_provider(
    session_factory,
    fake_telegram,
) -> None:
    provider = FakeProvider(
        responses=[
            _world_build_output(),
            _turn_output(1),
            _turn_output(2),
            _turn_output(3),
        ]
    )
    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=provider,
        sender=fake_telegram,
        settings=Settings(),
    )

    for update_id, text in [
        (4001, "/new 旧城雨夜"),
        (4002, "我询问闻鹤"),
        (4003, "我检查钟楼"),
        (4004, "我跟踪巡夜人"),
    ]:
        async with session_factory() as session:
            async with session.begin():
                await record_telegram_update(session, _message_update(update_id, text))
        await worker.process_update_id(update_id)

    async with session_factory() as session:
        game = await session.scalar(select(Game))
        player = await session.scalar(select(Player).where(Player.telegram_user_id == 42))
        updates = (
            await session.scalars(select(TelegramUpdate).order_by(TelegramUpdate.update_id))
        ).all()

    assert game is not None
    assert player is not None
    assert game.turn_number == 3
    assert player.remaining_turns == 7
    assert game.player_state["vitals"]["hp"] == 5
    assert [update.status for update in updates] == [UpdateStatus.COMPLETED] * 4
    assert len(fake_telegram.messages) == 4
    assert fake_telegram.messages[-1]["text"] == "第 3 回合叙述"


@pytest.mark.asyncio
async def test_valid_callback_is_acknowledged_before_turn_reply(
    session_factory,
    fake_telegram,
) -> None:
    provider = FakeProvider(responses=[_world_build_output(), _turn_output(1)])
    worker = WorkerProcessor(
        session_factory=session_factory,
        provider=provider,
        sender=fake_telegram,
        settings=Settings(),
    )

    async with session_factory() as session:
        async with session.begin():
            await record_telegram_update(session, _message_update(5001, "/new 旧城雨夜"))
    await worker.process_update_id(5001)
    callback_id = fake_telegram.messages[0]["reply_markup"]["inline_keyboard"][0][0][
        "callback_data"
    ]

    async with session_factory() as session:
        async with session.begin():
            await record_telegram_update(session, _callback_update(5002, callback_id))
    await worker.process_update_id(5002)

    async with session_factory() as session:
        update = await session.get(TelegramUpdate, 5002)

    assert fake_telegram.messages[1]["method"] == "answerCallbackQuery"
    assert fake_telegram.messages[1]["text"] == "处理中..."
    assert fake_telegram.messages[2]["method"] == "sendMessage"
    assert fake_telegram.messages[2]["text"] == "第 1 回合叙述"
    assert update is not None
    assert [item["method"] for item in update.reply_payload] == ["sendMessage"]
    assert len(update.telegram_message_ids) == 1


def _message_update(update_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 10,
            "from": {"id": 42, "is_bot": False, "first_name": "玩家"},
            "chat": {"id": 420, "type": "private"},
            "text": text,
        },
    }


def _callback_update(update_id: int, callback_id: str) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "from": {"id": 42, "is_bot": False, "first_name": "玩家"},
            "message": {"message_id": update_id + 10, "chat": {"id": 420, "type": "private"}},
            "data": callback_id,
        },
    }


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


def _turn_output(index: int) -> dict:
    return {
        "narration": f"第 {index} 回合叙述",
        "state_delta": [{"path": "vitals.hp", "op": "add", "value": -1}],
        "npc_updates": [
            {
                "key": "archivist",
                "revealed_to_player": True,
                "memory": f"第 {index} 回合记忆",
            }
        ],
        "relationship_updates": [
            {
                "source_key": "player",
                "target_key": "archivist",
                "edge_type": "player_npc",
                "trust_delta": 1,
            }
        ],
        "events": [
            {
                "summary": f"第 {index} 回合事件",
                "location": "old_city",
                "involved_entities": ["player", "archivist"],
            }
        ],
        "suggested_actions": [
            {"label": "行动一", "action": "行动一"},
            {"label": "行动二", "action": "行动二"},
            {"label": "行动三", "action": "行动三"},
        ],
        "memory_update": [{"scope": "world", "content": f"第 {index} 回合摘要"}],
        "time_advance": "minutes",
    }
