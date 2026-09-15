"""LangChain tool surface for the ``service`` tool category (Phase 1.B).

Five tools exposed to agents:

* :func:`service_start` — start a long-lived detached process
* :func:`service_stop` — stop a service by name (SIGTERM → 5s → SIGKILL)
* :func:`service_status` — live liveness reconciliation per row
* :func:`service_list` — list all tracked rows with inline reconciliation
* :func:`service_logs` — tail the stdio log file for a service

Each tool is registered with the ``"service"`` category via
:func:`daemon.tools._tool_registry.register_tool_category` and carries
a ``_full_doc_`` attribute (the convention ``proc_tools.py:2154-2169``
and ``daemon/tools/bash.py:415-433`` establish). The category lands in
``PRIVILEGED_TOOL_CATEGORIES`` via Phase 1.C.3 — the tools are
default-deny and reachable only through an explicit ``tools.allow``
entry naming ``service``.

Factory: :func:`create_service_tools` mirrors the ``create_proc_tools``
pattern at ``daemon/tools/proc_tools.py:1872-1900``:

* Returns ``[]`` for a falsy ``current_instance_id`` (so loader
  ``None``-manager stubs don't import half-configured toolsets — the
  precedent is the loader's warm-list pattern at
  ``daemon/loader.py:79-90``).
* Dereferences ``manager._service_tool_manager`` at CALL time, not at
  construction time (the manager may be None at factory-call time
  during early daemon boot; the actual call resolves it later when the
  tool is invoked).

Decorator order is **PINNED**: ``@register_tool_category("service")``
OUTER, ``@tool`` INNER. The langchain ``@tool`` wrapper preserves the
inner-function attributes via ``functools.wraps`` — but ONLY if the
``register`` decorator ran FIRST on the raw function. The
``test_attestation_registration.py:128-140`` canonical pin (A14
amendment) asserts this ordering by source-grep.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Optional

from langchain_core.tools import tool
from pydantic import Field

from ._tool_registry import register_tool_category
from .service_spawner import DEFAULT_TAIL_LINES

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Module-level attrs (consumed by tool_help + the registry)
# ─────────────────────────────────────────────────────────────────────


#: Human-readable category name (the precedent at
#: ``daemon/tools/proc_tools.py:66`` uses ``"Background Processes"``).
#: The ``service`` category is "Service" — long-lived detached
#: processes (dev servers, databases, watchers).
CATEGORY_NAME: str = "Service"

#: One-paragraph category doc for ``tool_help(category=...)`` and
#: ``list_tools_by_category()`` projections.
CATEGORY_DOC: str = """\
Long-lived detached processes (dev servers, databases, watchers) that
survive instance lifecycle and daemon restart. NOT an OS service —
no launchd / systemd / launchctl integration; the daemon simply
tracks the process and reaps it on explicit ``service_stop``.

**When to use ``service_*`` vs ``proc_*``**:
- ``proc_*``: instance-scoped background processes (killed on
  instance cleanup); tracked in-memory; per-instance cap of 10.
- ``service_*``: daemon-scoped detached processes (survive instance
  termination AND daemon restart); tracked in the
  ``service_tracking`` DB table; daemon-global cap of 10.
