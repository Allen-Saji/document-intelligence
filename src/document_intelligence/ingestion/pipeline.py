from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from document_intelligence.ingestion.contracts import (
    IngestionDocument,
    IngestionStage,
    ProcessingOutcome,
)
from document_intelligence.retrieval.index import ChunkIndexRecord

CHUNK_NAMESPACE = UUID("839ba940-58c8-4ce3-81a6-c94f1be4ffcd")


class IngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: UUID
    workspace_id: UUID
    corpus_id: UUID
    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    source_object_key: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pipeline_version: str = Field(min_length=1)


class TextChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    page_number: int = Field(ge=1)
    content: str = Field(min_length=1)


class MalwareScanner(Protocol):
    async def scan(self, object_key: str, sha256: str) -> None: ...


class DocumentParser(Protocol):
    async def parse(self, object_key: str) -> IngestionDocument: ...


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]: ...


class SearchPublisher(Protocol):
    async def publish(self, records: Sequence[ChunkIndexRecord], idempotency_key: str) -> None: ...


class IngestionPipeline:
    """Run scan, parse, quarantine, chunk, embedding, and idempotent publication in order."""

    def __init__(
        self,
        *,
        scanner: MalwareScanner,
        parser: DocumentParser,
        embedder: Embedder,
        publisher: SearchPublisher,
    ) -> None:
        self._scanner = scanner
        self._parser = parser
        self._embedder = embedder
        self._publisher = publisher

    async def run(self, request: IngestionRequest) -> ProcessingOutcome:
        try:
            await self._scanner.scan(request.source_object_key, request.source_sha256)
            document = await self._parser.parse(request.source_object_key)
            if (
                document.source.object_key != request.source_object_key
                or document.source.sha256 != request.source_sha256
            ):
                raise ValueError("parser result does not match the verified source")
            chunks = chunk_document(document)
            if not chunks:
                return ProcessingOutcome(
                    stage=IngestionStage.FAILED,
                    searchable_page_count=0,
                    quarantined_page_count=len(document.quarantined_pages),
                    failure_code="no_searchable_content",
                )
            vectors = await self._embedder.embed([chunk.content for chunk in chunks])
            if len(vectors) != len(chunks) or any(not vector for vector in vectors):
                raise ValueError("embedder returned invalid vector count or empty vector")
            if len({len(vector) for vector in vectors}) != 1:
                raise ValueError("embedder returned inconsistent vector dimensions")
            records = tuple(
                ChunkIndexRecord(
                    organization_id=request.organization_id,
                    workspace_id=request.workspace_id,
                    corpus_id=request.corpus_id,
                    document_id=request.document_id,
                    document_version_id=document.source.document_version_id,
                    chunk_id=chunk.id,
                    page_number=chunk.page_number,
                    content=chunk.content,
                    embedding=vector,
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            )
            await self._publisher.publish(
                records, publication_key(document, request.pipeline_version)
            )
            return ProcessingOutcome(
                stage=IngestionStage.PUBLISHED,
                searchable_page_count=len(document.searchable_pages),
                quarantined_page_count=len(document.quarantined_pages),
            )
        except ValueError:
            raise
        except Exception:
            return ProcessingOutcome(
                stage=IngestionStage.FAILED,
                searchable_page_count=0,
                quarantined_page_count=0,
                failure_code="ingestion_dependency_failure",
            )


def chunk_document(document: IngestionDocument, max_chars: int = 900) -> tuple[TextChunk, ...]:
    if max_chars < 100:
        raise ValueError("chunk size must be at least 100 characters")
    chunks: list[TextChunk] = []
    for page in document.searchable_pages:
        text = page.text.strip()
        for ordinal, start in enumerate(range(0, len(text), max_chars)):
            content = text[start : start + max_chars].strip()
            if content:
                identity = (
                    f"{document.source.document_version_id}:{page.page_number}:{ordinal}:{content}"
                )
                chunk_id = uuid5(CHUNK_NAMESPACE, identity)
                chunks.append(TextChunk(id=chunk_id, page_number=page.page_number, content=content))
    return tuple(chunks)


def publication_key(document: IngestionDocument, pipeline_version: str) -> str:
    value = f"{document.source.document_version_id}:{document.source.sha256}:{pipeline_version}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
