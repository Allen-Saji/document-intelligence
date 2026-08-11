from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from document_intelligence.auth.contracts import (
    ApiKeyPrincipal,
    ApiKeyRecord,
    ApiKeyScope,
    verify_api_key,
)

ApiKeyLookup = Callable[[str], Awaitable[ApiKeyRecord | None]]


class ApiKeyAuthenticationError(PermissionError):
    """Authentication failed without exposing key lookup or verification details."""


async def authenticate_api_key(
    *,
    plaintext_token: str,
    pepper: str,
    lookup_by_prefix: ApiKeyLookup,
    required_scope: ApiKeyScope,
    now: datetime | None = None,
) -> ApiKeyPrincipal:
    """Resolve an active key by public prefix, then verify its secret in constant time."""

    prefix, separator, secret = plaintext_token.partition(".")
    if not separator or not secret or not prefix.startswith("diak_v1_"):
        raise ApiKeyAuthenticationError("invalid API key")
    record = await lookup_by_prefix(prefix)
    checked_at = now or datetime.now(UTC)
    if record is None or not verify_api_key(
        plaintext_token=plaintext_token,
        record=record,
        pepper=pepper,
        at=checked_at,
    ):
        raise ApiKeyAuthenticationError("invalid API key")
    if required_scope not in record.scopes:
        raise ApiKeyAuthenticationError("API key lacks required scope")
    return ApiKeyPrincipal(
        api_key_id=record.id,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        actor_id=record.created_by_user_id,
        scopes=record.scopes,
    )
