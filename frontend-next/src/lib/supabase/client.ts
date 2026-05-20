import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

// Typed alias so callers get proper inference without `as any`
type BrowserClient = ReturnType<typeof createBrowserClient>;

export function createClient(): BrowserClient {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !key) {
    // Return a minimal typed stub during build when env vars are absent.
    // Cast through `unknown` instead of `any` so callers retain type safety.
    console.warn("Supabase credentials missing — using stub client for build.");
    return {
      auth: {
        getSession: async () => ({
          data: { session: null },
          error: null,
        }),
        onAuthStateChange: () => ({
          data: { subscription: { unsubscribe: () => {} } },
        }),
        signInWithPassword: async () => ({
          data: { session: null, user: null },
          error: null,
        }),
        signUp: async () => ({ data: { session: null, user: null }, error: null }),
        signOut: async () => ({ error: null }),
        resetPasswordForEmail: async () => ({ data: {}, error: null }),
        updateUser: async () => ({ data: { user: null }, error: null }),
      },
    } as unknown as BrowserClient;  // unknown → BrowserClient preserves inference
  }

  return createBrowserClient(url, key);
}
