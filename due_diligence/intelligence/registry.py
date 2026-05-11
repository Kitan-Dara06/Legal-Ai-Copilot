"""
Registry Query Engine — PostgreSQL (SQLAlchemy sync)
=====================================================
Queries the ``defined_terms_registry`` table in PostgreSQL
using synchronous SQLAlchemy sessions.

Compatible drop-in for the old sqlite3 ``RegistryQueryEngine``.
"""

import logging
from typing import Dict, List, Optional

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import get_database_url_sync
from app.models import DefinedTermRegistry

logger = logging.getLogger(__name__)


class RegistryQueryEngine:
    """
    Queries the PostgreSQL ``defined_terms_registry`` table.

    All queries run through a synchronous SQLAlchemy session so
    that this class can be called from thread-bound contexts
    (e.g. ``HybridRetriever.search()``).
    """

    def __init__(self, db_path: Optional[str] = None):
        # db_path kept for backward compatibility — ignored.
        # The engine is created lazily from the DATABASE_URL_SYNC env var.
        self._engine = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_engine(self):
        """Return (and memoise) the sync engine."""
        if self._engine is None:
            self._engine = create_engine(
                get_database_url_sync(),
                pool_timeout=5,
                pool_pre_ping=True,
            )
        return self._engine

    def _row_to_dict(self, row: DefinedTermRegistry) -> Dict:
        """Map an ORM row to the standard output dict used by callers."""
        return {
            "definition": row.definition,
            "document": str(row.source_document_id),
            "path": row.clause_reference,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_term(self, term: str) -> List[Dict]:
        """
        Fetch all definitions of *term* (case-insensitive) across all documents.

        Returns a list of dicts with keys ``definition``, ``document``,
        and ``path`` (clause reference).
        """
        engine = self._get_engine()
        stmt = select(DefinedTermRegistry).where(
            func.lower(DefinedTermRegistry.term) == term.lower()
        )

        try:
            with Session(engine) as db:
                rows = db.execute(stmt).scalars().all()
                return [self._row_to_dict(r) for r in rows]
        except Exception:
            logger.exception("[RegistryQueryEngine] get_term(%r) failed", term)
            return []

    def detect_conflicts(self) -> Dict[str, List[Dict]]:
        """
        Find terms that are defined in **more than one** distinct source
        document and return them grouped by term.

        Returns a dict like::

            {
                "Confidential Information": [
                    {"definition": "...", "document": "...", "path": "..."},
                    ...
                ],
                ...
            }
        """
        engine = self._get_engine()

        # Sub-query: terms that appear in >1 distinct source_document_id.
        conflict_subq = (
            select(
                DefinedTermRegistry.term,
                func.count(DefinedTermRegistry.source_document_id.distinct()).label(
                    "doc_count"
                ),
            )
            .group_by(DefinedTermRegistry.term)
            .having(func.count(DefinedTermRegistry.source_document_id.distinct()) > 1)
            .subquery()
        )

        # Main query: full rows whose term matches one of the conflicting terms.
        stmt = (
            select(DefinedTermRegistry)
            .join(
                conflict_subq,
                DefinedTermRegistry.term == conflict_subq.c.term,
            )
            .order_by(DefinedTermRegistry.term, DefinedTermRegistry.source_document_id)
        )

        try:
            with Session(engine) as db:
                rows = db.execute(stmt).scalars().all()

            results: Dict[str, List[Dict]] = {}
            for row in rows:
                results.setdefault(row.term, []).append(self._row_to_dict(row))

            return results
        except Exception:
            logger.exception("[RegistryQueryEngine] detect_conflicts() failed")
            return {}
