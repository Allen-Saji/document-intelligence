# Phase 0 Spikes

These probes validate foundational choices inside the real repository. Phase 0 is complete only
when each probe produces a saved result and a keep, replace, or constrain decision. The suite
closed on 2026-08-11; remaining work belongs to Phase 1 production hardening.

## Parser and geometry

```bash
uv sync --extra phase0 --group dev
uv run python spikes/parser/run.py --fetch-only
uv run python spikes/parser/run.py
```

The versioned corpus manifest is `spikes/parser/corpus.json`. It pins source and rights
pages, download URLs, byte sizes, SHA-256 hashes, document categories, OCR profiles,
automated minimums, and the physical pages that require visual review. Source PDFs are
downloaded into ignored `data/parser-corpus/`; raw Docling JSON, Markdown, page renders,
and reports are written into ignored `artifacts/phase-0/parser/`.
Model files are downloaded into ignored `model-cache/` so the run does not depend on
or write to a user-global cache.

Run one case while iterating:

```bash
uv run python spikes/parser/run.py --document docling-code-formula
```

Run the alternate OCR profile only for pages quarantined by the primary parser:

```bash
uv run python spikes/parser/run.py --document loc-wireless-telegraph-scan --fallback
```

The report records the primary quarantine reasons and each fallback page result. Fallback
renders are saved under the document's `fallback/` artifact directory. A fallback result
never enters the primary parse or index automatically.

The runner does not mark manual fidelity checks complete. Review the listed page renders
against the original PDF and record the decision before closing the Phase 0 parser gate.
After recording `artifacts/phase-0/parser/manual-review.json`, rebuild the aggregate report:

```bash
uv run python spikes/parser/run.py --summarize-existing
```

Required follow-up after the automated run:

- record extraction time, page count, reading order, tables, code, formulas, OCR, and geometry quality
- preserve failing documents as regression fixtures when licensing permits
- define the low-confidence quarantine threshold from observed failures

## PostgreSQL RLS

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -U postgres -d document_intelligence \
  < spikes/rls/verify.sql
```

The verification script proves both allowed access and cross-tenant denial through the non-owner application role, then rolls back its test data.

## OpenSearch hybrid retrieval

```bash
docker compose --profile search up -d opensearch
```

The real runner creates a versioned chunk index from the saved Docling page artifacts, embeds it with the pinned BGE model, runs lexical, dense, hybrid, and CrossEncoder-reranked variants, and proves the mandatory tenant pre-filter prevents cross-tenant hits:

```bash
docker compose --profile search up -d opensearch
HF_HOME=/tmp/document-intelligence-hf uv run --extra phase0 python spikes/retrieval/real_run.py
```

The selected BGE and CrossEncoder revisions are recorded in `spikes/retrieval/models.json`.
The result is `artifacts/phase-0/retrieval/real-benchmark.json`. Quarantined pages and
picture-channel cases are excluded from the ordinary body-index score. The measured local
result constrains the current hybrid query: dense retrieval outperformed hybrid on the small
corpus, and CrossEncoder reranking added about one second per query without closing all misses.

## Temporal recovery

```bash
docker compose --profile workflow up -d temporal
```

```bash
uv run --extra phase0 python spikes/workflow/run.py
```

The probe kills a worker after parse completion, starts a replacement worker, and verifies one
parse attempt, one indexed active version, and successful workflow completion. The Compose
profile pins Temporal 1.29.3 and uses the image's bundled dynamic configuration.

The first workflow test must terminate a worker between parse and index activities, restart it, and prove the workflow resumes without duplicating the active document version.

## Object storage

The production contract targets S3-compatible storage. Select the local emulator during the spike instead of adopting the archived MinIO server without review. The test must cover multipart upload, immutable version identity, signed reads, interrupted upload cleanup, and delete verification.

The selected emulator is LocalStack 4.4.0:

```bash
docker compose --profile storage up -d localstack
uv run --extra phase0 python spikes/storage/run.py
```

The result is `artifacts/phase-0/storage/multipart.json`.

## Telemetry

The trace proof must propagate one trace across API, Temporal workflow, parser activity, index activity, OpenSearch query, and generation stub. It must verify that document bodies and secret values are absent from exported telemetry by default.

```bash
uv run python spikes/telemetry/run.py
```

The result is `artifacts/phase-0/telemetry/trace.json`.

## Retrieval dataset

The retrieval dataset is `spikes/retrieval/cases.json`. It is a versioned contract over
document and physical-page evidence, not a release-sized evaluation set. Run its validation
with the normal test suite. The OpenSearch smoke runner creates a versioned chunk index,
indexes allowed and foreign tenant records, runs tenant-filtered hybrid queries, applies the
transparent exact-term reranker, and writes `artifacts/phase-0/retrieval/benchmark.json`:

```bash
docker compose --profile search up -d opensearch
uv run python spikes/retrieval/run.py --index di-phase0-chunks-v1
```

The runner uses deterministic hash vectors only to exercise the index and query wiring. It is
not a semantic embedding quality claim. Expand the fixture with further human-labeled cases
and a selected embedding model before making retrieval quality claims.
