import httpx
import pytest

from llm_rpg.config import Settings
from llm_rpg.telegram.sender import (
    HttpTelegramSender,
    answer_callback_payload,
    message_payload,
)


@pytest.mark.asyncio
async def test_http_sender_handles_callback_query_boolean_result() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/answerCallbackQuery"):
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    settings = Settings(
        telegram_bot_token="123456789:test-token",
        telegram_api_base_url="https://telegram.test",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = HttpTelegramSender(settings=settings, client=client)
        message_ids = await sender.send_reply_payload(
            [
                answer_callback_payload(callback_query_id="callback-1"),
                message_payload(chat_id=42, text="继续行动"),
            ]
        )

    assert message_ids == [0, 77]
    assert requests == [
        "/bot123456789:test-token/answerCallbackQuery",
        "/bot123456789:test-token/sendMessage",
    ]


@pytest.mark.asyncio
async def test_http_sender_sends_chat_action() -> None:
    requests: list[tuple[str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, request.content))
        return httpx.Response(200, json={"ok": True, "result": True})

    settings = Settings(
        telegram_bot_token="123456789:test-token",
        telegram_api_base_url="https://telegram.test",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = HttpTelegramSender(settings=settings, client=client)
        await sender.send_chat_action(42)

    assert requests[0][0] == "/bot123456789:test-token/sendChatAction"
    assert b'"chat_id":42' in requests[0][1]
    assert b'"action":"typing"' in requests[0][1]
