\set ON_ERROR_STOP on
\getenv APP_DB_PASSWORD APP_DB_PASSWORD

CREATE ROLE document_intelligence_app
  LOGIN
  PASSWORD :'APP_DB_PASSWORD'
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOBYPASSRLS;

CREATE SCHEMA app;

CREATE TABLE app.organizations (
  id uuid PRIMARY KEY,
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.workspaces (
  id uuid PRIMARY KEY,
  organization_id uuid NOT NULL REFERENCES app.organizations(id),
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id)
);

CREATE TABLE app.documents (
  id uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  workspace_id uuid NOT NULL,
  display_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, workspace_id)
    REFERENCES app.workspaces(organization_id, id)
);

ALTER TABLE app.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.organizations FORCE ROW LEVEL SECURITY;
ALTER TABLE app.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.workspaces FORCE ROW LEVEL SECURITY;
ALTER TABLE app.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.documents FORCE ROW LEVEL SECURITY;

CREATE POLICY organizations_tenant_isolation ON app.organizations
  USING (
    id = NULLIF(current_setting('app.organization_id', true), '')::uuid
  )
  WITH CHECK (
    id = NULLIF(current_setting('app.organization_id', true), '')::uuid
  );

CREATE POLICY workspaces_tenant_isolation ON app.workspaces
  USING (
    organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
  )
  WITH CHECK (
    organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
  );

CREATE POLICY documents_tenant_isolation ON app.documents
  USING (
    organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
    AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
  )
  WITH CHECK (
    organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
    AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
  );

GRANT USAGE ON SCHEMA app TO document_intelligence_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO document_intelligence_app;
