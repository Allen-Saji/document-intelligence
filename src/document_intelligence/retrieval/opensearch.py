from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.retrieval.query import (
    HybridQueryInput,
    build_tenant_scoped_dense_query,
    build_tenant_scoped_lexical_query,
)
from document_intelligence.retrieval.rerank import SearchHit, search_hit_from_response


class OpenSearchSearchClient(Protocol):
    async def search(self, *, index: str, body: Mapping[str, object]) -> Mapping[str, object]: ...


class OpenSearchCandidateRetriever:
    """Translate application retrieval calls into independently filtered OpenSearch queries."""

    def __init__(self, *, client: OpenSearchSearchClient, index_name: str) -> None:
        if not index_name:
            raise ValueError("index_name must not be empty")
        self._client = client
        self._index_name = index_name

    async def lexical(
        self, query: HybridQueryInput, tenant: TenantContext
    ) -> Sequence[SearchHit]:
        response = await self._client.search(
            index=self._index_name,
            body=build_tenant_scoped_lexical_query(query, tenant),
        )
        return _search_hits(response)

    async def dense(self, query: HybridQueryInput, tenant: TenantContext) -> Sequence[SearchHit]:
        response = await self._client.search(
            index=self._index_name,
            body=build_tenant_scoped_dense_query(query, tenant),
        )
        return _search_hits(response)


def _search_hits(response: Mapping[str, object]) -> list[SearchHit]:
    hits_section = response.get("hits")
    if not isinstance(hits_section, Mapping):
        raise ValueError("OpenSearch response did not contain hits")
    raw_hits = hits_section.get("hits")
    if not isinstance(raw_hits, list):
        raise ValueError("OpenSearch response hits must be a list")
    parsed: list[SearchHit] = []
    for raw_hit in raw_hits:
        if not isinstance(raw_hit, Mapping):
            raise ValueError("OpenSearch response contained an invalid hit")
        source = raw_hit.get("_source")
        if not isinstance(source, dict):
            raise ValueError("OpenSearch response hit did not contain a source")
        score = raw_hit.get("_score")
        parsed.append(search_hit_from_response(source, score if isinstance(score, float) else None))
    return parsed
