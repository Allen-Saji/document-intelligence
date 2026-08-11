from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Protocol
from uuid import UUID

from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from document_intelligence.api.dependencies import ApiKeyLookup
from document_intelligence.audit import AuditEventDraft
from document_intelligence.auth.contracts import ApiKeyPrincipal, ApiKeyRecord
from document_intelligence.config import Settings
from document_intelligence.core.tenancy import DatabaseTenantContext
from document_intelligence.database.engine import create_database_engine
from document_intelligence.database.repositories import PostgresTenantRepository
from document_intelligence.database.tenancy import tenant_transaction
from document_intelligence.documents.services import UploadNotFoundError
from document_intelligence.documents.uploads import (
    UploadIntent,
    UploadReservation,
    abort_upload,
    reserve_upload,
)
from document_intelligence.generation.openai import OpenAIResponsesProvider
from document_intelligence.generation.orchestration import (
    AnswerOrchestrator,
    AnswerPipelineConfig,
)
from document_intelligence.generation.service import AnswerService
from document_intelligence.retrieval.embeddings import (
    RuntimeDependencyError,
    SentenceTransformerQueryEmbedder,
)
from document_intelligence.retrieval.opensearch import OpenSearchCandidateRetriever
from document_intelligence.retrieval.opensearch_client import AsyncOpenSearchSearchClient
from document_intelligence.retrieval.service import RetrievalService
from document_intelligence.storage.multipart import (
    MultipartPart,
    MultipartUploadPlan,
    S3CompatibleObjectStore,
    StoredObject,
)
from document_intelligence.workflows.ingestion import TemporalDocumentIngestionStarter


class CloseableService(Protocol):
    async def close(self) -> None: ...


class RuntimeServiceBundle:
    def __init__(
        self,
        *,
        database_engine: AsyncEngine,
        search_client: CloseableService,
        api_key_lookup: ApiKeyLookup,
        corpus_access_resolver: Callable[[ApiKeyPrincipal], Awaitable[tuple[UUID, ...]]],
        upload_service: DatabaseUploadService,
        answer_orchestrator: AnswerOrchestrator,
    ) -> None:
        self._database_engine = database_engine
        self._search_client = search_client
        self.api_key_lookup = api_key_lookup
        self.corpus_access_resolver = corpus_access_resolver
        self.upload_service = upload_service
        self.answer_orchestrator = answer_orchestrator

    def install(self, app: FastAPI) -> None:
        app.state.api_key_lookup = self.api_key_lookup
        app.state.corpus_access_resolver = self.corpus_access_resolver
        app.state.upload_service = self.upload_service
        app.state.answer_orchestrator = self.answer_orchestrator
        app.state.runtime_services = self
        app.state.runtime_composition_errors = ()

    async def close(self) -> None:
        await self._search_client.close()
        await self._database_engine.dispose()


def configure_runtime_services(app: FastAPI, settings: Settings) -> RuntimeServiceBundle | None:
    if settings.env not in {"staging", "production"}:
        app.state.runtime_composition_errors = ()
        return None
    if settings.missing_production_settings():
        app.state.runtime_composition_errors = ()
        return None
    try:
        bundle = build_runtime_services(settings)
    except (RuntimeDependencyError, ValueError) as error:
        app.state.runtime_composition_errors = (str(error),)
        return None
    bundle.install(app)
    return bundle


def build_runtime_services(settings: Settings) -> RuntimeServiceBundle:
    database_engine = create_database_engine(settings)
    search_client = AsyncOpenSearchSearchClient(url=_required_secret(settings.opensearch_url))
    embedder = SentenceTransformerQueryEmbedder(model_name=settings.embedding_model_name)
    retrieval_service = RetrievalService(
        retriever=OpenSearchCandidateRetriever(
            client=search_client,
            index_name=_required(settings.opensearch_index_name, "opensearch_index_name"),
        )
    )
    answer_service = AnswerService(_generation_provider(settings))
    upload_store = S3CompatibleObjectStore.from_settings(settings)
    ingestion_starter = LazyTemporalDocumentIngestionStarter(
        target=_required_secret(settings.temporal_target),
        pipeline_version=_required(
            settings.ingestion_pipeline_version, "ingestion_pipeline_version"
        ),
    )
    return RuntimeServiceBundle(
        database_engine=database_engine,
        search_client=search_client,
        api_key_lookup=DatabaseApiKeyLookup(database_engine),
        corpus_access_resolver=DatabaseCorpusAccessResolver(database_engine),
        upload_service=DatabaseUploadService(
            engine=database_engine,
            store=upload_store,
            start_ingestion=ingestion_starter.start,
        ),
        answer_orchestrator=AnswerOrchestrator(
            query_embedder=embedder,
            retrieval_service=retrieval_service,
            answer_service=answer_service,
            config=AnswerPipelineConfig(
                index_version=_required(
                    settings.retrieval_index_version, "retrieval_index_version"
                ),
                pipeline_version=_required(
                    settings.answer_pipeline_version, "answer_pipeline_version"
                ),
            ),
        ),
    )


class DatabaseApiKeyLookup:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def __call__(self, token_prefix: str) -> ApiKeyRecord | None:
        async with self._engine.connect() as connection:
            return await PostgresTenantRepository(connection).lookup_api_key_by_prefix(token_prefix)


