"""The ``wedge-capture`` privileged tool category (2 read-only tools).

Two tools
---------
``wedge_capture_list`` — the recent captures (timestamp, reason, host,
summary + the short metrics summary).
``wedge_capture_get``  — ONE capture's full payload.

ACCESS POSTURE — TWO LAYERS, BOTH REQUIRED (D29)
--------------------------------------------------
1. The ``wedge-capture`` category is PRIVILEGED, so it never joins the
   empty-allow default-grant universe.
2. A privileged category alone is NOT sufficient — a **literal tool
   name** in an allow list bypasses the category gate entirely. So
   every tool body ALSO calls the one shared module-level helper
   :func:`_is_wedge_capture_authorized`.

Both gates are required; neither substitutes for the other. The
allow-list is ``{ari, leader, maintenancer}`` — the capture payload is
a privileged diagnostic artifact (all-thread stack dumps, per-thread CPU
maps, process RSS, the DB-side executing turns) and has no business in
a general instance-reading surface.

TOOLS NEVER RAISE
-----------------
Every failure returns a typed refusal string (the house envelope). The
manual trigger is deliberately **NOT** exposed here: an agent must not
be able to force captures. It stays on the admin API / CLI.

THE READS HAVE NO SIDE EFFECT
-----------------------------
A read writes NO audit row, deletes NOTHING, and triggers NOTHING.

THE FILE ENUMERATION IS PINNED (MR-5)
-------------------------------------
The fetch tool reads EXACTLY the D8 artifact file set. It never lists
a directory's contents to decide what to read — so an unexpected entry
(the ``.pending_row.json`` reconcile sidecar, a stray file) can never
leak into the evidence payload.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from daemon.services.wedge_capture.writer import (
    CAPTURE_FILE_NAMES,
    CAPTURE_ID_PATTERN,
    MAX_ARTIFACT_BYTES,
    _read_artifact,
)

from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Wedge Capture"
CATEGORY_DOC = """\
Read-only views over the wedge-capture evidence store.

Tools in this category:
- ``wedge_capture_list`` — the recent captures (one row per capture)
- ``wedge_capture_get``  — ONE capture's full payload

These tools READ. They never write, never delete, and never trigger a
capture (the manual trigger stays on the admin API / CLI, so an agent
cannot fill the disk).

