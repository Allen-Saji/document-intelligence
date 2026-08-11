# Document Intelligence

Evidence-backed investigation for technical PDFs.

Document Intelligence is a FastAPI backend for uploading technical PDFs, indexing their extracted
content, and streaming answers with server-validated citations. Each citation resolves to an
immutable document version, physical PDF page, source passage, and conservative page region.

[![verify](https://github.com/Allen-Saji/document-intelligence/actions/workflows/verify.yml/badge.svg)](https://github.com/Allen-Saji/document-intelligence/actions/workflows/verify.yml)

## Status

This repository is a proof-of-work backend, not a hosted SaaS product.

The current codebase demonstrates production-shaped contracts for tenant isolation, upload
lifecycle, parsing, retrieval, answer generation, citation validation, runtime composition, and
local security hardening. It is not deployed, has no product UI, and should not receive private
customer documents until production storage, authentication, retention, monitoring, and operations
have been deployed and audited.

## What it does

- Accepts API-key-authenticated upload reservations for technical PDFs.
- Promotes uploads only after server-side size and SHA-256 verification.
- Preserves immutable source-object identity for ingestion and reprocessing.
- Parses PDFs with page provenance and quarantines unsafe extraction results.
- Builds tenant-filtered lexical and dense retrieval requests.
- Reranks and packs evidence before generation.
- Streams answer events from `POST /v1/answers:stream`.
- Gives the model only bounded evidence IDs and passages.
- Resolves citations on the server instead of trusting model-written filenames or page numbers.
- Returns insufficient-evidence states instead of inventing unsupported answers.
- Applies local request-rate and estimated-token budget controls before retrieval or generation.
- Provides repeatable security posture and operations-drill checks.

## Architecture

The backend is a modular Python service with explicit provider boundaries.

| Area | Current implementation |
| --- | --- |
| API | FastAPI with health, upload, signed-read, and answer-stream routes |
| Identity | Scoped API keys stored as peppered HMAC-SHA256 hashes |
| Database | PostgreSQL with forced row-level security and tenant-bound relations |
| Object storage | S3-compatible multipart upload and immutable object promotion |
| Workflow | Temporal workflow contracts for ingestion and projection removal |
| Parsing | Docling adapter with quarantine-aware page extraction contracts |
| Search | OpenSearch projection and tenant-filtered retrieval contracts |
| Embeddings | BGE-compatible sentence-transformers adapters |
| Generation | OpenAI Responses API adapter with structured output and `store=false` |
| Telemetry | OpenTelemetry spans with document content and secret redaction |
| Local infra | Docker Compose profiles for PostgreSQL, Redis, OpenSearch, Temporal, and LocalStack |

The trust model is simple: tenant identity comes from verified authentication, not request payloads.
Every downstream system receives the same tenant context through database RLS, object keys, search
filters, cache keys, workflow inputs, and citation validation.

## Repository layout

```text
src/document_intelligence/    application code
tests/                        deterministic unit and contract tests
migrations/                   Alembic migrations
docs/                         architecture, security, API, and phase notes
infra/                        local infrastructure initialization
scripts/                      verification and smoke-test commands
spikes/                       executable technology probes and benchmark harnesses
```

## Requirements

- Python 3.12
- uv
- Docker with Compose

Production-like adapters also require the optional `production` dependencies:

```bash
uv sync --group dev --extra production
```

For fast local test work, the default development install is enough:

```bash
uv sync --group dev
```

## Quickstart

Start the local dependencies you need:

```bash
docker compose up -d postgres redis
docker compose --profile search --profile storage --profile workflow up -d
```

Copy the example environment if you want a local `.env` file:

```bash
cp .env.example .env
```

Do not commit `.env` or any real secret. The repository checks keep `.env` files ignored.

Apply migrations against a migration-capable database identity:

```bash
APP_DATABASE_URL="$MIGRATION_DATABASE_URL" uv run alembic upgrade head
```

Run the API:

```bash
uv run uvicorn document_intelligence.api.app:create_app --factory --reload
```

Run the ingestion worker:

```bash
uv run python -m document_intelligence.worker.main
```

Health endpoints:

```text
GET http://127.0.0.1:8000/health/live
GET http://127.0.0.1:8000/health/ready
```

## Configuration

The application reads `APP_` settings from the environment. See [.env.example](.env.example) for
the complete local template.

Important production-like settings include:

- `APP_DATABASE_URL`
- `APP_OPENSEARCH_URL`
- `APP_OPENSEARCH_INDEX_NAME`
- `APP_TEMPORAL_TARGET`
- `APP_S3_BUCKET`
- `APP_API_KEY_PEPPER`
- `APP_MALWARE_SCANNER_COMMAND`
- `APP_GENERATION_PROVIDER`
- `APP_GENERATION_MODEL`
- `APP_OPENAI_API_KEY` or `OPENAI_API_KEY`
- `APP_ANSWER_RATE_LIMIT_PER_MINUTE`
- `APP_ANSWER_MONTHLY_TOKEN_BUDGET`

Readiness reports missing setting names and safe runtime errors only. It never returns credential
values.

## API contract

The generated OpenAPI contract is committed at [docs/openapi.json](docs/openapi.json).

Regenerate and check it with:

```bash
uv run python scripts/export_openapi.py
git diff --exit-code -- docs/openapi.json
```

Implemented route groups:

- `GET /health/live`
- `GET /health/ready`
- `POST /v1/uploads`
- `POST /v1/uploads/{reservation_id}/complete`
- `DELETE /v1/uploads/{reservation_id}`
- `GET /v1/uploads/{reservation_id}/read`
- `POST /v1/answers:stream`

Upload and answer endpoints require a scoped `X-API-Key`.

## Verification

Run the deterministic checks:

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv lock --check
uv build
uv run python scripts/export_openapi.py --check
uv run python scripts/phase6_security_check.py
uv run python scripts/phase6_drill_manifest.py
docker compose --profile search --profile storage --profile workflow --profile runtime config
```

The current local verification target is 170 passing tests.

## OpenAI smoke test

The OpenAI adapter reads `OPENAI_API_KEY` or `APP_OPENAI_API_KEY` at runtime. Keep the key outside
the repository.

```bash
uv run python scripts/openai_generation_smoke.py
```

The smoke request uses one synthetic passage, disables provider-side response storage, and prints
only the evidence state plus whether its opaque citation ID matched.

## Security and operations

Security controls currently covered by executable checks include:

- ignored local environment files
- no tracked `.env` files except `.env.example`
- non-root runtime container user
- no `.env` copy into the container image
- OpenAI generation with `store=false` and no model tools
- answer admission control before retrieval and generation
- ClamAV-compatible worker scanner configuration
- loopback-only Compose ports
- no `pull_request_target` workflow trigger
- Phase 6 threat-model and operations documentation
- Phase 6 drill manifest generation

Run:

```bash
uv run python scripts/phase6_security_check.py
uv run python scripts/phase6_drill_manifest.py
```

The security checker is a local proof gate, not a professional security audit.

## Documentation

- [Architecture](docs/architecture.md)
- [Security boundary](docs/security.md)
- [Threat model](docs/threat-model.md)
- [Operations runbook](docs/operations.md)
- [Phase 0 gates](docs/phase-0.md)
- [Phase 2 ingestion safeguards](docs/phase-2.md)
- [Phase 3 retrieval](docs/phase-3.md)
- [Phase 4 answer boundary](docs/phase-4.md)
- [Phase 5 runtime composition](docs/phase-5.md)
- [Phase 6 security and operations proof](docs/phase-6.md)

## Current limitations

- No frontend is included.
- No hosted deployment exists.
- Hosted malware scanner operations and definition updates are not configured.
- Live infrastructure health checks are not wired to a deployment platform.
- The parser and retrieval evaluation corpus is intentionally small.
- Scanned and picture-dense pages require conservative quarantine and review before indexing.
- No license file has been added yet.

