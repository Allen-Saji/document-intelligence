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

    organization_id: UUID
    workspace_id: UUID
    document_version_id: UUID
    idempotency_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: PublicationState
    chunk_count: int = Field(ge=0)


class IndexProjection(Protocol):
    async def upsert(self, records: tuple[ChunkIndexRecord, ...]) -> None: ...

    async def delete_version(self, document_version_id: UUID) -> None: ...


class PublicationLedger(Protocol):
    async def get(
        self, idempotency_key: str, *, organization_id: UUID, workspace_id: UUID
    ) -> PublicationRecord | None: ...

    async def save(self, record: PublicationRecord) -> None: ...


class IdempotentPublisher:
    """Publish once, preserve the active projection on retries, and support rollback/deletion."""

    def __init__(self, *, projection: IndexProjection, ledger: PublicationLedger) -> None:
        self._projection = projection
        self._ledger = ledger

    async def publish(self, records: Sequence[ChunkIndexRecord], idempotency_key: str) -> None:
        if not records:
            raise ValueError("cannot publish an empty document projection")
        existing = await self._ledger.get(
            idempotency_key,
            organization_id=records[0].organization_id,
            workspace_id=records[0].workspace_id,
        )
        if existing is not None and existing.state == PublicationState.ACTIVE:
            return
        await self._projection.upsert(tuple(records))
        await self._ledger.save(
            PublicationRecord(
                organization_id=records[0].organization_id,
                workspace_id=records[0].workspace_id,
                document_version_id=records[0].document_version_id,
                idempotency_key=idempotency_key,
                state=PublicationState.ACTIVE,
                chunk_count=len(records),
            )
        )

    async def rollback(self, record: PublicationRecord) -> PublicationRecord:
        return await self._remove(record, PublicationState.ROLLED_BACK)

    async def delete(self, record: PublicationRecord) -> PublicationRecord:
        return await self._remove(record, PublicationState.DELETED)

    async def _remove(
        self, record: PublicationRecord, target: PublicationState
    ) -> PublicationRecord:
        current = await self._ledger.get(
            record.idempotency_key,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
        )
        if current is None:
            raise ValueError("cannot remove a publication that is not recorded")
        if current.state == target:
            return current
        if current.state == PublicationState.ACTIVE:
            await self._projection.delete_version(current.document_version_id)
        updated = current.model_copy(update={"state": target})
        await self._ledger.save(updated)
        return updated
