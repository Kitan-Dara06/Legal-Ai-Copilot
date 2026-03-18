# Full Project Audit — Implementation Plan

Comprehensive review of every file in the Legal AI Copilot project, covering security, architecture, UX, and data integrity. Grouped by severity.

---

## Critical (Must Fix Before Deploy)

### C1: Data Leak — Qdrant [org_id](file:///home/kitan/Documents/legal_rag/app/dependencies.py#102-108) Format Mismatch  
**Files:** [tasks.py:383](file:///home/kitan/Documents/legal_rag/app/tasks.py#L383) · [store.py:148](file:///home/kitan/Documents/legal_rag/app/services/store.py#L148)  
**Problem:** After the VARCHAR→UUID migration, old Qdrant points still have slug-formatted [org_id](file:///home/kitan/Documents/legal_rag/app/dependencies.py#102-108) (e.g. `"my-firm"`). New uploads store UUID (e.g. `"550e8400-..."`). Qdrant keyword filter matches on exact string — so queries may return stale cross-org data or miss documents entirely.  
**Fix:** Write `scripts/fix_qdrant_org_ids.py` to scroll all Qdrant points and remap old slug [org_id](file:///home/kitan/Documents/legal_rag/app/dependencies.py#102-108) → UUID via the Postgres `organizations` table. Run once.

### C2: Invited Users Forced to Create an Org  
**File:** [dependencies.py:312-319](file:///home/kitan/Documents/legal_rag/app/dependencies.py#L312-L319)  
**Problem:** When [_build_supabase_auth_context](file:///home/kitan/Documents/legal_rag/app/dependencies.py#257-335) finds a valid invite for the email, it raises `403 invite_required` instead of auto-accepting. The frontend has no mechanism to trigger `accept-invite-by-token` from the magic link flow.  
**Fix:** Auto-accept the invite inside [_build_supabase_auth_context](file:///home/kitan/Documents/legal_rag/app/dependencies.py#257-335): create User, create UserOrgMembership, mark invite accepted, return context.

### C3: Forgot Password Broken — Streamlit JS Sandboxing  
**File:** [frontend/app.py:337-347](file:///home/kitan/Documents/legal_rag/frontend/app.py#L337-L347)  
**Problem:** `st.markdown("<script>...", unsafe_allow_html=True)` is silently ignored by Streamlit. The JS that converts Supabase `#access_token=` hash fragments to `?access_token=` query params never executes. Recovery tokens never reach Python.  
**Fix:** Replace with `st.components.v1.html()` which actually executes JavaScript.

### C4: Admin Role Checked Globally, Not Per-Org  
**Files:** [dependencies.py:88-99](file:///home/kitan/Documents/legal_rag/app/dependencies.py#L88-L99) · [dependencies.py:475-484](file:///home/kitan/Documents/legal_rag/app/dependencies.py#L475-L484)  
**Problem:** [get_admin_auth_context](file:///home/kitan/Documents/legal_rag/app/dependencies.py#88-100) and [get_supabase_admin_context](file:///home/kitan/Documents/legal_rag/app/dependencies.py#475-485) check `User.role` (the deprecated global column) instead of `UserOrgMembership.role` for the active org. A user who is ADMIN in org A but MEMBER in org B gets ADMIN privileges in org B.  
**Fix:** Look up `UserOrgMembership.role` for the active org when checking admin status.

### C5: XSRF Protection Disabled  
**File:** [docker-compose.yml:31-32](file:///home/kitan/Documents/legal_rag/docker-compose.yml#L31-L32)

```yaml
STREAMLIT_SERVER_ENABLE_CORS=false
STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false
```

**Problem:** Disabling XSRF protection makes the Streamlit frontend vulnerable to cross-site request forgery.  
**Fix:** Re-enable XSRF protection. If CORS issues arise, configure allowed origins instead of disabling entirely.

### C6: No CORS Middleware on FastAPI  
**File:** [main.py](file:///home/kitan/Documents/legal_rag/main.py)  
**Problem:** No `CORSMiddleware` configured. The API will reject preflight requests from any origin except same-origin. Currently works because Streamlit proxies backend calls server-side, but any future client-side JS or mobile app will fail.  
**Fix:** Add `CORSMiddleware` with explicit `allow_origins` set to the production domain.

### C7: RLS Policies Are Permissive (`USING (true)`)  
**File:** [supabase_rls_app_role.sql](file:///home/kitan/Documents/legal_rag/supabase_rls_app_role.sql)  
**Problem:** All RLS policies allow full access (`USING (true) WITH CHECK (true)`) for the postgres role. If Supabase's `anon` or `authenticated` roles can reach tables directly (e.g. via PostgREST), all data is exposed.  
**Fix:** Restrict RLS policies to `service_role` only and ensure the app connects with a dedicated role that has controlled permissions. Add org-scoped RLS for [files](file:///home/kitan/Documents/legal_rag/app/routers/injest.py#76-118), `api_keys`, and `invites`.

---

## High (Fix Soon)

### H1: Organisation Name Never Displayed  
**Files:** [models.py:48](file:///home/kitan/Documents/legal_rag/app/models.py#L48) · [frontend/app.py](file:///home/kitan/Documents/legal_rag/frontend/app.py)  
**Problem:** `Organization.name` column exists but is never populated. The UI shows the slug (`my-firm-2025`) instead of the display name (`Acme Legal LLP`). The signup and setup-org flows don't ask for a display name.  
**Fix:**
- Add `org_name` field to [SignupRequest](file:///home/kitan/Documents/legal_rag/app/routers/auth.py#36-40) and [SetupOrgRequest](file:///home/kitan/Documents/legal_rag/app/routers/auth.py#51-53) schemas  
- Populate `Organization.name` during creation  
- Display `org.name` (with slug as subtitle) in the sidebar

### H2: Login Returns 403 Intermittently  
**File:** [dependencies.py:257-334](file:///home/kitan/Documents/legal_rag/app/dependencies.py#L257-L334)  
**Problem:** [_build_supabase_auth_context](file:///home/kitan/Documents/legal_rag/app/dependencies.py#257-335) raises 403 for *any* first-time Supabase login where no local User exists and no invite is found. This happens when:
1. A user signs up via Supabase (e.g. Google OAuth) but hasn't called `POST /auth/signup` first
2. The Supabase session is valid but the backend has no matching User row

The error message says "Please sign up" but the user thinks they already have.  
**Fix:** Make the `setup_required` error message clearer in the frontend — show a prominent banner explaining the user needs to create a workspace, not re-sign-up.

### H3: R2 Object Keys Are Guessable  
**File:** [injest.py:128](file:///home/kitan/Documents/legal_rag/app/routers/injest.py#L128): `blob_name = f"{file_id}_{filename}"`  
**Problem:** Object keys are `<integer_id>_<filename>`. Since file IDs are auto-incrementing integers, anyone with R2 access could enumerate all stored PDFs.  
**Fix:** Use UUID-based blob names: `blob_name = f"{uuid.uuid4().hex}_{filename}"` and store the blob_name in the [files](file:///home/kitan/Documents/legal_rag/app/routers/injest.py#76-118) table for retrieval.

### H4: Session Filename Query Missing org_id Filter  
**File:** [session.py:121-126](file:///home/kitan/Documents/legal_rag/app/routers/session.py#L121-L126)  
**Problem:** `FileModel.id.in_(file_ids_in_session)` without `FileModel.org_id == org_id`. Low risk (session itself is org-verified), but violates defense-in-depth.  
**Fix:** Add `FileModel.org_id == org_id` to the WHERE clause.

### H5: Frontend Doesn't Send `X-Active-Org` Header  
**File:** [frontend/app.py:89-94](file:///home/kitan/Documents/legal_rag/frontend/app.py#L89-L94)  
**Problem:** [get_headers()](file:///home/kitan/Documents/legal_rag/frontend/app.py#89-95) only sends Bearer or API key. [get_org_id_unified](file:///home/kitan/Documents/legal_rag/app/dependencies.py#369-467) falls back to membership lookup, which works for single-org users but will break when multi-org is used.  
**Fix:** Store `org_slug` in session state and include `X-Active-Org: <slug>` in all API requests.

### H6: Qdrant Health Check Blocks Event Loop  
**File:** [health.py:40-48](file:///home/kitan/Documents/legal_rag/app/routers/health.py#L40-L48)  
**Problem:** [_check_qdrant()](file:///home/kitan/Documents/legal_rag/app/routers/health.py#40-49) is synchronous but called from an async endpoint. The blocking network call ties up the event loop.  
**Fix:** Wrap in `run_in_executor` or make it async.

---

## Medium (UX & Architecture)

### M1: "Change Password" Shown After Login  
**File:** [frontend/app.py:599-607](file:///home/kitan/Documents/legal_rag/frontend/app.py#L599-L607)  
**Problem:** The "Account Settings → Change Password" expander is always visible in the sidebar after login. Users don't expect to change their password immediately after logging in.  
**Fix:** Remove the always-visible expander. Keep password change only in the recovery flow (`is_recovering`) and add it as a less prominent option (e.g., within a profile/settings page or behind a "Security" accordion at the bottom).

### M2: Upload Gives Instant Success Before Upload Completes  
**File:** [frontend/app.py:815-831](file:///home/kitan/Documents/legal_rag/frontend/app.py#L815-L831)  
**Problem:** `st.button("Upload to Backend")` fires, the spinner runs briefly, but the backend returns 202 (accepted) immediately before the file is actually processed. The user sees "Uploaded successfully!" while the file is still transferring to R2.  
**Fix:**
- Show a progress indication during the `httpx.post` call (Streamlit's `st.spinner` is already there, which is correct)
- After 202, auto-poll `GET /files/{file_id}/status` every 2-3 seconds and show real-time progress until READY
- Replace the file list refresh with an auto-refresh loop

### M3: Available Files List Doesn't Scale  
**File:** [frontend/app.py:836-846](file:///home/kitan/Documents/legal_rag/frontend/app.py#L836-L846)  
**Problem:** All READY files are listed with `st.caption()` for each. With 100+ files this is unusable.  
**Suggestions:**
1. **Search/filter bar** at the top — filter by filename with `st.text_input`  
2. **Pagination** — show 10 files per page with "Previous / Next" buttons (the API already supports [limit](file:///home/kitan/Documents/legal_rag/app/dependencies.py#110-117)/`offset`)  
3. **Replace list with `st.dataframe`** — sortable table with columns: filename, upload date, actions. Supports virtual scrolling.  
4. **Collapse into a dropdown** for session file selection instead of showing all files permanently

### M4: Frontend Stores `org_slug` vs [org_id](file:///home/kitan/Documents/legal_rag/app/dependencies.py#102-108) Inconsistently  
**File:** [frontend/app.py:152](file:///home/kitan/Documents/legal_rag/frontend/app.py#L152)  
**Problem:** `st.session_state.org_id = data.get("org_slug") or data.get("org_id")` — sometimes stores the slug, sometimes the UUID. This causes confusion when the value is used in API calls.  
**Fix:** Store both separately: `session_state.org_id` (UUID) and `session_state.org_slug` (human-readable). Display the slug, send the slug via `X-Active-Org`.

### M5: Deprecated `@app.on_event("startup")`  
**File:** [main.py:55](file:///home/kitan/Documents/legal_rag/main.py#L55)  
**Problem:** FastAPI deprecated `on_event("startup")` in favor of [lifespan context manager](https://fastapi.tiangolo.com/advanced/events/).  
**Fix:** Migrate to `@asynccontextmanager async def lifespan(app)` pattern.

### M6: No Org Switcher in Frontend  
**File:** [frontend/app.py](file:///home/kitan/Documents/legal_rag/frontend/app.py)  
**Problem:** Phase 2 planned an org switcher dropdown but it was never built. Users in multiple orgs have no way to switch.  
**Fix:** Add `st.selectbox` in sidebar populated from `GET /auth/my-orgs`. On change, set `session_state.org_slug` and clear files/sessions.

### M7: [planner.py](file:///home/kitan/Documents/legal_rag/app/services/planner.py) [execute_plan](file:///home/kitan/Documents/legal_rag/app/services/planner.py#101-190) Has Default `org_id="default_org"`  
**File:** [planner.py:101](file:///home/kitan/Documents/legal_rag/app/services/planner.py#L101)  
**Problem:** Default value `"default_org"` could bypass tenant isolation if the caller forgets to pass [org_id](file:///home/kitan/Documents/legal_rag/app/dependencies.py#102-108).  
**Fix:** Remove the default value. Make [org_id](file:///home/kitan/Documents/legal_rag/app/dependencies.py#102-108) a required parameter (no default). `legal_primitives.search_tool` already raises if `org_id == "default_org"`, but the defense should be at the function signature level.

---

## Low (Nice to Have / Cleanup)

### L1: Docker `COPY . /app` Copies Unnecessary Files  
**Files:** [Dockerfile](file:///home/kitan/Documents/legal_rag/Dockerfile) · [Dockerfile.streamlit](file:///home/kitan/Documents/legal_rag/Dockerfile.streamlit)  
**Problem:** `COPY . /app` copies [.env](file:///home/kitan/Documents/legal_rag/.env), `.git`, `venv/`, PDFs, etc. into the image.  
**Fix:** Add [.dockerignore](file:///home/kitan/Documents/legal_rag/.dockerignore) entries for `.env*`, `.git`, `venv/`, `*.pdf`, [__pycache__/](file:///home/kitan/Documents/legal_rag/tests/__pycache__), `logs/`, `results/`.

### L2: PDF Files Committed to Repo  
**File:** Root directory contains [.pdf](file:///home/kitan/Documents/legal_rag/Exhibit%2010.pdf) files (legal contracts)  
**Problem:** Sensitive legal documents in the repo.  
**Fix:** Add `*.pdf` to [.gitignore](file:///home/kitan/Documents/legal_rag/.gitignore), remove from git history with `git filter-branch` or BFG Repo-Cleaner.

### L3: `print()` Statements Throughout  
**Files:** [tasks.py](file:///home/kitan/Documents/legal_rag/app/tasks.py), [planner.py](file:///home/kitan/Documents/legal_rag/app/services/planner.py), [legal_primitives.py](file:///home/kitan/Documents/legal_rag/app/services/legal_primitives.py), [store.py](file:///home/kitan/Documents/legal_rag/app/services/store.py)  
**Problem:** ~50+ `print()` calls mixed with proper `logger` usage. Won't appear in structured logs.  
**Fix:** Replace all `print()` with `logger.info()` / `logger.debug()`.

### L4: `SUPERBASE_KEY` Typo Fallback  
**Files:** [dependencies.py:22](file:///home/kitan/Documents/legal_rag/app/dependencies.py#L22) · [frontend/app.py:20](file:///home/kitan/Documents/legal_rag/frontend/app.py#L20)  
**Problem:** `os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPERBASE_KEY")` — legacy typo still supported.  
**Fix:** Remove the `SUPERBASE_KEY` fallback after confirming no env files use it.

### L5: [object_storage.py](file:///home/kitan/Documents/legal_rag/app/services/object_storage.py) Functions Named `*_gcs`  
**File:** [object_storage.py](file:///home/kitan/Documents/legal_rag/app/services/object_storage.py)  
**Problem:** Functions named [upload_local_file_to_gcs](file:///home/kitan/Documents/legal_rag/app/services/object_storage.py#54-64), [download_file_from_gcs](file:///home/kitan/Documents/legal_rag/app/services/object_storage.py#66-70) etc. but the service is Cloudflare R2.  
**Fix:** Rename to [upload_file](file:///home/kitan/Documents/legal_rag/app/routers/injest.py#153-308), [download_file](file:///home/kitan/Documents/legal_rag/app/services/object_storage.py#66-70), [delete_file](file:///home/kitan/Documents/legal_rag/app/routers/injest.py#422-477) for clarity.

### L6: No Input Validation on [org_id](file:///home/kitan/Documents/legal_rag/app/dependencies.py#102-108) Slug  
**Files:** [auth.py:94](file:///home/kitan/Documents/legal_rag/app/routers/auth.py#L94) · [auth.py:230](file:///home/kitan/Documents/legal_rag/app/routers/auth.py#L230)  
**Problem:** [org_id](file:///home/kitan/Documents/legal_rag/app/dependencies.py#102-108) slug is lowercased and stripped but no regex validation. User could submit special characters, SQL-like strings, or very long slugs.  
**Fix:** Add a regex pattern to `SetupOrgRequest.org_id`: `Field(pattern=r"^[a-z0-9][a-z0-9-]{2,50}$")`.

### L7: `File.content` Stores Full Text in Postgres  
**File:** [models.py:169](file:///home/kitan/Documents/legal_rag/app/models.py#L169)  
**Problem:** For large PDFs, `content` (full extracted text) can be several MB per file. With 100+ files, this bloats the DB.  
**Fix:** Consider storing content in R2 as [.txt](file:///home/kitan/Documents/legal_rag/requirements.txt) alongside the PDF, or lazy-load it only when needed.

---

## Summary of Priority Order

| Priority | Count | Action |
|----------|-------|--------|
| 🔴 Critical | 7 | Must fix before production deploy |
| 🟠 High | 6 | Fix within the sprint |
| 🟡 Medium | 7 | Schedule in backlog |
| ⚪ Low | 7 | Cleanup when convenient |

---

## Verification Plan

### After Critical Fixes
1. Upload 2 files → verify exactly 2 in Available Files (data leak)
2. Admin invites new user → new user clicks magic link → verify auto-join (invite flow)
3. Click "Forgot password" → click reset link → verify new password form appears (password reset)
4. Create 2 orgs, upload to each → verify cross-org isolation in file list AND Qdrant queries
5. User who is MEMBER in one org cannot perform ADMIN actions in that org

### After High/Medium Fixes
6. Login with Supabase OAuth → verify clear "create workspace" message instead of raw 403
7. Multi-org user → verify org switcher works and scopes files correctly
8. Upload 10+ files → verify file list is usable (search/pagination)
