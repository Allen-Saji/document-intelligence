from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from document_intelligence.retrieval.rerank import SearchHit


class PackedEvidence(BaseModel):
    """A server-owned evidence reference for a future answer-generation boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(pattern=r"^ev_[0-9a-f-]{36}$")
    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    document_version_id: str
    chunk_id: str
    page_number: int = Field(ge=1)
    block_type: str
    content: str = Field(min_length=1)


class EvidencePacket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[PackedEvidence, ...]
    character_count: int = Field(ge=0)
    omitted_candidate_count: int = Field(ge=0)


def pack_evidence(hits: Sequence[SearchHit], *, character_budget: int = 12_000) -> EvidencePacket:
    """Pack whole chunks only, preserving original evidence text and page provenance."""

    if character_budget < 1:
        raise ValueError("character_budget must be positive")
    items: list[PackedEvidence] = []
    used = 0
    for hit in hits:
        content = hit.record.content
        if len(content) > character_budget - used:
            continue
        items.append(
            PackedEvidence(
                evidence_id=f"ev_{hit.record.chunk_id}",
                document_id=hit.record.document_id,
                document_version_id=str(hit.record.document_version_id),
                chunk_id=str(hit.record.chunk_id),
                page_number=hit.record.page_number,
                block_type=hit.record.block_type,
                content=content,
            )
        )
        used += len(content)
    return EvidencePacket(
        items=tuple(items), character_count=used, omitted_candidate_count=len(hits) - len(items)
    )
