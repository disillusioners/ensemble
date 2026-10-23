"""The wedge-capture ADMIN API (D32 / D33 / D37 / D38 / A-MR-a / T19 / T24).

Four endpoints under the prefix ``/wedge-captures`` (mounted at
``/api/wedge-captures``):

===========================  ======  ==================================
Endpoint                    Method  What it does
===========================  ======  ==================================
``""``                      GET     the capture LIST
``/status``                 GET     the watchdog status + the
                                    effective auto-continue mode
``/capture``                POST    a MANUAL capture — 202 + a
                                    pre-allocated id, NEVER an
                                    in-request completion
``/{capture_id}``           GET     ONE capture's full payload
===========================  ======  ==================================

THE MANUAL CAPTURE NEVER RUNS IN-REQUEST (D38 / RK-38)
-------------------------------------------------------
The handler validates, ENQUEUES into the depth-1 slot and returns
**HTTP 202** with the pre-allocated capture id + directory. The
WATCHDOG THREAD services the request on its next tick. A capture
performed inline would block the event loop for up to the 20 s
capture deadline — exactly the event-loop-starvation class this
instrument exists to catch — and would create a SECOND
concurrent-writer class racing the watchdog for the same directory,
row, lock and rate-cap counter.

The three typed refusals (the house family):

* ``watchdog_disabled`` — the kill-switch is off OR the watchdog is
  stopped. Nothing is enqueued; no capture, no row.
* ``manual_rate_limited`` — a third request inside the same minute.
  Refused AT ENQUEUE, before anything is queued.
* ``capture_busy`` — the depth-1 slot is occupied. Nothing is
  enqueued, **no 202** is returned, and there is **NO COALESCING** —
  the refused operator retries.

THE READS NEVER WAIT BEHIND A CAPTURE (D37 / A-MR-a)
------------------------------------------------------
Every watchdog-derived field is read from a SNAPSHOT taken under the
watchdog's lock, and the lock is NEVER held across a capture step —
so a status or list read never waits behind a capture in flight.

THE PATH-TRAVERSAL GUARD (D40)
-------------------------------
A capture id is matched against a strict pattern (the canonical
reason tokens) and the resolved path is re-checked to be INSIDE the
capture root. An unknown id returns the house ``not_found`` envelope —
never a path-traversal attempt.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from daemon.auth.decorators import scope_required
from daemon.routers.maintenance_origin_guard import (
    require_trusted_origin as _require_origin,
)
from daemon.services.wedge_capture.writer import (
    CAPTURE_FILE_NAMES,
    CAPTURE_ID_PATTERN,
    MAX_ARTIFACT_BYTES,
    _read_artifact,
)

logger = logging.getLogger(__name__)

#: The D8 artifact file set — the fetch payload carries EXACTLY these
#: (MR-5: the ``.pending_row.json`` sidecar is a reconcile artifact,
#: not capture evidence, and is never included). The canonical tuple
#: lives in the writer (D8 single-sourcing); this alias keeps this
#: surface's historical name.
ARTIFACT_FILE_NAMES = CAPTURE_FILE_NAMES

#: The router — mounted under ``/api/wedge-captures``.
router = APIRouter(prefix="/wedge-captures", tags=["wedge-captures"])


def _envelope(error: str, message: str, **details: Any) -> dict[str, Any]:
    """The house error envelope (the A-8 ratified shape)."""
    return {"error": error, "message": message, "details": details}


def _fail(status_code: int, error: str, message: str, **details: Any) -> HTTPException:
    """Build the house HTTPException."""
    return HTTPException(
        status_code=status_code,
        detail=_envelope(error, message, **details),
    )


def _watchdog(request: Request) -> Any:
    """The watchdog instance from ``app.state`` (or ``None``)."""
    state = getattr(request.app, "state", None)
    return getattr(state, "wedge_capture_watchdog", None) if state else None


def _capture_root(request: Request) -> Path:
    """The ABSOLUTE capture root (the watchdog's resolved config)."""
    watchdog = _watchdog(request)
    if watchdog is not None:
        config = getattr(watchdog, "_config", None)
        if config is not None:
            return Path(config.capture_dir)
    state = getattr(request.app, "state", None)
    data_dir = getattr(state, "data_dir", None) if state else None
    from daemon.services.wedge_capture.config import WedgeCaptureConfig

    return Path(WedgeCaptureConfig.from_env(data_dir=data_dir).capture_dir)


def _repository(request: Request) -> Any:
    """The ``wedge_captures`` repository (or ``None``)."""
    watchdog = _watchdog(request)
    if watchdog is not None:
        repository = getattr(watchdog, "_repository", None)
        if repository is not None:
            return repository
    state = getattr(request.app, "state", None)
    manager = getattr(state, "manager", None) if state else None
    engine = getattr(manager, "engine", None)
    if engine is None:
        return None
    try:
        from daemon.repositories.wedge_captures.repository import (
            WedgeCaptureRepository,
        )

        return WedgeCaptureRepository(engine)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wedge-capture: repository unavailable: %s", exc)
        return None


def _directory_manifest(capture_dir: Path) -> dict[str, Any] | None:
    """Read one capture's manifest from disk."""
    manifest_path = capture_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


# ── the list ────────────────────────────────────────────────────────────────


@router.get(
    "",
    dependencies=[scope_required("maintenance:read")],
    responses={
        200: {"description": "The capture list"},
        403: {"description": "Scope guard refused"},
    },
)
async def list_captures(request: Request, limit: int = 50) -> dict[str, Any]:
    """The recent captures.

    The TABLE is the primary source; when it is unreachable the
    LIST falls back to scanning the capture DIRECTORY (D12) — a
    capture whose row write failed is still discoverable.
    """
    root = _capture_root(request)
    repository = _repository(request)
    rows: list[dict[str, Any]] = []
    source = "table"
    if repository is not None:
        try:
            for row in repository.list_recent(limit=max(1, min(int(limit), 500))):
                payload = repository.row_to_dict(row)
                if payload:
                    rows.append(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wedge-capture: list read failed: %s", exc)
            rows = []
    if not rows:
        source = "filesystem"
        from daemon.services.wedge_capture.writer import capture_directories

        # ``reversed(list)`` is an ITERATOR — slicing it raises
        # ``TypeError`` and would take the whole D12 filesystem fallback
        # (the most valuable recovery path: a capture whose row write
        # failed) down with it. Materialise first, slice second.
        newest_first = list(reversed(capture_directories(root)))
        for directory in newest_first[: max(1, int(limit))]:
            manifest = _directory_manifest(directory)
            if manifest is None:
                continue
            rows.append(
                {
                    "capture_id": manifest.get("capture_id", directory.name),
                    "captured_at": manifest.get("captured_at"),
                    "trigger_reason": manifest.get("trigger_reason"),
                    "trigger_source": manifest.get("trigger_source"),
                    "boot_epoch": manifest.get("boot_epoch"),
                    "host": manifest.get("host"),
                    "summary": manifest.get("summary"),
                    "auto_continue_mode": manifest.get("auto_continue_mode"),
                    "file_count": manifest.get("file_count"),
                    "total_bytes": manifest.get("total_bytes"),
                    "capture_dir": str(directory),
                }
            )
    return {"captures": rows, "count": len(rows), "source": source}


# ── the status ──────────────────────────────────────────────────────────────


@router.get(
    "/status",
    dependencies=[scope_required("maintenance:read")],
    responses={
        200: {"description": "The watchdog status + the effective mode"},
        403: {"description": "Scope guard refused"},
    },
)
async def get_status(request: Request) -> dict[str, Any]:
    """The watchdog status + the effective AutoContinue mode (D37).

    Every field is ABSENT-WHEN-N/A (never a ``null`` where a concrete
    value is known). The effective mode comes from the boot RECORD —
    what the boot pass read at its boot, never a re-derived
    precedence chain (BC-1 / D16).
    """
    watchdog = _watchdog(request)
    if watchdog is None:
        return {
            "enabled": False,
            "running": False,
            "auto_continue_mode": "unknown",
            "note": (
                "the wedge-capture watchdog is not running "
                "(kill-switch off, or the daemon booted without it)"
            ),
        }
    try:
        return watchdog.status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wedge-capture: status read failed: %s", exc)
        raise _fail(500, "status_failed", f"the status read failed: {exc}")


# ── the manual capture ──────────────────────────────────────────────────────


@router.post(
    "/capture",
    status_code=202,
    dependencies=[Depends(_require_origin), scope_required("maintenance:read")],
    responses={
        202: {"description": "The capture was ENQUEUED (not completed)"},
        403: {"description": "The Origin guard or the scope guard refused"},
        409: {"description": "capture_busy / manual_rate_limited"},
        503: {"description": "watchdog_disabled"},
    },
)
async def post_capture(request: Request) -> dict[str, Any]:
    """ENQUEUE a manual capture — **202 Accepted**, never an in-request run.

    The response carries the **pre-allocated** capture id + the
    directory the capture will be written to. The operator reads the
    outcome through the list / fetch surface — the CLI never polls.
    """
    watchdog = _watchdog(request)
    if watchdog is None:
        raise _fail(
            503,
            "watchdog_disabled",
            "the wedge-capture watchdog is not running — the kill-switch "
            "is off or the daemon booted without it",
        )
    try:
        accepted, refusal, detail = watchdog.enqueue_manual_capture()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wedge-capture: manual capture enqueue failed: %s", exc)
        raise _fail(500, "capture_failed", f"the enqueue failed: {exc}")

    if not accepted:
        status_code = 503 if refusal == "watchdog_disabled" else 409
        raise _fail(
            status_code,
            refusal,
            {
                "capture_busy": (
                    "the manual-capture slot is occupied — nothing was "
                    "queued and no capture was started; retry"
                ),
                "manual_rate_limited": (
                    "the manual-capture rate cap (2/minute) was reached — "
                    "nothing was queued; retry shortly"
                ),
                "watchdog_disabled": (
                    "the wedge-capture watchdog is disabled — nothing was "
                    "queued and no capture was started"
                ),
            }.get(refusal, "the request was refused"),
            **detail,
        )

    return {
        "status": "accepted",
        "capture_id": detail.get("capture_id"),
        "capture_dir": detail.get("capture_dir"),
        "reason": detail.get("reason", "manual"),
        "enqueued": True,
        "note": detail.get("note"),
    }


# ── the fetch ───────────────────────────────────────────────────────────────


@router.get(
    "/{capture_id}",
    dependencies=[scope_required("maintenance:read")],
    responses={
        200: {"description": "One capture's full payload"},
        403: {"description": "Scope guard refused"},
        404: {"description": "not_found"},
    },
)
async def get_capture(request: Request, capture_id: str) -> dict[str, Any]:
    """One capture's FULL payload (the manifest + every artifact).

    The id is resolved against the TABLE first and the FILESYSTEM
    second (the D12 fallback). The id must match the strict capture-id
    pattern and the resolved path must be INSIDE the capture root —
    a traversal attempt is refused, never attempted.
    """
    root = _capture_root(request)
    if not CAPTURE_ID_PATTERN.match(capture_id or ""):
        raise _fail(
            404,
            "not_found",
            f"no capture matches id {capture_id!r}",
        )

    repository = _repository(request)
    row = None
    if repository is not None:
        try:
            row = repository.get(capture_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wedge-capture: fetch read failed: %s", exc)
            row = None

    capture_dir: Path | None = None
    row_dir = getattr(row, "capture_dir", None) if row is not None else None
    if isinstance(row_dir, str) and row_dir.strip():
        # A row value that is not a usable path string is IGNORED (the
        # filesystem fallback still applies) — a read must never 500 on
        # a malformed row (AC28's "never a 500" posture).
        capture_dir = Path(row_dir)
    else:
        candidate = root / capture_id
        if candidate.exists():
            capture_dir = candidate

    if capture_dir is None:
        raise _fail(
            404,
            "not_found",
            f"no capture matches id {capture_id!r}",
        )

    try:
        resolved = capture_dir.resolve()
        root_resolved = root.resolve()
    except OSError as exc:
        raise _fail(500, "capture_failed", f"the capture path is unreadable: {exc}")
    if root_resolved != resolved and root_resolved not in resolved.parents:
        raise _fail(
            404,
            "not_found",
            f"no capture matches id {capture_id!r}",
        )

    manifest = _directory_manifest(resolved) or {}
    artifacts: dict[str, Any] = {}
    for name in ARTIFACT_FILE_NAMES:
        artifact_path = resolved / name
        if not artifact_path.exists():
            continue
        artifacts[name] = _read_artifact(artifact_path)

    payload: dict[str, Any] = {
        "capture_id": capture_id,
        "capture_dir": str(resolved),
        "manifest": manifest,
        "artifacts": artifacts,
    }
    if row is not None:
        payload["row"] = repository.row_to_dict(row)
        payload["auto_continue_mode"] = getattr(row, "auto_continue_mode", None)
    else:
        payload["row"] = None
        payload["auto_continue_mode"] = manifest.get("auto_continue_mode")
    return payload


__all__ = [
    "ARTIFACT_FILE_NAMES",
    "CAPTURE_ID_PATTERN",
    "MAX_ARTIFACT_BYTES",
    "router",
]
