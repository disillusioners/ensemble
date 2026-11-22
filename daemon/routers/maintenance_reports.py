"""Read-only report-list door for the Central Maintenance System (Phase 4).

``GET /api/maintenance-reports`` — the run list the FE Report page reads
(plan Task 4.7.4 — ``phase4-plan.md:122``; the BR-17 safety contract at
``phase4-plan.md:55``).

WHAT THIS DOOR IS
-----------------
A READ-ONLY list of the maintenance runs whose artifacts live under the
artifact root ``<live-install root>/.agents/maintenancer/RESULTS/``
(BR-16 — the LIVE-INSTALL root, never a worktree):

* ``run-<run_id>.md``     — the human-readable run report,
* ``receipt-<run_id>.md`` — the machine-readable receipt,
* ``INDEX.md``            — the chronological ledger (not a run).

The response is a JSON ARRAY of per-run objects::

    {
      "run_id": "ckpt-20261122_101500123456-a1b2c3d4",
      "trigger_type": "scheduled",
      "started_at": "2026-11-22T10:15:00Z",
      "ended_at": "2026-11-22T10:31:12Z",
      "overall_status": "ok",
      "report_view_url": "/views/maintenance-reports/run-ckpt-....md",
      "receipt_view_url": "/views/maintenance-reports/receipt-ckpt-....md"
    }

The two ``*_view_url`` fields are VIEW URLs — paths INTO the
``maintenance-reports`` live-views root (BR-18) — NEVER repo-relative
file paths. The artifact CONTENT is served by the live-views subsystem
(traversal guard + the ``.md`` renderer); this door only LISTS.

ORDER: newest run first (descending ``run_id``; the run-id prefix is a
UTC timestamp, so lexicographic order IS chronological order — the
house run-identity invariant).

SAFETY CONTRACT (BR-17)
-----------------------
1. **GET(+HEAD) only.** This module exposes no write verb — the door
   is a list, never an authoring surface (the WRITER is the
   ``run-report-writer`` worker child, never the daemon).
2. **Scope ``maintenance:read``** — the EXISTING taxonomy entry
   (``daemon/auth/scopes.py:76``); this module mints NO new scope.
3. **Fail-closed when auth is OFF** — the door returns
   ``503 AUTH_NOT_CONFIGURED`` (the tool-usage door's posture, copied
   verbatim: ``daemon/routers/tool_usage.py``).
4. **Fixed root, resolved at daemon startup.** The artifact root is
   resolved ONCE (module import == daemon startup) through the
   seeder's own helper (``maintenance_results_dir()``) — NEVER from
   ``Path.cwd()``, so a daemon launched from a worktree still reads
   its own install. The seeder (P1), this door (BR-17) and the
   live-views root registration (BR-18) therefore share ONE root
   basis BY CONSTRUCTION (plan Task 4.8 root-resolution note).
5. **The run-id is a FILENAME COMPONENT, never a path.** Every run id
   is matched against ``^ckpt-\\d{8}_\\d{12}-[0-9a-f]{8}$`` before it
   is used to build a filename, and the built path is re-checked with
   an ``os.path.realpath`` containment test against the resolved root
   (defense-in-depth — the same layered guard the live-views resolver
   applies at ``daemon/services/live_views.py:30-35``).

The receipt/report HEADER FIELDS are parsed from the artifact itself
(receipt schema — plan Task 4.3 / decisions.md PD-4a: ``run_id``,
``trigger_type``, ``started_at``, ``ended_at``, ``overall_status``).
The parser is TOLERANT of the two shapes the schema allows — plain
``key: value`` lines or a fenced YAML block — and stops at the
``lanes:`` block so a lane entry's own keys can never be mistaken for
a header field.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from daemon._redact import redact_exc_str
from daemon.auth.decorators import scope_required
from daemon.repositories.source.maintenance_seed import maintenance_results_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maintenance-reports", tags=["maintenance-reports"])

#: The read scope — REUSED from the v1 taxonomy (``daemon/auth/
#: scopes.py:76``). This module mints NO new scope.
_SCOPE_READ = "maintenance:read"

_ERR_AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
_ERR_UNAVAILABLE = "maintenance_reports_unavailable"

#: The 1 MB serialized-response guard — the tool-usage door's abuse
#: bound, copied verbatim (a long-lived install accumulates one entry
#: per weekly run).
MAX_RESPONSE_BYTES = 1_000_000

#: BR-17 — the run-id shape. A run id is used ONLY as a filename
#: component under the fixed root; it never becomes a path segment.
RUN_ID_PATTERN = re.compile(r"^ckpt-\d{8}_\d{12}-[0-9a-f]{8}$")

#: BR-18 — the live-views root NAME (a named root, sibling of
#: ``designer-artifact`` / ``planning`` / ``tmp-images``).
LIVE_VIEW_ROOT_NAME = "maintenance-reports"

#: BR-18 — the live-views ALLOWLIST: the ``.md`` renderer only …
LIVE_VIEW_ALLOWED_EXTENSIONS = ("md",)

#: … restricted to these filename patterns (the three artifact shapes
#: BR-16 defines; nothing else under the root is served).
LIVE_VIEW_FILENAME_PATTERNS = ("run-*.md", "receipt-*.md", "INDEX.md")

#: The URL prefix the live-views router is mounted under (the
#: ``/views`` family is mounted on the APP, not on ``api_router`` —
#: ``daemon/routers/live_views.py``).
_VIEWS_URL_PREFIX = "/views"

#: The receipt/report header fields (plan Task 4.3 / decisions.md
#: PD-4a). Everything else in the artifact is lane detail.
_HEADER_FIELDS = (
    "run_id",
    "trigger_type",
    "started_at",
    "ended_at",
    "overall_status",
)

#: The artifact-root directory — resolved ONCE, at module import
#: (== daemon startup). NEVER re-resolved per request: a per-request
#: resolution would reintroduce the cwd drift BR-17 forbids.
RESULTS_ROOT: Path = maintenance_results_dir()


def _err(status_code: int, error: str, message: str) -> HTTPException:
    """Typed ``{error, message}`` body — the read-door shape, verbatim."""
    return HTTPException(
        status_code=status_code, detail={"error": error, "message": message}
    )


def _require_auth_on(request: Request) -> None:
    """The fail-closed posture (the tool-usage door's, copied verbatim).

    ``create_app`` sets ``app.state.auth_config`` ONLY when auth is
    enabled; its absence is the authoritative OFF signal. In that
    posture the door must NOT be served — the deployed default is
    auth-OFF on a ``0.0.0.0`` bind with permissive CORS, so an open
    route here would publish the maintenance run history to anyone
    who can reach the port.
    """
    cfg = getattr(getattr(request.app, "state", None), "auth_config", None)
    if cfg is None:
        raise _err(
            503,
            _ERR_AUTH_NOT_CONFIGURED,
            "Auth not configured (fail-closed)",
        )


def _size_guard(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reject an oversized serialized response (the 1 MB abuse bound)."""
    encoded = json.dumps(payload, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "the maintenance report list exceeded the size bound",
        )
    return payload


def _contained_path(root: Path, filename: str) -> Path | None:
    """Resolve ``root/filename`` under the BR-17 containment guard.

    The filename is built from a REGEX-VALIDATED run id (never from
    caller input), so it cannot carry a separator; the realpath check
    is the defense-in-depth layer behind that (a symlink inside the
    root cannot escape it). Returns ``None`` when the candidate is
    outside the resolved root.
    """
    try:
        root_real = os.path.realpath(str(root))
        candidate_real = os.path.realpath(str(root / filename))
    except OSError:
        return None
    if candidate_real == root_real:
        return None
    if not candidate_real.startswith(root_real + os.sep):
        return None
    return Path(candidate_real)


def _parse_header_fields(text: str) -> dict[str, str]:
    """Extract the receipt/report HEADER fields from an artifact.

    Tolerant of both shapes the schema allows (plan Task 4.3): plain
    ``key: value`` lines OR a fenced YAML block — both carry the same
    zero-indent header keys. The scan stops at the ``lanes:`` /
    ``recommendations:`` block so a lane entry's own keys (PD-4a:
    ``lane``, ``status``, ``measured_at``, ``summary``, ``metrics``,
    ``actions_taken``, ``recommendations``, ``evidence_paths``) can
    never be mistaken for a header field.
    """
    fields: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        if key in ("lanes", "recommendations"):
            # The header block ends here.
            break
        if key not in _HEADER_FIELDS or key in fields:
            continue
        cleaned = value.strip().strip('"').strip("'").strip()
        if cleaned:
            fields[key] = cleaned
    return fields


def _read_header(root: Path, filename: str) -> dict[str, str]:
    """Read + parse one artifact's header (empty dict on any miss)."""
    path = _contained_path(root, filename)
    if path is None:
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug(
            "[MaintenanceReports] could not read %s: %s",
            filename,
            exc,
        )
        return {}
    return _parse_header_fields(text)


def _view_url(filename: str) -> str:
    """The VIEW URL for an artifact — a path INTO the live-views root."""
    return f"{_VIEWS_URL_PREFIX}/{LIVE_VIEW_ROOT_NAME}/{filename}"


def _collect_runs(root: Path) -> list[dict[str, Any]]:
    """Enumerate the runs under the fixed root (newest first).

    A run is present when EITHER artifact exists (``run-<id>.md`` or
    ``receipt-<id>.md``) — a partially written run (the write order is
    run → receipt → INDEX) is still a run. Any filename whose run id
    does not match :data:`RUN_ID_PATTERN` is ignored: it is not a
    maintenance artifact, and an unvalidated id never reaches a path.
    """
    if not root.is_dir():
        logger.warning(
            "[MaintenanceReports] the artifact root %s does not exist — "
            "no runs to list",
            root,
        )
        return []

    artifacts: dict[str, dict[str, str]] = {}
    try:
        entries = sorted(root.iterdir())
    except OSError as exc:
        logger.warning(
            "[MaintenanceReports] could not list the artifact root %s: %s",
            root,
            exc,
        )
        return []

    for entry in entries:
        name = entry.name
        if not entry.is_file():
            continue
        if name == "INDEX.md":
            continue
        if name.startswith("run-") and name.endswith(".md"):
            kind = "report"
            run_id = name[len("run-") : -len(".md")]
        elif name.startswith("receipt-") and name.endswith(".md"):
            kind = "receipt"
            run_id = name[len("receipt-") : -len(".md")]
        else:
            continue
        if not RUN_ID_PATTERN.match(run_id):
            logger.debug(
                "[MaintenanceReports] ignoring %s — the run id does not "
                "match the canonical shape",
                name,
            )
            continue
        artifacts.setdefault(run_id, {})[kind] = name

    rows: list[dict[str, Any]] = []
    for run_id in sorted(artifacts, reverse=True):
        names = artifacts[run_id]
        receipt_name = names.get("receipt")
        report_name = names.get("report")
        # The receipt is the machine-readable record; the report is the
        # fallback carrier of the same header fields.
        header: dict[str, str] = {}
        if receipt_name:
            header = _read_header(root, receipt_name)
        if report_name and "overall_status" not in header:
            report_header = _read_header(root, report_name)
            for key, value in report_header.items():
                header.setdefault(key, value)

        rows.append(
            {
                "run_id": run_id,
                "trigger_type": header.get("trigger_type"),
                "started_at": header.get("started_at"),
                "ended_at": header.get("ended_at"),
                "overall_status": header.get("overall_status"),
                "report_view_url": _view_url(
                    report_name or f"run-{run_id}.md"
                ),
                "receipt_view_url": _view_url(
                    receipt_name or f"receipt-{run_id}.md"
                ),
            }
        )
    return rows


@router.get(
    "",
    dependencies=[scope_required(_SCOPE_READ)],
    responses={
        200: {"description": "The maintenance runs, newest first."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {"description": "The caller lacks the maintenance:read scope."},
        503: {"description": "Auth is OFF (fail-closed) or the root is unreadable."},
    },
)
async def list_maintenance_reports(request: Request) -> Any:
    """``GET /api/maintenance-reports`` — the run list (read-only)."""
    _require_auth_on(request)
    try:
        rows = await asyncio.to_thread(_collect_runs, RESULTS_ROOT)
        return _size_guard(rows)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — the door never 500s
        logger.warning(
            "maintenance report list failed: %s", redact_exc_str(exc, limit=200)
        )
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "the maintenance reports could not be read",
        ) from exc


@router.head(
    "",
    dependencies=[scope_required(_SCOPE_READ)],
    responses={
        200: {"description": "The same status semantics as the GET list."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {"description": "The caller lacks the maintenance:read scope."},
        503: {"description": "Auth is OFF (fail-closed) or the root is unreadable."},
    },
)
async def head_maintenance_reports(request: Request) -> Response:
    """``HEAD /api/maintenance-reports`` — the list's status semantics.

    Mirrors the GET handler (the house live-views precedent — a
    separate HEAD declaration beside the GET one): the SAME gate, the
    SAME scope check and the SAME size bound, with the body dropped (a
    HEAD response never carries one).
    """
    _require_auth_on(request)
    try:
        rows = await asyncio.to_thread(_collect_runs, RESULTS_ROOT)
        _size_guard(rows)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — the door never 500s
        logger.warning(
            "maintenance report list failed: %s", redact_exc_str(exc, limit=200)
        )
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "the maintenance reports could not be read",
        ) from exc
    return Response(status_code=200, content=b"", media_type="application/json")


__all__ = [
    "router",
    "RESULTS_ROOT",
    "RUN_ID_PATTERN",
    "LIVE_VIEW_ROOT_NAME",
    "LIVE_VIEW_ALLOWED_EXTENSIONS",
    "LIVE_VIEW_FILENAME_PATTERNS",
    "MAX_RESPONSE_BYTES",
]
