"""MCP-KB key-management CRUD — oauth2-auth Phase 3 (T3.12 / FR-41).

NEW SMALL endpoint family under the OAuth2 HUMAN lane:
``/api/mcp/kb/keys`` (list / get / create / update / delete). A
logged-in human (cookie session OR a scoped bearer) manages the
module-lane KB keys minted by T3.11 — OAuth2's exact role per the v1.4
user ruling (decisions.md D19): it protects the token-management CRUD,
never the module endpoints themselves.

Scope: ``mcpkb:admin`` (documented choice — §7.3's illustrative
taxonomy has no KB entry; the name follows the established
``<module>:<action>`` shape of ``jobs:write`` / ``sources:write`` and
``admin`` because key management is admin-class credential control).
Under the v1 decorator contract (``daemon/auth/decorators.py``), a
cookie-session human satisfies ANY required scope (session-valid-is-
enough, D3.7); a bearer must actually hold ``mcpkb:admin`` or ``*``.

Registered on ``api_router`` ONLY when auth is enabled (the §5.6
reconciled tally: 30 legacy PROTECTED families + this 1 new family).
The routes carry the explicit ``auth_protected`` tag so the middleware's
ordered resolution classifies them PROTECTED even though the
``/api/mcp/kb`` module-lane Mount is a path prefix of them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from daemon.auth.decorators import require_scope
from daemon.auth.route_meta import tag_routes_by_path
from daemon.repositories.mcp_kb_keys.models import McpKbKey

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp/kb/keys", tags=["mcp-kb-keys"])


class KbKeyCreated(BaseModel):
    """Create response — carries the plaintext key EXACTLY ONCE."""

    id: str = Field(..., description="CRUD handle for the key row")
    label: Optional[str] = Field(None, description="Operator-friendly name")
    created_at: str = Field(..., description="now_utc_iso() at mint")
    key: str = Field(
        ...,
        description="The kbk_… plaintext — shown ONCE, never retrievable again",
    )


class KbKeyInfo(BaseModel):
    """Read shape — hash-only (the plaintext NEVER round-trips)."""

    id: str
    label: Optional[str] = None
    created_at: str
    last_used_at: Optional[str] = None
    revoked_at: Optional[str] = None


class KbKeyUpdate(BaseModel):
    label: Optional[str] = Field(..., description="New operator-friendly label")


class KbKeyList(BaseModel):
    keys: list[KbKeyInfo]


def _get_store(request: Request) -> Any:
    store = getattr(request.app.state, "mcp_kb_key_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "KB_KEYS_UNAVAILABLE",
                "message": "MCP-KB key store unavailable",
            },
        )
    return store


def _to_info(row: McpKbKey) -> KbKeyInfo:
    return KbKeyInfo(
        id=row.id,
        label=row.label,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


@router.get("", response_model=KbKeyList)
@require_scope("mcpkb:admin")
async def list_keys(request: Request) -> KbKeyList:
    """List keys (revoked rows hidden — pass ``?include_revoked=true``)."""
    store = _get_store(request)
    include_revoked = (
        request.query_params.get("include_revoked", "").lower() in ("1", "true", "yes")
    )
    rows = await asyncio.to_thread(
        store.list_keys, include_revoked=include_revoked
    )
    return KbKeyList(keys=[_to_info(row) for row in rows])


@router.post("", response_model=KbKeyCreated, status_code=201)
@require_scope("mcpkb:admin")
async def create_key(request: Request, body: KbKeyUpdate | None = None) -> KbKeyCreated:
    """Mint a key — the ``key`` field of the response is shown ONCE."""
    store = _get_store(request)
    label = body.label if body is not None else None
    row, plaintext = await asyncio.to_thread(store.create_key, label)
    return KbKeyCreated(
        id=row.id,
        label=row.label,
        created_at=row.created_at,
        key=plaintext,
    )


@router.get("/{key_id}", response_model=KbKeyInfo)
@require_scope("mcpkb:admin")
async def get_key(key_id: str, request: Request) -> KbKeyInfo:
    store = _get_store(request)
    row = await asyncio.to_thread(store.get_key, key_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "KB_KEY_NOT_FOUND", "message": f"No key {key_id}"},
        )
    return _to_info(row)


@router.put("/{key_id}", response_model=KbKeyInfo)
@require_scope("mcpkb:admin")
async def update_key(key_id: str, body: KbKeyUpdate, request: Request) -> KbKeyInfo:
    """Rename a key (keys are immutable beyond the label — rotate by
    minting a new key and revoking/deleting the old one)."""
    store = _get_store(request)
    row = await asyncio.to_thread(store.update_label, key_id, body.label)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "KB_KEY_NOT_FOUND", "message": f"No key {key_id}"},
        )
    return _to_info(row)


@router.delete("/{key_id}", status_code=204)
@require_scope("mcpkb:admin")
async def delete_key(key_id: str, request: Request):
    """Hard-delete a key row. Prefer ``POST .../{key_id}/revoke`` for
    audit-trail rotation (soft revoke keeps the row visible)."""
    store = _get_store(request)
    if not await asyncio.to_thread(store.delete_key, key_id):
        raise HTTPException(
            status_code=404,
            detail={"error": "KB_KEY_NOT_FOUND", "message": f"No key {key_id}"},
        )
    return None


@router.post("/{key_id}/revoke", response_model=KbKeyInfo)
@require_scope("mcpkb:admin")
async def revoke_key(key_id: str, request: Request) -> KbKeyInfo:
    """Soft-revoke (idempotent) — the auditable rotation half."""
    store = _get_store(request)
    if not await asyncio.to_thread(store.revoke_key, key_id):
        row = await asyncio.to_thread(store.get_key, key_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "KB_KEY_NOT_FOUND", "message": f"No key {key_id}"},
            )
        return _to_info(row)  # already revoked — idempotent echo
    row = await asyncio.to_thread(store.get_key, key_id)
    return _to_info(row)  # type: ignore[arg-type]


def tag_keys_router_protected(app: Any) -> None:
    """Mark this family explicitly PROTECTED in the route walk (T3.12).

    Called from ``create_app`` after registration so the middleware's
    ordered tagged-route resolution puts these templates BEFORE the
    ``/api/mcp/kb`` module-lane Mount (whose path is a prefix of them).
    """
    tag_routes_by_path(app, path_prefix="/api/mcp/kb/keys", protected=True)


__all__ = ["router", "tag_keys_router_protected"]
