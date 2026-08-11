from __future__ import annotations

import pytest

from document_intelligence.retrieval.lifecycle import OpenSearchIndexManager, versioned_index_name


class FakeIndices:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict[str, object]]] = []
        self.alias_updates: list[dict[str, object]] = []

    def create(self, *, index: str, body: dict[str, object]) -> None:
        self.created.append((index, body))

    def update_aliases(self, *, body: dict[str, object]) -> None:
        self.alias_updates.append(body)


class FakeOpenSearch:
    def __init__(self) -> None:
        self.indices = FakeIndices()


def test_index_manager_creates_versioned_indexes_and_publishes_with_atomic_alias_swap() -> None:
    client = FakeOpenSearch()
    manager = OpenSearchIndexManager(client)  # type: ignore[arg-type]

    index_name = manager.create_version(
        alias_name="workspace_chunks", pipeline_version="2026.08.11", embedding_dimensions=3
    )
    manager.publish(
        alias_name="workspace_chunks",
        index_name=index_name,
        previous_index_name="workspace_chunks-v2026-08-10",
    )

    assert index_name == "workspace_chunks-v2026-08-11"
    assert client.indices.created[0][1]["mappings"]
    assert client.indices.alias_updates == [
        {
            "actions": [
                {"remove": {"index": "workspace_chunks-v2026-08-10", "alias": "workspace_chunks"}},
                {"add": {"index": index_name, "alias": "workspace_chunks"}},
            ]
        }
    ]


def test_index_names_reject_unsafe_aliases_and_versions() -> None:
    with pytest.raises(ValueError, match="safe index name"):
        versioned_index_name(alias_name="workspace chunks", pipeline_version="v1")
