"""Add a narrow API-key lookup function for pre-tenant authentication.

Revision ID: 0005_add_api_key_lookup_function
Revises: 0004_record_multipart_upload_ids
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_add_api_key_lookup_function"
down_revision: str | Sequence[str] | None = "0004_record_multipart_upload_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.lookup_api_key_by_prefix(p_token_prefix text)
        RETURNS TABLE (
            id uuid,
            organization_id uuid,
            workspace_id uuid,
            created_by_user_id uuid,
            label text,
            token_prefix text,
            token_hash text,
            scopes text[],
            created_at timestamptz,
            expires_at timestamptz,
            revoked_at timestamptz,
            last_used_at timestamptz
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = app, pg_temp
        AS $$
            SELECT
                api_keys.id,
                api_keys.organization_id,
                api_keys.workspace_id,
                api_keys.created_by_user_id,
                api_keys.label,
                api_keys.token_prefix,
                api_keys.token_hash,
                api_keys.scopes,
                api_keys.created_at,
                api_keys.expires_at,
                api_keys.revoked_at,
                api_keys.last_used_at
            FROM app.api_keys
            WHERE api_keys.token_prefix = p_token_prefix
            LIMIT 1
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION app.lookup_api_key_by_prefix(text) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION app.lookup_api_key_by_prefix(text) TO document_intelligence_app"
    )


def downgrade() -> None:
    raise RuntimeError("API-key lookup function migration is intentionally forward-only")
