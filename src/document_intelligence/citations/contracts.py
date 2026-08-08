from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceState(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    CONFLICTING = "conflicting"
    INSUFFICIENT = "insufficient"
    FAILED = "failed"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(pattern=r"^ev_[a-zA-Z0-9_-]{8,128}$")
    organization_id: UUID
    workspace_id: UUID
    corpus_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    page_number: int = Field(ge=1)
    passage: str = Field(min_length=1)


class ClaimDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def citations_are_unique(self) -> "ClaimDraft":
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("claim evidence_ids must not contain duplicates")
        return self


class ModelAnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: EvidenceState
    claims: tuple[ClaimDraft, ...] = ()
    missing_information: tuple[str, ...] = ()

    @model_validator(mode="after")
    def state_matches_content(self) -> "ModelAnswerDraft":
        answer_states = {
            EvidenceState.SUPPORTED,
            EvidenceState.PARTIAL,
            EvidenceState.CONFLICTING,
        }
        if self.state in answer_states and not self.claims:
            raise ValueError("answer-bearing evidence states require cited claims")
        if self.state == EvidenceState.INSUFFICIENT:
            if self.claims:
                raise ValueError("insufficient answers must not contain material claims")
            if not self.missing_information:
                raise ValueError("insufficient answers must explain missing information")
        if self.state == EvidenceState.FAILED and self.claims:
            raise ValueError("failed answers must not contain material claims")
        return self


class ResolvedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    evidence: tuple[EvidenceItem, ...]


class ValidatedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: EvidenceState
    claims: tuple[ResolvedClaim, ...]
    missing_information: tuple[str, ...]


def validate_and_resolve_answer(
    draft: ModelAnswerDraft,
    supplied_evidence: tuple[EvidenceItem, ...],
    *,
    organization_id: UUID,
    workspace_id: UUID,
    allowed_corpus_ids: tuple[UUID, ...],
) -> ValidatedAnswer:
    """Resolve only evidence supplied to the model and authorized for the active tenant."""

    evidence_by_id = {item.evidence_id: item for item in supplied_evidence}
    if len(evidence_by_id) != len(supplied_evidence):
        raise ValueError("supplied evidence IDs must be unique")

    allowed_corpora = set(allowed_corpus_ids)
    unauthorized = [
        item.evidence_id
        for item in supplied_evidence
        if (
            item.organization_id != organization_id
            or item.workspace_id != workspace_id
            or item.corpus_id not in allowed_corpora
        )
    ]
    if unauthorized:
        raise ValueError("supplied evidence contains items outside the active tenant")

    resolved_claims: list[ResolvedClaim] = []
    for claim in draft.claims:
        unknown = [
            evidence_id for evidence_id in claim.evidence_ids if evidence_id not in evidence_by_id
        ]
        if unknown:
            raise ValueError("model cited evidence that was not supplied")
        resolved_claims.append(
            ResolvedClaim(
                text=claim.text,
                evidence=tuple(evidence_by_id[evidence_id] for evidence_id in claim.evidence_ids),
            )
        )

    return ValidatedAnswer(
        state=draft.state,
        claims=tuple(resolved_claims),
        missing_information=draft.missing_information,
    )
