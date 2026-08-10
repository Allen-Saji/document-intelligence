from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from document_intelligence.core.tenancy import TenantContext


class HybridQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=4_000)
    query_vector: tuple[float, ...] = Field(min_length=1)
    lexical_candidates: int = Field(default=50, ge=1, le=500)
    dense_candidates: int = Field(default=50, ge=1, le=500)


def _terms(values: Sequence[object]) -> list[str]:
    return [str(value) for value in values]


def build_tenant_scoped_hybrid_query(
    query: HybridQueryInput,
    tenant: TenantContext,
) -> dict[str, Any]:
    """Build an OpenSearch hybrid query with mandatory authorization pre-filters."""

    authorization_filter: dict[str, Any] = {
        "bool": {
            "filter": [
                {"term": {"organization_id": str(tenant.organization_id)}},
                {"term": {"workspace_id": str(tenant.workspace_id)}},
                {"terms": {"corpus_id": _terms(tenant.allowed_corpus_ids)}},
                {"term": {"is_searchable": True}},
            ]
        }
    }

    return {
        "size": max(query.lexical_candidates, query.dense_candidates),
        "_source": {
            "includes": [
                "organization_id",
                "workspace_id",
                "corpus_id",
                "document_id",
                "document_version_id",
                "chunk_id",
                "page_number",
                "block_type",
                "content",
                "is_searchable",
            ]
        },
        "query": {
            "hybrid": {
                "queries": [
                    {
                        "match": {
                            "content": {
                                "query": query.question,
                                "operator": "or",
                            }
                        }
                    },
                    {
                        "knn": {
                            "embedding": {
                                "vector": list(query.query_vector),
                                "k": query.dense_candidates,
                            }
                        }
                    },
                ],
                "filter": authorization_filter,
            }
        },
    }
