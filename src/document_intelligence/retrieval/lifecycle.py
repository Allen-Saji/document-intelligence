from __future__ import annotations

import re
from typing import Any, Protocol

from document_intelligence.retrieval.index import build_chunk_index_definition

INDEX_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,119}$")


class OpenSearchIndices(Protocol):
    def create(self, *, index: str, body: dict[str, object]) -> Any: ...

    def update_aliases(self, *, body: dict[str, object]) -> Any: ...


class OpenSearchClient(Protocol):
    indices: OpenSearchIndices


def versioned_index_name(*, alias_name: str, pipeline_version: str) -> str:
    normalized_alias = alias_name.casefold()
    normalized_version = pipeline_version.casefold().replace(".", "-")
    index_name = f"{normalized_alias}-v{normalized_version}"
    if not INDEX_NAME_PATTERN.fullmatch(index_name):
        raise ValueError("index alias and pipeline version must form a safe index name")
    return index_name


class OpenSearchIndexManager:
    """Create immutable index versions and atomically move read aliases between them."""

    def __init__(self, client: OpenSearchClient) -> None:
        self._client = client

    def create_version(
        self, *, alias_name: str, pipeline_version: str, embedding_dimensions: int
    ) -> str:
        index_name = versioned_index_name(alias_name=alias_name, pipeline_version=pipeline_version)
        self._client.indices.create(
            index=index_name, body=build_chunk_index_definition(embedding_dimensions)
        )
        return index_name

    def publish(self, *, alias_name: str, index_name: str, previous_index_name: str | None) -> None:
        if not INDEX_NAME_PATTERN.fullmatch(alias_name) or not INDEX_NAME_PATTERN.fullmatch(
            index_name
        ):
            raise ValueError("alias and index names must be safe OpenSearch identifiers")
        actions: list[dict[str, dict[str, str]]] = []
        if previous_index_name is not None:
            actions.append({"remove": {"index": previous_index_name, "alias": alias_name}})
        actions.append({"add": {"index": index_name, "alias": alias_name}})
        self._client.indices.update_aliases(body={"actions": actions})

    def rollback(self, *, alias_name: str, failed_index_name: str, stable_index_name: str) -> None:
        self.publish(
            alias_name=alias_name,
            index_name=stable_index_name,
            previous_index_name=failed_index_name,
        )
