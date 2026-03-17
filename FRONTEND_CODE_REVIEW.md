### Critical Bugs

- **None identified in the Streamlit frontend at this level of review**
  - The `frontend/app.py` file is a single-page Streamlit client that mainly proxies user actions to the secured FastAPI backend. All interactions (auth, file upload, sessions, chat) go through backend APIs that already enforce org isolation and API-key checks. There are no obvious crashes, broken flows, or frontend-side logic errors that would, by themselves, corrupt data or bypass backend security.

---

### Security Risks

- **Unvalidated backend base URL via `API_URL` environment variable** (`frontend/app.py`)
  - The frontend takes `API_URL` from the environment or defaults to `http://localhost:8000`. If this environment variable is misconfigured (e.g. pointing to an attacker-controlled server) the UI would happily send user credentials (email/password) and API keys to that endpoint. This is a standard trust-in-configuration issue rather than a code bug, but it’s important operationally because the frontend does not attempt to validate the origin or use HTTPS by default.
  - **Recommendation**: In deployment, ensure `API_URL` is set to a trusted `https://` origin only and managed via your infrastructure tooling (not user-editable). Optionally, add a simple runtime guard that warns or refuses to run if `API_URL` is not HTTPS in non-local environments.

- **Backend-controlled Markdown rendered in chat window** (`frontend/app.py`)
  - Chat answers from the backend are rendered via `st.chat_message("assistant").markdown(answer)`. Because the backend is your own API, this is acceptable, but it means any future bug that allows user-controlled content to be reflected directly in the answer body could manifest as untrusted Markdown/HTML in the UI. Streamlit generally sanitizes unsafe HTML unless explicitly allowed, so the risk is low, but it’s worth being aware of this attack surface.
  - **Recommendation**: Keep the backend answer strictly text/Markdown without arbitrary HTML, and avoid ever mixing raw, user-submitted HTML into the `answer` field. If you later enable richer rendering, audit that path specifically.

---

### Clean Code Suggestions and System Design

- **Synchronous HTTP calls in the main UI thread** (`frontend/app.py`)
  - All interactions (login/signup, file uploads, session creation, chat, etc.) use `httpx` calls directly in the Streamlit callbacks. For long-running requests (uploads, agentic queries), this can block the UI for the duration of the call, although Streamlit’s spinners mitigate the UX somewhat.
  - **Recommendation**: This is acceptable for a lightweight internal tool. If responsiveness becomes an issue, consider: (1) tightening/standardizing timeouts on all HTTP calls (login/signup currently have no explicit timeout), and (2) for very long operations, polling dedicated status endpoints from short, bounded requests rather than waiting on a single long call.

- **Chat mode parameter slightly out of sync with backend semantics** (`frontend/app.py`)
  - For non-agentic mode, the frontend passes `mode="fast"` to `/ask`, but the backend supports `mode` values `"hybrid"`, `"concept"`, and `"multiquery"`. Because the backend treats unknown modes as the default hybrid path, this does not break functionality, but it does make the code less self-explanatory and could cause confusion when evolving retrieval strategies.
  - **Recommendation**: Align the frontend with the backend by sending a supported mode string (e.g. `"hybrid"` vs `"concept"` / `"multiquery"`), or introduce an explicit `"fast"` mode on the backend if you want a distinct behavior. This keeps the contract between frontend and backend precise.

- **Limited error surface for auth flows (no differentiation between network vs credential errors)** (`frontend/app.py`)
  - Login and signup both catch `Exception` and display a generic `Error: {e}` when network or server errors occur, while also using the same UI messaging for incorrect credentials and backend failures. This is functionally fine but can confuse users when the root cause is connectivity vs. authentication.
  - **Recommendation**: Consider distinguishing 4xx credential issues from network/timeouts (which you already do in `refresh_files`) and showing clearer messages (e.g., “Invalid email or password” vs. “Cannot reach server, please try again later”).

- **Stateful session management is tightly coupled to a single `app.py` file** (`frontend/app.py`)
  - The file handles auth, file management, session lifecycle, and chat UX all in one module. This is manageable at current size but can become hard to evolve as features grow (e.g., separate pages for file management vs. chat, admin views, etc.).
  - **Recommendation**: If the frontend grows, consider refactoring into multiple pages/components (Streamlit pages or submodules) and centralizing API client logic (a small `backend_client` helper) so that API error handling, headers, and base URL logic are reused consistently.

