import httpx
import pytest
from pydantic import SecretStr

from document_intelligence.api.app import create_app
from document_intelligence.config import Settings


@pytest.mark.asyncio
async def test_liveness_does_not_depend_on_external_services() -> None:
    app = create_app(Settings(env="test"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_production_readiness_returns_missing_setting_names_only() -> None:
    app = create_app(Settings(env="production"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert "APP_DATABASE_URL" in body["missing_settings"]


@pytest.mark.asyncio
async def test_production_readiness_succeeds_with_complete_configuration() -> None:
    settings = Settings(
        env="production",
        database_url=SecretStr("postgresql://example"),
        opensearch_url=SecretStr("https://search.example"),
        temporal_target=SecretStr("temporal.example:7233"),
        s3_bucket="documents",
        oidc_issuer=SecretStr("https://identity.example"),
        api_key_pepper=SecretStr("pepper"),
        generation_provider="provider",
        generation_model="model",
        otel_exporter_otlp_endpoint=SecretStr("https://otel.example"),
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["missing_settings"] == []


@pytest.mark.asyncio
async def test_staging_readiness_requires_external_configuration() -> None:
    app = create_app(Settings(env="staging"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert "APP_DATABASE_URL" in response.json()["missing_settings"]
