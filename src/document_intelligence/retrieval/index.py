from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChunkIndexRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: UUID
    workspace_id: UUID
    corpus_id: UUID
    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    document_version_id: UUID
    chunk_id: UUID
    page_number: int = Field(ge=1)
    block_type: Literal["text", "table", "code", "formula", "picture"] = "text"
    content: str = Field(min_length=1)
    embedding: tuple[float, ...] = Field(min_length=1)
    is_searchable: bool = True


def build_chunk_index_definition(embedding_dimensions: int) -> dict[str, object]:
    if embedding_dimensions < 1:
        raise ValueError("embedding_dimensions must be positive")
    return {
        "settings": {
            "index": {
                "knn": True,
                "number_of_shards": 1,
                "number_of_replicas": 0,
            }
        },
        "mappings": {
            "properties": {
                "organization_id": {"type": "keyword"},
                "workspace_id": {"type": "keyword"},
                "corpus_id": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "document_version_id": {"type": "keyword"},
                "chunk_id": {"type": "keyword"},
                "page_number": {"type": "integer"},
                "block_type": {"type": "keyword"},
                "content": {"type": "text"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": embedding_dimensions,
                    "method": {
                        "name": "hnsw",
                        "engine": "lucene",
                        "space_type": "cosinesimil",
                    },
                },
                "is_searchable": {"type": "boolean"},
            }
        },
    }


def build_bulk_payload(index_name: str, records: list[ChunkIndexRecord]) -> str:
    if not index_name:
        raise ValueError("index_name must not be empty")
    if not records:
        raise ValueError("records must not be empty")
    dimensions = len(records[0].embedding)
    if any(len(record.embedding) != dimensions for record in records):
        raise ValueError("all records must use the same embedding dimensions")

    lines: list[str] = []
    for record in records:
        lines.append(json.dumps({"index": {"_index": index_name, "_id": str(record.chunk_id)}}))
        lines.append(record.model_dump_json())
    return "\n".join(lines) + "\n"
