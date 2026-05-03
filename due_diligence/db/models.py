"""
SQLite DB Models for the Due Diligence Agent
=============================================
Two tables using plain sqlite3 — consistent with the rest of the codebase.
No SQLAlchemy / Alembic for V1.

Tables:
  deal_sessions  — one row per user-initiated due diligence run
  audit_log      — one row per escalation triggered during a run
"""

import sqlite3
from datetime import datetime
from typing import Dict, List, Optional


DB_PATH = "./legal_registry.db"


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DB_PATH):
    """Create all tables if they do not exist. Safe to call multiple times."""
    conn = _get_conn(db_path)
    conn.executescript("""
        -- Existing defined_terms table (created by DefinedTermExtractor)
        CREATE TABLE IF NOT EXISTS defined_terms (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            term            TEXT NOT NULL,
            definition      TEXT NOT NULL,
            source_document TEXT NOT NULL,
            hierarchy       TEXT NOT NULL
        );

        -- Due diligence sessions
        CREATE TABLE IF NOT EXISTS deal_sessions (
            session_id      TEXT PRIMARY KEY,
            goal            TEXT NOT NULL,
            document_names  TEXT NOT NULL,   -- JSON array of strings
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TEXT NOT NULL,
            completed_at    TEXT
        );

        -- Escalation audit log
        CREATE TABLE IF NOT EXISTS audit_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL,
            task_id         TEXT NOT NULL,
            trigger_type    TEXT NOT NULL,   -- insufficient_coverage | definitional_conflict | structural_ambiguity
            reviewer_note   TEXT NOT NULL,
            context         TEXT,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES deal_sessions(session_id)
        );
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def create_session(
    session_id: str,
    goal: str,
    document_names: List[str],
    db_path: str = DB_PATH,
) -> str:
    """Insert a new deal session. Returns the session_id."""
    import json
    conn = _get_conn(db_path)
    conn.execute(
        """
        INSERT INTO deal_sessions (session_id, goal, document_names, status, created_at)
        VALUES (?, ?, ?, 'running', ?)
        """,
        (session_id, goal, json.dumps(document_names), datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return session_id


def complete_session(session_id: str, db_path: str = DB_PATH):
    conn = _get_conn(db_path)
    conn.execute(
        "UPDATE deal_sessions SET status = 'complete', completed_at = ? WHERE session_id = ?",
        (datetime.utcnow().isoformat(), session_id),
    )
    conn.commit()
    conn.close()


def get_session(session_id: str, db_path: str = DB_PATH) -> Optional[Dict]:
    import json
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM deal_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["document_names"] = json.loads(d["document_names"])
        return d
    return None


# ---------------------------------------------------------------------------
# Audit log helpers
# ---------------------------------------------------------------------------

def log_escalation(
    session_id: str,
    task_id: str,
    trigger_type: str,
    reviewer_note: str,
    context: str = "",
    db_path: str = DB_PATH,
):
    """Insert one escalation event into the audit log."""
    conn = _get_conn(db_path)
    conn.execute(
        """
        INSERT INTO audit_log (session_id, task_id, trigger_type, reviewer_note, context, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, task_id, trigger_type, reviewer_note, context, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_audit_log(session_id: str, db_path: str = DB_PATH) -> List[Dict]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE session_id = ? ORDER BY id ASC", (session_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CLI: initialise the database
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    print(f"Database initialised at: {DB_PATH}")
    print("Tables: defined_terms, deal_sessions, audit_log")
