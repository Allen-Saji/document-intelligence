# Phase 0 Acceptance Gates

Phase 0 closes only after each result is saved under `artifacts/phase-0/` and summarized in an architecture decision record.

## Parser

- representative born-digital, scanned, multi-column, table, code, and formula PDFs processed
- source URL, rights reference, byte size, SHA-256, parser profile, and page range pinned in the corpus manifest
- raw Docling JSON, Markdown, rendered pages, runtime metadata, and automated checks saved per document
- physical page numbers and bounding regions verified manually
- resource use and failure behavior measured
- low-confidence quarantine rule defined

Picture content has a separate contract:

- picture-derived text defaults to `pending` and cannot enter the ordinary body index
- accepted picture text requires reviewer identity, review time, and a review note
- accepted content is emitted on the `picture` index channel with page and picture provenance
- rejected or unreviewed content remains evidence-only until a later reviewed decision

## Tenant isolation

- database RLS denies cross-tenant reads and writes
- search pre-filter denies cross-tenant results
- generated object access cannot cross tenant prefixes
- cache and trace fields contain tenant-safe identifiers only
- background workflow cannot publish into a different tenant

## Retrieval

- lexical, dense, hybrid, and reranked variants use one labeled dataset
- recall and latency reported per variant
- exact identifiers and semantic paraphrases both represented
- complexity retained only when it improves measured quality

## Durable workflows

- duplicate submission is idempotent
- worker termination resumes from durable state
- retry does not duplicate active documents or search projections
- cancellation and permanent failure reach explicit terminal states

## Storage

- multipart upload resumes or cleans up safely
- committed source object has immutable version identity
- authorized signed read expires
- retention and delete behavior is testable

## Telemetry

- trace context crosses every deployable boundary
- latency and error attributes identify the failing stage
- secrets and document bodies are absent by default
- cost and model metadata can be attached without exposing credentials
