from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.retrieval.fusion import (
    reciprocal_rank_fusion,
    select_source_diverse_hits,
)
from document_intelligence.retrieval.packing import EvidencePacket, pack_evidence
from document_intelligence.retrieval.query import HybridQueryInput
from document_intelligence.retrieval.rerank import (
    SearchHit,
    SemanticReranker,
    rerank_hits,
    validate_tenant_hits,
)

QUOTED_TERM_PATTERN = re.compile(r'"([^"\n]+)"|`([^`\n]+)`')
TECHNICAL_TERM_PATTERN = re.compile(r"\b(?:[A-Z]{2,}(?:[._/-][A-Z0-9]+)*|[A-Za-z]+\d[\w./:-]*)\b")


class RetrievalRequest(BaseModel):
    """Trusted retrieval input; tenant scope is derived by the authenticated caller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant: TenantContext
    question: str = Field(min_length=1, max_length=4_000)
    query_vector: tuple[float, ...] = Field(min_length=1)
    standalone_question: str | None = Field(default=None, min_length=1, max_length=4_000)
    conversation: tuple[str, ...] = ()
    exact_terms: tuple[str, ...] = ()
    lexical_candidates: int = Field(default=50, ge=1, le=500)
    dense_candidates: int = Field(default=50, ge=1, le=500)
    context_character_budget: int = Field(default=12_000, ge=1, le=100_000)
    source_limit: int = Field(default=12, ge=1, le=100)
    per_document_limit: int = Field(default=2, ge=1, le=20)
    index_version: str = Field(min_length=1, max_length=120)
    pipeline_version: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def require_unique_exact_terms(self) -> RetrievalRequest:
        normalized = [term.casefold() for term in self.exact_terms]
        if len(normalized) != len(set(normalized)):
            raise ValueError("exact_terms must not contain duplicates")
        return self


class PreparedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    search_question: str = Field(min_length=1)
    exact_terms: tuple[str, ...]


class RetrievalExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    search_question: str
    exact_terms: tuple[str, ...]
    lexical_candidate_count: int = Field(ge=0)
    dense_candidate_count: int = Field(ge=0)
    fused_candidate_count: int = Field(ge=0)
    diverse_candidate_count: int = Field(ge=0)
    index_version: str
    pipeline_version: str


class CandidateTrace(BaseModel):
    """Content-free ranking trace that lets an authorized operator replay retrieval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: UUID
    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    page_number: int = Field(ge=1)
    lexical_rank: int | None = Field(default=None, ge=1)
    dense_rank: int | None = Field(default=None, ge=1)
    fused_rank: int = Field(ge=1)
    reranked_rank: int = Field(ge=1)


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence: EvidencePacket
    explanation: RetrievalExplanation
    candidates: tuple[CandidateTrace, ...]


class CandidateRetriever(Protocol):
    async def lexical(
        self, query: HybridQueryInput, tenant: TenantContext
    ) -> Sequence[SearchHit]: ...

    async def dense(
        self, query: HybridQueryInput, tenant: TenantContext
    ) -> Sequence[SearchHit]: ...


class ConversationRewriter(Protocol):
    async def rewrite(self, conversation: Sequence[str], question: str) -> str: ...


class ContextExpander(Protocol):
    async def expand(
        self, hits: Sequence[SearchHit], tenant: TenantContext
    ) -> Sequence[SearchHit]: ...


