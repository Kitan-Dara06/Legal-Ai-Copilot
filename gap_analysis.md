# Lex Gap Analysis — Legal Action Agent (SRS 3) + Unification

> **Three documents read:** `Legal_Action_Agent_SRS_v1_0.pdf` · `Lex_Unified_SRS_v1_0.pdf`  
> **Two codebases scanned:** `/legal_rag` (Legal AI Copilot — complete) · `/legaltech` (Legal Due Diligence Agent — partial)  
> Date: 2026-05-01

---

## Part 1 — What exists today

> [!NOTE]
> Features previously marked ❌ Missing in `/legal_rag` were verified against both codebases. 8 of 10 exist in `/legaltech` and are confirmed implemented. Only **DeadlineRegistry** and a **full Workspace model** remain genuinely absent across both repos.

### `/legal_rag` (Legal AI Copilot — System 1)
| Component | Status | Where it lives |
|---|---|---|
| FastAPI backend (`app/`) | ✅ Complete | `legal_rag/app/` |
| Supabase Auth (JWT, org scoping, invites) | ✅ Complete | `app/dependencies.py` |
| PostgreSQL via SQLAlchemy + Alembic | ✅ Complete | `app/database.py` + `alembic/` |
| Session-based RAG query (`/ask`, `/ask-agent`) | ✅ Complete | `app/tasks.py` |
| Document ingestion via Celery | ✅ Complete | `app/tasks.py` |
| Qdrant vector store (single embedding) | ✅ Complete | `app/tasks.py` |
| Redis + RabbitMQ | ✅ Complete | `docker-compose.yml` |
| Next.js frontend (`frontend-next/`) | ✅ Complete | `frontend-next/` |
| Due Diligence frontend page (PlanReview, etc.) | ✅ Exists (wired to legaltech API) | `frontend-next/` |
| Dual named vectors (voyage-law-2 + nomic-embed-text) | ✅ **Exists in legaltech** — not yet wired to legal_rag Qdrant | `legaltech/backend/ingestion/embedder.py` |
| SPLADE sparse index (BM25-style sparse vector) | ✅ **Exists in legaltech** — SPLADE via fastembed | `legaltech/backend/ingestion/embedder.py` · `retrieval/hybrid_search.py` |
| RRF fusion | ✅ **Exists in legaltech** — `FusionQuery(fusion=Fusion.RRF)` via Qdrant | `legaltech/backend/retrieval/hybrid_search.py` |
| Cross-encoder reranker (BGE-reranker-base) | ✅ **Exists in legaltech** — `LegalCrossEncoder` w/ graph shield + linearisation | `legaltech/backend/retrieval/reranker.py` |
| GeminiOCR for scanned PDFs | ✅ **Exists in legal_rag** — `ocr_pdf_to_markdown_pages()` via google-genai | `legal_rag/app/services/ocr_gemini.py` |
| Clause-boundary chunker | ✅ **Exists in legaltech** — `ClauseChunker` (regex, hierarchical, not spaCy) | `legaltech/backend/ingestion/chunker.py` |
| Defined terms registry (extract + conflict detection) | ✅ **Exists in legaltech** — `DefinedTermExtractor` + `RegistryQueryEngine` | `legaltech/backend/ingestion/terms_extractor.py` · `intelligence/registry.py` |
| Cross-reference graph (Neo4j AuraDB) | ✅ **Exists in legaltech** — `DependencyGraph` (MERGE nodes/edges, BFS traversal) | `legaltech/backend/intelligence/graph.py` · `retrieval/graph_expansion.py` |
| Deadline registry | ❌ **Truly missing** — not in either codebase | — |
| Workspace model (deal folders) | ⚠️ **Partial** — `DealSession` exists (sessions only); no `workspaces` / `goals` / `workspace_sessions` tables | `legaltech/backend/db/pg_models.py` |

