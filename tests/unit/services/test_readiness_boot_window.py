"""BOOT-WARM tolerance pins for the checkpoint-saver readiness probe.

Incident 2026-11-22 (v0.31.33 boot, ensemble-main journal, verified): at
22:24:40Z — ~112s into the boot, with readyz already GREEN since
~22:23:53 — ONE readiness refresh tick logged

    "[Readiness] degraded: checkpoint_saver: sentinel checkpoint read
     failed (checkpoint path cannot serve reads — see readiness logs)"

with ``queue_max_age=1.266607s``; the NEXT tick was green again (3/3
ticks green at 5-9ms). A green→503→green flap INSIDE the boot window
is an auto-rollback risk at a flip ceremony: the Emperor's boot SLO
(2026-11-21) is boot_epoch→readyz-green ≤ 120s, and a 503 inside that
window reads as "never went green".

ROOT CAUSE (measured at base 5f0eafa83): the sentinel RIDES THE HOT
PATH BY DESIGN — its ``aget_tuple`` takes the saver's ONE
``asyncio.Lock``, which every production checkpoint op also takes. The
boot-restore storm held that lock past the probe's inner budget
(0.9s), so the probe returned a GRACE-ELIGIBLE class (``pool_busy`` /
``read_timeout``) — yet the composite degraded anyway, because the
grace tracker DELIBERATELY never changes ``ready``/``reasons``.

THE PINS BELOW COVER BOTH DIRECTIONS (the contract is two-sided):
  * a grace-eligible class inside the boot window must NOT degrade —
    the component reports healthy and the transient is PUBLISHED;
  * a hard failure (an exception), the OUTER probe timeout, ANY class
    after the window closes, and a MISSING boot epoch must STILL
    degrade — fail-closed, never a masked checkpoint-path failure.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from daemon.constants import READINESS_BOOT_WARM_WINDOW_S
from daemon.services import boot_epoch as boot_epoch_module
from daemon.services import readiness as readiness_module
from daemon.services.readiness import (
    CHECKPOINT_SAVER_PROBE_TIMEOUT_S,
    FAILURE_CLASS_HARD_COMPONENT_RED,
    FAILURE_CLASS_OK,
    FAILURE_CLASS_POOL_BUSY,
    FAILURE_CLASS_PROBE_TIMEOUT,
    FAILURE_CLASS_READ_TIMEOUT,
    GraceTracker,
    refresh_readiness_composite,
)

# The live boot epoch of the v0.31.33 boot (2026-11-22 22:22:48Z).
_BOOT_EPOCH = datetime(2026, 11, 22, 22, 22, 48, tzinfo=timezone.utc)
# The live degraded tick (22:24:40Z — 112s into the boot).
_FLAP_TICK = datetime(2026, 11, 22, 22, 24, 40, tzinfo=timezone.utc)


@pytest.fixture
def epoch_control():
    """Install the process boot epoch; restore the previous one after.

    Uses the REAL seam (``boot_epoch.set_boot_epoch``) so the pins
    exercise the production epoch mechanism, not a patched getter.
    """
    previous = boot_epoch_module.get_boot_epoch()

    def _install(epoch: datetime | None) -> None:
        boot_epoch_module.set_boot_epoch(epoch)

    yield _install
    boot_epoch_module.set_boot_epoch(previous)


async def _refresh(
    checkpoint_saver_probe,
    now: datetime,
    *,
    grace_tracker: GraceTracker | None = None,
):
    """One refresh cycle with ONLY the checkpoint-saver component moving."""
    return await refresh_readiness_composite(
        db_probe=lambda: True,
        queue_probe=lambda: 0.0,
        services_ok=True,
        queue_freshness_threshold_seconds=10.0,
        checkpoint_saver_probe=checkpoint_saver_probe,
        grace_tracker=grace_tracker,
        now=lambda: now,
    )


# ── The window itself ───────────────────────────────────────────────────────


class TestWindowDefinition:
    """The window is a NAMED constant aligned with the Emperor boot SLO."""

    def test_window_is_120_seconds(self):
        """The Emperor boot SLO (2026-11-21): boot_epoch→readyz-green
        ≤ 120s. The tolerance must never outlive the measured window."""
        assert READINESS_BOOT_WARM_WINDOW_S == 120.0

    def test_probe_budget_was_not_bumped(self):
        """Contract: NO budget bump (the fix is verdict-truth, not
        desensitisation). The inner budget is 0.9s (1.0 - 0.1)."""
        assert CHECKPOINT_SAVER_PROBE_TIMEOUT_S == 1.0

    def test_probe_budget_is_not_widened_by_the_window(self):
        """The window must not leak into the probe budget."""
        assert READINESS_BOOT_WARM_WINDOW_S > CHECKPOINT_SAVER_PROBE_TIMEOUT_S


# ── Direction 1: the TRANSIENT must NOT degrade (in-window) ────────────────


class TestInWindowTransientDoesNotDegrade:
    """Inside the boot window a grace-eligible class is tolerated."""

    @pytest.mark.asyncio
    async def test_in_window_pool_busy_stays_green(self, epoch_control):
        """The live flap: pool_busy at ~112s into the boot → GREEN."""
        epoch_control(_BOOT_EPOCH)
        composite = await _refresh(
            lambda: (False, FAILURE_CLASS_POOL_BUSY), _FLAP_TICK
        )
        assert composite.checkpoint_saver is True
        assert composite.ready is True
        assert composite.component_classes["checkpoint_saver"] == FAILURE_CLASS_OK

    @pytest.mark.asyncio
    async def test_in_window_read_timeout_stays_green(self, epoch_control):
        """``read_timeout`` is the OTHER grace-eligible class the probe
        reports (lock free, read did not answer)."""
        epoch_control(_BOOT_EPOCH)
        composite = await _refresh(
            lambda: (False, FAILURE_CLASS_READ_TIMEOUT), _FLAP_TICK
        )
        assert composite.checkpoint_saver is True
        assert composite.ready is True

    @pytest.mark.asyncio
    async def test_in_window_transient_is_published_not_silent(self, epoch_control):
        """The tolerance must NEVER be silent: the class, the age and
        the window ride ``detail.checkpoint_saver_boot_warm``."""
        epoch_control(_BOOT_EPOCH)
        composite = await _refresh(
            lambda: (False, FAILURE_CLASS_POOL_BUSY), _FLAP_TICK
        )
        detail = composite.to_payload()["detail"]
        marker = detail["checkpoint_saver_boot_warm"]
        assert marker["tolerated"] is True
        assert marker["failure_class"] == FAILURE_CLASS_POOL_BUSY
        assert marker["boot_elapsed_seconds"] == pytest.approx(112.0, abs=1.0)
        assert marker["window_seconds"] == READINESS_BOOT_WARM_WINDOW_S
        # The composite is healthy — the marker is advisory, not a
        # degradation reason.
        assert composite.reasons == []

    @pytest.mark.asyncio
    async def test_in_window_transient_resets_the_failure_streak(
        self, epoch_control
    ):
        """GraceTracker semantics are UNCHANGED: the tolerated tick
        reports the component HEALTHY, so the streak resets naturally
        (no grace is consumed, no counter accumulates)."""
        epoch_control(_BOOT_EPOCH)
        tracker = GraceTracker()
        composite = await _refresh(
            lambda: (False, FAILURE_CLASS_POOL_BUSY),
            _FLAP_TICK,
            grace_tracker=tracker,
        )
        assert composite.checkpoint_saver is True
        assert composite.failure_streak.get("checkpoint_saver", 0) == 0
        assert composite.grace_active is False


# ── Direction 2: the GENUINE failure must still degrade (fail-closed) ───────


class TestGenuineFailureStillDegrades:
    """The tolerance must never mask a real checkpoint-path failure."""

    @pytest.mark.asyncio
    async def test_in_window_exception_still_degrades(self, epoch_control):
        """An EXCEPTION (hard_component_red) degrades EVEN inside the
        window — a broken checkpoint path is never masked."""
        epoch_control(_BOOT_EPOCH)

        def _raising():
            raise ConnectionError("server closed the connection unexpectedly")

        composite = await _refresh(_raising, _FLAP_TICK)
        assert composite.checkpoint_saver is False
        assert composite.ready is False
        assert (
            composite.component_classes["checkpoint_saver"]
            == FAILURE_CLASS_HARD_COMPONENT_RED
        )

    @pytest.mark.asyncio
    async def test_in_window_outer_probe_timeout_still_degrades(
        self, epoch_control
    ):
        """The OUTER budget expiring (the probe thread never answered)
        is the orchestrator's own ``probe_timeout`` class — it degrades
        EVEN inside the window."""
        epoch_control(_BOOT_EPOCH)

        def _hanging():
            time.sleep(CHECKPOINT_SAVER_PROBE_TIMEOUT_S + 0.5)
            return True

        composite = await _refresh(_hanging, _FLAP_TICK)
        assert composite.checkpoint_saver is False
        assert composite.ready is False
        assert (
            composite.component_classes["checkpoint_saver"]
            == FAILURE_CLASS_PROBE_TIMEOUT
        )

    @pytest.mark.asyncio
    async def test_post_window_pool_busy_degrades(self, epoch_control):
        """After the window closes EVERY class degrades — the tolerance
        is a boot-window-only allowance, never a permanent one."""
        epoch_control(_BOOT_EPOCH)
        after_window = _BOOT_EPOCH + timedelta(
            seconds=READINESS_BOOT_WARM_WINDOW_S + 5
        )
        composite = await _refresh(
            lambda: (False, FAILURE_CLASS_POOL_BUSY), after_window
        )
        assert composite.checkpoint_saver is False
        assert composite.ready is False
        detail = composite.to_payload()["detail"]
        assert "checkpoint_saver_boot_warm" not in detail

    @pytest.mark.asyncio
    async def test_missing_boot_epoch_degrades(self, epoch_control):
        """No epoch captured (the capture failed, or this is not the
        daemon process) ⇒ NO window ⇒ fail-closed."""
        epoch_control(None)
        composite = await _refresh(
            lambda: (False, FAILURE_CLASS_POOL_BUSY), _FLAP_TICK
        )
        assert composite.checkpoint_saver is False
        assert composite.ready is False

    @pytest.mark.asyncio
    async def test_legacy_bare_false_probe_still_degrades(self, epoch_control):
        """A bare ``False`` carries NO class (the legacy probe form) —
        it is the ``unknown`` class, which is NEVER grace-eligible."""
        epoch_control(_BOOT_EPOCH)
        composite = await _refresh(lambda: False, _FLAP_TICK)
        assert composite.checkpoint_saver is False
        assert composite.ready is False

    @pytest.mark.asyncio
    async def test_epoch_in_the_future_is_tolerated_while_the_process_is_young(
        self, epoch_control, monkeypatch
    ):
        """CLOCK DISAGREEMENT (the DB clock is ahead of the daemon
        clock): the epoch lands in the daemon's future. The daemon has
        just booted (the epoch IS the boot instant), so the window is
        open — but the tolerance is BOUNDED by the process uptime, so a
        large clock skew can never mask contention for hours."""
        monkeypatch.setattr(
            readiness_module, "_readiness_process_uptime_seconds", lambda: 5.0
        )
        # The epoch lands 300s AFTER the tick: the daemon's clock says
        # the boot has not happened yet (the DB clock is ahead).
        future_epoch = _FLAP_TICK + timedelta(seconds=300)
        epoch_control(future_epoch)
        composite = await _refresh(
            lambda: (False, FAILURE_CLASS_POOL_BUSY), _FLAP_TICK
        )
        assert composite.checkpoint_saver is True
        assert composite.ready is True

    @pytest.mark.asyncio
    async def test_epoch_in_the_future_degrades_once_the_process_is_old(
        self, epoch_control, monkeypatch
    ):
        """The same clock disagreement, but the process has been up
        LONGER than the window: the boot-warm allowance is over, so the
        class degrades (the tolerance may never outlive the process)."""
        monkeypatch.setattr(
            readiness_module,
            "_readiness_process_uptime_seconds",
            lambda: READINESS_BOOT_WARM_WINDOW_S + 10.0,
        )
        # The epoch lands 300s AFTER the tick: the daemon's clock says
        # the boot has not happened yet (the DB clock is ahead).
        future_epoch = _FLAP_TICK + timedelta(seconds=300)
        epoch_control(future_epoch)
        composite = await _refresh(
            lambda: (False, FAILURE_CLASS_POOL_BUSY), _FLAP_TICK
        )
        assert composite.checkpoint_saver is False
        assert composite.ready is False


# ── Observability: the failure CLASS reaches the reason strings ─────────────


class TestFailureClassIsObservable:
    """The live log could not distinguish pool_busy from hard — the
    degraded reasons now carry the §4.0 class token."""

    @pytest.mark.asyncio
    async def test_degraded_reason_carries_the_class(self, epoch_control):
        epoch_control(_BOOT_EPOCH)
        after_window = _BOOT_EPOCH + timedelta(
            seconds=READINESS_BOOT_WARM_WINDOW_S + 5
        )
        composite = await _refresh(
            lambda: (False, FAILURE_CLASS_POOL_BUSY), after_window
        )
        assert any(
            "class=pool_busy" in reason for reason in composite.reasons
        ), composite.reasons

    @pytest.mark.asyncio
    async def test_hard_failure_reason_carries_the_class(self, epoch_control):
        epoch_control(_BOOT_EPOCH)

        def _raising():
            raise ConnectionError("boom")

        composite = await _refresh(_raising, _FLAP_TICK)
        assert any(
            "class=hard_component_red" in reason for reason in composite.reasons
        ), composite.reasons
