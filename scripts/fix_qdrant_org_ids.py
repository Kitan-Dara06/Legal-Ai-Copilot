#!/usr/bin/env python3
"""
One-time Qdrant Migration: Fix org_id Format Mismatch (C1)

After the VARCHAR→UUID migration, old Qdrant points may still have
slug-formatted org_id values (e.g. "my-firm") instead of UUID format
(e.g. "550e8400-..."). This script scrolls all Qdrant points and
updates the old slug org_id to the correct UUID from Postgres.

Usage:
    python scripts/fix_qdrant_org_ids.py [--dry-run] [--collection COLLECTION_NAME]

Requires:
    - DATABASE_URL environment variable (Postgres)
    - QDRANT_URL environment variable
    - QDRANT_API_KEY (optional, for Qdrant Cloud)
"""

import argparse
import logging
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

UUID_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def is_uuid(value: str) -> bool:
    return bool(UUID_REGEX.match(value))


def build_slug_to_uuid_map() -> dict[str, str]:
    """Fetch org slug→UUID mapping from Postgres."""
    from sqlalchemy import create_engine, text

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")

    # Convert async URL to sync if needed
    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(sync_url)

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, slug FROM organizations")).fetchall()

    mapping = {row.slug: str(row.id) for row in rows}
    logger.info("Loaded %d org slug→UUID mappings from Postgres", len(mapping))
    return mapping


def fix_qdrant_org_ids(collection: str, dry_run: bool = True):
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointIdsList, SetPayloadOperation

    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")
    if not qdrant_url:
        raise RuntimeError("QDRANT_URL not set")

    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=120)

    slug_to_uuid = build_slug_to_uuid_map()

    updated = 0
    skipped = 0
    errors = 0
    offset = None

    while True:
        result = client.scroll(
            collection_name=collection,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points, next_offset = result

        if not points:
            break

        for point in points:
            org_id = point.payload.get("org_id")
            if not org_id or is_uuid(str(org_id)):
                skipped += 1
                continue

            # Old slug format — look up the UUID
            uuid_val = slug_to_uuid.get(str(org_id))
            if not uuid_val:
                logger.warning(
                    "Point %s has unknown org_id slug '%s' — no mapping found",
                    point.id,
                    org_id,
                )
                errors += 1
                continue

            if dry_run:
                logger.info(
                    "[DRY RUN] Would update point %s: org_id '%s' → '%s'",
                    point.id,
                    org_id,
                    uuid_val,
                )
            else:
                client.set_payload(
                    collection_name=collection,
                    payload={"org_id": uuid_val},
                    points=[point.id],
                )
                logger.info(
                    "Updated point %s: org_id '%s' → '%s'",
                    point.id,
                    org_id,
                    uuid_val,
                )
            updated += 1

        if next_offset is None:
            break
        offset = next_offset

    action = "Would update" if dry_run else "Updated"
    logger.info(
        "Done. %s %d points, skipped %d (already UUID), %d errors.",
        action,
        updated,
        skipped,
        errors,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix Qdrant org_id slug→UUID")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes only")
    parser.add_argument(
        "--collection",
        default=os.getenv("QDRANT_COLLECTION_NAME", "legal_chunks"),
        help="Qdrant collection name",
    )
    args = parser.parse_args()

    logger.info(
        "Starting Qdrant org_id fix (collection=%s, dry_run=%s)",
        args.collection,
        args.dry_run,
    )
    fix_qdrant_org_ids(args.collection, dry_run=args.dry_run)
