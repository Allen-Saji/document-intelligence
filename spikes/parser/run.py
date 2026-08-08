from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import resource
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from document_intelligence.parsing.benchmark import (
    CorpusDocument,
    ParserCorpus,
    analyze_docling_export,
    checks_passed,
    evaluate_features,
    fetch_fixture,
    load_corpus,
    verify_fixture,
)

DEFAULT_MANIFEST = Path(__file__).with_name("corpus.json")
DEFAULT_DATA_DIR = Path("data/parser-corpus")
DEFAULT_OUTPUT_DIR = Path("artifacts/phase-0/parser")
DEFAULT_MODEL_CACHE = Path("model-cache")


def build_converter(profile: str) -> Any:
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            OcrMode,
            PdfPipelineOptions,
            RapidOcrOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        message = "Install Phase 0 dependencies with: uv sync --extra phase0 --group dev"
        raise RuntimeError(message) from exc

    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=True,
        do_code_enrichment=profile == "code-formula-enriched",
        do_formula_enrichment=profile == "code-formula-enriched",
        generate_page_images=True,
        images_scale=1.5,
    )
    if profile == "full-page-ocr":
        pipeline_options.ocr_options = RapidOcrOptions(
            mode=OcrMode.FULL_PAGE,
            lang=["english"],
            backend="torch",
        )

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )


def benchmark_document(
    document: CorpusDocument,
    pdf: Path,
    output_root: Path,
    converter: Any | None = None,
) -> dict[str, Any]:
    verify_fixture(document, pdf)
    active_converter = converter or build_converter(document.profile)
    page_range = document.page_range
    started = time.perf_counter()
    if page_range is None:
        result = active_converter.convert(pdf)
    else:
        result = active_converter.convert(pdf, page_range=page_range)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    exported = result.document.export_to_dict()
    metrics = analyze_docling_export(exported)
    checks = evaluate_features(metrics, document.expected)
    conversion_status = _enum_value(result.status)
    conversion_errors = [_model_dump(error) for error in result.errors]
    conversion_succeeded = conversion_status == "success" and not conversion_errors

    document_output = output_root / document.id
    pages_output = document_output / "pages"
    pages_output.mkdir(parents=True, exist_ok=True)
    _write_json(document_output / "document.json", exported)
    (document_output / "document.md").write_text(
        f"{result.document.export_to_markdown()}\n", encoding="utf-8"
    )
    rendered_pages = _save_page_images(result.document.pages, pages_output)
    geometry_overlays = _save_geometry_overlays(exported, pages_output)

    report = {
        "schema_version": 1,
        "corpus_id": document.id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": (
            "automated-pass" if conversion_succeeded and checks_passed(checks) else "automated-fail"
        ),
        "manual_review_status": "pending",
        "input": {
            "filename": document.filename,
            "sha256": document.sha256,
            "byte_size": document.byte_size,
            "page_range": document.page_range,
            "profile": document.profile,
            "categories": document.categories,
        },
        "runtime": {
            "elapsed_ms": elapsed_ms,
            "peak_rss_mb": _peak_rss_mb(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": _package_versions(),
        },
        "conversion": {
            "status": conversion_status,
            "errors": conversion_errors,
            "rendered_pages": rendered_pages,
            "geometry_overlays": geometry_overlays,
        },
        "metrics": metrics,
        "automated_checks": checks,
        "manual_review": [review.model_dump(mode="json") for review in document.manual_review],
    }
    _write_json(document_output / "report.json", report)
    return report


