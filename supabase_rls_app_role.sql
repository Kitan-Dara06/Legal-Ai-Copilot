-- RLS: permissive policies for the app database role (DATABASE_URL)
-- Run this in Supabase SQL Editor after enabling RLS on these tables.
--
-- If your DATABASE_URL uses a role other than 'postgres', replace postgres
-- in "TO postgres" with that role (e.g. TO your_app_role).

-- Organizations
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
CREATE POLICY "app_role_all_organizations"
  ON organizations FOR ALL TO postgres
  USING (true) WITH CHECK (true);

-- Users
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY "app_role_all_users"
  ON users FOR ALL TO postgres
  USING (true) WITH CHECK (true);

-- API keys
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY "app_role_all_api_keys"
  ON api_keys FOR ALL TO postgres
  USING (true) WITH CHECK (true);

-- Invites
ALTER TABLE invites ENABLE ROW LEVEL SECURITY;
CREATE POLICY "app_role_all_invites"
  ON invites FOR ALL TO postgres
  USING (true) WITH CHECK (true);

-- Files
ALTER TABLE files ENABLE ROW LEVEL SECURITY;
CREATE POLICY "app_role_all_files"
  ON files FOR ALL TO postgres
  USING (true) WITH CHECK (true);
