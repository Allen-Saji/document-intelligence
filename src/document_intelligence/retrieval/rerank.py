from __future__ import annotations

from collections.abc import Sequence
from math import isfinite
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.evaluation.retrieval import EvidenceLocation
from document_intelligence.provenance import PageRegion


class SearchHitRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: UUID
    workspace_id: UUID
    corpus_id: UUID
    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    document_version_id: UUID
    chunk_id: UUID
    page_number: int = Field(ge=1)
    block_type: Literal["text", "table", "code", "formula", "picture"] = "text"
    content: str = Field(min_length=1)
    source_region: PageRegion | None = None
    is_searchable: bool = True


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record: SearchHitRecord
    score: float = Field(ge=0)

    @property
    def evidence(self) -> EvidenceLocation:
        return EvidenceLocation(
            document_id=self.record.document_id,
            page_number=self.record.page_number,
            block_type=self.record.block_type,
        )


class SemanticScorer(Protocol):
    async def score(self, question: str, passages: Sequence[str]) -> Sequence[float]: ...


class SemanticReranker:
    """Apply a model-backed score while keeping tenant and evidence validation outside the model."""

    def __init__(self, *, scorer: SemanticScorer) -> None:
        self._scorer = scorer

    async def rerank(self, question: str, hits: Sequence[SearchHit]) -> list[SearchHit]:
        scores = list(await self._scorer.score(question, [hit.record.content for hit in hits]))
        if len(scores) != len(hits) or any(not isfinite(score) for score in scores):
            raise ValueError("reranker returned invalid scores")
        return [
            hit
            for _, hit in sorted(
                zip(scores, hits, strict=True), key=lambda pair: pair[0], reverse=True
            )
        ]


def validate_tenant_hits(hits: list[SearchHit], tenant: TenantContext) -> None:
    for hit in hits:
        record = hit.record
        if (
            record.organization_id != tenant.organization_id
            or record.workspace_id != tenant.workspace_id
            or record.corpus_id not in tenant.allowed_corpus_ids
            or not record.is_searchable
        ):
            raise ValueError("search returned a hit outside the active tenant")


def _term_coverage(content: str, exact_terms: list[str]) -> float:
    if not exact_terms:
        return 0.0
    normalized_content = content.casefold()
    matched = sum(term.casefold() in normalized_content for term in exact_terms)
    return matched / len(exact_terms)


def rerank_hits(hits: list[SearchHit], exact_terms: list[str]) -> list[SearchHit]:
    return sorted(
        hits,
        key=lambda hit: (_term_coverage(hit.record.content, exact_terms), hit.score),
        reverse=True,
    )


def search_hit_from_response(source: dict[str, Any], score: float | None) -> SearchHit:
    return SearchHit(
        record=SearchHitRecord.model_validate(source),
        score=max(score or 0.0, 0.0),
    )
