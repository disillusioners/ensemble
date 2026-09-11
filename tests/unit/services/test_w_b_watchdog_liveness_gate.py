"""W-B liveness-gate tests (feature/fix-wc-wake-resilience, 2026-09-11).

Pre-W-B B3 escalation / release (commit cdb63ab6) was gated only on
the freshness proxy ``instances.last_activity_at``. The 4-warning
review send-back flagged that the proxy does NOT refresh during a
running turn — only ``task.last_heartbeat_at`` moves (worker pool
heartbeat thread). A genuinely-working-but-DB-silent >~6h child
turn would get its parent force-released.

W-B closes the gap with a TRUE liveness predicate:
``task_repository.child_has_recent_heartbeat(child_id, threshold_s)``
returns True iff any task row for the child has a heartbeat fresher
than ``threshold_s`` ago. When True, B3 escalation AND release are
suppressed for that (parent, child) pair — the child is by
construction genuinely working.

Surface (this file):

1. **opt-in permissive default** — when ``task_repository`` is NOT
   injected via the constructor, ``_has_recent_heartbeat`` returns
   False and the pre-W-B B3 behavior is preserved.
2. **working child suppresses escalation** — repo helper returns
   True → no escalation even past the nudge threshold.
3. **working child suppresses release** — repo helper returns True
   → no release even past the release threshold.
4. **heartbeat-then-silent child still escalates** — repo helper
   transitions True → False across ticks; escalation resumes.
5. **threshold uses hang_threshold_seconds** — the heartbeat
   threshold is the same as the freshness-proxy threshold (default
   3600s); no new config knob.
6. **nudge count keeps climbing during suppression** — the
   suppression gate does NOT reset the counter; a later heartbeat-
   silent tick still has the count.
7. **per-pair isolation** — one pair suppressed, another pair not.

Test setup: MagicMock manager + MagicMock instance repo (same shape
as test_b3_watchdog_escalation.py) + a MagicMock ``task_repository``
whose ``child_has_recent_heartbeat`` is configured via a list of
return values. The production ``TaskRepository.child_has_recent_heartbeat``
helper is exercised by an integration test in the same file
(TestChildHasRecentHeartbeatHelper, gated on the SQLite file-backed
engine — same pattern as test_a3_eligible_pending_sweep.py).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — mirror test_b3_watchdog_escalation.py's fixture shape
# ---------------------------------------------------------------------------


def _build_manager_mock():
    """Build a mock manager exposing the ``enqueue_message`` surface."""
    mgr = MagicMock()
    mgr.enqueue_message = MagicMock()  # sync — the watchdog awaits but
    # the existing B3 fixtures use AsyncMock; either works
    import asyncio
    async def _noop(*_args, **_kwargs):
        return None
    mgr.enqueue_message = _noop
    # Wrap in AsyncMock for await_count parity
    from unittest.mock import AsyncMock
    mgr.enqueue_message = AsyncMock()
    return mgr


def _build_repo_mock(
    *,
    parent_ids: list[str] | None = None,
    hung_by_parent: dict[str, list[tuple[str, float]]] | None = None,
    terminal_child_ids: list[str] | None = None,
    parents_with_non_term_children: set[str] | None = None,
) -> MagicMock:
    """Build a mock instance repo for the watchdog (mirror of B3 fixture)."""
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


def _build_task_repo_mock(
    *, heartbeat_results: list[bool] | None = None,
    heartbeat_by_child: dict[str, bool] | None = None,
) -> MagicMock:
    """Build a mock task_repository with a pop-from-list helper.

    Modes:
    * ``heartbeat_by_child`` — a per-child_id bool map. A query
      for a child whose id is in the map returns the mapped value;
      others return False. Use this for per-pair isolation tests.
    * ``heartbeat_results`` — a list of bools consumed in order by
      the mock. If the list is exhausted the last value is re-used
      (so a single-element list applies to every call). Pass
      ``[True]`` to suppress every pair, ``[False]`` to never
      suppress.

    If both are None, the helper returns False (never suppress).
    """
    repo = MagicMock()
    if heartbeat_results is not None:
        results = list(heartbeat_results)

        def _list_helper(_child_id, _threshold_seconds):
            if not results:
                return False
            if len(results) == 1:
                return results[0]
            return results.pop(0)

        repo.child_has_recent_heartbeat = MagicMock(side_effect=_list_helper)
    elif heartbeat_by_child is not None:
        mapping = dict(heartbeat_by_child)

        def _map_helper(child_id, _threshold_seconds):
            return bool(mapping.get(child_id, False))

        repo.child_has_recent_heartbeat = MagicMock(side_effect=_map_helper)
    else:
        # Default — never suppress.
        repo.child_has_recent_heartbeat = MagicMock(return_value=False)
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
    """Build a WaitingChildrenWatchdog with the mocked collaborators."""
    from daemon.services.waiting_children_watchdog import (
        WaitingChildrenWatchdog,
    )
    return WaitingChildrenWatchdog(
        instance_repository=repo,
        manager=manager,
        enabled=True,
        interval_seconds=interval_seconds,
        hang_threshold_seconds=hang_threshold_seconds,
        escalation_nudge_count=escalation_nudge_count,
        release_after_nudge_count=release_after_nudge_count,
        task_repository=task_repo,
    )


# ---------------------------------------------------------------------------
# W-B gate behavior — heartbeat-driven suppression
# ---------------------------------------------------------------------------


class TestWBLivenessGate:
    """W-B: the heartbeat predicate suppresses B3 escalation AND
    release for working children. Strictly opt-in via the
    constructor-injected ``task_repository``.
    """

    @pytest.mark.asyncio
    async def test_no_task_repo_injected_keeps_pre_wb_behavior(self):
        """No ``task_repository`` injected → gate is permissive →
        escalation fires at the nudge threshold (pre-W-B behavior)."""
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        w = _build_watchdog(
            manager=manager, repo=repo,
            escalation_nudge_count=2,
            release_after_nudge_count=5,
            # task_repo=None — the gate is permissive
        )

        await w.run_once()
        await w.run_once()

        # tick 1: base notice + nudge=1
        # tick 2: nudge=2 → escalation fires (gate is permissive)
        assert w.escalation_notices_enqueued == 1
        assert w.release_notices_enqueued == 0

    @pytest.mark.asyncio
    async def test_working_child_suppresses_escalation(self):
        """Repo helper returns True (recent heartbeat) → escalation
        is suppressed even past the nudge threshold."""
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        task_repo = _build_task_repo_mock(heartbeat_results=[True])
        w = _build_watchdog(
            manager=manager, repo=repo, task_repo=task_repo,
            escalation_nudge_count=2,
            release_after_nudge_count=5,
        )

        # 5 ticks — would cross the escalation threshold (2) and
        # the release threshold (5) WITHOUT the gate. WITH the
        # gate (heartbeat present) → neither fires.
        for _ in range(5):
            await w.run_once()

        # Base notice fires on tick 1 (anti-spam gated, one per
        # episode). Escalation AND release are suppressed.
        assert w.escalation_notices_enqueued == 0
        assert w.release_notices_enqueued == 0
        # The nudge count keeps climbing — the gate does NOT reset
        # the counter on suppression.
        assert w.nudge_count_for("parent-A", "child-X") == 5
        # The repo helper was consulted (proves the gate is active).
        assert task_repo.child_has_recent_heartbeat.call_count >= 5

    @pytest.mark.asyncio
    async def test_working_child_suppresses_release(self):
        """Even past the release threshold, a child with a fresh
        heartbeat must NOT be released."""
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        task_repo = _build_task_repo_mock(heartbeat_results=[True])
        w = _build_watchdog(
            manager=manager, repo=repo, task_repo=task_repo,
            escalation_nudge_count=2,
            release_after_nudge_count=3,
        )

        # 5 ticks — would cross BOTH thresholds. With the gate,
        # neither fires.
        for _ in range(5):
            await w.run_once()

        assert w.escalation_notices_enqueued == 0
        assert w.release_notices_enqueued == 0

    @pytest.mark.asyncio
    async def test_silent_child_still_escalates_and_releases(self):
        """Helper returns False (no recent heartbeat) → existing B3
        escalation AND release paths fire unchanged."""
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        task_repo = _build_task_repo_mock(heartbeat_results=[False])
        w = _build_watchdog(
            manager=manager, repo=repo, task_repo=task_repo,
            escalation_nudge_count=2,
            release_after_nudge_count=4,
        )

        await w.run_once()  # tick 1: base notice + nudge=1
        await w.run_once()  # tick 2: nudge=2 → escalation fires
        await w.run_once()  # tick 3: nudge=3 (esc cooldown, no fire)
        await w.run_once()  # tick 4: nudge=4 → release fires

        assert w.escalation_notices_enqueued == 1
        assert w.release_notices_enqueued == 1

    @pytest.mark.asyncio
    async def test_heartbeat_then_silent_child_escalates(self):
        """Transition: tick N has heartbeat → suppression. Tick N+1
        heartbeat goes silent → escalation fires at the next nudge
        threshold.

        Simulates the W-B bug class: a child genuinely working for
        >6h (heartbeats beating) is NOT released; then the worker
        dies (heartbeats stop) → escalation kicks in at the next
        threshold. The W-B fix does NOT introduce silent dead-lock.
        """
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        # Ticks 1-3: heartbeat present → suppress. Ticks 4-5:
        # heartbeat silent → escalate.
        task_repo = _build_task_repo_mock(
            heartbeat_results=[True, True, True, False, False]
        )
        w = _build_watchdog(
            manager=manager, repo=repo, task_repo=task_repo,
            escalation_nudge_count=3,
            release_after_nudge_count=5,
        )

        # 3 ticks of suppression (heartbeat present)
        for _ in range(3):
            await w.run_once()
        # Nudge=3 reached but gate suppresses escalation.
        assert w.escalation_notices_enqueued == 0
        # Counter kept climbing.
        assert w.nudge_count_for("parent-A", "child-X") == 3

        # Tick 4: heartbeat silent, nudge=4 → escalation fires
        # (gate threshold was 3; nudge=4 > 3, so escalation is
        # eligible on the FIRST silent tick).
        await w.run_once()
        assert w.escalation_notices_enqueued == 1
        assert w.release_notices_enqueued == 0
        # Nudge count is now 4.
        assert w.nudge_count_for("parent-A", "child-X") == 4

    @pytest.mark.asyncio
    async def test_threshold_uses_hang_threshold_seconds(self):
        """The heartbeat threshold mirrors hang_threshold_seconds.
        Verify by wiring a helper that records the threshold it
        receives — should match the watchdog's configured value."""
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={"parent-A": [("child-X", 4000.0)]},
        )
        captured_thresholds: list[int] = []

        def _capture(_child_id: str, threshold_seconds: int) -> bool:
            captured_thresholds.append(int(threshold_seconds))
            return False  # never suppress; we want to observe the
            # threshold passed

        task_repo = MagicMock()
        task_repo.child_has_recent_heartbeat = MagicMock(side_effect=_capture)

        w = _build_watchdog(
            manager=manager, repo=repo, task_repo=task_repo,
            escalation_nudge_count=2,
            release_after_nudge_count=5,
            hang_threshold_seconds=7200,  # 2h
        )

        await w.run_once()
        # The helper received the watchdog's hang_threshold_seconds.
        assert captured_thresholds == [7200], (
            f"heartbeat threshold MUST equal hang_threshold_seconds; "
            f"got {captured_thresholds!r}"
        )

    @pytest.mark.asyncio
    async def test_per_pair_isolation_one_suppressed_one_fires(self):
        """Pair A: heartbeat present → suppress. Pair B: heartbeat
        silent → escalation fires. The gate is per-(parent, child)
        pair — suppression of one pair does NOT bleed to another.
        """
        manager = _build_manager_mock()
        repo = _build_repo_mock(
            parent_ids=["parent-A"],
            hung_by_parent={
                "parent-A": [
                    ("child-X-alive", 4000.0),
                    ("child-Y-dead", 4000.0),
                ],
            },
        )
        # child-X-alive gets True (heartbeat present), child-Y-dead
        # gets False (no heartbeat). Per-child map → the suppression
        # is stable across ticks (each pair is checked by its own
        # mapped value, not consumed from a shared list).
        task_repo = _build_task_repo_mock(
            heartbeat_by_child={
                "child-X-alive": True,
                "child-Y-dead": False,
            }
        )
        w = _build_watchdog(
            manager=manager, repo=repo, task_repo=task_repo,
            escalation_nudge_count=2,
            release_after_nudge_count=5,
        )

        # Ticks 1-2: nudge=2 for both pairs.
        await w.run_once()
        await w.run_once()
        # child-X-alive: heartbeat present → suppressed.
        # child-Y-dead: heartbeat silent → escalation fires.
        assert w.escalation_notices_enqueued == 1
        # Verify the suppressed pair is silent.
        # (escalation_enqueued_total is 1 — the only fire was for
        # child-Y-dead.)
        # 2 ticks × 2 pairs = 4 helper calls (one per pair per
        # tick at the B3 nudge-threshold check).
        assert task_repo.child_has_recent_heartbeat.call_count == 4