### `/legaltech` (Legal Due Diligence Agent — System 2)
| Component | Status | Notes |
|---|---|---|
| Clause-boundary chunker (`ingestion/chunker.py`) | ✅ Exists | Regex-based `ClauseChunker`, 5-level hierarchy (HEADER, ARTICLE, 1., 1.1, (a), (i)) |
| Dual embedder: voyage-law-2 + nomic-embed-text-v1.5 (`ingestion/embedder.py`) | ✅ Exists | 3 named vectors: `dense_voyage` (1024d), `dense_nomic` (768d), `sparse_legal` (SPLADE) |
| Parser: PyMuPDF + python-docx (`ingestion/parser.py`) | ✅ Exists | Font-calibrated block extraction with page numbers |
| GeminiOCR | ⚠️ Not in legaltech — found in `legal_rag/app/services/ocr_gemini.py` | Must be shared module at unification |
| Hybrid search: voyage/nomic dense + SPLADE sparse + RRF (`retrieval/hybrid_search.py`) | ✅ Exists | `HybridRetriever` with Qdrant `FusionQuery(RRF)`, conflict annotation hook |
| Cross-encoder reranker — BGE-reranker-base (`retrieval/reranker.py`) | ✅ Exists | `LegalCrossEncoder`: graph shield (prunes superseded clauses), strict linearisation, `.predict()` |
| Graph expansion (`retrieval/graph_expansion.py`) | ✅ Functional | `GraphExpander` calls `DependencyGraph.get_dependency_chains()` — 53 lines, complete |
| Neo4j AuraDB graph (`intelligence/graph.py`) | ✅ Exists | `DependencyGraph`: MERGE upsert, intra/cross-doc edges, BFS up to 5 hops, stats API |
| Cross-reference parser (`ingestion/reference_parser.py`) | ✅ Exists | Regex pre-pass → LLM fallback; wires Neo4j cross-doc edges |
| Terms extractor (`ingestion/terms_extractor.py`) | ✅ Exists | LLM extraction → PostgreSQL `legaltech.defined_terms` |
| Terms conflict detection (`intelligence/registry.py`) | ✅ Exists | `RegistryQueryEngine.detect_conflicts()` — cross-doc term conflicts via PostgreSQL |
| Findings generation (`synthesis/finding_gen.py`) | ✅ Exists | — |
| Contradiction detection (`synthesis/contradiction.py`) | ✅ Exists | — |
| Escalation triggers (`escalation/triggers.py`) | ✅ Exists | — |
| Planner/decomposer (`planner/decomposer.py`) | ✅ Exists | — |
| Planner executor (`planner/executor.py`) | ✅ Exists | — |
| PostgreSQL models (`db/pg_models.py`) | ⚠️ Partial | `DefinedTerm`, `DealSession`, `AuditLog` — not the full unified schema |
| SQLite models (`db/models.py`) | ⚠️ Dev/demo only | Must be discarded at unification |
| Auth integration (Supabase) | ❌ Missing | No auth in legaltech |
| Celery async jobs | ❌ Missing | Synchronous demo only |
| LangGraph state machine | ❌ Missing | — |
| DeadlineRegistry | ❌ Missing | No date extraction or deadline table |
| ResearchLog | ❌ Missing | — |
| Workspace model (workspaces, goals, workspace_sessions) | ❌ Missing | Only `DealSession` exists |
| Named vectors shared with legal_rag's Qdrant | ⚠️ Separate | legaltech uses `./qdrant_storage` locally; not integrated with legal_rag's Qdrant instance |

---

## Part 2 — Legal Action Agent gaps (System 3)

**The Legal Action Agent does not exist at all.** Every component below must be built from scratch. Items marked ✅ "leverage" mean existing code can be adapted.

### 2.1 Core Architecture — Not built
| Requirement | Gap | Leverage |
|---|---|---|
| LangGraph master state machine | ❌ Not built | legaltech has no LangGraph |
| Intent classifier node (`intent_node`) | ❌ Not built | — |
| Ambiguity gate (`ambiguity_gate_node`) | ❌ Not built | — |
| ANALYZE path nodes | ❌ Not built | legaltech retrieval pipeline ✅ |
| REASON path nodes | ❌ Not built | legaltech synthesis/findings ✅ |
| ACT path nodes | ❌ Not built | — |
| Approval gate manager | ❌ Not built | — |
| Execution engine | ❌ Not built | — |
| Saga recovery / compensation | ❌ Not built | — |
| Deadline scanner (Celery Beat) | ❌ Not built | — |

