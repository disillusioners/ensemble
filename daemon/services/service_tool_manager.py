"""Manager-held facade for the ``service`` tool category (Phase 1.B).

This module is the **owner of all kill-site authority** for daemon-
managed detached processes (D3 of
``.agents/shared/planning/service-tool/decisions.md``). Every
``service_start`` / ``service_stop`` / ``service_status`` /
``service_list`` / ``service_logs`` tool call funnels through this
facade; the tools in :mod:`daemon.tools.service_tools` are thin
LangChain wrappers that dereference ``manager._service_tool_manager``
at CALL time (not at construction time — the factory pattern matches
``create_proc_tools`` at ``daemon/tools/proc_tools.py:1872-1900``).

Layering (per ``phase1-plan.md`` §D6):

* ``InstanceManager.__init__`` constructs ``self._service_tool_manager
  = ServiceToolManager(repo=..., cap=10)`` — wired in 1.C.13.
* The reconcile sweep (``ServiceReconciliationService`` skeleton in
  Phase 1.C / full impl in Phase 2) is a *separate* collaborator that
  also takes the ``ServiceRepo`` directly (A9 — narrow collaborator);
  it does NOT go through this facade.
* The manager is NOT a module singleton — each ``InstanceManager``
  instance owns its own ``ServiceToolManager``. (Module-level state
  would be a leak between daemons and tests.)

PID-reuse defense (F1 + A1 + A2 + A13 — the binding acceptance):

* ``stop`` ALWAYS verifies ``(pid, start_time)`` ownership BEFORE
  sending SIGTERM; a recycled PID returns ``reason="pid_recycled"``
  without signaling (the recycled process is some unrelated user
  process — a stray SIGTERM would be a kill against an unrelated PID).
* ``stop`` is async; the grace poll uses
  ``await asyncio.to_thread(get_process_start_time, pid)`` (NEVER
  ``time.sleep`` busy-wait — A2 BLOCKING).
* The grace loop re-verifies ownership on EVERY iteration; a
  mid-grace recycle returns ``reason="pid_recycled_during_grace"``
  without escalating to SIGKILL.
* Immediately before the SIGKILL escalation, the loop re-verifies
  ONE MORE TIME (``reason="pid_recycled_pre_kill"`` if the PID was
  recycled in the last 100 ms before the deadline).
* The F2 lost-race cleanup ``killpg`` in ``start`` (the
  ``IntegrityError`` branch — the orphaned child of a lost
  same-name race) re-verifies ownership BEFORE signaling too
  (council Finding 1): ``(pid, start_time)`` equality against the
  spawn-returned token; a mismatch SKIPs the signal with the
  ``pid_recycled_before_cleanup`` WARNING and still returns the
  ``name_in_use`` shape. This is the FIFTH guarded signal site —
  with it, EVERY ``killpg`` in this module sits behind an
  ownership re-verify (pre-signal / per-poll / pre-escalation /
  force-escalation / lost-race cleanup).
* All status-mutating UPDATEs are delegated to the repo's
  ``mark_exited`` / ``update_status`` which enforce the A13 atomic
  guard (``WHERE id=? AND status IN ('starting','running')``); the
  returned rowcount is the "did I win the race?" signal — 0 means a
  sweep↔stop race lost and is treated as idempotent success.

Kill switch (``D7 / A8``): the manager is constructed unconditionally
but its ``enabled`` flag defaults to True. The flag is read via
``getattr(config, "service_tool_enabled", True)`` — defensive
``getattr`` per the task spec (the resolvers land in 1.C.9 in
parallel; absent ⇒ default-on behavior). All public methods honor the
flag and return a structured ``{"status": "disabled", ...}`` shape so
the caller can present a clean error.

Cross-platform: Windows is NOT supported. The plan ships on Linux +
macOS; the spawner raises ``NotImplementedError`` on Windows and we
let it propagate (test surface is gated to Linux/macOS).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from typing import Optional

from sqlalchemy.exc import IntegrityError

from daemon.repositories.service_tool.models import (
    ServiceStatus,
    ServiceTracking,
)
from daemon.repositories.service_tool.repository import ServiceRepo
from daemon.tools.service_spawner import (
    DEFAULT_TAIL_LINES,
    MAX_LOG_TAIL_BYTES,
    get_process_start_time,
    is_process_alive,
    kill_log_path,
    spawn as spawner_spawn,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────


#: Grace period for the SIGTERM→SIGKILL escalation in ``stop``. Mirrors
#: ``proc_tools._STOP_GRACE_SECONDS=5`` (the binding codebase
#: precedent).
DEFAULT_STOP_GRACE_SECONDS: float = 5.0

#: Default poll interval inside the grace window. 100ms keeps the
#: async loop responsive while bounding the worst-case recycle-
#: detection latency.
DEFAULT_GRACE_POLL_INTERVAL_SECONDS: float = 0.1

#: Maximum log tail bytes — see ``daemon/tools/service_spawner.py``
#: ``MAX_LOG_TAIL_BYTES`` for the binding contract.
MAX_LOG_TAIL_BYTES_LIMIT: int = MAX_LOG_TAIL_BYTES

#: Default ``cap`` when the manager is constructed without an explicit
#: value. Mirrors the D5 brief cap=10 (architect verdict).
DEFAULT_MAX_CONCURRENT: int = 10

#: Regex for service-name validation — same shape as the tool-layer
#: pydantic Field constraint, applied at the manager boundary as a
#: defense-in-depth check.
_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_-]+$")

#: Service-name max length — matches the D3 tool schema (1-64 chars).
_NAME_MAX_LEN: int = 64

#: Disabled marker for the str-returning ``logs`` surface (council
#: F4): the canonical ``{"status": "disabled", "reason": ...}`` dict
#: shape rendered as text — the str twin of the marker the dict
#: surfaces return, so every surface expresses the SAME disabled
#: contract. Logs is a diagnostic read; a structured marker string is
#: the clearest honest "nothing to read: the category is off" answer
#: without silently masquerading as an empty log.
DISABLED_LOGS_MARKER: str = (
    '{"status": "disabled", "reason": "service_tool_enabled=False"}'
)


# ─────────────────────────────────────────────────────────────────────
# ServiceToolManager
# ─────────────────────────────────────────────────────────────────────


class ServiceToolManager:
    """Manager-held facade for ``service`` tool category operations.

    Constructed once per ``InstanceManager`` (1.C.13 wires
    ``self._service_tool_manager = ServiceToolManager(repo=..., cap=10)``).
    Holds a direct reference to ``ServiceRepo`` (no module singleton)
    and a ``cap`` for the D5 same-name guard.

    Args:
        repo: The :class:`ServiceRepo` instance (1.A-landed, frozen
            interface per the F3 contract).
        cap: Maximum concurrent ``STARTING`` / ``RUNNING`` rows
            (D5 brief cap). Defaults to 10; bounded >= 1 by the
            ``Field(ge=1)`` constraint in ``ServiceToolConfig``
            (1.C.11) and re-asserted at construction.
        enabled: Whether the category is enabled (D7 kill switch).
            ``True`` by default; the 1.C resolver
            ``_resolve_service_tool_enabled`` overrides per-env.

    Thread-safety: the manager is a thin async wrapper around a sync
    repo + sync spawner. Async callers wrap every sync call in
    ``await asyncio.to_thread(...)`` so the event loop is never
    blocked (A2 BLOCKING — the 5s grace must NOT busy-wait on
    ``time.sleep``).

    The manager holds NO mutable state of its own — every state read
    goes through the repo (so the reconcile sweep can see the same
    truth). The only manager-level state is the ``cap`` integer and
    the ``enabled`` boolean (both set at construction).
    """

    def __init__(
        self,
        repo: ServiceRepo,
        cap: int = DEFAULT_MAX_CONCURRENT,
        enabled: bool = True,
    ) -> None:
        if not isinstance(repo, ServiceRepo):
            raise TypeError(
                f"ServiceToolManager requires a ServiceRepo; got "
                f"{type(repo).__name__}"
            )
        if cap < 1:
            raise ValueError(f"cap must be >= 1; got {cap}")
        self.repo = repo
        self.cap = cap
        self.enabled = enabled

    # ── Public surface ─────────────────────────────────────────────

    async def start(
        self,
        name: str,
        argv: list[str],
        cwd: Optional[str],
        started_by_instance_id: str,
        started_by_agent_id: str,
    ) -> dict:
        """Start a detached service. Returns the canonical result dict.

        Sequence (per D3 + F2 + F8 pseudocode at ``decisions.md``):

        1. **Disabled flag** — return ``{"status": "disabled"}`` if the
           kill-switch is off.
        2. **Name validation** — pattern + length check (defense in
           depth on top of the tool-layer pydantic constraint).
        3. **Cap check** — count active rows via the repo; reject
           if at cap (BEFORE any side effect, per the task binding
           contract).
        4. **Name-uniqueness pre-check** — Python-side
           ``repo.get_by_name(name)`` (active_only=True by default)
           fast-fail BEFORE spawning.
        5. **F8 spawn-failure path** — ``try spawn(...) except
           OSError``: write an EXITED row via ``insert_with_status``
           (immediate, no 30s slot block, no kill needed because
           the child never existed). Return ``spawn_failed``.
        6. **F2 concurrent-race path** — ``try repo.insert(...)``
           (the partial UNIQUE index raises IntegrityError if a
           concurrent caller won the race); re-verify ``(pid,
           start_time)`` ownership, then ``killpg`` the
           just-spawned child (otherwise it leaks as a live untracked
           kill-exempt process; a recycled PID skips the signal —
           ``pid_recycled_before_cleanup``) and return ``name_in_use``.
        7. **Happy path** — return
           ``{"name": ..., "pid": ..., "status": "running",
           "log_path": ...}``.

        All sync repo + spawn calls are wrapped in
        ``asyncio.to_thread`` (A2 / F7 seam layering).
        """
        if not self.enabled:
            return {
                "name": name,
                "status": "disabled",
                "reason": "service_tool_enabled=False",
            }

        # Name validation (defense in depth — the tool-layer Field
        # constraint should catch this first, but the manager is also
        # exposed to operator-side admin paths in future phases).
        if not isinstance(name, str) or not _NAME_PATTERN.match(name):
            return {
                "name": name,
                "status": "invalid_name",
                "reason": (
                    "name must match ^[a-zA-Z0-9_-]+$ (1-64 chars); "
                    f"got {name!r}"
                ),
            }
        if len(name) > _NAME_MAX_LEN:
            return {
                "name": name,
                "status": "invalid_name",
                "reason": f"name length {len(name)} > {_NAME_MAX_LEN}",
            }
        if not argv:
            return {
                "name": name,
                "status": "invalid_argv",
                "reason": "argv must be a non-empty list",
            }

        # Cap check FIRST — no side effect, no spawn, no insert.
        # ``list_active`` is cheap (the partial UNIQUE index makes the
        # active-row scan O(active_rows), which is bounded by ``cap``).
        active_count = await asyncio.to_thread(
            lambda: len(self.repo.list_active())
        )
        if active_count >= self.cap:
            return {
                "name": name,
                "status": "cap_exceeded",
                "reason": "max_concurrent_reached",
                "cap": self.cap,
                "active": active_count,
            }

        # Name-uniqueness pre-check (Python-side fast-fail before
        # spawn — saves a killpg round-trip on the common case).
        existing = await asyncio.to_thread(self.repo.get_by_name, name)
        if existing is not None:
            return {
                "name": name,
                "status": "name_in_use",
                "reason": "name_already_active",
                "existing_pid": existing.pid,
            }

        # Resolve the log path BEFORE spawn (creates the directory).
        # The spawner writes stdio to this file; if the directory
        # does not exist Popen raises FileNotFoundError synchronously
        # and we land in the F8 branch below.
        log_path = await asyncio.to_thread(kill_log_path, name)

        # ── F8: synchronous spawn failure ────────────────────────
        try:
            pid, start_time = await asyncio.to_thread(
                spawner_spawn, argv, log_path, cwd
            )
        except OSError as exc:
            # Child never existed; nothing to kill. Write an EXITED
            # row immediately so the slot is released and
            # ``service_status`` / ``service_list`` surface the failure
            # (the A3 eternal-`starting` reaper in Phase 2 only covers
            # crash/interrupt windows — synchronous Popen failure is
            # closed here).
            await asyncio.to_thread(
                self.repo.insert_with_status,
                name=name,
                command=argv,
                pid=None,
                start_time=None,
                cwd=cwd or os.getcwd(),
                started_by_instance_id=started_by_instance_id,
                started_by_agent_id=started_by_agent_id,
                log_path=log_path,
                status=ServiceStatus.EXITED.value,
                reason="spawn_failed",
                exit_code=-1,
            )
            logger.error(
                "[ServiceTool] service_start spawn_failed name=%s err=%s",
                name,
                exc,
            )
            return {
                "name": name,
                "status": "spawn_failed",
                "reason": str(exc),
            }
        except ValueError as exc:
            # argv empty (should be caught at the validation step but
            # defense in depth — spawner_spawn raises ValueError on
            # empty argv).
            return {
                "name": name,
                "status": "invalid_argv",
                "reason": str(exc),
            }

        # ── F2: concurrent same-name race ─────────────────────────
        try:
            row = await asyncio.to_thread(
                self.repo.insert,
                name=name,
                command=argv,
                pid=pid,
                start_time=start_time,
                cwd=cwd or os.getcwd(),
                status=ServiceStatus.STARTING.value,
                started_by_instance_id=started_by_instance_id,
                started_by_agent_id=started_by_agent_id,
                log_path=log_path,
            )
        except IntegrityError:
            # A concurrent caller won the race (the partial UNIQUE
            # index ``idx_service_tracking_name_active`` rejected our
            # insert). The just-spawned child is otherwise an ORPHAN:
            # setsid'd, never registered in our reconcile sweep, and
            # kill-exempt (no daemon knows it exists). We MUST
            # ``killpg`` it now — without this branch, every lost
            # race leaks a live untracked OS process.
            #
            # F1 (council Finding 1): ownership re-verify BEFORE the
            # cleanup signal — the same invariant every other signal
            # site in this module applies. The window between the
            # spawn and this handler is exactly where a fast-exiting
            # child's PID could be recycled onto an unrelated process;
            # a blind ``killpg`` there would be a stray kill. We own
            # ``(pid, start_time)`` from the spawn that just
            # succeeded, so equality means the group is still ours.
            # A mismatch (including a dead read ⇒ ``None``) SKIPs the
            # signal (class token ``pid_recycled_before_cleanup``);
            # the ``name_in_use`` resolution below is unaffected
            # either way (the INSERT never committed, so there is no
            # DB row to clean up).
            cleanup_start = await asyncio.to_thread(
                get_process_start_time, pid
            )
            if cleanup_start is not None and cleanup_start == start_time:
                try:
                    await asyncio.to_thread(
                        os.killpg, pid, signal.SIGKILL
                    )
                except (ProcessLookupError, PermissionError) as exc:
                    logger.warning(
                        "[ServiceTool] service_start concurrent_race killpg_failed "
                        "name=%s pid=%s err=%s",
                        name,
                        pid,
                        exc,
                    )
                logger.warning(
                    "[ServiceTool] service_start concurrent_race name=%s pid=%s killed",
                    name,
                    pid,
                )
            else:
                logger.warning(
                    "[ServiceTool] service_start pid_recycled_before_cleanup "
                    "name=%s pid=%s expected_start=%s got=%s — cleanup killpg "
                    "SKIPPED (the PID may have been recycled onto an unrelated "
                    "process; no signal sent)",
                    name,
                    pid,
                    start_time,
                    cleanup_start,
                )
            return {
                "name": name,
                "status": "name_in_use",
                "reason": "concurrent_start_won_race",
                "pid": pid,
            }

        # Transition STARTING -> RUNNING: the model docstring at
        # ``daemon/repositories/service_tool/models.py`` says
        # ``STARTING`` means "Popen returned, we have a PID, but we
        # have not yet confirmed the process is alive — the
        # ``service_start`` tool transitions STARTING -> RUNNING after
        # the first successful liveness ping". ``os.kill(pid, 0)``
        # is the canonical POSIX liveness ping (no signal delivered —
        # only EPERM/ESRCH checks). We use ``is_process_alive`` which
        # additionally excludes zombies (approver-gate requirement).
        alive_now = await asyncio.to_thread(is_process_alive, pid)
        if alive_now:
            await asyncio.to_thread(
                self.repo.update_status, row.id, ServiceStatus.RUNNING.value
            )

        logger.info(
            "[ServiceTool] service_started name=%s pid=%s status=%s",
            row.name,
            row.pid,
            ServiceStatus.RUNNING.value if alive_now else row.status,
        )
        return {
            "name": row.name,
            "pid": row.pid,
            "status": "running",
            "log_path": row.log_path,
            "start_time": row.start_time,
        }

    async def stop(self, name: str, force: bool = False) -> dict:
        """Stop a service by name. Resolves to a TERMINAL shape (council F3).

        ``stop`` blocks through the grace window and ALWAYS returns a
        resolved outcome — there is no ``{"status": "running"}``
        return (an earlier revision of this docstring advertised one;
        no code path ever produced it).

        Returns:
            One of:

            * ``{"name": ..., "pid": ..., "status": "exited"}`` —
              clean exit. May carry ``exit_code``.
            * ``{"name": ..., "status": "not_found"}`` — no row ever
              existed with this name.
            * ``{"name": ..., "status": "starting", "reason": "pid_not_yet_assigned"}``
              — row exists in STARTING but no PID was assigned (F8
              synchronous-spawn-failure path; nothing to signal).
            * ``{"name": ..., "status": "exited", "reason": "pid_dead"}``
              — the stored PID is already dead (no signal needed).
            * ``{"name": ..., "status": "exited", "reason": "pid_recycled"}``
              — the stored PID exists but ``start_time`` differs (F1).
            * ``{"name": ..., "status": "exited", "reason": "pid_recycled_during_grace"}``
              — recycle detected DURING the grace poll (F1).
            * ``{"name": ..., "status": "exited", "reason": "pid_recycled_pre_kill"}``
              — recycle detected immediately BEFORE the SIGKILL
              escalation (F1 second layer).
            * ``{"name": ..., "status": "disabled", ...}`` — kill
              switch off.

        The 5s grace uses ``await asyncio.to_thread(get_process_start_time, pid)``
        polling (NEVER ``time.sleep`` busy-wait — A2 BLOCKING).
        """
        if not self.enabled:
            return {
                "name": name,
                "status": "disabled",
                "reason": "service_tool_enabled=False",
            }

        # ── Resolve the row (active-only first; fall back to any-status
        # for the idempotent "already exited" return) ────────────
        row = await asyncio.to_thread(self.repo.get_by_name, name)
        if row is None:
            existing = await asyncio.to_thread(
                self.repo.get_by_name_any_status, name
            )
            if existing is not None:
                return {
                    "name": name,
                    "status": "exited",
                    "exit_code": existing.exit_code,
                }
            return {"name": name, "status": "not_found"}

        # Defensive: a STARTING row with no PID (F8 spawn-failure
        # edge — the row is EXITED in the canonical path, but if the
        # INSERT raced before ``mark_exited`` finished, defensively
        # return).
        if row.pid is None:
            return {
                "name": name,
                "status": "starting",
                "reason": "pid_not_yet_assigned",
            }

        # ── F1 pre-signal ownership re-verify ─────────────────────
        current_start = await asyncio.to_thread(
            get_process_start_time, row.pid
        )
        if current_start is None:
            await asyncio.to_thread(self.repo.mark_exited, row.id)
            logger.info(
                "[ServiceTool] service_stop pid_dead name=%s pid=%s",
                name,
                row.pid,
            )
            return {
                "name": name,
                "pid": row.pid,
                "status": "exited",
                "reason": "pid_dead",
            }
        if current_start != row.start_time:
            await asyncio.to_thread(self.repo.mark_exited, row.id)
            logger.warning(
                "[ServiceTool] service_stop pid_recycled name=%s pid=%s "
                "expected_start=%s got=%s",
                name,
                row.pid,
                row.start_time,
                current_start,
            )
            return {
                "name": name,
                "pid": row.pid,
                "status": "exited",
                "reason": "pid_recycled",
            }

        # ── A1: signal the WHOLE GROUP via killpg ────────────────
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            await asyncio.to_thread(os.killpg, row.pid, sig)
        except ProcessLookupError:
            # Process already dead between the F1 re-verify and the
            # signal — the manager treats this as a clean exit.
            await asyncio.to_thread(self.repo.mark_exited, row.id)
            return {
                "name": name,
                "pid": row.pid,
                "status": "exited",
                "reason": "pid_dead",
            }

        if force:
            await asyncio.to_thread(self.repo.mark_exited, row.id)
            logger.info(
                "[ServiceTool] service_stopped name=%s pid=%s force=True",
                name,
                row.pid,
            )
            return {
                "name": name,
                "pid": row.pid,
                "status": "exited",
            }

        # ── A2 + F1: grace-period async poll with ownership
        # re-verify on EACH iteration ──────────────────────────
        deadline = time.monotonic() + DEFAULT_STOP_GRACE_SECONDS
        exited_during_grace = False
        while time.monotonic() < deadline:
            await asyncio.sleep(DEFAULT_GRACE_POLL_INTERVAL_SECONDS)
            current_start = await asyncio.to_thread(
                get_process_start_time, row.pid
            )
            if current_start is None:
                # Process exited cleanly within grace — done.
                exited_during_grace = True
                break
            if current_start != row.start_time:
                # F1: PID recycled during grace — STOP, do NOT
                # escalate to SIGKILL. The recycled process is now
                # some unrelated user process; a SIGKILL would be a
                # stray signal against an unrelated PID (the exact
                # hazard F1 closes).
                await asyncio.to_thread(self.repo.mark_exited, row.id)
                logger.warning(
                    "[ServiceTool] service_stop grace_recycle name=%s pid=%s "
                    "expected_start=%s got=%s grace_elapsed=%.2fs",
                    name,
                    row.pid,
                    row.start_time,
                    current_start,
                    time.monotonic() - (deadline - DEFAULT_STOP_GRACE_SECONDS),
                )
                return {
                    "name": name,
                    "pid": row.pid,
                    "status": "exited",
                    "reason": "pid_recycled_during_grace",
                }
        else:
            # Loop completed without break — process still alive past
            # grace. F1 second layer: re-verify (pid, start_time)
            # immediately before the SIGKILL escalation. If the PID
            # was recycled in the last 100ms, mark EXITED instead of
            # escalating.
            final_start = await asyncio.to_thread(
                get_process_start_time, row.pid
            )
            if final_start is not None and final_start != row.start_time:
                await asyncio.to_thread(self.repo.mark_exited, row.id)
                logger.warning(
                    "[ServiceTool] service_stop pre_kill_recycle name=%s "
                    "pid=%s expected_start=%s got=%s",
                    name,
                    row.pid,
                    row.start_time,
                    final_start,
                )
                return {
                    "name": name,
                    "pid": row.pid,
                    "status": "exited",
                    "reason": "pid_recycled_pre_kill",
                }
            # A1: SIGKILL via killpg too — same rationale as SIGTERM
            # (reach fork-children of the setsid leader).
            try:
                await asyncio.to_thread(os.killpg, row.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        await asyncio.to_thread(self.repo.mark_exited, row.id)
        logger.info(
            "[ServiceTool] service_stopped name=%s pid=%s force=%s grace_exited=%s",
            name,
            row.pid,
            force,
            exited_during_grace,
        )
        return {
            "name": name,
            "pid": row.pid,
            "status": "exited",
        }

    async def status(self, name: str) -> dict:
        """Live status of one service. Inline liveness reconciliation.

        Returns:
            ``{"name": ..., "pid": ..., "status": ..., "exit_code": ...?}``
            on success; ``{"name": ..., "status": "not_found"}`` if
            the name was never registered; ``{"status": "disabled"}``
            when the kill-switch is off.

        Liveness: ``is_process_alive(pid)`` (excludes zombies) +
            ``get_process_start_time(pid) == row.start_time`` (F1
            ownership re-verify). On mismatch, mark EXITED inline and
            return the EXITED shape with ``reason="pid_recycled"``.
        """
        if not self.enabled:
            return {
                "name": name,
                "status": "disabled",
                "reason": "service_tool_enabled=False",
            }

        row = await asyncio.to_thread(self.repo.get_by_name_any_status, name)
        if row is None:
            return {"name": name, "status": "not_found"}

        # Defensive: a STARTING row with no PID — return starting.
        if row.pid is None:
            return {
                "name": name,
                "status": "starting",
                "exit_code": row.exit_code,
            }

        # Inline liveness reconciliation: read the live start_time,
        # compare with the stored token. A mismatch ⇒ PID recycled.
        # The store may also be EXITED already (idempotent return).
        if row.status == ServiceStatus.EXITED.value:
            return {
                "name": row.name,
                "pid": row.pid,
                "status": "exited",
                "exit_code": row.exit_code,
            }

        current_start = await asyncio.to_thread(
            get_process_start_time, row.pid
        )
        alive = await asyncio.to_thread(is_process_alive, row.pid)
        if not alive or current_start is None:
            await asyncio.to_thread(self.repo.mark_exited, row.id)
            return {
                "name": row.name,
                "pid": row.pid,
                "status": "exited",
                "reason": "pid_dead",
            }
        if current_start != row.start_time:
            await asyncio.to_thread(self.repo.mark_exited, row.id)
            return {
                "name": row.name,
                "pid": row.pid,
                "status": "exited",
                "reason": "pid_recycled",
            }
        return {
            "name": row.name,
            "pid": row.pid,
            "status": row.status,
            "start_time": row.start_time,
            "log_path": row.log_path,
        }

    async def list_all(self) -> list[dict]:
        """List every tracked row (any status), newest first.

        Inline liveness reconciliation for STARTING/RUNNING rows:
        dead/recycled PIDs are marked EXITED and returned in the
        EXITED shape. The reconciliation is per-row and uses the
        same ``is_process_alive`` + ``get_process_start_time``
        contract as ``status``.

        OFF gate (review W2): when ``enabled=False``, returns the
        disabled marker shape with NO DB queries, NO ``mark_exited``
        writes, and NO liveness probes — byte-identical to the
        pre-Phase-1 ``service_list`` contract under the flag. The
        disabled marker is a single-element list mirroring the
        ``{"status": "disabled", "reason": ...}`` shape used by
        ``start`` / ``stop`` / ``status``.
        """
        if not self.enabled:
            return [
                {
                    "status": "disabled",
                    "reason": "service_tool_enabled=False",
                }
            ]
        rows = await asyncio.to_thread(self.repo.list_all)
        out: list[dict] = []
        for row in rows:
            if row.status in (
                ServiceStatus.STARTING.value,
                ServiceStatus.RUNNING.value,
            ) and row.pid is not None:
                current_start = await asyncio.to_thread(
                    get_process_start_time, row.pid
                )
                alive = await asyncio.to_thread(is_process_alive, row.pid)
                if not alive or current_start is None:
                    await asyncio.to_thread(self.repo.mark_exited, row.id)
                    row = (
                        await asyncio.to_thread(self.repo.get_by_id, row.id)
                    ) or row
                elif current_start != row.start_time:
                    await asyncio.to_thread(self.repo.mark_exited, row.id)
                    row = (
                        await asyncio.to_thread(self.repo.get_by_id, row.id)
                    ) or row
            try:
                command = (
                    json.loads(row.command)
                    if isinstance(row.command, str)
                    else row.command
                )
            except (ValueError, TypeError):
                command = row.command
            out.append(
                {
                    "name": row.name,
                    "pid": row.pid,
                    "status": row.status,
                    "exit_code": row.exit_code,
                    "command": command,
                    "cwd": row.cwd,
                    "log_path": row.log_path,
                    "started_by_instance_id": row.started_by_instance_id,
                    "started_by_agent_id": row.started_by_agent_id,
                    "created_at": row.created_at,
                }
            )
        return out

    async def logs(self, name: str, tail_lines: int = DEFAULT_TAIL_LINES) -> str:
        """Tail the log file for a service. Reads ``log_path`` from
        the row — NEVER re-derives the path from the name (F19).

        OFF gate (council F4 — the last holdout, closing the uniform
        OFF contract): when ``enabled=False``, returns the disabled
        marker BEFORE any row lookup — NO DB queries, no reads. The
        marker is :data:`DISABLED_LOGS_MARKER`: the canonical
        ``{"status": "disabled", "reason": "service_tool_enabled=False"}``
        dict shape rendered as text (the str twin of the marker the
        dict surfaces return).

        Returns:
            The last ``tail_lines`` lines as a single string. If the
            row has no ``log_path`` (defensive), the file does not
            exist, or ``name`` is unknown, returns an empty string.
            When the kill-switch is off, returns
            :data:`DISABLED_LOGS_MARKER`.

        OOM protection: reads only the LAST ``MAX_LOG_TAIL_BYTES``
        bytes of the file (F19 / 1.B.7) — a 100 MB log with
        ``tail_lines=200`` returns the last 200 lines, not the whole
        file.
        """
        if not self.enabled:
            return DISABLED_LOGS_MARKER
        row = await asyncio.to_thread(self.repo.get_by_name_any_status, name)
        if row is None:
            return ""
        log_path = row.log_path
        if not log_path:
            return ""

        return await asyncio.to_thread(_tail_log_file, log_path, tail_lines)

    # ── helpers ────────────────────────────────────────────────────

    def _count_active(self) -> int:
        """Sync helper: count ``STARTING`` / ``RUNNING`` rows.

        Used by tests and by callers that need a sync counter (e.g.
        the boot probe at ``load_config`` time). Returns the count,
        not the rows.
        """
        return len(self.repo.list_active())


# ─────────────────────────────────────────────────────────────────────
# Module-level helpers (sync, called via asyncio.to_thread)
# ─────────────────────────────────────────────────────────────────────


def _tail_log_file(log_path: str, tail_lines: int) -> str:
    """Read the last ``tail_lines`` lines from a log file (sync).

    Bounded by ``MAX_LOG_TAIL_BYTES`` (10 MB) — a 100 MB log is read
    from offset ``filesize - 10MB`` to EOF, then the line boundary
    is re-aligned by dropping a partial leading line. This is the
    F19 / 1.B.7 OOM guard.

    Returns an empty string if the file does not exist or is not
    readable (the log file may be missing on the F8 spawn-failure
    path — never raise from ``logs``).
    """
    if tail_lines < 1:
        return ""
    try:
        file_size = os.path.getsize(log_path)
    except OSError:
        return ""

    # Read at most the last MAX_LOG_TAIL_BYTES bytes.
    read_size = min(file_size, MAX_LOG_TAIL_BYTES)
    offset = file_size - read_size
    try:
        with open(log_path, "rb") as fh:
            if offset > 0:
                fh.seek(offset)
            raw = fh.read(read_size)
    except OSError:
        return ""

    if not raw:
        return ""

    text = raw.decode("utf-8", errors="replace")

    # If we started mid-file, the first line may be partial — drop it.
    if offset > 0:
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1 :]
        else:
            # No newline in the entire tail chunk — return as-is.
            pass

    lines = text.splitlines()
    if tail_lines and len(lines) > tail_lines:
        lines = lines[-tail_lines:]
    return "\n".join(lines)


__all__ = [
    "DEFAULT_GRACE_POLL_INTERVAL_SECONDS",
    "DEFAULT_MAX_CONCURRENT",
    "DEFAULT_STOP_GRACE_SECONDS",
    "MAX_LOG_TAIL_BYTES_LIMIT",
    "ServiceToolManager",
    "_tail_log_file",
]