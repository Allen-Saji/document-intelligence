"""Add Phase 1 identity, lifecycle, audit, and search-projection foundations.

Revision ID: 0001_platform_foundation
Revises:
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_platform_foundation"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE TYPE app.workspace_role AS ENUM ('owner', 'admin', 'member', 'viewer')")
    op.execute(
        "CREATE TYPE app.document_lifecycle_state AS ENUM "
        "('upload_reserved', 'uploading', 'uploaded', 'ingesting', 'ready', 'quarantined', "
        "'failed', 'deleted')"
    )
    op.execute(
        "CREATE TYPE app.upload_state AS ENUM "
        "('reserved', 'uploaded', 'completed', 'expired', 'failed')"
    )
    op.execute(
        "CREATE TYPE app.processing_state AS ENUM "
        "('queued', 'running', 'succeeded', 'failed', 'cancelled')"
    )

    op.execute(
        "CREATE TABLE app.users ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "oidc_issuer text NOT NULL, "
        "oidc_subject text NOT NULL, "
        "email text, "
        "email_verified boolean NOT NULL DEFAULT false, "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "UNIQUE (oidc_issuer, oidc_subject)"
        ")"
    )
    op.execute(
        "CREATE TABLE app.memberships ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "organization_id uuid NOT NULL REFERENCES app.organizations(id), "
        "workspace_id uuid NOT NULL, "
        "user_id uuid NOT NULL REFERENCES app.users(id), "
        "role app.workspace_role NOT NULL, "
        "is_active boolean NOT NULL DEFAULT true, "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "updated_at timestamptz NOT NULL DEFAULT now(), "
        "UNIQUE (workspace_id, user_id), "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id)"
        ")"
    )
    op.execute(
        "CREATE TABLE app.groups ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "organization_id uuid NOT NULL REFERENCES app.organizations(id), "
        "workspace_id uuid NOT NULL, "
        "name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120), "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "UNIQUE (workspace_id, name), "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id)"
        ")"
    )
    op.execute(
        "CREATE TABLE app.group_members ("
        "group_id uuid NOT NULL REFERENCES app.groups(id) ON DELETE CASCADE, "
        "user_id uuid NOT NULL REFERENCES app.users(id), "
        "organization_id uuid NOT NULL, "
        "workspace_id uuid NOT NULL, "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "PRIMARY KEY (group_id, user_id), "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id)"
        ")"
    )
    op.execute(
        "CREATE TABLE app.sessions ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "organization_id uuid NOT NULL REFERENCES app.organizations(id), "
        "workspace_id uuid NOT NULL, "
        "user_id uuid NOT NULL REFERENCES app.users(id), "
        "refresh_token_hash text NOT NULL CHECK (refresh_token_hash ~ '^[a-f0-9]{64}$'), "
        "expires_at timestamptz NOT NULL, "
        "revoked_at timestamptz, "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "last_seen_at timestamptz, "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id)"
        ")"
    )
    op.execute(
        "CREATE TABLE app.api_keys ("
        "id uuid PRIMARY KEY, "
        "organization_id uuid NOT NULL REFERENCES app.organizations(id), "
        "workspace_id uuid NOT NULL, "
        "created_by_user_id uuid NOT NULL REFERENCES app.users(id), "
        "label text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 120), "
        "token_prefix text NOT NULL UNIQUE CHECK (token_prefix ~ '^diak_v1_[a-f0-9]{12}$'), "
        "token_hash text NOT NULL CHECK (token_hash ~ '^[a-f0-9]{64}$'), "
        "scopes text[] NOT NULL CHECK (cardinality(scopes) > 0), "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "expires_at timestamptz, "
        "revoked_at timestamptz, "
        "last_used_at timestamptz, "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id), "
        "CHECK (expires_at IS NULL OR expires_at > created_at)"
        ")"
    )
    op.execute(
        "CREATE TABLE app.corpora ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "organization_id uuid NOT NULL REFERENCES app.organizations(id), "
        "workspace_id uuid NOT NULL, "
        "name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200), "
        "permissions_version integer NOT NULL DEFAULT 1 CHECK (permissions_version > 0), "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "UNIQUE (workspace_id, name), "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id)"
        ")"
    )
    op.execute(
        "CREATE TABLE app.corpus_permissions ("
        "corpus_id uuid NOT NULL REFERENCES app.corpora(id) ON DELETE CASCADE, "
        "group_id uuid NOT NULL REFERENCES app.groups(id) ON DELETE CASCADE, "
        "organization_id uuid NOT NULL, "
        "workspace_id uuid NOT NULL, "
        "can_read boolean NOT NULL DEFAULT true, "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "PRIMARY KEY (corpus_id, group_id), "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id)"
        ")"
    )

    op.execute(
        "ALTER TABLE app.documents "
        "ADD COLUMN lifecycle_state app.document_lifecycle_state "
        "NOT NULL DEFAULT 'upload_reserved', "
        "ADD COLUMN active_version_id uuid, "
        "ADD COLUMN deleted_at timestamptz"
    )
    op.execute(
        "CREATE TABLE app.document_versions ("
        "id uuid PRIMARY KEY, "
        "organization_id uuid NOT NULL, "
        "workspace_id uuid NOT NULL, "
        "document_id uuid NOT NULL REFERENCES app.documents(id), "
        "version_number integer NOT NULL CHECK (version_number > 0), "
        "source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[a-f0-9]{64}$'), "
        "byte_size bigint NOT NULL CHECK (byte_size > 0), "
        "created_by_user_id uuid NOT NULL REFERENCES app.users(id), "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "UNIQUE (document_id, version_number), "
        "UNIQUE (document_id, source_sha256), "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id)"
        ")"
    )
    op.execute(
        "ALTER TABLE app.documents ADD CONSTRAINT documents_active_version_fk "
        "FOREIGN KEY (active_version_id) REFERENCES app.document_versions(id)"
    )
    op.execute(
        "CREATE TABLE app.document_objects ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "organization_id uuid NOT NULL, "
        "workspace_id uuid NOT NULL, "
        "document_version_id uuid NOT NULL REFERENCES app.document_versions(id) ON DELETE CASCADE, "
        "object_key text NOT NULL, "
        "kind text NOT NULL CHECK (kind IN "
        "('original_pdf', 'page_render', 'parsed_export', 'export')), "
        "checksum_sha256 text NOT NULL CHECK (checksum_sha256 ~ '^[a-f0-9]{64}$'), "
        "byte_size bigint NOT NULL CHECK (byte_size >= 0), "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "UNIQUE (document_version_id, kind, object_key), "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id)"
        ")"
    )
    op.execute(
        "CREATE TABLE app.upload_reservations ("
        "id uuid PRIMARY KEY, "
        "organization_id uuid NOT NULL, "
        "workspace_id uuid NOT NULL, "
        "actor_id uuid NOT NULL REFERENCES app.users(id), "
        "document_id uuid NOT NULL REFERENCES app.documents(id), "
        "document_version_id uuid NOT NULL, "
        "display_name text NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 500), "
        "declared_size_bytes bigint NOT NULL CHECK (declared_size_bytes > 0), "
        "state app.upload_state NOT NULL, "
        "multipart_object_key text NOT NULL UNIQUE, "
        "final_object_key text UNIQUE, "
        "sha256 text CHECK (sha256 IS NULL OR sha256 ~ '^[a-f0-9]{64}$'), "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "expires_at timestamptz NOT NULL, "
        "completed_at timestamptz, "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id), "
        "CHECK (expires_at > created_at), "
        "CHECK ((state = 'completed') = "
        "(final_object_key IS NOT NULL AND sha256 IS NOT NULL AND completed_at IS NOT NULL))"
        ")"
    )
    op.execute(
        "CREATE TABLE app.pipeline_versions ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "name text NOT NULL, "
        "version text NOT NULL, "
        "config_sha256 text NOT NULL CHECK (config_sha256 ~ '^[a-f0-9]{64}$'), "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "UNIQUE (name, version)"
        ")"
    )
    op.execute(
        "CREATE TABLE app.processing_runs ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "organization_id uuid NOT NULL, "
        "workspace_id uuid NOT NULL, "
        "document_version_id uuid NOT NULL REFERENCES app.document_versions(id), "
        "pipeline_version_id uuid NOT NULL REFERENCES app.pipeline_versions(id), "
        "state app.processing_state NOT NULL DEFAULT 'queued', "
        "temporal_workflow_id text NOT NULL UNIQUE, "
        "idempotency_key text NOT NULL UNIQUE, "
        "started_at timestamptz, "
        "completed_at timestamptz, "
        "failure_code text, "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "UNIQUE (document_version_id, pipeline_version_id), "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id)"
        ")"
    )
    op.execute(
        "CREATE TABLE app.search_indexes ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "organization_id uuid NOT NULL, "
        "workspace_id uuid NOT NULL, "
        "pipeline_version_id uuid NOT NULL REFERENCES app.pipeline_versions(id), "
        "index_name text NOT NULL UNIQUE, "
        "alias_name text NOT NULL, "
        "state app.processing_state NOT NULL DEFAULT 'queued', "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "published_at timestamptz, "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id)"
        ")"
    )
    op.execute(
        "CREATE TABLE app.audit_events ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "organization_id uuid NOT NULL REFERENCES app.organizations(id), "
        "workspace_id uuid, "
        "actor_id uuid REFERENCES app.users(id), "
        "event_type text NOT NULL, "
        "target_type text NOT NULL, "
        "target_id uuid, "
        "request_id uuid, "
        "occurred_at timestamptz NOT NULL, "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "FOREIGN KEY (organization_id, workspace_id) "
        "REFERENCES app.workspaces(organization_id, id)"
        ")"
    )

    op.execute(
        "CREATE OR REPLACE FUNCTION app.prevent_tenant_reassignment() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "IF NEW.organization_id <> OLD.organization_id OR NEW.workspace_id <> OLD.workspace_id "
        "THEN RAISE EXCEPTION 'tenant ownership is immutable'; END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION app.prevent_organization_reassignment() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN IF NEW.organization_id <> OLD.organization_id "
        "THEN RAISE EXCEPTION 'organization ownership is immutable'; END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER workspaces_organization_immutable BEFORE UPDATE ON app.workspaces "
        "FOR EACH ROW EXECUTE FUNCTION app.prevent_organization_reassignment()"
    )
    tenant_tables = (
        "memberships",
        "groups",
        "group_members",
        "sessions",
        "api_keys",
        "corpora",
        "corpus_permissions",
        "documents",
        "document_versions",
        "document_objects",
        "upload_reservations",
        "processing_runs",
        "search_indexes",
    )
    for table in tenant_tables:
        op.execute(
            f"CREATE TRIGGER {table}_tenant_immutable BEFORE UPDATE ON app.{table} "
            "FOR EACH ROW EXECUTE FUNCTION app.prevent_tenant_reassignment()"
        )
        op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON app.{table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON app.{table} USING ("
            "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid "
            "AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
            ") WITH CHECK ("
            "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid "
            "AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
            ")"
        )

    op.execute("ALTER TABLE app.users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.users FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY users_actor_isolation ON app.users USING ("
        "id = NULLIF(current_setting('app.actor_id', true), '')::uuid"
        ") WITH CHECK (id = NULLIF(current_setting('app.actor_id', true), '')::uuid)"
    )
    op.execute("ALTER TABLE app.pipeline_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.pipeline_versions FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY pipeline_versions_read_only ON app.pipeline_versions USING (true)")
    op.execute("ALTER TABLE app.audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.audit_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY audit_events_tenant_isolation ON app.audit_events USING ("
        "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid "
        "AND (workspace_id IS NULL OR workspace_id = "
        "NULLIF(current_setting('app.workspace_id', true), '')::uuid)"
        ") WITH CHECK ("
        "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid "
        "AND (workspace_id IS NULL OR workspace_id = "
        "NULLIF(current_setting('app.workspace_id', true), '')::uuid)"
        ")"
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app "
        "TO document_intelligence_app"
    )
    op.execute("REVOKE UPDATE, DELETE ON app.audit_events FROM document_intelligence_app")


def downgrade() -> None:
    raise RuntimeError("Phase 1 foundation migration is intentionally forward-only")
