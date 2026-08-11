from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from document_intelligence.retrieval.index import ChunkIndexRecord


class PublicationState(StrEnum):
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"
    DELETED = "deleted"


class PublicationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_version_id: UUID
    idempotency_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: PublicationState
    chunk_count: int = Field(ge=0)


class IndexProjection(Protocol):
    async def upsert(self, records: tuple[ChunkIndexRecord, ...]) -> None: ...

    async def delete_version(self, document_version_id: UUID) -> None: ...


class PublicationLedger(Protocol):
    async def get(self, idempotency_key: str) -> PublicationRecord | None: ...

    async def save(self, record: PublicationRecord) -> None: ...


class IdempotentPublisher:
    """Publish once, preserve the active projection on retries, and support rollback/deletion."""

    def __init__(self, *, projection: IndexProjection, ledger: PublicationLedger) -> None:
        self._projection = projection
        self._ledger = ledger

    async def publish(self, records: Sequence[ChunkIndexRecord], idempotency_key: str) -> None:
        existing = await self._ledger.get(idempotency_key)
        if existing is not None and existing.state == PublicationState.ACTIVE:
            return
        if not records:
            raise ValueError("cannot publish an empty document projection")
        await self._projection.upsert(tuple(records))
        await self._ledger.save(
            PublicationRecord(
                document_version_id=records[0].document_version_id,
                idempotency_key=idempotency_key,
                state=PublicationState.ACTIVE,
                chunk_count=len(records),
            )
        )

    async def rollback(self, record: PublicationRecord) -> PublicationRecord:
        await self._projection.delete_version(record.document_version_id)
        rolled_back = record.model_copy(update={"state": PublicationState.ROLLED_BACK})
        await self._ledger.save(rolled_back)
        return rolled_back

    async def delete(self, record: PublicationRecord) -> PublicationRecord:
        await self._projection.delete_version(record.document_version_id)
        deleted = record.model_copy(update={"state": PublicationState.DELETED})
        await self._ledger.save(deleted)
        return deleted
