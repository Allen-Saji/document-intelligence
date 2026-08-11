from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from document_intelligence.api.app import create_app
from document_intelligence.auth.contracts import (
    ApiKeyScope,
    Membership,
    WorkspaceRole,
    issue_api_key,
)
from document_intelligence.config import Settings
from document_intelligence.core.tenancy import DatabaseTenantContext
from document_intelligence.documents.services import UploadService
from document_intelligence.documents.uploads import UploadReservation, UploadState
from document_intelligence.storage.multipart import MultipartPart, MultipartUploadPlan, StoredObject

NOW = datetime(2026, 8, 11, tzinfo=UTC)
MEMBERSHIP = Membership(
    user_id=UUID("00000000-0000-4000-8000-000000000001"),
    organization_id=UUID("00000000-0000-4000-8000-000000000002"),
    workspace_id=UUID("00000000-0000-4000-8000-000000000003"),
    role=WorkspaceRole.MEMBER,
)


class MemoryRepository:
    def __init__(self) -> None:
        self.reservations: dict[UUID, UploadReservation] = {}
        self.events: list[str] = []
        self.promoted: list[StoredObject] = []

    async def create(self, reservation: UploadReservation) -> None:
        self.reservations[reservation.id] = reservation

    async def get(self, reservation_id: UUID) -> UploadReservation | None:
        return self.reservations.get(reservation_id)

    async def update(self, reservation: UploadReservation) -> None:
        self.reservations[reservation.id] = reservation

    async def record_promoted_object(self, stored: StoredObject) -> None:
        self.promoted.append(stored)
        self.reservations[stored.reservation.id] = stored.reservation

    async def append_audit(self, event: object) -> None:
        self.events.append(event.event_type)


class MemoryStore:
    async def require_versioned_bucket(self) -> None:
        return None

    async def begin_multipart_upload(self, reservation: UploadReservation) -> MultipartUploadPlan:
        attached = reservation.model_copy(update={"multipart_upload_id": "upload-1"})
        return MultipartUploadPlan(
            reservation=attached,
            part_size_bytes=5 * 1024 * 1024,
            part_count=1,
            part_upload_urls=("https://storage.test/part-1",),
        )

    async def finalize_multipart_upload(
        self, reservation: UploadReservation, parts: Sequence[MultipartPart]
    ) -> StoredObject:
        assert parts == (MultipartPart(part_number=1, etag="etag-1"),)
        completed = reservation.model_copy(
            update={
                "state": UploadState.COMPLETED,
                "final_object_key": "immutable/source.pdf",
                "sha256": "a" * 64,
                "completed_at": NOW,
            }
        )
        return StoredObject(
            reservation=completed,
            object_version_id="version-1",
            byte_size=completed.declared_size_bytes,
            sha256="a" * 64,
        )

    async def abort_multipart_upload(self, reservation: UploadReservation) -> None:
        return None

    async def signed_read_url(self, object_key: str) -> str:
        assert object_key == "immutable/source.pdf"
        return "https://storage.test/read"


def build_client() -> tuple[httpx.AsyncClient, MemoryRepository, list[UUID]]:
    issued = issue_api_key(
        membership=MEMBERSHIP,
        label="upload test",
        requested_scopes=(ApiKeyScope.DOCUMENT_READ, ApiKeyScope.DOCUMENT_WRITE),
        pepper="test-pepper",
        now=NOW,
    )
    repository = MemoryRepository()
    started: list[UUID] = []

    async def lookup(prefix: str):
        return issued.record if prefix == issued.record.token_prefix else None

    async def start_ingestion(stored: StoredObject) -> None:
        started.append(stored.reservation.document_version_id)

    app = create_app(Settings(env="test", api_key_pepper=SecretStr("test-pepper")))
    app.state.api_key_lookup = lookup
    app.state.upload_service = UploadService(
        store=MemoryStore(), repository=repository, start_ingestion=start_ingestion
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": issued.plaintext_token},
    )
    return client, repository, started


@pytest.mark.asyncio
async def test_authenticated_upload_api_reserves_completes_and_returns_signed_read() -> None:
    client, repository, started = build_client()
    async with client:
        reserved = await client.post(
            "/v1/uploads",
            json={
                "display_name": "Protocol",
                "original_filename": "protocol.pdf",
                "declared_size_bytes": 42,
            },
        )
        assert reserved.status_code == 201
        reservation_id = reserved.json()["reservation_id"]

        completed = await client.post(
            f"/v1/uploads/{reservation_id}/complete",
            json={"parts": [{"part_number": 1, "etag": "etag-1"}]},
        )
        assert completed.status_code == 200
        assert completed.json()["state"] == "completed"

        read = await client.get(f"/v1/uploads/{reservation_id}/read")

    assert read.json() == {"url": "https://storage.test/read"}
    assert repository.events == ["document.upload_reserved", "document.upload_completed"]
    assert len(started) == 1


@pytest.mark.asyncio
async def test_upload_api_rejects_missing_or_invalid_key_without_leaking_lookup_details() -> None:
    client, _, _ = build_client()
    intent = {
        "display_name": "Protocol",
        "original_filename": "protocol.pdf",
        "declared_size_bytes": 42,
    }
    async with client:
        client.headers.pop("X-API-Key")
        missing = await client.post("/v1/uploads", json=intent)
        invalid = await client.post(
            "/v1/uploads",
            json=intent,
            headers={"X-API-Key": "diak_v1_000000000000.invalid"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "invalid API key"


@pytest.mark.asyncio
async def test_upload_api_returns_not_found_for_unknown_tenant_scoped_reservation() -> None:
    client, _, _ = build_client()
    unknown = UUID("00000000-0000-4000-8000-000000000099")
    async with client:
        response = await client.delete(f"/v1/uploads/{unknown}")

    assert response.status_code == 404


def test_document_write_tenant_context_does_not_require_corpus_access() -> None:
    tenant = DatabaseTenantContext(
        organization_id=MEMBERSHIP.organization_id,
        workspace_id=MEMBERSHIP.workspace_id,
        actor_id=MEMBERSHIP.user_id,
    )

    assert tenant.actor_id == MEMBERSHIP.user_id
