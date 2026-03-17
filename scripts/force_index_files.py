#!/usr/bin/env python3
"""
Force Index Files
=================
Directly processes PDFs into Qdrant, bypassing GCS entirely.

Useful when:
  - Files are stuck as READY in Postgres but missing from Qdrant
  - GCS bucket doesn't exist yet
  - You need to re-index specific files immediately

Usage:
    # Auto-mode: scans app/uploads/ and matches PDFs to DB records
    venv/bin/python scripts/force_index_files.py

    # Manual mode: provide a specific PDF path and file_id
    venv/bin/python scripts/force_index_files.py --file /path/to/file.pdf --file-id 111 --org-id stream_ui_org
"""

import argparse
import os
import sys
import hashlib

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ───────────────────────────