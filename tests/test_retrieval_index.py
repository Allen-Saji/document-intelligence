import json
from uuid import UUID

import pytest

from document_intelligence.retrieval.index import (
    ChunkIndexRecord,
    build_bulk_payload,
    build_chunk_index_definition,
)

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
CORPUS_ID = UUID("00000000-0000-4000-8000-000000000004")


def record(*, chunk_id: str, embedding: tuple[float, ...] = (0.1, 0.2)) -> ChunkIndexRecord:
    return ChunkIndexRecord(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        corpus_id=CORPUS_ID,
        document_id="fixture-document",
        document_version_id=UUID("00000000-0000-4000-8000-000000000010"),
        chunk_id=UUID(chunk_id),
        page_number=1,
        content="Fixture content",
        embedding=embedding,
    )


def test_chunk_index_definition_contains_vector_and_authorization_fields() -> None:
    definition = build_chunk_index_definition(384)

    properties = definition["mappings"]["properties"]
    assert properties["embedding"] == {
        "type": "knn_vector",
        "dimension": 384,
        "method": {
            "name": "hnsw",
            "engine": "lucene",
            "space_type": "cosinesimil",
        },
    }
    assert properties["organization_id"] == {"type": "keyword"}
    assert properties["is_searchable"] == {"type": "boolean"}


def test_bulk_payload_is_newline_delimited_and_pins_index_name() -> None:
    payload = build_bulk_payload(
        "chunks-v1",
        [record(chunk_id="00000000-0000-4000-8000-000000000011")],
    )

    lines = payload.splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {
        "index": {
            "_index": "chunks-v1",
            "_id": "00000000-0000-4000-8000-000000000011",
        }
    }
    assert json.loads(lines[1])["content"] == "Fixture content"


def test_bulk_payload_rejects_mixed_embedding_dimensions() -> None:
    records = [
        record(chunk_id="00000000-0000-4000-8000-000000000011"),
        record(chunk_id="00000000-0000-4000-8000-000000000012", embedding=(0.1,)),
    ]

    with pytest.raises(ValueError, match="same embedding dimensions"):
        build_bulk_payload("chunks-v1", records)
