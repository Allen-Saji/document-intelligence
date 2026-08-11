from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from document_intelligence.documents.uploads import UploadReservation, UploadState
from document_intelligence.storage.multipart import StoredObject
from document_intelligence.workflows.ingestion import (
    INGESTION_TASK_QUEUE,
    DocumentIngestionWorkflow,
    IngestionInput,
    IngestionWorkflow,
    TemporalDocumentIngestionStarter,
    TemporalIngestionStarter,
    document_ingestion_workflow_id,
    ingestion_workflow_id,
)


class RecordingTemporalClient:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None

    async def start_workflow(self, workflow: object, input: object, **kwargs: object) -> None:
        self.workflow = workflow
        self.input = input
        self.kwargs = kwargs


def stored_object() -> StoredObject:
    reservation = UploadReservation(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        organization_id=UUID("00000000-0000-4000-8000-000000000002"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000003"),
        actor_id=UUID("00000000-0000-4000-8000-000000000004"),
        document_id=UUID("00000000-0000-4000-8000-000000000005"),
        document_version_id=UUID("00000000-0000-4000-8000-000000000006"),
        display_name="Protocol",
        declared_size_bytes=12,
        state=UploadState.COMPLETED,
        multipart_object_key="temporary/object.pdf",
        multipart_upload_id="upload-1",
        final_object_key="immutable/object.pdf",
        sha256="a" * 64,
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
        expires_at=datetime(2026, 8, 12, tzinfo=UTC),
        completed_at=datetime(2026, 8, 11, 1, tzinfo=UTC),
    )
    return StoredObject(
        reservation=reservation,
        object_version_id="s3-version-1",
        byte_size=12,
        sha256="a" * 64,
    )


@pytest.mark.asyncio
async def test_temporal_starter_uses_stable_version_and_pipeline_idempotency_key() -> None:
    client = RecordingTemporalClient()
    starter = TemporalIngestionStarter(client=client, pipeline_version="2026.08.11")  # type: ignore[arg-type]

    await starter.start(stored_object())

    assert client.workflow == IngestionWorkflow.run
    assert isinstance(client.input, IngestionInput)
    assert client.kwargs is not None
    assert client.kwargs["task_queue"] == INGESTION_TASK_QUEUE
    assert client.kwargs["id"] == "ingest:00000000-0000-4000-8000-000000000006:2026.08.11"


def test_workflow_identity_changes_when_pipeline_or_document_version_changes() -> None:
    input = IngestionInput(
        organization_id=UUID("00000000-0000-4000-8000-000000000002"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000003"),
        document_id=UUID("00000000-0000-4000-8000-000000000005"),
        document_version_id=UUID("00000000-0000-4000-8000-000000000006"),
        source_object_key="immutable/object.pdf",
        source_sha256="a" * 64,
        pipeline_version="v1",
    )

    assert ingestion_workflow_id(input) == "ingest:00000000-0000-4000-8000-000000000006:v1"


@pytest.mark.asyncio
async def test_document_starter_launches_the_real_pipeline_with_stable_identity() -> None:
    client = RecordingTemporalClient()
    starter = TemporalDocumentIngestionStarter(
        client=client,  # type: ignore[arg-type]
        corpus_id=UUID("00000000-0000-4000-8000-000000000007"),
        pipeline_version="2026.08.11",
    )

    await starter.start(stored_object())

    assert client.workflow == DocumentIngestionWorkflow.run
    assert client.kwargs is not None
    assert client.kwargs["id"] == "ingest:00000000-0000-4000-8000-000000000006:2026.08.11"
    assert document_ingestion_workflow_id(client.input) == client.kwargs["id"]  # type: ignore[arg-type]
