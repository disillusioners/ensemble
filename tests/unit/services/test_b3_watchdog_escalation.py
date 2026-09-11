"""B3 RESOLVED (2026-09-11) — Watchdog escalation policy.

The pre-B3 watchdog had an infinite silent park bug: a non-terminal
child got the same hang notice every interval, with no escalation,
no release, and no way out. The parent could stay parked forever
even when the watchdog repeatedly confirmed the wedge signature
(84563a03-class incidents).

B3 design (within the spec's design-freedom):

  * ``escalation_nudge_count`` (default 3): number of nudges before
    an escalation notice fires. The escalation is a more urgent
    cousin of the hang notice — recommends spawn-replacement,
    explicit termination, or operator escalation.
  * ``release_after_nudge_count`` (default 5): number of nudges
    before the watchdog SYNTHESIZES a release — enqueues a wake
    turn via ``enqueue_message`` that tells the parent to proceed
    without the hung child. This breaks the infinite-silent-park
    cycle by handing the decision back to the parent's LLM.

Anti-spam invariants:

  * Nudges are counted per ``(parent, child)`` pair. A pair that
    persists in BOTH the SQL result AND ``_notified`` is in a
    continuing episode and its nudge count climbs each tick.
  * Episode-end (parent left WC, child terminal, or the scan-driven
    sweep cleared the pair) resets the nudge count for that pair —
    a future episode with the same pair starts fresh at nudge=1.
  * Escalation / release cooldowns are per-pair, gated by
    ``_escalation_notified`` / ``_release_notified`` sets — each
    fires ONCE per episode.

Census stays at 23/1/0 — B3 uses the existing
``InstanceManager.enqueue_message`` primitive. No new
admission_state writer / JobItem creator / work_id mint site.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helpers — minimal manager fixture
# ---------------------------------------------------------------------------


def _build_manager_mock():
    """Build a mock manager exposing the ``enqueue_message`` surface."""
    mgr = MagicMock()
    mgr.enqueue_message = AsyncMock()
    # Track calls (the return value is unused for the watchdog,
    # which only checks that ``enqueue_message`` was awaited).
    return mgr


def _build_repo_mock(
    *,
    parent_ids: list[str] | None = None,
    hung_by_parent: dict[str, list[tuple[str, float]]] | None = None,
    terminal_child_ids: list[str] | None = None,
    children_status: dict[str, str] | None = None,
    parents_with_non_term_children: set[str] | None = None,
) -> MagicMock:
    """Build a mock instance repo for the watchdog.

    By default, ``parents_with_non_terminal_children`` returns the
    set of all parent_ids (the children we reported as hung) — so
    the wedge pass SKIPS those parents (they have non-terminal
    children). Tests that want the wedge pass to fire can override.
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
    # Default: every parent in ``parent_ids`` has at least one
    # non-terminal child (the hung child we reported). Override to
    # ``set()`` if you want the wedge pass to fire.
    if parents_with_non_term_children is None:
        parents_with_non_term_children = set(parent_ids or [])
    repo.parents_with_non_terminal_children = MagicMock(
        return_value=parents_with_non_term_children
    )
    return repo


def _build_watchdog(
    *,
    manager,
    repo,
    escalation_nudge_count: int = 3,
    release_after_nudge_count: int = 5,
    interval_seconds: int = 60,
    hang_threshold_seconds: int = 3600,
) -> MagicMock:
    """Build a WaitingChildrenWatchdog with the mock collaborators."""
    from daemon.services.waiting_children_watchdog import WaitingChildrenWatchdog

    return WaitingChildrenWatchdog(
        instance_repository=repo,
        manager=manager,
        enabled=True,
        interval_seconds=interval_seconds,
        hang_threshold_seconds=hang_threshold_seconds,
        escalation_nudge_count=escalation_nudge_count,
        release_after_nudge_count=release_after_nudge_count,
    )


# ---------------------------------------------------------------------------
# B3 invariants
# ---------------------------------------------------------------------------


