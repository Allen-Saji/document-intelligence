from __future__ import annotations

from collections import defaultdict
from typing import Any

from document_intelligence.ingestion.contracts import (
    IngestionDocument,
    PageExtraction,
    SourceDocument,
)
from document_intelligence.parsing.benchmark import analyze_docling_export


def document_from_docling_export(
    source: SourceDocument,
    exported: dict[str, Any],
    *,
    diagram_pages: tuple[int, ...] = (),
) -> IngestionDocument:
    """Convert parser output while retaining the Phase 0 page-quality quarantine rules."""

    analysis = analyze_docling_export(exported, diagram_pages)
    text_by_page: dict[int, list[str]] = defaultdict(list)
    for item in exported.get("texts", []):
        text = item.get("text")
        if not isinstance(text, str):
            continue
        for provenance in item.get("prov", []):
            page_number = provenance.get("page_no")
            if isinstance(page_number, int) and page_number > 0:
                text_by_page[page_number].append(text)
    pages: list[PageExtraction] = []
    for page_number, metrics in analysis["pages"].items():
        page = int(page_number)
        pages.append(
            PageExtraction(
                page_number=page,
                text="\n".join(text_by_page[page]),
                quality_reasons=tuple(metrics["quality_reasons"]),
            )
        )
    return IngestionDocument(source=source, pages=tuple(pages))
