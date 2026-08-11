from __future__ import annotations

from uuid import UUID

import pytest

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.retrieval.fusion import (
    reciprocal_rank_fusion,
    select_source_diverse_hits,
)
from document_intelligence.retrieval.rerank import SearchHit, SearchHitRecord, SemanticReranker
from document_intelligence.retrieval.service import (
    RetrievalRequest,
    RetrievalService,
    prepare_query,
)

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
CORPUS_ID = UUID("00000000-0000-4000-8000-000000000003")


def tenant() -> TenantContext:
    return TenantContext(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=UUID("00000000-0000-4000-8000-000000000004"),
        allowed_corpus_ids=(CORPUS_ID,),
    )


def hit(
    *,
    chunk: int,
    document: str = "protocol",
    content: str = "ordinary evidence",
    score: float = 1.0,
    organization_id: UUID = ORG_ID,
) -> SearchHit:
    return SearchHit(
        record=SearchHitRecord(
            organization_id=organization_id,
            workspace_id=WORKSPACE_ID,
            corpus_id=CORPUS_ID,
            document_id=document,
            document_version_id=UUID("00000000-0000-4000-8000-000000000010"),
            chunk_id=UUID(f"00000000-0000-4000-8000-{chunk:012d}"),
            page_number=chunk,
            content=content,
        ),
        score=score,
    )


class Retriever:
    def __init__(self, lexical: list[SearchHit], dense: list[SearchHit]) -> None:
        self.lexical_hits = lexical
        self.dense_hits = dense

    async def lexical(self, query: object, scope: TenantContext) -> list[SearchHit]:
        assert scope == tenant()
        return self.lexical_hits

    async def dense(self, query: object, scope: TenantContext) -> list[SearchHit]:
        assert scope == tenant()
        return self.dense_hits


class Scorer:
    async def score(self, question: str, passages: list[str]) -> list[float]:
        assert question == "Does `SVM-1` define FINALITY?"
        return [float(index) for index, _ in enumerate(passages)]


class Rewriter:
    async def rewrite(self, conversation: tuple[str, ...], question: str) -> str:
        assert conversation == ("Which protocol?",)
        assert question == "What about finality?"
        return "Does SVM-1 define FINALITY?"


def request(**updates: object) -> RetrievalRequest:
    values: dict[str, object] = {
        "tenant": tenant(),
        "question": "Does `SVM-1` define FINALITY?",
        "query_vector": (0.1, 0.2),
        "source_limit": 3,
        "per_document_limit": 1,
        "index_version": "chunks-v1",
        "pipeline_version": "retrieval-v1",
    }
    values.update(updates)
    return RetrievalRequest.model_validate(values)


def test_prepare_query_uses_standalone_question_without_losing_exact_identifiers() -> None:
    prepared = prepare_query(
        request(standalone_question="Does the protocol define finality?")
    )

    assert prepared.search_question == "Does the protocol define finality?"
    assert prepared.exact_terms == ("SVM-1", "FINALITY")


def test_reciprocal_rank_fusion_deduplicates_and_rewards_two_branch_agreement() -> None:
    lexical = [hit(chunk=1), hit(chunk=2)]
    dense = [hit(chunk=2), hit(chunk=3)]

    fused = reciprocal_rank_fusion((lexical, dense), rank_constant=1)

    assert [item.record.chunk_id for item in fused] == [
        hit(chunk=2).record.chunk_id,
        hit(chunk=1).record.chunk_id,
        hit(chunk=3).record.chunk_id,
    ]


def test_source_diversity_limits_chunks_from_one_document() -> None:
    selected = select_source_diverse_hits(
        [hit(chunk=1), hit(chunk=2), hit(chunk=3, document="audit")],
        per_document_limit=1,
        limit=3,
    )

    assert [item.record.document_id for item in selected] == ["protocol", "audit"]


@pytest.mark.asyncio
async def test_service_validates_fuses_reranks_and_packs_whole_evidence_chunks() -> None:
    service = RetrievalService(
        retriever=Retriever(
            lexical=[
                hit(chunk=1, content="Related context", score=0.9),
                hit(chunk=2, document="audit", content="SVM-1 FINALITY is defined", score=0.4),
            ],
            dense=[hit(chunk=2, document="audit", content="SVM-1 FINALITY is defined")],
        )
    )

    result = await service.retrieve(request(context_character_budget=100))

    assert result.explanation.fused_candidate_count == 2
    assert result.explanation.diverse_candidate_count == 2
    assert result.explanation.index_version == "chunks-v1"
    assert [item.document_id for item in result.evidence.items] == ["audit", "protocol"]
    assert result.evidence.items[0].evidence_id == "ev_00000000-0000-4000-8000-000000000002"
    assert result.evidence.character_count == len("SVM-1 FINALITY is defined") + len(
        "Related context"
    )
    assert result.candidates[0].dense_rank == 1


@pytest.mark.asyncio
async def test_service_rejects_foreign_hits_even_if_a_backend_returns_them() -> None:
    foreign = hit(
        chunk=1,
        organization_id=UUID("00000000-0000-4000-8000-000000000099"),
    )
    service = RetrievalService(retriever=Retriever(lexical=[foreign], dense=[]))

    with pytest.raises(ValueError, match="outside the active tenant"):
        await service.retrieve(request())


@pytest.mark.asyncio
async def test_service_can_use_an_injected_semantic_reranker() -> None:
    service = RetrievalService(
        retriever=Retriever(
            lexical=[hit(chunk=1, content="first"), hit(chunk=2, content="second")], dense=[]
        ),
        semantic_reranker=SemanticReranker(scorer=Scorer()),
    )

    result = await service.retrieve(request())

    assert result.evidence.items[0].content == "second"


@pytest.mark.asyncio
async def test_service_rewrites_a_follow_up_before_retrieval() -> None:
    service = RetrievalService(
        retriever=Retriever(lexical=[], dense=[]), conversation_rewriter=Rewriter()
    )

    result = await service.retrieve(
        request(question="What about finality?", conversation=("Which protocol?",))
    )

    assert result.explanation.search_question == "Does SVM-1 define FINALITY?"
