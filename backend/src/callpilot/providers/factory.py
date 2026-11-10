"""Pick and build the provider for a call from settings."""

from __future__ import annotations

from callpilot.config import Settings
from callpilot.providers.base import Mode, ProviderUnavailableError, RealtimeProvider

VOICE_ORDER = ("openai", "gemini")
TEXT_ORDER = ("anthropic", "openai")


def _has_key(settings: Settings, provider: str) -> bool:
    key = getattr(settings, f"{provider}_api_key", None)
    return key is not None and bool(key.get_secret_value())


def resolve_provider_name(settings: Settings, mode: Mode) -> str | None:
    """The provider that will handle `mode`, or None if nothing is configured."""
    choice = settings.voice_provider if mode == "voice" else settings.text_provider
    if choice != "auto":
        return choice if _has_key(settings, choice) else None
    order = VOICE_ORDER if mode == "voice" else TEXT_ORDER
    return next((name for name in order if _has_key(settings, name)), None)


def create_provider(settings: Settings, mode: Mode) -> RealtimeProvider:
    name = resolve_provider_name(settings, mode)
    if name is None:
        raise ProviderUnavailableError(
            f"No {mode} provider is configured. Add an API key to .env (see .env.example)."
        )
    if name == "anthropic":
        from callpilot.providers.anthropic_text import AnthropicTextProvider

        assert settings.anthropic_api_key is not None
        return AnthropicTextProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.anthropic_model,
            effort=settings.anthropic_effort,
        )
    if name == "openai":
        from callpilot.providers.openai_realtime import OpenAIRealtimeProvider

        assert settings.openai_api_key is not None
        return OpenAIRealtimeProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            voice=settings.openai_voice,
        )
    if name == "gemini":
        from callpilot.providers.gemini_live import GeminiLiveProvider

        assert settings.gemini_api_key is not None
        return GeminiLiveProvider(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_model,
            voice=settings.gemini_voice,
        )
    raise ProviderUnavailableError(f"The {name} provider is not available yet.")
