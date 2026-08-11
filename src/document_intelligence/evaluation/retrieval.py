from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    page_number: int = Field(ge=1)
    block_type: Literal["text", "table", "code", "formula", "picture"] = "text"
    anchor: str | None = Field(default=None, min_length=1)


class RetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    question: str = Field(min_length=1)
    category: Literal[
        "factual",
        "synthesis",
        "identifier",
        "unanswerable",
        "conflicting",
        "follow-up",
    ]
    expected_state: Literal["supported", "insufficient", "conflicting"]
    gold_evidence: list[EvidenceLocation] = Field(default_factory=list)
    exact_terms: list[str] = Field(default_factory=list)
    conversation: list[str] = Field(default_factory=list)
    standalone_question: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_case_shape(self) -> RetrievalCase:
        if self.expected_state == "insufficient" and self.gold_evidence:
            raise ValueError("insufficient cases cannot contain gold evidence")
        if self.expected_state != "insufficient" and not self.gold_evidence:
            raise ValueError("supported and conflicting cases require gold evidence")
        if self.category == "unanswerable" and self.expected_state != "insufficient":
            raise ValueError("unanswerable cases must have insufficient state")
        if self.category == "conflicting" and self.expected_state != "conflicting":
            raise ValueError("conflicting cases must have conflicting state")
        if self.category == "follow-up":
            if not self.conversation or self.standalone_question is None:
                raise ValueError("follow-up cases require conversation and standalone question")
        elif self.conversation or self.standalone_question is not None:
            raise ValueError("conversation fields are only valid for follow-up cases")
        return self


class RetrievalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    corpus_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str = Field(min_length=1)
    cases: list[RetrievalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_cases(self) -> RetrievalDataset:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("retrieval case IDs must be unique")
        return self


def load_retrieval_dataset(path: Path) -> RetrievalDataset:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RetrievalDataset.model_validate(payload)


def recall_at_k(case: RetrievalCase, retrieved: list[EvidenceLocation], k: int) -> float | None:
    if not case.gold_evidence:
        return None
    expected = {(item.document_id, item.page_number) for item in case.gold_evidence}
    actual = {(item.document_id, item.page_number) for item in retrieved[:k]}
    return len(expected & actual) / len(expected)


def reciprocal_rank(case: RetrievalCase, retrieved: list[EvidenceLocation]) -> float | None:
    if not case.gold_evidence:
        return None
    expected = {(item.document_id, item.page_number) for item in case.gold_evidence}
    for rank, item in enumerate(retrieved, start=1):
        if (item.document_id, item.page_number) in expected:
            return 1 / rank
    return 0.0


def dataset_category_counts(dataset: RetrievalDataset) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in dataset.cases:
        counts[case.category] = counts.get(case.category, 0) + 1
    return dict(sorted(counts.items()))


class RetrievalMeasurement(BaseModel):
    """One replayable retrieval result for a labeled case and named pipeline variant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case: RetrievalCase
    pipeline: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    retrieved: tuple[EvidenceLocation, ...]
    latency_ms: float = Field(ge=0)


class RetrievalMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pipeline: str
    case_count: int = Field(ge=1)
    supported_case_count: int = Field(ge=0)
    mean_recall_at_5: float | None = Field(default=None, ge=0, le=1)
    mean_reciprocal_rank: float | None = Field(default=None, ge=0, le=1)
    mean_latency_ms: float = Field(ge=0)
    category_recall_at_5: dict[str, float] = Field(default_factory=dict)


def summarize_retrieval_measurements(
    measurements: list[RetrievalMeasurement],
) -> RetrievalMetrics:
    if not measurements:
        raise ValueError("at least one retrieval measurement is required")
    pipelines = {measurement.pipeline for measurement in measurements}
    if len(pipelines) != 1:
        raise ValueError("measurements must describe one pipeline")
    recalls = [
        recall_at_k(measurement.case, list(measurement.retrieved), 5)
        for measurement in measurements
    ]
    ranks = [
        reciprocal_rank(measurement.case, list(measurement.retrieved))
        for measurement in measurements
    ]
    supported_recalls = [value for value in recalls if value is not None]
    supported_ranks = [value for value in ranks if value is not None]
    category_values: dict[str, list[float]] = {}
    for measurement, recall in zip(measurements, recalls, strict=True):
        if recall is not None:
            category_values.setdefault(measurement.case.category, []).append(recall)
    return RetrievalMetrics(
        pipeline=pipelines.pop(),
        case_count=len(measurements),
        supported_case_count=len(supported_recalls),
        mean_recall_at_5=_mean(supported_recalls),
        mean_reciprocal_rank=_mean(supported_ranks),
        mean_latency_ms=sum(item.latency_ms for item in measurements) / len(measurements),
        category_recall_at_5={
            category: sum(values) / len(values)
            for category, values in sorted(category_values.items())
        },
    )


def candidate_beats_baseline(
    candidate: RetrievalMetrics,
    baseline: RetrievalMetrics,
    *,
    max_mean_latency_ms: float,
) -> bool:
    """Select additional retrieval complexity only for a measurable quality gain."""

    if candidate.case_count != baseline.case_count:
        raise ValueError("candidate and baseline must use the same number of cases")
    if candidate.mean_recall_at_5 is None or baseline.mean_recall_at_5 is None:
        return False
    if candidate.mean_reciprocal_rank is None or baseline.mean_reciprocal_rank is None:
        return False
    return (
        candidate.mean_recall_at_5 >= baseline.mean_recall_at_5
        and candidate.mean_reciprocal_rank > baseline.mean_reciprocal_rank
        and candidate.mean_latency_ms <= max_mean_latency_ms
    )


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None
