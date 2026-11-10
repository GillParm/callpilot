"""Runtime configuration, read from environment variables (and `.env` in development)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development", alias="CALLPILOT_ENV")
    database_path: Path = Field(default=Path("data/callpilot.db"), alias="DATABASE_PATH")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"], alias="CORS_ORIGINS"
    )
    clinic_timezone: str = Field(default="America/Toronto", alias="CLINIC_TIMEZONE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Model providers. Keys are SecretStr so they never show up in logs or reprs.
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    # Which provider runs each mode. "auto" picks the first one with a key.
    voice_provider: Literal["auto", "openai", "gemini"] = Field(
        default="auto", alias="VOICE_PROVIDER"
    )
    text_provider: Literal["auto", "anthropic", "openai"] = Field(
        default="auto", alias="TEXT_PROVIDER"
    )

    anthropic_model: str = Field(default="claude-opus-5-5", alias="ANTHROPIC_MODEL")
    # A receptionist turn is short and latency-sensitive, so default to low effort.
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = Field(
        default="low", alias="ANTHROPIC_EFFORT"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
