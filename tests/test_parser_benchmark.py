from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from document_intelligence.parsing.benchmark import (
    CorpusDocument,
    ExpectedFeatures,
    ParserCorpus,
    analyze_docling_export,
    checks_passed,
    evaluate_features,
    fetch_fixture,
    load_corpus,
    sha256_file,
    verify_fixture,
)


def test_repository_corpus_manifest_is_valid() -> None:
    corpus = load_corpus(Path("spikes/parser/corpus.json"))

    assert corpus.id == "phase-0-parser-v1"
    assert len(corpus.documents) == 4
    assert {category for document in corpus.documents for category in document.categories} == {
        "born-digital",
        "scanned",
        "multi-column",
        "table-heavy",
        "code-heavy",
        "formula-heavy",
        "mixed-text-image",
    }


def test_manifest_rejects_unsafe_filename(tmp_path: Path) -> None:
    payload = json.loads(Path("spikes/parser/corpus.json").read_text(encoding="utf-8"))
    payload["documents"][0]["filename"] = "../escape.pdf"
    manifest = tmp_path / "corpus.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="plain PDF filename"):
        load_corpus(manifest)


def test_manifest_rejects_insecure_source_url(tmp_path: Path) -> None:
    payload = json.loads(Path("spikes/parser/corpus.json").read_text(encoding="utf-8"))
    payload["documents"][0]["source_url"] = "http://example.com/fixture.pdf"
    manifest = tmp_path / "corpus.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="must use HTTPS"):
        load_corpus(manifest)


def test_manifest_rejects_duplicate_document_ids(tmp_path: Path) -> None:
    payload = json.loads(Path("spikes/parser/corpus.json").read_text(encoding="utf-8"))
    payload["documents"][1]["id"] = payload["documents"][0]["id"]
    manifest = tmp_path / "corpus.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="must be unique"):
        load_corpus(manifest)


def test_manifest_rejects_inverted_page_range(tmp_path: Path) -> None:
    payload = json.loads(Path("spikes/parser/corpus.json").read_text(encoding="utf-8"))
    payload["documents"][0]["page_range"] = [3, 2]
    manifest = tmp_path / "corpus.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="one-based start and end"):
        load_corpus(manifest)


def test_verify_fixture_checks_size_and_hash(tmp_path: Path) -> None:
    corpus = load_corpus(Path("spikes/parser/corpus.json"))
    document = corpus.documents[0].model_copy(
        update={"byte_size": 7, "sha256": sha256_file(_write_fixture(tmp_path, b"fixture"))}
    )
    fixture = tmp_path / document.filename
    fixture.write_bytes(b"fixture")

    verify_fixture(document, fixture)

    fixture.write_bytes(b"changed-size")
    with pytest.raises(ValueError, match="size mismatch"):
        verify_fixture(document, fixture)


def test_verify_fixture_rejects_same_size_hash_mismatch(tmp_path: Path) -> None:
    corpus = load_corpus(Path("spikes/parser/corpus.json"))
    document = corpus.documents[0].model_copy(
        update={"byte_size": 7, "sha256": sha256_file(_write_fixture(tmp_path, b"fixture"))}
    )
    fixture = tmp_path / document.filename
    fixture.write_bytes(b"changed")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_fixture(document, fixture)


def test_verify_fixture_rejects_missing_file(tmp_path: Path) -> None:
    document = load_corpus(Path("spikes/parser/corpus.json")).documents[0]

    with pytest.raises(FileNotFoundError, match="missing corpus fixture"):
        verify_fixture(document, tmp_path / document.filename)


def test_fetch_fixture_uses_verified_cached_file(tmp_path: Path) -> None:
    content = b"fixture"
    source = tmp_path / "fixture.pdf"
    source.write_bytes(content)
    document = (
        load_corpus(Path("spikes/parser/corpus.json"))
        .documents[0]
        .model_copy(
            update={
                "filename": source.name,
                "byte_size": len(content),
                "sha256": sha256_file(source),
            }
        )
    )

    assert fetch_fixture(document, tmp_path) == source


def test_fetch_fixture_refuses_existing_partial_download(tmp_path: Path) -> None:
    document = (
        load_corpus(Path("spikes/parser/corpus.json"))
        .documents[0]
        .model_copy(update={"filename": "fixture.pdf"})
    )
    partial = tmp_path / "fixture.pdf.part"
    partial.write_bytes(b"partial")

    with pytest.raises(FileExistsError, match="inspect it before retrying"):
        fetch_fixture(document, tmp_path)


