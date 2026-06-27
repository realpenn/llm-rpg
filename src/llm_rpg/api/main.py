from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import async_sessionmaker

from llm_rpg.config import Settings, get_settings
from llm_rpg.db import async_session_factory
from llm_rpg.logging import configure_logging
from llm_rpg.redis import RedisUpdateQueue, UpdateQueue
from llm_rpg.schemas.enums import UpdateStatus
from llm_rpg.worker.lifecycle import record_telegram_update


def create_app(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker = async_session_factory,
    queue: UpdateQueue | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    queue = queue or RedisUpdateQueue()
    configure_logging(settings.log_level)

    app = FastAPI(title="llm-rpg", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/telegram/webhook")
    async def telegram_webhook(
        request: Request,
        secret_token: Annotated[
            str | None,
            Header(alias="X-Telegram-Bot-Api-Secret-Token"),
        ] = None,
    ) -> dict[str, str | int | bool]:
        if settings.telegram_webhook_secret and secret_token != settings.telegram_webhook_secret:
            raise HTTPException(status_code=403, detail="invalid Telegram webhook secret")
        raw_update = await request.json()
        try:
            async with session_factory() as session:
                async with session.begin():
                    update, created = await record_telegram_update(session, raw_update)
                    update_id = update.update_id
                    should_enqueue = update.status == UpdateStatus.PENDING
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if should_enqueue:
            await queue.enqueue_update(update_id)
        return {"ok": "true", "update_id": update_id, "created": created}

    return app


app = create_app()
