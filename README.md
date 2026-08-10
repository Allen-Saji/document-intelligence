# Document Intelligence

Document Intelligence is a production-focused application for investigating technical PDFs. It is designed to answer questions with claim-level citations that resolve to an immutable document version, physical PDF page, source passage, and page region.

Phase 0 architecture validation is complete. The repository is not deployed and does not make production-readiness claims.

## Product contract

The finished application must:

- ingest born-digital, scanned, multi-column, table-heavy, code-heavy, and formula-heavy PDFs
- isolate organizations and workspaces across database, search, storage, cache, workflows, and telemetry
- combine lexical and semantic retrieval, then rerank the evidence
- stream answers only after validating claim-level citations
- report supported, partial, conflicting, insufficient, or failed evidence states
- survive retries, cancellation, worker restarts, reindexing, and dependency failures
- expose quality, security, cost, latency, and recovery evidence before release

## Phase 0

Phase 0 validates decisions that would be expensive to reverse:

1. PDF extraction and page geometry with Docling and OCR.
2. PostgreSQL row-level tenant isolation.
3. OpenSearch tenant-filtered hybrid retrieval.
4. Temporal workflow recovery after worker interruption.
5. Immutable S3-compatible object storage and multipart upload.
6. End-to-end trace propagation across API, workflow, parser, index, and query stages.

The repository already contains executable application contracts for:

- production configuration validation without exposing secrets
- tenant-scoped retrieval queries
- generated tenant-scoped object keys
- evidence and citation validation
- liveness and readiness endpoints

It also contains a pinned four-document parser corpus, a repeatable Docling benchmark, and saved
Phase 0 probes for real retrieval, Temporal recovery, S3-compatible storage, and tracing.
The benchmark is deliberately a gate, not a success demo: scanned-page, diagram, reading-order,
and picture-contained-text failures must be quarantined before extracted content can enter the
retrieval index. Source PDFs, model files, and generated benchmark reports remain local and are
excluded from Git.

## Local foundation

Requirements:

- Python 3.12
- uv
- Docker with Compose for infrastructure spikes

Install and verify the core foundation:

```bash
uv sync --group dev
uv run ruff check .
uv run mypy src
uv run pytest
```

Run the API:

```bash
uv run uvicorn document_intelligence.api.app:create_app --factory --reload
```

Then open:

- `GET http://127.0.0.1:8000/health/live`
- `GET http://127.0.0.1:8000/health/ready`

Readiness reports only missing configuration names. It never returns credential values.

## Infrastructure profiles

The Compose file keeps heavy services opt-in:

```bash
# PostgreSQL and Redis
docker compose up -d postgres redis

# Add OpenSearch
docker compose --profile search up -d opensearch

# Add Temporal
docker compose --profile workflow up -d temporal
```

The local credentials in `compose.yaml` are development-only. Production secrets must come from the deployment platform's secret manager.

## Repository guide

- `src/document_intelligence/`: application and domain contracts
- `tests/`: deterministic foundation tests
- `spikes/`: Phase 0 executable probes and acceptance notes
- `infra/`: local infrastructure and isolation proofs
- `docs/`: architecture, Phase 0 gates, and security boundaries

## Current boundaries

- No product UI exists yet.
- No external generation provider is selected yet.
- No private customer document should be uploaded during Phase 0.
- The parser corpus uses public technical documents and locally fetched evaluation fixtures.

## Documentation

- [Architecture](docs/architecture.md)
- [Phase 0 gates](docs/phase-0.md)
- [Security boundary](docs/security.md)
