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
      const forwardedHost = request.headers.get('x-forwarded-host') // original origin before load balancer
      const isLocalEnv = process.env.NODE_ENV === 'development'

      const buildRedirectUrl = (base: string) => {
        const url = new URL(`${base}${next}`)
        if (orgId) url.searchParams.set('org_id', orgId)
        if (orgName) url.searchParams.set('org_name', orgName)
        return url.toString()
      }

      if (isLocalEnv) {
        return NextResponse.redirect(buildRedirectUrl(origin))
      } else if (forwardedHost) {
        return NextResponse.redirect(buildRedirectUrl(`https://${forwardedHost}`))
      } else {
        return NextResponse.redirect(buildRedirectUrl(origin))
      }
    }
  }

  // return the user to an error page with instructions
  return NextResponse.redirect(`${origin}/auth/auth-code-error`)
}
