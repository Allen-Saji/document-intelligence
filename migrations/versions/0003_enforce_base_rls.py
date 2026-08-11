"""Enforce RLS for legacy foundation tables regardless of bootstrap history.

Revision ID: 0003_enforce_base_rls
Revises: 0002_tenant_bound_foreign_keys
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_enforce_base_rls"
down_revision: str | Sequence[str] | None = "0002_tenant_bound_foreign_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.organizations FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS organizations_tenant_isolation ON app.organizations")
    op.execute(
        "CREATE POLICY organizations_tenant_isolation ON app.organizations USING ("
        "id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
        ") WITH CHECK (id = NULLIF(current_setting('app.organization_id', true), '')::uuid)"
    )
    op.execute("ALTER TABLE app.workspaces ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.workspaces FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS workspaces_tenant_isolation ON app.workspaces")
    op.execute(
        "CREATE POLICY workspaces_tenant_isolation ON app.workspaces USING ("
        "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
        ") WITH CHECK (organization_id = "
        "NULLIF(current_setting('app.organization_id', true), '')::uuid)"
    )
    op.execute("ALTER TABLE app.documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.documents FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS documents_tenant_isolation ON app.documents")
    op.execute(
        "CREATE POLICY documents_tenant_isolation ON app.documents USING ("
        "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid "
        "AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
        ") WITH CHECK ("
        "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid "
        "AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
        ")"
    )


def downgrade() -> None:
    raise RuntimeError("Base RLS enforcement is intentionally forward-only")
