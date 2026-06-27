import asyncio
import logging

from redis.asyncio import Redis

from llm_rpg.config import get_settings
from llm_rpg.db import async_session_factory
from llm_rpg.llm.provider import create_provider
from llm_rpg.logging import configure_logging
from llm_rpg.redis import UPDATE_QUEUE_KEY
from llm_rpg.telegram.sender import HttpTelegramSender
from llm_rpg.worker.polling import run_polling_forever
from llm_rpg.worker.processor import WorkerProcessor


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    provider = create_provider()
    sender = HttpTelegramSender(settings)
    processor = WorkerProcessor(
        session_factory=async_session_factory,
        provider=provider,
        sender=sender,
        settings=settings,
    )
    logger.info("worker_started", extra={"app_env": settings.app_env})
    try:
        if settings.telegram_mode == "polling":
            await run_polling_forever(
                session_factory=async_session_factory,
                processor=processor,
                settings=settings,
            )
            return
        while True:
            item = await redis.blpop(UPDATE_QUEUE_KEY, timeout=5)
            if item is None:
                await asyncio.sleep(0)
                continue
            _, update_id = item
            await processor.process_update_id(int(update_id))
    finally:
        await provider.close()
        await sender.close()
        await redis.aclose()


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("worker_stopped")


if __name__ == "__main__":
    main()
