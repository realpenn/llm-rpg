import asyncio

import httpx

from llm_rpg.config import get_settings
from llm_rpg.telegram.sender import telegram_method_url


async def run() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("缺少 TELEGRAM_BOT_TOKEN")
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(
            telegram_method_url(settings, "deleteWebhook"),
            json={"drop_pending_updates": False},
        )
        response.raise_for_status()
        print(response.text)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