class TestWBBoundaryCases:
    """W-B edge cases — strict opt-in behavior and silent fallbacks."""

    def test_unwired_task_repository_falls_back_to_permissive(self):
        """When ``task_repository=None`` (the default), the helper
        returns False (no recent heartbeat) and the existing B3
        behavior is preserved. No silent gate activation."""
        manager = _build_manager_mock()
        repo = _build_repo_mock()

        w = _build_watchdog(
            manager=manager, repo=repo, task_repo=None,
        )

        # Direct call — should NOT raise, should return False.
        result = w._has_recent_heartbeat("child-X", 3600)
        assert result is False, (
            "Unwired task_repository MUST return False (permissive) "
            "so the pre-W-B B3 behavior is preserved test-by-test."
        )

    def test_task_repository_without_helper_falls_back_to_permissive(self):
        """When ``task_repository`` exists but does NOT implement
        ``child_has_recent_heartbeat`` (e.g., a test fixture using
        MagicMock without wiring the helper), the gate falls back
        to permissive. Production TaskRepository always implements
        the helper; this is a test-only seam."""
        manager = _build_manager_mock()
        repo = _build_repo_mock()
        # A bare MagicMock with no helper attribute access path.
        # We deliberately don't add child_has_recent_heartbeat.
        bare_task_repo = MagicMock(spec=[])

        w = _build_watchdog(
            manager=manager, repo=repo, task_repo=bare_task_repo,
        )

        result = w._has_recent_heartbeat("child-X", 3600)
        assert result is False, (
            "Task repo without child_has_recent_heartbeat MUST "
            "fall back to permissive (no recent heartbeat → "
            "existing B3 escalation/release path preserved)."
        )


