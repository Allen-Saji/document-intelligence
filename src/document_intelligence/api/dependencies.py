from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Protocol

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from document_intelligence.auth.api_keys import ApiKeyAuthenticationError, authenticate_api_key
from document_intelligence.auth.contracts import ApiKeyPrincipal, ApiKeyRecord, ApiKeyScope
from document_intelligence.config import Settings
from document_intelligence.core.tenancy import DatabaseTenantContext

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class ApiKeyLookup(Protocol):
    async def __call__(self, token_prefix: str) -> ApiKeyRecord | None: ...


async def require_document_write_principal(
    request: Request,
    token: Annotated[str | None, Depends(api_key_header)],
) -> ApiKeyPrincipal:
    return await _require_principal(request, token, ApiKeyScope.DOCUMENT_WRITE)


async def require_document_read_principal(
    request: Request,
    token: Annotated[str | None, Depends(api_key_header)],
) -> ApiKeyPrincipal:
    return await _require_principal(request, token, ApiKeyScope.DOCUMENT_READ)


async def require_investigation_read_principal(
    request: Request,
    token: Annotated[str | None, Depends(api_key_header)],
) -> ApiKeyPrincipal:
    return await _require_principal(request, token, ApiKeyScope.INVESTIGATION_READ)


async def _require_principal(
    request: Request,
    token: str | None,
    required_scope: ApiKeyScope,
) -> ApiKeyPrincipal:
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required"
        )
    settings: Settings = request.app.state.settings
    lookup: Callable[[str], Awaitable[ApiKeyRecord | None]] | None = getattr(
        request.app.state, "api_key_lookup", None
    )
    pepper = settings.api_key_pepper.get_secret_value() if settings.api_key_pepper else ""
    if lookup is None or not pepper:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key authentication is not configured",
        )
    try:
        return await authenticate_api_key(
            plaintext_token=token,
            pepper=pepper,
            lookup_by_prefix=lookup,
            required_scope=required_scope,
        )
    except ApiKeyAuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key"
        ) from error


def tenant_from_principal(principal: ApiKeyPrincipal) -> DatabaseTenantContext:
    return DatabaseTenantContext(
        organization_id=principal.organization_id,
        workspace_id=principal.workspace_id,
        actor_id=principal.actor_id,
    )
