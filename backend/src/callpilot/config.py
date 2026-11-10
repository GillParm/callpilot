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

    openai_model: str = Field(default="gpt-realtime-2.1", alias="OPENAI_REALTIME_MODEL")
    openai_voice: str = Field(default="marin", alias="OPENAI_VOICE")

    gemini_model: str = Field(default="gemini-3.8-live", alias="GEMINI_MODEL")
    gemini_text_model: str = Field(default="gemini-3.5-flash-lite", alias="GEMINI_TEXT_MODEL")
    gemini_voice: str = Field(default="Kore", alias="GEMINI_VOICE")

    # Who writes after-call summaries: "auto" tries anthropic, then gemini, then a
    # plain summary built from the call data.
    summary_provider: Literal["auto", "anthropic", "gemini", "plain"] = Field(
        default="auto", alias="SUMMARY_PROVIDER"
    )

    anthropic_model: str = Field(default="claude-opus-5-5", alias="ANTHROPIC_MODEL")
    # A receptionist turn is short and latency-sensitive, so default to low effort.
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = Field(
        default="low", alias="ANTHROPIC_EFFORT"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
