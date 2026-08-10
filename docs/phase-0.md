# Phase 0 Acceptance Gates

Phase 0 closes only after each result is saved under `artifacts/phase-0/` and summarized in an architecture decision record.
The Phase 0 probe suite closed on 2026-08-11. This is an architecture and benchmark decision,
not a production-readiness claim.

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

Decision: keep Docling and page-scoped OCR as the primary parsing path, with quarantine before
chunking. Do not silently promote fallback OCR or picture-derived text into the ordinary body
index. Pages 10, 11, and 13 of the LOC scan remain quarantined by policy.

## Tenant isolation

- database RLS denies cross-tenant reads and writes
- search pre-filter denies cross-tenant results
- generated object access cannot cross tenant prefixes
- cache and trace fields contain tenant-safe identifiers only
- background workflow cannot publish into a different tenant

The PostgreSQL RLS proof covers organization, workspace, and document boundaries. The live
OpenSearch probes returned zero foreign-tenant hits from both fixture and parser-derived
indexes. Generated object keys carry organization, workspace, document, and version identity;
the LocalStack storage probe verified immutable version metadata and did not expose a cross-tenant
object path. Further adversarial authorization coverage is deferred to Phase 1.

## Retrieval

- lexical, dense, hybrid, and reranked variants use one labeled dataset
- recall and latency reported per variant
- exact identifiers and semantic paraphrases both represented
- complexity retained only when it improves measured quality

The initial wiring proof is saved at `artifacts/phase-0/retrieval/benchmark.json`. The real
parser-derived proof is saved at `artifacts/phase-0/retrieval/real-benchmark.json`; it indexes
32 page records, including 16 foreign-tenant records, and returns zero foreign hits. Nine
supported text-channel cases were scored after excluding three picture-channel cases and two
quarantined body pages. With BAAI/bge-small-en-v1.5 revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, dense retrieval reached recall@5 0.889, recall@10
1.0, and MRR 0.772 at 5.81 ms mean latency. Lexical reached recall@5 0.667 and MRR 0.701;
the current OpenSearch hybrid query reached recall@5 0.667 and MRR 0.695. CrossEncoder
reranking with revision `233902d25c440f23af6f7d6e94d2946bac0bee0a` raised recall@5 to 0.778
and MRR to 0.702 at 1.14 seconds mean latency.

Decision: retain all four measurable variants and the tenant-filtered query contract, but do
not promote the current hybrid or reranked pipeline as selected production quality. Dense
retrieval is the current small-corpus baseline; hybrid fusion and reranking require a larger
labeled set and tuning in Phase 1.

## Durable workflows

- duplicate submission is idempotent
- worker termination resumes from durable state
- retry does not duplicate active documents or search projections
- cancellation and permanent failure reach explicit terminal states

The Temporal probe at `artifacts/phase-0/workflow/recovery.json` killed a worker after parse
completion, restarted a replacement worker, and verified one parse attempt, one indexed active
version, and successful workflow completion. The local Compose profile uses Temporal 1.29.3;
the previous 1.31.2 tag was invalid and was replaced with the verified image tag.

## Storage

- multipart upload resumes or cleans up safely
- committed source object has immutable version identity
- authorized signed read expires
- retention and delete behavior is testable

The LocalStack probe at `artifacts/phase-0/storage/multipart.json` completed a two-part 10 MiB
upload, verified object version metadata, round-tripped the content, read it through a signed
URL, aborted an abandoned upload, and verified deletion.

## Telemetry

- trace context crosses every deployable boundary
- latency and error attributes identify the failing stage
- secrets and document bodies are absent by default
- cost and model metadata can be attached without exposing credentials

The telemetry probe at `artifacts/phase-0/telemetry/trace.json` produced seven spans across
API, workflow, parser, index, query, and generation stages under one trace ID. Content, body,
and API-key attributes were removed before export.

## Closure decision

Phase 0 is complete as an architecture and benchmark spike. Keep PostgreSQL RLS, generated
tenant-scoped object keys, OpenSearch pre-filtering, Docling with page quarantine, Temporal,
LocalStack-compatible S3 contracts, and OpenTelemetry tracing. Constrain ordinary indexing to
reviewed text-channel content, keep picture content on its separate channel, and do not claim
semantic retrieval quality from the nine-case result set. Phase 1 owns production schemas,
larger evaluation data, real ingestion, deployment, and operational hardening.
