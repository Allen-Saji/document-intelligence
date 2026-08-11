# Phase 5 Production Composition Root

Phase 5 converts the earlier backend contracts into runtime-wired API and worker processes. This is
not a deployment claim. It is the composition layer that decides which concrete adapters the API and
ingestion worker use in staging and production.

## Implemented contract

- `create_app()` attempts production runtime composition only in `staging` and `production`.
- `local` and `test` environments keep the existing explicit test-injection behavior.
- Missing settings keep endpoints fail-closed and continue to appear under `missing_settings`.
- Missing runtime packages appear under `runtime_errors`; secret values are never exposed.
- The answer stream is wired from:
  - database-backed API-key lookup
  - database-backed corpus authorization
  - BGE-compatible query embedding
  - tenant-scoped OpenSearch retrieval
  - OpenAI structured answer generation
- The upload API is wired from:
  - database-backed API-key lookup
  - target-corpus authorization
  - S3-compatible multipart storage
  - tenant-scoped upload reservation persistence
  - a lazy Temporal ingestion starter
- The ingestion worker is wired from:
  - S3 source-object integrity verification
  - Docling PDF parsing through the same quarantine conversion contract
  - BGE-compatible batch embeddings
  - OpenSearch bulk projection writes and deletion
  - PostgreSQL publication idempotency records
  - Temporal activity registration for ingestion and projection removal

## Database authentication boundary

API-key lookup happens before tenant context exists. Normal table reads cannot work there because
forced row-level security denies access without transaction-local tenant settings.

Migration `0005_add_api_key_lookup_function` adds a narrow security-definer function:

```sql
app.lookup_api_key_by_prefix(p_token_prefix text)
```

It returns at most one API-key record by token prefix. The existing verifier still checks the
peppered HMAC hash, expiry, revocation, and required scope in application code.

## Corpus authorization

After authentication, the API binds a transaction-local database tenant context and resolves readable
corpora for the API-key actor.

- Owners and admins can read all corpora in the workspace.
- Other actors read corpora through group membership and `corpus_permissions.can_read`.
- If no readable corpus exists, `POST /v1/answers:stream` returns `403`.

Uploads now require a target `corpus_id` in the reservation request. The production upload service
authorizes that corpus inside the same tenant boundary before creating provider multipart state or
persisting an upload reservation. The target corpus is stored on the reservation and passed to the
Temporal ingestion workflow after immutable object promotion.

## Runtime adapters

The first production API and worker paths use:

- PostgreSQL through SQLAlchemy async engines and `asyncpg`.
- OpenSearch through a small async search-client wrapper around `opensearch-py`.
- S3-compatible storage through the existing boto3-backed multipart adapter.
- Temporal through a lazy client that starts the durable ingestion workflow only after the promoted
  object and database records are committed.
- `BAAI/bge-small-en-v1.5` by default through `sentence-transformers` for query embeddings.
- Docling as the first worker parser adapter.
- The OpenAI Responses adapter from Phase 4 for structured generation.

The OpenSearch, sentence-transformers, and Docling imports are intentionally lazy and checked during
production composition. The default dev environment stays light unless production extras are
installed.

## Process entrypoints

- API: `uv run uvicorn document_intelligence.api.app:create_app --factory --host 0.0.0.0 --port 8000`
- Worker: `uv run python -m document_intelligence.worker.main`
- Local container profile: `docker compose --profile runtime up api ingestion-worker`

## Required production settings

- `APP_DATABASE_URL`
- `APP_OPENSEARCH_URL`
- `APP_OPENSEARCH_INDEX_NAME`
- `APP_INGESTION_PIPELINE_VERSION`
- `APP_RETRIEVAL_INDEX_VERSION`
- `APP_ANSWER_PIPELINE_VERSION`
- `APP_API_KEY_PEPPER`
- `APP_GENERATION_PROVIDER=openai`
- `APP_GENERATION_MODEL`
- `APP_OPENAI_API_KEY` or `OPENAI_API_KEY`
- the existing storage, Temporal, identity, and telemetry settings from earlier phases

## Still required

- External malware scanning integration. The current worker verifies source-object integrity before
  parsing; it does not claim antivirus coverage.
- Live infrastructure checks against PostgreSQL, OpenSearch, S3-compatible storage, and Temporal.
- A larger retrieval and answer evaluation set before production model or ranking claims.

These are production-readiness gates after Phase 5, not missing runtime composition contracts.
