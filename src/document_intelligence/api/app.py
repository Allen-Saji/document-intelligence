from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from document_intelligence import __version__
from document_intelligence.api.routes.health import router as health_router
from document_intelligence.api.routes.uploads import router as uploads_router
from document_intelligence.api.telemetry import install_request_telemetry
from document_intelligence.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(
        title="Document Intelligence API",
        description="Evidence-backed technical PDF investigation API.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    install_request_telemetry(app)
    app.include_router(health_router)
    app.include_router(uploads_router)
    return app
