"""B3 DB-error fail-closed gap tests (feature/fix-wc-wake-resilience).

These tests close the audit gap where the W-B liveness gate's
``child_has_recent_heartbeat`` probe is not wrapped in a try/except
around the repo call — the audit flagged that a DB error on the
heartbeat probe is NOT explicitly fail-closed at the B3 release /
escalation decision.

Mission spec: "DB-error on heartbeat probe → release WITHHELD
(fail-closed direction)".

The audit's concern: ``WaitingChildrenWatchdog._has_recent_heartbeat``
(documented at lines 740-857 of
``daemon/services/waiting_children_watchdog.py``) calls the repo's
``child_has_recent_heartbeat(child_id, threshold_seconds)`` and
returns ``bool(method(...))``. There is NO ``try/except`` around the
``method(...)`` call. If the repo raises (e.g.,
``sqlalchemy.exc.OperationalError`` on a transient DB blip, or a
``TimeoutError`` from a wedged connection pool), the exception
propagates OUT of ``_has_recent_heartbeat`` and OUT of the per-pair
loop. The watchdog's per-tick ``try/except Exception`` around the
per-parent scan catches it — but the broader question is whether
this is the intended fail-closed direction.

There are two failure modes to consider:

1. **Propagate-up fail-closed** (current observed behavior per the
   audit): the exception escapes ``_has_recent_heartbeat``,
   bubbles to the per-parent ``try/except`` at line 1267 (catch and
   log; ``stats['errors'] += 1``; advance to next parent). The
   release / escalation decision is NEVER taken on this tick — the
   per-parent scan aborts before reaching the B3 release /
   escalation branches. **This is fail-closed by accident, not by
   design.**

2. **Wrapped fail-closed** (the mission spec): the helper wraps the
   repo call in a try/except, catches the DB error, logs at
   WARNING, and returns False ("no recent heartbeat"). The B3
   release / escalation then fires on the silent-child assumption —
   exactly the pre-W-B behavior. **This is fail-closed by design.**

The audit's question: which direction is correct? If the test
asserts that no release / escalation fires on a DB-error tick
(mission spec, fail-closed), and the real code propagates the
exception out of the tick, the test is documenting the defect.

The test design follows the audit's "If the real code instead
propagates the exception out of the tick (audit suspects the
``_has_recent_heartbeat`` wrapper at waiting_children_watchdog.py:740-859
does NOT catch repo exceptions), let the test fail with evidence —
that IS the finding."

Harness: MagicMock manager + MagicMock instance repo + a
``task_repository`` whose ``child_has_recent_heartbeat`` raises
``sqlalchemy.exc.OperationalError`` on the heartbeat probe.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError


# ---------------------------------------------------------------------------
# Helpers — mirror test_b3_watchdog_escalation.py + test_w_b_watchdog_liveness_gate.py
# ---------------------------------------------------------------------------


def _build_manager_mock():
    """Build a mock manager exposing the ``enqueue_message`` surface."""
    mgr = MagicMock()
    mgr.enqueue_message = AsyncMock()
    return mgr


def _build_repo_mock(
    *,
    parent_ids: list[str] | None = None,
    hung_by_parent: dict[str, list[tuple[str, float]]] | None = None,
    terminal_child_ids: list[str] | None = None,
    parents_with_non_term_children: set[str] | None = None,
) -> MagicMock:
    """Build a mock instance repo (same shape as
    ``test_b3_watchdog_escalation._build_repo_mock``).
    """
    repo = MagicMock()
    repo.list_waiting_children_parents = MagicMock(
        return_value=parent_ids or []
    )
    repo.list_hung_children_for_parent = MagicMock(
        side_effect=lambda parent_id, threshold_seconds: (
            hung_by_parent.get(parent_id, []) if hung_by_parent else []
        )
    )
    repo.list_terminal_instance_ids = MagicMock(
        return_value=set(terminal_child_ids or [])
    )
    repo.get = MagicMock(
        side_effect=lambda instance_id: MagicMock(
            status="waiting_children"
        )
    )
    if parents_with_non_term_children is None:
        parents_with_non_term_children = set(parent_ids or [])
    repo.parents_with_non_terminal_children = MagicMock(
        return_value=parents_with_non_term_children
    )
    return repo


def _build_task_repo_raising(
    *, exception_factory=None,
) -> MagicMock:
    """Build a task_repository mock whose
    ``child_has_recent_heartbeat`` raises on every call.

    ``exception_factory`` — optional callable that returns the
    exception instance. Defaults to a function that produces a
    fresh ``OperationalError`` instance per call (so the same
    ``OperationalError`` is not shared across the call records —
    MagicMock records the call_args which can carry references).
    """
    if exception_factory is None:
        def exception_factory():
            return OperationalError("DB blip", params=None, orig=Exception("connection reset"))

    repo = MagicMock()
    repo.child_has_recent_heartbeat = MagicMock(
        side_effect=exception_factory
    )
    return repo


def _build_watchdog(
    *,
    manager,
    repo,
    task_repo=None,
    escalation_nudge_count: int = 3,
    release_after_nudge_count: int = 5,
    interval_seconds: int = 60,
    hang_threshold_seconds: int = 3600,
):
    """Build a WaitingChildrenWatchdog with the mock collaborators."""
    from daemon.services.waiting_children_watchdog import (
        WaitingChildrenWatchdog,
    )

    return WaitingChildrenWatchdog(
        instance_repository=repo,
        manager=manager,
        enabled=True,
        interval_seconds=interval_seconds,
        hang_threshold_seconds=hang_threshold_seconds,
        task_repository=task_repo,
        escalation_nudge_count=escalation_nudge_count,
        release_after_nudge_count=release_after_nudge_count,
    )


# ---------------------------------------------------------------------------
# Gap 1: B3 release fail-closed on heartbeat-probe DB error.
# ---------------------------------------------------------------------------


class TestB3ReleaseFailClosedOnProbeError:
    """Mission spec: a DB error on the heartbeat probe MUST withhold
    the B3 release (fail-closed direction). The pair has been
    "released" no nudges past the threshold — the operator must
    not see a release-notice row land in MessageQueue while the
    DB is unhealthy.

    The audit observed: ``_has_recent_heartbeat`` (lines 740-857)
    does NOT wrap the repo call in try/except. The exception
    propagates up to the per-parent ``try/except`` at line 1267,
    which catches ``Exception`` (DB-level included), logs at ERROR,
    bumps ``stats['errors']``, and continues to the next parent —
    NO release / escalation / base notice enqueued.

    This test asserts the mission-spec behavior:
    ``release_notices_enqueued == 0`` AND ``escalation_notices_enqueued == 0``
    AND ``enqueue_message.await_count == 0`` regardless of how the
    underlying code routed the error (exception-swallowed or
    propagated-and-skipped-the-loop).

    If the test fails with ``OperationalError`` propagating out
    of ``run_once``, that IS the audit's finding — the watchdog
    does not contain the heartbeat probe error at the helper
    level, and the per-parent scan aborts mid-loop. The test
    catches that with a defensive wrapper so the assertion can
    record the finding without crashing the test runner.
    """

    @pytest.mark.asyncio
    async def test_release_withheld_when_heartbeat_probe_raises(
        self,
    ):
        """Heartbeat probe raises ``OperationalError`` → B3 release
        is NOT enqueued. Mission spec: fail-closed.
        """
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        task_repo = _build_task_repo_raising()
        w = _build_watchdog(
            manager=manager,
            repo=repo,
            task_repo=task_repo,
            escalation_nudge_count=1,
            release_after_nudge_count=2,
        )

        # Drive the watchdog past the release threshold (2 ticks).
        # The heartbeat probe MUST raise on every tick (the
        # task_repository mock is configured to raise on every
        # call).
        exception_caught = None
        for tick_idx in range(2):
            try:
                await w.run_once()
            except OperationalError as exc:
                exception_caught = exc
                break
            except Exception as exc:  # pragma: no cover — defensive
                # Anything else from the watchdog — record for
                # diagnostics, do not crash the test.
                exception_caught = exc
                break

        # Mission-spec assertion: NO release enqueued. The pair
        # has been past the threshold for one full tick; under
        # the mission spec the release MUST be withheld.
        assert w.release_notices_enqueued == 0, (
            "B3 DB-error fail-closed: a release notice MUST NOT be "
            "enqueued while the heartbeat probe is raising "
            f"OperationalError. Got release_notices_enqueued="
            f"{w.release_notices_enqueued!r}"
        )
        # Escalation also must NOT have fired on tick 1 (the only
        # tick where escalation could have fired under
        # escalation=1, release=2).
        assert w.escalation_notices_enqueued == 0, (
            "B3 DB-error fail-closed: an escalation notice MUST "
            "NOT be enqueued while the heartbeat probe is raising "
            f"OperationalError. Got escalation_notices_enqueued="
            f"{w.escalation_notices_enqueued!r}"
        )
        # The base hang notice also must NOT fire — the
        # per-pair nudge-count bump on tick 1 is gated on the
        # heartbeat probe return value; if the probe raises,
        # the per-pair loop aborts before the base notice.
        assert manager.enqueue_message.await_count == 0, (
            "B3 DB-error fail-closed: NO enqueue_message calls "
            "when the heartbeat probe is raising. Got "
            f"await_count={manager.enqueue_message.await_count}"
        )
        # The heartbeat probe was consulted at least once (proves
        # the failure mode was reached — not bypassed).
        assert task_repo.child_has_recent_heartbeat.call_count >= 1, (
            "Heartbeat probe MUST be consulted on a tick with a "
            "hung child; if it was bypassed, the test setup is "
            "wrong. Got call_count="
            f"{task_repo.child_has_recent_heartbeat.call_count}"
        )
        # If the watchdog propagated the exception out, the
        # exception was caught above; we record it for
        # diagnostics. The audit explicitly asked: "let the test
        # fail with evidence — that IS the finding."
        if exception_caught is not None:
            # Make the finding actionable: the helper did NOT
            # catch the DB error, the exception escaped the
            # per-pair loop, and the per-parent scan aborted.
            pytest.fail(
                "FINDING: WaitingChildrenWatchdog._has_recent_heartbeat "
                "did NOT contain the OperationalError — the "
                "exception escaped the helper and aborted the "
                f"per-parent scan. exc={exception_caught!r}. "
                "Mission spec requires fail-closed: the helper "
                "MUST catch OperationalError, log at WARNING, and "
                "return False (no recent heartbeat) so the "
                "release / escalation decision can be made "
                "deterministically. (The current code catches "
                "the error at the per-parent level via a "
                "broad ``except Exception``, which logs at ERROR "
                "and skips the B3 path for that parent — fail-"
                "closed by accident, not by design.)"
            )


# ---------------------------------------------------------------------------
# Gap 2: B3 escalation fail-closed on heartbeat-probe DB error.
# ---------------------------------------------------------------------------


class TestB3EscalationFailClosedOnProbeError:
    """Sibling of the release fail-closed test: the B3 escalation
    MUST also be withheld when the heartbeat probe raises.

    The release and escalation branches are siblings in the B3
    ladder (``waiting_children_watchdog.py:1113-1207``); both gate
    on ``self._has_recent_heartbeat`` returning False. If the
    probe raises, both must be withheld.

    Same harness as the release test, but with a tighter
    escalation threshold so the escalation decision is reachable
    on a single tick.
    """

    @pytest.mark.asyncio
    async def test_escalation_withheld_when_heartbeat_probe_raises(
        self,
    ):
        """Heartbeat probe raises → B3 escalation is NOT enqueued.
        Mission spec: fail-closed.
        """
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        task_repo = _build_task_repo_raising()
        # Escalation threshold = 1 so the tick where the probe
        # raises could have fired the escalation had the helper
        # returned False. The test asserts it does NOT fire.
        w = _build_watchdog(
            manager=manager,
            repo=repo,
            task_repo=task_repo,
            escalation_nudge_count=1,
            release_after_nudge_count=5,
        )

        exception_caught = None
        for tick_idx in range(1):
            try:
                await w.run_once()
            except OperationalError as exc:
                exception_caught = exc
                break
            except Exception as exc:  # pragma: no cover — defensive
                exception_caught = exc
                break

        assert w.escalation_notices_enqueued == 0, (
            "B3 DB-error fail-closed: an escalation notice MUST "
            "NOT be enqueued while the heartbeat probe is raising "
            f"OperationalError. Got escalation_notices_enqueued="
            f"{w.escalation_notices_enqueued!r}"
        )
        assert manager.enqueue_message.await_count == 0, (
            "B3 DB-error fail-closed: NO enqueue_message calls "
            "when the heartbeat probe is raising. Got "
            f"await_count={manager.enqueue_message.await_count}"
        )
        assert task_repo.child_has_recent_heartbeat.call_count >= 1, (
            "Heartbeat probe MUST be consulted on a tick with a "
            "hung child; if it was bypassed, the test setup is "
            "wrong. Got call_count="
            f"{task_repo.child_has_recent_heartbeat.call_count}"
        )
        if exception_caught is not None:
            pytest.fail(
                "FINDING: WaitingChildrenWatchdog._has_recent_heartbeat "
                "did NOT contain the OperationalError — the "
                "exception escaped the helper and aborted the "
                f"per-parent scan. exc={exception_caught!r}. "
                "Mission spec requires fail-closed."
            )


# ---------------------------------------------------------------------------
# Gap 3: post-error recovery — once the probe is healthy again,
# the B3 ladder must resume normally (no permanent stall).
# ---------------------------------------------------------------------------


class TestB3RecoversAfterProbeError:
    """A failing heartbeat probe must NOT permanently suppress the
    B3 ladder. Once the probe is healthy again, the watchdog
    resumes its normal escalation / release cadence.

    This is the "happy recovery" sibling of the fail-closed test:
    the fail-closed direction is correct ONLY if it does not
    introduce a permanent stall. The test ticks once with a
    raising probe (release withheld), then switches the probe to
    a healthy ``return_value=False`` and ticks again. The release
    fires on the second healthy tick (it picks up where the
    suppressed tick left off, modulo the nudge-count semantics —
    the failed tick still bumped the nudge count, so the next
    healthy tick reaches the threshold one nudge sooner).
    """

    @pytest.mark.asyncio
    async def test_release_resumes_after_probe_recovers(self):
        """Tick 1: probe raises → release withheld.
        Tick 2: probe healthy (returns False) → release fires.

        The nudge count incremented on tick 1 (the per-pair loop
        bumps it BEFORE the heartbeat probe is called, so even a
        failing probe leaves the count at 1). On tick 2 with
        release_after_nudge_count=2, the count climbs to 2 and
        the release fires.
        """
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        task_repo = _build_task_repo_raising()
        w = _build_watchdog(
            manager=manager,
            repo=repo,
            task_repo=task_repo,
            escalation_nudge_count=1,
            release_after_nudge_count=2,
        )

        # ── Tick 1: probe raises → release withheld.
        exception_caught = None
        try:
            await w.run_once()
        except OperationalError as exc:
            exception_caught = exc
        except Exception as exc:  # pragma: no cover — defensive
            exception_caught = exc
        if exception_caught is not None:
            pytest.fail(
                "FINDING: probe raised and escaped — aborting "
                "recovery test. exc=" + repr(exception_caught)
            )
        assert w.release_notices_enqueued == 0, (
            "Tick 1 (probe failing): release MUST be withheld. "
            f"Got release_notices_enqueued={w.release_notices_enqueued!r}"
        )

        # ── Tick 2: probe recovers (returns False). The watchdog
        # resumes its B3 cadence.
        task_repo.child_has_recent_heartbeat = MagicMock(
            return_value=False
        )
        await w.run_once()
        # The release fires on tick 2: nudge count was 1 from
        # tick 1 (the per-pair loop bumped it before the probe
        # raised), tick 2 bumps it to 2, release threshold met.
        assert w.release_notices_enqueued == 1, (
            "Tick 2 (probe healthy): release MUST fire (nudge=2 "
            f"reached threshold). Got release_notices_enqueued="
            f"{w.release_notices_enqueued!r}"
        )
        # Two enqueues total across the two ticks: tick 1's base
        # notice (if it fired) and tick 2's release. With
        # escalation=1, tick 2 also fires the escalation (nudge=2
        # >= escalation=1); but the escalation is gated on the
        # release path that fires FIRST in the B3 loop, and on
        # the same iteration the release fires too. Both fire on
        # tick 2.
        assert manager.enqueue_message.await_count >= 1, (
            "Tick 2 enqueue count MUST be >= 1. Got "
            f"await_count={manager.enqueue_message.await_count}"
        )
