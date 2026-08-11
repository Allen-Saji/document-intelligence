from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.retrieval.rerank import SearchHit


class NeighborRetriever(Protocol):
    async def neighbors(
        self,
        *,
        document_version_id: UUID,
        page_number: int,
        radius: int,
        tenant: TenantContext,
    ) -> Sequence[SearchHit]: ...


class AdjacentContextExpander:
    """Add authorized nearby evidence after ranking without replacing ranked passages."""

    def __init__(self, *, neighbors: NeighborRetriever, radius: int = 1) -> None:
        if radius < 1:
            raise ValueError("radius must be positive")
        self._neighbors = neighbors
        self._radius = radius

    async def expand(self, hits: Sequence[SearchHit], tenant: TenantContext) -> list[SearchHit]:
        expanded = list(hits)
        known = {hit.record.chunk_id for hit in expanded}
        for hit in hits:
            adjacent = await self._neighbors.neighbors(
                document_version_id=hit.record.document_version_id,
                page_number=hit.record.page_number,
                radius=self._radius,
                tenant=tenant,
            )
            for candidate in adjacent:
                if candidate.record.chunk_id not in known:
                    expanded.append(candidate)
                    known.add(candidate.record.chunk_id)
        return expanded
