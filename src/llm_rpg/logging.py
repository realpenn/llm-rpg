import json
import logging
import re
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = redact_secrets(self.formatException(record.exc_info))
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _STANDARD_LOG_RECORD_KEYS:
                continue
            payload[key] = redact_log_value(key, value)
        return json.dumps(payload, ensure_ascii=False, default=str)


_STANDARD_LOG_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}

_SENSITIVE_KEY_NAMES = {
    "authorization",
    "api_key",
    "apikey",
    "api-key",
    "key",
    "secret",
    "token",
    "telegram_bot_token",
    "llm_api_key",
}
_TELEGRAM_BOT_URL_RE = re.compile(r"(/bot)([^/\s\"']+)(/)")
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_BEARER_RE = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|api_key|api-key|key|secret|authorization)=)([^&\s\"']+)"
)


def redact_log_value(key: str, value: Any) -> Any:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {
            item_key: redact_log_value(str(item_key), item_value)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_log_value("", item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_log_value("", item) for item in value)
    return value


def redact_secrets(text: str) -> str:
    redacted = _TELEGRAM_BOT_URL_RE.sub(r"\1[REDACTED]\3", text)
    redacted = _TELEGRAM_TOKEN_RE.sub("[REDACTED_TELEGRAM_TOKEN]", redacted)
    redacted = _BEARER_RE.sub(r"\1[REDACTED]", redacted)
    return _QUERY_SECRET_RE.sub(r"\1[REDACTED]", redacted)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in _SENSITIVE_KEY_NAMES or normalized.endswith(
        ("_token", "_secret", "_api_key")
    )


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