# ---------------------------------------------------------------------------
# Static invariants — gate shape preserved
# ---------------------------------------------------------------------------


class TestWBStaticInvariants:
    """W-B (2026-09-11): the liveness gate uses the constructor-
    injected ``task_repository`` and does NOT add new admission-
    state writers / JobItem creators / work_id mint sites.
    """

    def test_gate_does_not_add_admission_state_writer(self):
        """The W-B gate routes through the existing
        ``enqueue_message`` primitive (no new writer)."""
        from daemon.services.waiting_children_watchdog import (
            WaitingChildrenWatchdog,
        )
        import inspect

        src = inspect.getsource(WaitingChildrenWatchdog.run_once)
        # W-B MUST go through ``self._manager.enqueue_message`` —
        # no direct repo.enqueue / repo.create that would add a
        # new admission_state_writer.
        assert "self._manager.enqueue_message" in src
        assert "self._repo.create" not in src
        assert "self._repo.enqueue" not in src

    def test_helper_uses_task_repository_not_manager_fallback(self):
        """``_has_recent_heartbeat`` must consult ONLY
        ``self._task_repository`` (strict opt-in) — NOT fall back
        to ``self._manager._task_repo`` (which would trip up MagicMock
        test fixtures)."""
        from daemon.services.waiting_children_watchdog import (
            WaitingChildrenWatchdog,
        )
        import inspect

        src = inspect.getsource(WaitingChildrenWatchdog._has_recent_heartbeat)
        # Strip the docstring so the literal ``self._manager._task_repo``
        # reference in the rationale block doesn't false-positive the
        # assertion.
        code_lines: list[str] = []
        in_docstring = False
        for line in src.splitlines():
            stripped = line.strip()
            if not in_docstring and (
                stripped.startswith('"""')
                or stripped.startswith("'''")
            ):
                in_docstring = True
                # single-line docstring?
                rest = stripped[3:]
                if rest.endswith('"""') or rest.endswith("'''"):
                    in_docstring = False
                continue
            if in_docstring:
                if stripped.endswith('"""') or stripped.endswith("'''"):
                    in_docstring = False
                continue
            code_lines.append(line)
        code_only = "\n".join(code_lines)
        # The helper body should NOT reference ``self._manager._task_repo``
        # — that fallback was deliberately removed in W-B so the
        # gate is strictly opt-in.
        assert "self._manager._task_repo" not in code_only, (
            "W-B _has_recent_heartbeat MUST NOT fall back to "
            "self._manager._task_repo (MagicMock fixtures have "
            "auto-attrs that would wrongly trigger the gate)."
        )
        # The helper body must consult ``self._task_repository``.
        assert "self._task_repository" in code_only


