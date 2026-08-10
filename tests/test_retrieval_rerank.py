from uuid import UUID

import pytest

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.retrieval.rerank import (
    SearchHit,
    SearchHitRecord,
    rerank_hits,
    validate_tenant_hits,
)

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
OTHER_ORG_ID = UUID("00000000-0000-4000-8000-000000000099")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
CORPUS_ID = UUID("00000000-0000-4000-8000-000000000004")


def tenant() -> TenantContext:
    return TenantContext(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=UUID("00000000-0000-4000-8000-000000000003"),
        allowed_corpus_ids=(CORPUS_ID,),
    )


def hit(*, content: str, score: float, organization_id: UUID = ORG_ID) -> SearchHit:
    return SearchHit(
        record=SearchHitRecord(
            organization_id=organization_id,
            workspace_id=WORKSPACE_ID,
            corpus_id=CORPUS_ID,
            document_id="fixture-document",
            document_version_id=UUID("00000000-0000-4000-8000-000000000010"),
            chunk_id=UUID("00000000-0000-4000-8000-000000000011"),
            page_number=1,
            content=content,
        ),
        score=score,
    )


def test_reranker_promotes_exact_term_coverage_over_raw_score() -> None:
    hits = [
        hit(content="A related paragraph", score=0.99),
        hit(content="JavaScript code example", score=0.40),
    ]

    ranked = rerank_hits(hits, ["JavaScript", "code"])

    assert ranked[0].record.content == "JavaScript code example"


def test_tenant_validation_rejects_cross_organization_hits() -> None:
    with pytest.raises(ValueError, match="outside the active tenant"):
        validate_tenant_hits(
            [hit(content="forbidden", score=0.9, organization_id=OTHER_ORG_ID)], tenant()
        )


def test_tenant_validation_rejects_unsearchable_hits() -> None:
    candidate = hit(content="hidden", score=0.9)
    hidden_record = candidate.record.model_copy(update={"is_searchable": False})

    with pytest.raises(ValueError, match="outside the active tenant"):
        validate_tenant_hits([candidate.model_copy(update={"record": hidden_record})], tenant())
