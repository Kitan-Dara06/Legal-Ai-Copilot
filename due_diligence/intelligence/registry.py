"""
Registry Query Engine — PostgreSQL
====================================
Replaced the sqlite3 implementation with SQLAlchemy queries
against the legaltech.defined_terms table in PostgreSQL.

Public API is identical to the old sqlite3 version:
  get_term(term) -> List[Dict]
  detect_conflicts() -> Dict[str, List[Dict]]
"""

import re
from typing import Dict, List, Optional

from sqlalchemy import func, select, text



class RegistryQueryEngine:
    """
    Queries the PostgreSQL legaltech.defined_terms table.
    Compatible drop-in for the old sqlite3 RegistryQueryEngine.
    """

    def __init__(self, db_path: Optional[str] = None):
        # db_path param kept for backward compat — ignored (uses DATABASE_URL)
        pass

    def get_term(self, term: str) -> List[Dict]:
        """Fetches all definitions of a specific term across all documents."""
        with SessionLocal() as db:
            rows = (
                .filter(func.lower(DefinedTerm.term) == term.lower())
                .all()
            )
            return [
                {
                    "definition": r.definition,
                    "document": r.source_document,
                    "path": r.hierarchy,
                }
                for r in rows
            ]

    def detect_conflicts(self) -> Dict[str, List[Dict]]:
        """
        Returns a dict of term → list[definition dicts] for any term
        that appears in more than one source_document.
        """
        with SessionLocal() as db:
            # Find terms defined in more than one distinct document
            subq = (
                .group_by(func.lower(DefinedTerm.term))
                .having(
                    func.count(func.distinct(DefinedTerm.source_document)) > 1
                )
                .subquery()
            )

            flagged = (
                .filter(func.lower(DefinedTerm.term).in_(
                    select(func.lower(subq.c.term))
                ))
                .all()
            )

        # Group by (case-insensitive) term
        conflicts: Dict[str, List[Dict]] = {}
        for row in flagged:
            key = row.term
            if key not in conflicts:
                conflicts[key] = []
            conflicts[key].append({
                "definition": row.definition,
                "document": row.source_document,
                "path": row.hierarchy,
            })

        return conflicts
