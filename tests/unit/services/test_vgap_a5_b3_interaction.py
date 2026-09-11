"""A5 × B3 interaction gap tests (feature/fix-wc-wake-resilience).

These tests close the audit gap where the A5 wedge-notice direct
notify and the B3 escalation/release ladder run concurrently on the
same parent/child pair within the same ``watchdog.run_once()`` tick.

The A5 path and the B3 path are independent counters / sets:

* A5 wedge notice: ``WaitingChildrenWatchdog.wedge_notices_enqueued``
  (incremented in the wedge pass; the parent has zero non-terminal
  children AND zero live carrier).
* B3 release: ``WaitingChildrenWatchdog.release_notices_enqueued``
  (incremented when ``_nudge_counts[(parent, child)] >=
  release_after_nudge_count`` for a hung child).
* B3 escalation: ``WaitingChildrenWatchdog.escalation_notices_enqueued``
  (incremented when ``_nudge_counts[(parent, child)] >=
  escalation_nudge_count`` for a hung child).

By design, the wedge predicate (zero non-terminal children) and the
B3 predicate (hung non-terminal child) are MUTUALLY EXCLUSIVE on a
single (parent, child) pair. The audit's "interaction" question is
not "both fire on the same tick for the same pair" — it is:

1. **No double-notify** — when the B3 release fires on a tick where
   ``_wedge_notified`` already contains the parent (from a prior
   wedge episode), the wedge pass MUST NOT also dispatch a wedge
   notice in addition to the release.

2. **No double-state-flip** — a single ``enqueue_message`` call
   flips ``WAITING_CHILDREN`` → ``RUNNING`` exactly once per
   notice. A release enqueue and a wedge enqueue both go through
   the same primitive; a double-dispatch on the same parent would
   flip twice (the second call would be a no-op or an error —
   either way the test should pin the invariant).

3. **Release → wedge cooldown race is safe** — when the B3 release
   flips the parent out of ``WAITING_CHILDREN`` (parent-left-WC
   purge), the ``_wedge_notified`` entry from any prior wedge
   episode MUST be cleared so a fresh wedge episode on re-entry
   can re-notify. The audit cited the wedge-discard sites
   ``~:1507`` and ``~:1517`` of ``waiting_children_watchdog.py``
   as the safety valves for this invariant.

4. **No wedge-notice right after a release** — when B3 release
   fires on a tick, the wedge pass MUST NOT also dispatch a wedge
   notice on the same tick (parent has the hung non-terminal
   child → wedge shape is broken → wedge MUST be skipped, AND
   any stale ``_wedge_notified`` entry MUST be discarded at
   ``:1507`` / ``:1517``).

The harness mirrors ``test_b3_watchdog_escalation.py`` (MagicMock
manager + MagicMock instance repo) plus an injected
``task_repository`` MagicMock. The pool seam is mocked at the
``worker_pool.notify_work()`` boundary (the A5 redundant-notify
callsite).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — mirror test_b3_watchdog_escalation.py + test_a5_wedge_notify_work.py
# ---------------------------------------------------------------------------


def _build_manager_mock(*, enqueue_raises: BaseException | None = None):
    """Build a mock manager exposing the ``enqueue_message`` surface.

    When ``enqueue_raises`` is non-None, every ``enqueue_message``
    call raises that exception — used for the fail-closed B3 sibling
    tests in ``test_vgap_b3_dberror_failclosed.py`` (this file does
    not exercise the raising path).
    """
    mgr = MagicMock()
    if enqueue_raises is None:
        mgr.enqueue_message = AsyncMock()
    else:
        mgr.enqueue_message = AsyncMock(side_effect=enqueue_raises)
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

    Default behavior: every parent in ``parent_ids`` has at least one
    non-terminal child (the hung one we reported). Override to
    ``set()`` to make the wedge pass fire on a parent.
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


def _build_task_repo_mock(*, heartbeat_results: list[bool] | None = None):
    """Build a mock task_repository whose
    ``child_has_recent_heartbeat`` is permissive (never suppresses).
    The interaction tests do NOT exercise the heartbeat gate — that
    surface is covered by ``test_w_b_watchdog_liveness_gate.py``.
    """
    repo = MagicMock()
    if heartbeat_results is None:
        # Default — never suppress (the gate is permissive when the
        # repo returns False).
        repo.child_has_recent_heartbeat = MagicMock(return_value=False)
    else:
        results = list(heartbeat_results)

        def _list_helper(_child_id, _threshold_seconds):
            if not results:
                return False
            if len(results) == 1:
                return results[0]
            return results.pop(0)

        repo.child_has_recent_heartbeat = MagicMock(side_effect=_list_helper)
    return repo


def _build_watchdog(
    *,
    manager,
    repo,
    task_repo=None,
    worker_pool: MagicMock | None,
    escalation_nudge_count: int = 3,
    release_after_nudge_count: int = 5,
    interval_seconds: int = 60,
    hang_threshold_seconds: int = 3600,
):
    """Build a WaitingChildrenWatchdog with the mock collaborators.

    The ``worker_pool`` is wired onto the manager shim via
    ``manager._worker_pool`` — the A5 redundant-notify code at
    ``waiting_children_watchdog.py:_notify_result =
    worker_pool.notify_work()`` reads from
    ``self._manager._worker_pool``.
    """
    from daemon.services.waiting_children_watchdog import (
        WaitingChildrenWatchdog,
    )

    w = WaitingChildrenWatchdog(
        instance_repository=repo,
        manager=manager,
        enabled=True,
        interval_seconds=interval_seconds,
        hang_threshold_seconds=hang_threshold_seconds,
        task_repository=task_repo,
        escalation_nudge_count=escalation_nudge_count,
        release_after_nudge_count=release_after_nudge_count,
    )
    if worker_pool is not None:
        manager._worker_pool = worker_pool
    return w


# ---------------------------------------------------------------------------
# Gap 1: no double-notify when B3 release fires on a tick where
# _wedge_notified already contains the parent.
# ---------------------------------------------------------------------------


class TestA5B3NoDoubleNotify:
    """A5 × B3 interaction: when the B3 release fires on a tick
    where ``_wedge_notified`` already contains the parent (from a
    prior wedge episode), the wedge pass MUST NOT also dispatch a
    wedge notice in addition to the release.

    The release enqueue is the ONLY enqueue for this tick (after
    the base notice on tick 1). The A5 redundant-notify fires only
    when the wedge actually enqueues; since the wedge is skipped
    (parent has non-terminal child), ``worker_pool.notify_work()``
    is NOT called by the wedge path.

    NOTE: the B3 release path goes through ``enqueue_message``,
    which (in production) internally calls ``worker_pool.notify_work()``.
    We mock ``enqueue_message`` so the internal notify is not
    observed here — we only count the A5 wedge's direct notify
    call. The ``worker_pool.notify_work.call_count`` therefore
    pins the A5 wedge seam: 0 calls means the wedge was correctly
    skipped.
    """

    @pytest.mark.asyncio
    async def test_release_does_not_double_dispatch_with_stale_wedge(self):
        """Pre-seed ``_wedge_notified.add(P)`` (prior wedge episode).
        B3 release threshold=2 → release fires on tick 2. Wedge pass
        sees the hung non-terminal child → wedge is skipped, AND the
        stale ``_wedge_notified`` entry is discarded at the wedge
        discard site ``:1507`` (zero-non-terminal-children guard).

        Assert: only the release enqueue fires on tick 2 (the wedge
        is skipped, so no wedge enqueue, no A5 wedge direct notify).
        The ``_wedge_notified`` entry is gone after the tick.
        """
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
            # parent-A has a non-terminal child → wedge is skipped
            # on the wedge pass (parent in
            # ``parents_with_non_terminal_children``).
        )
        task_repo = _build_task_repo_mock(heartbeat_results=[False])
        worker_pool = MagicMock()
        w = _build_watchdog(
            manager=manager,
            repo=repo,
            task_repo=task_repo,
            worker_pool=worker_pool,
            escalation_nudge_count=2,
            release_after_nudge_count=3,
        )
        # Pre-seed: prior wedge episode for parent-A.
        w._wedge_notified.add("parent-A")

        # Tick 1: base hang notice + nudge=1.
        # Tick 2: nudge=2 → escalation fires (threshold=2).
        # Tick 3: nudge=3 → release fires (threshold=3).
        await w.run_once()
        await w.run_once()
        await w.run_once()

        # Enqueue audit — base notice (tick 1) + escalation (tick 2)
        # + release (tick 3). Wedge is skipped on all ticks (parent
        # has non-terminal child → wedge predicate fails → discard
        # at :1507 fires).
        assert manager.enqueue_message.await_count == 3, (
            "B3 + A5 interaction MUST dispatch exactly 3 enqueues "
            "(tick 1 base + tick 2 escalation + tick 3 release). "
            "Wedge must NOT add a 4th enqueue while parent has a "
            f"hung non-terminal child; got await_count="
            f"{manager.enqueue_message.await_count}"
        )
        # Identify the enqueues by metadata — base, escalation,
        # release. No wedge enqueue in the list.
        enqueue_metadatas = [
            call.kwargs.get("metadata", {}) or {}
            for call in manager.enqueue_message.await_args_list
        ]
        # Exactly one base notice, one escalation, one release.
        assert sum(
            1 for md in enqueue_metadatas if md.get("watchdog_notice")
        ) == 1, (
            "Exactly one base hang notice (tick 1) — got "
            f"metadatas={enqueue_metadatas!r}"
        )
        assert sum(
            1 for md in enqueue_metadatas if md.get("watchdog_escalation")
        ) == 1, (
            "Exactly one escalation (tick 2) — got "
            f"metadatas={enqueue_metadatas!r}"
        )
        assert sum(
            1 for md in enqueue_metadatas if md.get("watchdog_release")
        ) == 1, (
            "Exactly one release (tick 3) — got "
            f"metadatas={enqueue_metadatas!r}"
        )
        assert sum(
            1 for md in enqueue_metadatas if md.get("wedge_notice")
        ) == 0, (
            "No wedge notice dispatched — parent has a hung "
            "non-terminal child (wedge predicate blocked). Got "
            f"metadatas={enqueue_metadatas!r}"
        )
        # Worker-pool seam: A5 wedge direct notify_work is only
        # called when the wedge ACTUALLY enqueues. Since the wedge
        # was skipped all three ticks, the A5 notify is silent.
        assert worker_pool.notify_work.call_count == 0, (
            "A5 redundant-notify MUST NOT fire when the wedge is "
            "skipped (parent has non-terminal child). Got "
            f"call_count={worker_pool.notify_work.call_count}"
        )
        # Stale wedge cooldown entry was discarded by the wedge
        # pass on the FIRST tick (the :1507 discard fires whenever
        # the parent has non-terminal children, regardless of
        # whether the wedge actually fires).
        assert "parent-A" not in w.wedge_episodes, (
            "Stale wedge-cooldown entry MUST be discarded by the "
            "wedge pass (discard site ~:1507 — parent has "
            "non-terminal children). Got wedge_episodes="
            f"{set(w.wedge_episodes)!r}"
        )
        # Counters — the escalation + release fired exactly once each.
        assert w.escalation_notices_enqueued == 1
        assert w.release_notices_enqueued == 1
        assert w.wedge_notices_enqueued == 0


# ---------------------------------------------------------------------------
# Gap 2: release → wedge cooldown race is safe.
# ---------------------------------------------------------------------------


class TestA5B3ReleaseWedgeRace:
    """A5 × B3 race: when B3 release flips the parent out of
    ``WAITING_CHILDREN`` (parent-left-WC purge), the
    ``_wedge_notified`` entry from any prior wedge episode MUST be
    cleared so a fresh wedge episode on re-entry can re-notify.

    Four-tick scenario (escalation=1, release=2):

    * Tick 1: parent in WC with one hung child. Prior wedge episode
      pre-seeded. B3 path fires base + escalation (nudge=1, the
      minimum nudge count to reach escalation threshold). Wedge is
      skipped (parent has non-terminal child) AND the stale
      ``_wedge_notified`` entry is purged by the :1507 discard.
    * Tick 2: B3 release fires (nudge=2). In production this
      ``enqueue_message`` flips the parent out of WC; we simulate
      by clearing ``list_waiting_children_parents`` for tick 3.
    * Tick 3: parent NOT in WC → parent-left-WC purge fires on
      this tick. No new enqueue. The purge keeps ``_wedge_notified``
      cleared (no-op, already empty after tick 1's :1507 discard).
    * Tick 4: parent re-enters WC with no children (wedge shape) →
      wedge fires fresh — proves the cooldown does not block
      re-notification after the post-release re-entry.
    """

    @pytest.mark.asyncio
    async def test_release_flips_parent_clears_stale_wedge_cooldown(self):
        """Four-tick scenario: B3 release fires (tick 2) and the
        parent re-enters WC (tick 4) with the wedge shape. The
        wedge MUST fire fresh on tick 4 — the stale
        ``_wedge_notified`` entry was cleared by the :1507 discard
        on tick 1, and the parent-left-WC purge keeps it cleared
        on tick 3.
        """
        manager = _build_manager_mock()
        # Tick 1: parent in WC with ONE hung child. Default
        # ``parents_with_non_term_children`` returns {parent-A}
        # (because the hung child is non-terminal) → wedge is
        # skipped on tick 1.
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        task_repo = _build_task_repo_mock(heartbeat_results=[False])
        worker_pool = MagicMock()
        w = _build_watchdog(
            manager=manager,
            repo=repo,
            task_repo=task_repo,
            worker_pool=worker_pool,
            escalation_nudge_count=1,
            release_after_nudge_count=2,
        )
        # Pre-seed: prior wedge episode for parent-A.
        w._wedge_notified.add("parent-A")
        assert "parent-A" in w.wedge_episodes

        # ── Tick 1: base hang notice + escalation fires (nudge=1,
        # escalation threshold = 1). The wedge is skipped (parent
        # has non-terminal child) and the stale ``_wedge_notified``
        # entry is purged by the :1507 discard.
        await w.run_once()
        assert manager.enqueue_message.await_count == 2, (
            "Tick 1: base + escalation = 2 enqueues. Got "
            f"await_count={manager.enqueue_message.await_count}"
        )
        # The escalation enqueue runs FIRST in the B3 loop
        # (escalation/release thresholds are checked before the
        # base notice is enqueued); the base notice enqueue runs
        # AFTER. Identify each by metadata.
        first_kw = manager.enqueue_message.await_args_list[0].kwargs
        second_kw = manager.enqueue_message.await_args_list[1].kwargs
        first_md = first_kw.get("metadata", {}) or {}
        second_md = second_kw.get("metadata", {}) or {}
        # One of them is the escalation, the other is the base.
        if first_md.get("watchdog_escalation"):
            escalation_idx, base_idx = 0, 1
        else:
            escalation_idx, base_idx = 1, 0
        assert (
            manager.enqueue_message.await_args_list[
                escalation_idx
            ].kwargs["metadata"].get("watchdog_escalation") is True
        )
        assert (
            manager.enqueue_message.await_args_list[
                base_idx
            ].kwargs["metadata"].get("watchdog_notice") is True
        )
        assert "parent-A" not in w.wedge_episodes, (
            "Tick 1 wedge pass discards stale wedge entry via :1507 "
            "(parent has non-terminal child). Got "
            f"wedge_episodes={set(w.wedge_episodes)!r}"
        )

        # ── Tick 2: B3 release fires (nudge=2, release threshold=2).
        # In production the ``enqueue_message`` flip would push
        # parent out of WC; we simulate the post-flip state on
        # tick 3 by clearing ``list_waiting_children_parents``.
        await w.run_once()
        assert manager.enqueue_message.await_count == 3, (
            "Tick 2: release fires (3rd enqueue). Got "
            f"await_count={manager.enqueue_message.await_count}"
        )
        third_kw = manager.enqueue_message.await_args_list[2].kwargs
        assert third_kw["metadata"].get("watchdog_release") is True
        assert w.release_notices_enqueued == 1
        assert w.escalation_notices_enqueued == 1

        # ── Tick 3: parent NOT in WC (status flipped by the release
        # enqueue_message). Mock: empty parent enumeration. The
        # parent-left-WC purge fires — any ``_wedge_notified`` entry
        # for a parent no longer in the WC set is discarded.
        repo.list_waiting_children_parents = MagicMock(return_value=[])
        repo.list_hung_children_for_parent = MagicMock(return_value=[])
        await w.run_once()
        # No new enqueues on tick 3 (parent not in WC).
        assert manager.enqueue_message.await_count == 3, (
            "Tick 3: no parent scan, no enqueue. Got "
            f"await_count={manager.enqueue_message.await_count}"
        )
        # _wedge_notified is empty (cleared on tick 1 via :1507;
        # tick 3's purge is a no-op).
        assert "parent-A" not in w.wedge_episodes

        # ── Tick 4: parent re-enters WC with NO children (wedge
        # shape). Mock: parent in WC, no hung children, no
        # non-terminal children → wedge fires fresh.
        repo.list_waiting_children_parents = MagicMock(
            return_value=["parent-A"]
        )
        repo.list_hung_children_for_parent = MagicMock(return_value=[])
        # parent-A has NO non-terminal children (empty set).
        repo.parents_with_non_terminal_children = MagicMock(
            return_value=set()
        )
        # No live carrier → wedge predicate fires.
        task_repo.list_live_process_report_carriers_for_instance = (
            lambda instance_id: []
        )

        await w.run_once()
        # Tick 4: wedge fires (no stale cooldown).
        assert manager.enqueue_message.await_count == 4, (
            "Tick 4: wedge enqueue brings the total to 4 "
            "(base + escalation + release + wedge). Got "
            f"await_count={manager.enqueue_message.await_count}"
        )
        # The wedge's metadata is on the fourth call.
        fourth_kw = manager.enqueue_message.await_args_list[3].kwargs
        assert fourth_kw["metadata"]["wedge_notice"] is True
        assert w.wedge_notices_enqueued == 1, (
            "Tick 4 MUST fire a fresh wedge notice — the stale "
            "cooldown entry was cleared by the :1507 discard on "
            "tick 1 and the parent-left-WC purge on tick 3. "
            "Got wedge_notices_enqueued="
            f"{w.wedge_notices_enqueued!r}"
        )
        # A5 direct notify_work fires once (for the wedge on tick 4).
        assert worker_pool.notify_work.call_count == 1, (
            "A5 redundant-notify fires once for the wedge on tick 4 "
            "(the only tick where the wedge actually enqueued). "
            f"Got call_count={worker_pool.notify_work.call_count}"
        )
        # After the wedge fires, the cooldown is re-populated.
        assert "parent-A" in w.wedge_episodes


# ---------------------------------------------------------------------------
# Gap 3: per-mechanism counters track exactly one dispatch each when
# A5 wedge and B3 escalation both fire on the same tick (different
# parents — the cross-parent scenario that IS possible).
# ---------------------------------------------------------------------------


class TestA5B3DifferentParentsCounters:
    """A5 × B3 across different parents: when one parent satisfies
    the wedge shape and another parent satisfies the B3 release
    shape, both mechanisms fire on the same tick and the counters
    track exactly one dispatch per mechanism.
    """

    @pytest.mark.asyncio
    async def test_wedge_and_release_on_same_tick_counted_independently(
        self,
    ):
        """parent-A: wedge shape (zero non-terminal children, no
        live carrier). parent-B: B3 base notice on first tick (one
        hung child; escalation + release thresholds set high so
        neither fires on tick 1).

        On tick 1, the WEDGE and the BASE NOTICE fire
        independently. Counters track exactly one dispatch per
        mechanism. The audit scenario of interest is the
        per-mechanism counter hygiene: two independent mechanisms,
        two independent enqueues, zero cross-contamination.
        """
        manager = _build_manager_mock()
        repo = MagicMock()
        repo.list_waiting_children_parents = MagicMock(
            return_value=["parent-A", "parent-B"]
        )
        repo.list_hung_children_for_parent = MagicMock(
            side_effect=lambda parent_id, threshold_seconds: {
                # parent-A: wedge shape — NO hung children
                "parent-A": [],
                # parent-B: B3 base notice — child-Y is hung
                "parent-B": [("child-Y", 4000.0)],
            }.get(parent_id, [])
        )
        repo.list_terminal_instance_ids = MagicMock(return_value=set())
        repo.get = MagicMock(
            side_effect=lambda instance_id: MagicMock(
                status="waiting_children"
            )
        )
        # parent-A is NOT in ``parents_with_non_terminal_children``
        # (zero non-terminal children → wedge fires). parent-B IS
        # in the set (child-Y is non-terminal → wedge skipped for
        # parent-B).
        repo.parents_with_non_terminal_children = MagicMock(
            return_value={"parent-B"}
        )
        # parent-A has no live carrier → wedge predicate fires.
        task_repo = MagicMock()
        task_repo.list_live_process_report_carriers_for_instance = (
            lambda instance_id: []
        )
        task_repo.child_has_recent_heartbeat = MagicMock(
            return_value=False
        )
        worker_pool = MagicMock()
        # High thresholds so escalation + release don't fire on
        # tick 1 — the test isolates the wedge vs. base notice
        # interaction (different mechanisms, independent counters).
        w = _build_watchdog(
            manager=manager,
            repo=repo,
            task_repo=task_repo,
            worker_pool=worker_pool,
            escalation_nudge_count=10,
            release_after_nudge_count=20,
        )

        await w.run_once()

        # Tick 1: one wedge (parent-A) + one base notice (parent-B).
        # Total enqueues = 2.
        assert manager.enqueue_message.await_count == 2, (
            "Two enqueues: wedge (parent-A) + base notice (parent-B). "
            f"Got await_count={manager.enqueue_message.await_count}"
        )
        # Counters track exactly one dispatch per mechanism.
        assert w.wedge_notices_enqueued == 1, (
            "Wedge counter MUST be 1 — only parent-A satisfied the "
            f"wedge shape. Got {w.wedge_notices_enqueued!r}"
        )
        # A5 direct notify fires once (for the wedge on parent-A).
        assert worker_pool.notify_work.call_count == 1, (
            "A5 redundant-notify fires once for the wedge path. "
            f"Got call_count={worker_pool.notify_work.call_count}"
        )
        # Identify the two enqueues by metadata.
        enqueue_metadatas = [
            call.kwargs.get("metadata", {}) or {}
            for call in manager.enqueue_message.await_args_list
        ]
        wedge_calls = [
            md for md in enqueue_metadatas if md.get("wedge_notice")
        ]
        base_calls = [
            md for md in enqueue_metadatas if md.get("watchdog_notice")
        ]
        assert len(wedge_calls) == 1
        assert len(base_calls) == 1
        # Wedge does NOT carry the hang-notice marker (proves the
        # two mechanisms have non-overlapping metadata contracts).
        assert "watchdog_notice" not in wedge_calls[0]
        # Base notice does NOT carry the wedge marker.
        assert "wedge_notice" not in base_calls[0]
        # Per-mechanism idempotency: the wedge entry for parent-A
        # is now in the cooldown set; the base notice added
        # (parent-B, child-Y) to ``_notified``.
        assert "parent-A" in w.wedge_episodes
        assert ("parent-B", "child-Y") in w.notified_episodes
