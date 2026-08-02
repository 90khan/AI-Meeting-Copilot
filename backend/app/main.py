"""FastAPI application bootstrap."""

from asyncio import Event
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.live_transcription import router as live_transcription_router
from app.core.config import Settings, get_settings
from app.core.container import Container

APP_VERSION = "0.1.0"


def create_app(
    settings: Settings | None = None,
    *,
    readiness_event: Event | None = None,
) -> FastAPI:
    """Create a FastAPI application with an isolated composition container."""

    if settings is None:
        settings = get_settings()

    container = Container(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await container.start()
        if readiness_event is not None:
            readiness_event.set()
        try:
            yield
        finally:
            await container.stop()

    app = FastAPI(lifespan=lifespan, version=APP_VERSION)
    app.state.container = container
    app.include_router(health_router)
    app.include_router(live_transcription_router)
    return app
