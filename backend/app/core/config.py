"""Config OpenRouter (SPECS §5.6 v1.1). Fonte: env / .env (nunca commitar .env)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    OPENROUTER_API_KEY: str = "sk-or-v1-CHANGE_ME"
    OPENROUTER_MODEL: str = "google/gemma-4-26b-a4b-it:free"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_SITE_URL: str = "https://horadatarefa.app"
    OPENROUTER_APP_NAME: str = "Hora da Tarefa"
    OPENROUTER_TIMEOUT_SECONDS: int = 60
    OPENROUTER_MAX_TOKENS: int = 2048
    OPENROUTER_TEMPERATURE: float = 0.1

    AI_ENABLED: bool = True
    AI_CONFIDENCE_OK: float = 0.75
    AI_CONFIDENCE_REVIEW: float = 0.60
    AI_WORKER_CONCURRENCY: int = 3
    AI_MAX_IMAGE_SIDE: int = 1600
    AI_JPEG_QUALITY: int = 82

    TELEGRAM_BOT_TOKEN: str = "change-me"
    TELEGRAM_WEBHOOK_SECRET: str = "change-me"
    RUN_MODE: str = "polling"


def get_settings() -> Settings:
    return Settings()
