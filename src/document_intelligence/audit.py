from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditEventDraft(BaseModel):
    """Content-free event that can be persisted in the append-only audit log."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: UUID
    workspace_id: UUID | None = None
    actor_id: UUID | None = None
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,119}$")
    target_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,119}$")
    target_id: UUID | None = None
    occurred_at: datetime
    request_id: UUID | None = None