def run_corpus(
    corpus: ParserCorpus,
    selected_ids: list[str],
    data_dir: Path,
    output_dir: Path,
    fetch: bool,
) -> dict[str, Any]:
    selected = _select_documents(corpus, selected_ids)
    reports: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    converters: dict[str, Any] = {}

    for document in selected:
        try:
            pdf = fetch_fixture(document, data_dir) if fetch else data_dir / document.filename
            converter = converters.get(document.profile)
            if converter is None:
                converter = build_converter(document.profile)
                converters[document.profile] = converter
            reports.append(benchmark_document(document, pdf, output_dir, converter=converter))
        except Exception as exc:
            failure = {"document_id": document.id, "error": f"{type(exc).__name__}: {exc}"}
            failures.append(failure)
            print(json.dumps(failure, sort_keys=True), file=sys.stderr)

    summary = {
        "schema_version": 1,
        "corpus_id": corpus.id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "selected_documents": [document.id for document in selected],
        "automated_passes": [
            report["corpus_id"] for report in reports if report["status"] == "automated-pass"
        ],
        "automated_failures": [
            report["corpus_id"] for report in reports if report["status"] == "automated-fail"
        ],
        "execution_failures": failures,
        "manual_review_status": "pending" if reports else "not-started",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Docling Phase 0 corpus benchmark.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model-cache", type=Path, default=DEFAULT_MODEL_CACHE)
    parser.add_argument(
        "--document",
        action="append",
        default=[],
        help="Run one document ID. Repeat to select more than one. Defaults to the full corpus.",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Download missing fixtures and verify all fixture sizes and hashes.",
    )
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="Download and verify selected fixtures without running Docling.",
    )
    parser.add_argument(
        "--summarize-existing",
        action="store_true",
        help="Rebuild the corpus summary from existing per-document reports.",
    )
    args = parser.parse_args()

    corpus = load_corpus(args.manifest.resolve())
    selected = _select_documents(corpus, args.document)
    _configure_model_cache(args.model_cache.resolve())
    if args.summarize_existing:
        summary = summarize_existing(corpus, args.output_dir.resolve())
        print(json.dumps(summary, indent=2, sort_keys=True))
        return
    if args.fetch_only:
        for document in selected:
            path = fetch_fixture(document, args.data_dir.resolve())
            print(f"verified {document.id}: {path}")
        return

    summary = run_corpus(
        corpus=corpus,
        selected_ids=args.document,
        data_dir=args.data_dir.resolve(),
        output_dir=args.output_dir.resolve(),
        fetch=args.fetch,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["automated_failures"] or summary["execution_failures"]:
        raise SystemExit(1)


def summarize_existing(corpus: ParserCorpus, output_dir: Path) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    missing_reports: list[dict[str, str]] = []
    for document in corpus.documents:
        report_path = output_dir / document.id / "report.json"
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                _validate_existing_report(report, document)
                checks = evaluate_features(report["metrics"], document.expected)
                conversion = report["conversion"]
                report["status"] = (
                    "automated-pass"
                    if (
                        conversion["status"] == "success"
                        and not conversion["errors"]
                        and checks_passed(checks)
                    )
                    else "automated-fail"
                )
                reports.append(report)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                missing_reports.append(
                    {"document_id": document.id, "error": f"invalid report: {exc}"}
                )
        else:
            missing_reports.append(
                {"document_id": document.id, "error": f"missing report: {report_path}"}
            )

    manual_review_path = output_dir / "manual-review.json"
    manual_review_status = "pending"
    if manual_review_path.is_file():
        manual_review_status = json.loads(manual_review_path.read_text(encoding="utf-8")).get(
            "status", "unknown"
        )

    summary = {
        "schema_version": 1,
        "corpus_id": corpus.id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "selected_documents": [document.id for document in corpus.documents],
        "automated_passes": [
            report["corpus_id"] for report in reports if report["status"] == "automated-pass"
        ],
        "automated_failures": [
            report["corpus_id"] for report in reports if report["status"] == "automated-fail"
        ],
        "execution_failures": missing_reports,
        "manual_review_status": manual_review_status,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "summary.json", summary)
    return summary


def _validate_existing_report(report: dict[str, Any], document: CorpusDocument) -> None:
    report_input = report["input"]
    expected_input = {
        "filename": document.filename,
        "sha256": document.sha256,
        "byte_size": document.byte_size,
        "page_range": list(document.page_range) if document.page_range is not None else None,
        "profile": document.profile,
        "categories": document.categories,
    }
    mismatches = [
        field for field, expected in expected_input.items() if report_input.get(field) != expected
    ]
    if mismatches:
        raise ValueError(f"report input differs from manifest: {', '.join(mismatches)}")


def _select_documents(corpus: ParserCorpus, selected_ids: list[str]) -> list[CorpusDocument]:
    if not selected_ids:
        return corpus.documents
    by_id = {document.id: document for document in corpus.documents}
    unknown = sorted(set(selected_ids) - by_id.keys())
    if unknown:
        raise ValueError(f"unknown document IDs: {', '.join(unknown)}")
    return [by_id[document_id] for document_id in selected_ids]


def _configure_model_cache(cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache / "huggingface"))
    os.environ.setdefault("TORCH_HOME", str(cache / "torch"))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache / "xdg"))


