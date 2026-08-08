# Security Boundary

## Protected assets

- private source documents and derived text
- organization membership and corpus permissions
- model-provider and infrastructure credentials
- saved investigations, answers, feedback, and exports
- immutable audit history

## Non-negotiable controls

- tenant context comes from verified authentication, never request payload fields
- PostgreSQL application roles cannot bypass forced RLS
- OpenSearch access goes through a tenant-filtering wrapper
- original objects are private and accessed through authorized short-lived links
- parser workers have no network egress and strict resource limits
- document instructions are data and cannot invoke tools
- model citations resolve only from evidence supplied by the server
- secrets and private content are redacted from logs and traces
- deletion is a durable, audited, verified workflow

## Phase 0 threat cases

- direct object reference to another organization
- corpus ID substitution
- cached answer reused across tenants
- background retry with stale permissions
- malicious PDF structure or embedded content
- document text attempting to override system instructions
- model inventing evidence identifiers
- tracing exporter receiving document bodies or credentials

Security claims remain unproven until the corresponding tests run against real infrastructure.
