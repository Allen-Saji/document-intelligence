from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from collections import Counter
from itertools import pairwise
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
    diagram_pages: list[int] = Field(default_factory=list)

    @field_validator("diagram_pages")
    @classmethod
    def require_positive_unique_diagram_pages(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("diagram pages must be one-based positive integers")
        if len(value) != len(set(value)):
            raise ValueError("diagram pages must be unique")
        return value


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


def analyze_docling_export(
    exported: dict[str, Any], diagram_pages: list[int] | tuple[int, ...] = ()
) -> dict[str, Any]:
    pages = exported.get("pages", {})
    page_sizes = {int(page_number): page.get("size", {}) for page_number, page in pages.items()}
    page_metrics: dict[int, dict[str, Any]] = {
        page_number: {
            "item_count": 0,
            "text_chars": 0,
            "label_counts": Counter(),
            "picture_count": 0,
            "picture_text_count": 0,
            "reading_order_inversions": 0,
        }
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
    items_by_ref: dict[str, dict[str, Any]] = {}
    picture_boxes_by_page: dict[int, list[dict[str, float]]] = {}
    item_boxes_by_page: dict[str, list[tuple[int, dict[str, float]]]] = {}

    for collection in CONTENT_COLLECTIONS:
        items = exported.get(collection, [])
        collection_counts[collection] = len(items)
        for item_index, item in enumerate(items):
            label = str(item.get("label", collection.removesuffix("s")))
            labels[label] += 1
            self_ref = item.get("self_ref")
            if isinstance(self_ref, str):
                items_by_ref[self_ref] = {"collection": collection, "item": item}
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
                    normalized_bbox = _normalized_bbox(bbox)
                    if normalized_bbox is not None:
                        item_boxes_by_page.setdefault(str(self_ref), []).append(
                            (page_number, normalized_bbox)
                        )
                        if collection == "pictures":
                            picture_boxes_by_page.setdefault(page_number, []).append(
                                normalized_bbox
                            )
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
                if collection == "pictures":
                    page_metrics[page_number]["picture_count"] += 1

    for self_ref, item_data in items_by_ref.items():
        if item_data["collection"] != "texts":
            continue
        for page_number, text_bbox in item_boxes_by_page.get(self_ref, []):
            if any(
                _bbox_contains(picture_bbox, text_bbox)
                for picture_bbox in picture_boxes_by_page.get(page_number, [])
            ):
                page_metrics[page_number]["picture_text_count"] += 1

    for page_number, inversion_count in _reading_order_inversions(
        exported, items_by_ref, item_boxes_by_page, page_sizes
    ).items():
        page_metrics[page_number]["reading_order_inversions"] = inversion_count

    geometry_coverage = (
        valid_geometry_entry_count / provenance_entry_count if provenance_entry_count else 0.0
    )
    body = exported.get("body", [])
    body_children = body.get("children", []) if isinstance(body, dict) else body
    if not isinstance(body_children, list):
        body_children = []
    diagram_page_set = set(diagram_pages)
    serializable_page_metrics = {
        str(page_number): {
            "item_count": metrics["item_count"],
            "text_chars": metrics["text_chars"],
            "label_counts": dict(sorted(metrics["label_counts"].items())),
            "picture_count": metrics["picture_count"],
            "picture_text_count": metrics["picture_text_count"],
            "reading_order_inversions": metrics["reading_order_inversions"],
            "quality_reasons": _page_quality_reasons(
                page_number, metrics, page_number in diagram_page_set
            ),
        }
        for page_number, metrics in sorted(page_metrics.items())
    }
    empty_pages = [
        page_number
        for page_number, metrics in sorted(page_metrics.items())
        if metrics["item_count"] == 0
    ]
    quarantine_reasons = {
        page_number: page["quality_reasons"]
        for page_number, page in serializable_page_metrics.items()
        if page["quality_reasons"]
    }

    return {
        "page_count": len(pages),
        "text_chars": text_chars,
        "collection_counts": dict(sorted(collection_counts.items())),
        "label_counts": dict(sorted(labels.items())),
        "body_child_count": len(body_children),
        "pages": serializable_page_metrics,
        "empty_pages": empty_pages,
        "quarantined_pages": [int(page) for page in sorted(quarantine_reasons, key=int)],
        "quarantine_reasons": quarantine_reasons,
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


def _page_quality_reasons(
    page_number: int, metrics: dict[str, Any], is_declared_diagram_page: bool
) -> list[str]:
    reasons: list[str] = []
    if metrics["item_count"] == 0:
        reasons.append("empty-page")
    if is_declared_diagram_page and metrics["picture_count"] == 0:
        reasons.append("missing-picture-region")
    if metrics["picture_text_count"] > 0:
        reasons.append("text-inside-picture")
    if metrics["reading_order_inversions"] > 0:
        reasons.append("suspect-reading-order")
    return reasons


def _normalized_bbox(bbox: Any) -> dict[str, float] | None:
    if not isinstance(bbox, dict):
        return None
    try:
        left = float(bbox["l"])
        right = float(bbox["r"])
        top = float(bbox["t"])
        bottom = float(bbox["b"])
    except (KeyError, TypeError, ValueError):
        return None
    if bbox.get("coord_origin") == "BOTTOMLEFT":
        visual_top = -max(top, bottom)
        visual_bottom = -min(top, bottom)
    else:
        visual_top = min(top, bottom)
        visual_bottom = max(top, bottom)
    return {
        "left": min(left, right),
        "right": max(left, right),
        "top": visual_top,
        "bottom": visual_bottom,
    }


def _bbox_contains(outer: dict[str, float], inner: dict[str, float]) -> bool:
    center_x = (inner["left"] + inner["right"]) / 2
    center_y = (inner["top"] + inner["bottom"]) / 2
    outer_top = min(outer["top"], outer["bottom"])
    outer_bottom = max(outer["top"], outer["bottom"])
    return outer["left"] <= center_x <= outer["right"] and outer_top <= center_y <= outer_bottom


def _reading_order_inversions(
    exported: dict[str, Any],
    items_by_ref: dict[str, dict[str, Any]],
    item_boxes_by_page: dict[str, list[tuple[int, dict[str, float]]]],
    page_sizes: dict[int, dict[str, Any]],
) -> dict[int, int]:
    body = exported.get("body", [])
    if isinstance(body, dict):
        body = body.get("children", [])
    if not isinstance(body, list):
        return {}

    sequence_by_page: dict[int, list[dict[str, Any]]] = {}
    for order, child in enumerate(body):
        reference = child.get("$ref") if isinstance(child, dict) else None
        if not isinstance(reference, str) or reference not in items_by_ref:
            continue
        for page_number, bbox in item_boxes_by_page.get(reference, []):
            sequence_by_page.setdefault(page_number, []).append({"order": order, "bbox": bbox})

    inversions: dict[int, int] = {}
    for page_number, sequence in sequence_by_page.items():
        page_size = page_sizes.get(page_number, {})
        try:
            page_width = float(page_size["width"])
            page_height = float(page_size["height"])
        except (KeyError, TypeError, ValueError):
            continue
        count = 0
        for previous, current in pairwise(sequence):
            previous_bbox = previous["bbox"]
            current_bbox = current["bbox"]
            previous_center = (previous_bbox["left"] + previous_bbox["right"]) / 2
            current_center = (current_bbox["left"] + current_bbox["right"]) / 2
            horizontal_distance = abs(current_center - previous_center)
            same_column = horizontal_distance <= page_width * 0.2
            moved_up = current_bbox["top"] < previous_bbox["top"] - page_height * 0.05
            if same_column and moved_up:
                count += 1
        if count:
            inversions[page_number] = count
    return inversions
