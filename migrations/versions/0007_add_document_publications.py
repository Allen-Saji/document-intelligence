"""Add durable document publication records.

Revision ID: 0007_add_document_publications
Revises: 0006_add_upload_corpus_target
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_add_document_publications"
down_revision: str | Sequence[str] | None = "0006_add_upload_corpus_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE app.document_publications ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "organization_id uuid NOT NULL, "
        "workspace_id uuid NOT NULL, "
        "document_version_id uuid NOT NULL, "
        "idempotency_key text NOT NULL CHECK (idempotency_key ~ '^[a-f0-9]{64}$'), "
        "state text NOT NULL CHECK (state IN ('active', 'rolled_back', 'deleted')), "
        "chunk_count integer NOT NULL CHECK (chunk_count >= 0), "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "updated_at timestamptz NOT NULL DEFAULT now(), "
        "UNIQUE (idempotency_key), "
        "FOREIGN KEY (organization_id, workspace_id, document_version_id) "
        "REFERENCES app.document_versions(organization_id, workspace_id, id)"
        ")"
    )
    op.execute("ALTER TABLE app.document_publications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.document_publications FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY document_publications_tenant_isolation "
        "ON app.document_publications USING ("
        "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid "
        "AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
        ") WITH CHECK ("
        "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid "
        "AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
        ")"
    )


def downgrade() -> None:
    raise RuntimeError("Document publication migration is intentionally forward-only")
