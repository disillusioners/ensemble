"""In-process registry of ``OpenCodeSessionManager`` instances.

Direct port of the in-memory ``Server.sessions`` map in
``.inspiration-projects/opencode_skill_src/internal/daemon/server.go``
(``Server.sessions map[string]*manager.SessionManager``, line 47)
plus the persistence-loading logic in ``Start()`` (lines 115-144) and
the per-action handlers in the body of ``handleConnection`` (lines 298-374).

The Go ``Server`` and the Go ``Registry`` are split into two structs
(session manager map + SQLite registry respectively). In this port we
collapse them into a single ``OpenCodeSessionRegistry`` that owns both:

- ``self._repository`` — the SQLite-backed CRUD
- ``self._managers``  — the in-memory map ``session_id → SessionManager``

A single ``asyncio.Lock`` guards ``_managers``. The repository has its
own SQLAlchemy session lifecycle and is naturally thread/async-safe at
the row level.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .client import OpenCodeClient
from .constants import ABORT_REMOTE_SETTLE_S, _now_rfc3339
from .repository import OpenCodeSessionRepository
from .session_manager import (
    IDLE,
    OpenCodeSessionManager,
    PersistedState,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# OpenCodeSessionRegistry
# ─────────────────────────────────────────────────────────────────────────────


class OpenCodeSessionRegistry:
    """Owns the live ``OpenCodeSessionManager`` instances and the repository.

    Public methods (all async):

    - ``create_new(project, session_name, working_dir)`` — INIT_SESSION handler
    - ``abort_session(project, session_name)`` — ABORT_SESSION handler
    - ``recover_from_registry()`` — startup recovery
    - ``handle_start_work(project, session_name)`` — /start-work lock
    - ``get_session_record(project, session_name)`` — public delegate for repository.get()
    - ``get_manager(session_id)`` — accessor used by PROMPT/COMMAND handlers
    - ``find_by_id(session_id)`` — registry-level lookup
    """

    def __init__(
        self,
        repository: OpenCodeSessionRepository,
        on_state_change: Any | None = None,
    ) -> None:
        """Initialize the registry.

        Args:
            repository: SQLite-backed CRUD.
            on_state_change: Optional callback passed to each
                ``OpenCodeSessionManager`` so persistence propagates.
        """
        self._repository: OpenCodeSessionRepository = repository
        self._managers: dict[str, OpenCodeSessionManager] = {}
        self._managers_lock: asyncio.Lock = asyncio.Lock()
        self._on_state_change: Any = on_state_change

    # ── Accessors ─────────────────────────────────────────────────────────

    async def get_manager(self, session_id: str) -> OpenCodeSessionManager | None:
        """Return the manager for ``session_id`` or ``None``.

        Used by the PROMPT/COMMAND/ANSWER/RESUME handlers in the
        Go ``server.go`` lines 410-507.
        """
        async with self._managers_lock:
            return self._managers.get(session_id)

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Return all session records. Mirrors ``Registry.List``."""
        return self._repository.list()

    async def find_by_id(self, session_id: str) -> dict[str, Any] | None:
        """Look up the ``(project, session_name)`` for a session id.

        Mirrors ``Registry.FindByID`` (registry.go). Uses the index on
        ``id`` for O(log N) lookup.
        """
        return self._repository.find_by_id(session_id)

    async def get_session_record(
        self, project: str, session_name: str,
    ) -> dict[str, Any] | None:
        """Get a session record by ``(project, session_name)`` composite key.

        Public delegate for ``repository.get()`` so that tool functions
        don't need to reach into ``_repository`` (private attribute).
        Returns ``None`` if the session doesn't exist.
        """
        return self._repository.get(project, session_name)

    # ── INIT_SESSION equivalent ───────────────────────────────────────────

    async def create_new(
        self,
        project: str,
        session_name: str,
        working_dir: str,
    ) -> str:
        """Create a new session and register it.

        Ports the ``INIT_SESSION`` block in ``server.go`` lines 298-336
        line-by-line:

        1. If an existing session exists for ``(project, session_name)``:
           - best-effort abort the old remote session (server.go:313-316)
           - delete the old row (server.go:317-320)
        2. Create a new remote session via the OpenCode HTTP API
           (server.go:322-327).
        3. Save the new ``(project, session_name, id, working_dir)`` to
           the repository (server.go:329-333).
        4. Create a new ``OpenCodeSessionManager`` in memory, wire up
           persistence, and start its background loop.
        5. Return the new session ID.

        Args:
            project: Project identifier (used as part of the primary key).
            session_name: Human-readable session name.
            working_dir: Working directory for the new session.

        Returns:
            The OpenCode session ID returned by ``CreateSession``.

        Raises:
            Exception: When ``CreateSession`` fails. The repository
                delete (if any) is not rolled back — we accept a small
                window of orphan rows to keep this method simple, same
                as the Go version.
        """
        # ── Step 1: clean up any previous session for this (project, name)
        # server.go:312-320
        existing = self._repository.get(project, session_name)
        if existing is not None:
            old_session_id = existing.get("id")
            old_working_dir = existing.get("working_dir")
            logger.info(
                "Session %s/%s exists, aborting old session %s",
                project,
                session_name,
                old_session_id,
            )
            if old_session_id and old_working_dir:
                try:
                    # server.go:314: best-effort abort; failure is logged
                    abort_client = OpenCodeClient(old_working_dir)
                    try:
                        await abort_client.abort_session(old_session_id)
                    finally:
                        await abort_client.aclose()
                except Exception as exc:
                    logger.warning("Failed to abort old session: %s", exc)

            try:
                self._repository.delete(project, session_name)
            except KeyError:
                # server.go:317-319: log and continue
                logger.warning("Failed to delete old session: not found")

        # ── Step 2: create new remote session
        # server.go:322-327
        client = OpenCodeClient(working_dir)
        try:
            new_session_id = await client.create_session(session_name)
        except Exception as exc:
            await client.aclose()
            raise RuntimeError(f"Failed to create session: {exc}") from exc
        # Note: we deliberately keep the client alive — the manager needs
        # to talk to the same client. The manager's __init__ builds its
        # own client, so we close this one and let the manager build a
        # fresh one (preserving the working_dir).
        await client.aclose()

        # ── Step 3: persist
        # server.go:329-333
        try:
            self._repository.create(project, session_name, new_session_id, working_dir)
        except Exception as exc:
            logger.error("Failed to save session to registry: %s", exc)
            raise RuntimeError(f"Failed to save session: {exc}") from exc

        # ── Step 4: build + register manager
        # server.go equivalent: in INIT_SESSION, the Go server does NOT
        # create a manager; managers are only created on GET_SESSION or
        # START_SESSION. We create the manager eagerly here so callers
        # can immediately dispatch PROMPT/COMMAND without an extra
        # round-trip. This matches the behaviour of GET_SESSION's
        # "load into memory on demand" path.
        manager = await self._load_manager_into_memory(
            project=project,
            session_name=session_name,
            session_id=new_session_id,
            working_dir=working_dir,
        )

        logger.info(
            "Initialized session %s/%s with ID %s",
            project,
            session_name,
            new_session_id,
        )
        return new_session_id

    # ── ABORT_SESSION equivalent ───────────────────────────────────────────

    async def abort_session(self, project: str, session_name: str) -> dict[str, Any]:
        """Abort a session: remote + local.

        Ports the ``ABORT_SESSION`` block in ``server.go`` lines 338-374:

        1. Look up the session in the repository.
        2. Best-effort ``AbortSession`` HTTP call.
        3. If remote abort succeeded, wait 3 seconds for the server to
           settle (server.go:359).
        4. Reset the in-memory manager's state via ``AbortTask``.
        5. Return a status message.

        Args:
            project: Project identifier.
            session_name: Session name.

        Returns:
            Dict with ``status`` and ``message`` keys (matches the
            envelope used by the Go server).

        Raises:
            KeyError: When the session is not in the repository. Mirrors
                the Go version's ``"Session not found"`` error response.
        """
        # server.go:347-351
        session = self._repository.get(project, session_name)
        if session is None:
            return {"status": "error", "message": "Session not found"}

        session_id = session.get("id")
        working_dir = session.get("working_dir")
        if not session_id:
            return {"status": "error", "message": "Session has no id"}

        # server.go:354-356: best-effort remote abort
        abort_err: Exception | None = None
        if working_dir:
            client = OpenCodeClient(working_dir)
            try:
                await client.abort_session(session_id)
            except Exception as exc:
                abort_err = exc
                logger.warning("Warning: Failed to abort remote session: %s", exc)
            finally:
                await client.aclose()

        # server.go:357-360: 3-second settle delay after successful abort
        if abort_err is None:
            await asyncio.sleep(ABORT_REMOTE_SETTLE_S)

        # server.go:362-367: reset local manager state
        manager = await self.get_manager(session_id)
        if manager is not None:
            await manager.abort_task()

        logger.info("Aborted tasks for session %s/%s", project, session_name)

        # server.go:370-374
        if abort_err is not None:
            return {
                "status": "ok",
                "message": f"Local tasks aborted, but remote abort failed: {abort_err}",
            }
        return {"status": "ok", "message": "Session aborted and ready for new input"}

    # ── START_SESSION equivalent (load into memory) ───────────────────────

    async def _load_manager_into_memory(
        self,
        project: str,
        session_name: str,
        session_id: str,
        working_dir: str,
    ) -> OpenCodeSessionManager:
        """Build a manager for an existing session and start its loop.

        Ports ``Server.loadSessionIntoMemory`` (server.go:65-94) but
        also handles the case where the session is being created for
        the first time (no persisted state yet).
        """
        # Get the persisted state from the repository (may be just-created
        # with all defaults).
        record = self._repository.get(project, session_name) or {
            "project": project,
            "session_name": session_name,
            "id": session_id,
            "working_dir": working_dir,
            "last_agent": "",
            "is_agent_locked": False,
            "state": IDLE.value,
            "latest_response": None,
            "questions": [],
            "last_activity": _now_rfc3339(),
        }

        persisted = PersistedState(
            last_agent=record.get("last_agent", ""),
            is_agent_locked=record.get("is_agent_locked", False),
            state=record.get("state", IDLE.value),
            latest_response=record.get("latest_response"),
            questions=record.get("questions", []),
            last_activity=record.get("last_activity", _now_rfc3339()),
        )

        # Build the manager with the persistence callback wired up
        # server.go:80-93
        manager = OpenCodeSessionManager(
            session_id=session_id,
            working_dir=working_dir,
            persisted_state=persisted,
            on_state_change=self._make_state_change_callback(project, session_name),
        )

        # Start the background loop
        manager.start()

        async with self._managers_lock:
            # Don't double-register
            if session_id not in self._managers:
                self._managers[session_id] = manager

        logger.info(
            "Loaded session into memory: %s %s (ID: %s, Dir: %s, State: %s)",
            project,
            session_name,
            session_id,
            working_dir,
            record.get("state", IDLE.value),
        )
        return manager

    async def load_session_into_memory(self, session_id: str) -> OpenCodeSessionManager | None:
        """Load a session into the in-memory map on demand.

        Ports the body of the ``GET_SESSION`` handler in
        ``server.go`` lines 384-408 — used when a caller references a
        session by ID and the daemon hasn't loaded it yet.
        """
        record = self._repository.find_by_id(session_id)
        if record is None:
            return None
        project = record.get("project", "")
        session_name = record.get("session_name", "")
        working_dir = record.get("working_dir") or ""
        return await self._load_manager_into_memory(
            project=project,
            session_name=session_name,
            session_id=session_id,
            working_dir=working_dir,
        )

    # ── Crash recovery ────────────────────────────────────────────────────

    async def recover_from_registry(self) -> int:
        """Load every persisted session into memory on startup.

        Ports the auto-recovery block in ``Server.Start`` (server.go
        lines 115-144). For each row in the ``opencode_sessions`` table,
        we build a manager, wire up persistence, and start its loop.

        Returns:
            Number of sessions recovered.
        """
        sessions = self._repository.list()
        for record in sessions:
            project = record["project"]
            session_name = record["session_name"]
            session_id = record.get("id")
            if not session_id:
                logger.warning(
                    "skipping session %s/%s: no id in repository",
                    project,
                    session_name,
                )
                continue
            working_dir = record.get("working_dir") or ""
            try:
                manager = await self._load_manager_into_memory(
                    project=project,
                    session_name=session_name,
                    session_id=session_id,
                    working_dir=working_dir,
                )
                # Sync with OpenCode to reconcile state after crash/restart
                try:
                    await manager.sync_state_with_open_code()
                except Exception as exc:
                    logger.warning(
                        "Failed to sync recovered session %s with OpenCode: %s",
                        session_id, exc,
                    )
            except Exception as exc:
                logger.warning(
                    "failed to recover session %s/%s: %s",
                    project,
                    session_name,
                    exc,
                )
        logger.info("Recovered %d session(s) from registry", len(sessions))
        return len(sessions)

    # ── /start-work lock ──────────────────────────────────────────────────

    async def handle_start_work(
        self,
        project: str,
        session_name: str,
        agent: str = "atlas",
    ) -> None:
        """Lock the session's agent to ``agent`` (``"atlas"`` by default).

        Ports the body of the ``/start-work`` block in ``server.go``
        lines 436-444:

            if sessionData, err := s.registry.FindByID(...); err == nil {
                if err := s.registry.UpdateAgentState(
                    sessionData.Project, sessionData.SessionName,
                    "atlas", true,
                ); err != nil {
                    log.Printf("Failed to lock agent for session %s: %v", ...)
                }
            }

        Args:
            project: Project identifier.
            session_name: Session name.
            agent: The agent to lock to. Defaults to ``"atlas"`` per
                ``constants.START_WORK_AGENT``.
        """
        try:
            self._repository.update_agent_state(
                project=project,
                session_name=session_name,
                last_agent=agent,
                is_locked=True,
            )
            logger.info("Locked agent to '%s' for session %s/%s", agent, project, session_name)
        except KeyError as exc:
            logger.warning("Failed to lock agent for session %s/%s: %s", project, session_name, exc)

    # ── State persistence callback ────────────────────────────────────────

    def _make_state_change_callback(self, project: str, session_name: str):
        """Build the ``OnStateChange`` callback for one session.

        Ports the body of ``Server.setupStatePersistence``
        (server.go:417-428). The callback is called by the manager
        whenever state changes; we translate the in-memory snapshot
        into a row update.
        """
        async def on_state_change(state: PersistedState) -> None:
            try:
                self._repository.update_session_data(
                    project=project,
                    session_name=session_name,
                    last_agent=state.last_agent,
                    is_agent_locked=state.is_agent_locked,
                    state=state.state,
                    latest_response=state.latest_response,
                    questions=state.questions,
                    last_activity=state.last_activity,
                )
            except Exception as exc:
                logger.warning(
                    "failed to persist state for %s/%s: %s",
                    project,
                    session_name,
                    exc,
                )

        return on_state_change

    # ── Cleanup ───────────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Stop all running managers. Used at daemon shutdown."""
        async with self._managers_lock:
            managers = list(self._managers.values())
            self._managers.clear()
        for manager in managers:
            try:
                await manager.stop()
            except Exception as exc:
                logger.warning("error stopping manager: %s", exc)
