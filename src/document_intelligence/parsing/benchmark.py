from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTENT_COLLECTIONS = ("texts", "tables", "pictures", "key_value_items")


class CorpusRights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["public-domain", "publicly-accessible-local-evaluation"]
    reference_url: str
    note: str

    @field_validator("reference_url")
    @classmethod
    def require_https_reference(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("rights reference URL must use HTTPS")
        return value


class ExpectedFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_page_count: int = Field(ge=1)
    min_text_chars: int = Field(default=1, ge=0)
    min_geometry_coverage: float = Field(default=0.95, ge=0, le=1)
    max_empty_pages: int = Field(default=0, ge=0)
    max_invalid_provenance: int = Field(default=0, ge=0)
    min_tables: int = Field(default=0, ge=0)
    min_pictures: int = Field(default=0, ge=0)
    min_labels: dict[str, int] = Field(default_factory=dict)


class ManualReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages: list[int] = Field(min_length=1)
    checks: list[
        Literal[
            "reading-order",
            "text-fidelity",
            "table-structure",
            "code-fidelity",
            "formula-fidelity",
            "ocr-fidelity",
            "bounding-boxes",
        ]
    ] = Field(min_length=1)
    note: str

    @field_validator("pages")
    @classmethod
    def require_positive_unique_pages(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("manual review pages must be one-based positive integers")
        if len(value) != len(set(value)):
            raise ValueError("manual review pages must be unique")
        return value


class CorpusDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str
    filename: str
    source_url: str
    source_page: str
    sha256: str
    byte_size: int = Field(gt=0)
    categories: list[
        Literal[
            "born-digital",
            "scanned",
            "multi-column",
            "table-heavy",
            "code-heavy",
            "formula-heavy",
            "mixed-text-image",
        ]
    ] = Field(min_length=1)
    profile: Literal["standard", "full-page-ocr", "code-formula-enriched"] = "standard"
    page_range: tuple[int, int] | None = None
    rights: CorpusRights
    expected: ExpectedFeatures
    manual_review: list[ManualReview] = Field(min_length=1)

    @field_validator("filename")
    @classmethod
    def require_safe_pdf_filename(cls, value: str) -> str:
        if Path(value).name != value or Path(value).suffix.lower() != ".pdf":
            raise ValueError("filename must be a plain PDF filename")
        return value

    @field_validator("source_url", "source_page")
    @classmethod
    def require_https_source(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("source URLs must use HTTPS")
        return value

    @field_validator("sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if not SHA256_PATTERN.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def validate_page_scope(self) -> CorpusDocument:
        if self.page_range is not None:
            start, end = self.page_range
            if start < 1 or end < start:
                raise ValueError("page_range must contain one-based start and end pages")
            selected_pages = end - start + 1
            if self.expected.min_page_count > selected_pages:
                raise ValueError("min_page_count cannot exceed the selected page range")
        return self


class ParserCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str
    documents: list[CorpusDocument] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_documents(self) -> ParserCorpus:
        ids = [document.id for document in self.documents]
        filenames = [document.filename for document in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("document IDs must be unique")
        if len(filenames) != len(set(filenames)):
            raise ValueError("document filenames must be unique")
        return self


def load_corpus(path: Path) -> ParserCorpus:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ParserCorpus.model_validate(payload)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_fixture(document: CorpusDocument, path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"missing corpus fixture: {path}")
    actual_size = path.stat().st_size
    if actual_size != document.byte_size:
        raise ValueError(
            f"size mismatch for {document.id}: expected {document.byte_size}, got {actual_size}"
        )
    actual_hash = sha256_file(path)
    if actual_hash != document.sha256:
        raise ValueError(
            f"SHA-256 mismatch for {document.id}: expected {document.sha256}, got {actual_hash}"
        )


def fetch_fixture(document: CorpusDocument, data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / document.filename
    if target.exists():
        verify_fixture(document, target)
        return target

    partial = data_dir / f"{document.filename}.part"
    if partial.exists():
        raise FileExistsError(
            f"partial download already exists at {partial}; inspect it before retrying"
        )

    request = urllib.request.Request(
        document.source_url,
        headers={"User-Agent": "document-intelligence-parser-benchmark/0.1"},
    )
    downloaded = 0
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("xb") as output:
        if response.geturl().split(":", maxsplit=1)[0] != "https":
            raise ValueError("fixture download redirected away from HTTPS")
        while block := response.read(1024 * 1024):
            downloaded += len(block)
            if downloaded > document.byte_size:
                raise ValueError(f"download for {document.id} exceeded its pinned byte size")
            output.write(block)

    verify_fixture(document, partial)
    partial.rename(target)
    return target


def analyze_docling_export(exported: dict[str, Any]) -> dict[str, Any]:
    pages = exported.get("pages", {})
    page_sizes = {int(page_number): page.get("size", {}) for page_number, page in pages.items()}
    page_metrics: dict[int, dict[str, Any]] = {
        page_number: {"item_count": 0, "text_chars": 0, "label_counts": Counter()}
        for page_number in page_sizes
    }
    labels: Counter[str] = Counter()
    collection_counts: Counter[str] = Counter()
    text_chars = 0
    items_with_provenance = 0
    items_with_valid_geometry = 0
    provenance_entry_count = 0
    valid_geometry_entry_count = 0
    invalid_provenance: list[dict[str, Any]] = []

    for collection in CONTENT_COLLECTIONS:
        items = exported.get(collection, [])
        collection_counts[collection] = len(items)
        for item_index, item in enumerate(items):
            label = str(item.get("label", collection.removesuffix("s")))
            labels[label] += 1
            text = item.get("text")
            if isinstance(text, str):
                text_chars += len(text)
            provenance = item.get("prov", [])
            if provenance:
                items_with_provenance += 1
            valid_geometry = False
            item_pages: set[int] = set()
            for provenance_index, entry in enumerate(provenance):
                provenance_entry_count += 1
                page_number = entry.get("page_no")
                bbox = entry.get("bbox")
                reason = _invalid_provenance_reason(page_number, bbox, page_sizes)
                if reason is None:
                    valid_geometry = True
                    valid_geometry_entry_count += 1
                    item_pages.add(page_number)
                else:
                    invalid_provenance.append(
                        {
                            "collection": collection,
                            "item_index": item_index,
                            "provenance_index": provenance_index,
                            "reason": reason,
                        }
                    )
            if valid_geometry:
                items_with_valid_geometry += 1
            for page_number in item_pages:
                page_metrics[page_number]["item_count"] += 1
                page_metrics[page_number]["text_chars"] += len(text) if isinstance(text, str) else 0
                page_metrics[page_number]["label_counts"][label] += 1

    geometry_coverage = (
        valid_geometry_entry_count / provenance_entry_count if provenance_entry_count else 0.0
    )
    body_children = exported.get("body", {}).get("children", [])
    serializable_page_metrics = {
        str(page_number): {
            "item_count": metrics["item_count"],
            "text_chars": metrics["text_chars"],
            "label_counts": dict(sorted(metrics["label_counts"].items())),
        }
        for page_number, metrics in sorted(page_metrics.items())
    }
    empty_pages = [
        page_number
        for page_number, metrics in sorted(page_metrics.items())
        if metrics["item_count"] == 0
    ]

    return {
        "page_count": len(pages),
        "text_chars": text_chars,
        "collection_counts": dict(sorted(collection_counts.items())),
        "label_counts": dict(sorted(labels.items())),
        "body_child_count": len(body_children),
        "pages": serializable_page_metrics,
        "empty_pages": empty_pages,
        "items_with_provenance": items_with_provenance,
        "items_with_valid_geometry": items_with_valid_geometry,
        "provenance_entries": provenance_entry_count,
        "valid_geometry_entries": valid_geometry_entry_count,
        "geometry_coverage": round(geometry_coverage, 6),
        "invalid_provenance": invalid_provenance,
    }


def evaluate_features(
    metrics: dict[str, Any], expected: ExpectedFeatures
) -> dict[str, dict[str, Any]]:
    label_counts = metrics["label_counts"]
    collection_counts = metrics["collection_counts"]
    checks: dict[str, dict[str, Any]] = {
        "page_count": _minimum_check(metrics["page_count"], expected.min_page_count),
        "text_chars": _minimum_check(metrics["text_chars"], expected.min_text_chars),
        "geometry_coverage": _minimum_check(
            metrics["geometry_coverage"], expected.min_geometry_coverage
        ),
        "empty_pages": _maximum_check(len(metrics["empty_pages"]), expected.max_empty_pages),
        "invalid_provenance": _maximum_check(
            len(metrics["invalid_provenance"]), expected.max_invalid_provenance
        ),
        "tables": _minimum_check(collection_counts.get("tables", 0), expected.min_tables),
        "pictures": _minimum_check(collection_counts.get("pictures", 0), expected.min_pictures),
    }
    for label, minimum in sorted(expected.min_labels.items()):
        checks[f"label:{label}"] = _minimum_check(label_counts.get(label, 0), minimum)
    return checks


def checks_passed(checks: dict[str, dict[str, Any]]) -> bool:
    return all(check["passed"] for check in checks.values())


def _minimum_check(actual: int | float, minimum: int | float) -> dict[str, Any]:
    return {"actual": actual, "minimum": minimum, "passed": actual >= minimum}


def _maximum_check(actual: int | float, maximum: int | float) -> dict[str, Any]:
    return {"actual": actual, "maximum": maximum, "passed": actual <= maximum}


def _invalid_provenance_reason(
    page_number: Any, bbox: Any, page_sizes: dict[int, dict[str, Any]]
) -> str | None:
    if not isinstance(page_number, int) or page_number not in page_sizes:
        return "unknown-page"
    if not isinstance(bbox, dict):
        return "missing-bounding-box"
    try:
        left = float(bbox["l"])
        top = float(bbox["t"])
        right = float(bbox["r"])
        bottom = float(bbox["b"])
        width = float(page_sizes[page_number]["width"])
        height = float(page_sizes[page_number]["height"])
    except (KeyError, TypeError, ValueError):
        return "malformed-bounding-box"
    if not (0 <= left < right <= width):
        return "horizontal-bounds"
    if not (0 <= min(top, bottom) < max(top, bottom) <= height):
        return "vertical-bounds"
    return None
