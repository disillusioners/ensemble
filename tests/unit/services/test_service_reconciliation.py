"""Unit tests for the Phase-1 ``ServiceReconciliationService`` skeleton.

Service-tool Phase 1.C task 1.C.12 — lifecycle shell only:

* importability + module constants;
* constructor contract (A9 repo injected DIRECTLY; A8 — NO
  ``max_concurrent`` param, the cap lives on ``ServiceToolManager``);
* ``start()`` idempotence (double-start is a silent no-op — one task);
* ``stop()`` template semantics (``set()`` → ``cancel()`` → ``await``
  with ``CancelledError`` swallowed) + safe when never started;
* ``sweep_once()`` stub shape — the counters dict keys are pinned NOW
  so the A6 guaranteed boot pass and the lifespan boot log format are
  stable across the Phase-2 body fill.

Pattern: ``tests/unit/services/`` neighbors of the
``EligiblePendingSweepService`` template. All async tests run on the
module-scoped event-loop fixture (plain ``asyncio.run`` per test —
each test gets a fresh loop, matching the template's stop/await
semantics).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from daemon.services.service_reconciliation import (
    DEFAULT_STARTING_GRACE_SECONDS,
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    ServiceReconciliationService,
)


# ── fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def repo() -> MagicMock:
    """A ServiceRepo stand-in — the Phase-1 stub never calls it, but
    the constructor contract (A9: repo injected directly) is pinned
    by passing it here."""
    return MagicMock(name="ServiceRepo")


# ── importability + constants ───────────────────────────────────────


class TestModuleContract:
    def test_module_constants_pinned(self) -> None:
        """Defaults share the house sweep cadence (90s) and the A3
        starting-grace window (30s) — module-level so the config
        layer can reference them."""
        assert DEFAULT_SWEEP_INTERVAL_SECONDS == 90
        assert DEFAULT_STARTING_GRACE_SECONDS == 30

    def test_constructor_accepts_repo_positionally(self, repo) -> None:
        """A9: the narrowest collaborator (ServiceRepo) is the FIRST
        constructor arg — injected directly, NOT via ServiceManager."""
        svc = ServiceReconciliationService(repo, 120, 45)
        assert svc.interval_seconds == 120
        assert svc.starting_grace_seconds == 45

    def test_constructor_defaults(self, repo) -> None:
        svc = ServiceReconciliationService(repo)
        assert svc.interval_seconds == DEFAULT_SWEEP_INTERVAL_SECONDS
        assert svc.starting_grace_seconds == DEFAULT_STARTING_GRACE_SECONDS

    def test_interval_floor_clamped_in_ctor(self, repo) -> None:
        """Direct construction with a sub-1 interval clamps to 1 —
        the loop can never spin (the authoritative ge=1 floor is the
        pydantic Field at config level; this is the second line of
        defence for legacy fixtures)."""
        svc = ServiceReconciliationService(repo, interval_seconds=0)
        assert svc.interval_seconds == 1

    def test_no_max_concurrent_param(self, repo) -> None:
        """A8: the dead ``max_concurrent`` param is DROPPED — the cap
        lives on ServiceToolManager; the reconcile service only marks
        rows EXITED and never enforces capacity."""
        with pytest.raises(TypeError):
            ServiceReconciliationService(repo, max_concurrent=5)  # type: ignore[call-arg]


# ── lifecycle ───────────────────────────────────────────────────────


class TestLifecycle:
    def test_start_is_idempotent(self, repo) -> None:
        """Double start() spawns ONE task — the second call is a
        silent no-op (template pattern)."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo)
            await svc.start()
            first_task = svc._task
            assert first_task is not None and not first_task.done()
            await svc.start()  # silent no-op
            assert svc._task is first_task, (
                "double start() must not respawn the sweep task"
            )
            await svc.stop()

        asyncio.run(scenario())

    def test_stop_when_never_started_is_safe(self, repo) -> None:
        """stop() on a never-started service is a silent no-op — the
        shutdown path must survive partial startup failures."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo)
            await svc.stop()  # must not raise
            assert svc._task is None

        asyncio.run(scenario())

    def test_stop_cancels_and_awaits_the_task(self, repo) -> None:
        """A8 template semantics: stop() sets the stop event, cancels
        the task, awaits it (CancelledError swallowed) and clears the
        handle — no un-awaited cancelled task leak."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            await svc.start()
            task = svc._task
            assert task is not None
            await svc.stop()
            assert task.done(), "stop() must leave the task done"
            assert svc._task is None

        asyncio.run(scenario())

    def test_restart_after_stop(self, repo) -> None:
        """start() after a clean stop() respawns the task (restart
        symmetry — the lifespan may stop then re-start in tests)."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo)
            await svc.start()
            first = svc._task
            await svc.stop()
            await svc.start()
            second = svc._task
            assert second is not None and second is not first
            await svc.stop()

        asyncio.run(scenario())


# ── sweep stub shape ────────────────────────────────────────────────


class TestSweepOnceStub:
    def test_sweep_once_returns_zeroed_counters(self, repo) -> None:
        """The Phase-1 stub returns the EXACT counters dict shape the
        Phase-2 body must preserve (the A6 boot pass and the
        ``[ServiceTool] reconcile_boot_sweep`` lifespan log index into
        these keys)."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo)
            counters = await svc.sweep_once()
            assert counters == {
                "alive": 0,
                "reaped": 0,
                "errors": 0,
                "starting_reaped": 0,
            }

        asyncio.run(scenario())

    def test_sweep_once_ticks_the_counter(self, repo) -> None:
        """Each call bumps the public tick counter (observability)."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo)
            await svc.sweep_once()
            await svc.sweep_once()
            assert svc.ticks_total == 2
            assert svc.counters() == {"ticks_total": 2, "sweep_errors": 0}

        asyncio.run(scenario())

    def test_loop_survives_a_raising_sweep(self, repo) -> None:
        """Defense-in-depth: a sweep body that raises is swallowed +
        counted and the loop keeps ticking (the Phase-2 body inherits
        this guarantee — a bad row must never kill the sweep)."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            calls = {"n": 0}

            async def boom() -> dict[str, int]:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("synthetic tick failure")
                return {"alive": 0, "reaped": 0, "errors": 0, "starting_reaped": 0}

            svc.sweep_once = boom  # type: ignore[method-assign]
            await svc.start()
            # Poll until the second tick lands (bounded wait — never
            # rely on real sleep timing in CI).
            for _ in range(200):
                if calls["n"] >= 2:
                    break
                await asyncio.sleep(0.01)
            await svc.stop()
            assert calls["n"] >= 2, "loop must tick past a raising body"
            assert svc.sweep_errors >= 1, "the failed tick must be counted"

        asyncio.run(scenario())
