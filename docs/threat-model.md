# Threat Model

This threat model covers the current backend and runtime composition proof. It assumes a future
hosted deployment, but the controls listed here are evaluated locally until deployment exists.

## Assets

Restricted:

- API keys and provider credentials.
- Private source PDFs.
- Extracted document text, page geometry, and derived chunks.
- Tenant membership, corpus permissions, and audit events.
- Saved answers, investigations, feedback, and exports.

Confidential:

- Retrieval traces and ranking evidence.
- Runtime settings and infrastructure topology.
- Operational runbooks and incident notes.

Public:

- Repository source code.
- Public API schema and documentation.
- Synthetic fixtures and local-only examples.

## Trust boundaries

1. Client to API: every upload and answer request is authenticated by scoped API key.
2. API to database: tenant context is set transaction-locally before tenant reads and writes.
3. API to object storage: object keys are server generated; source reads require authorization.
4. Worker to parser: source-object integrity is checked before parsing.
5. Parser to index: quarantined pages do not become searchable chunks.
6. Retrieval to generation: packed evidence is re-authorized before model calls.
7. Generation to client: citations are server resolved and validated before streaming.
8. Runtime to operators: readiness exposes safe setting names and safe runtime errors only.

## STRIDE analysis

### API authentication

- Spoofing: API-key prefixes are looked up through a narrow database function; full token hashes are
  verified with a server-side pepper.
- Tampering: tenant IDs are not accepted from request bodies.
- Repudiation: upload lifecycle actions append audit events.
- Information disclosure: failed auth returns generic errors.
- Denial of service: request rate limits and estimated token budgets run before retrieval and
  generation.
- Elevation of privilege: scoped API-key contracts separate document read and write permissions.

### Upload and storage

- Spoofing: upload reservations bind organization, workspace, actor, document, version, and corpus.
- Tampering: promoted source objects require server-side size and SHA-256 verification.
- Repudiation: reserve, complete, and abort events are auditable.
- Information disclosure: signed reads are reservation scoped.
- Denial of service: maximum PDF size is enforced; per-tenant storage quotas remain deferred.
- Elevation of privilege: target corpus is authorized before provider multipart state is created.

### Ingestion worker

- Spoofing: workflow identity includes immutable document version and pipeline version.
- Tampering: parser input must match expected object key, document-version ID, and SHA-256.
- Repudiation: publication is recorded through an idempotency ledger.
- Information disclosure: parsing errors must not emit document text to logs.
- Denial of service: a ClamAV-compatible scanner can reject malicious source objects before parsing;
  sandboxing and hosted resource limits remain deferred.
- Elevation of privilege: worker publication remains tenant scoped.

### Retrieval and generation

- Spoofing: answer requests derive corpus scope from authenticated server state.
- Tampering: caller-supplied evidence, corpus IDs, citations, model, and provider are not accepted.
- Repudiation: answer events can be traced without storing document bodies in telemetry.
- Information disclosure: the OpenAI adapter receives passages and opaque evidence IDs only; it sets
  `store=False` and does not configure tools.
- Denial of service: model spend is bounded by local admission control before retrieval starts.
- Elevation of privilege: packed evidence is re-authorized before generation.

### Local operations

- Spoofing: local Compose credentials are development-only and must not be reused for production.
- Tampering: migrations are versioned and deterministic.
- Repudiation: local runbooks require saving verification output before claims are made.
- Information disclosure: `.env` files are ignored; `.env.example` contains placeholders only.
- Denial of service: load and soak testing are deferred until a deployment target exists.
- Elevation of privilege: the runtime image runs as a non-root user.

## Abuse cases

1. A caller swaps corpus IDs to read another workspace. Mitigation: corpus scope is server resolved.
2. A caller uploads a PDF to a corpus they cannot write. Mitigation: target corpus authorization.
3. A malicious PDF tries to inject instructions into the model. Mitigation: document text is data,
   not system prompt content; citations are server validated.
4. A parser emits bad OCR text for a diagram page. Mitigation: page quarantine before indexing.
5. A model invents a filename or page number. Mitigation: model cites opaque IDs only.
6. A leaked `.env` is accidentally committed. Mitigation: `.env` patterns are ignored and checked.
7. A caller tries to run up model cost. Mitigation: answer admission control enforces rate and
   estimated token budgets before retrieval or generation.
8. A malicious source object reaches the parser. Mitigation: the worker chains checksum verification
   with a ClamAV-compatible external scan command.
9. A container breakout gains root inside the app image. Mitigation: runtime image uses non-root user.
10. A local service accidentally binds to all interfaces. Mitigation: Compose port mappings are checked
   for loopback binding.

## Open hardening work

- Operate malware-definition updates and scanner health checks in the target environment.
- Add durable tenant quotas and provider-reported usage accounting.
- Add production secret-manager integration.
- Add hosted infrastructure health checks.
- Run load, soak, and fault-injection checks once a deployment target exists.
- Perform external security review before handling real private customer documents.
