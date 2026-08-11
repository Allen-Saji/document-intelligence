from __future__ import annotations

from uuid import UUID

import pytest

from document_intelligence.ingestion.activities import IngestionActivities
from document_intelligence.ingestion.contracts import (
    IngestionDocument,
    PageExtraction,
    SourceDocument,
)
from document_intelligence.ingestion.pipeline import (
    IngestionPipeline,
    IngestionRequest,
    chunk_document,
)
from document_intelligence.provenance import PageRegion


class Scanner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def scan(self, object_key: str, sha256: str) -> None:
        self.calls.append((object_key, sha256))


class Parser:
    def __init__(self, document: IngestionDocument) -> None:
        self.document = document

    async def parse(self, _: str) -> IngestionDocument:
        return self.document


class Embedder:
    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [(float(len(text)), 1.0) for text in texts]


class Publisher:
    def __init__(self) -> None:
        self.records: list[object] = []
        self.idempotency_keys: list[str] = []

    async def publish(self, records: object, idempotency_key: str) -> None:
        self.records = list(records)
        self.idempotency_keys.append(idempotency_key)


def source() -> SourceDocument:
    return SourceDocument(
        document_version_id=UUID("00000000-0000-4000-8000-000000000001"),
        object_key="immutable/doc.pdf",
        sha256="a" * 64,
        byte_size=100,
    )


def request() -> IngestionRequest:
    return IngestionRequest(
        organization_id=UUID("00000000-0000-4000-8000-000000000002"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000003"),
        corpus_id=UUID("00000000-0000-4000-8000-000000000004"),
        document_id="protocol-spec",
        document_version_id=UUID("00000000-0000-4000-8000-000000000001"),
        source_object_key="immutable/doc.pdf",
        source_sha256="a" * 64,
        pipeline_version="v1",
    )


@pytest.mark.asyncio
async def test_pipeline_publishes_only_searchable_chunks_with_a_stable_key() -> None:
    document = IngestionDocument(
        source=source(),
        pages=(
            PageExtraction(
                page_number=1,
                text="Trusted text",
                source_region=PageRegion(left=1.0, top=2.0, right=30.0, bottom=40.0),
            ),
            PageExtraction(page_number=2, text="", quality_reasons=("empty-page",)),
        ),
    )
    scanner = Scanner()
    publisher = Publisher()
    pipeline = IngestionPipeline(
        scanner=scanner,
        parser=Parser(document),
        embedder=Embedder(),
        publisher=publisher,
    )

    outcome = await pipeline.run(request())

    assert outcome.stage == "published"
    assert outcome.searchable_page_count == 1
    assert outcome.quarantined_page_count == 1
    assert scanner.calls == [("immutable/doc.pdf", "a" * 64)]
    assert len(publisher.records) == 1
    assert publisher.records[0].source_region == PageRegion(
        left=1.0, top=2.0, right=30.0, bottom=40.0
    )
    assert len(publisher.idempotency_keys) == 1


@pytest.mark.asyncio
async def test_pipeline_fails_closed_when_no_page_is_searchable() -> None:
    document = IngestionDocument(
        source=source(),
        pages=(PageExtraction(page_number=1, text="", quality_reasons=("empty-page",)),),
    )
    pipeline = IngestionPipeline(
        scanner=Scanner(), parser=Parser(document), embedder=Embedder(), publisher=Publisher()
    )

    outcome = await pipeline.run(request())

    assert outcome.stage == "failed"
    assert outcome.failure_code == "no_searchable_content"


def test_chunking_is_deterministic_and_never_uses_quarantined_pages() -> None:
    document = IngestionDocument(
        source=source(),
        pages=(
            PageExtraction(page_number=1, text="a" * 220),
            PageExtraction(page_number=2, text="b" * 220, quality_reasons=("ocr-low-confidence",)),
        ),
    )

    first = chunk_document(document, max_chars=100)
    second = chunk_document(document, max_chars=100)

    assert first == second
    assert {chunk.page_number for chunk in first} == {1}


@pytest.mark.asyncio
async def test_pipeline_rejects_parser_output_for_a_different_verified_source() -> None:
    mismatched = source().model_copy(update={"sha256": "b" * 64})
    document = IngestionDocument(
        source=mismatched, pages=(PageExtraction(page_number=1, text="x"),)
    )
    pipeline = IngestionPipeline(
        scanner=Scanner(), parser=Parser(document), embedder=Embedder(), publisher=Publisher()
    )

    with pytest.raises(ValueError, match="does not match"):
        await pipeline.run(request())


@pytest.mark.asyncio
async def test_pipeline_rejects_parser_output_for_a_different_document_version() -> None:
    document = IngestionDocument(
        source=source().model_copy(
            update={"document_version_id": UUID("00000000-0000-4000-8000-000000000099")}
        ),
        pages=(PageExtraction(page_number=1, text="x"),),
    )
    pipeline = IngestionPipeline(
        scanner=Scanner(), parser=Parser(document), embedder=Embedder(), publisher=Publisher()
    )

    with pytest.raises(ValueError, match="does not match"):
        await pipeline.run(request())


@pytest.mark.asyncio
async def test_temporal_activity_delegates_to_the_same_fail_closed_pipeline() -> None:
    document = IngestionDocument(source=source(), pages=(PageExtraction(page_number=1, text="x"),))
    publisher = Publisher()
    activity = IngestionActivities(
        IngestionPipeline(
            scanner=Scanner(), parser=Parser(document), embedder=Embedder(), publisher=publisher
        )
    )

    outcome = await activity.run_ingestion_pipeline(request())

    assert outcome.stage == "published"
    assert len(publisher.records) == 1