### 2.2 Data Model Gaps vs Action Agent SRS (§7)
> The Unified SRS collapses these into a single DB. These tables are completely absent from both codebases.

| Table | Status | Notes |
|---|---|---|
| `workspaces` | ❌ Missing | Replaces Copilot's session model |
| `workspace_sessions` | ❌ Missing | Cross-goal sessions |
| `goals` | ❌ Missing | New concept |
| `workflow_executions` | ❌ Missing | Replaces Copilot's chat sessions |
| `findings` | ❌ Missing | From legaltech but not in PG schema |
| `actions` | ❌ Missing | ACT path only |
| `approval_requests` | ❌ Missing | ACT path HITL gate |
| `audit_log` | ❌ Missing (Copilot has none; legaltech has none) | Critical — 7-year retention |
| `defined_terms_registry` | ❌ Missing | legaltech has SQLite demo version |
| `cross_reference_index` | ❌ Missing (PG side of Neo4j bridge) | |
| `deadline_registry` | ❌ Missing | |
| `tool_call_log` | ❌ Missing | |
| `research_log` | ❌ Missing | |

### 2.3 API Gaps vs Action Agent SRS (§8)
None of the Action Agent endpoints exist. Key missing routes:

```
POST   /action-agent/workflows                           # Submit document + goal
POST   /action-agent/workflows/{id}/confirm-plan         # HITL Gate 1
GET    /action-agent/approvals                           # List pending approvals
POST   /workflows/{id}/approve                           # HITL Gate 2 (token auth)
POST   /workflows/{id}/reject                            # HITL Gate 2 reject
POST   /workflows/{id}/resolve-escalation                # Admin escalation
POST   /approvals/{id}/reissue                           # Re-issue expired gate
GET    /action-agent/workflows/{id}/audit                # Audit trail
GET    /action-agent/escalations                         # Escalation dashboard
GET    /action-agent/deadlines                           # Deadline registry
```

Also missing from Unified SRS §8 (workspace-centric routes):
```
POST   /workspaces                                       # Create workspace
POST   /workspaces/{id}/documents                        # Upload to workspace
POST   /workspaces/{id}/goals                            # Submit goal (unified entry)
POST   /workspaces/{id}/goals/{id}/confirm-intent        # Ambiguity gate response
GET    /workspaces/{id}/defined-terms                    # Defined terms with conflicts
GET    /workspaces/{id}/graph/expand                     # Cross-ref graph expansion
GET    /research/export                                  # Research log export
```

### 2.4 Frontend Gaps
| Page | Status | Notes |
|---|---|---|
| Workspace view (`/workspaces/{id}`) | ❌ Missing | Deal folder UI |
| Adaptive results panel (ANALYZE/REASON/ACT variants) | ❌ Missing | One panel, 3 render modes |
| Ambiguity gate UI (inline) | ❌ Missing | Inline classification confirmation |
| Plan confirmation view (`/action-agent/{id}/plan`) | ⚠️ PlanReview.tsx exists | Wired to legaltech API, not unified |
| Approval inbox (`/approvals`) | ❌ Missing | Pending approvals with urgency |
| Token-gated approval page (no-login URL) | ❌ Missing | Mobile-first |
| Workflow status / action queue | ❌ Missing | |
| Audit trail view | ❌ Missing | Admin + Lawyer |
| Escalation management | ❌ Missing | Admin only |
| Deadline registry view | ❌ Missing | |

### 2.5 Infrastructure Gaps
| Item | Status |
|---|---|
| Neo4j AuraDB integration | ❌ Missing from legal_rag; legaltech has basic Neo4j but not integrated |
| Cloudflare R2 object storage | ❌ Missing — documents stored locally |
| LangGraph PostgreSQL checkpoint backend (`langgraph-checkpoint-postgres`) | ❌ Missing |
| SMTP + Slack notification layer | ❌ Missing |
| Cohere reranker in legal_rag | ❌ Missing (only in legaltech) |
| GeminiOCR in legal_rag | ❌ Missing (only in legaltech) |
| ResearchLog instrumentation | ❌ Missing |
| Prompt archiving to R2 | ❌ Missing |

