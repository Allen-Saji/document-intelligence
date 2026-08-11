from __future__ import annotations

from temporalio import activity

from document_intelligence.ingestion.contracts import ProcessingOutcome
from document_intelligence.ingestion.pipeline import IngestionPipeline, IngestionRequest
from document_intelligence.ingestion.publication import IdempotentPublisher, PublicationRecord
from document_intelligence.ingestion.removal import (
    ProjectionRemovalOperation,
    ProjectionRemovalRequest,
)


class IngestionActivities:
    """Temporal worker adapter. Dependencies are supplied by the worker composition root."""

    def __init__(self, pipeline: IngestionPipeline) -> None:
        self._pipeline = pipeline

    @activity.defn(name="run_ingestion_pipeline")
    async def run_ingestion_pipeline(self, request: IngestionRequest) -> ProcessingOutcome:
        return await self._pipeline.run(request)


class PublicationActivities:
    """Temporal adapter for idempotent search-projection removal."""

    def __init__(self, publisher: IdempotentPublisher) -> None:
        self._publisher = publisher

    @activity.defn(name="remove_document_projection")
    async def remove_document_projection(
        self, request: ProjectionRemovalRequest
    ) -> PublicationRecord:
        if request.operation == ProjectionRemovalOperation.ROLLBACK:
            return await self._publisher.rollback(request.publication)
        return await self._publisher.delete(request.publication)