"""


# ─────────────────────────────────────────────────────────────────────
# Tool factory
# ─────────────────────────────────────────────────────────────────────


def create_service_tools(
    manager,
    current_instance_id: str,
    agent_id: str = "",
    version_tag: Optional[str] = None,
) -> list:
    """Create the five ``service_*`` tools scoped to the instance.

    Args:
        manager: The :class:`daemon.manager.InstanceManager` instance.
            Tools dereference ``manager._service_tool_manager`` at
            CALL time (NOT at construction) so a None-stub manager
            works for the loader warm-list.
        current_instance_id: Owning instance id (used for forensic
            audit trails in the ``started_by_instance_id`` column).
            Falsy ⇒ return ``[]`` (no tools; matches
            ``create_proc_tools`` precedent at
            ``daemon/tools/proc_tools.py:1896-1900``).
        agent_id: Owner agent name (recorded in
            ``started_by_agent_id``). Defaults to ``""``.
        version_tag: Optional version tag (used by versioned agent
            tool-filter resolution; mirror ``create_instance_tools``
            precedent at ``daemon/tools/instance.py:1816-1819``).

    Returns:
        A list of the five LangChain tool functions. Empty list when
        ``current_instance_id`` is falsy.
    """
    if not current_instance_id:
        # No instance context — return an empty list rather than a
        # half-configured toolset. Mirrors ``create_proc_tools`` at
        # ``daemon/tools/proc_tools.py:1896-1900`` and the loader
        # ``None``-manager stub pattern at ``daemon/loader.py:79-90``.
        return []

    # Capture into closure. Tools re-bind on each factory call which
    # is what we want — different instance, different toolset.
    _instance_id: str = current_instance_id
    _agent_id: str = agent_id

    # ── service_start ────────────────────────────────────────────
    @register_tool_category("service")
    @tool
    async def service_start(
        name: Annotated[
            str,
            Field(
                min_length=1,
                max_length=64,
                pattern=r"^[a-zA-Z0-9_-]+$",
                description=(
                    "Service name. Lowercase / digits / underscore / dash. "
                    "Must be unique among active services (D5 same-name "
                    "guard; name-reuse-after-EXITED is allowed)."
                ),
            ),
        ],
        command: Annotated[
            list[str],
            Field(
                min_length=1,
                description=(
                    "Argv array (NOT a shell string — no shell expansion "
                    "= no shell injection). e.g. ``[\"npm\", \"run\", \"dev\"]``."
                ),
            ),
        ],
        cwd: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Absolute working directory. ``None`` inherits the "
                    "daemon cwd."
                ),
            ),
        ],
    ) -> dict:
        """Start a long-lived service. Survives instance + daemon restart. Use tool_help('service_start') for details.

        Args:
            name: Unique service name (``^[a-zA-Z0-9_-]+$``, 1-64 chars).
            command: Argv array (no shell expansion).
            cwd: Working directory (absolute path).

        Returns:
            ``{"name", "pid", "status": "running", "log_path"}`` on
            success. Error shapes:
            ``{"status": "cap_exceeded"}``,
            ``{"status": "name_in_use"}`` (pre-check or
            ``reason="concurrent_start_won_race"``),
            ``{"status": "spawn_failed"}``,
            ``{"status": "invalid_name"}``,
            ``{"status": "invalid_argv"}``,
            ``{"status": "disabled"}``.
        """
        service_manager = getattr(manager, "_service_tool_manager", None)
        if service_manager is None:
            return {
                "name": name,
                "status": "disabled",
                "reason": "service_tool_manager_not_available",
            }
        return await service_manager.start(
            name=name,
            argv=command,
            cwd=cwd,
            started_by_instance_id=_instance_id,
            started_by_agent_id=_agent_id,
        )

    service_start._full_doc_ = """\
Start a long-lived service as a daemon-managed detached process. The
service survives both instance termination AND daemon restart; the
daemon does NOT reap it on stop. The service's stdio is written to
``data/services/<name>.log`` (configurable via
``ENSEMBLE_SERVICE_LOG_DIR``).

Args:
    name: Unique service name (``^[a-zA-Z0-9_-]+$``, 1-64 chars). The
        D5 same-name guard via the partial UNIQUE index
        ``idx_service_tracking_name_active`` allows name-reuse after a
        row transitions to ``EXITED`` (the previous slot is released).
    command: Argv array (NOT a shell string). No shell expansion = no
        shell injection. e.g. ``["npm", "run", "dev"]``,
        ``["python", "-m", "http.server", "8000"]``.
    cwd: Absolute working directory for the child. ``None`` inherits
        the daemon cwd. A bad cwd returns ``spawn_failed`` synchronously.

