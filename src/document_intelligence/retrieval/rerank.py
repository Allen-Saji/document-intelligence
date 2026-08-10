from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.evaluation.retrieval import EvidenceLocation


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
