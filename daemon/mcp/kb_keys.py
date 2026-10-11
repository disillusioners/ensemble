"""MCP-KB module-lane key mechanism — oauth2-auth Phase 3 (T3.11).

CONFIRMED minimal build (dispatch 2026-10-11, per plan-overview §12
item 4(a) confirm-at-implementation): the KB mounts
(``/api/mcp/kb``, ``/api/mcp/kb/sse``) sit behind a thin pure-ASGI key
guard. The mechanism is a security improvement INDEPENDENT of OAuth2 —
it runs identically with ``auth.enabled`` true or false (FR-42) and is
invisible to the OAuth2 middleware (module-lane pass-through, FR-39).

Module-owned everything (D20a/D20b — lane isolation is a HARD
requirement):

* **Store** — the module-owned ``mcp_kb_keys`` table (own per-table
  migration). NOT ``AuthConfig``, NOT ``auth_bearer_tokens``; no
  cross-store lookups in either direction.
* **Key format / token namespace (D20b)** — ``kbk_`` + 48 lowercase
  hex chars (``secrets.token_hex(24)``). The prefix makes the
  credential format visually and programmatically distinct from the
  OAuth2 ``ensemble_sid`` cookie / ``Authorization: Bearer`` header and
  the webhook ``X-Webhook-Secret`` shared secret, so a module token can
  never be mistaken for another lane's (D20b by construction).
* **Presentation** — ``X-KB-Key: kbk_…`` header on mount requests. A
  bearer header or webhook secret is NEVER consulted here (and a KB key
  is never consulted there — D20d negative matrix, phase 5).
* **Rejection semantics (D20d)** — store has ≥1 active key and the
  header is missing → ``401 {"error": "KB_KEY_REQUIRED"}``; presented
  but no active row hashes to it → ``401 {"error": "KB_KEY_INVALID"}``.
  Constant-time compare via hash lookup (SHA-256 both sides).
* **Bootstrap posture** — an EMPTY store passes through (today's
  fully-open behavior preserved byte-for-byte until the first key is
  minted). This is the zero-disruption default: the mechanism activates
  exactly when the operator mints the first key via the T3.12 CRUD.
  (The pre-existing open posture of ``kb_server.py`` is the documented
  baseline; the guard is the improvement path, not a behavior change
  for unconfigured deployments.)

The guard resolves the store from ``app.state.mcp_kb_key_store`` at
request time (host_capture lookup pattern). Missing store (tests /
partial boot) = empty-store posture (pass-through).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from typing import Any

from daemon.repositories.mcp_kb_keys.models import McpKbKey
from daemon.repositories.mcp_kb_keys.repository import McpKbKeyRepository
from daemon.services.timestamps import now_utc_iso

logger = logging.getLogger(__name__)

#: Credential prefix (D20b namespace separation — see module docstring).
KB_KEY_PREFIX = "kbk_"

#: Header the mounts expect the key in.
KB_KEY_HEADER = "x-kb-key"

#: Random bytes behind the hex payload (48 hex chars).
KB_KEY_RANDOM_BYTES = 24


def mint_kb_key() -> str:
    """Mint one plaintext key — ``kbk_`` + 48 hex (called at CRUD create;
    the plaintext is returned ONCE and never stored)."""
    return KB_KEY_PREFIX + secrets.token_hex(KB_KEY_RANDOM_BYTES)


def hash_kb_key(presented: str) -> str:
    """SHA-256 hex of the presented material (the storage form)."""
    return hashlib.sha256(presented.encode("utf-8")).hexdigest()


class McpKbKeyStore:
    """Verification + mint service over the module-owned repository."""

    def __init__(self, engine: Any, *, now_iso_fn: Any = now_utc_iso) -> None:
        self._repository = McpKbKeyRepository(engine)
        self._now_iso_fn = now_iso_fn

    # ── verification (mount guard path) ─────────────────────────────

    def has_active_keys(self) -> bool:
        """True when at least one active (non-revoked) key exists."""
        return bool(self._repository.list_keys(include_revoked=False))

    def verify(self, presented: str | None) -> tuple[bool, str]:
        """Verify a presented key. Returns ``(ok, reject_code)``.

        ``reject_code`` is ``""`` on success, else ``KB_KEY_REQUIRED``
        (missing header) or ``KB_KEY_INVALID`` (no active row hashes to
        it). Hash-vs-hash comparison: the presented value is SHA-256'd
        and looked up — no plaintext secret ever sits in a comparison
        against a stored value it could leak.
        """
        if not presented:
            return False, "KB_KEY_REQUIRED"
        row = self._repository.get_active_by_hash(hash_kb_key(presented))
        if row is None:
            return False, "KB_KEY_INVALID"
        self._repository.touch_key(row.id_hash, now_iso=self._now_iso_fn())
        return True, ""

    # ── mint / manage (T3.12 CRUD path) ─────────────────────────────

    def create_key(self, label: str | None) -> tuple[McpKbKey, str]:
        """Mint + persist one key; returns ``(row, plaintext)`` — the
        plaintext appears exactly here, once (NFR-13 hygiene).

        The returned row is a DETACHED snapshot (attributes read before
        insert) — the repository's commit expires the inserted instance
        and reading it outside its Session would raise
        DetachedInstanceError."""
        plaintext = mint_kb_key()
        row = McpKbKey(
            id=secrets.token_hex(8),
            id_hash=hash_kb_key(plaintext),
            label=label,
            created_at=self._now_iso_fn(),
        )
        snapshot = McpKbKey(
            id=row.id,
            id_hash=row.id_hash,
            label=row.label,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            revoked_at=row.revoked_at,
        )
        self._repository.insert_key(row)
        logger.info(
            "[AuthAudit] event=kb_key_created key_id=%s label=%s",
            snapshot.id,
            label or "-",
        )
        return snapshot, plaintext

    def list_keys(self, *, include_revoked: bool = False) -> list[McpKbKey]:
        return list(self._repository.list_keys(include_revoked=include_revoked))

    def get_key(self, key_id: str) -> McpKbKey | None:
        return self._repository.get_key(key_id)

    def update_label(self, key_id: str, label: str | None) -> McpKbKey | None:
        return self._repository.update_label(key_id, label)

    def revoke_key(self, key_id: str) -> bool:
        revoked = self._repository.revoke_key(key_id, now_iso=self._now_iso_fn())
        if revoked:
            logger.info("[AuthAudit] event=kb_key_revoked key_id=%s", key_id)
        return revoked

    def delete_key(self, key_id: str) -> bool:
        deleted = self._repository.delete_key(key_id)
        if deleted:
            logger.info("[AuthAudit] event=kb_key_deleted key_id=%s", key_id)
        return deleted


class KbKeyGuardApp:
    """Pure-ASGI key guard wrapped around each KB mount (T3.11).

    Empty-store / missing-store → pass through untouched (bootstrap
    posture). Otherwise ``X-KB-Key`` must verify against the module
    store — D20d rejection codes on failure.
    """

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._wrapped(scope, receive, send)
            return
        app_ref = scope.get("app")
        store = getattr(getattr(app_ref, "state", None), "mcp_kb_key_store", None)
        # Availability (P3 review): the store is synchronous SQLModel —
        # bridge through asyncio.to_thread (auth_service.py precedent)
        # so the guard never blocks the event loop on PG.
        if store is None or not await asyncio.to_thread(store.has_active_keys):
            await self._wrapped(scope, receive, send)
            return
        presented = _header_value(scope, KB_KEY_HEADER)
        ok, reject_code = await asyncio.to_thread(store.verify, presented)
        if ok:
            await self._wrapped(scope, receive, send)
            return
        logger.warning(
            "[AuthAudit] event=kb_key_rejected code=%s path=%s",
            reject_code,
            scope.get("path", "?"),
        )
        await _send_json(send, 401, {"error": reject_code})


def _header_value(scope: dict[str, Any], name: str) -> str | None:
    for raw_key, raw_value in scope.get("headers", []) or []:
        try:
            if raw_key.decode("latin-1").lower() == name:
                return raw_value.decode("latin-1")
        except (UnicodeDecodeError, AttributeError):
            continue
    return None


async def _send_json(send: Any, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


__all__ = [
    "KB_KEY_HEADER",
    "KB_KEY_PREFIX",
    "KbKeyGuardApp",
    "McpKbKeyStore",
    "hash_kb_key",
    "mint_kb_key",
]
