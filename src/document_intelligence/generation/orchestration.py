from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from time import perf_counter
from typing import Protocol

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.generation.service import (
    AnswerRequest,
    AnswerService,
    AnswerStreamEvent,
    stream_validated_answer,
)
from document_intelligence.retrieval.service import RetrievalRequest, RetrievalService
from document_intelligence.telemetry.tracing import start_safe_span


class QueryEmbedder(Protocol):
    async def embed_query(self, text: str) -> Sequence[float]: ...


class AnswerPipelineConfig(BaseModel):
    """Trusted answer pipeline defaults; callers must not supply these values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index_version: str = Field(min_length=1, max_length=120)
    pipeline_version: str = Field(min_length=1, max_length=120)
    lexical_candidates: int = Field(default=50, ge=1, le=500)
    dense_candidates: int = Field(default=50, ge=1, le=500)
    context_character_budget: int = Field(default=12_000, ge=1, le=100_000)
    source_limit: int = Field(default=12, ge=1, le=100)
    per_document_limit: int = Field(default=2, ge=1, le=20)


class AnswerOrchestrator:
    """Compose tenant-scoped retrieval with validated answer generation."""

    def __init__(
        self,
        *,
        query_embedder: QueryEmbedder,
        retrieval_service: RetrievalService,
        answer_service: AnswerService,
        config: AnswerPipelineConfig,
    ) -> None:
        self._query_embedder = query_embedder
        self._retrieval_service = retrieval_service
        self._answer_service = answer_service
        self._config = config
        self._tracer = trace.get_tracer(__name__)

    async def stream(
        self,
        *,
        tenant: TenantContext,
        question: str,
        conversation: Sequence[str] = (),
    ) -> AsyncIterator[AnswerStreamEvent]:
        started_at = perf_counter()
        with start_safe_span(
            self._tracer,
            "answer.pipeline",
            {
                "answer.index_version": self._config.index_version,
                "answer.pipeline_version": self._config.pipeline_version,
            },
        ) as span:
            yield AnswerStreamEvent(event="status", data={"stage": "retrieval_started"})
            try:
                query_vector = tuple(await self._query_embedder.embed_query(question))
                result = await self._retrieval_service.retrieve(
                    RetrievalRequest(
                        tenant=tenant,
                        question=question,
                        conversation=tuple(conversation),
                        query_vector=query_vector,
                        lexical_candidates=self._config.lexical_candidates,
                        dense_candidates=self._config.dense_candidates,
                        context_character_budget=self._config.context_character_budget,
                        source_limit=self._config.source_limit,
                        per_document_limit=self._config.per_document_limit,
                        index_version=self._config.index_version,
                        pipeline_version=self._config.pipeline_version,
                    )
                )
            except Exception:
                span.set_attribute("answer.error_code", "retrieval_failed")
                span.set_attribute("answer.duration_ms", _elapsed_ms(started_at))
                yield AnswerStreamEvent(
                    event="error",
                    data={
                        "code": "retrieval_failed",
                        "message": "Evidence retrieval is unavailable.",
                    },
                )
                return

            span.set_attribute("answer.evidence_count", len(result.evidence.items))
            span.set_attribute(
                "answer.omitted_candidate_count", result.evidence.omitted_candidate_count
            )
            yield AnswerStreamEvent(
                event="status",
                data={
                    "stage": "evidence_selected",
                    "evidence_count": len(result.evidence.items),
                    "omitted_candidate_count": result.evidence.omitted_candidate_count,
                },
            )

            async for event in stream_validated_answer(
                self._answer_service,
                AnswerRequest(tenant=tenant, question=question, evidence=result.evidence),
            ):
                if event.event == "answer":
                    span.set_attribute("answer.state", str(event.data.get("state", "")))
                    span.set_attribute("answer.claim_count", len(event.data.get("claims", ())))
                elif event.event == "error":
                    span.set_attribute("answer.error_code", str(event.data.get("code", "")))
                yield event
            span.set_attribute("answer.duration_ms", _elapsed_ms(started_at))


def _elapsed_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)
