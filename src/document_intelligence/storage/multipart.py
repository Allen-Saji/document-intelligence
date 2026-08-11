from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from document_intelligence.config import Settings
from document_intelligence.documents.uploads import (
    UploadReservation,
    attach_multipart_upload,
    complete_upload,
    record_uploaded,
)

MIN_PART_SIZE_BYTES = 5 * 1024 * 1024
DEFAULT_PART_SIZE_BYTES = 8 * 1024 * 1024
MAX_PART_COUNT = 10_000


class MultipartPart(BaseModel):
    """Client-returned S3 part identity. The service never trusts a client checksum."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    part_number: int = Field(ge=1, le=MAX_PART_COUNT)
    etag: str = Field(min_length=1, max_length=512)


class MultipartUploadPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation: UploadReservation
    part_size_bytes: int = Field(ge=MIN_PART_SIZE_BYTES)
    part_count: int = Field(ge=1, le=MAX_PART_COUNT)
    part_upload_urls: tuple[str, ...] = Field(min_length=1)


class StoredObject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation: UploadReservation
    object_version_id: str = Field(min_length=1)
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ObjectStorageError(RuntimeError):
    """Safe storage error that contains no object body, token, or credential data."""


class S3Client(Protocol):
    def create_multipart_upload(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def generate_presigned_url(self, operation_name: str, **kwargs: Any) -> str: ...

    def complete_multipart_upload(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_object(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def head_object(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def copy_object(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def delete_object(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def abort_multipart_upload(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_bucket_versioning(self, **kwargs: Any) -> Mapping[str, Any]: ...


class StreamingBody(Protocol):
    def read(self, amt: int = ...) -> bytes: ...

    def close(self) -> Any: ...


@dataclass(frozen=True)
class S3CompatibleObjectStore:
    """S3-compatible multipart storage with server-side integrity verification.

    The adapter accepts a path-style client so it can target LocalStack, AWS S3, or another
    compatible provider. It always verifies the completed temporary object itself before copying
    it under the immutable source key.
    """

    client: S3Client
    bucket: str
    part_size_bytes: int = DEFAULT_PART_SIZE_BYTES
    signed_url_ttl_seconds: int = 900

    def __post_init__(self) -> None:
        if not self.bucket:
            raise ValueError("S3 bucket must be configured")
        if self.part_size_bytes < MIN_PART_SIZE_BYTES:
            raise ValueError("multipart part size is below the S3 minimum")
        if self.signed_url_ttl_seconds <= 0:
            raise ValueError("signed URL TTL must be positive")

    @classmethod
    def from_settings(cls, settings: Settings) -> S3CompatibleObjectStore:
        """Create a path-style S3 client while relying on the deployment credential chain."""

        if not settings.s3_bucket:
            raise ValueError("APP_S3_BUCKET must be configured")
        endpoint_url = (
            settings.s3_endpoint_url.get_secret_value()
            if settings.s3_endpoint_url is not None
            else None
        )
        import boto3  # type: ignore[import-untyped]
        from botocore.client import Config  # type: ignore[import-untyped]

        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        return cls(client=client, bucket=settings.s3_bucket)

    async def require_versioned_bucket(self) -> None:
        """Refuse operation when immutable source objects lack object-version recovery."""

        configuration = await asyncio.to_thread(
            self.client.get_bucket_versioning, Bucket=self.bucket
        )
        if configuration.get("Status") != "Enabled":
            raise ObjectStorageError("S3 bucket versioning must be enabled")

    async def begin_multipart_upload(self, reservation: UploadReservation) -> MultipartUploadPlan:
        """Create provider upload state and direct-upload URLs for a reserved PDF."""

        if reservation.multipart_upload_id is not None:
            raise ValueError("multipart upload is already attached")
        response = await asyncio.to_thread(
            self.client.create_multipart_upload,
            Bucket=self.bucket,
            Key=reservation.multipart_object_key,
            ContentType="application/pdf",
            Metadata={
                "organization-id": str(reservation.organization_id),
                "workspace-id": str(reservation.workspace_id),
                "document-id": str(reservation.document_id),
                "document-version-id": str(reservation.document_version_id),
                "upload-reservation-id": str(reservation.id),
            },
        )
        upload_id = response.get("UploadId")
        if not isinstance(upload_id, str) or not upload_id:
            raise ObjectStorageError("S3 did not return a multipart upload ID")
        attached = attach_multipart_upload(reservation, multipart_upload_id=upload_id)
        part_count = _part_count(attached.declared_size_bytes, self.part_size_bytes)
        urls: list[str] = []
        for part_number in range(1, part_count + 1):
            url = await asyncio.to_thread(
                self.client.generate_presigned_url,
                "upload_part",
                Params={
                    "Bucket": self.bucket,
                    "Key": attached.multipart_object_key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=self.signed_url_ttl_seconds,
                HttpMethod="PUT",
            )
            if not url:
                raise ObjectStorageError("S3 did not return a part upload URL")
            urls.append(url)
        return MultipartUploadPlan(
            reservation=attached,
            part_size_bytes=self.part_size_bytes,
            part_count=part_count,
            part_upload_urls=tuple(urls),
        )

    async def finalize_multipart_upload(
        self, reservation: UploadReservation, parts: Sequence[MultipartPart]
    ) -> StoredObject:
        """Verify a completed upload and promote it to an immutable source object key."""

        if reservation.multipart_upload_id is None:
            raise ValueError("multipart upload ID is required for finalization")
        normalized_parts = _normalized_parts(parts)
        completion = await asyncio.to_thread(
            self.client.complete_multipart_upload,
            Bucket=self.bucket,
            Key=reservation.multipart_object_key,
            UploadId=reservation.multipart_upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": part.part_number, "ETag": part.etag} for part in normalized_parts
                ]
            },
        )
        uploaded = record_uploaded(reservation)
        temporary_version_id = _require_version_id(completion, "temporary multipart object")
        byte_size, sha256 = await asyncio.to_thread(
            self._calculate_temporary_object_checksum,
            uploaded.multipart_object_key,
            temporary_version_id,
        )
        completed = complete_upload(
            uploaded,
            verified_size_bytes=byte_size,
            verified_sha256=sha256,
        )
        if completed.final_object_key is None:
            await self._delete_temporary_object(uploaded.multipart_object_key, temporary_version_id)
            raise ObjectStorageError("uploaded byte size did not match the reserved size")

        existing = await asyncio.to_thread(self._head_if_exists, completed.final_object_key)
        if existing is None:
            copied = await asyncio.to_thread(self._copy_to_immutable_key, completed)
        else:
            copied = existing
            _validate_existing_immutable_object(copied, byte_size, sha256)
        await self._delete_temporary_object(uploaded.multipart_object_key, temporary_version_id)
        version_id = _require_version_id(copied, "immutable S3 object")
        return StoredObject(
            reservation=completed,
            object_version_id=version_id,
            byte_size=byte_size,
            sha256=sha256,
        )

    async def abort_multipart_upload(self, reservation: UploadReservation) -> None:
        if reservation.multipart_upload_id is None:
            return
        await asyncio.to_thread(
            self.client.abort_multipart_upload,
            Bucket=self.bucket,
            Key=reservation.multipart_object_key,
            UploadId=reservation.multipart_upload_id,
        )

    def _calculate_temporary_object_checksum(self, key: str, version_id: str) -> tuple[int, str]:
        response = self.client.get_object(Bucket=self.bucket, Key=key, VersionId=version_id)
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise ObjectStorageError("S3 response did not contain an object body")
        stream = body
        digest = hashlib.sha256()
        byte_size = 0
        try:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                byte_size += len(chunk)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        return byte_size, digest.hexdigest()

    def _copy_to_immutable_key(self, reservation: UploadReservation) -> Mapping[str, Any]:
        if reservation.final_object_key is None or reservation.sha256 is None:
            raise ValueError("completed upload must have immutable object metadata")
        return self.client.copy_object(
            Bucket=self.bucket,
            Key=reservation.final_object_key,
            CopySource={"Bucket": self.bucket, "Key": reservation.multipart_object_key},
            ContentType="application/pdf",
            MetadataDirective="REPLACE",
            Metadata={
                "organization-id": str(reservation.organization_id),
                "workspace-id": str(reservation.workspace_id),
                "document-id": str(reservation.document_id),
                "document-version-id": str(reservation.document_version_id),
                "sha256": reservation.sha256,
            },
        )

    def _head_if_exists(self, key: str) -> Mapping[str, Any] | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            # Provider SDKs use distinct, provider-specific not-found errors.
            if _is_not_found(error):
                return None
            raise

    async def _delete_temporary_object(self, key: str, version_id: str) -> None:
        await asyncio.to_thread(
            self.client.delete_object,
            Bucket=self.bucket,
            Key=key,
            VersionId=version_id,
        )


def _part_count(byte_size: int, part_size_bytes: int) -> int:
    count = (byte_size + part_size_bytes - 1) // part_size_bytes
    if count > MAX_PART_COUNT:
        raise ValueError("upload exceeds the S3 multipart part limit")
    return max(count, 1)


def _normalized_parts(parts: Sequence[MultipartPart]) -> tuple[MultipartPart, ...]:
    if not parts:
        raise ValueError("multipart completion requires at least one part")
    normalized = tuple(sorted(parts, key=lambda part: part.part_number))
    expected = tuple(range(1, len(normalized) + 1))
    if tuple(part.part_number for part in normalized) != expected:
        raise ValueError("multipart part numbers must be contiguous starting at one")
    return normalized


def _is_not_found(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return False
    detail = response.get("Error")
    if not isinstance(detail, Mapping):
        return False
    return detail.get("Code") in {"404", "NoSuchKey", "NotFound"}


def _validate_existing_immutable_object(
    response: Mapping[str, Any], expected_size: int, expected_sha256: str
) -> None:
    metadata = response.get("Metadata")
    if not isinstance(metadata, Mapping):
        raise ObjectStorageError("existing immutable object has no metadata")
    if response.get("ContentLength") != expected_size or metadata.get("sha256") != expected_sha256:
        raise ObjectStorageError("immutable object key collides with different content")


def _require_version_id(response: Mapping[str, Any], object_name: str) -> str:
    version_id = response.get("VersionId")
    if not isinstance(version_id, str) or not version_id:
        raise ObjectStorageError(f"{object_name} has no version ID")
    return version_id
