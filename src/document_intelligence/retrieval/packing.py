from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from document_intelligence.provenance import PageRegion
from document_intelligence.retrieval.rerank import SearchHit


class PackedEvidence(BaseModel):
    """A server-owned evidence reference for a future answer-generation boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(pattern=r"^ev_[0-9a-f-]{36}$")
    organization_id: UUID
    workspace_id: UUID
    corpus_id: UUID
    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    document_version_id: UUID
    chunk_id: UUID
    page_number: int = Field(ge=1)
    block_type: str
    content: str = Field(min_length=1)
    source_region: PageRegion | None = None


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
                organization_id=hit.record.organization_id,
                workspace_id=hit.record.workspace_id,
                corpus_id=hit.record.corpus_id,
                document_id=hit.record.document_id,
                document_version_id=hit.record.document_version_id,
                chunk_id=hit.record.chunk_id,
                page_number=hit.record.page_number,
                block_type=hit.record.block_type,
                content=content,
                source_region=hit.record.source_region,
            )
        )
        used += len(content)
    return EvidencePacket(
        items=tuple(items), character_count=used, omitted_candidate_count=len(hits) - len(items)
    )
