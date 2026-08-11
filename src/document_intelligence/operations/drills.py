from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class DrillStep:
    order: int
    action: str
    evidence: str


@dataclass(frozen=True)
class DrillPlan:
    id: str
    title: str
    deployment_required: bool
    steps: tuple[DrillStep, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "deployment_required": self.deployment_required,
            "steps": [
                {"order": step.order, "action": step.action, "evidence": step.evidence}
                for step in self.steps
            ],
        }


def phase6_drill_plans() -> tuple[DrillPlan, ...]:
    return (
        DrillPlan(
            id="backup-restore",
            title="Local PostgreSQL backup and restore proof",
            deployment_required=False,
            steps=(
                DrillStep(
                    1,
                    "record git commit, Alembic head, and Compose image versions",
                    "manifest",
                ),
                DrillStep(2, "create a local PostgreSQL dump", "dump file path and checksum"),
                DrillStep(3, "restore into a fresh local volume", "restore command output"),
                DrillStep(4, "run Alembic upgrade head", "migration output"),
                DrillStep(5, "run RLS probes and deterministic tests", "test output"),
            ),
        ),
        DrillPlan(
            id="reindex-rollback",
            title="Local search reindex and rollback proof",
            deployment_required=False,
            steps=(
                DrillStep(
                    1,
                    "publish a document version with pipeline version A",
                    "publication record",
                ),
                DrillStep(2, "run retrieval checks against projection A", "retrieval output"),
                DrillStep(
                    3,
                    "publish the same source with pipeline version B",
                    "publication record",
                ),
                DrillStep(4, "rollback or delete projection B", "OpenSearch delete response"),
                DrillStep(5, "verify active projection state", "alias and ledger output"),
            ),
        ),
        DrillPlan(
            id="live-dependency-health",
            title="Local dependency health proof",
            deployment_required=False,
            steps=(
                DrillStep(
                    1,
                    "start PostgreSQL, Redis, OpenSearch, LocalStack, and Temporal",
                    "Compose ps",
                ),
                DrillStep(2, "check API liveness and readiness", "HTTP responses"),
                DrillStep(3, "check OpenSearch cluster health", "cluster health response"),
                DrillStep(4, "check Temporal TCP reachability", "connection result"),
                DrillStep(5, "check S3-compatible bucket access", "bucket head response"),
            ),
        ),
    )


def phase6_drill_manifest_json() -> str:
    return json.dumps(
        {"drills": [plan.to_dict() for plan in phase6_drill_plans()]},
        indent=2,
        sort_keys=True,
    )
