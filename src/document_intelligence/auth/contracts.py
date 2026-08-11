from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from document_intelligence.core.tenancy import TenantContext


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class ApiKeyScope(StrEnum):
    DOCUMENT_READ = "document:read"
    DOCUMENT_WRITE = "document:write"
    INVESTIGATION_READ = "investigation:read"
    INVESTIGATION_WRITE = "investigation:write"
    ADMIN = "admin"


ROLE_SCOPES: dict[WorkspaceRole, frozenset[ApiKeyScope]] = {
    WorkspaceRole.OWNER: frozenset(ApiKeyScope),
    WorkspaceRole.ADMIN: frozenset(ApiKeyScope),
    WorkspaceRole.MEMBER: frozenset(
        {
            ApiKeyScope.DOCUMENT_READ,
            ApiKeyScope.DOCUMENT_WRITE,
            ApiKeyScope.INVESTIGATION_READ,
            ApiKeyScope.INVESTIGATION_WRITE,
        }
    ),
    WorkspaceRole.VIEWER: frozenset({ApiKeyScope.DOCUMENT_READ, ApiKeyScope.INVESTIGATION_READ}),
}


class VerifiedIdentity(BaseModel):
    """Claims normalized from a verified OIDC token, never from client input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(min_length=1, max_length=255)
    issuer: str = Field(pattern=r"^https://")
    email: str | None = Field(default=None, max_length=320)
    email_verified: bool = False


class Membership(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: UUID
    organization_id: UUID
    workspace_id: UUID
    role: WorkspaceRole
    is_active: bool = True

    def permits(self, scope: ApiKeyScope) -> bool:
        return self.is_active and scope in ROLE_SCOPES[self.role]


class ApiKeyRecord(BaseModel):
    """Persistable API-key metadata. The plaintext token is deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    organization_id: UUID
    workspace_id: UUID
    created_by_user_id: UUID
    label: str = Field(min_length=1, max_length=120)
    token_prefix: str = Field(pattern=r"^diak_v1_[a-f0-9]{12}$")
    token_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    scopes: tuple[ApiKeyScope, ...] = Field(min_length=1)
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None

    @field_validator("scopes")
    @classmethod
    def scopes_are_unique(cls, value: tuple[ApiKeyScope, ...]) -> tuple[ApiKeyScope, ...]:
        if len(value) != len(set(value)):
            raise ValueError("API key scopes must not contain duplicates")
        return value

    @model_validator(mode="after")
    def expiry_follows_creation(self) -> ApiKeyRecord:
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("API key expiry must be after creation")
        return self

    def is_usable(self, at: datetime) -> bool:
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > at)


class IssuedApiKey(BaseModel):
    """One-time API-key delivery. Do not log or persist this model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record: ApiKeyRecord
    plaintext_token: str = Field(pattern=r"^diak_v1_[a-f0-9]{12}\.[A-Za-z0-9_-]{43}$")


class ApiKeyPrincipal(BaseModel):
    """Verified server-side principal derived from an active scoped API key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key_id: UUID
    organization_id: UUID
    workspace_id: UUID
    actor_id: UUID
    scopes: tuple[ApiKeyScope, ...]

    def permits(self, scope: ApiKeyScope) -> bool:
        return scope in self.scopes

    def tenant_context(self, allowed_corpus_ids: tuple[UUID, ...]) -> TenantContext:
        return TenantContext(
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            actor_id=self.actor_id,
            allowed_corpus_ids=allowed_corpus_ids,
        )


def issue_api_key(
    *,
    membership: Membership,
    label: str,
    requested_scopes: tuple[ApiKeyScope, ...],
    pepper: str,
    now: datetime | None = None,
    expires_at: datetime | None = None,
) -> IssuedApiKey:
    """Issue a high-entropy, scoped key only within the caller's membership authority."""

    if not pepper:
        raise ValueError("API key pepper must be configured")
    if not membership.is_active:
        raise PermissionError("inactive membership cannot issue API keys")
    if not requested_scopes:
        raise ValueError("API key must have at least one scope")
    if len(requested_scopes) != len(set(requested_scopes)):
        raise ValueError("API key scopes must not contain duplicates")
    if any(scope not in ROLE_SCOPES[membership.role] for scope in requested_scopes):
        raise PermissionError("requested API key scopes exceed membership authority")

    issued_at = now or datetime.now(UTC)
    if issued_at.tzinfo is None:
        raise ValueError("API key timestamps must be timezone-aware")
    key_id = uuid4()
    token_prefix = f"diak_v1_{key_id.hex[:12]}"
    plaintext_token = f"{token_prefix}.{secrets.token_urlsafe(32)}"
    record = ApiKeyRecord(
        id=key_id,
        organization_id=membership.organization_id,
        workspace_id=membership.workspace_id,
        created_by_user_id=membership.user_id,
        label=label,
        token_prefix=token_prefix,
        token_hash=_token_hash(plaintext_token, pepper),
        scopes=requested_scopes,
        created_at=issued_at,
        expires_at=expires_at,
    )
    return IssuedApiKey(record=record, plaintext_token=plaintext_token)


def verify_api_key(
    *, plaintext_token: str, record: ApiKeyRecord, pepper: str, at: datetime
) -> bool:
    """Perform a constant-time verification without retaining a recoverable secret."""

    if not pepper or not record.is_usable(at):
        return False
    if not plaintext_token.startswith(f"{record.token_prefix}."):
        return False
    return hmac.compare_digest(_token_hash(plaintext_token, pepper), record.token_hash)


def _token_hash(plaintext_token: str, pepper: str) -> str:
    return hmac.new(
        pepper.encode("utf-8"), plaintext_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()
