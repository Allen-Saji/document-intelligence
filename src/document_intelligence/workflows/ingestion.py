from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from temporalio import workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from document_intelligence.ingestion.pipeline import IngestionRequest
from document_intelligence.ingestion.removal import ProjectionRemovalRequest
from document_intelligence.storage.multipart import StoredObject

INGESTION_TASK_QUEUE = "document-intelligence-ingestion-v1"


class IngestionInput(BaseModel):
    """Immutable workflow input. It contains IDs and object metadata, never PDF content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: UUID
    workspace_id: UUID
    document_id: UUID
    document_version_id: UUID
    source_object_key: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pipeline_version: str = Field(min_length=1, max_length=120)


def ingestion_workflow_id(input: IngestionInput) -> str:
    return f"ingest:{input.document_version_id}:{input.pipeline_version}"


def document_ingestion_workflow_id(request: IngestionRequest) -> str:
    """Use the immutable version and parser version to deduplicate a full ingest."""

    return f"ingest:{request.document_version_id}:{request.pipeline_version}"


def projection_removal_workflow_id(request: ProjectionRemovalRequest) -> str:
    publication = request.publication
    return (
        f"projection-removal:{publication.organization_id}:{publication.workspace_id}:"
        f"{publication.document_version_id}:"
        f"{publication.idempotency_key}:{request.operation}"
    )


@workflow.defn
class IngestionWorkflow:
    """Durable ordered ingestion shell registered by the worker deployment."""

    @workflow.run
    async def run(self, input: IngestionInput) -> str:
        await workflow.execute_activity(
            "verify_source",
            input,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        await workflow.execute_activity(
            "parse_and_quarantine",
            input,
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        await workflow.execute_activity(
            "index_searchable_content",
            input,
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return str(input.document_version_id)


@workflow.defn
class DocumentIngestionWorkflow:
    """Phase 2 workflow whose worker activity owns scan-to-publication recovery."""

    @workflow.run
    async def run(self, request: IngestionRequest) -> str:
        await workflow.execute_activity(
            "run_ingestion_pipeline",
            request,
            start_to_close_timeout=timedelta(minutes=45),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return request.document_id


@workflow.defn
class DocumentProjectionRemovalWorkflow:
    """Durably remove an indexed version for rollback or deletion."""

    @workflow.run
    async def run(self, request: ProjectionRemovalRequest) -> str:
        await workflow.execute_activity(
            "remove_document_projection",
            request,
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return str(request.publication.document_version_id)


class TemporalIngestionStarter:
    """Start exactly one active workflow for an immutable version and pipeline version."""

    def __init__(self, *, client: Client, pipeline_version: str) -> None:
        self._client = client
        self._pipeline_version = pipeline_version

    async def start(self, stored: StoredObject) -> None:
        final_key = stored.reservation.final_object_key
        if final_key is None:
            raise ValueError("an immutable source object is required for ingestion")
        input = IngestionInput(
            organization_id=stored.reservation.organization_id,
            workspace_id=stored.reservation.workspace_id,
            document_id=stored.reservation.document_id,
            document_version_id=stored.reservation.document_version_id,
            source_object_key=final_key,
            source_sha256=stored.sha256,
            pipeline_version=self._pipeline_version,
        )
        await self._client.start_workflow(
            IngestionWorkflow.run,
            input,
            id=ingestion_workflow_id(input),
            task_queue=INGESTION_TASK_QUEUE,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )


class TemporalDocumentIngestionStarter:
    """Start the scan-to-publication workflow after immutable upload promotion.

    The caller selects the destination corpus. Upload storage intentionally does
    not infer corpus membership from an object key.
    """

    def __init__(self, *, client: Client, pipeline_version: str) -> None:
        self._client = client
        self._pipeline_version = pipeline_version

    async def start(self, stored: StoredObject) -> None:
        final_key = stored.reservation.final_object_key
        if final_key is None:
            raise ValueError("an immutable source object is required for ingestion")
        request = IngestionRequest(
            organization_id=stored.reservation.organization_id,
            workspace_id=stored.reservation.workspace_id,
            corpus_id=stored.reservation.corpus_id,
            document_id=str(stored.reservation.document_id),
            document_version_id=stored.reservation.document_version_id,
            source_object_key=final_key,
            source_sha256=stored.sha256,
            pipeline_version=self._pipeline_version,
        )
        await self._client.start_workflow(
            DocumentIngestionWorkflow.run,
            request,
            id=document_ingestion_workflow_id(request),
            task_queue=INGESTION_TASK_QUEUE,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )


class TemporalProjectionRemovalStarter:
    """Start one idempotent rollback or deletion workflow per publication."""

    def __init__(self, *, client: Client) -> None:
        self._client = client

    async def start(self, request: ProjectionRemovalRequest) -> None:
        await self._client.start_workflow(
            DocumentProjectionRemovalWorkflow.run,
            request,
            id=projection_removal_workflow_id(request),
            task_queue=INGESTION_TASK_QUEUE,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
