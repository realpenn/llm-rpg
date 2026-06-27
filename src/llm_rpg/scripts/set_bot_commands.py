import asyncio

import httpx

from llm_rpg.config import get_settings

COMMANDS = [
    {"command": "start", "description": "开始使用"},
    {"command": "new", "description": "创建新游戏"},
    {"command": "worlds", "description": "查看世界预设"},
    {"command": "world", "description": "查看当前世界"},
    {"command": "people", "description": "查看已知人物"},
    {"command": "status", "description": "查看状态"},
    {"command": "inventory", "description": "查看背包"},
    {"command": "reset", "description": "归档当前游戏"},
    {"command": "archive", "description": "查看归档"},
    {"command": "help", "description": "查看帮助"},
]


async def run() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("缺少 TELEGRAM_BOT_TOKEN")
    url = _bot_method_url(
        settings.telegram_api_base_url, settings.telegram_bot_token, "setMyCommands"
    )
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(url, json={"commands": COMMANDS})
        response.raise_for_status()
        print(response.text)


def main() -> None:
    asyncio.run(run())


def _bot_method_url(base_url: str, token: str, method: str) -> str:
    return f"{base_url.rstrip('/')}/bot{token}/{method}"


if __name__ == "__main__":
    main()
