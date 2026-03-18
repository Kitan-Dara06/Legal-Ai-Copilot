-- RLS: restrictive policies for the app database role (DATABASE_URL)
-- Run this in Supabase SQL Editor after enabling RLS on these tables.
--
-- The app's backend connects with a service role that has full access.
-- Anonymous / authenticated Supabase roles get NO access via PostgREST.
-- This prevents data exposure through Supabase's auto-generated REST API.

-- Drop old permissive policies first
DROP POLICY IF EXISTS "app_role_all_organizations" ON organizations;
DROP POLICY IF EXISTS "app_role_all_users" ON users;
DROP POLICY IF EXISTS "app_role_all_api_keys" ON api_keys;
DROP POLICY IF EXISTS "app_role_all_invites" ON invites;
DROP POLICY IF EXISTS "app_role_all_files" ON files;

-- Organizations: only service_role (backend) can access
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_organizations"
  ON organizations FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- Users: only service_role
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_users"
  ON users FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- API keys: only service_role
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_api_keys"
  ON api_keys FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- Invites: only service_role
ALTER TABLE invites ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_invites"
  ON invites FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- Files: only service_role
ALTER TABLE files ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_files"
  ON files FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- User-org memberships: only service_role
ALTER TABLE user_org_memberships ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_user_org_memberships"
  ON user_org_memberships FOR ALL TO service_role
  USING (true) WITH CHECK (true);
