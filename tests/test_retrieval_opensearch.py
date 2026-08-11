from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import pytest

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.provenance import PageRegion
from document_intelligence.retrieval.index import ChunkIndexRecord
from document_intelligence.retrieval.opensearch import OpenSearchCandidateRetriever
from document_intelligence.retrieval.opensearch_client import OpenSearchBulkIndexProjection
from document_intelligence.retrieval.query import HybridQueryInput

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
CORPUS_ID = UUID("00000000-0000-4000-8000-000000000003")
DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000005")
CHUNK_ID = UUID("00000000-0000-4000-8000-000000000006")


class Client:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    async def search(self, *, index: str, body: Mapping[str, object]) -> Mapping[str, object]:
        self.calls.append((index, body))
        return self.response


def tenant() -> TenantContext:
    return TenantContext(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=UUID("00000000-0000-4000-8000-000000000004"),
        allowed_corpus_ids=(CORPUS_ID,),
    )


def response() -> Mapping[str, object]:
    return {
        "hits": {
            "hits": [
                {
                    "_score": 0.8,
                    "_source": {
                        "organization_id": str(ORG_ID),
                        "workspace_id": str(WORKSPACE_ID),
                        "corpus_id": str(CORPUS_ID),
                        "document_id": "protocol",
                        "document_version_id": "00000000-0000-4000-8000-000000000005",
                        "chunk_id": "00000000-0000-4000-8000-000000000006",
                        "page_number": 1,
                        "content": "trusted evidence",
                        "source_region": {
                            "left": 1.0,
                            "top": 2.0,
                            "right": 30.0,
                            "bottom": 40.0,
                        },
                    },
                }
            ]
        }
    }


@pytest.mark.asyncio
async def test_adapter_builds_tenant_filtered_queries_for_each_candidate_branch() -> None:
    client = Client(response())
    retriever = OpenSearchCandidateRetriever(client=client, index_name="chunks-v1")
    query = HybridQueryInput(question="What is finality?", query_vector=(0.1, 0.2))

    lexical = await retriever.lexical(query, tenant())
    dense = await retriever.dense(query, tenant())

    assert [item.record.content for item in lexical] == ["trusted evidence"]
    assert lexical[0].record.source_region == PageRegion(left=1.0, top=2.0, right=30.0, bottom=40.0)
    assert [item.record.content for item in dense] == ["trusted evidence"]
    lexical_filters = client.calls[0][1]["query"]["bool"]["filter"]
    dense_filter = client.calls[1][1]["query"]["knn"]["embedding"]["filter"]
    assert lexical_filters == dense_filter


@pytest.mark.asyncio
async def test_adapter_rejects_malformed_search_response() -> None:
    retriever = OpenSearchCandidateRetriever(client=Client({}), index_name="chunks-v1")
    query = HybridQueryInput(question="What is finality?", query_vector=(0.1,))

    with pytest.raises(ValueError, match="did not contain hits"):
        await retriever.lexical(query, tenant())


@pytest.mark.asyncio
async def test_bulk_projection_publishes_and_deletes_document_versions() -> None:
    class BulkClient:
        def __init__(self) -> None:
            self.bulk_body = ""
            self.deleted: dict[str, object] | None = None

        async def bulk(self, *, body: str) -> dict[str, object]:
            self.bulk_body = body
            return {"errors": False}

        async def delete_by_query(
            self, *, index: str, body: dict[str, object]
        ) -> dict[str, object]:
            self.deleted = {"index": index, "body": body}
            return {"deleted": 1}

    client = BulkClient()
    projection = OpenSearchBulkIndexProjection(client=client, index_name="chunks-current")  # type: ignore[arg-type]
    record = ChunkIndexRecord(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        corpus_id=CORPUS_ID,
        document_id="protocol",
        document_version_id=DOCUMENT_VERSION_ID,
        chunk_id=CHUNK_ID,
        page_number=1,
        content="trusted evidence",
        embedding=(0.1, 0.2),
    )

    await projection.upsert((record,))
    await projection.delete_version(DOCUMENT_VERSION_ID)

    assert '"_index": "chunks-current"' in client.bulk_body
    assert client.deleted == {
        "index": "chunks-current",
        "body": {"query": {"term": {"document_version_id": str(DOCUMENT_VERSION_ID)}}},
    }
