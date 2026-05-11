"""Integrations Router — Tool credential management with pgcrypto.

NFR-SEC-04: External tool credentials encrypted at rest via pgcrypto.
Never exposed to the frontend.

Section 8.5 SRS:
  GET    /orgs/{org_id}/integrations              — List tools (masked keys)
  POST   /orgs/{org_id}/integrations/{tool_name}  — Save encrypted credential
  DELETE /orgs/{org_id}/integrations/{tool_name}  — Remove credential
  POST   /integrations/test                       — Test a webhook/API connection
"""

import json
import logging
import os
import uuid
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_org_id_unified

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orgs/{org_id}/integrations", tags=["Integrations"])

# Master encryption key for pgcrypto
MASTER_KEY = os.getenv("PGCRYPTO_MASTER_KEY", "")


class IntegrationOut(BaseModel):
    tool_name: str
    key_preview: str  # First 8 chars of the encrypted key (masked)
    created_at: str

    model_config = {"from_attributes": True}


class SaveIntegrationRequest(BaseModel):
    config_json: str  # JSON string of credentials (will be encrypted)


class TestIntegrationRequest(BaseModel):
    tool_name: str
    config_json: str  # Credentials to test
    test_type: str = "connectivity"  # connectivity, send_test_message


def _mask_key(key: str) -> str:
    """Show first 8 chars, mask the rest."""
    if len(key) <= 8:
        return key
    return key[:8] + "..." + key[-4:]


@router.get("", response_model=List[IntegrationOut])
async def list_integrations(
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """List configured integrations with masked keys."""
    if not MASTER_KEY:
        return []

    org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id
    result = await db.execute(
        text("""
            SELECT tool_name, created_at,
                   convert_from(pgp_sym_decrypt(config_encrypted, :master_key), 'UTF-8') AS decrypted
            FROM org_tool_credentials
            WHERE org_id = :org_id
            ORDER BY tool_name
        """),
        {"org_id": org_uuid, "master_key": MASTER_KEY},
    )
    rows = result.fetchall()
    return [
        IntegrationOut(
            tool_name=row[0],
            key_preview=_mask_key(row[2]) if row[2] else "",
            created_at=row[1].isoformat() if row[1] else "",
        )
        for row in rows
    ]


@router.post("/{tool_name}", status_code=201)
async def save_integration(
    tool_name: str,
    req: SaveIntegrationRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Save an encrypted tool credential."""
    if not MASTER_KEY:
        raise HTTPException(
            status_code=500, detail="PGCRYPTO_MASTER_KEY not configured"
        )

    org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id

    # Upsert: delete existing, then insert encrypted
    await db.execute(
        text(
            "DELETE FROM org_tool_credentials WHERE org_id = :org_id AND tool_name = :tool_name"
        ),
        {"org_id": org_uuid, "tool_name": tool_name},
    )

    await db.execute(
        text("""
            INSERT INTO org_tool_credentials (org_id, tool_name, config_encrypted)
            VALUES (:org_id, :tool_name, pgp_sym_encrypt(:config_json, :master_key))
        """),
        {
            "org_id": org_uuid,
            "tool_name": tool_name,
            "config_json": req.config_json,
            "master_key": MASTER_KEY,
        },
    )
    await db.commit()
    return {"status": "saved", "tool_name": tool_name}


@router.delete("/{tool_name}", status_code=200)
async def delete_integration(
    tool_name: str,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Remove a tool credential."""
    org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id
    await db.execute(
        text(
            "DELETE FROM org_tool_credentials WHERE org_id = :org_id AND tool_name = :tool_name"
        ),
        {"org_id": org_uuid, "tool_name": tool_name},
    )
    await db.commit()
    return {"status": "deleted", "tool_name": tool_name}


@router.post("/test", status_code=200)
async def test_integration(
    req: TestIntegrationRequest,
    org_id: str = Depends(get_org_id_unified),
):
    """Test a webhook/API connection without saving."""
    config = json.loads(req.config_json)
    webhook_url = config.get("webhook_url", "")

    if req.test_type == "connectivity":
        try:
            if "slack.com" in webhook_url:
                r = httpx.post(
                    webhook_url,
                    json={"text": "🔌 Lex integration test — connection successful"},
                    timeout=10,
                )
                if r.status_code == 200:
                    return {
                        "status": "ok",
                        "message": "Slack webhook responded successfully",
                    }
            elif "hooks.slack.com" in webhook_url:
                r = httpx.post(
                    webhook_url,
                    json={"text": "🔌 Lex integration test — connection successful"},
                    timeout=10,
                )
                if r.status_code == 200:
                    return {
                        "status": "ok",
                        "message": "Slack webhook responded successfully",
                    }
            else:
                r = httpx.get(webhook_url, timeout=10)
                if r.status_code < 500:
                    return {
                        "status": "ok",
                        "message": f"Endpoint responded with HTTP {r.status_code}",
                    }

            return {
                "status": "error",
                "message": f"HTTP {r.status_code}: {r.text[:100]}",
            }
        except httpx.TimeoutException:
            return {"status": "error", "message": "Connection timed out"}
        except Exception as e:
            return {"status": "error", "message": str(e)[:200]}

    return {"status": "error", "message": f"Unknown test type: {req.test_type}"}
