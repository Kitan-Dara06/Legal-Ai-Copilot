import logging
import os
import ssl
import time
from pathlib import Path

import httpx
import sentry_sdk
import streamlit as st
from dotenv import load_dotenv
from sentry_sdk.integrations.logging import LoggingIntegration
from supabase import ClientOptions, create_client

# Load .env from project root
_env = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env)

API_URL = os.getenv("API_URL", "http://localhost:8000")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "")

_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
        ],
        send_default_pii=False,
    )

st.set_page_config(
    page_title="Legal AI Copilot",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for aesthetics
st.markdown(
    """
<style>
    .stChatFloatingInputContainer {
        padding-bottom: 2rem;
    }
    .main-header {
        font-family: 'Inter', sans-serif;
        color: #1E3A8A;
        font-weight: 700;
        margin-bottom: 0px;
    }
    .sub-header {
        font-family: 'Inter', sans-serif;
        color: #6B7280;
        margin-top: 0px;
        margin-bottom: 2rem;
    }
</style>
""",
    unsafe_allow_html=True,
)

# Initialization
if "access_token" not in st.session_state:
    st.session_state.access_token = None
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "org_id" not in st.session_state:
    st.session_state.org_id = None
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "available_files" not in st.session_state:
    st.session_state.available_files = []
if "is_recovering" not in st.session_state:
    st.session_state.is_recovering = False
if "setup_required" not in st.session_state:
    st.session_state.setup_required = False

# Legacy: API key still works for get_headers if no Supabase token
if "api_key" not in st.session_state:
    st.session_state.api_key = None
# App role from backend (ADMIN can invite members)
if "app_role" not in st.session_state:
    st.session_state.app_role = None
if "org_slug" not in st.session_state:
    st.session_state.org_slug = None
if "org_name" not in st.session_state:
    st.session_state.org_name = None
if "file_search_query" not in st.session_state:
    st.session_state.file_search_query = ""
if "file_page" not in st.session_state:
    st.session_state.file_page = 0


def get_headers():
    headers = {}
    if st.session_state.access_token:
        headers["Authorization"] = f"Bearer {st.session_state.access_token}"
    elif st.session_state.api_key:
        headers["X-API-Key"] = st.session_state.api_key
    # Always include X-Active-Org so the backend resolves the org consistently
    if st.session_state.get("org_slug"):
        headers["X-Active-Org"] = st.session_state.org_slug
    return headers


# Longer timeouts for Supabase (avoids SSL handshake / read timeout on slow networks)
# Single value => applies to connect/read/write/pool.
_SUPABASE_HTTP_TIMEOUT = httpx.Timeout(60.0)


def _get_supabase_client():
    """Supabase client with longer timeouts for auth (connect 30s, read 60s)."""
    http_client = httpx.Client(timeout=_SUPABASE_HTTP_TIMEOUT)
    options = ClientOptions(httpx_client=http_client)
    return create_client(SUPABASE_URL, SUPABASE_KEY, options=options)


def _handle_supabase_connection_error(e: Exception) -> str:
    """Turn connection/timeout/SSL errors into a clear message."""
    if isinstance(e, httpx.HTTPStatusError):
        try:
            err_data = e.response.json()
            msg = (
                err_data.get("error_description")
                or err_data.get("msg")
                or err_data.get("message")
            )
            if msg:
                return f"Supabase error: {msg}"
        except Exception:
            pass
        return f"HTTP error {e.response.status_code}: {e.response.text}"
    if isinstance(e, (httpx.TimeoutException, ssl.SSLError, OSError, ConnectionError)):
        return (
            "Connection to Supabase timed out or failed. "
            "Check SUPABASE_URL, your network, and firewall/VPN. "
            "If you are on a slow connection, try again."
        )
    return str(e)


def _supabase_login(email: str, password: str):
    try:
        client = _get_supabase_client()
        resp = client.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as e:
        raise ValueError(_handle_supabase_connection_error(e))
    if not resp.session or not resp.session.access_token:
        raise ValueError("No session returned")
    st.session_state.access_token = resp.session.access_token
    st.session_state.user_email = resp.user.email if resp.user else email
    # Backend maps Supabase user to org; fetch org_id from /auth/me
    try:
        me = httpx.get(
            f"{API_URL}/auth/me",
            headers={"Authorization": f"Bearer {st.session_state.access_token}"},
            timeout=10.0,
        )
        if me.status_code == 200:
            data = me.json()
            st.session_state.org_id = data.get("org_id")
            st.session_state.org_slug = data.get("org_slug") or data.get("org_id")
            st.session_state.org_name = data.get("org_name")
            st.session_state.app_role = data.get("app_role")
            st.session_state.setup_required = False
        elif me.status_code == 403:
            err = {}
            try:
                err = me.json().get("detail", {})
            except Exception:
                err = {}
            if isinstance(err, dict) and err.get("code") in (
                "setup_required",
                "invite_required",
            ):
                st.session_state.setup_required = True
                st.session_state.app_role = None
                st.session_state.org_id = None
    except Exception:
        pass


def _supabase_signup(email: str, password: str):
    try:
        client = _get_supabase_client()
        resp = client.auth.sign_up({"email": email, "password": password})
    except Exception as e:
        raise ValueError(_handle_supabase_connection_error(e))
    if resp.session and resp.session.access_token:
        st.session_state.access_token = resp.session.access_token
        st.session_state.user_email = resp.user.email if resp.user else email
        try:
            me = httpx.get(
                f"{API_URL}/auth/me",
                headers={"Authorization": f"Bearer {st.session_state.access_token}"},
                timeout=10.0,
            )
            if me.status_code == 200:
                data = me.json()
                st.session_state.org_id = data.get("org_id")
                st.session_state.org_slug = data.get("org_slug") or data.get("org_id")
                st.session_state.org_name = data.get("org_name")
                st.session_state.app_role = data.get("app_role")
                st.session_state.setup_required = False
            elif me.status_code == 403:
                err = {}
                try:
                    err = me.json().get("detail", {})
                except Exception:
                    err = {}
                if isinstance(err, dict) and err.get("code") in (
                    "setup_required",
                    "invite_required",
                ):
                    st.session_state.setup_required = True
                    st.session_state.app_role = None
                    st.session_state.org_id = None
        except Exception:
            pass
    else:
        # Email confirmation may be required
        raise ValueError(
            "Signup created. If email confirmation is enabled, check your inbox; otherwise, log in now."
        )


def _supabase_send_reset_email(email: str):
    """
    Send a Supabase password reset email.
    Requires FRONTEND_URL to be set in env — raises a clear error if missing
    so we never silently send a link pointing at localhost.
    """
    if not email:
        raise ValueError(
            "Please enter your email address to receive a password reset link."
        )
    redirect_to = os.getenv("FRONTEND_URL", "").strip().rstrip("/")
    if not redirect_to:
        raise ValueError(
            "Password reset is not configured on this server. "
            "Ask your administrator to set the FRONTEND_URL environment variable."
        )
    try:
        url = f"{SUPABASE_URL}/auth/v1/recover"
        headers = {
            "apikey": SUPABASE_KEY,
            "Content-Type": "application/json",
        }
        resp = httpx.post(
            url,
            headers=headers,
            json={"email": email},
            params={"redirect_to": redirect_to},
            timeout=10.0,
        )
        resp.raise_for_status()
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(_handle_supabase_connection_error(e))


def _supabase_update_password(new_password: str):
    """
    Update the current logged-in user's password.
    This requires the user to be signed in (via recovery link or existing session).
    """
    if not new_password or len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    if not st.session_state.access_token:
        raise ValueError(
            "No valid session found to update password. Please log in or use the recovery link."
        )

    try:
        url = f"{SUPABASE_URL}/auth/v1/user"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {st.session_state.access_token}",
            "Content-Type": "application/json",
        }
        resp = httpx.put(
            url, headers=headers, json={"password": new_password}, timeout=10.0
        )
        resp.raise_for_status()
    except Exception as e:
        raise ValueError(_handle_supabase_connection_error(e))


