import json
import logging

from llm_rpg.logging import JsonFormatter, redact_log_value, redact_secrets


def test_json_formatter_redacts_telegram_bot_token_in_httpx_message() -> None:
    token = "123456789:TEST_fake_token_for_redaction_only_123456"
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: POST https://api.telegram.org/bot%s/getUpdates "HTTP/1.1 200 OK"',
        args=(token,),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert token not in payload["message"]
    assert "https://api.telegram.org/bot[REDACTED]/getUpdates" in payload["message"]


def test_redact_secrets_handles_bearer_and_query_values() -> None:
    text = "Authorization: Bearer sk-test-secret?token=abc123&ok=1"

    redacted = redact_secrets(text)

    assert "sk-test-secret" not in redacted
    assert "abc123" not in redacted
    assert "Bearer [REDACTED]" in redacted
    assert "token=[REDACTED]" in redacted


def test_redact_log_value_redacts_sensitive_extra_fields() -> None:
    value = {
        "telegram_bot_token": "123456789:abcdefghijklmnopqrstuvwxyzABCDEFG",
        "nested": {"Authorization": "Bearer secret-key"},
        "safe": "visible",
    }

    redacted = redact_log_value("payload", value)

    assert redacted["telegram_bot_token"] == "[REDACTED]"
    assert redacted["nested"]["Authorization"] == "[REDACTED]"
    assert redacted["safe"] == "visible"
