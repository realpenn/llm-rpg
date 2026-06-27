from typing import Protocol

from redis.asyncio import Redis

from llm_rpg.config import get_settings

UPDATE_QUEUE_KEY = "llm_rpg:updates"


class UpdateQueue(Protocol):
    async def enqueue_update(self, update_id: int) -> None:
        pass


class RedisUpdateQueue:
    def __init__(self, redis: Redis | None = None) -> None:
        settings = get_settings()
        self._redis = redis or Redis.from_url(settings.redis_url, decode_responses=True)

    async def enqueue_update(self, update_id: int) -> None:
        await self._redis.rpush(UPDATE_QUEUE_KEY, str(update_id))


class NullUpdateQueue:
    async def enqueue_update(self, update_id: int) -> None:
        return None
