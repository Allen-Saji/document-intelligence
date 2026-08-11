from __future__ import annotations

from types import TracebackType
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.worker import Worker

from document_intelligence.config import Settings
from document_intelligence.core.tenancy import DatabaseTenantContext
from document_intelligence.database.engine import create_database_engine
from document_intelligence.database.repositories import PostgresTenantRepository
from document_intelligence.database.tenancy import tenant_transaction
from document_intelligence.ingestion.activities import IngestionActivities, PublicationActivities
from document_intelligence.ingestion.adapters import (
    DoclingObjectParser,
    SourceAwareDocumentParser,
    SourceIntegrityScanner,
)
from document_intelligence.ingestion.pipeline import IngestionPipeline
from document_intelligence.ingestion.publication import IdempotentPublisher, PublicationRecord
from document_intelligence.retrieval.embeddings import SentenceTransformerQueryEmbedder
from document_intelligence.retrieval.opensearch_client import (
    AsyncOpenSearchSearchClient,
    OpenSearchBulkIndexProjection,
)
from document_intelligence.storage.multipart import S3CompatibleObjectStore
from document_intelligence.storage.source import S3SourceObjectReader
from document_intelligence.workflows.ingestion import (
    INGESTION_TASK_QUEUE,
    DocumentIngestionWorkflow,
    DocumentProjectionRemovalWorkflow,
)


class WorkerRuntimeBundle:
    def __init__(
        self,
        *,
        database_engine: AsyncEngine,
        search_client: AsyncOpenSearchSearchClient,
        activities: tuple[IngestionActivities, PublicationActivities],
    ) -> None:
        self._database_engine = database_engine
        self._search_client = search_client
        self.activities = activities

    async def close(self) -> None:
        await self._search_client.close()
        await self._database_engine.dispose()


def build_worker_runtime(settings: Settings) -> WorkerRuntimeBundle:
    missing = _missing_worker_settings(settings)
    if missing:
        raise ValueError(f"missing worker settings: {', '.join(missing)}")
    database_engine = create_database_engine(settings)
    search_client = AsyncOpenSearchSearchClient(url=_required_secret(settings.opensearch_url))
    store = S3CompatibleObjectStore.from_settings(settings)
    reader = S3SourceObjectReader(client=store.client, bucket=store.bucket)
    parser = SourceAwareDocumentParser(DoclingObjectParser(reader))
    embedder = SentenceTransformerQueryEmbedder(model_name=settings.embedding_model_name)
    projection = OpenSearchBulkIndexProjection(
        client=search_client,
        index_name=_required(settings.opensearch_index_name, "opensearch_index_name"),
    )
    publisher = IdempotentPublisher(
        projection=projection,
        ledger=PostgresPublicationLedger(database_engine),
    )
    pipeline = IngestionPipeline(
        scanner=SourceIntegrityScanner(reader),
        parser=parser,
        embedder=embedder,
        publisher=publisher,
    )
    return WorkerRuntimeBundle(
        database_engine=database_engine,
        search_client=search_client,
        activities=(
            IngestionActivities(pipeline),
            PublicationActivities(publisher),
        ),
    )


async def create_temporal_worker(settings: Settings, runtime: WorkerRuntimeBundle) -> Worker:
    from temporalio.client import Client

    client = await Client.connect(
        _required_secret(settings.temporal_target).get_secret_value(),
        lazy=True,
    )
    return Worker(
        client,
        task_queue=INGESTION_TASK_QUEUE,
        workflows=[DocumentIngestionWorkflow, DocumentProjectionRemovalWorkflow],
        activities=[
            runtime.activities[0].run_ingestion_pipeline,
            runtime.activities[1].remove_document_projection,
        ],
    )


class PostgresPublicationLedger:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get(
        self, idempotency_key: str, *, organization_id: UUID, workspace_id: UUID
    ) -> PublicationRecord | None:
        tenant = DatabaseTenantContext(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_id=organization_id,
        )
        async with DatabaseRepositoryContext(self._engine, tenant) as repository:
            return await repository.get_publication(idempotency_key)

    async def save(self, record: PublicationRecord) -> None:
        tenant = DatabaseTenantContext(
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            actor_id=record.organization_id,
        )
        async with DatabaseRepositoryContext(self._engine, tenant) as repository:
            await repository.save_publication(record)

class DatabaseRepositoryContext:
    def __init__(self, engine: AsyncEngine, tenant: DatabaseTenantContext) -> None:
        self._engine = engine
        self._tenant = tenant

    async def __aenter__(self) -> PostgresTenantRepository:
        self._connection_context = self._engine.connect()
        self._connection = await self._connection_context.__aenter__()
        self._transaction_context = tenant_transaction(self._connection, self._tenant)
        await self._transaction_context.__aenter__()
        return PostgresTenantRepository(self._connection)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._transaction_context.__aexit__(exc_type, exc, traceback)
        await self._connection_context.__aexit__(exc_type, exc, traceback)


def _required(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} must be configured")
    return value


def _required_secret(value: SecretStr | None) -> SecretStr:
    if value is None or not value.get_secret_value():
        raise ValueError("secret setting must be configured")
    return value


def _missing_worker_settings(settings: Settings) -> tuple[str, ...]:
    required = {
        "APP_DATABASE_URL": settings.database_url,
        "APP_OPENSEARCH_URL": settings.opensearch_url,
        "APP_OPENSEARCH_INDEX_NAME": settings.opensearch_index_name,
        "APP_TEMPORAL_TARGET": settings.temporal_target,
        "APP_S3_BUCKET": settings.s3_bucket,
        "APP_INGESTION_PIPELINE_VERSION": settings.ingestion_pipeline_version,
    }
    return tuple(name for name, value in required.items() if not _is_present(value))


def _is_present(value: str | SecretStr | None) -> bool:
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    return bool(value)