class TestB3EscalationPolicy:
    """B3: escalation_nudge_count and release_after_nudge_count
    drive the escalation and release paths.
    """

    @pytest.mark.asyncio
    async def test_first_nudge_fires_base_hang_notice(self):
        """First nudge → base hang notice (no escalation, no release)."""
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        w = _build_watchdog(manager=manager, repo=repo)

        await w.run_once()

        # Exactly one enqueue: the base hang notice.
        assert manager.enqueue_message.await_count == 1
        kwargs = manager.enqueue_message.await_args.kwargs
        assert kwargs["metadata"]["watchdog_notice"] is True
        # No escalation or release yet.
        assert w.escalation_notices_enqueued == 0
        assert w.release_notices_enqueued == 0
        # Nudge count = 1.
        assert w.nudge_count_for("parent-A", "child-X") == 1

    @pytest.mark.asyncio
    async def test_escalation_fires_at_threshold(self):
        """After escalation_nudge_count nudges → escalation notice fires."""
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        w = _build_watchdog(
            manager=manager, repo=repo,
            escalation_nudge_count=2,
            release_after_nudge_count=5,
        )

        # Run 2 ticks → nudge count climbs to 2 → escalation fires.
        await w.run_once()  # tick 1: base notice (new pair), nudge=1
        await w.run_once()  # tick 2: no new pairs, nudge=2 → escalation fires

        # 1 base notice + 1 escalation = 2 enqueues. The base
        # notice is anti-spam gated (one per episode); the
        # escalation fires on the SECOND tick when the nudge
        # threshold is reached.
        assert manager.enqueue_message.await_count == 2
        # The second call is the escalation — carries the
        # escalation metadata.
        escalation_kwargs = manager.enqueue_message.await_args_list[1].kwargs
        assert escalation_kwargs["metadata"]["watchdog_escalation"] is True
        assert escalation_kwargs["metadata"]["nudge_count"] == 2
        assert escalation_kwargs["metadata"]["escalated_pair"]["parent_id"] == "parent-A"
        assert w.escalation_notices_enqueued == 1
        assert w.release_notices_enqueued == 0
        # The escalation is one-per-episode — third tick MUST NOT
        # re-fire (the cooldown set is ``_escalation_notified``).
        await w.run_once()  # tick 3: nudge=3, no escalation (cooldown)
        assert w.escalation_notices_enqueued == 1  # unchanged

    @pytest.mark.asyncio
    async def test_release_fires_at_threshold(self):
        """After release_after_nudge_count nudges → release notice fires."""
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        w = _build_watchdog(
            manager=manager, repo=repo,
            escalation_nudge_count=2,
            release_after_nudge_count=4,
        )

        # Run 4 ticks → nudge count climbs to 4 → release fires.
        # Tick 1: base notice (new pair), nudge=1.
        # Tick 2: nudge=2 → escalation fires.
        # Tick 3: nudge=3 (escalation cooldown — no fire).
        # Tick 4: nudge=4 → release fires.
        await w.run_once()
        await w.run_once()
        await w.run_once()
        await w.run_once()

        # Last call is the release — carries the release metadata.
        release_kwargs = manager.enqueue_message.await_args_list[-1].kwargs
        assert release_kwargs["metadata"]["watchdog_release"] is True
        assert release_kwargs["metadata"]["nudge_count"] == 4
        assert release_kwargs["metadata"]["released_pair"]["parent_id"] == "parent-A"
        assert w.release_notices_enqueued == 1
        # The release is one-per-episode — fifth tick MUST NOT
        # re-fire (the cooldown set is ``_release_notified``).
        await w.run_once()  # nudge=5 (no release — cooldown)
        assert w.release_notices_enqueued == 1  # unchanged

    @pytest.mark.asyncio
    async def test_release_only_after_escalation_threshold(self):
        """release_after_nudge_count MUST exceed escalation_nudge_count
        (the validation in ``__init__`` enforces this)."""
        from daemon.services.waiting_children_watchdog import (
            WaitingChildrenWatchdog,
        )

        repo = _build_repo_mock()
        with pytest.raises(ValueError) as exc_info:
            WaitingChildrenWatchdog(
                instance_repository=repo,
                manager=_build_manager_mock(),
                escalation_nudge_count=5,
                release_after_nudge_count=3,  # <= escalation
            )
        assert "release_after_nudge_count" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_episode_end_resets_nudge_count(self):
        """When the parent leaves WC (episode ends), the nudge
        count for that pair is purged. A future episode with the
        same pair starts fresh at nudge=1."""
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        w = _build_watchdog(
            manager=manager, repo=repo,
            escalation_nudge_count=2,
            release_after_nudge_count=4,
        )

        # 2 ticks → nudge=2 (escalation fires).
        await w.run_once()
        await w.run_once()
        assert w.nudge_count_for("parent-A", "child-X") == 2

        # Parent leaves WC: tick with no parent_ids in the
        # enumeration → parent-left-WC purge clears the pair.
        repo.list_waiting_children_parents = MagicMock(return_value=[])
        await w.run_once()
        # Nudge count purged at episode end.
        assert w.nudge_count_for("parent-A", "child-X") == 0

        # Parent re-parks (same hung child): fresh episode at nudge=1.
        repo.list_waiting_children_parents = MagicMock(
            return_value=["parent-A"]
        )
        await w.run_once()
        assert w.nudge_count_for("parent-A", "child-X") == 1
        # No escalation / release in the new episode (counts are
        # reset, so the new episode starts the climb again).
        # (Total escalation_notices_enqueued is still 1 — from the
        # first episode. The new episode hasn't crossed its
        # threshold yet.)
        assert w.escalation_notices_enqueued == 1

    @pytest.mark.asyncio
    async def test_episode_end_purges_escalation_cooldown(self):
        """When the pair's episode ends (child terminal), the
        escalation cooldown is purged too — a fresh episode can
        re-escalate."""
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        w = _build_watchdog(
            manager=manager, repo=repo,
            escalation_nudge_count=2,
            release_after_nudge_count=5,
        )

        # 2 ticks → escalation fires.
        await w.run_once()
        await w.run_once()
        assert w.escalation_notices_enqueued == 1
        assert ("parent-A", "child-X") in w.escalation_episodes

        # Child becomes terminal → purge clears the cooldown.
        repo.list_hung_children_for_parent = MagicMock(return_value=[])
        repo.list_terminal_instance_ids = MagicMock(
            return_value={"child-X"}
        )
        await w.run_once()
        # Cooldown purged at episode end.
        assert ("parent-A", "child-X") not in w.escalation_episodes


