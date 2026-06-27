from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "local"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://llm_rpg:llm_rpg@localhost:5432/llm_rpg"
    redis_url: str = "redis://localhost:6379/0"

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_structured_mode: Literal["strict", "json_object", "auto"] = "auto"
    llm_context_token_budget: int = 12000
    llm_timeout_seconds: float = 60.0
    llm_disable_thinking: bool = False

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_mode: Literal["webhook", "polling"] = "webhook"
    telegram_webhook_url: str = ""
    telegram_api_base_url: str = "https://api.telegram.org"
    telegram_polling_retry_initial_seconds: float = 1.0
    telegram_polling_retry_max_seconds: float = 30.0

    admin_user_ids: list[int] = Field(default_factory=list)
    lease_ttl_seconds: int = 120
    max_processing_retries: int = 2
    retry_backoff_seconds: int = 5
    rate_limit_turns_per_minute: int = 6
    suggested_actions_retain_turns: int = 5
    memory_entry_cap: int = 80
    memory_entry_char_cap: int = 500

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def parse_admin_user_ids(cls, value: object) -> list[int] | object:
        if value is None or value == "":
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
