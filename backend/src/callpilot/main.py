"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from callpilot import __version__
from callpilot.api.routes import router as api_router
from callpilot.calllog.summarizer import Summarizer, create_summarizer
from callpilot.config import Settings, get_settings
from callpilot.db import connect, init_db
from callpilot.db.seed import seed_all
from callpilot.providers.base import Mode, RealtimeProvider
from callpilot.providers.factory import create_provider
from callpilot.transports.browser import router as browser_router

ProviderFactory = Callable[[Settings, Mode], RealtimeProvider]


def create_app(
    settings: Settings | None = None,
    *,
    provider_factory: ProviderFactory = create_provider,
    summarizer: Summarizer | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        tz = ZoneInfo(settings.clinic_timezone)
        db = connect(settings.database_path)
        init_db(db)
        seed_all(db, now=clock(), tz=tz)
        app.state.db = db
        app.state.tz = tz
        try:
            yield
        finally:
            db.close()

    app = FastAPI(title="CallPilot", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.provider_factory = provider_factory
    app.state.summarizer = summarizer or create_summarizer(settings)
    app.state.clock = clock
    # Strong references to fire-and-forget work (summaries) so it isn't GC'd.
    app.state.background_tasks = set()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(api_router)
    app.include_router(browser_router)
    return app


app = create_app()