class TestB3EnqueueFailureSafety:
    """B3: enqueue failures during escalation / release MUST be
    soft-failures — the sweep continues, the next tick retries.
    """

    @pytest.mark.asyncio
    async def test_escalation_enqueue_failure_does_not_abort_sweep(self):
        """Escalation enqueue raises → logged warning, sweep continues."""
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        w = _build_watchdog(
            manager=manager, repo=repo,
            escalation_nudge_count=2,
            release_after_nudge_count=5,
        )

        # First call (base hang notice) succeeds; second call
        # (escalation) fails; third call (base on tick 3 — wait,
        # the base notice is anti-spam gated so it doesn't fire on
        # tick 3; the B3 escalation retries on tick 3). Configure
        # the side_effect list so we can pin the exact call count.
        manager.enqueue_message = AsyncMock(
            side_effect=[
                None,  # tick 1: base
                RuntimeError("DB blip"),  # tick 2: escalation (fails)
                RuntimeError("DB blip"),  # tick 3: escalation retry (fails)
            ]
        )

        await w.run_once()
        await w.run_once()
        await w.run_once()

        # The base notice fired once; the escalation attempts both
        # failed. No escalation succeeded.
        assert manager.enqueue_message.await_count == 3
        assert w.escalation_notices_enqueued == 0
        # Sweep did NOT abort — the base notice on tick 1 fired,
        # and the tick 3 escalation retry was attempted (it
        # failed too, but the sweep continued).

    @pytest.mark.asyncio
    async def test_release_enqueue_failure_does_not_abort_sweep(self):
        """Release enqueue raises → logged warning, sweep continues.

        Setup: escalation=2, release=3. Tick 1: base. Tick 2:
        escalation. Tick 3: release (fails).
        """
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        w = _build_watchdog(
            manager=manager, repo=repo,
            escalation_nudge_count=2,
            release_after_nudge_count=3,
        )

        # Tick 1: base. Tick 2: escalation. Tick 3: release (fails).
        manager.enqueue_message = AsyncMock(
            side_effect=[
                None,  # tick 1: base
                None,  # tick 2: escalation
                RuntimeError("DB blip"),  # tick 3: release (fails)
            ]
        )

        await w.run_once()
        await w.run_once()
        await w.run_once()

        # Base notice (tick 1) + escalation (tick 2) + release
        # attempt (tick 3, failed).
        assert manager.enqueue_message.await_count == 3
        # Escalation succeeded, release failed.
        assert w.escalation_notices_enqueued == 1
        assert w.release_notices_enqueued == 0


