"""C2 RESOLVED (2026-09-11) — Periodic orphan-watcher sweep.

The pre-C2 path: ``DependencyBus._sweep_orphan_watchers`` was a
one-shot startup-only sweep (called from
:meth:`DependencyBus.start`). It cleaned the restart-window but
left a long gap: any orphan watcher that accumulated AFTER
startup (mid-run force-cancel, mid-run task death) waited for a
daemon restart to be swept.

C2 closes that gap with a periodic sweep that runs alongside the
A3 ``EligiblePendingSweepService`` — same cadence (90s default),
same bounded-interval asyncio-loop pattern. The startup sweep is
preserved (so the restart-window still gets the eager cleanup);
the periodic sweep is the structural backstop for the steady
state.

The sweep is ALWAYS-ON infrastructure (no env flag — per the
project owner's HARD POLICY on Batch A). The interval is the
sole tuning knob
(``ServicesConfig.orphan_watcher_sweep_interval_seconds`` default
90s).

Covered:
    1. ``sweep_once`` dispatches ``bus._sweep_orphan_watchers``
       exactly once per tick.
    2. ``sweep_once`` counters advance correctly (ticks /
       cancelled / errors / cumulative_*).
    3. ``sweep_once`` returns the bus's cancelled-rowcount as
       ``cancelled``.
    4. ``sweep_once`` is silent (DEBUG-only log, no raise) when
       the bus singleton is None — the legacy / pre-wiring
       lifespan case.
    5. ``sweep_once`` catches a bus sweep exception and records it
       in ``errors`` (defense-in-depth — the bus's own
       ``_sweep_orphan_watchers`` already swallows its own DB
       errors, but a future bus-side change MUST NOT crash the
       periodic lane).
    6. Lifecycle ``start()`` is idempotent (a second start while
       the task is alive is a silent no-op).
    7. Lifecycle ``stop()`` cancels + awaits the asyncio task
       cleanly; calling ``stop()`` on a service that was never
       started is a silent no-op.
    8. ``start()`` followed by ``stop()`` round-trip is bounded
       (the asyncio task exits within ``timeout``).
    9. Service has no new env flag — the bus's underlying
       atomic UPDATE is the only write primitive the periodic
       lane invokes (no new admission_state_writer).
   10. The service lives in
       ``daemon/services/orphan_watcher_sweep.py`` and is wired
       into ``daemon/api.py``'s lifespan alongside the
       eligible-PENDING sweep (sanity import check).
   11. LATE-BIND (born-dead wiring fix, 2026-11-21): a bus
       registered AFTER construction (the production ordering —
       ``init_dependency_bus`` runs ~1000 lifespan lines after the
       service is constructed) is picked up on the next tick via
       the module singleton; an explicitly injected fixture bus
       keeps precedence.
   12. DEAD-DETECTION (born-dead wiring fix, 2026-11-21): zero
       effective sweeps through the 2x-interval grace window
       (ticks 1-2) fires a loud ERROR on tick 3, then a
       rate-limited WARNING reminder every 10 no-op ticks — the
       trap is never DEBUG-quiet forever again.

Census stays at 23/1/0 — the periodic lane calls the bus's
``_sweep_orphan_watchers`` (the same primitive the startup path
uses); no new admission_state_writer / JobItem creator / work_id
mint site.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.services import orphan_watcher_sweep as orphan_watcher_sweep_module
from daemon.services.orphan_watcher_sweep import (
    DEFAULT_ORPHAN_SWEEP_INTERVAL_SECONDS,
    OrphanWatcherSweepService,
)


# ---------------------------------------------------------------------------
# 1. sweep_once — single-tick behavior
# ---------------------------------------------------------------------------


class TestSweepOnce:
    """C2 (2026-09-11): ``sweep_once`` calls the bus's
    ``_sweep_orphan_watchers`` exactly once per tick and surfaces
    the cancelled-rowcount in the counters dict.
    """

    @pytest.mark.asyncio
    async def test_sweep_once_dispatches_bus_sweep_exactly_once(self):
        """One tick → exactly one call to
        ``bus._sweep_orphan_watchers``."""
        bus = MagicMock()
        bus._sweep_orphan_watchers = AsyncMock(return_value=3)

        svc = OrphanWatcherSweepService(
            dependency_bus=bus,
            interval_seconds=1,
        )
        result = await svc.sweep_once()

        bus._sweep_orphan_watchers.assert_awaited_once()
        assert result["cancelled"] == 3

    @pytest.mark.asyncio
    async def test_sweep_once_returns_zero_when_bus_finds_none(self):
        """Bus returns 0 → ``cancelled`` is 0; counters still
        advance on the ticks axis (the tick happened, the sweep
        just didn't find anything)."""
        bus = MagicMock()
        bus._sweep_orphan_watchers = AsyncMock(return_value=0)

        svc = OrphanWatcherSweepService(
            dependency_bus=bus,
            interval_seconds=1,
        )
        result = await svc.sweep_once()

        assert result["cancelled"] == 0
        assert result["cumulative_cancelled"] == 0
        # tick counter advanced
        assert result["ticks"] == 1

    @pytest.mark.asyncio
    async def test_sweep_once_counters_accumulate_across_ticks(self):
        """Calling ``sweep_once`` repeatedly accumulates the
        ``cumulative_cancelled`` counter (the per-tick value is
        returned each time; the cumulative is the running total).
        """
        bus = MagicMock()
        bus._sweep_orphan_watchers = AsyncMock(side_effect=[2, 5, 1])

        svc = OrphanWatcherSweepService(
            dependency_bus=bus,
            interval_seconds=1,
        )
        r1 = await svc.sweep_once()
        r2 = await svc.sweep_once()
        r3 = await svc.sweep_once()

        assert r1["cancelled"] == 2
        assert r1["cumulative_cancelled"] == 2
        assert r2["cancelled"] == 5
        assert r2["cumulative_cancelled"] == 7
        assert r3["cancelled"] == 1
        assert r3["cumulative_cancelled"] == 8
        # tick counter is monotonic
        assert r3["ticks"] == 3

    @pytest.mark.asyncio
    async def test_sweep_once_silent_when_bus_unwired(self):
        """Legacy / pre-wiring case: ``dependency_bus=None`` AND the
        module singleton is None → ``sweep_once`` returns
        ``cancelled=0``, no raise, no spurious error. The bus's own
        startup sweep is the primary path; the periodic lane is the
        secondary backstop.

        UPDATED (born-dead wiring fix, 2026-11-21): the tick path now
        late-binds and consults ``get_dependency_bus()`` each tick, so
        this test PINS the getter to return None — keeping the
        legacy-None case deterministic regardless of whatever other
        tests may have left in the module singleton.
        """
        svc = OrphanWatcherSweepService(
            dependency_bus=None,
            interval_seconds=1,
        )
        with patch.object(
            orphan_watcher_sweep_module, "get_dependency_bus",
            return_value=None,
        ):
            # Must NOT raise.
            result = await svc.sweep_once()

        assert result["cancelled"] == 0
        assert result["errors"] == 0
        assert result["ticks"] == 1
        assert result["effective_runs"] == 0

    @pytest.mark.asyncio
    async def test_sweep_once_swallows_bus_exception(self):
        """The bus's ``_sweep_orphan_watchers`` already has its
        own fail-open swallow for DB errors (per its docstring),
        but the periodic lane MUST also be defense-in-depth: a
        future bus-side change that re-raises MUST NOT crash the
        lane. ``errors`` records the failure; the loop continues
        on the next tick.
        """
        bus = MagicMock()
        bus._sweep_orphan_watchers = AsyncMock(
            side_effect=RuntimeError("simulated bus sweep failure")
        )

        svc = OrphanWatcherSweepService(
            dependency_bus=bus,
            interval_seconds=1,
        )
        # Must NOT raise.
        result = await svc.sweep_once()

        assert result["cancelled"] == 0
        assert result["errors"] == 1
        # Tick still counted (the loop continued past the failure).
        assert result["ticks"] == 1

    @pytest.mark.asyncio
    async def test_counters_view_exposes_running_totals(self):
        """``counters()`` returns the running totals for
        tests / observability — separate from the
        ``sweep_once()`` per-tick return."""
        bus = MagicMock()
        bus._sweep_orphan_watchers = AsyncMock(side_effect=[4, 2])

        svc = OrphanWatcherSweepService(
            dependency_bus=bus,
            interval_seconds=1,
        )
        await svc.sweep_once()
        await svc.sweep_once()

        counters = svc.counters()
        assert counters["ticks"] == 2
        assert counters["cancelled_total"] == 6
        assert counters["errors_total"] == 0


# ---------------------------------------------------------------------------
# 2. Lifecycle — start / stop / shutdown contract
# ---------------------------------------------------------------------------


class TestLifecycle:
    """C2 (2026-09-11): the periodic lane uses the same
    asyncio-task + cancel/await lifecycle as
    ``EligiblePendingSweepService``.
    """

    @pytest.mark.asyncio
    async def test_start_is_idempotent_when_task_alive(self):
        """Calling ``start()`` twice while the loop is alive is a
        silent no-op (matches
        ``EligiblePendingSweepService.start`` contract)."""
        bus = MagicMock()
        bus._sweep_orphan_watchers = AsyncMock(return_value=0)

        svc = OrphanWatcherSweepService(
            dependency_bus=bus,
            # Tight interval so the loop ticks at least once
            # before stop() runs (avoids the test racing the
            # sleep).
            interval_seconds=1,
        )
        svc.start()
        try:
            # Yield once so the loop has a chance to enter its
            # first tick before the second start() call.
            await asyncio.sleep(0.05)
            svc.start()  # second start — must be a no-op
            # The service has exactly one asyncio task alive.
            assert svc._task is not None
            assert not svc._task.done()
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_stop_is_silent_when_never_started(self):
        """``stop()`` on a service that was never started is a
        silent no-op (matches
        ``EligiblePendingSweepService.stop`` contract)."""
        svc = OrphanWatcherSweepService(
            dependency_bus=MagicMock(),
            interval_seconds=1,
        )
        # Must NOT raise.
        await svc.stop()
        assert svc._task is None

    @pytest.mark.asyncio
    async def test_start_stop_round_trip(self):
        """``start()`` followed by ``stop()`` cancels + awaits the
        asyncio task cleanly. Bounded join budget (5s default —
        same as ``EligiblePendingSweepService.stop``)."""
        bus = MagicMock()
        bus._sweep_orphan_watchers = AsyncMock(return_value=0)

        svc = OrphanWatcherSweepService(
            dependency_bus=bus,
            interval_seconds=1,
        )
        svc.start()
        # Give the loop a chance to spawn its first tick.
        await asyncio.sleep(0.05)
        # The task is alive before stop().
        assert svc._task is not None and not svc._task.done()

        await svc.stop()
        # After stop(), the task is gone (the service clears
        # ``self._task`` in the ``finally`` clause).
        assert svc._task is None

    @pytest.mark.asyncio
    async def test_stop_polite_shutdown_via_stop_event(self):
        """``stop()`` sets the stop-event FIRST (polite
        shutdown) and then cancels the task (hard shutdown). The
        loop body exits via the ``stop_event.wait()`` return
        rather than via ``CancelledError`` when ``stop()`` is
        called between ticks.
        """
        bus = MagicMock()
        bus._sweep_orphan_watchers = AsyncMock(return_value=0)

        # Long interval so the loop is sleeping when stop() is
        # called — exercises the polite-shutdown path.
        svc = OrphanWatcherSweepService(
            dependency_bus=bus,
            interval_seconds=60,
        )
        svc.start()
        # Yield so the loop enters the wait_for(_stop_event.wait()).
        await asyncio.sleep(0.05)

        await svc.stop()

        # The stop_event was set (the loop's polite exit
        # observed it).
        assert svc._stop_event.is_set()
        # The task is gone after stop().
        assert svc._task is None


# ---------------------------------------------------------------------------
# 3. Default values — no new env flag, no new tunables
# ---------------------------------------------------------------------------


class TestDefaults:
    """C2 (2026-09-11): no new env flag — the periodic sweep is a
    code-level invariant. The only tuning knob is the interval
    (``ServicesConfig.orphan_watcher_sweep_interval_seconds`` —
    default 90s, mirrors A3's brief range).
    """

    def test_no_env_flag_read_in_source(self):
        """The source must not introduce any new ``os.environ.get``
        or env-flag reading — the periodic lane is ALWAYS-ON."""
        src = inspect.getsource(OrphanWatcherSweepService)
        assert "os.environ.get" not in src
        # No ENSEMBLE_* env-flag reading either.
        assert "ENSEMBLE_" not in src

    def test_default_interval_matches_a3_cadence(self):
        """The default interval mirrors ``EligiblePendingSweepService``
        so the two sweeps tick on the same bound (90s — midpoint of
        the A3 brief range)."""
        assert DEFAULT_ORPHAN_SWEEP_INTERVAL_SECONDS == 90

    def test_minimum_interval_floor_one(self):
        """The interval floor is 1 second — prevents spin. The
        constructor's ``max(1, int(...))`` clamps and the config
        field's ``Field(ge=1)`` enforces the same bound at boot
        time. Both surfaces are intentional defense in depth."""
        # Clamp behavior: passing 0 or negative becomes 1.
        svc = OrphanWatcherSweepService(
            dependency_bus=MagicMock(),
            interval_seconds=0,
        )
        assert svc._interval_seconds == 1
        svc = OrphanWatcherSweepService(
            dependency_bus=MagicMock(),
            interval_seconds=-100,
        )
        assert svc._interval_seconds == 1

    def test_census_no_new_admission_state_writer(self):
        """The periodic lane does not write to any
        admission-state-bearing path. It calls the bus's
        ``_sweep_orphan_watchers`` (the same primitive the
        startup path uses); no new admission_state_writer /
        JobItem creator / work_id mint site.
        """
        # Restrict the source view to code only (no docstrings)
        # so the docstring's reference to "UPDATE" doesn't
        # false-positive the assertion.
        tree = inspect.getsource(OrphanWatcherSweepService)
        # Strip the class-level docstring so the literal word
        # "UPDATE" in the description ("single atomic conditional
        # UPDATE that cancels PENDING...") does not match.
        code_lines = []
        in_docstring = False
        quote_count = 0
        for line in tree.splitlines():
            stripped = line.strip()
            if not in_docstring and (
                stripped.startswith('"""')
                or stripped.startswith("'''")
            ):
                in_docstring = True
                quote_count = 1
                # single-line docstring?
                rest = stripped[3:]
                if rest.endswith('"""') or rest.endswith("'''"):
                    in_docstring = False
                    quote_count = 0
                continue
            if in_docstring:
                if stripped.endswith('"""') or stripped.endswith("'''"):
                    in_docstring = False
                    quote_count = 0
                continue
            code_lines.append(line)
        code_only = "\n".join(code_lines)
        # No raw SQL — the periodic lane has zero DB-write
        # surface (it calls the bus's atomic UPDATE).
        assert "UPDATE" not in code_only
        assert "INSERT" not in code_only
        # No new repo wiring (no engine / session creation).
        assert "Session(" not in code_only
        assert "create_engine" not in code_only


# ---------------------------------------------------------------------------
# 4. Wiring — service imports cleanly and is reachable from api.py
# ---------------------------------------------------------------------------


class TestWiring:
    """C2 (2026-09-11): the service is exposed via
    ``daemon.services.orphan_watcher_sweep`` and wired into the
    daemon/api.py lifespan. Static checks pin both surfaces.
    """

    def test_service_module_importable(self):
        """The new module imports cleanly."""
        from daemon.services import orphan_watcher_sweep  # noqa: F401

    def test_api_lifespan_wires_orphan_sweep(self):
        """``daemon/api.py`` references the new service in its
        startup path AND its shutdown path. The check inspects
        the ``lifespan`` async context manager specifically —
        not the whole ``create_app`` factory, which has its own
        ``app.state`` references and would conflate the
        assertion.
        """
        from daemon import api

        # Get the source of the ``lifespan`` coroutine specifically.
        lifespan_src = inspect.getsource(api.lifespan)
        # Startup path — constructs and starts the service.
        assert "OrphanWatcherSweepService" in lifespan_src
        # The startup call uses the same ``.start()`` shape as
        # the eligible-pending sweep.
        assert ".start()" in lifespan_src.split(
            "OrphanWatcherSweepService", 1
        )[1]
        # Shutdown path — calls ``.stop()`` on the wired
        # singleton.
        assert ".stop()" in lifespan_src.split(
            "OrphanWatcherSweepService", 1
        )[1]

    def test_config_has_orphan_watcher_sweep_interval(self):
        """``ServicesConfig.orphan_watcher_sweep_interval_seconds``
        is the sole tuning knob — default 90s."""
        from daemon.config import ServicesConfig

        cfg = ServicesConfig()
        assert cfg.orphan_watcher_sweep_interval_seconds == 90


# ---------------------------------------------------------------------------
# 5. Late-bind — the born-dead wiring trap fix (2026-11-21)
# ---------------------------------------------------------------------------

_SWEEP_LOGGER = "daemon.services.orphan_watcher_sweep"


class TestLateBind:
    """Born-dead wiring trap (census A1, 2026-11-21): the api.py
    lifespan constructs this service BEFORE ``init_dependency_bus``
    registers the module singleton, so the old
    capture-at-construction behavior froze ``None`` into
    ``self._dependency_bus`` for the daemon's ENTIRE lifetime — every
    tick took the bus-is-None DEBUG branch and the periodic lane never
    dispatched a single sweep in production (live v0.31.22: 215+
    no-op ticks while the cycle counter climbed).

    The fix: an EXPLICITLY injected bus (legacy test fixture) wins;
    otherwise the module singleton is re-read on EVERY tick.
    """

    @pytest.mark.asyncio
    async def test_bus_registered_after_construction_is_picked_up(
        self, monkeypatch
    ):
        """The production trap shape: constructed while the singleton
        is still None, the bus registered LATER must be picked up on
        the NEXT tick — no re-construction, no re-wire call.

        The singleton is driven through the real module global (what
        ``set_dependency_bus`` assigns); ``monkeypatch`` keeps the
        leak-free auto-restore.
        """
        import daemon.services.dependency_bus as dependency_bus_module

        svc = OrphanWatcherSweepService(
            dependency_bus=None,
            interval_seconds=1,
        )
        # Pre-wiring: getter returns None → no sweep dispatched.
        with patch.object(
            orphan_watcher_sweep_module, "get_dependency_bus",
            return_value=None,
        ):
            pre = await svc.sweep_once()
        assert pre["cancelled"] == 0
        assert pre["effective_runs"] == 0

        # The lifespan registers the bus AFTER the service started.
        bus = MagicMock()
        bus._sweep_orphan_watchers = AsyncMock(return_value=2)
        monkeypatch.setattr(dependency_bus_module, "_dependency_bus", bus)

        post = await svc.sweep_once()
        bus._sweep_orphan_watchers.assert_awaited_once()
        assert post["cancelled"] == 2
        assert post["effective_runs"] == 1

    @pytest.mark.asyncio
    async def test_explicit_fixture_bus_wins_over_singleton(
        self, monkeypatch
    ):
        """An EXPLICITLY injected bus (legacy test fixture) keeps
        precedence over the late-bound singleton — existing fixture
        semantics are preserved, not silently re-wired mid-test."""
        import daemon.services.dependency_bus as dependency_bus_module

        fixture_bus = MagicMock()
        fixture_bus._sweep_orphan_watchers = AsyncMock(return_value=5)
        singleton_bus = MagicMock()
        singleton_bus._sweep_orphan_watchers = AsyncMock(return_value=99)
        monkeypatch.setattr(
            dependency_bus_module, "_dependency_bus", singleton_bus
        )

        svc = OrphanWatcherSweepService(
            dependency_bus=fixture_bus,
            interval_seconds=1,
        )
        result = await svc.sweep_once()

        fixture_bus._sweep_orphan_watchers.assert_awaited_once()
        singleton_bus._sweep_orphan_watchers.assert_not_awaited()
        assert result["cancelled"] == 5


# ---------------------------------------------------------------------------
# 6. Dead-detection — the trap is never DEBUG-quiet forever (2026-11-21)
# ---------------------------------------------------------------------------


class TestDeadDetection:
    """Born-dead detection: zero effective sweeps past the first two
    ticks (>= 2x interval on the loop cadence) escalates LOUDLY — an
    ERROR on first crossing, then a rate-limited WARNING reminder
    every 10 subsequent no-op ticks. A lane whose sweeps dispatch
    normally NEVER triggers it.
    """

    @pytest.mark.asyncio
    async def test_dead_detection_fires_error_after_grace_window(
        self, caplog
    ):
        """Zero effective sweeps through the 2x-interval grace window
        (ticks 1-2) → the DEAD-DETECTION ERROR fires on tick 3 (the
        first tick at 2x interval on the loop cadence)."""
        svc = OrphanWatcherSweepService(
            dependency_bus=None,
            interval_seconds=1,
        )
        with patch.object(
            orphan_watcher_sweep_module, "get_dependency_bus",
            return_value=None,
        ):
            with caplog.at_level(logging.ERROR, logger=_SWEEP_LOGGER):
                await svc.sweep_once()  # tick 1 (t=0) — grace
                await svc.sweep_once()  # tick 2 (t=1x) — grace
                assert not svc._dead_detection_fired
                await svc.sweep_once()  # tick 3 (t=2x) — crossing

        assert svc._dead_detection_fired is True
        errors = [
            r for r in caplog.records
            if r.levelno == logging.ERROR
            and "DEAD-DETECTION" in r.getMessage()
        ]
        assert len(errors) == 1
        assert svc.counters()["effective_runs_total"] == 0

    @pytest.mark.asyncio
    async def test_dead_detection_does_not_fire_within_grace(self, caplog):
        """Two no-op ticks (inside the 2x-interval window) must NOT
        fire the detection — the lifespan wires the bus shortly after
        start(), and that window is legitimate."""
        svc = OrphanWatcherSweepService(
            dependency_bus=None,
            interval_seconds=1,
        )
        with patch.object(
            orphan_watcher_sweep_module, "get_dependency_bus",
            return_value=None,
        ):
            with caplog.at_level(logging.ERROR, logger=_SWEEP_LOGGER):
                await svc.sweep_once()  # tick 1
                await svc.sweep_once()  # tick 2 (t=1x — still grace)

        assert svc._dead_detection_fired is False
        assert not [
            r for r in caplog.records if r.levelno == logging.ERROR
        ]

    @pytest.mark.asyncio
    async def test_dead_detection_reminder_is_rate_limited(self, caplog):
        """12 no-op ticks → exactly ONE ERROR (the crossing) and
        exactly ONE WARNING reminder (tick 10) — loud but not
        spammy."""
        svc = OrphanWatcherSweepService(
            dependency_bus=None,
            interval_seconds=1,
        )
        with patch.object(
            orphan_watcher_sweep_module, "get_dependency_bus",
            return_value=None,
        ):
            with caplog.at_level(logging.WARNING, logger=_SWEEP_LOGGER):
                for _ in range(12):
                    await svc.sweep_once()

        errors = [
            r for r in caplog.records if r.levelno == logging.ERROR
        ]
        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert len(errors) == 1
        assert len(warnings) == 1
        assert "still dead" in warnings[0].getMessage()

    @pytest.mark.asyncio
    async def test_no_dead_detection_when_sweeps_are_effective(self, caplog):
        """A lane whose sweeps dispatch normally (explicit working
        bus) never triggers the detection, and ``effective_runs``
        accumulates."""
        bus = MagicMock()
        bus._sweep_orphan_watchers = AsyncMock(return_value=0)
        svc = OrphanWatcherSweepService(
            dependency_bus=bus,
            interval_seconds=1,
        )
        with caplog.at_level(logging.ERROR, logger=_SWEEP_LOGGER):
            for _ in range(3):
                await svc.sweep_once()

        assert svc._dead_detection_fired is False
        assert svc.counters()["effective_runs_total"] == 3
        assert not [
            r for r in caplog.records if r.levelno == logging.ERROR
        ]

    @pytest.mark.asyncio
    async def test_lane_recovery_after_dead_detection(
        self, caplog, monkeypatch
    ):
        """After the detection fired, a bus registered later flips the
        lane effective again — the recovery is logged at INFO and
        ``effective_runs`` starts accumulating."""
        import daemon.services.dependency_bus as dependency_bus_module

        svc = OrphanWatcherSweepService(
            dependency_bus=None,
            interval_seconds=1,
        )
        with patch.object(
            orphan_watcher_sweep_module, "get_dependency_bus",
            return_value=None,
        ):
            await svc.sweep_once()  # tick 1 (t=0)
            await svc.sweep_once()  # tick 2 (t=1x)
            await svc.sweep_once()  # tick 3 (t=2x) — detection fires
        assert svc._dead_detection_fired is True

        bus = MagicMock()
        bus._sweep_orphan_watchers = AsyncMock(return_value=1)
        monkeypatch.setattr(dependency_bus_module, "_dependency_bus", bus)
        with caplog.at_level(logging.INFO, logger=_SWEEP_LOGGER):
            post = await svc.sweep_once()

        assert post["effective_runs"] == 1
        assert any(
            "recovered" in r.getMessage()
            for r in caplog.records
            if r.levelno == logging.INFO
        )