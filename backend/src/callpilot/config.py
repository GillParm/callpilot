"""Runtime configuration, read from environment variables (and `.env` in development)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
