from __future__ import annotations

from uuid import UUID

import pytest

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.database.tenancy import set_local_tenant_context


class RecordingConnection:
    def __init__(self) -> None:
        self.parameters: dict[str, str] | None = None

    async def execute(self, statement: object, parameters: dict[str, str]) -> object:
        self.parameters = parameters
        return statement


@pytest.mark.asyncio
async def test_tenant_context_is_bound_through_transaction_local_database_settings() -> None:
    connection = RecordingConnection()
    tenant = TenantContext(
        organization_id=UUID("00000000-0000-4000-8000-000000000001"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000002"),
        actor_id=UUID("00000000-0000-4000-8000-000000000003"),
        allowed_corpus_ids=(UUID("00000000-0000-4000-8000-000000000004"),),
    )

    await set_local_tenant_context(connection, tenant)

    assert connection.parameters == {
        "organization_id": str(tenant.organization_id),
        "workspace_id": str(tenant.workspace_id),
        "actor_id": str(tenant.actor_id),
    }
