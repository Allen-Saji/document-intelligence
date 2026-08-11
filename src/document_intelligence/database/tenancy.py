from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from document_intelligence.core.tenancy import DatabaseTenantContext


class TenantTransactionConnection(Protocol):
    async def execute(self, statement: Any, parameters: dict[str, str] | None = None) -> Any: ...


async def set_local_tenant_context(
    connection: TenantTransactionConnection, tenant: DatabaseTenantContext
) -> None:
    """Bind verified tenant data to the current PostgreSQL transaction only."""

    await connection.execute(
        text(
            "SELECT set_config('app.organization_id', :organization_id, true), "
            "set_config('app.workspace_id', :workspace_id, true), "
            "set_config('app.actor_id', :actor_id, true)"
        ),
        {
            "organization_id": str(tenant.organization_id),
            "workspace_id": str(tenant.workspace_id),
            "actor_id": str(tenant.actor_id),
        },
    )


@asynccontextmanager
async def tenant_transaction(
    connection: AsyncConnection, tenant: DatabaseTenantContext
) -> AsyncIterator[AsyncConnection]:
    """Ensure PostgreSQL RLS context cannot escape a transaction or pooled connection."""

    async with connection.begin():
        await set_local_tenant_context(connection, tenant)
        yield connection
