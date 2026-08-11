"""Persist provider multipart upload IDs for durable completion and cleanup.

Revision ID: 0004_record_multipart_upload_ids
Revises: 0003_enforce_base_rls
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_record_multipart_upload_ids"
down_revision: str | Sequence[str] | None = "0003_enforce_base_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.upload_reservations ADD COLUMN multipart_upload_id text UNIQUE")


def downgrade() -> None:
    raise RuntimeError("Multipart upload IDs are retained for durable recovery")
