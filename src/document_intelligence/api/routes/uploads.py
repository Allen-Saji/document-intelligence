from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from document_intelligence.api.dependencies import (
    require_document_read_principal,
    require_document_write_principal,
    tenant_from_principal,
)
from document_intelligence.auth.contracts import ApiKeyPrincipal
from document_intelligence.documents.services import UploadNotFoundError, UploadService
from document_intelligence.documents.uploads import UploadIntent, UploadReservation
from document_intelligence.storage.multipart import MultipartPart, MultipartUploadPlan, StoredObject

router = APIRouter(prefix="/v1/uploads", tags=["uploads"])


class MultipartPartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=512)


class CompleteUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parts: tuple[MultipartPartRequest, ...] = Field(min_length=1)


class UploadPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation_id: UUID
    document_id: UUID
    document_version_id: UUID
    part_size_bytes: int
    part_count: int
    part_upload_urls: tuple[str, ...]


class UploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation_id: UUID
    document_id: UUID
    document_version_id: UUID
    state: str
    sha256: str | None = None


class SignedReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str


def _service(request: Request) -> UploadService:
    service: UploadService | None = getattr(request.app.state, "upload_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="uploads unavailable"
        )
    return service


@router.post("", response_model=UploadPlanResponse, status_code=status.HTTP_201_CREATED)
async def reserve(
    intent: UploadIntent,
    principal: Annotated[ApiKeyPrincipal, Depends(require_document_write_principal)],
    service: Annotated[UploadService, Depends(_service)],
) -> UploadPlanResponse:
    plan = await service.reserve(tenant_from_principal(principal), intent)
    return _plan_response(plan)


@router.post("/{reservation_id}/complete", response_model=UploadResponse)
async def complete(
    reservation_id: UUID,
    payload: CompleteUploadRequest,
    _: Annotated[ApiKeyPrincipal, Depends(require_document_write_principal)],
    service: Annotated[UploadService, Depends(_service)],
) -> UploadResponse:
    try:
        stored = await service.complete(
            reservation_id,
            tuple(MultipartPart(**part.model_dump()) for part in payload.parts),
        )
    except UploadNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="upload not found"
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _stored_response(stored)


@router.delete("/{reservation_id}", response_model=UploadResponse)
async def abort(
    reservation_id: UUID,
    _: Annotated[ApiKeyPrincipal, Depends(require_document_write_principal)],
    service: Annotated[UploadService, Depends(_service)],
) -> UploadResponse:
    try:
        reservation = await service.abort(reservation_id)
    except UploadNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="upload not found"
        ) from error
    return _reservation_response(reservation)


@router.get("/{reservation_id}/read", response_model=SignedReadResponse)
async def signed_read(
    reservation_id: UUID,
    _: Annotated[ApiKeyPrincipal, Depends(require_document_read_principal)],
    service: Annotated[UploadService, Depends(_service)],
) -> SignedReadResponse:
    try:
        return SignedReadResponse(url=await service.signed_read(reservation_id))
    except UploadNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="upload not found"
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


def _plan_response(plan: MultipartUploadPlan) -> UploadPlanResponse:
    return UploadPlanResponse(
        reservation_id=plan.reservation.id,
        document_id=plan.reservation.document_id,
        document_version_id=plan.reservation.document_version_id,
        part_size_bytes=plan.part_size_bytes,
        part_count=plan.part_count,
        part_upload_urls=plan.part_upload_urls,
    )


def _stored_response(stored: StoredObject) -> UploadResponse:
    response = _reservation_response(stored.reservation)
    return response.model_copy(update={"sha256": stored.sha256})


def _reservation_response(reservation: UploadReservation) -> UploadResponse:
    return UploadResponse(
        reservation_id=reservation.id,
        document_id=reservation.document_id,
        document_version_id=reservation.document_version_id,
        state=reservation.state.value,
        sha256=reservation.sha256,
    )
