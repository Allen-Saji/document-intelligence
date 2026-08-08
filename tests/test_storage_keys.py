from uuid import UUID

import pytest

from document_intelligence.storage.keys import original_pdf_key


def test_original_key_is_generated_and_tenant_scoped() -> None:
    key = original_pdf_key(
        organization_id=UUID("00000000-0000-4000-8000-000000000001"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000002"),
        document_id=UUID("00000000-0000-4000-8000-000000000003"),
        version_id=UUID("00000000-0000-4000-8000-000000000004"),
        sha256="a" * 64,
    )

    assert key.startswith("organizations/00000000-0000-4000-8000-000000000001/")
    assert key.endswith(f"original-{'a' * 64}.pdf")
    assert "user-file" not in key


def test_original_key_rejects_invalid_checksum() -> None:
    with pytest.raises(ValueError, match="sha256"):
        original_pdf_key(
            organization_id=UUID(int=1),
            workspace_id=UUID(int=2),
            document_id=UUID(int=3),
            version_id=UUID(int=4),
            sha256="not-a-checksum",
        )


def test_original_key_normalizes_uppercase_checksum() -> None:
    key = original_pdf_key(
        organization_id=UUID(int=1),
        workspace_id=UUID(int=2),
        document_id=UUID(int=3),
        version_id=UUID(int=4),
        sha256="A" * 64,
    )

    assert key.endswith(f"original-{'a' * 64}.pdf")
