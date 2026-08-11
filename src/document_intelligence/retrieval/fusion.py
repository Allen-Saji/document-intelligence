from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from document_intelligence.retrieval.rerank import SearchHit


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[SearchHit]],
    *,
    rank_constant: int = 60,
    limit: int = 100,
) -> list[SearchHit]:
    """Fuse independent candidate lists without treating their raw scores as comparable."""

    if rank_constant < 1:
        raise ValueError("rank_constant must be positive")
    if limit < 1:
        raise ValueError("limit must be positive")
    fused: dict[UUID, SearchHit] = {}
    scores: dict[UUID, float] = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked, start=1):
            chunk_id = hit.record.chunk_id
            fused.setdefault(chunk_id, hit)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rank_constant + rank)
    return [
        fused[chunk_id].model_copy(update={"score": score})
        for chunk_id, score in sorted(scores.items(), key=lambda item: (-item[1], str(item[0])))[
            :limit
        ]
    ]


def select_source_diverse_hits(
    hits: Sequence[SearchHit], *, per_document_limit: int = 2, limit: int = 12
) -> list[SearchHit]:
    """Keep relevant evidence from one document from consuming the whole context budget."""

    if per_document_limit < 1:
        raise ValueError("per_document_limit must be positive")
    if limit < 1:
        raise ValueError("limit must be positive")
    selected: list[SearchHit] = []
    counts: dict[tuple[str, UUID], int] = {}
    for hit in hits:
        source = (hit.record.document_id, hit.record.document_version_id)
        if counts.get(source, 0) >= per_document_limit:
            continue
        counts[source] = counts.get(source, 0) + 1
        selected.append(hit)
        if len(selected) == limit:
            break
    return selected
