"""
PostgreSQL ORM Models — legaltech schema
=========================================
Three tables, all in the 'legaltech' schema:
  defined_terms  — legal term definitions extracted per document
  deal_sessions  — one row per due diligence run
  audit_log      — one row per escalation triggered during a run
"""

import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from due_diligence.db.postgres import Base


# ---------------------------------------------------------------------------
# Defined Terms
# ---------------------------------------------------------------------------

class DefinedTerm(Base):
    __tablename__ = "defined_terms"
    __table_args__ = (
        # Prevent exact duplicate rows on re-ingest
        UniqueConstraint("term", "source_document", "hierarchy", name="uq_term_doc_path"),
        {"schema": "legaltech"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    term = Column(String(512), nullable=False, index=True)
    definition = Column(Text, nullable=False)
    source_document = Column(String(512), nullable=False, index=True)
    hierarchy = Column(String(1024), nullable=False)  # e.g. "ARTICLE I > Section 1.1"


# ---------------------------------------------------------------------------
# Deal Sessions
# ---------------------------------------------------------------------------

class DealSession(Base):
    __tablename__ = "deal_sessions"
    __table_args__ = {"schema": "legaltech"}

    session_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    goal = Column(Text, nullable=False)
    document_names = Column(Text, nullable=False)  # JSON list of filenames
    status = Column(String(32), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = {"schema": "legaltech"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(36), nullable=False, index=True)
    task_id = Column(String(64), nullable=False)
    trigger_type = Column(String(64), nullable=False)  # insufficient_coverage | definitional_conflict | structural_ambiguity
    reviewer_note = Column(Text, nullable=False)
    context = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Helper functions (replaces db/models.py SQLite helpers)
# ---------------------------------------------------------------------------

def create_session(session_id: str, goal: str, document_names: List[str]) -> str:
    from due_diligence.db.postgres import SessionLocal
    db = SessionLocal()
    try:
        session = DealSession(
            session_id=session_id,
            goal=goal,
            document_names=json.dumps(document_names),
            status="running",
        )
        due_diligence.db.add(session)
        due_diligence.db.commit()
    finally:
        due_diligence.db.close()
    return session_id


def complete_session(session_id: str):
    from due_diligence.db.postgres import SessionLocal
    db = SessionLocal()
    try:
        s = due_diligence.db.query(DealSession).filter_by(session_id=session_id).first()
        if s:
            s.status = "complete"
            s.completed_at = datetime.now(timezone.utc)
            due_diligence.db.commit()
    finally:
        due_diligence.db.close()


def log_escalation(
    session_id: str,
    task_id: str,
    trigger_type: str,
    reviewer_note: str,
    context: str = "",
):
    from due_diligence.db.postgres import SessionLocal
    db = SessionLocal()
    try:
        entry = AuditLog(
            session_id=session_id,
            task_id=task_id,
            trigger_type=trigger_type,
            reviewer_note=reviewer_note,
            context=context,
        )
        due_diligence.db.add(entry)
        due_diligence.db.commit()
    finally:
        due_diligence.db.close()
