from pathlib import Path


def test_phase_one_migration_contains_identity_lifecycle_and_forced_rls() -> None:
    migration = Path("migrations/versions/0001_platform_foundation.py").read_text(encoding="utf-8")

    for table in (
        "users",
        "memberships",
        "sessions",
        "api_keys",
        "upload_reservations",
        "document_versions",
        "processing_runs",
        "search_indexes",
        "audit_events",
    ):
        assert f"app.{table}" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "prevent_tenant_reassignment" in migration


def test_follow_up_migration_binds_document_relations_to_the_same_tenant() -> None:
    migration = Path("migrations/versions/0002_tenant_bound_foreign_keys.py").read_text(
        encoding="utf-8"
    )

    assert "0001_platform_foundation" in migration
    assert "FOREIGN KEY (organization_id, workspace_id, document_id)" in migration
    assert "FOREIGN KEY (organization_id, workspace_id, document_version_id)" in migration
    assert "FOREIGN KEY (organization_id, workspace_id, active_version_id)" in migration


def test_phase_one_rls_probe_covers_api_key_and_object_adversarial_cases() -> None:
    probe = Path("spikes/rls/verify_phase1.sql").read_text(encoding="utf-8")

    assert "cross-tenant API-key insert unexpectedly succeeded" in probe
    assert "Tenant A object referenced Tenant B version" in probe
    assert "append-only audit update unexpectedly succeeded" in probe


def test_base_rls_migration_does_not_rely_on_a_fresh_compose_volume() -> None:
    migration = Path("migrations/versions/0003_enforce_base_rls.py").read_text(encoding="utf-8")

    assert "organizations_tenant_isolation" in migration
    assert "workspaces_tenant_isolation" in migration
    assert "documents_tenant_isolation" in migration
    assert migration.count("FORCE ROW LEVEL SECURITY") == 3


def test_api_key_lookup_function_is_narrow_and_security_definer() -> None:
    migration = Path("migrations/versions/0005_add_api_key_lookup_function.py").read_text(
        encoding="utf-8"
    )

    assert "SECURITY DEFINER" in migration
    assert "lookup_api_key_by_prefix" in migration
    assert "WHERE api_keys.token_prefix = p_token_prefix" in migration
    assert "GRANT EXECUTE ON FUNCTION app.lookup_api_key_by_prefix(text)" in migration


def test_upload_reservations_persist_target_corpus() -> None:
    migration = Path("migrations/versions/0006_add_upload_corpus_target.py").read_text(
        encoding="utf-8"
    )

    assert "ADD COLUMN corpus_id uuid" in migration
    assert "ORDER BY corpora.created_at, corpora.id" in migration
    assert "ALTER COLUMN corpus_id SET NOT NULL" in migration
    assert "upload_reservations_corpus_tenant_fk" in migration
    assert "REFERENCES app.corpora(organization_id, workspace_id, id)" in migration


def test_document_publications_are_tenant_scoped_and_durable() -> None:
    migration = Path("migrations/versions/0007_add_document_publications.py").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE app.document_publications" in migration
    assert "UNIQUE (idempotency_key)" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "document_publications_tenant_isolation" in migration
