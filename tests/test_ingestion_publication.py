from uuid import UUID

import pytest

from document_intelligence.ingestion.publication import (
    IdempotentPublisher,
    PublicationRecord,
    PublicationState,
)
from document_intelligence.retrieval.index import ChunkIndexRecord


class Projection:
    def __init__(self) -> None:
        self.upserts = 0
        self.deleted: list[UUID] = []

    async def upsert(self, records: tuple[ChunkIndexRecord, ...]) -> None:
        self.upserts += 1

    async def delete_version(self, document_version_id: UUID) -> None:
        self.deleted.append(document_version_id)


class Ledger:
    def __init__(self) -> None:
        self.records: dict[str, PublicationRecord] = {}

    async def get(self, key: str) -> PublicationRecord | None:
        return self.records.get(key)

    async def save(self, record: PublicationRecord) -> None:
        self.records[record.idempotency_key] = record


def record() -> ChunkIndexRecord:
    return ChunkIndexRecord(
        organization_id=UUID("00000000-0000-4000-8000-000000000001"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000002"),
        corpus_id=UUID("00000000-0000-4000-8000-000000000003"),
        document_id="protocol",
        document_version_id=UUID("00000000-0000-4000-8000-000000000004"),
        chunk_id=UUID("00000000-0000-4000-8000-000000000005"),
        page_number=1,
        content="trusted evidence",
        embedding=(0.1, 0.2),
    )


@pytest.mark.asyncio
async def test_publication_retry_is_idempotent_and_rollback_removes_the_projection() -> None:
    projection = Projection()
    ledger = Ledger()
    publisher = IdempotentPublisher(projection=projection, ledger=ledger)
    key = "a" * 64

    await publisher.publish((record(),), key)
    await publisher.publish((record(),), key)
    active = ledger.records[key]
    rolled_back = await publisher.rollback(active)

    assert projection.upserts == 1
    assert rolled_back.state == PublicationState.ROLLED_BACK
    assert projection.deleted == [record().document_version_id]


@pytest.mark.asyncio
async def test_deletion_removes_an_active_projection() -> None:
    projection = Projection()
    ledger = Ledger()
    publisher = IdempotentPublisher(projection=projection, ledger=ledger)
    key = "b" * 64
    await publisher.publish((record(),), key)

    deleted = await publisher.delete(ledger.records[key])

    assert deleted.state == PublicationState.DELETED


@pytest.mark.asyncio
async def test_removal_retries_do_not_delete_the_projection_twice() -> None:
    projection = Projection()
    ledger = Ledger()
    publisher = IdempotentPublisher(projection=projection, ledger=ledger)
    key = "c" * 64
    await publisher.publish((record(),), key)

    first = await publisher.delete(ledger.records[key])
    second = await publisher.delete(first)

    assert second.state == PublicationState.DELETED
    assert projection.deleted == [record().document_version_id]
