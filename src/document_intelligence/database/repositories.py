from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from document_intelligence.audit import AuditEventDraft
from document_intelligence.auth.contracts import ApiKeyRecord
from document_intelligence.documents.uploads import UploadReservation
from document_intelligence.ingestion.publication import PublicationRecord
from document_intelligence.storage.multipart import StoredObject


class PostgresTenantRepository:
    """RLS-scoped persistence operations.

    Callers must establish ``tenant_transaction`` before constructing and using this
    repository. Every select is therefore denied by PostgreSQL when a row belongs to
    another tenant, even if an identifier was guessed correctly.
    """

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def lookup_api_key_by_prefix(self, token_prefix: str) -> ApiKeyRecord | None:
        """Lookup one API key through the narrow pre-tenant auth function."""

        result = await self._connection.execute(
            text("SELECT * FROM app.lookup_api_key_by_prefix(:token_prefix)"),
            {"token_prefix": token_prefix},
        )
        row = result.mappings().one_or_none()
        return ApiKeyRecord.model_validate(dict(row)) if row is not None else None

    async def lookup_api_key(self, token_prefix: str) -> ApiKeyRecord | None:
        result = await self._connection.execute(
            text(
                "SELECT id, organization_id, workspace_id, created_by_user_id, label, "
                "token_prefix, token_hash, scopes, created_at, expires_at, revoked_at, "
                "last_used_at "
                "FROM app.api_keys WHERE token_prefix = :token_prefix"
            ),
            {"token_prefix": token_prefix},
        )
        row = result.mappings().one_or_none()
        return ApiKeyRecord.model_validate(dict(row)) if row is not None else None

    async def mark_api_key_used(self, api_key_id: UUID) -> None:
        await self._connection.execute(
            text("UPDATE app.api_keys SET last_used_at = now() WHERE id = :api_key_id"),
            {"api_key_id": api_key_id},
        )

    async def readable_corpus_ids(self, actor_id: UUID) -> tuple[UUID, ...]:
        """Resolve readable corpora inside an established tenant transaction."""

        result = await self._connection.execute(
            text(
                "WITH active_membership AS ("
                "SELECT role FROM app.memberships "
                "WHERE user_id = :actor_id AND is_active = true LIMIT 1"
                ") "
                "SELECT DISTINCT corpora.id "
                "FROM app.corpora "
                "WHERE EXISTS ("
                "SELECT 1 FROM active_membership "
                "WHERE role IN ('owner', 'admin')"
                ") "
                "OR EXISTS ("
                "SELECT 1 FROM app.corpus_permissions "
                "JOIN app.group_members ON "
                "group_members.organization_id = corpus_permissions.organization_id "
                "AND group_members.workspace_id = corpus_permissions.workspace_id "
                "AND group_members.group_id = corpus_permissions.group_id "
                "WHERE corpus_permissions.corpus_id = corpora.id "
                "AND corpus_permissions.can_read = true "
                "AND group_members.user_id = :actor_id"
                ") "
                "ORDER BY corpora.id"
            ),
            {"actor_id": actor_id},
        )
        return tuple(UUID(str(row[0])) for row in result.all())

    async def can_ingest_into_corpus(self, *, actor_id: UUID, corpus_id: UUID) -> bool:
        """Authorize document upload into a corpus inside an established tenant transaction."""

        result = await self._connection.execute(
            text(
                "WITH active_membership AS ("
                "SELECT role FROM app.memberships "
                "WHERE user_id = :actor_id AND is_active = true LIMIT 1"
                ") "
                "SELECT EXISTS ("
                "SELECT 1 FROM app.corpora "
                "WHERE corpora.id = :corpus_id "
                "AND ("
                "EXISTS ("
                "SELECT 1 FROM active_membership "
                "WHERE role IN ('owner', 'admin')"
                ") "
                "OR EXISTS ("
                "SELECT 1 FROM app.corpus_permissions "
                "JOIN app.group_members ON "
                "group_members.organization_id = corpus_permissions.organization_id "
                "AND group_members.workspace_id = corpus_permissions.workspace_id "
                "AND group_members.group_id = corpus_permissions.group_id "
                "WHERE corpus_permissions.corpus_id = corpora.id "
                "AND corpus_permissions.can_read = true "
                "AND group_members.user_id = :actor_id"
                ")"
                ")"
                ")"
            ),
            {"actor_id": actor_id, "corpus_id": corpus_id},
        )
        return bool(result.scalar_one())

    async def create(self, reservation: UploadReservation) -> None:
        await self._connection.execute(
            text(
                "INSERT INTO app.documents (id, organization_id, workspace_id, display_name) "
                "VALUES (:document_id, :organization_id, :workspace_id, :display_name)"
            ),
            _reservation_parameters(reservation),
        )
        await self._connection.execute(
            text(
                "INSERT INTO app.upload_reservations ("
                "id, organization_id, workspace_id, actor_id, document_id, "
                "document_version_id, corpus_id, display_name, declared_size_bytes, state, "
                "multipart_object_key, multipart_upload_id, "
                "created_at, expires_at) VALUES ("
                ":id, :organization_id, :workspace_id, :actor_id, :document_id, "
                ":document_version_id, :corpus_id, "
                ":display_name, :declared_size_bytes, :state, :multipart_object_key, "
                ":multipart_upload_id, :created_at, :expires_at)"
            ),
            _reservation_parameters(reservation),
        )

    async def get(self, reservation_id: UUID) -> UploadReservation | None:
        result = await self._connection.execute(
            text("SELECT * FROM app.upload_reservations WHERE id = :reservation_id"),
            {"reservation_id": reservation_id},
        )
        row = result.mappings().one_or_none()
        return UploadReservation.model_validate(dict(row)) if row is not None else None

    async def update(self, reservation: UploadReservation) -> None:
        result = await self._connection.execute(
            text(
                "UPDATE app.upload_reservations SET state = :state, completed_at = :completed_at "
                "WHERE id = :id"
            ),
            _reservation_parameters(reservation),
        )
        if result.rowcount != 1:
            raise LookupError("upload reservation not found")

    async def record_promoted_object(self, stored: StoredObject) -> None:
        reservation = stored.reservation
        if reservation.final_object_key is None:
            raise ValueError("promoted upload requires a final object key")
        parameters = _reservation_parameters(reservation)
        parameters.update(
            {
                "byte_size": stored.byte_size,
                "sha256": stored.sha256,
                "object_version_id": stored.object_version_id,
            }
        )
        await self._connection.execute(
            text(
                "INSERT INTO app.document_versions ("
                "id, organization_id, workspace_id, document_id, version_number, source_sha256, "
                "byte_size, created_by_user_id) VALUES ("
                ":document_version_id, :organization_id, :workspace_id, :document_id, 1, :sha256, "
                ":byte_size, :actor_id)"
            ),
            parameters,
        )
        await self._connection.execute(
            text(
                "INSERT INTO app.document_objects ("
                "organization_id, workspace_id, document_version_id, object_key, kind, "
                "checksum_sha256, "
                "byte_size) VALUES ("
                ":organization_id, :workspace_id, :document_version_id, :final_object_key, "
                "'original_pdf', :sha256, :byte_size)"
            ),
            parameters,
        )
        await self._connection.execute(
            text(
                "UPDATE app.upload_reservations SET state = :state, "
                "final_object_key = :final_object_key, "
                "sha256 = :sha256, completed_at = :completed_at WHERE id = :id"
            ),
            parameters,
        )
        await self._connection.execute(
            text(
                "UPDATE app.documents SET active_version_id = :document_version_id, "
                "lifecycle_state = 'uploaded' WHERE id = :document_id"
            ),
            parameters,
        )

    async def append_audit(self, event: AuditEventDraft) -> None:
        await self._connection.execute(
            text(
                "INSERT INTO app.audit_events (organization_id, workspace_id, actor_id, "
                "event_type, "
                "target_type, target_id, request_id, occurred_at) VALUES ("
                ":organization_id, :workspace_id, :actor_id, :event_type, :target_type, "
                ":target_id, :request_id, :occurred_at)"
            ),
            event.model_dump(),
        )

    async def get_publication(self, idempotency_key: str) -> PublicationRecord | None:
        result = await self._connection.execute(
            text(
                "SELECT organization_id, workspace_id, document_version_id, "
                "idempotency_key, state, chunk_count "
                "FROM app.document_publications WHERE idempotency_key = :idempotency_key"
            ),
            {"idempotency_key": idempotency_key},
        )
        row = result.mappings().one_or_none()
        return PublicationRecord.model_validate(dict(row)) if row is not None else None

    async def save_publication(self, record: PublicationRecord) -> None:
        await self._connection.execute(
            text(
                "INSERT INTO app.document_publications ("
                "organization_id, workspace_id, document_version_id, idempotency_key, "
                "state, chunk_count"
                ") VALUES ("
                ":organization_id, :workspace_id, :document_version_id, :idempotency_key, "
                ":state, :chunk_count"
                ") ON CONFLICT (idempotency_key) DO UPDATE SET "
                "state = EXCLUDED.state, chunk_count = EXCLUDED.chunk_count, updated_at = now()"
            ),
            record.model_dump(),
        )


def _reservation_parameters(reservation: UploadReservation) -> dict[str, Any]:
    return reservation.model_dump()
