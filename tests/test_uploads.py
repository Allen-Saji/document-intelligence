from datetime import UTC, datetime, timedelta
from uuid import UUID

from document_intelligence.documents.uploads import (
    UploadIntent,
    UploadReservation,
    UploadState,
    abort_upload,
    attach_multipart_upload,
    complete_upload,
    record_uploaded,
    reserve_upload,
)

NOW = datetime(2026, 8, 11, tzinfo=UTC)


def reserve() -> UploadReservation:
    return reserve_upload(
        organization_id=UUID("00000000-0000-4000-8000-000000000001"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000002"),
        actor_id=UUID("00000000-0000-4000-8000-000000000003"),
        intent=UploadIntent(
            corpus_id=UUID("00000000-0000-4000-8000-000000000004"),
            display_name="Consensus specification",
            original_filename="consensus-spec.pdf",
            declared_size_bytes=1024,
        ),
        now=NOW,
    )


def test_upload_promotes_only_after_transfer_and_server_verification() -> None:
    reservation = attach_multipart_upload(reserve(), multipart_upload_id="upload-1")
    assert reservation.state == UploadState.RESERVED
    assert "consensus-spec" not in reservation.multipart_object_key

    uploaded = record_uploaded(reservation, received_at=NOW + timedelta(minutes=1))
    completed = complete_upload(
        uploaded,
        verified_size_bytes=1024,
        verified_sha256="a" * 64,
        completed_at=NOW + timedelta(minutes=2),
    )

    assert completed.state == UploadState.COMPLETED
    assert completed.final_object_key is not None
    assert completed.final_object_key.endswith(f"original-{'a' * 64}.pdf")


def test_size_mismatch_fails_without_an_immutable_source_object() -> None:
    reservation = attach_multipart_upload(reserve(), multipart_upload_id="upload-1")
    uploaded = record_uploaded(reservation, received_at=NOW + timedelta(minutes=1))

    failed = complete_upload(
        uploaded,
        verified_size_bytes=999,
        verified_sha256="a" * 64,
        completed_at=NOW + timedelta(minutes=2),
    )

    assert failed.state == UploadState.FAILED
    assert failed.final_object_key is None


def test_expired_reservation_never_promotes() -> None:
    reservation = reserve_upload(
        organization_id=UUID("00000000-0000-4000-8000-000000000001"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000002"),
        actor_id=UUID("00000000-0000-4000-8000-000000000003"),
        intent=UploadIntent(
            corpus_id=UUID("00000000-0000-4000-8000-000000000004"),
            display_name="Consensus specification",
            original_filename="consensus-spec.pdf",
            declared_size_bytes=1024,
        ),
        now=NOW,
        ttl=timedelta(minutes=1),
    )

    attached = attach_multipart_upload(reservation, multipart_upload_id="upload-1")
    expired = record_uploaded(attached, received_at=NOW + timedelta(minutes=2))

    assert expired.state == UploadState.EXPIRED


def test_abort_closes_only_unpromoted_uploads() -> None:
    reservation = attach_multipart_upload(reserve(), multipart_upload_id="upload-1")

    assert abort_upload(reservation).state == UploadState.FAILED
