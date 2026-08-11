from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from document_intelligence import __version__
from document_intelligence.api.composition import configure_runtime_services
from document_intelligence.api.routes.answers import router as answers_router
from document_intelligence.api.routes.health import router as health_router
from document_intelligence.api.routes.uploads import router as uploads_router
from document_intelligence.api.telemetry import install_request_telemetry
from document_intelligence.config import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    *,
    configure_runtime: bool = True,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    runtime_services = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            services = getattr(app.state, "runtime_services", None)
            if services is not None:
                await services.close()

    app = FastAPI(
        title="Document Intelligence API",
        description="Evidence-backed technical PDF investigation API.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    if configure_runtime:
        runtime_services = configure_runtime_services(app, runtime_settings)
        if runtime_services is not None:
            app.state.runtime_services = runtime_services
    else:
        app.state.runtime_composition_errors = ()
    install_request_telemetry(app)
    app.include_router(answers_router)
    app.include_router(health_router)
    app.include_router(uploads_router)
    return app
