from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.evaluation.retrieval import (
    EvidenceLocation,
    load_retrieval_dataset,
    recall_at_k,
    reciprocal_rank,
)
from document_intelligence.retrieval.index import (
    ChunkIndexRecord,
    build_bulk_payload,
    build_chunk_index_definition,
)
from document_intelligence.retrieval.query import HybridQueryInput, build_tenant_scoped_hybrid_query
from document_intelligence.retrieval.rerank import (
    SearchHit,
    rerank_hits,
    search_hit_from_response,
    validate_tenant_hits,
)

DIMENSIONS = 16
ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000003")
CORPUS_ID = UUID("00000000-0000-4000-8000-000000000004")
FOREIGN_ORG_ID = UUID("00000000-0000-4000-8000-000000000099")
FOREIGN_WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000098")
FOREIGN_CORPUS_ID = UUID("00000000-0000-4000-8000-000000000097")


class OpenSearchHttpError(RuntimeError):
    pass


class OpenSearchHttpClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        body: str | dict[str, Any] | None = None,
        content_type: str = "application/json",
    ) -> Any:
        if isinstance(body, dict):
            payload = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            payload = body.encode("utf-8")
        else:
            payload = None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=payload,
            headers={"Content-Type": content_type},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise OpenSearchHttpError(f"{method} {path} returned {error.code}: {detail}") from error
        return json.loads(raw) if raw else None


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:[.'-][a-z0-9]+)*", text.casefold())


def stable_vector(text: str) -> tuple[float, ...]:
    vector = [0.0] * DIMENSIONS
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % DIMENSIONS
        vector[index] += 1.0
    norm = sum(value * value for value in vector) ** 0.5
    return tuple(value / norm for value in vector) if norm else tuple(vector)


def content_for_case(case_id: str, anchor: str | None, exact_terms: list[str]) -> str:
    source = {
        "factual-code-profile": (
            "JavaScript Code Example function add a b Listing 1 Simple JavaScript Program"
        ),
        "factual-formula-profile": (
            "Formula mathematical expression a squared plus eight equals twelve"
        ),
        "factual-faa-picture": "Figure 7-26 self-locking nuts",
        "factual-faa-boots-purpose": (
            "Boots Self-Locking Nut holds tight despite severe vibration and applies a "
            "constant locking force"
        ),
        "factual-faa-stainless-action": (
            "Stainless Steel Self-Locking Nut uses a locking shoulder and threaded insert "
            "to clamp the bolt"
        ),
        "factual-doclaynet-class-count": (
            "DocLayNet provides labelled bounding-boxes with 11 distinct classes"
        ),
        "factual-loc-mast-heights": (
            "THE MAST at equipped shore stations had heights of 130 or 180 feet"
        ),
        "factual-loc-operating-room": "THE OPERATING ROOM should be about 6 feet square",
        "synthesis-layout-dataset": "DocLayNet document-layout analysis and annotated page layouts",
        "identifier-u-s-s-topeka": "AERIAL WIRE ARRANGEMENT U.S.S. TOPEKA",
        "follow-up-formula-page": (
            "Formula mathematical expression a squared plus eight equals twelve"
        ),
        "follow-up-mast-height": (
            "THE MAST at equipped shore stations had heights of 130 or 180 feet"
        ),
    }.get(case_id, " ".join(exact_terms))
    return " ".join(part for part in (source, anchor or "") if part)


def _record_id(case_id: str, evidence: EvidenceLocation, tenant_id: UUID) -> UUID:
    value = (
        f"{tenant_id}:{case_id}:{evidence.document_id}:"
        f"{evidence.page_number}:{evidence.block_type}"
    )
    return uuid5(NAMESPACE_URL, value)


def build_fixture_records(dataset: Any) -> list[ChunkIndexRecord]:
    records: list[ChunkIndexRecord] = []
    for case in dataset.cases:
        for evidence in case.gold_evidence:
            content = content_for_case(case.id, evidence.anchor, case.exact_terms)
            records.append(
                ChunkIndexRecord(
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    corpus_id=CORPUS_ID,
                    document_id=evidence.document_id,
                    document_version_id=uuid5(NAMESPACE_URL, evidence.document_id),
                    chunk_id=_record_id(case.id, evidence, ORG_ID),
                    page_number=evidence.page_number,
                    block_type=evidence.block_type,
                    content=content,
                    embedding=stable_vector(content),
                )
            )
            records.append(
                ChunkIndexRecord(
                    organization_id=FOREIGN_ORG_ID,
                    workspace_id=FOREIGN_WORKSPACE_ID,
                    corpus_id=FOREIGN_CORPUS_ID,
                    document_id=evidence.document_id,
                    document_version_id=uuid5(NAMESPACE_URL, evidence.document_id),
                    chunk_id=_record_id(case.id, evidence, FOREIGN_ORG_ID),
                    page_number=evidence.page_number,
                    block_type=evidence.block_type,
                    content=content,
                    embedding=stable_vector(content),
                )
            )
    return records


def _tenant() -> TenantContext:
    return TenantContext(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=ACTOR_ID,
        allowed_corpus_ids=(CORPUS_ID,),
    )


def _evidence(hits: list[SearchHit]) -> list[EvidenceLocation]:
    return [hit.evidence for hit in hits]


def _query_text(case: Any) -> str:
    if case.category == "follow-up":
        return " ".join([*case.conversation, case.question])
    return case.question


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def run_benchmark(client: OpenSearchHttpClient, index_name: str, dataset: Any) -> dict[str, Any]:
    tenant = _tenant()
    records = build_fixture_records(dataset)
    client.request("DELETE", f"/{index_name}?ignore_unavailable=true")
    client.request("PUT", f"/{index_name}", build_chunk_index_definition(DIMENSIONS))
    bulk_response = client.request(
        "POST",
        "/_bulk",
        build_bulk_payload(index_name, records),
        content_type="application/x-ndjson",
    )
    if bulk_response.get("errors"):
        raise OpenSearchHttpError(f"bulk indexing returned item errors: {bulk_response}")
    client.request("POST", f"/{index_name}/_refresh")
    all_hits = client.request(
        "POST",
        f"/{index_name}/_search",
        {"size": len(records), "query": {"match_all": {}}},
    )["hits"]["hits"]
    foreign_hits = [
        item
        for item in all_hits
        if item["_source"].get("organization_id") == str(FOREIGN_ORG_ID)
    ]
    if not foreign_hits:
        raise OpenSearchHttpError("fixture did not index any foreign-tenant records")

    results: list[dict[str, Any]] = []
    foreign_hits_returned = 0
    for case in dataset.cases:
        query_text = _query_text(case)
        query = HybridQueryInput(question=query_text, query_vector=stable_vector(query_text))
        body = build_tenant_scoped_hybrid_query(query, tenant)
        search_started = time.perf_counter()
        response = client.request("POST", f"/{index_name}/_search", body)
        search_latency_ms = (time.perf_counter() - search_started) * 1000
        hits = [
            search_hit_from_response(item["_source"], item.get("_score"))
            for item in response["hits"]["hits"]
        ]
        foreign_hits_returned += sum(
            hit.record.organization_id == FOREIGN_ORG_ID for hit in hits
        )
        validate_tenant_hits(hits, tenant)
        rerank_started = time.perf_counter()
        reranked = rerank_hits(hits, case.exact_terms)
        rerank_latency_ms = (time.perf_counter() - rerank_started) * 1000
        results.append(
            {
                "case_id": case.id,
                "query_text": query_text,
                "hybrid": {
                    "recall_at_5": recall_at_k(case, _evidence(hits), 5),
                    "reciprocal_rank": reciprocal_rank(case, _evidence(hits)),
                    "latency_ms": search_latency_ms,
                },
                "hybrid_reranked": {
                    "recall_at_5": recall_at_k(case, _evidence(reranked), 5),
                    "reciprocal_rank": reciprocal_rank(case, _evidence(reranked)),
                    "latency_ms": search_latency_ms + rerank_latency_ms,
                },
                "returned_hits": len(hits),
            }
        )
    supported_results = [
        result
        for result, case in zip(results, dataset.cases, strict=True)
        if case.expected_state != "insufficient"
    ]
    return {
        "dataset_id": dataset.id,
        "index_name": index_name,
        "record_count": len(records),
        "foreign_record_count": len(foreign_hits),
        "foreign_hits_returned": foreign_hits_returned,
        "tenant_filter_verified": True,
        "metrics": {
            "supported_case_count": len(supported_results),
            "hybrid": {
                "mean_recall_at_5": _mean(
                    [result["hybrid"]["recall_at_5"] for result in supported_results]
                ),
                "mean_reciprocal_rank": _mean(
                    [result["hybrid"]["reciprocal_rank"] for result in supported_results]
                ),
                "mean_latency_ms": _mean(
                    [result["hybrid"]["latency_ms"] for result in results]
                ),
            },
            "hybrid_reranked": {
                "mean_recall_at_5": _mean(
                    [result["hybrid_reranked"]["recall_at_5"] for result in supported_results]
                ),
                "mean_reciprocal_rank": _mean(
                    [
                        result["hybrid_reranked"]["reciprocal_rank"]
                        for result in supported_results
                    ]
                ),
                "mean_latency_ms": _mean(
                    [result["hybrid_reranked"]["latency_ms"] for result in results]
                ),
            },
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Phase 0 OpenSearch retrieval smoke benchmark."
    )
    parser.add_argument("--url", default=os.getenv("OPENSEARCH_URL", "http://127.0.0.1:9202"))
    parser.add_argument("--index", default="di-phase0-chunks-v1")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("spikes/retrieval/cases.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/phase-0/retrieval/benchmark.json"),
    )
    args = parser.parse_args()
    dataset = load_retrieval_dataset(args.dataset)
    client = OpenSearchHttpClient(args.url)
    report = run_benchmark(client, args.index, dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
