from uuid import UUID

import pytest
from pydantic import ValidationError

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.retrieval.query import (
    HybridQueryInput,
    build_tenant_scoped_dense_query,
    build_tenant_scoped_hybrid_query,
    build_tenant_scoped_lexical_query,
)

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000003")
CORPUS_ID = UUID("00000000-0000-4000-8000-000000000004")


def tenant() -> TenantContext:
    return TenantContext(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=ACTOR_ID,
        allowed_corpus_ids=(CORPUS_ID,),
    )


def test_hybrid_query_injects_all_authorization_filters() -> None:
    request = HybridQueryInput(question="Where is finality defined?", query_vector=(0.1, 0.2))

    query = build_tenant_scoped_hybrid_query(request, tenant())

    filters = query["query"]["hybrid"]["filter"]["bool"]["filter"]
    assert {"term": {"organization_id": str(ORG_ID)}} in filters
    assert {"term": {"workspace_id": str(WORKSPACE_ID)}} in filters
    assert {"terms": {"corpus_id": [str(CORPUS_ID)]}} in filters
    assert {"term": {"is_searchable": True}} in filters


def test_hybrid_query_contains_lexical_and_dense_branches() -> None:
    request = HybridQueryInput(question="What keys are privileged?", query_vector=(0.1, 0.2))

    query = build_tenant_scoped_hybrid_query(request, tenant())

    branches = query["query"]["hybrid"]["queries"]
    assert "match" in branches[0]
    assert "knn" in branches[1]
    assert "embedding" not in query["_source"]["includes"]


def test_lexical_and_dense_queries_share_authorization_filters() -> None:
    request = HybridQueryInput(question="What keys are privileged?", query_vector=(0.1, 0.2))

    lexical = build_tenant_scoped_lexical_query(request, tenant())
    dense = build_tenant_scoped_dense_query(request, tenant())

    lexical_filters = lexical["query"]["bool"]["filter"]
    dense_filter = dense["query"]["knn"]["embedding"]["filter"]
    assert lexical_filters == dense_filter


def test_hybrid_query_uses_larger_candidate_limit() -> None:
    request = HybridQueryInput(
        question="What keys are privileged?",
        query_vector=(0.1, 0.2),
        lexical_candidates=25,
        dense_candidates=75,
    )

    query = build_tenant_scoped_hybrid_query(request, tenant())

    assert query["size"] == 75


def test_hybrid_query_rejects_invalid_candidate_limits() -> None:
    with pytest.raises(ValidationError):
        HybridQueryInput(question="Question", query_vector=(0.1,), lexical_candidates=0)


def test_tenant_context_rejects_duplicate_corpus_ids() -> None:
    with pytest.raises(ValidationError):
        TenantContext(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            actor_id=ACTOR_ID,
            allowed_corpus_ids=(CORPUS_ID, CORPUS_ID),
        )


def test_tenant_context_rejects_empty_corpus_authorization() -> None:
    with pytest.raises(ValidationError):
        TenantContext(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            actor_id=ACTOR_ID,
            allowed_corpus_ids=(),
        )
