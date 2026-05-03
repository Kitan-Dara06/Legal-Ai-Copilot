-- Trigger to delete local user profile when an account is deleted from Supabase Auth
-- Run this in the Supabase SQL Editor

-- 1. Create the function
create or replace function public.handle_deleted_user()
returns trigger as $$
begin
  -- Delete the user from public.users (will also trigger any cascading deletes you set up)
  delete from public.users where supabase_user_id = old.id::text;
  return old;
end;
$$ language plpgsql security definer;

-- 2. Create the trigger on auth.users
drop trigger if exists on_auth_user_deleted on auth.users;
create trigger on_auth_user_deleted
  after delete on auth.users
  for each row execute procedure public.handle_deleted_user();
