import { NextResponse } from 'next/server'
// The client you created from the Server-Side Auth instructions
import { createClient } from '@/lib/supabase/server'

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')
  // if "next" is in param, use it as the redirect URL
  const next = searchParams.get('next') ?? '/'

  // Extra params (e.g. org_id, org_name) that should be forwarded to the next page
  const orgId = searchParams.get('org_id')
  const orgName = searchParams.get('org_name')

  if (code) {
    const supabase = await createClient()
    const { error } = await supabase.auth.exchangeCodeForSession(code)
    if (!error) {
      const forwardedHost = request.headers.get('x-forwarded-host');
      const isLocalEnv = process.env.NODE_ENV === 'development';

      const buildRedirectUrl = (base: string) => {
        const url = new URL(`${base}${next}`);
        if (orgId) url.searchParams.set('org_id', orgId);
        if (orgName) url.searchParams.set('org_name', orgName);
        return url.toString();
      };

      // Ensure we don't accidentally redirect to the internal docker network hostname
      let resolvedBase = origin;
      if (isLocalEnv) {
          resolvedBase = origin;
      } else {
          // In production, prioritize explicit site config so Nginx internal upstream names don't leak
          const explicitSiteUrl = (process.env.NEXT_PUBLIC_API_URL || '').replace(/\/api$/, '');
          
          if (forwardedHost && !forwardedHost.includes('upstream')) {
              resolvedBase = `https://${forwardedHost}`;
          } else if (explicitSiteUrl) {
              resolvedBase = explicitSiteUrl;
          } else if (origin.includes('upstream')) {
              resolvedBase = 'https://legalrag.codes'; // Safe fallback
          }
      }

      return NextResponse.redirect(buildRedirectUrl(resolvedBase));
    }
  }

  // return the user to an error page with instructions
  return NextResponse.redirect(`${origin}/auth/auth-code-error`)
}
