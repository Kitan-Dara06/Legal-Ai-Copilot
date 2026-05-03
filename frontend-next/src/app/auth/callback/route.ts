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
      const isLocalEnv = process.env.NODE_ENV === 'development';

      // In production, strictly require explicit site config
      let resolvedBase = origin;
      if (!isLocalEnv) {
          const explicitSiteUrl = (process.env.NEXT_PUBLIC_API_URL || '').replace(/\/api$/, '');
          if (!explicitSiteUrl) {
              throw new Error("NEXT_PUBLIC_API_URL is required in production environment.");
          }
          resolvedBase = explicitSiteUrl;
      }

      // Password recovery → always send to the update-password page
      if (type === 'recovery') {
        return NextResponse.redirect(`${resolvedBase}/auth/update-password`)
      }

      // Default: forward with any extra params
      let next = searchParams.get('next') ?? '/'
      
      // STRICT OPEN-REDIRECT PROTECTION
      // 1. Ensure it's a relative path (starts with /)
      // 2. Prevent protocol-relative URLs (starts with //)
      // 3. Prevent any string containing '://' (absolute URLs)
      if (!next.startsWith('/') || next.startsWith('//') || next.includes('://')) {
          console.warn(`[AuthCallback] Blocked potentially unsafe redirect attempt to: ${next}`);
          next = '/';
      }
      
      const url = new URL(`${resolvedBase}${next}`)
      if (orgId) url.searchParams.set('org_id', orgId)
      if (orgName) url.searchParams.set('org_name', orgName)
      
      console.log(`[AuthCallback] Redirecting to: ${url.toString()}`);
      return NextResponse.redirect(url.toString())
    }
  }

  // return the user to an error page with instructions
  return NextResponse.redirect(`${origin}/auth/auth-code-error`)
}