---

## Part 3 — Unification gaps (Lex SRS vs both codebases)

The Lex Unified SRS says this is a **greenfield unified system** — not a wrapper. Key architectural changes required:

### 3.1 The Big Three Architectural Changes

| What Changes | Current State | Lex Target |
|---|---|---|
| **Ingestion timing** | legal_rag: per-query. legaltech: per-run sync demo | Once per document, fully async, shared by all goals |
| **Orchestrator** | None — sequential function calls | LangGraph master state machine with intent routing |
| **Data model** | Two separate schemas, partial overlap | One unified PostgreSQL schema, all tables |
| **Frontend** | legal_rag Next.js (separate mode/page) | One workspace, adaptive results panel |
| **Sessions** | Copilot chat sessions only | Workspace sessions spanning all intents |
| **Research data** | Not collected | ResearchLog on every retrieval call |
| **Defined terms / cross-ref graph** | Built on-demand in legaltech | Built at ingestion, available to all layers |

### 3.2 What Can Be Ported From Each Codebase

````carousel
#### From `/legal_rag` → Port as-is
- Supabase Auth layer (`app/dependencies.py`) → Unified auth
- Alembic migration setup → Extend with new tables
- Docker Compose stack → Extend with Neo4j container
- Celery + RabbitMQ wiring → Extend with new task queues
- Next.js frontend shell → Replace session pages with workspace pages
- Org management, invites, members → Keep

<!-- slide -->

#### From `/legaltech` → Port as-is
- `ingestion/chunker.py` → Stage 2 of unified pipeline
- `ingestion/embedder.py` → Stage 3 (dual named vectors)
- `ingestion/parser.py` + `ocr_gemini.py` → Stage 1
- `ingestion/reference_parser.py` → Stage 5 (cross-ref)
- `ingestion/terms_extractor.py` → Stage 4 (needs conflict detection)
- `retrieval/hybrid_search.py` → ANALYZE + REASON retrieval node
- `retrieval/reranker.py` → Reranker node (with research instrumentation)
- `synthesis/finding_gen.py` → REASON findings node
- `synthesis/contradiction.py` → REASON contradiction node
- `escalation/triggers.py` → Typed escalation system
- `planner/decomposer.py` → ACT action detection (refactor for LangGraph node)

<!-- slide -->

#### Must Be Built Net-New
- LangGraph state machine (all paths)
- Intent classifier node
- Ambiguity gate node + UI
- Approval gate manager (HMAC, TTL, durable state)
- Execution engine (tool dispatch + idempotency)
- Saga recovery module
- Deadline scanner (Celery Beat)
- Notification layer (SMTP + Slack)
- AuditLog (full schema + R2 archiving)
- ResearchLog (instrumented on every retrieval)
- Workspace + Goal + WorkflowExecution data model
- Unified Next.js workspace UI
- Token-gated approval page (mobile-first)
````

---

## Part 4 — Prioritised Build Order (aligned with Lex SRS §11)

> [!IMPORTANT]
> The Lex SRS defines a 15-stage build order. Current state completes **0 of 15 stages fully** — partial work exists for Stages 2–4 in legaltech.

