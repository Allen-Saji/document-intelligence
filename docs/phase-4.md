# Phase 4 Answer Boundary

Phase 4 adds evidence-bounded answer generation without weakening tenant isolation.

## Implemented contract

- `POST /v1/answers:stream` requires an API key with `investigation:read`.
- The request body accepts only `question` and optional `conversation`.
- Tenant identity comes from the verified API key.
- Allowed corpus IDs come from a server-side corpus authorization resolver.
- Query embedding, retrieval, and generation are runtime-injected services.
- The answer service re-authorizes packed evidence before any provider receives passage content.
- The provider sees only opaque evidence IDs and passage text.
- Model output is parsed into a strict answer schema.
- Claim citations are resolved server-side against the supplied, authorized evidence packet.
- Unknown, duplicate, or cross-tenant citations fail closed.
- One bounded citation repair attempt is allowed.
- SSE emits progress events plus a validated terminal answer or a generic error.

## Citation regions

Parser provenance is converted into `PageRegion` rectangles and propagated through:

- `PageExtraction`
- `TextChunk`
- `ChunkIndexRecord`
- `SearchHitRecord`
- `PackedEvidence`
- `EvidenceItem`
- public answer citations

The region is a conservative page-level rectangle for the indexed page content. It is suitable for
viewer navigation and highlighting. It is not a per-token or per-claim bounding box.

## OpenAI provider

The OpenAI adapter uses the Responses API with strict structured output, `store=False`, no tools,
bounded output tokens, client timeout, and normalized provider errors. The configured synthetic
smoke model is `gpt-5.6-luna` for lower-cost API checks.

## Runtime wiring still required

The API intentionally returns `503` until production installs:

- API-key lookup
- corpus authorization resolver
- BGE-compatible query embedder
- tenant-scoped retrieval service
- structured generation provider
- storage and workflow services for uploads and ingestion

These are composition-root tasks, not changes to the Phase 4 trust boundary.

## Verification

Phase 4 is covered by deterministic tests for:

- citation validation and bounded repair
- safe generation event streaming
- OpenAI request construction and error normalization
- parser-to-index citation-region propagation
- authenticated answer SSE routing
- retrieval-before-generation orchestration
- deterministic answer evaluation scoring

The live smoke check uses one synthetic passage and prints only the final evidence state and
whether the opaque citation ID matched.
