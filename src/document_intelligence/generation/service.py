from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from document_intelligence.citations.contracts import (
    EvidenceItem,
    EvidenceState,
    ModelAnswerDraft,
    ValidatedAnswer,
    validate_and_resolve_answer,
    validate_supplied_evidence,
)
from document_intelligence.core.tenancy import TenantContext
from document_intelligence.generation.contracts import (
    CitationRepair,
    GenerationPrompt,
    GenerationProviderError,
    PromptEvidence,
    StructuredGenerationProvider,
)
from document_intelligence.provenance import PageRegion
from document_intelligence.retrieval.packing import EvidencePacket, PackedEvidence


class AnswerRequest(BaseModel):
    """Trusted generation input produced after retrieval and authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant: TenantContext
    question: str = Field(min_length=1, max_length=4_000)
    evidence: EvidencePacket


class AnswerRun(BaseModel):
    """A validated terminal answer and whether one bounded citation repair was attempted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: ValidatedAnswer
    repair_attempted: bool = False


class PublicCitation(BaseModel):
    """Citation fields usable by an authorized client without exposing tenant internals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    document_version_id: str
    chunk_id: str
    page_number: int
    passage: str
    source_region: PageRegion | None = None


class PublicClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    citations: tuple[PublicCitation, ...]


class AnswerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: EvidenceState
    claims: tuple[PublicClaim, ...]
    missing_information: tuple[str, ...]


class AnswerStreamEvent(BaseModel):
    """One SSE event. Answer data exists only after full citation validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event: Literal["status", "answer", "error"]
    data: dict[str, Any]

    def encode(self) -> str:
        data = json.dumps(self.data, separators=(",", ":"), default=str)
        return f"event: {self.event}\ndata: {data}\n\n"


class AnswerService:
    def __init__(self, provider: StructuredGenerationProvider) -> None:
        self._provider = provider

    async def answer(self, request: AnswerRequest) -> AnswerRun:
        if not request.evidence.items:
            return AnswerRun(answer=_insufficient_answer())

        supplied = tuple(_evidence_item(item) for item in request.evidence.items)
        try:
            validate_supplied_evidence(
                supplied,
                organization_id=request.tenant.organization_id,
                workspace_id=request.tenant.workspace_id,
                allowed_corpus_ids=request.tenant.allowed_corpus_ids,
            )
        except ValueError:
            return AnswerRun(answer=_failed_answer())

        prompt = _prompt(request.question, request.evidence)
        try:
            draft = _draft(await self._generate(prompt))
        except (GenerationProviderError, ValidationError, ValueError, TypeError):
            return AnswerRun(answer=_failed_answer())

        try:
            return AnswerRun(answer=_validate(draft, supplied, request.tenant))
        except ValueError:
            return await self._repair(prompt, draft, supplied, request.tenant)

    async def _repair(
        self,
        prompt: GenerationPrompt,
        draft: ModelAnswerDraft,
        supplied: tuple[EvidenceItem, ...],
        tenant: TenantContext,
    ) -> AnswerRun:
        correction = CitationRepair(
            allowed_evidence_ids=tuple(item.evidence_id for item in supplied)
        )
        try:
            repaired = _draft(await self._repair_provider(prompt, draft, correction))
            answer = _validate(repaired, supplied, tenant)
        except (GenerationProviderError, ValidationError, ValueError, TypeError):
            return AnswerRun(answer=_failed_answer(), repair_attempted=True)
        return AnswerRun(answer=answer, repair_attempted=True)

    async def _generate(self, prompt: GenerationPrompt) -> object:
        try:
            return await self._provider.generate(prompt)
        except Exception as error:
            raise GenerationProviderError("generation provider request failed") from error

    async def _repair_provider(
        self,
        prompt: GenerationPrompt,
        draft: ModelAnswerDraft,
        correction: CitationRepair,
    ) -> object:
        try:
            return await self._provider.repair(prompt, draft, correction)
        except Exception as error:
            raise GenerationProviderError("generation provider repair failed") from error


async def stream_validated_answer(
    service: AnswerService, request: AnswerRequest
) -> AsyncIterator[AnswerStreamEvent]:
    """Emit only progress and a fully validated terminal answer or safe failure."""

    yield AnswerStreamEvent(event="status", data={"stage": "generation_started"})
    run = await service.answer(request)
    if run.answer.state == EvidenceState.FAILED:
        yield AnswerStreamEvent(
            event="error",
            data={"code": "generation_failed", "message": "Answer generation is unavailable."},
        )
        return
    yield AnswerStreamEvent(event="answer", data=answer_payload(run.answer).model_dump(mode="json"))


def _prompt(question: str, packet: EvidencePacket) -> GenerationPrompt:
    return GenerationPrompt(
        question=question,
        evidence=tuple(
            PromptEvidence(evidence_id=item.evidence_id, content=item.content)
            for item in packet.items
        ),
    )


def _evidence_item(item: PackedEvidence) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=item.evidence_id,
        organization_id=item.organization_id,
        workspace_id=item.workspace_id,
        corpus_id=item.corpus_id,
        document_version_id=item.document_version_id,
        chunk_id=item.chunk_id,
        page_number=item.page_number,
        passage=item.content,
        source_region=item.source_region,
    )


def _draft(value: object) -> ModelAnswerDraft:
    return ModelAnswerDraft.model_validate(value)


def _validate(
    draft: ModelAnswerDraft, supplied: tuple[EvidenceItem, ...], tenant: TenantContext
) -> ValidatedAnswer:
    return validate_and_resolve_answer(
        draft,
        supplied,
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        allowed_corpus_ids=tenant.allowed_corpus_ids,
    )


def _insufficient_answer() -> ValidatedAnswer:
    return ValidatedAnswer(
        state=EvidenceState.INSUFFICIENT,
        claims=(),
        missing_information=("No retrieved evidence was available to support an answer.",),
    )


def _failed_answer() -> ValidatedAnswer:
    return ValidatedAnswer(state=EvidenceState.FAILED, claims=(), missing_information=())


def answer_payload(answer: ValidatedAnswer) -> AnswerPayload:
    return AnswerPayload(
        state=answer.state,
        claims=tuple(
            PublicClaim(
                text=claim.text,
                citations=tuple(
                    PublicCitation(
                        evidence_id=item.evidence_id,
                        document_version_id=str(item.document_version_id),
                        chunk_id=str(item.chunk_id),
                        page_number=item.page_number,
                        passage=item.passage,
                        source_region=item.source_region,
                    )
                    for item in claim.evidence
                ),
            )
            for claim in answer.claims
        ),
        missing_information=answer.missing_information,
    )