def test_analyze_export_reports_geometry_and_labels() -> None:
    exported = {
        "pages": {"1": {"size": {"width": 100, "height": 200}, "page_no": 1}},
        "texts": [
            {
                "label": "code",
                "text": "print('ok')",
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {"l": 10, "t": 20, "r": 90, "b": 40},
                    }
                ],
            },
            {
                "label": "formula",
                "text": "x = 1",
                "prov": [
                    {
                        "page_no": 2,
                        "bbox": {"l": 10, "t": 20, "r": 90, "b": 40},
                    }
                ],
            },
        ],
        "tables": [],
        "pictures": [],
        "key_value_items": [],
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
    }

    metrics = analyze_docling_export(exported)

    assert metrics["page_count"] == 1
    assert metrics["text_chars"] == 16
    assert metrics["label_counts"] == {"code": 1, "formula": 1}
    assert metrics["geometry_coverage"] == 0.5
    assert metrics["provenance_entries"] == 2
    assert metrics["valid_geometry_entries"] == 1
    assert metrics["empty_pages"] == []
    assert metrics["pages"]["1"]["item_count"] == 1
    assert metrics["invalid_provenance"][0]["reason"] == "unknown-page"


def test_geometry_coverage_counts_every_provenance_entry() -> None:
    exported = {
        "pages": {"1": {"size": {"width": 100, "height": 200}, "page_no": 1}},
        "texts": [
            {
                "label": "text",
                "text": "mixed geometry",
                "prov": [
                    {"page_no": 1, "bbox": {"l": 10, "t": 20, "r": 90, "b": 40}},
                    {"page_no": 2, "bbox": {"l": 10, "t": 20, "r": 90, "b": 40}},
                ],
            }
        ],
        "tables": [],
        "pictures": [],
        "key_value_items": [],
        "body": {"children": [{"$ref": "#/texts/0"}]},
    }

    metrics = analyze_docling_export(exported)

    assert metrics["items_with_valid_geometry"] == 1
    assert metrics["geometry_coverage"] == 0.5


def test_analyze_export_reports_malformed_and_out_of_bounds_geometry() -> None:
    exported = {
        "pages": {"1": {"size": {"width": 100, "height": 200}, "page_no": 1}},
        "texts": [
            {"label": "text", "text": "missing", "prov": [{"page_no": 1}]},
            {
                "label": "text",
                "text": "outside",
                "prov": [{"page_no": 1, "bbox": {"l": -1, "t": 20, "r": 90, "b": 40}}],
            },
        ],
        "tables": [],
        "pictures": [],
        "key_value_items": [],
        "body": {"children": []},
    }

    metrics = analyze_docling_export(exported)

    assert metrics["geometry_coverage"] == 0.0
    assert [item["reason"] for item in metrics["invalid_provenance"]] == [
        "missing-bounding-box",
        "horizontal-bounds",
    ]


def test_feature_evaluation_reports_failed_minimum() -> None:
    metrics = {
        "page_count": 2,
        "text_chars": 100,
        "geometry_coverage": 1.0,
        "empty_pages": [],
        "invalid_provenance": [],
        "collection_counts": {"tables": 0, "pictures": 0},
        "label_counts": {"code": 1},
    }
    expected = ExpectedFeatures(
        min_page_count=2,
        min_text_chars=50,
        max_empty_pages=0,
        min_tables=1,
        min_labels={"code": 1},
    )

    checks = evaluate_features(metrics, expected)

    assert checks["tables"] == {"actual": 0, "minimum": 1, "passed": False}
    assert checks_passed(checks) is False


def test_feature_evaluation_rejects_invalid_provenance() -> None:
    metrics = {
        "page_count": 1,
        "text_chars": 100,
        "geometry_coverage": 0.99,
        "empty_pages": [],
        "invalid_provenance": [{"reason": "horizontal-bounds"}],
        "collection_counts": {"tables": 0, "pictures": 0},
        "label_counts": {},
    }

    checks = evaluate_features(metrics, ExpectedFeatures(min_page_count=1))

    assert checks["invalid_provenance"] == {"actual": 1, "maximum": 0, "passed": False}
    assert checks_passed(checks) is False