A capture is EVIDENCE, never a remedy — this instrument fixes
nothing. Reading a capture tells you what the daemon SAW; it does not
repair anything.
"""

#: The agents allowed to read the wedge-capture store. The privileged
#: category gates VISIBILITY; this set gates AUTHORITY. Both are
#: required.
WEDGE_CAPTURE_AUTHORIZED_AGENTS: frozenset[str] = frozenset(
    {"ari", "leader", "maintenancer"}
)

#: The D8 artifact file set — the fetch payload carries EXACTLY these
#: (the ``.pending_row.json`` sidecar is EXCLUDED — MR-5). The
#: canonical tuple lives in the writer (D8 single-sourcing); this
#: alias keeps this surface's historical name.
ARTIFACT_FILE_NAMES = CAPTURE_FILE_NAMES

#: The default number of list rows.
DEFAULT_LIST_LIMIT = 20

#: The two tool names this module constructs — the canonical list the
#: registry's ``KNOWN_TOOL_NAMES`` / ``DYNAMIC_TOOL_NAMES`` and the
#: registration pin pack agree with.
TOOL_NAMES: frozenset[str] = frozenset({
    "wedge_capture_list",
    "wedge_capture_get",
})


def _is_wedge_capture_authorized(agent_id: str) -> bool:
    """Is this agent allowed to read the wedge-capture store?

    ONE shared helper, called by BOTH tool bodies — the set lives in
    exactly one place and the bodies share it (the
    ``daemon/tools/instance_read.py`` / ``views_auth.py`` precedent).

    Fail-closed: a blank or non-``str`` ``agent_id`` is NEVER
    authorized.
    """
    if not isinstance(agent_id, str) or not agent_id:
        return False
    return agent_id in WEDGE_CAPTURE_AUTHORIZED_AGENTS


def _refusal(label: str, token: str, message: str) -> str:
    """The house refusal envelope.

    ``Error: {label} REFUSED — reason={token}: {message}`` — the same
    shape the other privileged categories use, so one parser reads
    them all.
    """
    return f"Error: {label} REFUSED — reason={token}: {message}"


def _error(msg: str) -> str:
    """House error envelope — tools never raise."""
    return f"ERROR: {msg}"


def _resolve_root(manager: Any) -> Path:
    """The capture root for the running daemon."""
    watchdog = getattr(manager, "_wedge_capture_watchdog", None)
    if watchdog is not None:
        config = getattr(watchdog, "_config", None)
        if config is not None:
            return Path(config.capture_dir)
    from daemon.services.wedge_capture.config import WedgeCaptureConfig

    return Path(WedgeCaptureConfig.from_env().capture_dir)


def create_wedge_capture_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the two ``wedge-capture`` tools for one instance/agent.

    Args:
        manager: The ``InstanceManager`` — used only to resolve the
            capture directory root. The tools read the capture
            directory ONLY; the metadata table is the HTTP router's
            path, never the agent tools'. They never write.
        current_instance_id: The caller's instance id (unused by the
            reads; kept for factory-signature parity).
        agent_id: The caller's agent id. Captured at factory time and
            checked by :func:`_is_wedge_capture_authorized` in every
            tool body.
        agent_tag: The caller's version tag (unused; signature parity).

    Returns:
        A list of the two ``@tool``-decorated callables.
    """
    caller_agent_id = agent_id or ""

    class WedgeCaptureListInput(BaseModel):
        """Input schema for wedge_capture_list."""

        limit: Annotated[
            int,
            Field(
                default=DEFAULT_LIST_LIMIT,
                description=(
                    f"Max rows to return (1-200, default {DEFAULT_LIST_LIMIT})."
                ),
            ),
        ]

    @register_tool_category("wedge-capture")
    @tool(args_schema=WedgeCaptureListInput)
    async def wedge_capture_list(limit: int = DEFAULT_LIST_LIMIT) -> str:
        """Recent wedge captures — one row per capture. Use tool_help("wedge_capture_list") for details."""
        if not _is_wedge_capture_authorized(caller_agent_id):
            return _refusal(
                "wedge_capture_list",
                "not-authorized",
                "the wedge-capture store is readable by ari, leader and "
                "maintenancer only",
            )
        try:
            root = _resolve_root(manager)
            rows = _list_captures(root, max(1, min(int(limit), 200)))
            return json.dumps(
                {"captures": rows, "count": len(rows), "root": str(root)},
                indent=2,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001 — tools never raise
            logger.warning("wedge_capture_list failed: %s", exc)
            return _error(f"wedge_capture_list failed: {exc}")

    class WedgeCaptureGetInput(BaseModel):
        """Input schema for wedge_capture_get."""

        capture_id: Annotated[
            str,
            Field(
                description=(
                    "The capture id — the capture directory's name "
                    "(<UTC-ts>-<host>-<reason>-<uuid8>)."
                ),
            ),
        ]

    @register_tool_category("wedge-capture")
    @tool(args_schema=WedgeCaptureGetInput)
    async def wedge_capture_get(capture_id: str) -> str:
        """One wedge capture's full payload — manifest + every artifact. Use tool_help("wedge_capture_get") for details."""
        if not _is_wedge_capture_authorized(caller_agent_id):
            return _refusal(
                "wedge_capture_get",
                "not-authorized",
                "the wedge-capture store is readable by ari, leader and "
                "maintenancer only",
            )
        if not isinstance(capture_id, str) or not CAPTURE_ID_PATTERN.match(capture_id):
            return _refusal(
                "wedge_capture_get",
                "not-found",
                f"no capture matches id {capture_id!r}",
            )
        try:
            root = _resolve_root(manager)
            capture_dir = (root / capture_id).resolve()
            resolved_root = root.resolve()
            if resolved_root != capture_dir and resolved_root not in capture_dir.parents:
                return _refusal(
                    "wedge_capture_get",
                    "not-found",
                    f"no capture matches id {capture_id!r}",
                )
            if not capture_dir.is_dir():
                return _refusal(
                    "wedge_capture_get",
                    "not-found",
                    f"no capture matches id {capture_id!r}",
                )
            manifest = {}
            manifest_path = capture_dir / "manifest.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    manifest = {}
            artifacts: dict[str, Any] = {}
            for name in ARTIFACT_FILE_NAMES:
                artifact_path = capture_dir / name
                if not artifact_path.exists():
                    continue
                artifacts[name] = _read_artifact(artifact_path)
            return json.dumps(
                {
                    "capture_id": capture_id,
                    "capture_dir": str(capture_dir),
                    "manifest": manifest,
                    "artifacts": artifacts,
                },
                indent=2,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001 — tools never raise
            logger.warning("wedge_capture_get failed: %s", exc)
            return _error(f"wedge_capture_get failed: {exc}")

    return [wedge_capture_list, wedge_capture_get]


def _list_captures(root: Path, limit: int) -> list[dict[str, Any]]:
    """The recent captures — FILESYSTEM-ONLY (the manifest walk).

    The capture directory is the authority (D23): this tool walks
    ``manifest.json`` files directly and NEVER consults the metadata
    table, so the list works even when the DB is the thing that is
    wedged.
    """
    rows: list[dict[str, Any]] = []
    try:
        from daemon.services.wedge_capture.writer import capture_directories

        # Materialise BEFORE slicing: ``reversed()`` yields an iterator,
        # and slicing it raises TypeError — which silently emptied the
        # tool's list (the same defect the router's fallback carried).
        newest_first = list(reversed(capture_directories(root)))
        for directory in newest_first[:limit]:
            manifest_path = directory / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
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
    except Exception as exc:  # noqa: BLE001
        logger.warning("wedge_capture_list scan failed: %s", exc)
    return rows


__all__ = [
    "ARTIFACT_FILE_NAMES",
    "CATEGORY_DOC",
    "CATEGORY_NAME",
    "CAPTURE_ID_PATTERN",
    "DEFAULT_LIST_LIMIT",
    "MAX_ARTIFACT_BYTES",
    "TOOL_NAMES",
    "WEDGE_CAPTURE_AUTHORIZED_AGENTS",
    "create_wedge_capture_tools",
]
