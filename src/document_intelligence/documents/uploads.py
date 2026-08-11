from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from document_intelligence.storage.keys import original_pdf_key, pending_upload_key

SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class UploadState(StrEnum):
    RESERVED = "reserved"
    UPLOADED = "uploaded"
    COMPLETED = "completed"
    EXPIRED = "expired"
    FAILED = "failed"


class UploadIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str = Field(min_length=1, max_length=500)
    original_filename: str = Field(min_length=5, max_length=255)
    content_type: str = "application/pdf"
    declared_size_bytes: int = Field(gt=0, le=262_144_000)

    @field_validator("original_filename")
    @classmethod
    def require_pdf_extension(cls, value: str) -> str:
        if not value.casefold().endswith(".pdf"):
            raise ValueError("uploads must use a .pdf filename")
        return value

    @field_validator("content_type")
    @classmethod
    def require_pdf_content_type(cls, value: str) -> str:
        if value.casefold() != "application/pdf":
            raise ValueError("uploads must declare application/pdf")
        return value


class UploadReservation(BaseModel):
    """Persistable upload state. User-controlled filenames never become object keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    organization_id: UUID
    workspace_id: UUID
    actor_id: UUID
    document_id: UUID
    document_version_id: UUID
    display_name: str
    declared_size_bytes: int
    state: UploadState
    multipart_object_key: str
    multipart_upload_id: str | None = None
    final_object_key: str | None = None
    sha256: str | None = None
    created_at: datetime
    expires_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def state_matches_immutable_fields(self) -> UploadReservation:
        if self.expires_at <= self.created_at:
            raise ValueError("upload reservation expiry must be after creation")
        complete = self.state == UploadState.COMPLETED
        if complete != (self.final_object_key is not None and self.sha256 is not None):
            raise ValueError("completed uploads require immutable object metadata")
        if complete != (self.completed_at is not None):
            raise ValueError("completed uploads require a completion timestamp")
        return self


def reserve_upload(
    *,
    organization_id: UUID,
    workspace_id: UUID,
    actor_id: UUID,
    intent: UploadIntent,
    now: datetime | None = None,
    ttl: timedelta = timedelta(hours=1),
) -> UploadReservation:
    if ttl <= timedelta(0):
        raise ValueError("upload reservation TTL must be positive")
    created_at = now or datetime.now(UTC)
    if created_at.tzinfo is None:
        raise ValueError("upload reservation timestamps must be timezone-aware")
    reservation_id = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    return UploadReservation(
        id=reservation_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        document_id=document_id,
        document_version_id=version_id,
        display_name=intent.display_name,
        declared_size_bytes=intent.declared_size_bytes,
        state=UploadState.RESERVED,
        multipart_object_key=pending_upload_key(
            organization_id=organization_id,
            workspace_id=workspace_id,
            upload_id=reservation_id,
        ),
        created_at=created_at,
        expires_at=created_at + ttl,
    )


def attach_multipart_upload(
    reservation: UploadReservation, *, multipart_upload_id: str
) -> UploadReservation:
    """Record the provider upload ID needed to resume, complete, or abort a reservation."""

    if reservation.state != UploadState.RESERVED:
        raise ValueError("only reserved uploads can receive a multipart upload ID")
    if reservation.multipart_upload_id is not None:
        raise ValueError("multipart upload ID is already attached")
    if not multipart_upload_id:
        raise ValueError("multipart upload ID must not be empty")
    return reservation.model_copy(update={"multipart_upload_id": multipart_upload_id})


def complete_upload(
    reservation: UploadReservation,
    *,
    verified_size_bytes: int,
    verified_sha256: str,
    completed_at: datetime | None = None,
) -> UploadReservation:
    """Promote an uploaded object only after server-side size and checksum verification."""

    at = completed_at or datetime.now(UTC)
    if at.tzinfo is None:
        raise ValueError("upload completion timestamps must be timezone-aware")
    if reservation.state != UploadState.UPLOADED:
        raise ValueError("only uploaded reservations can be completed")
    if reservation.multipart_upload_id is None:
        raise ValueError("uploaded reservations require a multipart upload ID")
    if at > reservation.expires_at:
        return reservation.model_copy(update={"state": UploadState.EXPIRED})
    if verified_size_bytes != reservation.declared_size_bytes:
        return reservation.model_copy(update={"state": UploadState.FAILED})
    normalized_sha256 = verified_sha256.casefold()
    if not SHA256_PATTERN.fullmatch(normalized_sha256):
        raise ValueError("verified_sha256 must contain exactly 64 hexadecimal characters")
    return reservation.model_copy(
        update={
            "state": UploadState.COMPLETED,
            "sha256": normalized_sha256,
            "final_object_key": original_pdf_key(
                organization_id=reservation.organization_id,
                workspace_id=reservation.workspace_id,
                document_id=reservation.document_id,
                version_id=reservation.document_version_id,
                sha256=normalized_sha256,
            ),
            "completed_at": at,
        }
    )


def record_uploaded(
    reservation: UploadReservation, *, received_at: datetime | None = None
) -> UploadReservation:
    """Record completed multipart transfer before checksum verification and promotion."""

    at = received_at or datetime.now(UTC)
    if at.tzinfo is None:
        raise ValueError("upload receipt timestamps must be timezone-aware")
    if reservation.state != UploadState.RESERVED:
        raise ValueError("only reserved uploads can be marked uploaded")
    if reservation.multipart_upload_id is None:
        raise ValueError("reserved uploads require a multipart upload ID before receipt")
    if at > reservation.expires_at:
        return reservation.model_copy(update={"state": UploadState.EXPIRED})
    return reservation.model_copy(update={"state": UploadState.UPLOADED})


def abort_upload(
    reservation: UploadReservation, *, aborted_at: datetime | None = None
) -> UploadReservation:
    """Close an unpromoted reservation after its provider multipart upload is aborted."""

    at = aborted_at or datetime.now(UTC)
    if at.tzinfo is None:
        raise ValueError("upload abort timestamps must be timezone-aware")
    if reservation.state not in {UploadState.RESERVED, UploadState.UPLOADED}:
        raise ValueError("only unpromoted uploads can be aborted")
    return reservation.model_copy(update={"state": UploadState.FAILED, "completed_at": None})
