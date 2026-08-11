from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from document_intelligence.citations.contracts import EvidenceState, ValidatedAnswer


class AnswerCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    question: str = Field(min_length=1)
    expected_state: EvidenceState
    required_claim_phrases: tuple[str, ...] = ()
    prohibited_phrases: tuple[str, ...] = ()
    required_evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_assertions_for_answer_cases(self) -> AnswerCase:
        if self.expected_state in {
            EvidenceState.SUPPORTED,
            EvidenceState.PARTIAL,
            EvidenceState.CONFLICTING,
        } and not (self.required_claim_phrases or self.required_evidence_ids):
            raise ValueError("answer-bearing cases require claim or citation assertions")
        return self


class AnswerEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    passed: bool
    state_matches: bool
    required_claim_phrases_found: tuple[str, ...]
    required_claim_phrases_missing: tuple[str, ...]
    prohibited_phrases_found: tuple[str, ...]
    required_evidence_ids_found: tuple[str, ...]
    required_evidence_ids_missing: tuple[str, ...]


class AnswerEvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_count: int = Field(ge=1)
    pass_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)


def evaluate_answer(case: AnswerCase, answer: ValidatedAnswer) -> AnswerEvaluation:
    claim_text = "\n".join(claim.text for claim in answer.claims).casefold()
    cited_ids = {evidence.evidence_id for claim in answer.claims for evidence in claim.evidence}
    found_required_phrases = tuple(
        phrase for phrase in case.required_claim_phrases if phrase.casefold() in claim_text
    )
    missing_required_phrases = tuple(
        phrase for phrase in case.required_claim_phrases if phrase.casefold() not in claim_text
    )
    found_prohibited_phrases = tuple(
        phrase for phrase in case.prohibited_phrases if phrase.casefold() in claim_text
    )
    found_required_evidence = tuple(
        evidence_id for evidence_id in case.required_evidence_ids if evidence_id in cited_ids
    )
    missing_required_evidence = tuple(
        evidence_id for evidence_id in case.required_evidence_ids if evidence_id not in cited_ids
    )
    state_matches = answer.state == case.expected_state
    passed = (
        state_matches
        and not missing_required_phrases
        and not found_prohibited_phrases
        and not missing_required_evidence
    )
    return AnswerEvaluation(
        case_id=case.id,
        passed=passed,
        state_matches=state_matches,
        required_claim_phrases_found=found_required_phrases,
        required_claim_phrases_missing=missing_required_phrases,
        prohibited_phrases_found=found_prohibited_phrases,
        required_evidence_ids_found=found_required_evidence,
        required_evidence_ids_missing=missing_required_evidence,
    )


def summarize_answer_evaluations(
    evaluations: tuple[AnswerEvaluation, ...],
) -> AnswerEvaluationSummary:
    if not evaluations:
        raise ValueError("at least one answer evaluation is required")
    pass_count = sum(evaluation.passed for evaluation in evaluations)
    return AnswerEvaluationSummary(
        case_count=len(evaluations),
        pass_count=pass_count,
        pass_rate=pass_count / len(evaluations),
    )
