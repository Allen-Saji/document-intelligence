from uuid import UUID

import pytest

from document_intelligence.core.cache import tenant_cache_key


def test_cache_key_keeps_identical_values_isolated_by_tenant() -> None:
    value = "same retrieval payload"
    first = tenant_cache_key(
        organization_id=UUID("00000000-0000-4000-8000-000000000001"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000002"),
        namespace="retrieval",
        value=value,
    )
    second = tenant_cache_key(
        organization_id=UUID("00000000-0000-4000-8000-000000000003"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000004"),
        namespace="retrieval",
        value=value,
    )

    assert first != second
    assert value not in first


def test_cache_key_rejects_ambiguous_empty_scope() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        tenant_cache_key(
            organization_id=UUID("00000000-0000-4000-8000-000000000001"),
            workspace_id=UUID("00000000-0000-4000-8000-000000000002"),
            namespace="",
            value="x",
        )
