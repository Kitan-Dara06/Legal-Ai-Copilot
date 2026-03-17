### Critical Bugs

- **None clearly identified as hard failures**
  - At this level of review, there are no definite logic errors that would consistently crash the app or corrupt data under normal conditions. Most flows are guarded reasonably with checks and exceptions. However, several configuration- and design-related risks (especially around infrastructure and security) could surface as runtime failures if environment variables or external services (Redis, Qdrant, DB, GCS) are misconfigured. These are captured as security and robustness issues below rather than as deterministic, always-on crashes.

---

### Security Risks

- **Unauthenticated session management endpoints allow session hijacking / information disclosure** (`app/routers/session.py`)
  - `create_new_session` and `upload_into_session` are protected via `org_id: str = Depends(get_org_id)`, but `get_session_info`, `remove_file_from_session`, `renew_session`, and `close_session` have no auth dependency. Any caller who can guess or obtain a valid `session_id` can read which files are in the session (including filenames), remove files from a session, or close/renew it, regardless of organization or user. This undermines the org isolation and API-key auth model, and if session IDs are logged or leaked, it becomes trivial to interfere with other users’ sessions.
  - **Recommendation**: Add `org_id: str = Depends(get_org_id)` (or the full `AuthContext`) to all session endpoints. On every operation, verify `session["org_id"] == org_id` and return 403 on mismatch so sessions cannot be read or mutated across organizations.

- **Redis connections disable TLS certificate verification (`ssl_cert_reqs=none`)** (`app/redis_client.py`, `app/worker.py`, `app/tasks.py`)
  - Redis URLs are built with `?ssl_cert_reqs=none` and `redis_backend_use_ssl={"ssl_cert_reqs": "none"}`, and the sync Redis client uses `rediss://...ssl_cert_reqs=none`. This explicitly disables TLS certificate validation, making connections vulnerable to man-in-the-middle attacks if traffic ever leaves a trusted internal network or is misrouted. Because Redis is used for sessions and progress tracking, an attacker with network position could tamper with session contents or falsify processing status.
  - **Recommendation**: Require proper TLS verification (remove `ssl_cert_reqs=none` and configure CA bundles), and fail fast if certificates cannot be validated. If a “no-verify” mode is needed for local-only development, gate it behind an explicit, clearly named debug flag and ensure it cannot be enabled in production.

- **Weak / hard-coded default database credentials and non-SSL DB connections** (`app/database.py`, `app/tasks.py`)
  - Both async and sync DB layers default to URLs like `postgresql+asyncpg://postgres:password@localhost:5432/legal_rag` and `postgresql://postgres:password@localhost:5432/legal_rag` when environment variables are missing. If these defaults ever leak into a non-local environment or are copied as-is, they represent trivial credentials to guess and do not enforce SSL. There is also no explicit `sslmode=require` or equivalent, so even remote connections may be unencrypted.
  - **Recommendation**: Remove or strictly scope insecure defaults to a clearly marked local/dev environment. Require strong credentials via environment variables for all non-local deployments, and configure SSL (`sslmode=require` or driver-specific equivalents) for production.

- **Session-based search trusts Redis org_id without independent DB cross-check** (`app/redis_client.py`, `app/routers/agent_query.py`)
  - `create_session` stores `__org_id__` in Redis and `get_session` returns it. `ask_agent` checks `session["org_id"] != org_id` to enforce isolation. If Redis data were ever tampered with (e.g., via an admin console or MITM enabled by weak TLS settings), an attacker could flip `__org_id__` and trick the API into treating a session as belonging to another org, enabling cross-tenant search. The requirement for a valid API key to hit `ask_agent` helps but does not fully mitigate the risk if Redis is compromised.
  - **Recommendation**: When resolving a session, cross-check Redis `file_ids` and `org_id` against the DB (`files` table) at least when a session is first used and on suspicious discrepancies. Treat any mismatch as a hard error, and combine this with strengthening Redis security to improve tenant isolation.

- **R2 storage client raises generic `ValueError` when env vars are missing** (`app/services/object_storage.py`)
  - If `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_R2_ACCESS_KEY_ID`, `CLOUDFLARE_R2_SECRET_ACCESS_KEY`, or `CLOUDFLARE_R2_BUCKET_NAME` are not set, `_get_client()` and `_bucket()` raise `ValueError`. Routes that use storage (ingestion, reprocessing, delete) may surface these as 500 responses with framework-generated details.
  - **Recommendation**: Fail fast at startup when required R2 env vars are missing (e.g., in a centralized settings module), or catch and wrap into sanitized HTTP responses (e.g., 503 “storage misconfigured”).

