from typing import Any, Protocol

import httpx

from llm_rpg.config import Settings, get_settings


class ReplySender(Protocol):
    async def send_reply_payload(self, reply_payload: list[dict[str, Any]]) -> list[int]:
        pass


class HttpTelegramSender:
    def __init__(
        self, settings: Settings | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client or httpx.AsyncClient(trust_env=False)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_reply_payload(self, reply_payload: list[dict[str, Any]]) -> list[int]:
        message_ids: list[int] = []
        for item in reply_payload:
            method = item["method"]
            payload = {key: value for key, value in item.items() if key != "method"}
            response = await self._client.post(
                telegram_method_url(self.settings, method), json=payload
            )
            response.raise_for_status()
            data = response.json()
            result = data.get("result") or {}
            if isinstance(result, dict) and "message_id" in result:
                message_ids.append(result["message_id"])
            else:
                message_ids.append(0)
        return message_ids

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        response = await self._client.post(
            telegram_method_url(self.settings, "sendChatAction"),
            json={"chat_id": chat_id, "action": action},
        )
        response.raise_for_status()


def message_payload(
    *,
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return payload


def answer_callback_payload(
    *,
    callback_query_id: str,
    text: str | None = None,
    show_alert: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "method": "answerCallbackQuery",
        "callback_query_id": callback_query_id,
        "show_alert": show_alert,
    }
    if text:
        payload["text"] = text
    return payload


def telegram_method_url(settings: Settings, method: str) -> str:
    return f"{settings.telegram_api_base_url.rstrip('/')}/bot{settings.telegram_bot_token}/{method}"
