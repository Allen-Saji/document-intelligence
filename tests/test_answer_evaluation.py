from __future__ import annotations

from uuid import UUID

import pytest

from document_intelligence.citations.contracts import (
    EvidenceItem,
    EvidenceState,
    ResolvedClaim,
    ValidatedAnswer,
)
from document_intelligence.evaluation.answers import (
    AnswerCase,
    evaluate_answer,
    summarize_answer_evaluations,
)


def answer() -> ValidatedAnswer:
    evidence = EvidenceItem(
        evidence_id="ev_00000000-0000-4000-8000-000000000001",
        organization_id=UUID("00000000-0000-4000-8000-000000000002"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000003"),
        corpus_id=UUID("00000000-0000-4000-8000-000000000004"),
        document_version_id=UUID("00000000-0000-4000-8000-000000000005"),
        chunk_id=UUID("00000000-0000-4000-8000-000000000006"),
        page_number=3,
        passage="Finality requires two votes.",
    )
    return ValidatedAnswer(
        state=EvidenceState.SUPPORTED,
        claims=(ResolvedClaim(text="Finality requires two votes.", evidence=(evidence,)),),
        missing_information=(),
    )


def test_answer_evaluator_scores_state_claims_and_citations() -> None:
    evaluation = evaluate_answer(
        AnswerCase(
            id="finality",
            question="What is finality?",
            expected_state=EvidenceState.SUPPORTED,
            required_claim_phrases=("two votes",),
            prohibited_phrases=("three votes",),
            required_evidence_ids=("ev_00000000-0000-4000-8000-000000000001",),
        ),
        answer(),
    )

    assert evaluation.passed is True
    assert evaluation.required_claim_phrases_missing == ()
    assert evaluation.required_evidence_ids_missing == ()


def test_answer_evaluator_reports_missing_assertions() -> None:
    evaluation = evaluate_answer(
        AnswerCase(
            id="wrong-finality",
            question="What is finality?",
            expected_state=EvidenceState.SUPPORTED,
            required_claim_phrases=("three votes",),
            prohibited_phrases=("two votes",),
            required_evidence_ids=("ev_missing",),
        ),
        answer(),
    )

    assert evaluation.passed is False
    assert evaluation.required_claim_phrases_missing == ("three votes",)
    assert evaluation.prohibited_phrases_found == ("two votes",)
    assert evaluation.required_evidence_ids_missing == ("ev_missing",)


def test_answer_evaluation_summary_counts_pass_rate() -> None:
    passed = evaluate_answer(
        AnswerCase(
            id="finality",
            question="What is finality?",
            expected_state=EvidenceState.SUPPORTED,
            required_claim_phrases=("two votes",),
        ),
        answer(),
    )
    failed = passed.model_copy(update={"passed": False})

    summary = summarize_answer_evaluations((passed, failed))

    assert summary.case_count == 2
    assert summary.pass_count == 1
    assert summary.pass_rate == 0.5


def test_answer_evaluation_summary_rejects_empty_measurements() -> None:
    with pytest.raises(ValueError, match="at least one"):
        summarize_answer_evaluations(())
