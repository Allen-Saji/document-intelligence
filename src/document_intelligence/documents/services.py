from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol
from uuid import UUID

from document_intelligence.audit import AuditEventDraft
from document_intelligence.core.tenancy import DatabaseTenantContext
from document_intelligence.documents.uploads import (
    UploadIntent,
    UploadReservation,
    abort_upload,
    reserve_upload,
)
from document_intelligence.storage.multipart import MultipartPart, MultipartUploadPlan, StoredObject


class UploadStore(Protocol):
    async def require_versioned_bucket(self) -> None: ...

    async def begin_multipart_upload(
        self, reservation: UploadReservation
    ) -> MultipartUploadPlan: ...

    async def finalize_multipart_upload(
        self, reservation: UploadReservation, parts: Sequence[MultipartPart]
    ) -> StoredObject: ...

    async def abort_multipart_upload(self, reservation: UploadReservation) -> None: ...

    async def signed_read_url(self, object_key: str) -> str: ...


class UploadRepository(Protocol):
    async def create(self, reservation: UploadReservation) -> None: ...

    async def get(self, reservation_id: UUID) -> UploadReservation | None: ...

    async def update(self, reservation: UploadReservation) -> None: ...

    async def record_promoted_object(self, stored: StoredObject) -> None: ...

    async def append_audit(self, event: AuditEventDraft) -> None: ...


WorkflowStarter = Callable[[StoredObject], Awaitable[None]]


class UploadNotFoundError(LookupError):
    """The requested upload is absent from the authenticated tenant."""


class UploadService:
    """Coordinate tenant-bound persistence, object promotion, audit, and workflow dispatch."""

    def __init__(
        self,
        *,
        store: UploadStore,
        repository: UploadRepository,
        start_ingestion: WorkflowStarter,
    ) -> None:
        self._store = store
        self._repository = repository
        self._start_ingestion = start_ingestion

    async def reserve(
        self, tenant: DatabaseTenantContext, intent: UploadIntent
    ) -> MultipartUploadPlan:
        reservation = reserve_upload(
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            actor_id=tenant.actor_id,
            intent=intent,
        )
        await self._store.require_versioned_bucket()
        plan = await self._store.begin_multipart_upload(reservation)
        await self._repository.create(plan.reservation)
        await self._audit(plan.reservation, "document.upload_reserved")
        return plan

    async def complete(
        self,
        tenant: DatabaseTenantContext,
        reservation_id: UUID,
        parts: Sequence[MultipartPart],
    ) -> StoredObject:
        reservation = await self._required_reservation(reservation_id)
        stored = await self._store.finalize_multipart_upload(reservation, parts)
        await self._repository.record_promoted_object(stored)
        await self._audit(stored.reservation, "document.upload_completed")
        await self._start_ingestion(stored)
        return stored

    async def abort(
        self, tenant: DatabaseTenantContext, reservation_id: UUID
    ) -> UploadReservation:
        reservation = await self._required_reservation(reservation_id)
        await self._store.abort_multipart_upload(reservation)
        aborted = abort_upload(reservation)
        await self._repository.update(aborted)
        await self._audit(aborted, "document.upload_aborted")
        return aborted

    async def signed_read(self, tenant: DatabaseTenantContext, reservation_id: UUID) -> str:
        reservation = await self._required_reservation(reservation_id)
        if reservation.final_object_key is None:
            raise ValueError("upload has not been promoted")
        return await self._store.signed_read_url(reservation.final_object_key)

    async def _required_reservation(self, reservation_id: UUID) -> UploadReservation:
        reservation = await self._repository.get(reservation_id)
        if reservation is None:
            raise UploadNotFoundError("upload reservation not found")
        return reservation

    async def _audit(self, reservation: UploadReservation, event_type: str) -> None:
        await self._repository.append_audit(
            AuditEventDraft(
                organization_id=reservation.organization_id,
                workspace_id=reservation.workspace_id,
                actor_id=reservation.actor_id,
                event_type=event_type,
                target_type="upload_reservation",
                target_id=reservation.id,
                occurred_at=reservation.completed_at or reservation.created_at,
            )
        )
