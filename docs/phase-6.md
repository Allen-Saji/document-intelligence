# Phase 6 Security and Operational Hardening

Phase 6 is a proof-of-work hardening milestone. It does not deploy Document Intelligence, open
the service to users, or claim readiness for private customer documents.

The goal is to show that the production architecture can be evaluated like a real service:
security boundaries are explicit, abuse cases are named, local checks are executable, and
operations work has a concrete runbook path.

## Implemented in this phase

- A STRIDE threat model in `docs/threat-model.md`.
- A local operations runbook in `docs/operations.md`.
- A repeatable posture check at `scripts/phase6_security_check.py`.
- A repeatable operational drill manifest at `scripts/phase6_drill_manifest.py`.
- Tests for the posture checker in `tests/test_security_posture.py`.
- Runtime container hardening: the Dockerfile now runs the app as a non-root user.
- Worker malware scanning through a ClamAV-compatible command adapter.
- Answer request admission control for request rate and estimated token budgets.

## Local proof gates

Run:

```bash
uv run python scripts/phase6_security_check.py
```

The checker verifies:

- `.env` and `.env.*` stay ignored while `.env.example` remains trackable.
- No tracked environment file exists except `.env.example`.
- The runtime Dockerfile ends with a non-root `USER`.
- The Dockerfile does not copy `.env` files into the image.
- The OpenAI adapter sets `store=False` and does not configure model tools.
- The answer route can return `429` before retrieval or generation starts.
- The worker requires an external malware scanner command in production settings.
- Local Compose ports bind to `127.0.0.1`.
- GitHub Actions does not use `pull_request_target`.
- Phase 6 threat-model and operations docs exist.
- Phase 6 operational drill manifests exist.

These checks are intentionally narrow. They are not a complete security audit. They are executable
evidence for the controls that are meaningful before deployment.

## Still deferred

- Hosted malware-definition updates and scanner operations.
- Real load, soak, and fault-injection runs against hosted infrastructure.
- Production identity-provider configuration.
- Production secret-manager integration.
- Backup and restore against cloud-managed storage.
- External penetration test or security review.
- Staging, beta, or production deployment.

## Phase 7 note

The original production plan labels Phase 7 as staging and controlled beta. That would normally mean
deploying isolated staging and production environments, onboarding design partners, and monitoring
real usage.

For this project direction, Phase 7 is deferred. The repository can still be a strong proof of work
if it has executable contracts, clear threat boundaries, reproducible local checks, honest docs, and
green CI. Do not present it as a live SaaS until deployment, monitoring, data-handling terms, and
operations are actually in place.

## Completion boundary

Phase 6 is complete for local proof-of-work when:

- deterministic tests pass
- the posture checker passes
- the drill manifest renders
- the docs state all deferred hosted controls explicitly
- no deployment or private-document readiness claim is made