def _save_page_images(pages: dict[int, Any], output_dir: Path) -> list[str]:
    rendered: list[str] = []
    for page_number, page in sorted(pages.items()):
        image = getattr(page, "image", None)
        if image is None:
            continue
        pil_image = image.pil_image
        filename = f"page-{int(page_number):04d}.png"
        pil_image.save(output_dir / filename, format="PNG")
        rendered.append(filename)
    return rendered


def _save_geometry_overlays(exported: dict[str, Any], output_dir: Path) -> list[str]:
    from PIL import Image, ImageDraw

    content_collections = ("texts", "tables", "pictures", "key_value_items")
    boxes_by_page: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    for collection in content_collections:
        for item in exported.get(collection, []):
            label = str(item.get("label", collection.removesuffix("s")))
            for provenance in item.get("prov", []):
                page_number = provenance.get("page_no")
                bbox = provenance.get("bbox")
                if isinstance(page_number, int) and isinstance(bbox, dict):
                    boxes_by_page.setdefault(page_number, []).append((label, bbox))

    overlays: list[str] = []
    for page_number, page in sorted(
        exported.get("pages", {}).items(), key=lambda pair: int(pair[0])
    ):
        numeric_page = int(page_number)
        source = output_dir / f"page-{numeric_page:04d}.png"
        if not source.is_file():
            continue
        image = Image.open(source).convert("RGB")
        draw = ImageDraw.Draw(image)
        page_width = float(page["size"]["width"])
        page_height = float(page["size"]["height"])
        scale_x = image.width / page_width
        scale_y = image.height / page_height

        for label, bbox in boxes_by_page.get(numeric_page, []):
            left = float(bbox["l"]) * scale_x
            right = float(bbox["r"]) * scale_x
            top = float(bbox["t"])
            bottom = float(bbox["b"])
            if bbox.get("coord_origin") == "BOTTOMLEFT":
                y1 = (page_height - max(top, bottom)) * scale_y
                y2 = (page_height - min(top, bottom)) * scale_y
            else:
                y1 = min(top, bottom) * scale_y
                y2 = max(top, bottom) * scale_y
            color = _label_color(label)
            draw.rectangle((left, y1, right, y2), outline=color, width=2)
            draw.text((left + 2, max(0, y1 - 12)), label, fill=color)

        filename = f"page-{numeric_page:04d}-geometry.png"
        image.save(output_dir / filename, format="PNG")
        overlays.append(filename)
    return overlays


def _label_color(label: str) -> tuple[int, int, int]:
    palette = {
        "code": (0, 92, 170),
        "formula": (143, 63, 152),
        "picture": (213, 94, 0),
        "table": (0, 130, 90),
        "section_header": (190, 35, 45),
    }
    return palette.get(label, (40, 90, 170))


def _package_versions() -> dict[str, str]:
    packages = ("docling", "docling-core", "docling-ibm-models", "rapidocr", "torch")
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(peak / divisor, 2)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(payload, indent=2, sort_keys=True)}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