---

### Clean Code Suggestions and System Design

- **Blocking I/O and heavy work inside async FastAPI endpoints** (`app/routers/query.py`, `app/routers/agent_query.py`, `app/services/legal_primitives.py`)
  - Several async endpoints call blocking client libraries (Groq SDK, Qdrant sync client, Cohere via `httpx.Client`, PDF parsing, etc.) directly from the event loop. Under load, this can severely reduce throughput and cause head-of-line blocking, even when CPU usage is low, because one slow external call can stall other requests on the same worker.
  - **Recommendation**: Prefer async client libraries (e.g., `httpx.AsyncClient`, async variants of Groq/Qdrant clients) or wrap heavy synchronous calls with `anyio.to_thread.run_sync` / `run_in_threadpool`. Reserve truly blocking work for background tasks (Celery workers) where appropriate.

- **Ingestion and session management logic duplicated across routers and tasks** (`app/routers/injest.py`, `app/routers/session.py`, `app/tasks.py`)
  - Upload flows in `injest.py` and `session.py` implement similar streaming, hashing, duplicate detection, and Celery dispatch patterns, and comments in `injest.py` still describe an old auth model (“No JWT or login required”) that no longer matches the code. Duplication increases the risk of subtle drift (e.g., handling of `FAILED` status, error messages), and outdated comments make behavior harder to understand.
  - **Recommendation**: Extract shared ingestion/session services (e.g., `services/ingestion.py`, `services/session.py`) that own streaming + hashing + DB record creation + Celery dispatch. Keep routers thin and update comments to reflect the current API-key–based authentication and org isolation.

- **Qdrant sparse-vector logic intentionally duplicated but brittle** (`app/tasks.py`, `app/services/store.py`)
  - The `compute_sparse_vector` function exists in both the indexing path (`tasks.py`) and the query path (`store.py`). Any change to the tokenization, hashing, or weighting in one must be mirrored in the other, or retrieval quality will silently degrade due to representation mismatch between index-time and query-time features.
  - **Recommendation**: Centralize sparse-vector computation in a single module (e.g., `services/sparse.py`) and import it from both tasks and store. This guarantees consistency and simplifies future algorithm changes.

- **Planner and legal primitives layer multiple concerns in single modules** (`app/services/planner.py`, `app/services/legal_primitives.py`)
  - `planner.execute_plan` is responsible for logging, wiring org IDs, building search parameters, regex post-processing, orchestrating multi-step tool flows, and invoking the LLM. `legal_primitives` combines embedding, retrieval, structured extraction, logic, and drafting. This tight coupling makes it hard to test components in isolation and to evolve one concern (e.g., retrieval strategy or model choice) without touching planner code.
  - **Recommendation**: Split these responsibilities into clearer layers: (1) retrieval service (embeddings + Qdrant/search abstractions), (2) extraction/logic/drafting utilities, and (3) a thin planner/orchestrator that sequences tools. This will improve testability, make behavior easier to reason about, and support experimentation with alternate strategies.

- **Environment configuration scattered and partly insecure by default** (multiple modules: `app/database.py`, `app/worker.py`, `app/tasks.py`, `app/services/embedder.py`, `app/services/object_storage.py`, etc.)
  - Many modules call `load_dotenv()` and read env vars independently, with slightly different defaults and error-handling behavior. Some defaults are insecure (e.g., `postgres:password` with no SSL) or ambiguous about production readiness. This fragmentation makes it difficult to know which environment variables are truly required, where, and in which environments, and makes misconfiguration more likely.
  - **Recommendation**: Introduce a centralized configuration/settings module (e.g., Pydantic `BaseSettings` class) that loads and validates all env vars once at startup, distinguishes between dev-only defaults and production-required values, and provides typed access elsewhere. This also gives a single place to enforce security-related configuration requirements (SSL, strong passwords, etc.).

- **System design: cross-cutting concerns (auth, org isolation, observability) not fully centralized** (general across `app/routers/*` and `app/services/*`)
  - Auth and org isolation are implemented primarily via dependencies (e.g., `get_org_id`, `AuthContext`) and per-endpoint checks, which is good, but some paths (notably parts of session management) do not consistently apply these abstractions. Observability (logging of key events, timings, failures) is also mostly ad hoc rather than via a shared logging/metrics layer.
  - **Recommendation**: Ensure every externally reachable endpoint goes through a small set of shared dependencies/middleware to enforce auth, org isolation, and basic request logging. Consider cross-cutting middleware or dedicated service modules for tenant-aware operations so that “happy path” features cannot easily bypass security or observability by omission.

