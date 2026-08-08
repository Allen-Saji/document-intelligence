from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict

from document_intelligence import __version__
from document_intelligence.config import Settings

router = APIRouter(prefix="/health", tags=["system"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "not_ready"]
    service: str
    version: str
    missing_settings: tuple[str, ...] = ()


@router.get("/live", response_model=HealthResponse)
async def liveness(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(status="ok", service=settings.service_name, version=__version__)


@router.get("/ready", response_model=HealthResponse)
async def readiness(request: Request, response: Response) -> HealthResponse:
    settings: Settings = request.app.state.settings
    missing = (
        settings.missing_production_settings() if settings.env in {"staging", "production"} else ()
    )
    if missing:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="not_ready",
            service=settings.service_name,
            version=__version__,
            missing_settings=missing,
        )
    return HealthResponse(status="ok", service=settings.service_name, version=__version__)
