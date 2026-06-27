from dataclasses import dataclass

READ_ONLY_COMMANDS = {
    "/admin_stats",
    "/archive",
    "/help",
    "/inventory",
    "/people",
    "/start",
    "/status",
    "/world",
    "/worlds",
}


@dataclass(slots=True)
class ParsedTelegramUpdate:
    update_id: int
    telegram_user_id: int
    telegram_chat_id: int
    update_kind: str
    turn_producing: bool
    text: str | None
    command: str | None
    raw_update: dict
    callback_query_id: str | None = None


def parse_telegram_update(raw_update: dict) -> ParsedTelegramUpdate:
    if "message" in raw_update:
        message = raw_update["message"]
        text = message.get("text")
        command = _command(text)
        return ParsedTelegramUpdate(
            update_id=raw_update["update_id"],
            telegram_user_id=message["from"]["id"],
            telegram_chat_id=message["chat"]["id"],
            update_kind="message",
            turn_producing=_is_turn_producing_message(text, command),
            text=text,
            command=command,
            raw_update=raw_update,
        )
    if "callback_query" in raw_update:
        callback = raw_update["callback_query"]
        return ParsedTelegramUpdate(
            update_id=raw_update["update_id"],
            telegram_user_id=callback["from"]["id"],
            telegram_chat_id=callback["message"]["chat"]["id"],
            update_kind="callback_query",
            turn_producing=True,
            text=callback.get("data"),
            command=None,
            raw_update=raw_update,
            callback_query_id=callback["id"],
        )
    raise ValueError("unsupported Telegram update kind")


def _command(text: str | None) -> str | None:
    if not text or not text.startswith("/"):
        return None
    return text.split(maxsplit=1)[0].split("@", maxsplit=1)[0]


def _is_turn_producing_message(text: str | None, command: str | None) -> bool:
    if text is None:
        return False
    if command in READ_ONLY_COMMANDS:
        return False
    return True
