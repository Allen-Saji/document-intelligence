from uuid import UUID

import pytest
from pydantic import ValidationError

from document_intelligence.citations.contracts import (
    ClaimDraft,
    EvidenceItem,
    EvidenceState,
    ModelAnswerDraft,
    validate_and_resolve_answer,
)

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
CORPUS_ID = UUID("00000000-0000-4000-8000-000000000003")


def evidence(*, organization_id: UUID = ORG_ID, workspace_id: UUID = WORKSPACE_ID) -> EvidenceItem:
    return EvidenceItem(
        evidence_id="ev_finality01",
        organization_id=organization_id,
        workspace_id=workspace_id,
        corpus_id=CORPUS_ID,
        document_version_id=UUID("00000000-0000-4000-8000-000000000004"),
        chunk_id=UUID("00000000-0000-4000-8000-000000000005"),
        page_number=17,
        passage="Finality is reached after the required voting threshold is observed.",
    )


def test_valid_answer_resolves_server_owned_evidence() -> None:
    supplied = evidence()
    draft = ModelAnswerDraft(
        state=EvidenceState.SUPPORTED,
        claims=(
            ClaimDraft(
                text="Finality requires the voting threshold.",
                evidence_ids=(supplied.evidence_id,),
            ),
        ),
    )

    answer = validate_and_resolve_answer(
        draft,
        (supplied,),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        allowed_corpus_ids=(CORPUS_ID,),
    )

    assert answer.claims[0].evidence[0].page_number == 17


def test_unknown_model_citation_is_rejected() -> None:
    draft = ModelAnswerDraft(
        state=EvidenceState.SUPPORTED,
        claims=(ClaimDraft(text="Unsupported claim.", evidence_ids=("ev_invented01",)),),
    )

    with pytest.raises(ValueError, match="not supplied"):
        validate_and_resolve_answer(
            draft,
            (evidence(),),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            allowed_corpus_ids=(CORPUS_ID,),
        )


def test_cross_tenant_evidence_is_rejected() -> None:
    other_org = UUID("00000000-0000-4000-8000-000000000099")
    supplied = evidence(organization_id=other_org)
    draft = ModelAnswerDraft(
        state=EvidenceState.SUPPORTED,
        claims=(ClaimDraft(text="Claim.", evidence_ids=(supplied.evidence_id,)),),
    )

    with pytest.raises(ValueError, match="outside the active tenant"):
        validate_and_resolve_answer(
            draft,
            (supplied,),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            allowed_corpus_ids=(CORPUS_ID,),
        )


def test_cross_workspace_evidence_is_rejected() -> None:
    supplied = evidence(workspace_id=UUID("00000000-0000-4000-8000-000000000099"))
    draft = ModelAnswerDraft(
        state=EvidenceState.SUPPORTED,
        claims=(ClaimDraft(text="Claim.", evidence_ids=(supplied.evidence_id,)),),
    )

    with pytest.raises(ValueError, match="outside the active tenant"):
        validate_and_resolve_answer(
            draft,
            (supplied,),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            allowed_corpus_ids=(CORPUS_ID,),
        )


def test_evidence_outside_allowed_corpora_is_rejected() -> None:
    supplied = evidence()
    draft = ModelAnswerDraft(
        state=EvidenceState.SUPPORTED,
        claims=(ClaimDraft(text="Claim.", evidence_ids=(supplied.evidence_id,)),),
    )

    with pytest.raises(ValueError, match="outside the active tenant"):
        validate_and_resolve_answer(
            draft,
            (supplied,),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            allowed_corpus_ids=(UUID("00000000-0000-4000-8000-000000000099"),),
        )


def test_duplicate_supplied_evidence_ids_are_rejected() -> None:
    supplied = evidence()
    draft = ModelAnswerDraft(
        state=EvidenceState.SUPPORTED,
        claims=(ClaimDraft(text="Claim.", evidence_ids=(supplied.evidence_id,)),),
    )

    with pytest.raises(ValueError, match="must be unique"):
        validate_and_resolve_answer(
            draft,
            (supplied, supplied),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            allowed_corpus_ids=(CORPUS_ID,),
        )


def test_claim_rejects_duplicate_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        ClaimDraft(text="Claim.", evidence_ids=("ev_duplicate01", "ev_duplicate01"))


def test_answer_bearing_state_requires_text_and_claims() -> None:
    with pytest.raises(ValidationError, match="require cited claims"):
        ModelAnswerDraft(state=EvidenceState.SUPPORTED)


def test_insufficient_answer_rejects_material_claims() -> None:
    with pytest.raises(ValidationError, match="must not contain material claims"):
        ModelAnswerDraft(
            state=EvidenceState.INSUFFICIENT,
            claims=(ClaimDraft(text="Claim.", evidence_ids=("ev_duplicate01",)),),
            missing_information=("Missing source",),
        )


def test_insufficient_answer_requires_missing_information() -> None:
    with pytest.raises(ValidationError):
        ModelAnswerDraft(
            state=EvidenceState.INSUFFICIENT,
        )


def test_failed_answer_rejects_material_claims() -> None:
    with pytest.raises(ValidationError, match="must not contain material claims"):
        ModelAnswerDraft(
            state=EvidenceState.FAILED,
            claims=(ClaimDraft(text="Claim.", evidence_ids=("ev_duplicate01",)),),
        )
