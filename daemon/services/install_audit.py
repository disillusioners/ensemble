"""Install-audit writer for the designer-agent bootstrap flow (P3-WP5).

Arch doc §7.4 day-1 audit: ONE JSON line per install attempt in
``install-audit.jsonl`` with the exact field set::

    {"ts", "event", "name", "actor", "parent",
     "secret_ref", "idempotency_key", "trace_id"}

``event`` is day-1 ``"mcp_install"``; ``"kms_issue"`` lines are written
by the KMS side later — this writer is generic and just takes the
event string (phase-lead fold decision). ``secret_ref`` carries the
KMS handle ONLY — plaintext NEVER rides the audit lane (§7.5
handles-not-secrets).

Path resolution (documented contract, mirrored by tests):

1. **Preferred** — ``<workdir>/.agents/shared/planning/designer-agent/install-audit.jsonl``
   where ``workdir`` is the explicit argument or, when omitted,
   ``os.getcwd()`` (the daemon's cwd is the project root in dev/ops
   lanes, so this resolves inside the feature's planning tree).
2. **Fallback** — when the preferred path is unwritable at runtime
   (missing planning tree, read-only FS, …):
   ``<data_dir>/install-audit.jsonl`` with ``data_dir`` resolved by the
   project-wide precedence chain ``ENSEMBLE_DATA_DIR > DATA_DIR >
   ./data`` (mirrors ``daemon/api.py``). The fallback is logged at
   WARNING with the OSError reason — callers can detect it via
   :attr:`InstallAuditResult.fallback_used`.

Day-1 atomicity: single-line append + flush on an ``O_APPEND`` handle.
Concurrent writers may interleave whole lines (line-buffered appends
of < PIPE_BUF size are atomic in practice on local FS) — full
temp+``mv`` atomicity + torn-write scan per the upgrade-journal
template is §7.5a deferred (arch doc).

Never raises: every failure mode returns an
:class:`InstallAuditResult` with ``written=False`` and the error text,
so a misbehaving audit lane can never fail an install request.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Preferred audit-lane location, relative to the active project/workdir.
INSTALL_AUDIT_RELATIVE_PATH = Path(
    ".agents/shared/planning/designer-agent/install-audit.jsonl"
)

#: Event vocabulary (day-1). ``kms_issue`` is reserved for the KMS side.
EVENT_MCP_INSTALL = "mcp_install"
EVENT_KMS_ISSUE = "kms_issue"

#: Exact §7.4 record field set (``ts`` added by the writer).
_AUDIT_FIELDS = (
    "ts",
    "event",
    "name",
    "actor",
    "parent",
    "secret_ref",
    "idempotency_key",
    "trace_id",
)


@dataclass
class InstallAuditResult:
    """Outcome of one :func:`append_install_audit` call. Never raises."""

    written: bool
    path: str | None = None
    fallback_used: bool = False
    error: str | None = None


def _resolve_data_dir() -> Path:
    """Data dir per the project-wide precedence chain (daemon/api.py)."""
    return Path(
        os.environ.get("ENSEMBLE_DATA_DIR")
        or os.environ.get("DATA_DIR")
        or "./data"
    )


def resolve_install_audit_path(workdir: str | Path | None = None) -> tuple[Path, bool]:
    """Resolve the audit-lane path. Returns ``(path, is_fallback)``.

    Preferred location wins when its parent tree is writable (the
    parent directory is created if missing — the planning tree exists
    in the feature worktree but the audit file itself is created on
    first append). Falls back to ``<data_dir>/install-audit.jsonl``
    when the preferred location cannot be opened for append.
    """
    base = Path(workdir) if workdir else Path.cwd()
    preferred = base / INSTALL_AUDIT_RELATIVE_PATH
    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
        # Probe writability with the same mode the append will use.
        with open(preferred, "a", encoding="utf-8"):
            pass
        return preferred, False
    except OSError as e:
        logger.warning(
            "install-audit: preferred path %r unwritable (%s); "
            "falling back to data dir",
            str(preferred),
            e,
        )
    fallback = _resolve_data_dir() / "install-audit.jsonl"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    return fallback, True


def append_install_audit(
    *,
    event: str,
    name: str,
    actor: str,
    parent: str | None = None,
    secret_ref: str | None = None,
    idempotency_key: str | None = None,
    trace_id: str | None = None,
    workdir: str | Path | None = None,
) -> InstallAuditResult:
    """Append ONE §7.4 audit line for an install event. Never raises.

    Args:
        event: Event vocabulary string (``"mcp_install"`` day-1;
            ``"kms_issue"`` reserved for the KMS side).
        name: Capability / server name the event is about (e.g.
            ``"opendesign"``).
        actor: Instance id (or system identity) performing the event.
        parent: Optional parent instance id (who dispatched the actor).
        secret_ref: KMS handle ONLY (never plaintext). ``None`` when
            the install minted no secret (zero-credential loopback).
        idempotency_key: The configure-builtin idempotency key
            (``sha256(name + schema_version + sorted(config))``).
        trace_id: Optional correlation id tying the audit line to a
            mission / job trace.
        workdir: Explicit project/workdir root for the preferred path.
            ``None`` → ``os.getcwd()``.

    Returns:
        :class:`InstallAuditResult` — ``written=True`` plus the path on
        success; ``written=False`` plus ``error`` on any failure.
    """
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "name": name,
        "actor": actor,
        "parent": parent,
        "secret_ref": secret_ref,
        "idempotency_key": idempotency_key,
        "trace_id": trace_id,
    }
    # Field hygiene: exact §7.4 set, in order, no extras.
    record = {k: record[k] for k in _AUDIT_FIELDS}

    try:
        path, is_fallback = resolve_install_audit_path(workdir)
        line = json.dumps(record, separators=(",", ":"), sort_keys=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        return InstallAuditResult(
            written=True,
            path=str(path),
            fallback_used=is_fallback,
        )
    except Exception as e:  # noqa: BLE001 — audit lane must never fail a request
        logger.error("install-audit: append failed: %s", e)
        return InstallAuditResult(written=False, error=str(e))


__all__ = [
    "INSTALL_AUDIT_RELATIVE_PATH",
    "EVENT_MCP_INSTALL",
    "EVENT_KMS_ISSUE",
    "InstallAuditResult",
    "resolve_install_audit_path",
    "append_install_audit",
]
