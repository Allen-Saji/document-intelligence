\set ON_ERROR_STOP on

BEGIN;

INSERT INTO app.organizations (id, name) VALUES
  ('00000000-0000-4000-8000-000000000001', 'Tenant A'),
  ('00000000-0000-4000-8000-000000000002', 'Tenant B');

INSERT INTO app.workspaces (id, organization_id, name) VALUES
  ('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'Workspace A'),
  ('00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000002', 'Workspace B');

INSERT INTO app.users (id, oidc_issuer, oidc_subject) VALUES
  ('00000000-0000-4000-8000-000000000101', 'https://identity.example', 'user-a'),
  ('00000000-0000-4000-8000-000000000202', 'https://identity.example', 'user-b');

INSERT INTO app.memberships (organization_id, workspace_id, user_id, role) VALUES
  ('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000101', 'owner'),
  ('00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000202', 'owner');

INSERT INTO app.documents (id, organization_id, workspace_id, display_name) VALUES
  ('00000000-0000-4000-8000-000000000111', '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', 'Tenant A document'),
  ('00000000-0000-4000-8000-000000000222', '00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000022', 'Tenant B document');

INSERT INTO app.document_versions (
  id, organization_id, workspace_id, document_id, version_number, source_sha256, byte_size, created_by_user_id
) VALUES
  ('00000000-0000-4000-8000-000000000311', '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000111', 1, repeat('a', 64), 1024, '00000000-0000-4000-8000-000000000101'),
  ('00000000-0000-4000-8000-000000000322', '00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000222', 1, repeat('b', 64), 1024, '00000000-0000-4000-8000-000000000202');

INSERT INTO app.document_objects (
  organization_id, workspace_id, document_version_id, object_key, kind, checksum_sha256, byte_size
) VALUES
  ('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000311', 'organizations/a/original.pdf', 'original_pdf', repeat('a', 64), 1024),
  ('00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000322', 'organizations/b/original.pdf', 'original_pdf', repeat('b', 64), 1024);

INSERT INTO app.api_keys (
  id, organization_id, workspace_id, created_by_user_id, label, token_prefix, token_hash, scopes
) VALUES
  ('00000000-0000-4000-8000-000000000411', '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000101', 'Tenant A key', 'diak_v1_000000000411', repeat('a', 64), ARRAY['document:read']),
  ('00000000-0000-4000-8000-000000000422', '00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000202', 'Tenant B key', 'diak_v1_000000000422', repeat('b', 64), ARRAY['document:read']);

INSERT INTO app.audit_events (organization_id, workspace_id, actor_id, event_type, target_type, occurred_at) VALUES
  ('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000101', 'document.uploaded', 'document', now());

SET LOCAL ROLE document_intelligence_app;
SET LOCAL app.organization_id = '00000000-0000-4000-8000-000000000001';
SET LOCAL app.workspace_id = '00000000-0000-4000-8000-000000000011';
SET LOCAL app.actor_id = '00000000-0000-4000-8000-000000000101';

DO $$
BEGIN
  IF (SELECT count(*) FROM app.api_keys) <> 1 THEN
    RAISE EXCEPTION 'RLS failed: Tenant A must see exactly one API key';
  END IF;
  IF (SELECT count(*) FROM app.document_objects) <> 1 THEN
    RAISE EXCEPTION 'RLS failed: Tenant A must see exactly one document object';
  END IF;
  IF (SELECT count(*) FROM app.users) <> 1 THEN
    RAISE EXCEPTION 'RLS failed: actor must see only its own user record';
  END IF;

  BEGIN
    INSERT INTO app.api_keys (
      id, organization_id, workspace_id, created_by_user_id, label, token_prefix, token_hash, scopes
    ) VALUES (
      '00000000-0000-4000-8000-000000000499',
      '00000000-0000-4000-8000-000000000002',
      '00000000-0000-4000-8000-000000000022',
      '00000000-0000-4000-8000-000000000202',
      'Forbidden key', 'diak_v1_000000000499', repeat('c', 64), ARRAY['document:read']
    );
    RAISE EXCEPTION 'RLS failed: cross-tenant API-key insert unexpectedly succeeded';
  EXCEPTION
    WHEN insufficient_privilege THEN NULL;
  END;

  BEGIN
    INSERT INTO app.document_objects (
      organization_id, workspace_id, document_version_id, object_key, kind, checksum_sha256, byte_size
    ) VALUES (
      '00000000-0000-4000-8000-000000000001',
      '00000000-0000-4000-8000-000000000011',
      '00000000-0000-4000-8000-000000000322',
      'organizations/a/foreign.pdf', 'original_pdf', repeat('c', 64), 1024
    );
    RAISE EXCEPTION 'Foreign-key failed: a Tenant A object referenced Tenant B version';
  EXCEPTION
    WHEN foreign_key_violation THEN NULL;
  END;

  BEGIN
    UPDATE app.documents
    SET workspace_id = '00000000-0000-4000-8000-000000000022'
    WHERE id = '00000000-0000-4000-8000-000000000111';
    RAISE EXCEPTION 'Trigger failed: tenant reassignment unexpectedly succeeded';
  EXCEPTION
    WHEN raise_exception THEN NULL;
  END;

  BEGIN
    UPDATE app.audit_events SET target_type = 'tampered';
    RAISE EXCEPTION 'Privileges failed: append-only audit update unexpectedly succeeded';
  EXCEPTION
    WHEN insufficient_privilege THEN NULL;
  END;
END
$$;

RESET ROLE;
ROLLBACK;

SELECT 'Phase 1 RLS verification passed' AS result;
