from __future__ import annotations

from uuid import UUID

import pytest

from document_intelligence.citations.contracts import EvidenceState
from document_intelligence.core.tenancy import TenantContext
from document_intelligence.generation.contracts import CitationRepair, GenerationPrompt
from document_intelligence.generation.orchestration import (
    AnswerOrchestrator,
    AnswerPipelineConfig,
)
from document_intelligence.generation.service import AnswerService
from document_intelligence.retrieval.rerank import SearchHit, SearchHitRecord
from document_intelligence.retrieval.service import RetrievalService

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
CORPUS_ID = UUID("00000000-0000-4000-8000-000000000003")


class Embedder:
    def __init__(self, vector: tuple[float, ...] = (0.1, 0.2)) -> None:
        self.vector = vector
        self.questions: list[str] = []

    async def embed_query(self, text: str) -> tuple[float, ...]:
        self.questions.append(text)
        return self.vector


class Retriever:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.questions: list[str] = []

    async def lexical(self, query: object, tenant: TenantContext) -> list[SearchHit]:
        self.questions.append(query.question)
        assert tenant == tenant_context()
        return self.hits

    async def dense(self, query: object, tenant: TenantContext) -> list[SearchHit]:
        assert tenant == tenant_context()
        return []


class FailingRetriever:
    async def lexical(self, query: object, tenant: TenantContext) -> list[SearchHit]:
        raise RuntimeError("raw backend failure")

    async def dense(self, query: object, tenant: TenantContext) -> list[SearchHit]:
        return []


class Provider:
    async def generate(self, prompt: GenerationPrompt) -> object:
        assert prompt.question == "What is finality?"
        return {
            "state": "supported",
            "claims": [
                {
                    "text": "Finality uses a threshold.",
                    "evidence_ids": [prompt.evidence[0].evidence_id],
                }
            ],
        }

    async def repair(
        self,
        prompt: GenerationPrompt,
        draft: object,
        correction: CitationRepair,
    ) -> object:
        raise AssertionError("repair should not be called")


def tenant_context() -> TenantContext:
    return TenantContext(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=UUID("00000000-0000-4000-8000-000000000004"),
        allowed_corpus_ids=(CORPUS_ID,),
    )


def hit() -> SearchHit:
    return SearchHit(
        record=SearchHitRecord(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            corpus_id=CORPUS_ID,
            document_id="protocol",
            document_version_id=UUID("00000000-0000-4000-8000-000000000005"),
            chunk_id=UUID("00000000-0000-4000-8000-000000000006"),
            page_number=1,
            content="Finality uses a threshold.",
        ),
        score=1.0,
    )


def orchestrator(retriever: object) -> AnswerOrchestrator:
    return AnswerOrchestrator(
        query_embedder=Embedder(),
        retrieval_service=RetrievalService(retriever=retriever),
        answer_service=AnswerService(Provider()),
        config=AnswerPipelineConfig(index_version="chunks-v1", pipeline_version="answers-v1"),
    )


@pytest.mark.asyncio
async def test_orchestrator_retrieves_before_emitting_validated_answer() -> None:
    events = [
        event
        async for event in orchestrator(Retriever([hit()])).stream(
            tenant=tenant_context(), question="What is finality?"
        )
    ]

    assert [event.event for event in events] == ["status", "status", "status", "answer"]
    assert [event.data["stage"] for event in events[:3]] == [
        "retrieval_started",
        "evidence_selected",
        "generation_started",
    ]
    assert events[-1].data["state"] == EvidenceState.SUPPORTED
    assert events[-1].data["claims"][0]["citations"][0]["page_number"] == 1


@pytest.mark.asyncio
async def test_orchestrator_streams_safe_retrieval_error_without_raw_detail() -> None:
    events = [
        event
        async for event in orchestrator(FailingRetriever()).stream(
            tenant=tenant_context(), question="What is finality?"
        )
    ]

    assert [event.event for event in events] == ["status", "error"]
    encoded = "".join(event.encode() for event in events)
    assert "raw backend failure" not in encoded
    assert "retrieval_failed" in encoded
