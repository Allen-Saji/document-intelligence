from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sentence_transformers import CrossEncoder, SentenceTransformer

from document_intelligence.core.tenancy import TenantContext
from document_intelligence.evaluation.retrieval import (
    EvidenceLocation,
    RetrievalCase,
    load_retrieval_dataset,
    recall_at_k,
    reciprocal_rank,
)
from document_intelligence.retrieval.index import (
    ChunkIndexRecord,
    build_bulk_payload,
    build_chunk_index_definition,
)
from document_intelligence.retrieval.query import (
    HybridQueryInput,
    build_tenant_scoped_dense_query,
    build_tenant_scoped_hybrid_query,
    build_tenant_scoped_lexical_query,
)
from document_intelligence.retrieval.rerank import (
    SearchHit,
    search_hit_from_response,
    validate_tenant_hits,
)

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000003")
CORPUS_ID = UUID("00000000-0000-4000-8000-000000000004")
FOREIGN_ORG_ID = UUID("00000000-0000-4000-8000-000000000099")
FOREIGN_WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000098")
FOREIGN_CORPUS_ID = UUID("00000000-0000-4000-8000-000000000097")
QUARANTINED_PAGES = {10, 11, 13}
DOCUMENT_ARTIFACTS = {
    "doclaynet-paper": Path("artifacts/phase-0/parser/doclaynet-paper/document.json"),
    "docling-code-formula": Path(
        "artifacts/phase-0/parser/docling-code-formula/document.json"
    ),
    "faa-handbook-sample": Path("artifacts/phase-0/parser/faa-handbook-sample/document.json"),
    "loc-wireless-telegraph-scan": Path(
        "artifacts/phase-0/parser/loc-wireless-telegraph-scan/document.json"
    ),
}


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
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise OpenSearchHttpError(f"{method} {path} returned {error.code}: {detail}") from error
        return json.loads(raw) if raw else None


def _tenant() -> TenantContext:
    return TenantContext(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=ACTOR_ID,
        allowed_corpus_ids=(CORPUS_ID,),
    )


def _query_text(case: RetrievalCase) -> str:
    if case.category == "follow-up":
        return " ".join([*case.conversation, case.question])
    return case.question


def _block_type(labels: set[str]) -> str:
    if "code" in labels:
        return "code"
    if "formula" in labels:
        return "formula"
    return "text"


