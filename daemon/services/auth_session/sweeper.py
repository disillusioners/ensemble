"""Auth-session background sweeper — oauth2-auth Phase 2 (T2.4).

Two steady-state loops over the auth tables (config-gated by
``auth.enabled`` — with auth OFF nothing here constructs, T2.8/FR-2):

* **Session/token loop** (default 300 s): batches of 1000 expired rows
  deleted from ``auth_sessions`` and ``auth_bearer_tokens``.
* **State loop** (default 60 s): deletes ``auth_states`` rows that are
  consumed (any age) OR expired — states are short-lived.

Lifecycle pattern (AMEND v1.2 sweeper conditions): created in the
lifespan via :func:`daemon.services.auth_session.lifecycle.start_auth_background`
following the readiness-refresher task pattern — cancelled AND awaited
on shutdown (NOT the manager's fire-and-forget variant).

Connection hygiene (2026-10-10 checkpoint-pool incident sensitivity):
each batch acquires and releases its own connection inside the
repository — no connection is held across the sweep, and the async
boundary is a per-batch ``asyncio.to_thread`` hop. The per-batch
deadline (default 8 s, inside the plan's 5-10 s window) is enforced
with ``asyncio.wait_for`` — a wedged backend stalls at most one batch
per tick, then the tick logs a WARNING and the loop continues. This is
the portable spelling of the plan's "per-batch statement timeout": a
PG-side ``SET LOCAL statement_timeout`` would be a dialect branch
(D17 forbids runtime dialect branches) — the intent (wedged backend
cannot stall the sweeper) is met identically on both backends.

AMEND v1.3 / F2 note: the batched delete's ``FOR UPDATE SKIP LOCKED``
rendering is PG-only and handled INSIDE SQLAlchemy
(``with_for_update(skip_locked=True)`` silently no-ops on SQLite —
plain batched DELETE). The dual shape is asserted by tests, not by any
runtime branch here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from daemon.repositories.auth_session.repository import AuthSessionRepository
from daemon.services.timestamps import now_utc_iso

logger = logging.getLogger(__name__)

#: Default session/token sweep interval (plan D2.4: 5 min).
DEFAULT_SESSION_INTERVAL_SECONDS = 300

#: Default state sweep interval (plan D2.4: 60 s).
DEFAULT_STATE_INTERVAL_SECONDS = 60

#: Default delete batch size (plan D2.4: batches of 1000).
DEFAULT_BATCH_SIZE = 1000

#: Default per-batch deadline (plan window 5-10 s; portable wait_for).
DEFAULT_BATCH_TIMEOUT_SECONDS = 8.0


class AuthSessionSweeper:
    """Background sweeper over the four auth tables (see module doc).

    ``boot_hook`` is an optional coroutine factory fired ONCE at task
    start (best-effort — exceptions log WARN and never fatal). The
    lifespan wiring passes the ``OAuthClient.discovery`` boot-time
    fetch here so the fetch happens at lifespan without stalling boot
    (NFR-7/C4: unreachable IdP ⇒ WARN, daemon serves).
    """

    def __init__(
        self,
        repository: AuthSessionRepository,
        *,
        session_interval_seconds: int = DEFAULT_SESSION_INTERVAL_SECONDS,
        state_interval_seconds: int = DEFAULT_STATE_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        batch_timeout_seconds: float = DEFAULT_BATCH_TIMEOUT_SECONDS,
        now_iso_fn: Callable[[], str] = now_utc_iso,
        boot_hook: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        self._repository = repository
        self._session_interval = session_interval_seconds
        self._state_interval = state_interval_seconds
        self._batch_size = batch_size
        self._batch_timeout = batch_timeout_seconds
        self._now_iso_fn = now_iso_fn
        self._boot_hook = boot_hook

    # ── public surface ──────────────────────────────────────────────

    async def run(self) -> None:
        """Run both loops until cancelled (lifespan task body)."""
        if self._boot_hook is not None:
            await self._run_boot_hook()
        session_loop = asyncio.create_task(
            self._loop(
                self._sweep_sessions_tokens_once,
                self._session_interval,
                "sessions+tokens",
            ),
            name="auth-sweeper-sessions",
        )
        state_loop = asyncio.create_task(
            self._loop(self._sweep_states_once, self._state_interval, "states"),
            name="auth-sweeper-states",
        )
        try:
            await asyncio.gather(session_loop, state_loop)
        finally:
            for task in (session_loop, state_loop):
                if not task.done():
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    # ── loop plumbing ───────────────────────────────────────────────

    async def _run_boot_hook(self) -> None:
        try:
            await self._boot_hook()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — best-effort by contract
            logger.warning(
                "[AuthSweeper] boot hook failed (non-fatal, sweep continues): "
                "%s: %s",
                type(exc).__name__,
                exc,
            )

    async def _loop(
        self,
        tick: Callable[[], Awaitable[None]],
        interval_seconds: int,
        label: str,
    ) -> None:
        """Run one tick immediately, then every ``interval_seconds``.

        Exits via cancellation (CancelledError → clean return).
        """
        while True:
            try:
                await tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the loop NEVER dies
                logger.warning(
                    "[AuthSweeper] %s sweep tick failed (continuing): %s: %s",
                    label,
                    type(exc).__name__,
                    exc,
                )
            await asyncio.sleep(interval_seconds)

    # ── ticks ───────────────────────────────────────────────────────

    async def _sweep_sessions_tokens_once(self) -> int:
        """One session+token sweep tick (batches of ``batch_size``).

        Returns the total rows deleted across both tables (0 when the
        per-batch deadline skipped the tick)."""
        cutoff = self._now_iso_fn()
        deleted_sessions = await self._bounded_batch(
            lambda: self._repository.sweep_expired_sessions(
                cutoff_iso=cutoff, batch_size=self._batch_size
            ),
            label="auth_sessions",
        )
        deleted_tokens = await self._bounded_batch(
            lambda: self._repository.sweep_expired_bearer_tokens(
                cutoff_iso=cutoff, batch_size=self._batch_size
            ),
            label="auth_bearer_tokens",
        )
        if deleted_sessions or deleted_tokens:
            logger.info(
                "[AuthSweeper] sessions+tokens sweep: deleted %d session(s), "
                "%d bearer token(s) (cutoff=%s)",
                deleted_sessions,
                deleted_tokens,
                cutoff,
            )
        return deleted_sessions + deleted_tokens

    async def _sweep_states_once(self) -> None:
        """One state sweep tick (consumed + expired rows)."""
        cutoff = self._now_iso_fn()
        deleted = await self._bounded_batch(
            lambda: self._repository.sweep_expired_states(cutoff_iso=cutoff),
            label="auth_states",
        )
        if deleted:
            logger.info(
                "[AuthSweeper] states sweep: deleted %d state(s) (cutoff=%s)",
                deleted,
                cutoff,
            )

    async def _bounded_batch(self, fn: Callable[[], object], *, label: str) -> int:
        """Run one DB batch off-loop under the per-batch deadline.

        The ``asyncio.wait_for`` deadline is the portable per-batch
        timeout: a wedged backend costs at most one deadline per tick —
        never the loop. Sync repository work runs via
        ``asyncio.to_thread`` (project standard bridge).
        """
        try:
            return int(
                await asyncio.wait_for(
                    asyncio.to_thread(fn), timeout=self._batch_timeout
                )
            )
        except TimeoutError:
            logger.warning(
                "[AuthSweeper] %s sweep batch exceeded the %.1fs per-batch "
                "deadline — skipping this tick (backend may be wedged)",
                label,
                self._batch_timeout,
            )
            return 0


__all__ = [
    "AuthSessionSweeper",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_BATCH_TIMEOUT_SECONDS",
    "DEFAULT_SESSION_INTERVAL_SECONDS",
    "DEFAULT_STATE_INTERVAL_SECONDS",
]
