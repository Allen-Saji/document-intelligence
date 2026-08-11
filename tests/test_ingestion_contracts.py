from uuid import UUID

import pytest

from document_intelligence.ingestion.contracts import (
    IngestionDocument,
    IngestionStage,
    PageExtraction,
    ProcessingOutcome,
    SourceDocument,
)


def source() -> SourceDocument:
    return SourceDocument(
        document_version_id=UUID("00000000-0000-4000-8000-000000000001"),
        object_key="organizations/a/documents/b/original.pdf",
        sha256="a" * 64,
        byte_size=123,
    )


def test_quarantined_pages_are_excluded_from_searchable_ingestion_output() -> None:
    document = IngestionDocument(
        source=source(),
        pages=(
            PageExtraction(page_number=1, text="Trusted content"),
            PageExtraction(page_number=2, text="", quality_reasons=("empty-page",)),
        ),
    )

    assert [page.page_number for page in document.searchable_pages] == [1]
    assert [page.page_number for page in document.quarantined_pages] == [2]


def test_published_documents_must_have_searchable_content() -> None:
    with pytest.raises(ValueError, match="searchable pages"):
        ProcessingOutcome(
            stage=IngestionStage.PUBLISHED,
            searchable_page_count=0,
            quarantined_page_count=1,
        )