Returns:
    On success: ``{"name", "pid", "status": "running", "log_path",
    "start_time"}``.
    On cap_exceeded: ``{"status": "cap_exceeded", "reason":
    "max_concurrent_reached", "cap": 10, "active": N}``.
    On name_in_use: ``{"status": "name_in_use", "reason":
    "name_already_active" | "concurrent_start_won_race"}``.
    On spawn_failed: ``{"status": "spawn_failed", "reason": str(exc)}``
    (an EXITED row is written for forensic visibility).
    On invalid_name / invalid_argv: structural validation failure.
    On disabled: ``ENSEMBLE_SERVICE_TOOL_ENABLED=0``.

Notes:
* The service is started with ``start_new_session=True`` (≡
  ``setsid(2)``) so it is its own session + process-group leader
  (pgid == pid). ``service_stop`` uses ``os.killpg(pid, sig)`` to
  reach fork-children (e.g. ``npm run dev`` workers).
* The PID-reuse defense (F1) keys on ``(pid, start_time)`` equality:
  the start time is read immediately after Popen and stored on the
  row. A subsequent ``service_stop`` re-reads it and aborts the
  signal if the kernel recycled the PID onto a different process.
* The grandchild-setsid escape limitation (F15): if the service's
  own child calls ``setsid(2)`` again (e.g. a daemonizing wrapper),
  that grandchild escapes the killpg reach — ``service_stop`` cannot
  reach it. Document this for the LLM; consider not invoking such
  wrappers, or stop the service via its public API instead.
* ``exit_code: int | None`` on EXITED rows — ``None`` is the canonical
  value for deaths NOT observed via ``service_stop`` (the row was
  marked EXITED by a sweep / a pid_dead / a pid_recycled path that
  has no exit code to record).
"""

    # ── service_stop ─────────────────────────────────────────────
    @register_tool_category("service")
    @tool
    async def service_stop(
        name: Annotated[
            str,
            Field(
                min_length=1,
                description="Service name (matches ``service_start``).",
            ),
        ],
        force: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Skip SIGTERM and go straight to SIGKILL. Use for "
                    "unresponsive services."
                ),
            ),
        ],
    ) -> dict:
        """Stop a service by name. SIGTERM by default; SIGKILL when force=True. Use tool_help('service_stop') for details.

        Args:
            name: Service name.
            force: ``True`` skips SIGTERM and SIGKILL immediately.

        Returns:
            ``{"name", "pid", "status": "exited"}`` on success; the
            4-result-tuple extended with F1 grace-window reasons
            (``pid_recycled`` / ``pid_recycled_during_grace`` /
            ``pid_recycled_pre_kill``). Idempotent: already-exited
            returns the stored EXITED shape. ``not_found`` when the
            name was never registered.
        """
        service_manager = getattr(manager, "_service_tool_manager", None)
        if service_manager is None:
            return {
                "name": name,
                "status": "disabled",
                "reason": "service_tool_manager_not_available",
            }
        return await service_manager.stop(name=name, force=force)

    service_stop._full_doc_ = """\
Stop a service by name. Idempotent — calling on an already-EXITED
service returns the stored EXITED shape; calling on a never-registered
name returns ``not_found``.

Strategy:
* Read the row by name (active first; any-status as fallback for the
  idempotent path).
* F1 ownership re-verify BEFORE any signal: read
  ``get_process_start_time(row.pid)``; if the PID is dead, return
  ``reason="pid_dead"`` (mark EXITED inline). If ``start_time``
  mismatches the stored value, return ``reason="pid_recycled"`` (the
  kernel recycled the PID onto a different process — a stray SIGTERM
  would be a kill against an unrelated PID).
* A1: signal the WHOLE PROCESS GROUP via ``os.killpg(row.pid, sig)``
  — a setsid'd service IS its own session + group leader (pgid ==
  pid); killing the group reaches fork-children (e.g. ``npm run
  dev`` workers) with zero added reachability.
* A2: the 5s grace period is an ``await asyncio.sleep(0.1)`` +
  ``await asyncio.to_thread(get_process_start_time, pid)`` POLL — NOT
  a blocking ``time.sleep`` busy-wait. The event loop remains
  responsive throughout.