def test_benchmark_rejects_non_successful_conversion(
    tmp_path: Path,
) -> None:
    benchmark_document = runpy.run_path("spikes/parser/run.py")["benchmark_document"]
    content = b"fixture"
    source = tmp_path / "fixture.pdf"
    source.write_bytes(content)
    document = (
        load_corpus(Path("spikes/parser/corpus.json"))
        .documents[0]
        .model_copy(
            update={
                "filename": source.name,
                "byte_size": len(content),
                "sha256": sha256_file(source),
                "expected": ExpectedFeatures(
                    min_page_count=1,
                    min_text_chars=1,
                    min_geometry_coverage=1.0,
                ),
            }
        )
    )
    exported = {
        "pages": {"1": {"size": {"width": 100, "height": 200}, "page_no": 1}},
        "texts": [
            {
                "label": "text",
                "text": "content",
                "prov": [{"page_no": 1, "bbox": {"l": 10, "t": 20, "r": 90, "b": 40}}],
            }
        ],
        "tables": [],
        "pictures": [],
        "key_value_items": [],
        "body": {"children": [{"$ref": "#/texts/0"}]},
    }
    fake_doc = SimpleNamespace(
        pages={},
        export_to_dict=lambda: exported,
        export_to_markdown=lambda: "content",
    )
    fake_result = SimpleNamespace(document=fake_doc, status="partial_success", errors=[])
    fake_converter = SimpleNamespace(convert=lambda _: fake_result)
    report = benchmark_document(document, source, tmp_path / "output", fake_converter)

    assert all(check["passed"] for check in report["automated_checks"].values())
    assert report["conversion"]["status"] == "partial_success"
    assert report["status"] == "automated-fail"


def test_document_selection_preserves_requested_order() -> None:
    runner = runpy.run_path("spikes/parser/run.py")
    corpus = load_corpus(Path("spikes/parser/corpus.json"))

    selected = runner["_select_documents"](corpus, ["faa-handbook-sample", "docling-code-formula"])

    assert [document.id for document in selected] == [
        "faa-handbook-sample",
        "docling-code-formula",
    ]


def test_document_selection_rejects_unknown_id() -> None:
    runner = runpy.run_path("spikes/parser/run.py")
    corpus = load_corpus(Path("spikes/parser/corpus.json"))

    with pytest.raises(ValueError, match="unknown document IDs: missing"):
        runner["_select_documents"](corpus, ["missing"])


def test_summary_recomputes_current_checks(tmp_path: Path) -> None:
    runner = runpy.run_path("spikes/parser/run.py")
    corpus = _single_document_corpus()
    document = corpus.documents[0]
    report_path = tmp_path / document.id / "report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(_passing_report(document)), encoding="utf-8")

    summary = runner["summarize_existing"](corpus, tmp_path)

    assert summary["automated_passes"] == [document.id]
    assert summary["automated_failures"] == []
    assert summary["execution_failures"] == []


def test_summary_rejects_report_from_stale_manifest(tmp_path: Path) -> None:
    runner = runpy.run_path("spikes/parser/run.py")
    corpus = _single_document_corpus()
    document = corpus.documents[0]
    report = _passing_report(document)
    report["input"]["profile"] = "full-page-ocr"
    report_path = tmp_path / document.id / "report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")

    summary = runner["summarize_existing"](corpus, tmp_path)

    assert summary["automated_passes"] == []
    assert "profile" in summary["execution_failures"][0]["error"]


def test_summary_reports_missing_document_report(tmp_path: Path) -> None:
    runner = runpy.run_path("spikes/parser/run.py")
    corpus = _single_document_corpus()

    summary = runner["summarize_existing"](corpus, tmp_path)

    assert summary["automated_passes"] == []
    assert summary["execution_failures"][0]["document_id"] == corpus.documents[0].id


def _single_document_corpus() -> ParserCorpus:
    document = (
        load_corpus(Path("spikes/parser/corpus.json"))
        .documents[0]
        .model_copy(
            update={
                "expected": ExpectedFeatures(
                    min_page_count=1,
                    min_text_chars=1,
                    min_geometry_coverage=1.0,
                )
            }
        )
    )
    return ParserCorpus(
        schema_version=1,
        id="test-corpus",
        description="Test corpus",
        documents=[document],
    )


def _passing_report(document: CorpusDocument) -> dict[str, object]:
    return {
        "corpus_id": document.id,
        "status": "stale-status-is-recomputed",
        "input": {
            "filename": document.filename,
            "sha256": document.sha256,
            "byte_size": document.byte_size,
            "page_range": document.page_range,
            "profile": document.profile,
            "categories": document.categories,
        },
        "conversion": {"status": "success", "errors": []},
        "metrics": {
            "page_count": 1,
            "text_chars": 10,
            "geometry_coverage": 1.0,
            "empty_pages": [],
            "invalid_provenance": [],
            "collection_counts": {"tables": 0, "pictures": 0},
            "label_counts": {},
        },
    }


def _write_fixture(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "fixture.bin"
    path.write_bytes(content)
    return path
