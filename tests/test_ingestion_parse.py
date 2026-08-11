from uuid import UUID

from document_intelligence.ingestion.contracts import SourceDocument
from document_intelligence.ingestion.parse import document_from_docling_export
from document_intelligence.provenance import PageRegion


def test_docling_export_admission_quarantines_empty_and_picture_contained_text_pages() -> None:
    source = SourceDocument(
        document_version_id=UUID("00000000-0000-4000-8000-000000000001"),
        object_key="immutable/doc.pdf",
        sha256="a" * 64,
        byte_size=10,
    )
    exported = {
        "pages": {
            "1": {"size": {"width": 100, "height": 100}},
            "2": {"size": {"width": 100, "height": 100}},
        },
        "texts": [
            {
                "self_ref": "#/texts/0",
                "text": "outside picture",
                "prov": [{"page_no": 1, "bbox": {"l": 1, "t": 1, "r": 20, "b": 10}}],
            }
        ],
        "tables": [],
        "pictures": [],
        "key_value_items": [],
        "body": [{"$ref": "#/texts/0"}],
    }

    document = document_from_docling_export(source, exported)

    assert [page.page_number for page in document.searchable_pages] == [1]
    assert document.searchable_pages[0].source_region == PageRegion(
        left=1.0, top=1.0, right=20.0, bottom=10.0
    )
    assert document.quarantined_pages[0].page_number == 2
    assert "empty-page" in document.quarantined_pages[0].quality_reasons
