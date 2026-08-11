from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
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
    generation_provider: str | None = None
    generation_model: str | None = None
    otel_exporter_otlp_endpoint: SecretStr | None = None

    def missing_production_settings(self) -> tuple[str, ...]:
        required = {
            "APP_DATABASE_URL": self.database_url,
            "APP_OPENSEARCH_URL": self.opensearch_url,
            "APP_TEMPORAL_TARGET": self.temporal_target,
            "APP_S3_BUCKET": self.s3_bucket,
            "APP_OIDC_ISSUER": self.oidc_issuer,
            "APP_API_KEY_PEPPER": self.api_key_pepper,
            "APP_GENERATION_PROVIDER": self.generation_provider,
            "APP_GENERATION_MODEL": self.generation_model,
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
