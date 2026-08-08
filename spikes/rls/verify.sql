\set ON_ERROR_STOP on

BEGIN;

INSERT INTO app.organizations (id, name) VALUES
  ('00000000-0000-4000-8000-000000000001', 'Tenant A'),
  ('00000000-0000-4000-8000-000000000002', 'Tenant B');

INSERT INTO app.workspaces (id, organization_id, name) VALUES
  ('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'Workspace A'),
  ('00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000002', 'Workspace B');

INSERT INTO app.documents (id, organization_id, workspace_id, display_name) VALUES
  (
    '00000000-0000-4000-8000-000000000111',
    '00000000-0000-4000-8000-000000000001',
    '00000000-0000-4000-8000-000000000011',
    'Tenant A document'
  ),
  (
    '00000000-0000-4000-8000-000000000222',
    '00000000-0000-4000-8000-000000000002',
    '00000000-0000-4000-8000-000000000022',
    'Tenant B document'
  );

SET LOCAL ROLE document_intelligence_app;
SET LOCAL app.organization_id = '00000000-0000-4000-8000-000000000001';
SET LOCAL app.workspace_id = '00000000-0000-4000-8000-000000000011';

DO $$
BEGIN
  IF (SELECT count(*) FROM app.organizations) <> 1 THEN
    RAISE EXCEPTION 'RLS failed: Tenant A must see exactly one organization';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM app.organizations
    WHERE id = '00000000-0000-4000-8000-000000000002'
  ) THEN
    RAISE EXCEPTION 'RLS failed: Tenant A can see Tenant B organization';
  END IF;

  IF (SELECT count(*) FROM app.documents) <> 1 THEN
    RAISE EXCEPTION 'RLS failed: Tenant A must see exactly one document';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM app.documents
    WHERE organization_id = '00000000-0000-4000-8000-000000000002'
  ) THEN
    RAISE EXCEPTION 'RLS failed: Tenant A can see Tenant B data';
  END IF;
END
$$;

DO $$
BEGIN
  BEGIN
    INSERT INTO app.organizations (id, name) VALUES (
      '00000000-0000-4000-8000-000000000003',
      'Forbidden Tenant C'
    );
    RAISE EXCEPTION 'RLS failed: cross-tenant organization insert unexpectedly succeeded';
  EXCEPTION
    WHEN insufficient_privilege THEN
      NULL;
  END;

  BEGIN
    INSERT INTO app.documents (id, organization_id, workspace_id, display_name) VALUES (
      '00000000-0000-4000-8000-000000000333',
      '00000000-0000-4000-8000-000000000002',
      '00000000-0000-4000-8000-000000000022',
      'Forbidden cross-tenant insert'
    );
    RAISE EXCEPTION 'RLS failed: cross-tenant insert unexpectedly succeeded';
  EXCEPTION
    WHEN insufficient_privilege THEN
      NULL;
  END;
END
$$;

RESET ROLE;
ROLLBACK;

SELECT 'RLS verification passed' AS result;
