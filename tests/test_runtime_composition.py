from __future__ import annotations

from fastapi import FastAPI
from pydantic import SecretStr

from document_intelligence.api import composition
from document_intelligence.api.composition import configure_runtime_services
from document_intelligence.config import Settings
from document_intelligence.database.engine import _asyncpg_url


class Engine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class SearchClient:
    def __init__(self, *, url: SecretStr) -> None:
        self.url_seen = bool(url.get_secret_value())
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class Store:
    @classmethod
    def from_settings(cls, settings: Settings) -> Store:
        assert settings.s3_bucket == "documents"
        return cls()


class Embedder:
    def __init__(self, *, model_name: str) -> None:
        self.model_name = model_name


class Provider:
    def __init__(self, *, api_key: SecretStr, model: str) -> None:
        self.api_key_seen = bool(api_key.get_secret_value())
        self.model = model

    async def generate(self, prompt: object) -> object:
        raise AssertionError("provider should not be called by composition tests")

    async def repair(self, prompt: object, draft: object, correction: object) -> object:
        raise AssertionError("provider should not be called by composition tests")


def complete_settings(**updates: object) -> Settings:
    values = {
        "env": "production",
        "database_url": SecretStr("postgresql://example"),
        "opensearch_url": SecretStr("https://search.example"),
        "temporal_target": SecretStr("temporal.example:7233"),
        "s3_bucket": "documents",
        "oidc_issuer": SecretStr("https://identity.example"),
        "api_key_pepper": SecretStr("pepper"),
        "opensearch_index_name": "chunks-current",
        "ingestion_pipeline_version": "ingestion-v1",
        "retrieval_index_version": "chunks-v1",
        "answer_pipeline_version": "answers-v1",
        "malware_scanner_command": "clamscan",
        "generation_provider": "openai",
        "generation_model": "gpt-5.6-luna",
        "openai_api_key": SecretStr("key"),
        "otel_exporter_otlp_endpoint": SecretStr("https://otel.example"),
    }
    values.update(updates)
    return Settings(**values)


def test_runtime_composition_installs_production_answer_dependencies(monkeypatch) -> None:
    engine = Engine()
    monkeypatch.setattr(composition, "create_database_engine", lambda _: engine)
    monkeypatch.setattr(composition, "AsyncOpenSearchSearchClient", SearchClient)
    monkeypatch.setattr(composition, "SentenceTransformerQueryEmbedder", Embedder)
    monkeypatch.setattr(composition, "OpenAIResponsesProvider", Provider)
    monkeypatch.setattr(composition, "S3CompatibleObjectStore", Store)
    app = FastAPI()

    bundle = configure_runtime_services(app, complete_settings())

    assert bundle is not None
    assert app.state.api_key_lookup is bundle.api_key_lookup
    assert app.state.corpus_access_resolver is bundle.corpus_access_resolver
    assert app.state.upload_service is bundle.upload_service
    assert app.state.answer_orchestrator is bundle.answer_orchestrator
    assert app.state.runtime_composition_errors == ()


def test_runtime_composition_skips_incomplete_production_settings() -> None:
    app = FastAPI()

    bundle = configure_runtime_services(app, Settings(env="production"))

    assert bundle is None
    assert not hasattr(app.state, "answer_orchestrator")
    assert app.state.runtime_composition_errors == ()


def test_runtime_composition_rejects_unsupported_generation_provider(monkeypatch) -> None:
    monkeypatch.setattr(composition, "create_database_engine", lambda _: Engine())
    monkeypatch.setattr(composition, "AsyncOpenSearchSearchClient", SearchClient)
    monkeypatch.setattr(composition, "SentenceTransformerQueryEmbedder", Embedder)
    monkeypatch.setattr(composition, "S3CompatibleObjectStore", Store)
    app = FastAPI()

    bundle = configure_runtime_services(
        app, complete_settings(generation_provider="other-provider")
    )

    assert bundle is None
    assert app.state.runtime_composition_errors == ("unsupported generation provider",)


def test_asyncpg_url_normalization_keeps_explicit_async_driver() -> None:
    assert _asyncpg_url("postgresql://db.example/app") == ("postgresql+asyncpg://db.example/app")
    assert _asyncpg_url("postgresql+asyncpg://db.example/app") == (
        "postgresql+asyncpg://db.example/app"
    )


def test_local_runtime_composition_is_left_to_test_injection() -> None:
    app = FastAPI()

    bundle = configure_runtime_services(app, Settings(env="test"))

    assert bundle is None
    assert app.state.runtime_composition_errors == ()