def is_logged_in():
    return bool(st.session_state.access_token or st.session_state.api_key)


# Handle Supabase redirect tokens (e.g. when user clicks recovery link).
# Supabase may return an access_token as a query param. If present, apply it
# to the session_state and attempt to fetch profile information so the UI
# can continue the recovery flow instead of showing "Auth session missing".
try:
    if st.query_params.get("type") == "recovery":
        st.session_state.is_recovering = True
        try:
            if "type" in st.query_params:
                del st.query_params["type"]
        except Exception:
            pass

    token = st.query_params.get("access_token") or st.query_params.get("token")
    if token:
        # Set the token into session state so subsequent calls use it.
        st.session_state.access_token = token
        # Remove query params from URL to avoid leaking token in UI.
        try:
            if "access_token" in st.query_params:
                del st.query_params["access_token"]
            if "token" in st.query_params:
                del st.query_params["token"]
        except Exception:
            # Some Streamlit versions may not allow clearing parameters; ignore.
            pass
        # Try to fetch /auth/me to load org/profile (best-effort).
        try:
            me = httpx.get(
                f"{API_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if me.status_code == 200:
                data = me.json()
                st.session_state.user_email = (
                    data.get("email") or st.session_state.user_email
                )
                st.session_state.org_id = data.get("org_id") or st.session_state.org_id
                st.session_state.app_role = (
                    data.get("app_role") or st.session_state.app_role
                )
                # Re-run so the UI reflects logged-in state immediately.
                st.rerun()
        except Exception:
            # If validation fails, leave the token in session_state; user can paste token manually.
            pass
except Exception:
    # Defensive: don't let query-param parsing break the login flow.
    pass


if not is_logged_in():
    # Supabase appends access_token in the URL hash for implicit flow (e.g., password recovery).
    # Streamlit cannot read the hash server-side. This JS auto-redirects the hash to a query parameter.
    import streamlit.components.v1 as components
    components.html(
        """
        <script>
        if (window.parent.location.hash.includes("access_token=")) {
            const hash = window.parent.location.hash.substring(1);
            window.parent.location.href = window.parent.location.pathname + "?" + hash;
        }
        </script>
        """,
        height=0,
    )

    st.markdown(
        "<h1 class='main-header'>⚖️ Legal AI Copilot - Login</h1>",
        unsafe_allow_html=True,
    )
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error("Supabase is not configured (SUPABASE_URL / SUPABASE_ANON_KEY).")
        st.stop()

    # If they landed here from an invite magic link, the URL might be just the app root.
    st.info(
        "**Invited by email?** Use the **same email** we sent the invite to: **Sign up** to set a password, or **Log in** if you already have one. You’ll then be added to the organization."
    )

    tab1, tab2 = st.tabs(["Login", "Sign Up"])

    with tab1:
        st.subheader("Login with Supabase")
        login_email = st.text_input("Email", key="login_email")
        login_password = st.text_input(
            "Password", type="password", key="login_password"
        )
        if st.button("Log In"):
            try:
                _supabase_login(login_email, login_password)
                st.rerun()
            except Exception as e:
                st.error(str(e))

        # Improved Forgot password UX
        with st.expander("Forgot password?", expanded=False):
            st.subheader("Send reset email")
            forgot_email = st.text_input(
                "Email for password reset",
                key="forgot_email",
                help="Enter the email for your account.",
            )
            if st.button("Send reset email"):
                try:
                    _supabase_send_reset_email(forgot_email)
                    st.success(
                        "Password reset email sent. Check your inbox (and spam)."
                    )
                except Exception as e:
                    st.error(str(e))

    with tab2:
        st.subheader("Sign Up (Supabase)")
        st.caption(
            "Create your Supabase account and choose a unique Organisation ID for your workspace."
        )
        signup_email = st.text_input("Email", key="signup_email")
        signup_password = st.text_input(
            "Password", type="password", key="signup_password"
        )
        signup_org_id = st.text_input(
            "Organisation ID",
            key="signup_org_id",
            placeholder="e.g. acme-legal or my-firm-2025",
            help="A unique, lowercase identifier for your workspace. You cannot change this later.",
        )
        signup_org_name = st.text_input(
            "Organisation Name",
            key="signup_org_name",
            placeholder="e.g. Acme Legal LLP",
            help="A human-readable display name for your organisation.",
        )
        if st.button("Sign Up"):
            org_id_clean = (signup_org_id or "").strip().lower()
            if not org_id_clean:
                st.error("Please choose an Organisation ID for your workspace.")
            else:
                try:
                    _supabase_signup(signup_email, signup_password)
                    # Register the org on the backend using setup-org with Bearer auth
                    org_name_clean = (signup_org_name or "").strip()
                    payload = {"org_id": org_id_clean}
                    if org_name_clean:
                        payload["org_name"] = org_name_clean
                    reg = httpx.post(
                        f"{API_URL}/auth/setup-org",
                        json=payload,
                        headers=get_headers(),
                        timeout=15.0,
                    )
                    if reg.status_code in (200, 201):
                        data = (
                            reg.json()
                            if reg.headers.get("content-type", "").startswith(
                                "application/json"
                            )
                            else {}
                        )
                        st.session_state.org_id = data.get("org_id", org_id_clean)
                        st.session_state.org_slug = org_id_clean
                        st.session_state.org_name = org_name_clean or org_id_clean
                        st.session_state.setup_required = False
                        st.success(
                            f"Organisation **{org_id_clean}** created! You're all set."
                        )
                    else:
                        err = (
                            reg.json().get("detail", reg.text)
                            if reg.headers.get("content-type", "").startswith(
                                "application/json"
                            )
                            else reg.text
                        )
                        st.warning(
                            f"Supabase account created, but org setup failed: {err}"
                        )
                    st.rerun()
                except Exception as e:
                    err_msg = str(e)
                    if "Sign-up successful" in err_msg:
                        st.info(err_msg)
                    else:
                        st.error(err_msg)

    if st.session_state.get("setup_required") and st.session_state.get("access_token"):
        st.markdown("---")
        st.subheader("Choose your workspace ID")
        st.caption(
            "Your account is authenticated, but no workspace is linked yet. Create one now."
        )
        setup_org_id = st.text_input(
            "Workspace ID",
            key="setup_required_org_id",
            placeholder="e.g. acme-legal",
        )
        if st.button("Create workspace", key="create_workspace_setup_required"):
            setup_org_id_clean = (setup_org_id or "").strip().lower()
            if not setup_org_id_clean:
                st.error("Please enter a workspace ID.")
            else:
                try:
                    res = httpx.post(
                        f"{API_URL}/auth/setup-org",
                        json={"org_id": setup_org_id_clean},
                        headers=get_headers(),
                        timeout=15.0,
                    )
                    if res.status_code in (200, 201):
                        data = (
                            res.json()
                            if res.headers.get("content-type", "").startswith(
                                "application/json"
                            )
                            else {}
                        )
                        st.session_state.org_id = data.get("org_id", setup_org_id_clean)
                        st.session_state.setup_required = False
                        # Refresh profile to populate role/org from backend
                        me = httpx.get(
                            f"{API_URL}/auth/me",
                            headers=get_headers(),
                            timeout=10.0,
                        )
                        if me.status_code == 200:
                            m = me.json()
                            st.session_state.org_id = (
                                m.get("org_id") or st.session_state.org_id
                            )
                            st.session_state.app_role = (
                                m.get("app_role") or st.session_state.app_role
                            )
                        st.success("Workspace created successfully.")
                        st.rerun()
                    else:
                        err = (
                            res.json().get("detail", res.text)
                            if res.headers.get("content-type", "").startswith(
                                "application/json"
                            )
                            else res.text
                        )
                        st.error(f"Workspace setup failed: {err}")
                except Exception as e:
                    st.error(str(e))

    st.stop()


if st.session_state.get("is_recovering"):
    st.info("Password Recovery: Please set your new password.")
    new_pw = st.text_input("New Password", type="password", key="recovery_new_pw")
    if st.button("Update Password", key="recovery_btn"):
        try:
            _supabase_update_password(new_pw)
            st.success("Password updated successfully!")
            st.session_state.is_recovering = False
            time.sleep(1)
            st.rerun()
        except Exception as e:
            st.error(str(e))
    st.markdown("---")

# Accept invite (when logged in and URL has ?invite_token=...)
invite_token = st.query_params.get("invite_token")
if invite_token and is_logged_in():
    try:
        info_res = httpx.get(
            f"{API_URL}/auth/invite-info",
            params={"token": invite_token},
            timeout=10.0,
        )
        if info_res.status_code == 200:
            st.info(
                "You've been invited to join an organization. Accept to switch to it."
            )
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Accept invite", key="accept_invite_btn"):
                    res = httpx.post(
                        f"{API_URL}/auth/accept-invite-by-token",
                        json={"token": invite_token},
                        headers=get_headers(),
                        timeout=10.0,
                    )
                    if res.status_code == 200:
                        data = res.json()
                        st.session_state.org_id = data.get("org_id")
                        st.success(
                            data.get("message", "You have joined the organization.")
                        )
                        if "invite_token" in st.query_params:
                            del st.query_params["invite_token"]
                        st.rerun()
                    else:
                        err = (
                            res.json().get("detail", res.text)
                            if res.headers.get("content-type", "").startswith(
                                "application/json"
                            )
                            else res.text
                        )
                        st.error(err)
            with col2:
                if st.button("Decline", key="decline_invite_btn"):
                    if "invite_token" in st.query_params:
                        del st.query_params["invite_token"]
                    st.rerun()
    except Exception as e:
        st.warning(f"Could not load invite: {e}")


def refresh_files():
    try:
        res = httpx.get(f"{API_URL}/files/list", headers=get_headers(), timeout=10.0)
        if res.status_code == 200:
            st.session_state.available_files = res.json().get("files", [])
        else:
            st.session_state.available_files = []
    except httpx.ConnectError:
        st.warning("⚠️ Cannot reach the backend. Is the FastAPI server running?")
    except httpx.TimeoutException:
        st.warning("⏳ Backend is slow to respond. Try refreshing in a moment.")
    except Exception:
        st.warning("⚠️ Could not load files. Check that the backend is online.")


# Sidebar: File Management
with st.sidebar:
    # Account settings removed — use "Forgot Password" on login page instead

    # Backfill org_id and app_role from /auth/me when we have a token but missing either
    if st.session_state.access_token and (
        st.session_state.app_role is None or st.session_state.org_id is None
    ):
        try:
            me = httpx.get(
                f"{API_URL}/auth/me",
                headers=get_headers(),
                timeout=10.0,
            )
            if me.status_code == 200:
                data = me.json()
                st.session_state.org_id = (
                    st.session_state.org_id
                    or data.get("org_slug")
                    or data.get("org_id")
                )
                st.session_state.app_role = st.session_state.app_role or data.get(
                    "app_role"
                )
                st.session_state.setup_required = False
                if st.session_state.org_id or st.session_state.app_role:
                    st.rerun()
            elif me.status_code == 403:
                err = {}
                try:
                    err = me.json().get("detail", {})
                except Exception:
                    err = {}
                if isinstance(err, dict) and err.get("code") in (
                    "setup_required",
                    "invite_required",
                ):
                    st.session_state.setup_required = True
                    st.session_state.org_id = None
                    st.session_state.app_role = None
                    st.rerun()
        except Exception:
            pass

    st.header("📂 Document Library")

    if st.session_state.user_email:
        st.markdown(f"**Logged in:** `{st.session_state.user_email}`")

    # Always show Org and Role when logged in (show "—" if not loaded yet)
    if st.session_state.access_token or st.session_state.user_email:
        org_display = st.session_state.org_name or st.session_state.org_slug or st.session_state.org_id or "—"
        role_display = st.session_state.app_role if st.session_state.app_role else "—"
        st.markdown(f"**Org:** {org_display}")
        if st.session_state.org_slug and st.session_state.org_slug != org_display:
            st.caption(f"Slug: `{st.session_state.org_slug}`")
        st.caption(f"**Role:** {role_display}")
        if not st.session_state.org_id or not st.session_state.app_role:
            if st.button(
                "Refresh profile",
                key="refresh_profile",
                help="Load org and role from backend",
            ):
                try:
                    me = httpx.get(
                        f"{API_URL}/auth/me", headers=get_headers(), timeout=10.0
                    )
                    if me.status_code == 200:
                        data = me.json()
                        st.session_state.org_id = (
                            data.get("org_slug")
                            or data.get("org_id")
                            or st.session_state.org_id
                        )
                        st.session_state.app_role = (
                            data.get("app_role") or st.session_state.app_role
                        )
                        st.session_state.setup_required = False
                        st.success("Profile loaded.")
                        st.rerun()
                    elif me.status_code == 403:
                        err = {}
                        try:
                            err = me.json().get("detail", {})
                        except Exception:
                            err = {}
                        if isinstance(err, dict) and err.get("code") in (
                            "setup_required",
                            "invite_required",
                        ):
                            st.session_state.setup_required = True
                            st.session_state.org_id = None
                            st.session_state.app_role = None
                            st.info(
                                "You're signed in but need to create a workspace. Use the form below."
                            )
                        else:
                            st.error(
                                f"Forbidden (403). Is your Supabase session still valid?"
                            )
                    else:
                        st.error(
                            f"Backend returned {me.status_code}. Is the API at {API_URL} running?"
                        )
                except Exception as e:
                    st.error(f"Cannot reach backend: {e}")

        if st.session_state.setup_required and st.session_state.access_token:
            st.warning(
                "Authenticated but no workspace exists yet. Create one to continue."
            )
            setup_org_id = st.text_input(
                "Workspace ID",
                key="setup_required_org_id_logged_in",
                placeholder="e.g. acme-legal",
            )
            if st.button("Create workspace", key="create_workspace_logged_in"):
                setup_org_id_clean = (setup_org_id or "").strip().lower()
                if not setup_org_id_clean:
                    st.error("Please enter a workspace ID.")
                else:
                    try:
                        res = httpx.post(
                            f"{API_URL}/auth/setup-org",
                            json={"org_id": setup_org_id_clean},
                            headers=get_headers(),
                            timeout=15.0,
                        )
                        if res.status_code in (200, 201):
                            data = (
                                res.json()
                                if res.headers.get("content-type", "").startswith(
                                    "application/json"
                                )
                                else {}
                            )
                            st.session_state.org_id = data.get(
                                "org_id", setup_org_id_clean
                            )
                            st.session_state.app_role = data.get("app_role", "ADMIN")
                            st.session_state.setup_required = False
                            st.success("Workspace created successfully.")
                            st.rerun()
                        else:
                            err = (
                                res.json().get("detail", res.text)
                                if res.headers.get("content-type", "").startswith(
                                    "application/json"
                                )
                                else res.text
                            )
                            st.error(f"Workspace setup failed: {err}")
                    except Exception as e:
                        st.error(str(e))

    # Admin: invite members (Supabase magic link email)
    if st.session_state.app_role == "ADMIN":
        st.divider()
        st.subheader("👥 Invite member")
        with st.form("invite_member_form", clear_on_submit=True):
            invite_email = st.text_input(
                "Email address",
                key="invite_email",
                placeholder="teammate@example.com",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button("Send invite")
        if submitted:
            email = (invite_email or "").strip()
            if not email:
                st.warning("Enter an email address.")
            else:
                try:
                    res = httpx.post(
                        f"{API_URL}/auth/invite-by-email",
                        json={"email": email},
                        headers=get_headers(),
                        timeout=15.0,
                    )
                    if res.status_code == 200:
                        data = res.json()
                        st.success(data.get("message", "Invite sent."))
                        if data.get("already_registered") and data.get("invite_link"):
                            st.caption("Share this link with them:")
                            st.code(data["invite_link"], language=None)
                    else:
                        try:
                            err = res.json().get("detail", res.text)
                        except Exception:
                            err = res.text
                        if isinstance(err, list):
                            err = "; ".join(str(x) for x in err)
                        st.error(str(err))
                except Exception as e:
                    st.error(str(e))

    if st.button("🚪 Logout", use_container_width=True):
        st.session_state.access_token = None
        st.session_state.user_email = None
        st.session_state.app_role = None
        st.session_state.api_key = None
        st.session_state.org_id = None
        st.session_state.session_id = None
        st.session_state.messages = []
        st.rerun()

    st.divider()

    # Upload Section
    uploaded_files = st.file_uploader(
        "Upload New Contracts", type=["pdf"], accept_multiple_files=True
    )
    if st.button("Upload to Backend", use_container_width=True) and uploaded_files:
        with st.spinner("Uploading & Processing..."):
            files_payload = [
                ("files", (f.name, f.read(), "application/pdf")) for f in uploaded_files
            ]
            try:
                res = httpx.post(
                    f"{API_URL}/files/upload",
                    files=files_payload,
                    headers=get_headers(),
                    timeout=120.0,
                )
                if res.status_code == 202:
                    st.success("Uploaded successfully! Processing in background...")
                    time.sleep(1)  # wait a moment for initial DB flush
            except Exception as e:
                st.error(f"Upload failed: {e}")

    st.divider()

    # File View Section with Search & Pagination
    st.subheader("Available Files")
    if st.button("🔄 Refresh List", use_container_width=True):
        refresh_files()

    if not st.session_state.available_files:
        st.info("No formatted documents available. Upload one to begin.")
    else:
        # Search/filter bar
        search_q = st.text_input(
            "🔍 Filter files",
            value=st.session_state.file_search_query,
            placeholder="Type to filter by filename...",
            key="file_search_input",
            label_visibility="collapsed",
        )
        st.session_state.file_search_query = search_q

        filtered = [
            f for f in st.session_state.available_files
            if search_q.lower() in f["filename"].lower()
        ] if search_q else st.session_state.available_files

        # Pagination
        PAGE_SIZE = 10
        total_files = len(filtered)
        total_pages = max(1, (total_files + PAGE_SIZE - 1) // PAGE_SIZE)
        # Clamp page
        if st.session_state.file_page >= total_pages:
            st.session_state.file_page = total_pages - 1
        if st.session_state.file_page < 0:
            st.session_state.file_page = 0

        start_idx = st.session_state.file_page * PAGE_SIZE
        page_files = filtered[start_idx : start_idx + PAGE_SIZE]

        st.caption(f"Showing {start_idx + 1}–{min(start_idx + len(page_files), total_files)} of {total_files} files")

        for f in page_files:
            file_id = f["file_id"]
            fname = f["filename"]
            st.caption(f"📄 {fname} (ID: {file_id})")

        # Pagination controls
        if total_pages > 1:
            col_prev, col_info, col_next = st.columns([1, 2, 1])
            with col_prev:
                if st.button("◀ Prev", disabled=(st.session_state.file_page == 0), key="file_page_prev"):
                    st.session_state.file_page -= 1
                    st.rerun()
            with col_info:
                st.caption(f"Page {st.session_state.file_page + 1} / {total_pages}")
            with col_next:
                if st.button("Next ▶", disabled=(st.session_state.file_page >= total_pages - 1), key="file_page_next"):
                    st.session_state.file_page += 1
                    st.rerun()

    st.divider()
    if st.session_state.session_id:
        st.success(f"🟢 Active Session:\n\n`{st.session_state.session_id[:8]}...`")

        # Display files currently inside the active session
        try:
            res = httpx.get(
                f"{API_URL}/session/{st.session_state.session_id}",
                headers=get_headers(),
                timeout=10.0,
            )
            if res.status_code == 200:
                session_data = res.json()
                session_files = session_data.get("files", [])
                st.markdown("**Files in Context:**")
                for f in session_files:
                    status_emoji = "⏳" if f["status"] == "PROCESSING" else "✅"
                    fname_display = f.get("filename", "Unknown File")
                    col_name, col_btn = st.columns([4, 1])
                    with col_name:
                        st.caption(f"{status_emoji} {fname_display}")
                    with col_btn:
                        if st.button(
                            "❌",
                            key=f"remove_{f['file_id']}",
                            help=f"Remove {fname_display} from session",
                        ):
                            try:
                                r = httpx.delete(
                                    f"{API_URL}/session/{st.session_state.session_id}/files/{f['file_id']}",
                                    headers=get_headers(),
                                    timeout=10.0,
                                )
                                if r.status_code == 200:
                                    st.toast(
                                        f"✅ Removed {fname_display} from session."
                                    )
                                    st.rerun()
                                else:
                                    st.error(
                                        f"Remove failed ({r.status_code}): {r.json().get('detail', r.text)}"
                                    )
                            except Exception as e:
                                st.error(f"Could not remove file: {e}")
        except Exception:
            st.error("Could not load session files.")

        st.divider()

        st.markdown("**Add Document to Session**")
        session_upload = st.file_uploader(
            "Upload directly to active chat", type=["pdf"], key="session_uploader"
        )
        if st.button("Upload to Session", use_container_width=True) and session_upload:
            with st.spinner("Processing & embedding into session..."):
                files_payload = {
                    "file": (
                        session_upload.name,
                        session_upload.read(),
                        "application/pdf",
                    )
                }
                try:
                    res = httpx.post(
                        f"{API_URL}/session/{st.session_state.session_id}/upload",
                        files=files_payload,
                        headers=get_headers(),
                        timeout=120.0,
                    )
                    if res.status_code in [200, 202]:
                        st.success("File added to active session!")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"Failed to upload: {res.text}")
                except Exception as e:
                    st.error(f"Upload failed: {e}")

        st.divider()
        if st.button("🛑 Terminate Session", type="primary", use_container_width=True):
            try:
                httpx.delete(
                    f"{API_URL}/session/{st.session_state.session_id}",
                    headers=get_headers(),
                )
            except Exception:
                pass
            st.session_state.session_id = None
            st.session_state.messages = []
            st.rerun()

# Main Interface
st.markdown("<h1 class='main-header'>⚖️ Legal AI Copilot</h1>", unsafe_allow_html=True)
st.markdown(
    "<p class='sub-header'>Chat seamlessly with your securely embedded corporate contracts.</p>",
    unsafe_allow_html=True,
)

# State 1: No active session
if not st.session_state.session_id:
    st.info("Initialize a secure workspace session to begin chatting.")

    if st.session_state.available_files:
        file_options = {
            f["file_id"]: f["filename"] for f in st.session_state.available_files
        }
        selected_file_ids = st.multiselect(
            "Select documents to include in this session's context:",
            options=list(file_options.keys()),
            format_func=lambda x: file_options[x],
        )

        if st.button("🚀 Create Workspace", type="primary") and selected_file_ids:
            with st.spinner("Initializing Workspace..."):
                try:
                    res = httpx.post(
                        f"{API_URL}/session/",
                        json=selected_file_ids,
                        headers=get_headers(),
                        timeout=120.0,
                    )
                    if res.status_code in [200, 201]:
                        st.session_state.session_id = res.json().get("session_id")
                        st.session_state.messages = [
                            {
                                "role": "assistant",
                                "content": "I'm ready. I have fully indexed the selected contracts. What would you like to know?",
                            }
                        ]
                        st.rerun()
                    else:
                        st.error(f"Failed to create session: {res.text}")
                except Exception as e:
                    st.error(f"API Error: {e}")

# State 2: Active Session
else:
    # Top Bar: Inference Mode Switcher
    col1, col2 = st.columns([1, 4])
    with col1:
        use_agentic = st.toggle(
            "🤖 Agentic Tool Router",
            value=False,
            help="Enable multi-step planning and tool usage. Slower but better for complex logic.",
        )
    with col2:
        st.write("")  # spacing

    # Chat History
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat Input
    if prompt := st.chat_input("Ask a question about your contracts..."):
        # Append user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            message_placeholder = st.empty()

            # Formulate request
            endpoint = "/ask-agent" if use_agentic else "/ask"
            q_params = {
                "session_id": st.session_state.session_id,
                "question": prompt,
                "mode": "hybrid" if use_agentic else "fast",
            }

            try:
                with st.spinner(
                    "Analyzing..." if not use_agentic else "Agent Planning..."
                ):
                    res = httpx.post(
                        f"{API_URL}{endpoint}",
                        params=q_params,
                        headers=get_headers(),
                        timeout=60.0,
                    )
                    res.raise_for_status()

                    # Handle varying response structures
                    if use_agentic:
                        answer = res.json().get("answer", "No answer provided")
                    else:
                        answer = res.json().get("answer", "No answer provided")

                    message_placeholder.markdown(answer)
                    st.session_state.messages.append(
                        {"role": "assistant", "content": answer}
                    )

            except Exception as e:
                st.error(f"Inference error: {e}")