| Stage | What | Current state | Effort |
|---|---|---|---|
| **1** | Unified PostgreSQL schema + Alembic migrations (all 12 tables) | 0% — schema must be designed fresh | High |
| **2** | Document intelligence pipeline (unified, async, Celery) | ~50% — legaltech components exist but not wired to PG + Celery | Medium |
| **3** | Defined terms extractor + conflict detection | ~40% — extractor exists, conflict detection missing | Low-Medium |
| **4** | Cross-reference graph (Neo4j node/edge writes + PG index) | ~25% — graph.py partial, no Celery wiring | Medium |
| **5** | Deadline extraction + DeadlineRegistry + Celery Beat scanner | 0% | Medium |
| **6** | Workspace API + session management + frontend workspace view | 0% | Medium |
| **7** | Intent classifier + ambiguity gate (LangGraph shell) | 0% | Medium |
| **8** | ANALYZE path (retrieval + reranker + synthesis) | ~60% — components exist in legaltech, need LangGraph wiring | Low |
| **9** | REASON path (graph expansion + findings + contradiction + escalations) | ~50% — components exist, need LangGraph wiring | Medium |
| **10** | ACT path: draft + approval gate | 0% | High |
| **11** | ACT path: execution engine + saga recovery | 0% | High |
| **12** | Audit trail (AuditLog table + R2 archiving) | 0% | Medium |
| **13** | Research instrumentation (ResearchLog on every retrieval) | 0% | Low |
| **14** | Notification layer (SMTP + Slack + dedup) | 0% | Low-Medium |
| **15** | CUAD evaluation scripts (offline, parallel) | ~30% — eval scripts exist in legaltech | Low |

---

## Part 5 — Critical Design Decisions Before Build Starts

> [!WARNING]
> These must be resolved before touching Stage 1 schema. Getting them wrong makes migration painful.

| Decision | Options | Recommendation |
|---|---|---|
| **Workspace vs Session model** | Keep Copilot sessions + add workspaces alongside, or full migration | Full migration — Lex SRS is explicit: workspaces replace sessions |
| **legaltech DB migration** | legaltech uses SQLite (`legal_registry.db`) for dev, pg_models.py partially | Discard SQLite entirely. Port pg_models.py into unified Alembic schema |
| **Qdrant collection strategy** | legaltech has separate `qdrant_storage/` local. legal_rag has its own Qdrant | Merge into one Qdrant instance, named vectors per document, workspace_id as payload filter |
| **LangGraph checkpoint backend** | `langgraph-checkpoint-postgres` (recommended by SRS OQ-03) | Use it — stability confirmed since LangGraph 0.2+ |
| **Neo4j** | legaltech has basic Neo4j; legal_rag has none | Add Neo4j to legal_rag Docker Compose. Use legaltech's `graph.py` as starting point |
| **R2 object storage** | Neither codebase has R2 | Add `boto3` with R2 endpoint. Documents currently stored locally in legal_rag |
| **Ingestion trigger** | Currently on-query (legal_rag) or CLI (legaltech) | Must become Celery task on upload, with pipeline stage tracking per document |

---

## Summary Scorecard

| System | SRS compliance | Key missing |
|---|---|---|
| Legal AI Copilot (01) | ~75% | Dual vectors + RRF not yet wired to its Qdrant; workspace model; deadline registry |
| Legal Due Diligence Agent (02) | ~65% | Celery async, Auth, unified PostgreSQL schema, Workspace/Goal model, LangGraph, DeadlineRegistry, ResearchLog |
| Legal Action Agent (03) | **0%** | Everything — does not exist |
| Lex Unified Platform | **~20%** | Schema, orchestrator, ACT path, approval gates, execution engine, saga, notifications, research log, audit trail, workspace UI; Qdrant unification; deadline pipeline |

> [!NOTE]
> The previously-flagged 10 ❌ gaps in `/legal_rag` were re-verified. **8 of 10 are implemented** in `/legaltech` or `/legal_rag/app/services/`. The two genuinely absent items are **DeadlineRegistry** (no date-extraction module or DB table exists anywhere) and the **full Workspace model** (`workspaces`, `goals`, `workspace_sessions` tables — only `DealSession` exists). The reranker is BGE-reranker-base (`BAAI/bge-reranker-base`) via `sentence-transformers`, not Cohere. The chunker is a regex-based `ClauseChunker`, not spaCy.

> [!IMPORTANT]
> The unification task remains: legaltech’s intelligence layer must be wired into legal_rag’s Celery/PostgreSQL/Qdrant production stack. The LangGraph orchestrator, DeadlineRegistry, workspace data model, and the complete ACT path are the primary build targets.