* F1 poll-loop re-verify: on EVERY iteration, compare
  ``get_process_start_time(row.pid)`` to ``row.start_time``. A
  mismatch during the grace window ⇒ return
  ``reason="pid_recycled_during_grace"`` WITHOUT escalating to SIGKILL.
* F1 pre-kill re-verify: immediately before the SIGKILL escalation
  (after the grace loop expires), re-verify once more — if recycled
  in the last 100ms before the deadline, return
  ``reason="pid_recycled_pre_kill"``.
* A13: every status-mutating UPDATE is delegated to the repo
  (``mark_exited``); the row-count return (0 = race-lost) is treated
  as idempotent success.

Args:
    name: Service name (matches the value passed to ``service_start``).
    force: ``True`` skips SIGTERM and goes straight to SIGKILL.

Returns:
    ``{"name", "pid", "status": "exited"}`` on a clean stop. Reason
    variants on the F1 PID-reuse defense:
    ``pid_dead``, ``pid_recycled``, ``pid_recycled_during_grace``,
    ``pid_recycled_pre_kill``.
    ``{"status": "not_found"}`` when the name was never registered.

Cross-instance stop semantics (name-keyed, daemon-global):
* Stop is name-keyed (NOT pid-keyed) because PIDs are recycled by
  the kernel; the agent has no memory of PIDs across restart or even
  across calls. The unique service name is the agent-visible
  identifier and is the stable handle.
* There is NO ``started_by`` gate: any agent that knows the name can
  stop the service. This is the OQ#4 resolution — name-keyed +
  daemon-global is the only sensible semantics for an LLM-driven
  orchestration tool, and the privilege is gated at the
  ``PRIVILEGED_TOOL_CATEGORIES`` layer (the category itself is
  default-deny).

Grandchild-setsid killpg ESCAPE limitation (F15):
* ``service_stop`` uses ``os.killpg(row.pid, sig)`` to reach fork-
  children. This works when the service and its descendants stay in
  the SAME process group. If a grandchild calls ``setsid(2)`` itself
  (e.g. a daemonizing wrapper that double-forks), it starts a NEW
  session + process group and ESCAPES the killpg reach — ``service_stop``
  CANNOT signal it. Document this for the LLM; consider not invoking
  such wrappers, or stop the service via its public API instead
  (e.g. ``kill -TERM <pid>`` to the wrapper directly).
"""

    # ── service_status ───────────────────────────────────────────
    @register_tool_category("service")
    @tool
    async def service_status(
        name: Annotated[
            str,
            Field(
                min_length=1,
                description="Service name.",
            ),
        ],
    ) -> dict:
        """Live status of one service (reconciles pid liveness on call). Use tool_help('service_status') for details.

        Args:
            name: Service name.

        Returns:
            ``{"name", "pid", "status", "exit_code"?}`` on success;
            ``{"status": "not_found"}`` if the name was never
            registered. Inline liveness reconciliation: a dead / recycled
            PID is marked EXITED and returned in the EXITED shape.
        """
        service_manager = getattr(manager, "_service_tool_manager", None)
        if service_manager is None:
            return {
                "name": name,
                "status": "disabled",
                "reason": "service_tool_manager_not_available",
            }
        return await service_manager.status(name=name)

    service_status._full_doc_ = """\
Live status of one service with inline liveness reconciliation.

Reads the row by name (any-status), then probes the kernel for the
stored PID: ``is_process_alive(pid)`` (which excludes zombies) and
``get_process_start_time(pid) == row.start_time`` (F1 ownership re-
verify). On mismatch, the row is marked EXITED inline and the EXITED
shape is returned.

Args:
    name: Service name.

Returns:
    ``{"name", "pid", "status", "start_time", "log_path"}`` for live
    rows; ``{"name", "pid", "status": "exited", "exit_code",
    "reason"?}`` for rows whose PID is dead or recycled (reconciled
    inline). ``{"status": "not_found"}`` if the name was never
    registered.

