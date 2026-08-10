from pathlib import Path

import pytest
from pydantic import ValidationError

from document_intelligence.evaluation.retrieval import (
    EvidenceLocation,
    RetrievalCase,
    dataset_category_counts,
    load_retrieval_dataset,
    recall_at_k,
    reciprocal_rank,
)


def test_seed_retrieval_dataset_is_valid() -> None:
    dataset = load_retrieval_dataset(Path("spikes/retrieval/cases.json"))

    assert dataset.id == "phase-0-retrieval-v1"
    assert len(dataset.cases) == 14
    assert dataset_category_counts(dataset) == {
        "factual": 8,
        "follow-up": 2,
        "identifier": 1,
        "synthesis": 1,
        "unanswerable": 2,
    }


def test_unanswerable_case_rejects_gold_evidence() -> None:
    payload = {
        "id": "case",
        "question": "Can this be answered?",
        "category": "unanswerable",
        "expected_state": "insufficient",
        "gold_evidence": [{"document_id": "doc", "page_number": 1}],
    }

    with pytest.raises(ValidationError, match="cannot contain gold evidence"):
        RetrievalCase.model_validate(payload)


def test_follow_up_case_requires_standalone_question() -> None:
    with pytest.raises(ValidationError, match="conversation and standalone question"):
        RetrievalCase.model_validate(
            {
                "id": "case",
                "question": "What about that one?",
                "category": "follow-up",
                "expected_state": "supported",
                "gold_evidence": [{"document_id": "doc", "page_number": 1}],
                "conversation": ["What is the formula on page two?"],
            }
        )


def test_retrieval_metrics_use_document_and_page_identity() -> None:
    case = RetrievalCase(
        id="case",
        question="Where is the formula?",
        category="factual",
        expected_state="supported",
        gold_evidence=[EvidenceLocation(document_id="doc", page_number=2)],
    )
    retrieved = [
        EvidenceLocation(document_id="other", page_number=2),
        EvidenceLocation(document_id="doc", page_number=2),
    ]

    assert recall_at_k(case, retrieved, 1) == 0.0
    assert recall_at_k(case, retrieved, 2) == 1.0
    assert reciprocal_rank(case, retrieved) == 0.5


def test_unanswerable_metrics_are_not_scored_as_recall() -> None:
    case = RetrievalCase(
        id="case",
        question="What is missing?",
        category="unanswerable",
        expected_state="insufficient",
    )

    assert recall_at_k(case, [], 5) is None
    assert reciprocal_rank(case, []) is None