class RetrievalService:
    """Run independent tenant-scoped retrieval branches and construct an evidence packet."""

    def __init__(
        self,
        *,
        retriever: CandidateRetriever,
        semantic_reranker: SemanticReranker | None = None,
        conversation_rewriter: ConversationRewriter | None = None,
        context_expander: ContextExpander | None = None,
    ) -> None:
        self._retriever = retriever
        self._semantic_reranker = semantic_reranker
        self._conversation_rewriter = conversation_rewriter
        self._context_expander = context_expander

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        prepared = await self._prepare_query(request)
        query = HybridQueryInput(
            question=prepared.search_question,
            query_vector=request.query_vector,
            lexical_candidates=request.lexical_candidates,
            dense_candidates=request.dense_candidates,
        )
        lexical, dense = await asyncio.gather(
            self._retriever.lexical(query, request.tenant),
            self._retriever.dense(query, request.tenant),
        )
        lexical_hits = list(lexical)
        dense_hits = list(dense)
        validate_tenant_hits(lexical_hits, request.tenant)
        validate_tenant_hits(dense_hits, request.tenant)
        fused = reciprocal_rank_fusion((lexical_hits, dense_hits))
        reranked = await self._rerank(prepared, fused)
        expanded = await self._expand(reranked, request.tenant)
        diverse = select_source_diverse_hits(
            expanded,
            per_document_limit=request.per_document_limit,
            limit=request.source_limit,
        )
        return RetrievalResult(
            evidence=pack_evidence(diverse, character_budget=request.context_character_budget),
            explanation=RetrievalExplanation(
                search_question=prepared.search_question,
                exact_terms=prepared.exact_terms,
                lexical_candidate_count=len(lexical_hits),
                dense_candidate_count=len(dense_hits),
                fused_candidate_count=len(fused),
                diverse_candidate_count=len(diverse),
                index_version=request.index_version,
                pipeline_version=request.pipeline_version,
            ),
            candidates=_candidate_traces(lexical_hits, dense_hits, fused, reranked),
        )

    async def _rerank(self, prepared: PreparedQuery, hits: Sequence[SearchHit]) -> list[SearchHit]:
        if self._semantic_reranker is None:
            return rerank_hits(list(hits), list(prepared.exact_terms))
        return await self._semantic_reranker.rerank(prepared.search_question, hits)

    async def _prepare_query(self, request: RetrievalRequest) -> PreparedQuery:
        if request.standalone_question is not None or not request.conversation:
            return prepare_query(request)
        if self._conversation_rewriter is None:
            return prepare_query(request)
        rewritten = await self._conversation_rewriter.rewrite(
            request.conversation, request.question
        )
        return prepare_query(request.model_copy(update={"standalone_question": rewritten}))

    async def _expand(self, hits: Sequence[SearchHit], tenant: TenantContext) -> list[SearchHit]:
        if self._context_expander is None:
            return list(hits)
        expanded = list(await self._context_expander.expand(hits, tenant))
        validate_tenant_hits(expanded, tenant)
        return expanded


def prepare_query(request: RetrievalRequest) -> PreparedQuery:
    """Choose a prepared follow-up question while retaining quoted and technical identifiers."""

    detected = [
        value
        for groups in QUOTED_TERM_PATTERN.findall(request.question)
        for value in groups
        if value
    ]
    detected.extend(TECHNICAL_TERM_PATTERN.findall(request.question))
    ordered_terms: list[str] = []
    known_terms: set[str] = set()
    for term in (*request.exact_terms, *detected):
        normalized = term.strip()
        key = normalized.casefold()
        if normalized and key not in known_terms:
            ordered_terms.append(normalized)
            known_terms.add(key)
    return PreparedQuery(
        search_question=request.standalone_question or request.question,
        exact_terms=tuple(ordered_terms),
    )


def _candidate_traces(
    lexical: Sequence[SearchHit],
    dense: Sequence[SearchHit],
    fused: Sequence[SearchHit],
    reranked: Sequence[SearchHit],
) -> tuple[CandidateTrace, ...]:
    lexical_ranks = _ranks(lexical)
    dense_ranks = _ranks(dense)
    reranked_ranks = _ranks(reranked)
    return tuple(
        CandidateTrace(
            chunk_id=hit.record.chunk_id,
            document_id=hit.record.document_id,
            page_number=hit.record.page_number,
            lexical_rank=lexical_ranks.get(hit.record.chunk_id),
            dense_rank=dense_ranks.get(hit.record.chunk_id),
            fused_rank=rank,
            reranked_rank=reranked_ranks[hit.record.chunk_id],
        )
        for rank, hit in enumerate(fused, start=1)
    )


def _ranks(hits: Sequence[SearchHit]) -> dict[UUID, int]:
    return {hit.record.chunk_id: rank for rank, hit in enumerate(hits, start=1)}
