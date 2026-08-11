# Document Intelligence

Document Intelligence is a production-focused application for investigating technical PDFs. It is designed to answer questions with claim-level citations that resolve to an immutable document version, physical PDF page, source passage, and page region.

Phases 0 through 4 are complete as backend application contracts. The repository is not deployed
and does not make production-readiness claims.

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
- verified-identity, workspace-role, and scoped hashed API-key contracts
- transaction-local PostgreSQL tenant context for non-bypassing application connections
- multipart upload reservation, server-verified promotion, and immutable object-key contracts
- API-key-authenticated multipart reservation, completion, abort, and signed-read routes
- stable Temporal workflow identities for each immutable version and pipeline revision
- versioned OpenSearch indexes with atomic alias publish and rollback operations
- tenant-aware cache keys and content-free request telemetry
- a provider-neutral structured answer service that re-authorizes packed evidence before generation,
  resolves citations server-side, bounds citation repair, and streams only validated terminal events
- an authenticated `POST /v1/answers:stream` route that derives tenant and corpus scope on the
  server before retrieval and generation
- an OpenAI Responses API adapter that uses strict structured output and disables provider-side
  response storage

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

Run the Phase 1 PostgreSQL migration after the local database is available:

```bash
APP_DATABASE_URL="$MIGRATION_DATABASE_URL" uv run alembic upgrade head
```

`MIGRATION_DATABASE_URL` must use a separate migration identity that can change schema. The
runtime `document_intelligence_app` role remains non-owner and cannot bypass RLS.

The migration is followed by a live adversarial RLS check (development database only):

```bash
docker compose exec -T postgres psql -U postgres -d document_intelligence \
  < spikes/rls/verify_phase1.sql
```

Run the API:

```bash
uv run uvicorn document_intelligence.api.app:create_app --factory --reload
```

Then open:

- `GET http://127.0.0.1:8000/health/live`
- `GET http://127.0.0.1:8000/health/ready`

The generated API contract is checked in at [`docs/openapi.json`](docs/openapi.json). Regenerate it
after route changes with:

```bash
uv run python scripts/export_openapi.py
```

Upload and answer endpoints require a scoped `X-API-Key` and are intentionally unavailable until
the runtime is configured with database-backed key lookup, tenant corpus authorization, storage,
retrieval, and workflow adapters. The HTTP contract is stable and exercised with in-memory adapters
in tests; the production composition root is the next deployment concern, not a bypass around
tenant isolation.

Answer streaming accepts only the user question and optional conversation turns. Caller-supplied
corpus IDs, tenant IDs, evidence, citations, or provider choices are rejected by schema validation
or ignored by the trusted server pipeline. Citation regions are conservative page-level rectangles
derived from parser provenance and carried through indexing, retrieval, answer validation, and the
public citation payload.

Readiness reports only missing configuration names. It never returns credential values.

## OpenAI generation smoke check

The OpenAI adapter reads `OPENAI_API_KEY` or `APP_OPENAI_API_KEY` at runtime. Do not copy a key
into this repository or commit an environment file. The default test model is `gpt-5.6-luna`.

```bash
uv run python scripts/openai_generation_smoke.py
```

The smoke request uses one synthetic passage, disables provider-side response storage, and prints
only the evidence state plus whether its opaque citation ID matched.

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
- `docs/`: architecture, phase gates, and security boundaries
- `docs/openapi.json`: generated HTTP contract committed for client generation

## Current boundaries

- No product UI exists yet.
- No private customer document should be uploaded until production storage, auth, and retention
  controls are deployed and audited.
- The parser corpus uses public technical documents and locally fetched evaluation fixtures.
- The answer endpoint needs production wiring for corpus authorization, BGE-compatible query
  embedding, retrieval adapters, and provider configuration before deployment.

## Documentation

- [Architecture](docs/architecture.md)
- [Phase 0 gates](docs/phase-0.md)
- [Phase 4 answer boundary](docs/phase-4.md)
- [Security boundary](docs/security.md)
