from __future__ import annotations

from io import BytesIO
from typing import Any, ClassVar
from uuid import UUID

import pytest

from document_intelligence.config import Settings
from document_intelligence.documents.uploads import UploadIntent, UploadReservation, reserve_upload
from document_intelligence.storage.multipart import (
    MIN_PART_SIZE_BYTES,
    MultipartPart,
    ObjectStorageError,
    S3CompatibleObjectStore,
)


class NotFoundError(Exception):
    response: ClassVar[dict[str, dict[str, str]]] = {"Error": {"Code": "NoSuchKey"}}


class FakeS3Client:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.pending_key = ""
        self.objects: dict[str, dict[str, Any]] = {}
        self.deleted_keys: list[str] = []
        self.versioning_status = "Enabled"

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        self.pending_key = kwargs["Key"]
        return {"UploadId": "upload-1"}

    def generate_presigned_url(self, operation_name: str, **kwargs: Any) -> str:
        return f"https://storage.test/{operation_name}/{kwargs['Params']['PartNumber']}"

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        assert kwargs["UploadId"] == "upload-1"
        return {"VersionId": "temporary-version"}

    def get_object(self, **kwargs: Any) -> dict[str, BytesIO]:
        assert kwargs["Key"] == self.pending_key
        return {"Body": BytesIO(self.payload)}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        if key not in self.objects:
            raise NotFoundError
        return self.objects[key]

    def copy_object(self, **kwargs: Any) -> dict[str, str]:
        self.objects[kwargs["Key"]] = {
            "ContentLength": len(self.payload),
            "Metadata": kwargs["Metadata"],
            "VersionId": "immutable-version",
        }
        return {"VersionId": "immutable-version"}

    def delete_object(self, **kwargs: Any) -> dict[str, str]:
        self.deleted_keys.append(kwargs["Key"])
        return {}

    def abort_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        return {}

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, str]:
        return {"Status": self.versioning_status}


def reservation(byte_size: int) -> UploadReservation:
    return reserve_upload(
        organization_id=UUID("00000000-0000-4000-8000-000000000001"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000002"),
        actor_id=UUID("00000000-0000-4000-8000-000000000003"),
        intent=UploadIntent(
            display_name="Protocol specification",
            original_filename="protocol.pdf",
            declared_size_bytes=byte_size,
        ),
    )


@pytest.mark.asyncio
async def test_multipart_store_presigns_and_promotes_verified_content() -> None:
    payload = b"p" * 1024
    client = FakeS3Client(payload)
    store = S3CompatibleObjectStore(
        client=client,
        bucket="documents",
        part_size_bytes=MIN_PART_SIZE_BYTES,
    )

    plan = await store.begin_multipart_upload(reservation(len(payload)))
    stored = await store.finalize_multipart_upload(
        plan.reservation, [MultipartPart(part_number=1, etag="etag-1")]
    )

    assert plan.part_count == 1
    assert plan.part_upload_urls == ("https://storage.test/upload_part/1",)
    assert stored.object_version_id == "immutable-version"
    assert stored.reservation.final_object_key in client.objects
    assert plan.reservation.multipart_object_key in client.deleted_keys


@pytest.mark.asyncio
async def test_multipart_store_rejects_unverified_size_before_immutable_promotion() -> None:
    client = FakeS3Client(b"short")
    store = S3CompatibleObjectStore(client=client, bucket="documents")
    plan = await store.begin_multipart_upload(reservation(1024))

    with pytest.raises(ObjectStorageError, match="byte size"):
        await store.finalize_multipart_upload(
            plan.reservation, [MultipartPart(part_number=1, etag="etag-1")]
        )

    assert client.objects == {}
    assert plan.reservation.multipart_object_key in client.deleted_keys


@pytest.mark.asyncio
async def test_multipart_completion_requires_contiguous_parts() -> None:
    client = FakeS3Client(b"p")
    store = S3CompatibleObjectStore(client=client, bucket="documents")
    plan = await store.begin_multipart_upload(reservation(1))

    with pytest.raises(ValueError, match="contiguous"):
        await store.finalize_multipart_upload(
            plan.reservation, [MultipartPart(part_number=2, etag="etag-2")]
        )


@pytest.mark.asyncio
async def test_storage_requires_a_versioned_bucket() -> None:
    client = FakeS3Client(b"p")
    client.versioning_status = "Suspended"
    store = S3CompatibleObjectStore(client=client, bucket="documents")

    with pytest.raises(ObjectStorageError, match="versioning"):
        await store.require_versioned_bucket()


def test_storage_factory_requires_a_bucket() -> None:
    with pytest.raises(ValueError, match="APP_S3_BUCKET"):
        S3CompatibleObjectStore.from_settings(Settings(env="test"))
