from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from pydantic import SecretStr

from document_intelligence.retrieval.embeddings import RuntimeDependencyError
from document_intelligence.retrieval.index import ChunkIndexRecord, build_bulk_payload


class AsyncOpenSearchSearchClient:
    """Small adapter around opensearch-py's async client."""

    def __init__(self, *, url: SecretStr) -> None:
        if importlib.util.find_spec("opensearchpy") is None:
            raise RuntimeDependencyError("opensearch-py package is not installed")
        module = importlib.import_module("opensearchpy")
        async_opensearch = module.AsyncOpenSearch

        self._client: Any = async_opensearch(hosts=[url.get_secret_value()])

    async def search(self, *, index: str, body: Mapping[str, object]) -> Mapping[str, object]:
        response = await self._client.search(index=index, body=dict(body))
        if not isinstance(response, Mapping):
            raise ValueError("OpenSearch client returned a non-mapping response")
        return response

    async def bulk(self, *, body: str) -> Mapping[str, object]:
        response = await self._client.bulk(body=body)
        if not isinstance(response, Mapping):
            raise ValueError("OpenSearch client returned a non-mapping bulk response")
        if response.get("errors") is True:
            raise ValueError("OpenSearch bulk publish reported item errors")
        return response

    async def delete_by_query(
        self, *, index: str, body: Mapping[str, object]
    ) -> Mapping[str, object]:
        response = await self._client.delete_by_query(index=index, body=dict(body))
        if not isinstance(response, Mapping):
            raise ValueError("OpenSearch client returned a non-mapping delete response")
        return response

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()


class OpenSearchBulkIndexProjection:
    """Publish and remove chunk projections in one configured OpenSearch index or alias."""

    def __init__(self, *, client: AsyncOpenSearchSearchClient, index_name: str) -> None:
        if not index_name:
            raise ValueError("index_name must not be empty")
        self._client = client
        self._index_name = index_name

    async def upsert(self, records: Sequence[ChunkIndexRecord]) -> None:
        await self._client.bulk(
            body=build_bulk_payload(self._index_name, list(records)),
        )

    async def delete_version(self, document_version_id: UUID) -> None:
        await self._client.delete_by_query(
            index=self._index_name,
            body={
                "query": {
                    "term": {
                        "document_version_id": str(document_version_id),
                    }
                }
            },
        )