Zombie-liveness (approver gate): a process in state ``Z`` (zombie)
is NOT counted as alive — its stat file still exists on Linux, so
``get_process_start_time`` alone is insufficient. ``is_process_alive``
reads the ``state`` field from ``/proc/<pid>/stat`` (Linux) or the
``ps -o stat=`` column (macOS) and excludes ``Z``.
"""

    # ── service_list ─────────────────────────────────────────────
    @register_tool_category("service")
    @tool
    async def service_list() -> list[dict]:
        """List every tracked service (any status) ordered by created_at DESC. Reconciles pid liveness inline. Use tool_help('service_list') for details.

        Returns:
            ``[{name, pid, status, exit_code, command, cwd, log_path,
            started_by_instance_id, started_by_agent_id, created_at},
            ...]`` — one entry per row. Inline liveness reconciliation
            marks dead / recycled rows EXITED before returning.
        """
        service_manager = getattr(manager, "_service_tool_manager", None)
        if service_manager is None:
            return []
        return await service_manager.list_all()

    service_list._full_doc_ = """\
List every tracked service, ordered by ``created_at`` DESC (newest
first). Returns both STARTING/RUNNING and EXITED rows.

Inline liveness reconciliation: each STARTING/RUNNING row is probed
for liveness (``is_process_alive`` + F1 ``start_time`` ownership re-
verify); dead / recycled rows are marked EXITED inline before
returning. EXITED rows are passed through unchanged.

Args: (none)

Returns:
    A list of dicts (one per row), each with ``name``, ``pid``,
    ``status``, ``exit_code`` (EXITED only), ``command`` (parsed from
    the JSON-encoded column), ``cwd``, ``log_path``,
    ``started_by_instance_id``, ``started_by_agent_id``,
    ``created_at``.
"""

    # ── service_logs ─────────────────────────────────────────────
    @register_tool_category("service")
    @tool
    async def service_logs(
        name: Annotated[
            str,
            Field(
                min_length=1,
                description="Service name.",
            ),
        ],
        tail_lines: Annotated[
            int,
            Field(
                default=DEFAULT_TAIL_LINES,
                ge=1,
                le=10000,
                description=(
                    "Number of lines to return (default 200, max 10000). "
                    "OOM-safe: only the last 10 MB of the log file is "
                    "read regardless of ``tail_lines``."
                ),
            ),
        ],
    ) -> str:
        """Tail the log file for a service. No reaping; stdio is owned by the OS. Use tool_help('service_logs') for details.

        Args:
            name: Service name.
            tail_lines: Lines to return (default 200, max 10000). OOM-
                safe: only the last 10 MB of the file is read.

        Returns:
            The last ``tail_lines`` lines as a single string. Empty
            string when the row is missing, the file is unreadable, or
            the row has no ``log_path`` (defensive — should not
            happen in practice).
        """
        service_manager = getattr(manager, "_service_tool_manager", None)
        if service_manager is None:
            return ""
        return await service_manager.logs(name=name, tail_lines=tail_lines)

    service_logs._full_doc_ = """\
Tail the stdio log file for a service. ``service_logs`` is the ONLY
tool that lets the LLM diagnose a service that crashed silently —
without it the LLM would have to ``read_file`` the log via the
filesystem tool (a permissioning hop and a category-leak).

Reads ``log_path`` from the row (NEVER re-derives the path from the
name — F19). OOM-safe: the read is bounded at 10 MB; a 100 MB log
with ``tail_lines=200`` returns the last 200 lines, not the whole
file. The first line of the read chunk is dropped if it is partial
(the chunk may start mid-line because we read from offset
``filesize - 10MB``).

Args:
    name: Service name (matches ``service_start``).
    tail_lines: Number of lines to return (default 200, max 10000).

Returns:
    The last ``tail_lines`` lines as a single string. Empty string
    when the row is missing or the file is unreadable.
"""

    return [
        service_start,
        service_stop,
        service_status,
        service_list,
        service_logs,
    ]


__all__ = [
    "CATEGORY_DOC",
    "CATEGORY_NAME",
    "create_service_tools",
]