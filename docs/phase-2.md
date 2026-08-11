# Document ingestion contracts

Phase 2 implements the application contracts that take a verified immutable PDF
through safe ingestion and make only trusted text available to search. This is
repository-level implementation and verification, not a claim that a cloud
worker or external scanning service is deployed.

## Ingestion order

1. An upload has already been promoted to an immutable object after server-side
   byte-size and SHA-256 verification.
2. The worker scans that exact object before parsing it.
3. The parser returns page-level extraction plus quality reasons. A page with a
   quality reason is quarantined and cannot produce chunks.
4. The pipeline verifies that parser output names the same object key, checksum,
   and document version requested by the workflow.
5. Searchable pages are chunked deterministically, embedded, and written to the
   search projection.
6. The publisher records an idempotency key derived from document version,
   checksum, and pipeline version. A retry therefore preserves one active
   projection instead of creating duplicates.

If no page is searchable, the pipeline returns a terminal failed outcome and
does not call the embedder or publisher. Unexpected scanner, parser, embedder,
or publisher failures also return a content-free failed outcome.

## Durable workflow boundaries

`DocumentIngestionWorkflow` delegates the full pipeline to one retryable
Temporal activity. `TemporalDocumentIngestionStarter` assigns a workflow ID
from the immutable document version and pipeline version, so duplicate delivery
joins the existing workflow while an intentional reprocess with a new pipeline
version starts independently.

`DocumentProjectionRemovalWorkflow` uses the same durable pattern for rollback
and deletion of an active search projection. Repeated removal requests do not
delete a projection more than once. This hides the document version from
ordinary search; original-object retention, legal hold, audit authorization,
and permanent object deletion remain later lifecycle work.

## Interfaces supplied by deployment

The domain layer deliberately depends on narrow interfaces for malware scanning,
PDF parsing, embeddings, search projection, and the publication ledger. A
worker composition root must supply concrete, isolated implementations for
those interfaces. This keeps hostile-file handling and provider credentials out
of request handlers and makes adapters independently testable.

## Verification

The tests cover:

- page quarantine exclusion and deterministic chunks
- source object, checksum, and document-version mismatch rejection
- no-searchable-content failure without publication
- Temporal activity and workflow starter contracts
- idempotent publication, rollback, deletion, and repeated deletion

The existing Phase 0 corpus and parser benchmark continue to provide the
representative parser provenance and page-quality evidence. Production readiness
still requires concrete worker adapters, broader regression data, operational
fault tests, and deployment evidence.
