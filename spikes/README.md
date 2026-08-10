# Phase 0 Spikes

These probes validate foundational choices inside the real repository. A spike is complete only when it produces a saved result and a keep, replace, or constrain decision.

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

The first test must create a versioned chunk index, ingest two tenants, run lexical, dense, and hybrid queries, and prove the mandatory tenant pre-filter prevents cross-tenant hits.

## Temporal recovery

```bash
docker compose --profile workflow up -d temporal
```

The first workflow test must terminate a worker between parse and index activities, restart it, and prove the workflow resumes without duplicating the active document version.

## Object storage

The production contract targets S3-compatible storage. Select the local emulator during the spike instead of adopting the archived MinIO server without review. The test must cover multipart upload, immutable version identity, signed reads, interrupted upload cleanup, and delete verification.

## Telemetry

The trace proof must propagate one trace across API, Temporal workflow, parser activity, index activity, OpenSearch query, and generation stub. It must verify that document bodies and secret values are absent from exported telemetry by default.

## Retrieval dataset

The seed retrieval dataset is `spikes/retrieval/cases.json`. It is a versioned contract
over document and physical-page evidence, not a release-sized evaluation set. Run its
validation with the normal test suite. Expand it with human-labeled cases before making
retrieval quality claims.
