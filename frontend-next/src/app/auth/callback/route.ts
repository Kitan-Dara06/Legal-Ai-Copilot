import { NextResponse } from 'next/server'
import { createClient } from '@/lib/supabase/server'

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')
  const type = searchParams.get('type') // 'recovery' for password reset links

  // Extra params that should be forwarded on
  const orgId = searchParams.get('org_id')
  const orgName = searchParams.get('org_name')

  if (code) {
    const supabase = await createClient()
    const { error } = await supabase.auth.exchangeCodeForSession(code)
    if (!error) {
      const forwardedHost = request.headers.get('x-forwarded-host');
      const isLocalEnv = process.env.NODE_ENV === 'development';

      // In production, prioritize explicit site config so Nginx internal upstream names don't leak
      let resolvedBase = origin;
      if (!isLocalEnv) {
          const explicitSiteUrl = (process.env.NEXT_PUBLIC_API_URL || '').replace(/\/api$/, '');
          if (forwardedHost && !forwardedHost.includes('upstream')) {
              resolvedBase = `https://${forwardedHost}`;
          } else if (explicitSiteUrl) {
              resolvedBase = explicitSiteUrl;
          } else if (origin.includes('upstream')) {
              resolvedBase = 'https://legalrag.codes';
          }
      }

      // Password recovery → always send to the update-password page
      if (type === 'recovery') {
        return NextResponse.redirect(`${resolvedBase}/auth/update-password`)
      }

      // Default: forward with any extra params
      const next = searchParams.get('next') ?? '/'
      const url = new URL(`${resolvedBase}${next}`)
      if (orgId) url.searchParams.set('org_id', orgId)
      if (orgName) url.searchParams.set('org_name', orgName)
      return NextResponse.redirect(url.toString())
    }
  }

  // return the user to an error page with instructions
  return NextResponse.redirect(`${origin}/auth/auth-code-error`)
}
