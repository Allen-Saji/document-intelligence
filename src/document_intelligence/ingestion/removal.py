from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from document_intelligence.ingestion.publication import PublicationRecord


class ProjectionRemovalOperation(StrEnum):
    ROLLBACK = "rollback"
    DELETE = "delete"


class ProjectionRemovalRequest(BaseModel):
    """Immutable request to hide a document version from ordinary search."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    publication: PublicationRecord
    operation: ProjectionRemovalOperation