class TestB3DefaultPolicyValues:
    """B3: default values (escalation=3, release=5) per the spec.
    No env flags — pure constructor defaults.
    """

    def test_default_escalation_nudge_count_is_3(self):
        from daemon.services.waiting_children_watchdog import (
            WaitingChildrenWatchdog,
        )

        repo = _build_repo_mock()
        w = WaitingChildrenWatchdog(
            instance_repository=repo,
            manager=_build_manager_mock(),
        )
        assert w.escalation_nudge_count == 3

    def test_default_release_after_nudge_count_is_5(self):
        from daemon.services.waiting_children_watchdog import (
            WaitingChildrenWatchdog,
        )

        repo = _build_repo_mock()
        w = WaitingChildrenWatchdog(
            instance_repository=repo,
            manager=_build_manager_mock(),
        )
        assert w.release_after_nudge_count == 5


class TestB3StaticInvariants:
    """B3 (2026-09-11): the escalation / release cooldowns are
    per-pair episode-scoped. The wedge-pass is unchanged.
    """

    def test_run_once_still_returns_four_key_stats_dict(self):
        """The existing 4-key ``run_once`` stats dict is preserved
        (back-compat with existing test fixtures that pin the
        shape). B3 adds counters via SEPARATE properties.
        """
        from daemon.services.waiting_children_watchdog import (
            WaitingChildrenWatchdog,
        )
        import inspect

        repo = _build_repo_mock()
        w = WaitingChildrenWatchdog(
            instance_repository=repo,
            manager=_build_manager_mock(),
        )
        src = inspect.getsource(w.run_once)
        # The 4-key stats dict is still the source-of-truth for
        # the existing test contract.
        assert '"parents_scanned"' in src
        assert '"parents_skipped_paused"' in src
        assert '"notices_enqueued"' in src
        assert '"errors"' in src

    def test_b3_does_not_add_admission_state_writer(self):
        """B3 uses the existing ``InstanceManager.enqueue_message``
        primitive — no new admission state writer / JobItem creator /
        work_id mint site."""
        from daemon.services.waiting_children_watchdog import (
            WaitingChildrenWatchdog,
        )
        import inspect

        # Read both ``run_once`` (the only place B3 enqueues).
        src = inspect.getsource(WaitingChildrenWatchdog.run_once)
        # B3 escalation / release MUST go through ``self._manager.enqueue_message``.
        assert "self._manager.enqueue_message" in src
        # No direct repo calls (would bypass the facade and add a
        # new writer).
        assert "self._repo.create" not in src
        assert "self._repo.enqueue" not in src