def _load_page_texts(document_id: str, path: Path) -> list[ChunkIndexRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    page_text: dict[int, list[str]] = {}
    page_labels: dict[int, set[str]] = {}
    for item in payload["texts"]:
        text = item.get("text", "").strip()
        if not text:
            continue
        for provenance in item.get("prov", []):
            page_number = int(provenance["page_no"])
            page_text.setdefault(page_number, []).append(text)
            page_labels.setdefault(page_number, set()).add(item.get("label", "text"))

    records: list[ChunkIndexRecord] = []
    version_id = uuid5(NAMESPACE_URL, f"document-version:{document_id}")
    for page_number in sorted(page_text):
        chunk_id = uuid5(NAMESPACE_URL, f"page-chunk:{document_id}:{page_number}")
        records.append(
            ChunkIndexRecord(
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                corpus_id=CORPUS_ID,
                document_id=document_id,
                document_version_id=version_id,
                chunk_id=chunk_id,
                page_number=page_number,
                block_type=_block_type(page_labels[page_number]),
                content="\n".join(page_text[page_number]),
                embedding=(0.0,),
                is_searchable=not (
                    document_id == "loc-wireless-telegraph-scan"
                    and page_number in QUARANTINED_PAGES
                ),
            )
        )
    return records


def load_parser_chunks() -> list[ChunkIndexRecord]:
    records: list[ChunkIndexRecord] = []
    for document_id, path in DOCUMENT_ARTIFACTS.items():
        records.extend(_load_page_texts(document_id, path))
    return records


def _with_embedding(record: ChunkIndexRecord, embedding: Any) -> ChunkIndexRecord:
    return record.model_copy(update={"embedding": tuple(float(value) for value in embedding)})


def _evidence(hits: list[SearchHit]) -> list[EvidenceLocation]:
    return [hit.evidence for hit in hits]


def _supported_cases(dataset: Any) -> list[RetrievalCase]:
    supported: list[RetrievalCase] = []
    for case in dataset.cases:
        if case.expected_state == "insufficient":
            continue
        if any(evidence.block_type == "picture" for evidence in case.gold_evidence):
            continue
        if any(
            evidence.document_id == "loc-wireless-telegraph-scan"
            and evidence.page_number in QUARANTINED_PAGES
            for evidence in case.gold_evidence
        ):
            continue
        supported.append(case)
    return supported


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _query_body(variant: str, query: HybridQueryInput, tenant: TenantContext) -> dict[str, Any]:
    if variant == "lexical":
        return build_tenant_scoped_lexical_query(query, tenant)
    if variant == "dense":
        return build_tenant_scoped_dense_query(query, tenant)
    return build_tenant_scoped_hybrid_query(query, tenant)


def _run_search(
    client: OpenSearchHttpClient,
    index_name: str,
    body: dict[str, Any],
) -> tuple[list[SearchHit], float]:
    started = time.perf_counter()
    response = client.request("POST", f"/{index_name}/_search", body)
    latency_ms = (time.perf_counter() - started) * 1000
    hits = [
        search_hit_from_response(item["_source"], item.get("_score"))
        for item in response["hits"]["hits"]
    ]
    return hits, latency_ms


def _rerank(
    model: CrossEncoder,
    query_text: str,
    hits: list[SearchHit],
) -> tuple[list[SearchHit], float]:
    started = time.perf_counter()
    scores = model.predict(
        [(query_text, hit.record.content) for hit in hits],
        show_progress_bar=False,
    )
    ranked = [
        hit
        for _, hit in sorted(
            zip((float(score) for score in scores), hits, strict=True),
            key=lambda pair: pair[0],
            reverse=True,
        )
    ]
    return ranked, (time.perf_counter() - started) * 1000


def run_benchmark(
    client: OpenSearchHttpClient,
    index_name: str,
    dataset: Any,
    embedding_model: SentenceTransformer,
    reranker: CrossEncoder,
    embedding_model_name: str,
    embedding_revision: str,
    reranker_model_name: str,
    reranker_revision: str,
) -> dict[str, Any]:
    tenant = _tenant()
    raw_records = load_parser_chunks()
    embeddings = embedding_model.encode(
        [record.content for record in raw_records],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    allowed_records = [
        _with_embedding(record, vector)
        for record, vector in zip(raw_records, embeddings, strict=True)
    ]
    foreign_records = [
        record.model_copy(
            update={
                "organization_id": FOREIGN_ORG_ID,
                "workspace_id": FOREIGN_WORKSPACE_ID,
                "corpus_id": FOREIGN_CORPUS_ID,
                "chunk_id": uuid5(NAMESPACE_URL, f"foreign:{record.chunk_id}"),
            }
        )
        for record in allowed_records
    ]
    records = [*allowed_records, *foreign_records]
    client.request("DELETE", f"/{index_name}?ignore_unavailable=true")
    client.request(
        "PUT",
        f"/{index_name}",
        build_chunk_index_definition(len(allowed_records[0].embedding)),
    )
    bulk_response = client.request(
        "POST",
        "/_bulk",
        build_bulk_payload(index_name, records),
        content_type="application/x-ndjson",
    )
    if bulk_response.get("errors"):
        raise OpenSearchHttpError(f"bulk indexing returned item errors: {bulk_response}")
    client.request("POST", f"/{index_name}/_refresh")

    cases = _supported_cases(dataset)
    results: list[dict[str, Any]] = []
    foreign_hits_returned = 0
    for case in cases:
        query_text = _query_text(case)
        query_embedding = embedding_model.encode(
            [f"Represent this sentence for searching relevant passages: {query_text}"],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]
        query = HybridQueryInput(
            question=query_text,
            query_vector=tuple(float(value) for value in query_embedding),
        )
        row: dict[str, Any] = {"case_id": case.id, "query_text": query_text}
        hybrid_hits: list[SearchHit] = []
        for variant in ("lexical", "dense", "hybrid"):
            hits, latency_ms = _run_search(
                client,
                index_name,
                _query_body(variant, query, tenant),
            )
            foreign_hits_returned += sum(
                hit.record.organization_id == FOREIGN_ORG_ID for hit in hits
            )
            validate_tenant_hits(hits, tenant)
            if variant == "hybrid":
                hybrid_hits = hits
            row[variant] = {
                "recall_at_5": recall_at_k(case, _evidence(hits), 5),
                "recall_at_10": recall_at_k(case, _evidence(hits), 10),
                "reciprocal_rank": reciprocal_rank(case, _evidence(hits)),
                "latency_ms": latency_ms,
            }
        reranked_hits, rerank_latency_ms = _rerank(reranker, query_text, hybrid_hits)
        row["hybrid_reranked"] = {
            "recall_at_5": recall_at_k(case, _evidence(reranked_hits), 5),
            "recall_at_10": recall_at_k(case, _evidence(reranked_hits), 10),
            "reciprocal_rank": reciprocal_rank(case, _evidence(reranked_hits)),
            "latency_ms": row["hybrid"]["latency_ms"] + rerank_latency_ms,
        }
        results.append(row)

    variants = ("lexical", "dense", "hybrid", "hybrid_reranked")
    metrics: dict[str, dict[str, float | int | None]] = {}
    for variant in variants:
        metrics[variant] = {
            "case_count": len(results),
            "mean_recall_at_5": _mean([row[variant]["recall_at_5"] for row in results]),
            "mean_recall_at_10": _mean([row[variant]["recall_at_10"] for row in results]),
            "mean_reciprocal_rank": _mean(
                [row[variant]["reciprocal_rank"] for row in results]
            ),
            "mean_latency_ms": _mean([row[variant]["latency_ms"] for row in results]),
        }
    return {
        "dataset_id": dataset.id,
        "index_name": index_name,
        "embedding_model": embedding_model_name,
        "embedding_revision": embedding_revision,
        "reranker_model": reranker_model_name,
        "reranker_revision": reranker_revision,
        "parser_artifacts": [str(path) for path in DOCUMENT_ARTIFACTS.values()],
        "record_count": len(records),
        "searchable_record_count": sum(record.is_searchable for record in allowed_records),
        "quarantined_record_count": sum(not record.is_searchable for record in allowed_records),
        "excluded_picture_case_count": sum(
            case.expected_state != "insufficient"
            and any(evidence.block_type == "picture" for evidence in case.gold_evidence)
            for case in dataset.cases
        ),
        "foreign_record_count": len(foreign_records),
        "foreign_hits_returned": foreign_hits_returned,
        "tenant_filter_verified": foreign_hits_returned == 0,
        "metrics": metrics,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real parser-derived retrieval benchmark.")
    parser.add_argument("--url", default="http://127.0.0.1:9202")
    parser.add_argument("--index", default="di-phase0-real-v1")
    parser.add_argument("--dataset", type=Path, default=Path("spikes/retrieval/cases.json"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/phase-0/retrieval/real-benchmark.json"),
    )
    parser.add_argument("--embedding-model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument(
        "--embedding-revision",
        default="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
    )
    parser.add_argument("--reranker-model", default="cross-encoder/ms-marco-MiniLM-L-6-v2")
    parser.add_argument(
        "--reranker-revision",
        default="233902d25c440f23af6f7d6e94d2946bac0bee0a",
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("model-cache/huggingface"))
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    dataset = load_retrieval_dataset(args.dataset)
    embedding_model = SentenceTransformer(
        args.embedding_model,
        cache_folder=str(args.cache_dir),
        device=args.device,
    )
    reranker = CrossEncoder(
        args.reranker_model,
        cache_folder=str(args.cache_dir),
        device=args.device,
    )
    report = run_benchmark(
        OpenSearchHttpClient(args.url),
        args.index,
        dataset,
        embedding_model,
        reranker,
        args.embedding_model,
        args.embedding_revision,
        args.reranker_model,
        args.reranker_revision,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
