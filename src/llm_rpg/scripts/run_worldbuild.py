import argparse
import asyncio
import json

from sqlalchemy.exc import SQLAlchemyError

from llm_rpg.db import async_session_factory
from llm_rpg.game.worldbuilder import (
    ActiveGameExistsError,
    build_and_persist_world,
    get_or_create_player,
)
from llm_rpg.llm.provider import create_provider


async def run(seed: str, telegram_user_id: int) -> None:
    provider = create_provider()
    try:
        async with async_session_factory() as session:
            async with session.begin():
                player = await get_or_create_player(
                    session,
                    telegram_user_id=telegram_user_id,
                    username="manual-worldbuild",
                    display_name="Manual Worldbuild",
                )
                result = await build_and_persist_world(session, provider, player, seed)
        print(
            json.dumps(
                {
                    "game_id": result.game.id,
                    "opening_narration": result.output.opening_narration,
                    "suggested_actions": [action.action for action in result.suggested_actions],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await provider.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one world-build vertical slice.")
    parser.add_argument("seed", help="玩家自定义世界种子")
    parser.add_argument("--telegram-user-id", type=int, default=0)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.seed, args.telegram_user_id))
    except ActiveGameExistsError as exc:
        raise SystemExit(f"已有 active game；请先归档或换 --telegram-user-id。{exc}") from exc
    except SQLAlchemyError as exc:
        raise SystemExit(f"数据库操作失败: {exc}") from exc


if __name__ == "__main__":
    main()
