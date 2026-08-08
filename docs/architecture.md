# Architecture

## State ownership

- PostgreSQL owns organizations, permissions, lifecycle state, pipeline versions, queries, answers, citations, feedback, usage, and audit events.
- S3-compatible object storage owns immutable originals and versioned derived artifacts.
- OpenSearch is a rebuildable tenant-filtered search projection.
- Temporal owns durable workflow history, retries, timers, and cancellation.
- Redis holds only ephemeral cache and rate-limit state.

## Trust boundaries

1. The edge authenticates the caller and applies coarse abuse controls.
2. The API derives tenant context from verified authentication.
3. PostgreSQL RLS, search filters, object keys, cache keys, and workflow inputs enforce the same tenant context.
4. Parser workers treat documents as hostile and run without network egress.
5. Generation receives a bounded evidence packet and cannot select object locations or tenant identifiers.
6. The server validates and resolves citations before streaming them to a client.

## Why this is a modular monolith first

The API, workflow activities, parsing, retrieval, generation, and citation code share one repository and explicit package contracts. They deploy separately only where scaling, resource isolation, or security requires it. This avoids premature network boundaries while retaining independent parser and worker isolation.

## Current proof surface

- `TenantContext` rejects empty or duplicate corpus authorization.
- the OpenSearch query builder injects organization, workspace, corpus, and searchable-state filters
- object keys use generated identifiers and checksums, never user filenames
- answer validation rejects unknown citations and cross-tenant evidence
- production readiness reports missing configuration names without exposing values
