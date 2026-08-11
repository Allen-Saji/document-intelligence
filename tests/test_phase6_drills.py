import json

from document_intelligence.operations.drills import phase6_drill_manifest_json, phase6_drill_plans


def test_phase6_drills_cover_backup_reindex_and_live_health() -> None:
    plans = phase6_drill_plans()

    assert {plan.id for plan in plans} == {
        "backup-restore",
        "reindex-rollback",
        "live-dependency-health",
    }
    assert all(not plan.deployment_required for plan in plans)
    assert all(len(plan.steps) >= 5 for plan in plans)


def test_phase6_drill_manifest_is_json_serializable() -> None:
    manifest = json.loads(phase6_drill_manifest_json())

    assert manifest["drills"][0]["id"] == "backup-restore"
