from __future__ import annotations

from pydantic import SecretStr

from document_intelligence.config import Settings
from document_intelligence.worker import composition


class Engine:
    async def dispose(self) -> None:
        return None


class SearchClient:
    def __init__(self, *, url: SecretStr) -> None:
        self.url_seen = bool(url.get_secret_value())

    async def close(self) -> None:
        return None


class Store:
    def __init__(self) -> None:
        self.client = object()
        self.bucket = "documents"

    @classmethod
    def from_settings(cls, settings: Settings) -> Store:
        assert settings.s3_bucket == "documents"
        return cls()


class Parser:
    def __init__(self, reader: object) -> None:
        self.reader = reader


class Embedder:
    def __init__(self, *, model_name: str) -> None:
        self.model_name = model_name


def settings(**updates: object) -> Settings:
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
        "generation_provider": "openai",
        "generation_model": "gpt-5.6-luna",
        "openai_api_key": SecretStr("key"),
        "otel_exporter_otlp_endpoint": SecretStr("https://otel.example"),
    }
    values.update(updates)
    return Settings(**values)


def test_worker_runtime_composition_builds_temporal_activities(monkeypatch) -> None:
    monkeypatch.setattr(composition, "create_database_engine", lambda _: Engine())
    monkeypatch.setattr(composition, "AsyncOpenSearchSearchClient", SearchClient)
    monkeypatch.setattr(composition, "S3CompatibleObjectStore", Store)
    monkeypatch.setattr(composition, "DoclingObjectParser", Parser)
    monkeypatch.setattr(composition, "SentenceTransformerQueryEmbedder", Embedder)

    runtime = composition.build_worker_runtime(settings())

    assert len(runtime.activities) == 2


def test_worker_runtime_rejects_incomplete_production_settings() -> None:
    try:
        composition.build_worker_runtime(Settings(env="production"))
    except ValueError as error:
        assert "missing worker settings" in str(error)
    else:
        raise AssertionError("worker composition should fail closed")
