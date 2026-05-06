-- RLS: restrictive policies for the app database role (DATABASE_URL)
-- Run this in Supabase SQL Editor after enabling RLS on these tables.
--
-- The app's backend connects with a service role that has full access.
-- Anonymous / authenticated Supabase roles get NO access via PostgREST.
-- This prevents data exposure through Supabase's auto-generated REST API.

-- Drop old permissive policies first (including deprecated table names)
DROP POLICY IF EXISTS "app_role_all_organizations" ON organizations;
DROP POLICY IF EXISTS "app_role_all_users" ON users;
DROP POLICY IF EXISTS "app_role_all_api_keys" ON api_keys;
DROP POLICY IF EXISTS "app_role_all_invites" ON organization_invites;
DROP POLICY IF EXISTS "app_role_all_files" ON documents;

-- Helper to quickly enable RLS and create service_role-only policy
DO $$
DECLARE
    t_name text;
    tables_list text[] := ARRAY[
        'organizations',
        'users',
        'user_org_memberships',
        'api_keys',
        'organization_invites',
        'workspaces',
        'workspace_sessions',
        'documents',
        'goals',
        'workflow_executions',
        'findings',
        'actions',
        'approval_requests',
        'audit_log',
        'defined_terms_registry',
        'deadline_registry',
        'tool_call_log'
    ];
BEGIN
    FOREACH t_name IN ARRAY tables_list
    LOOP
        -- Enable RLS
        EXECUTE format('ALTER TABLE IF EXISTS %I ENABLE ROW LEVEL SECURITY', t_name);

        -- Drop any existing service_role policy to avoid duplicates
        EXECUTE format('DROP POLICY IF EXISTS "service_role_all_%s" ON %I', t_name, t_name);

        -- Create the service_role-only policy
        EXECUTE format('
            CREATE POLICY "service_role_all_%s"
            ON %I FOR ALL TO service_role
            USING (true) WITH CHECK (true);
        ', t_name, t_name);
    END LOOP;
END
$$;

-- Rule FR-EXEC-01 / Auditing: Add database-level INSERT-only constraint for audit_log
DO $$
BEGIN
    -- Drop policy if it exists to allow re-running
    DROP POLICY IF EXISTS "audit_log_insert_only" ON audit_log;

    -- Ensure service_role can only INSERT and SELECT, strictly preventing UPDATE and DELETE
    -- We restrict this at the database level by revoking UPDATE and DELETE privileges
    REVOKE UPDATE, DELETE ON TABLE audit_log FROM PUBLIC;
    REVOKE UPDATE, DELETE ON TABLE audit_log FROM service_role;
    REVOKE UPDATE, DELETE ON TABLE audit_log FROM authenticated;
    REVOKE UPDATE, DELETE ON TABLE audit_log FROM anon;
END
$$;
