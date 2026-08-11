from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Diagnostics expose only the names of missing settings. Secret values stay wrapped in
    SecretStr and must never be serialized or logged.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
        populate_by_name=True,
    )

    env: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    service_name: str = "document-intelligence-api"

    database_url: SecretStr | None = None
    opensearch_url: SecretStr | None = None
    temporal_target: SecretStr | None = None
    s3_endpoint_url: SecretStr | None = None
    s3_bucket: str | None = None
    s3_region: str = "us-east-1"
    oidc_issuer: SecretStr | None = None
    api_key_pepper: SecretStr | None = None
    opensearch_index_name: str | None = None
    embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    ingestion_pipeline_version: str | None = None
    retrieval_index_version: str | None = None
    answer_pipeline_version: str | None = None
    answer_rate_limit_per_minute: int = Field(default=30, ge=1, le=10_000)
    answer_monthly_token_budget: int = Field(default=500_000, ge=1)
    answer_estimated_output_tokens: int = Field(default=1_200, ge=1, le=100_000)
    generation_provider: str | None = None
    generation_model: str | None = None
    generation_test_model: str = "gpt-5.6-luna"
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("APP_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    otel_exporter_otlp_endpoint: SecretStr | None = None

    def missing_production_settings(self) -> tuple[str, ...]:
        required = {
            "APP_DATABASE_URL": self.database_url,
            "APP_OPENSEARCH_URL": self.opensearch_url,
            "APP_TEMPORAL_TARGET": self.temporal_target,
            "APP_S3_BUCKET": self.s3_bucket,
            "APP_OIDC_ISSUER": self.oidc_issuer,
            "APP_API_KEY_PEPPER": self.api_key_pepper,
            "APP_OPENSEARCH_INDEX_NAME": self.opensearch_index_name,
            "APP_INGESTION_PIPELINE_VERSION": self.ingestion_pipeline_version,
            "APP_RETRIEVAL_INDEX_VERSION": self.retrieval_index_version,
            "APP_ANSWER_PIPELINE_VERSION": self.answer_pipeline_version,
            "APP_GENERATION_PROVIDER": self.generation_provider,
            "APP_GENERATION_MODEL": self.generation_model,
            "APP_OPENAI_API_KEY": self.openai_api_key,
            "APP_OTEL_EXPORTER_OTLP_ENDPOINT": self.otel_exporter_otlp_endpoint,
        }
        return tuple(name for name, value in required.items() if not _is_present(value))

    @property
    def is_ready(self) -> bool:
        return self.env in {"local", "test"} or not self.missing_production_settings()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _is_present(value: str | SecretStr | None) -> bool:
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    return bool(value)
