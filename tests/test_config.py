from pydantic import SecretStr

from document_intelligence.config import Settings


def test_local_environment_is_ready_without_external_configuration() -> None:
    assert Settings(env="local").is_ready is True


def test_production_environment_reports_names_not_values() -> None:
    settings = Settings(env="production", database_url=SecretStr("do-not-expose"))

    missing = settings.missing_production_settings()

    assert "APP_DATABASE_URL" not in missing
    assert "APP_OPENSEARCH_URL" in missing
    assert "do-not-expose" not in repr(missing)
    assert settings.is_ready is False


def test_empty_secret_is_reported_as_missing_without_exposing_it() -> None:
    settings = Settings(env="production", database_url=SecretStr(""))

    missing = settings.missing_production_settings()

    assert "APP_DATABASE_URL" in missing
    assert settings.is_ready is False


def test_complete_production_configuration_is_ready() -> None:
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

    assert settings.missing_production_settings() == ()
    assert settings.is_ready is True