class DatabaseCorpusAccessResolver:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def __call__(self, principal: ApiKeyPrincipal) -> tuple[UUID, ...]:
        tenant = DatabaseTenantContext(
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
        )
        async with self._engine.connect() as connection, tenant_transaction(connection, tenant):
            return await PostgresTenantRepository(connection).readable_corpus_ids(
                principal.actor_id
            )


class DatabaseUploadService:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        store: S3CompatibleObjectStore,
        start_ingestion: Callable[[StoredObject], Awaitable[None]],
    ) -> None:
        self._engine = engine
        self._store = store
        self._start_ingestion = start_ingestion

    async def reserve(
        self, tenant: DatabaseTenantContext, intent: UploadIntent
    ) -> MultipartUploadPlan:
        await self._authorize_corpus(tenant, intent.corpus_id)
        reservation = reserve_upload(
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            actor_id=tenant.actor_id,
            intent=intent,
        )
        await self._store.require_versioned_bucket()
        plan = await self._store.begin_multipart_upload(reservation)
        async with self._repository(tenant) as repository:
            await repository.create(plan.reservation)
            await self._audit(repository, plan.reservation, "document.upload_reserved")
        return plan

    async def complete(
        self,
        tenant: DatabaseTenantContext,
        reservation_id: UUID,
        parts: tuple[MultipartPart, ...],
    ) -> StoredObject:
        reservation = await self._required_reservation(tenant, reservation_id)
        await self._authorize_corpus(tenant, reservation.corpus_id)
        stored = await self._store.finalize_multipart_upload(reservation, parts)
        async with self._repository(tenant) as repository:
            await repository.record_promoted_object(stored)
            await self._audit(repository, stored.reservation, "document.upload_completed")
        await self._start_ingestion(stored)
        return stored

    async def abort(
        self, tenant: DatabaseTenantContext, reservation_id: UUID
    ) -> UploadReservation:
        reservation = await self._required_reservation(tenant, reservation_id)
        await self._store.abort_multipart_upload(reservation)
        aborted = abort_upload(reservation)
        async with self._repository(tenant) as repository:
            await repository.update(aborted)
            await self._audit(repository, aborted, "document.upload_aborted")
        return aborted

    async def signed_read(self, tenant: DatabaseTenantContext, reservation_id: UUID) -> str:
        reservation = await self._required_reservation(tenant, reservation_id)
        if reservation.final_object_key is None:
            raise ValueError("upload has not been promoted")
        return await self._store.signed_read_url(reservation.final_object_key)

    async def _authorize_corpus(self, tenant: DatabaseTenantContext, corpus_id: UUID) -> None:
        async with self._repository(tenant) as repository:
            allowed = await repository.can_ingest_into_corpus(
                actor_id=tenant.actor_id, corpus_id=corpus_id
            )
        if not allowed:
            raise PermissionError("corpus is not writable")

    async def _required_reservation(
        self, tenant: DatabaseTenantContext, reservation_id: UUID
    ) -> UploadReservation:
        async with self._repository(tenant) as repository:
            reservation = await repository.get(reservation_id)
        if reservation is None:
            raise UploadNotFoundError("upload reservation not found")
        return reservation

    async def _audit(
        self,
        repository: PostgresTenantRepository,
        reservation: UploadReservation,
        event_type: str,
    ) -> None:
        await repository.append_audit(
            AuditEventDraft(
                organization_id=reservation.organization_id,
                workspace_id=reservation.workspace_id,
                actor_id=reservation.actor_id,
                event_type=event_type,
                target_type="upload_reservation",
                target_id=reservation.id,
                occurred_at=reservation.completed_at or reservation.created_at,
            )
        )

    def _repository(self, tenant: DatabaseTenantContext) -> DatabaseRepositoryContext:
        return DatabaseRepositoryContext(self._engine, tenant)


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


class LazyTemporalDocumentIngestionStarter:
    def __init__(self, *, target: SecretStr, pipeline_version: str) -> None:
        self._target = target
        self._pipeline_version = pipeline_version
        self._client: object | None = None
        self._lock = asyncio.Lock()

    async def start(self, stored: StoredObject) -> None:
        client = await self._client_instance()
        await TemporalDocumentIngestionStarter(
            client=client,  # type: ignore[arg-type]
            pipeline_version=self._pipeline_version,
        ).start(stored)

    async def _client_instance(self) -> object:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                from temporalio.client import Client

                self._client = await Client.connect(
                    self._target.get_secret_value(),
                    lazy=True,
                )
        return self._client


def _generation_provider(settings: Settings) -> OpenAIResponsesProvider:
    provider = _required(settings.generation_provider, "generation_provider")
    if provider.casefold() != "openai":
        raise ValueError("unsupported generation provider")
    return OpenAIResponsesProvider(
        api_key=_required_secret(settings.openai_api_key),
        model=_required(settings.generation_model, "generation_model"),
    )


def _required(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} must be configured")
    return value


def _required_secret(value: SecretStr | None) -> SecretStr:
    if value is None or not value.get_secret_value():
        raise ValueError("secret setting must be configured")
    return value
