from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from document_intelligence.auth.contracts import (
    ApiKeyScope,
    Membership,
    WorkspaceRole,
    issue_api_key,
    verify_api_key,
)

NOW = datetime(2026, 8, 11, tzinfo=UTC)
MEMBERSHIP = Membership(
    user_id=UUID("00000000-0000-4000-8000-000000000001"),
    organization_id=UUID("00000000-0000-4000-8000-000000000002"),
    workspace_id=UUID("00000000-0000-4000-8000-000000000003"),
    role=WorkspaceRole.MEMBER,
)


def test_api_key_is_one_time_plaintext_and_verifies_against_persistable_record() -> None:
    issued = issue_api_key(
        membership=MEMBERSHIP,
        label="ci ingestion",
        requested_scopes=(ApiKeyScope.DOCUMENT_READ, ApiKeyScope.DOCUMENT_WRITE),
        pepper="test-pepper",
        now=NOW,
    )

    assert issued.plaintext_token.startswith(f"{issued.record.token_prefix}.")
    assert issued.record.token_hash not in issued.plaintext_token
    assert verify_api_key(
        plaintext_token=issued.plaintext_token,
        record=issued.record,
        pepper="test-pepper",
        at=NOW,
    )
    assert not verify_api_key(
        plaintext_token=issued.plaintext_token,
        record=issued.record,
        pepper="wrong-pepper",
        at=NOW,
    )


def test_api_key_scope_cannot_exceed_membership_role() -> None:
    with pytest.raises(PermissionError, match="exceed membership authority"):
        issue_api_key(
            membership=MEMBERSHIP,
            label="forbidden admin key",
            requested_scopes=(ApiKeyScope.ADMIN,),
            pepper="test-pepper",
            now=NOW,
        )


def test_expired_api_key_cannot_verify() -> None:
    issued = issue_api_key(
        membership=MEMBERSHIP,
        label="short lived",
        requested_scopes=(ApiKeyScope.DOCUMENT_READ,),
        pepper="test-pepper",
        now=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )

    assert not verify_api_key(
        plaintext_token=issued.plaintext_token,
        record=issued.record,
        pepper="test-pepper",
        at=NOW + timedelta(minutes=5),
    )
