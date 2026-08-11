from __future__ import annotations

from temporalio import activity

from document_intelligence.ingestion.contracts import ProcessingOutcome
from document_intelligence.ingestion.pipeline import IngestionPipeline, IngestionRequest


class IngestionActivities:
    """Temporal worker adapter. Dependencies are supplied by the worker composition root."""

    def __init__(self, pipeline: IngestionPipeline) -> None:
        self._pipeline = pipeline

    @activity.defn(name="run_ingestion_pipeline")
    async def run_ingestion_pipeline(self, request: IngestionRequest) -> ProcessingOutcome:
        return await self._pipeline.run(request)