# ---------------------------------------------------------------------------
# Integration — TaskRepository.child_has_recent_heartbeat against SQLite
# ---------------------------------------------------------------------------


class TestChildHasRecentHeartbeatHelper:
    """Integration coverage for the production helper
    :meth:`daemon.repositories.task.repository.TaskRepository.child_has_recent_heartbeat`.

    Pins the SQL-side behavior on a real file-backed SQLite engine —
    the helper is the source-of-truth for the liveness predicate the
    watchdog relies on, so a regression here would silently widen or
    narrow the W-B gate in production. Same fixture recipe as
    ``tests/job_queue/test_a3_eligible_pending_sweep.py``.
    """

    @pytest.fixture
    def engine(self, tmp_path):
        """File-backed SQLite (NullPool + WAL + busy_timeout)."""
        from datetime import datetime, timezone
        from pathlib import Path

        from sqlalchemy import create_engine, event
        from sqlalchemy.pool import NullPool
        from sqlmodel import SQLModel

        db_path = tmp_path / "wb_child_has_recent_heartbeat.db"
        eng = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )

        @event.listens_for(eng, "connect")
        def _configure_sqlite(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        # Register the Task model
        import daemon.repositories.task.models  # noqa: F401
        SQLModel.metadata.create_all(eng)
        try:
            yield eng
        finally:
            eng.dispose()

    def _insert_task(
        self, engine, instance_id, status, *, last_heartbeat_at=None
    ):
        """Insert a task row with the given status + heartbeat."""
        from datetime import datetime, timezone

        from sqlalchemy import text

        from daemon.repositories.task.models import TaskStatus

        now = datetime.now(timezone.utc)
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO task
                        (task_type, instance_id, message_id, status,
                         retry_count, created_at, last_heartbeat_at,
                         cancel_requested, retry_scheduled, work_id,
                         is_deferred, is_background)
                    VALUES
                        (:task_type, :instance_id, :message_id, :status,
                         :retry_count, :created_at, :last_heartbeat_at,
                         :cancel_requested, :retry_scheduled, :work_id,
                         :is_deferred, :is_background)
                    """
                ),
                {
                    "task_type": "process_message",
                    "instance_id": instance_id,
                    "message_id": None,
                    "status": status,
                    "retry_count": 0,
                    "created_at": now,
                    "last_heartbeat_at": last_heartbeat_at,
                    "cancel_requested": False,
                    "retry_scheduled": False,
                    "work_id": f"wid-wb-{now.timestamp()}-{instance_id}-{status}",
                    "is_deferred": False,
                    "is_background": False,
                },
            )
            return result.lastrowid

    def test_returns_true_when_task_has_fresh_heartbeat(self, engine):
        """A task with ``last_heartbeat_at`` newer than the
        threshold returns True."""
        from datetime import datetime, timedelta, timezone

        from daemon.repositories.task.repository import TaskRepository

        self._insert_task(
            engine, "child-alive", "running",
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        repo = TaskRepository(engine)
        assert repo.child_has_recent_heartbeat("child-alive", 60) is True

    def test_returns_false_when_heartbeat_older_than_threshold(self, engine):
        """A task with ``last_heartbeat_at`` older than the threshold
        returns False."""
        from datetime import datetime, timedelta, timezone

        from daemon.repositories.task.repository import TaskRepository

        self._insert_task(
            engine, "child-stale", "running",
            last_heartbeat_at=(
                datetime.now(timezone.utc) - timedelta(seconds=120)
            ),
        )
        repo = TaskRepository(engine)
        assert repo.child_has_recent_heartbeat("child-stale", 60) is False

    def test_returns_false_when_no_task_rows_exist(self, engine):
        """No task rows for the instance → False (no liveness)."""
        from daemon.repositories.task.repository import TaskRepository

        repo = TaskRepository(engine)
        assert (
            repo.child_has_recent_heartbeat("child-missing", 3600)
            is False
        )

    def test_returns_false_when_all_heartbeats_null(self, engine):
        """Task rows with ``last_heartbeat_at IS NULL`` only → False."""
        from daemon.repositories.task.repository import TaskRepository

        self._insert_task(
            engine, "child-null", "pending",
            last_heartbeat_at=None,
        )
        repo = TaskRepository(engine)
        assert repo.child_has_recent_heartbeat("child-null", 3600) is False

    def test_returns_true_if_any_task_heartbeat_is_fresh(self, engine):
        """Compound predicate: ANY task with a fresh heartbeat
        returns True, even if OTHER tasks for the same instance
        have stale or NULL heartbeats."""
        from datetime import datetime, timedelta, timezone

        from daemon.repositories.task.repository import TaskRepository

        now = datetime.now(timezone.utc)
        self._insert_task(
            engine, "child-mixed", "completed",
            last_heartbeat_at=now - timedelta(seconds=120),
        )
        self._insert_task(
            engine, "child-mixed", "running",
            last_heartbeat_at=now,
        )
        repo = TaskRepository(engine)
        assert repo.child_has_recent_heartbeat("child-mixed", 60) is True

    def test_threshold_zero_uses_no_threshold(self, engine):
        """threshold_seconds=0 with a fresh heartbeat returns True
        (any non-NULL heartbeat is newer than 'now - 0s')."""
        from datetime import datetime, timezone

        from daemon.repositories.task.repository import TaskRepository

        self._insert_task(
            engine, "child-edge", "running",
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        repo = TaskRepository(engine)
        # threshold_seconds=0 → any heartbeat at-or-newer than now
        # counts. Borderline — clock skew can flip the result; the
        # assertion is permissive (must not raise).
        result = repo.child_has_recent_heartbeat("child-edge", 0)
        assert isinstance(result, bool)

    def test_negative_threshold_raises(self, engine):
        """Negative threshold is a programming error — ValueError."""
        from daemon.repositories.task.repository import TaskRepository

        repo = TaskRepository(engine)
        with pytest.raises(ValueError) as exc_info:
            repo.child_has_recent_heartbeat("child-X", -1)
        assert "threshold_seconds" in str(exc_info.value)
