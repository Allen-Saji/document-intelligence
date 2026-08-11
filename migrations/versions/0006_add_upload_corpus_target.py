"""Persist the target corpus for upload reservations.

Revision ID: 0006_add_upload_corpus_target
Revises: 0005_add_api_key_lookup_function
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_add_upload_corpus_target"
down_revision: str | Sequence[str] | None = "0005_add_api_key_lookup_function"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.upload_reservations ADD COLUMN corpus_id uuid")
    op.execute(
        """
        UPDATE app.upload_reservations
        SET corpus_id = (
            SELECT corpora.id
            FROM app.corpora
            WHERE corpora.organization_id = upload_reservations.organization_id
            AND corpora.workspace_id = upload_reservations.workspace_id
            ORDER BY corpora.created_at, corpora.id
            LIMIT 1
        )
        WHERE upload_reservations.corpus_id IS NULL
        """
    )
    op.execute("ALTER TABLE app.upload_reservations ALTER COLUMN corpus_id SET NOT NULL")
    op.execute(
        "ALTER TABLE app.upload_reservations ADD CONSTRAINT "
        "upload_reservations_corpus_tenant_fk FOREIGN KEY "
        "(organization_id, workspace_id, corpus_id) "
        "REFERENCES app.corpora(organization_id, workspace_id, id)"
    )


def downgrade() -> None:
    raise RuntimeError("Upload corpus target migration is intentionally forward-only")
