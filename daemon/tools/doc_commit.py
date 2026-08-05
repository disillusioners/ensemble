"""Thin tool wrapper for :class:`~daemon.services.doc_commit_service.DocCommitService`.

The blueprinter is the only authorized caller of this tool. Authorization is
enforced inside ``commit_docs_validated_tool``: any caller whose ``agent_id``
is not ``"blueprinter"`` is rejected before any subprocess runs.

Architectural posture: this tool is a STRUCTURED DATA CALL to a server-side
service — it does NOT give the blueprinter shell access. The actual
``subprocess.run`` calls happen inside :class:`DocCommitService`, server-side,
with ``shell=False`` and explicit arg lists.

The blueprinter is therefore **still bound** by the soul.md line 90 rule
"I do not execute shell commands" — calling ``commit_docs_validated`` is
analogous to calling ``blueprint_create`` (a service-mediated data op),
not to running a shell.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from daemon.services.doc_commit_service import DocCommitService

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "DocMaintenance"
CATEGORY_DOC = """Atomic doc-commit tool (blueprinter-only).

commit_docs_validated() runs an atomic validate → git commit sequence for
doc-maintenance writes. The subprocess invocations happen server-side inside
DocCommitService — the blueprinter has no shell access. Build FAIL or TIMEOUT
hard-stops the commit (changes remain in the working tree).

Only the blueprinter agent is authorized to invoke this tool.
"""


def create_doc_commit_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    agent_id: str = "",
) -> list:
    """Create commit_docs_validated tool with injected manager reference.

    Authorization: only ``blueprinter`` may invoke the tool. Other agent_ids
    receive an authorization error without any service call.
    """

    def _get_workdir() -> str | None:
        try:
            inst = manager._instance_repository.get(current_instance_id)
            if inst is not None and getattr(inst, "project_id", None):
                project_id = inst.project_id
                try:
                    project = manager._project_repository.get(project_id)
                    if project is not None and getattr(project, "workdir", None):
                        return project.workdir
                except Exception:
                    pass
        except Exception:
            pass
        fallback = getattr(manager, "workdir", None)
        return str(fallback) if fallback is not None else None

    def _get_project_metadata() -> dict:
        """Resolve project metadata dict, including doc_maintenance_* fields."""
        try:
            inst = manager._instance_repository.get(current_instance_id)
            if inst is not None and getattr(inst, "project_id", None):
                project_id = inst.project_id
                project = manager._project_repository.get(project_id)
                if project is not None:
                    meta = getattr(project, "project_metadata", None) or {}
                    if isinstance(meta, dict):
                        return meta
        except Exception:
            pass
        return {}

    @register_tool_category("doc_maintenance")
    @tool
    def commit_docs_validated(
        changed_paths: list[str],
        message: str,
    ) -> str:
        """Atomically validate (build/test) and git-commit doc-maintenance writes.

        Restricted tool — only the blueprinter agent may invoke this. The
        underlying service runs subprocesses server-side (no shell access).
        Build FAIL or TIMEOUT hard-stops the commit; changes remain in the
        working tree for review.

        Args:
            changed_paths: Paths modified by doc-maintainer workers, relative
                to the project workdir. Re-validated against the doc allowlist.
            message: Conventional commit message (e.g.,
                ``docs(blueprinter): auto-update rebuild auth [skip ci]``).

        Returns:
            Human-readable result string with status, commit hash (on success),
            or reason (on rejection).
        """
        # Authorization gate.
        if agent_id != "blueprinter":
            return (
                f"Error: UNAUTHORIZED: only the 'blueprinter' agent may call "
                f"commit_docs_validated (caller agent_id={agent_id!r})"
            )

        workdir = _get_workdir()
        if not workdir:
            return "Error: project workdir not available from instance context"

        # Opt-in gate: doc_maintenance_commit_enabled must be true.
        metadata = _get_project_metadata()
        if not metadata.get("doc_maintenance_commit_enabled", False):
            return (
                "Error: SKIPPED: doc_maintenance_commit_enabled is false "
                "(operator opt-out). Changes remain in the working tree."
            )

        if not changed_paths:
            return "Error: SKIPPED: no changed paths supplied"

        service = DocCommitService(
            workdir=workdir,
            project_metadata=metadata,
        )
        import asyncio

        try:
            result = asyncio.run(service.commit_docs_validated(changed_paths, message))
        except Exception as exc:
            logger.exception("DocCommitService raised unexpectedly")
            return f"Error: SERVICE_ERROR: {exc}"

        # Format the result for human/LLM consumption.
        lines = [
            f"Status: {result.status}",
            f"Reason: {result.reason or '(none)'}",
            f"Files: {', '.join(result.files) if result.files else '(none)'}",
            f"Commit hash: {result.commit_hash or '(none)'}",
            f"Duration: {result.duration_ms}ms",
        ]
        if result.build_output:
            lines.append(f"Build output (truncated):\n{result.build_output}")
        return "\n".join(lines)

    return [commit_docs_validated]
