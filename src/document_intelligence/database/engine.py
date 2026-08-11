from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from document_intelligence.config import Settings


def create_database_engine(settings: Settings) -> AsyncEngine:
    if settings.database_url is None:
        raise ValueError("database_url must be configured")
    return create_async_engine(
        _asyncpg_url(settings.database_url.get_secret_value()),
        pool_pre_ping=True,
    )


def _asyncpg_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url
