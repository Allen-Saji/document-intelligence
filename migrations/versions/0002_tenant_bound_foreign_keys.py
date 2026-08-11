"""Bind tenant-owned relations to organization and workspace IDs.

Revision ID: 0002_tenant_bound_foreign_keys
Revises: 0001_platform_foundation
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_tenant_bound_foreign_keys"
down_revision: str | Sequence[str] | None = "0001_platform_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("documents", "document_versions", "groups", "corpora"):
        op.execute(
            f"ALTER TABLE app.{table} ADD CONSTRAINT {table}_tenant_identity "
            "UNIQUE (organization_id, workspace_id, id)"
        )

    op.execute("ALTER TABLE app.group_members DROP CONSTRAINT group_members_group_id_fkey")
    op.execute(
        "ALTER TABLE app.group_members ADD CONSTRAINT group_members_group_tenant_fk "
        "FOREIGN KEY (organization_id, workspace_id, group_id) "
        "REFERENCES app.groups(organization_id, workspace_id, id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE app.corpus_permissions DROP CONSTRAINT corpus_permissions_corpus_id_fkey"
    )
    op.execute(
        "ALTER TABLE app.corpus_permissions DROP CONSTRAINT corpus_permissions_group_id_fkey"
    )
    op.execute(
        "ALTER TABLE app.corpus_permissions ADD CONSTRAINT corpus_permissions_corpus_tenant_fk "
        "FOREIGN KEY (organization_id, workspace_id, corpus_id) "
        "REFERENCES app.corpora(organization_id, workspace_id, id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE app.corpus_permissions ADD CONSTRAINT corpus_permissions_group_tenant_fk "
        "FOREIGN KEY (organization_id, workspace_id, group_id) "
        "REFERENCES app.groups(organization_id, workspace_id, id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE app.document_versions DROP CONSTRAINT document_versions_document_id_fkey"
    )
    op.execute(
        "ALTER TABLE app.document_versions ADD CONSTRAINT document_versions_document_tenant_fk "
        "FOREIGN KEY (organization_id, workspace_id, document_id) "
        "REFERENCES app.documents(organization_id, workspace_id, id)"
    )
    op.execute("ALTER TABLE app.documents DROP CONSTRAINT documents_active_version_fk")
    op.execute(
        "ALTER TABLE app.documents ADD CONSTRAINT documents_active_version_tenant_fk "
        "FOREIGN KEY (organization_id, workspace_id, active_version_id) "
        "REFERENCES app.document_versions(organization_id, workspace_id, id)"
    )
    op.execute(
        "ALTER TABLE app.document_objects DROP CONSTRAINT document_objects_document_version_id_fkey"
    )
    op.execute(
        "ALTER TABLE app.document_objects ADD CONSTRAINT document_objects_version_tenant_fk "
        "FOREIGN KEY (organization_id, workspace_id, document_version_id) "
        "REFERENCES app.document_versions(organization_id, workspace_id, id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE app.upload_reservations DROP CONSTRAINT upload_reservations_document_id_fkey"
    )
    op.execute(
        "ALTER TABLE app.upload_reservations ADD CONSTRAINT upload_reservations_document_tenant_fk "
        "FOREIGN KEY (organization_id, workspace_id, document_id) "
        "REFERENCES app.documents(organization_id, workspace_id, id)"
    )
    op.execute(
        "ALTER TABLE app.processing_runs DROP CONSTRAINT processing_runs_document_version_id_fkey"
    )
    op.execute(
        "ALTER TABLE app.processing_runs ADD CONSTRAINT processing_runs_document_version_tenant_fk "
        "FOREIGN KEY (organization_id, workspace_id, document_version_id) "
        "REFERENCES app.document_versions(organization_id, workspace_id, id)"
    )


def downgrade() -> None:
    raise RuntimeError("Tenant-bound foreign keys are intentionally forward-only")
