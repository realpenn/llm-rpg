import asyncio

import httpx

from llm_rpg.config import get_settings


async def run() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("缺少 TELEGRAM_BOT_TOKEN")
    if not settings.telegram_webhook_url:
        raise SystemExit("缺少 TELEGRAM_WEBHOOK_URL")
    url = _bot_method_url(settings.telegram_api_base_url, settings.telegram_bot_token, "setWebhook")
    payload = {
        "url": settings.telegram_webhook_url,
        "drop_pending_updates": False,
    }
    if settings.telegram_webhook_secret:
        payload["secret_token"] = settings.telegram_webhook_secret
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        print(response.text)


def main() -> None:
    asyncio.run(run())


def _bot_method_url(base_url: str, token: str, method: str) -> str:
    return f"{base_url.rstrip('/')}/bot{token}/{method}"


if __name__ == "__main__":
    main()
