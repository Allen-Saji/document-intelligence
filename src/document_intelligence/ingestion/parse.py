from __future__ import annotations

from collections import defaultdict
from typing import Any

from document_intelligence.ingestion.contracts import (
    IngestionDocument,
    PageExtraction,
    SourceDocument,
)
from document_intelligence.parsing.benchmark import analyze_docling_export
from document_intelligence.provenance import PageRegion, enclosing_region


def document_from_docling_export(
    source: SourceDocument,
    exported: dict[str, Any],
    *,
    diagram_pages: tuple[int, ...] = (),
) -> IngestionDocument:
    """Convert parser output while retaining the Phase 0 page-quality quarantine rules."""

    analysis = analyze_docling_export(exported, diagram_pages)
    text_by_page: dict[int, list[str]] = defaultdict(list)
    regions_by_page: dict[int, list[PageRegion]] = defaultdict(list)
    for item in exported.get("texts", []):
        text = item.get("text")
        if not isinstance(text, str):
            continue
        for provenance in item.get("prov", []):
            page_number = provenance.get("page_no")
            if isinstance(page_number, int) and page_number > 0:
                text_by_page[page_number].append(text)
                region = _page_region(provenance.get("bbox"))
                if region is not None:
                    regions_by_page[page_number].append(region)
    pages: list[PageExtraction] = []
    for page_number, metrics in analysis["pages"].items():
        page = int(page_number)
        pages.append(
            PageExtraction(
                page_number=page,
                text="\n".join(text_by_page[page]),
                source_region=enclosing_region(tuple(regions_by_page[page])),
                quality_reasons=tuple(metrics["quality_reasons"]),
            )
        )
    return IngestionDocument(source=source, pages=tuple(pages))


def _page_region(value: object) -> PageRegion | None:
    if not isinstance(value, dict):
        return None
    left = value.get("l")
    top = value.get("t")
    right = value.get("r")
    bottom = value.get("b")
    if not (
        isinstance(left, int | float)
        and isinstance(top, int | float)
        and isinstance(right, int | float)
        and isinstance(bottom, int | float)
    ):
        return None
    try:
        return PageRegion(
            left=float(left),
            top=float(top),
            right=float(right),
            bottom=float(bottom),
        )
    except ValueError:
        return None
