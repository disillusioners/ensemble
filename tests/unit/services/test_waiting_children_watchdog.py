"""Tests for the WAITING_CHILDREN hang watchdog (issue #8).

Covers the watchdog contract end-to-end:

* Threshold boundary exactness — child at ``threshold-1`` seconds is
  NOT hung, child at ``threshold+epsilon`` IS hung (the repo helper
  uses ``age > threshold``).
* Hang predicate exclusions — ``paused`` AND ``waiting_children``
  children are never counted hung (parking does not refresh
  ``last_activity_at``; nudging would duplicate live work).
* Healthy tree (children active recently) → no-op, no delivery.
* PAUSED parent → skipped, ``enqueue_message`` NOT called.
* Notice content + provenance — the ``source`` kwarg IS stamped onto
  the durable ``MessageQueue`` row (the enqueue path's provenance
  home) plus the structured ``message_metadata`` audit dict.
* **ACCEPTANCE (pure hang)** — a single hung child, NO sibling
  termination, NO external message: the notice travels the REAL
  wake path (``InstanceMessagingService.enqueue_message`` →
  ``_prepare_enqueued_message``: MessageQueue + Task rows, WC→RUNNING
  flip, worker-pool ``notify_work``). The parent is observably woken.
* Anti-spam — once per (parent, child, episode); episode reset via
  the scan sweep (child terminal/paused/resumed), the parent-left-WC
  purge, and the child-terminal purge (independent of scan success).
* Dialect parity — the PostgreSQL ``EXTRACT(EPOCH ...)`` branch and
  the SQLite ``julianday`` branch of the hang SQL are
  compile-checked / render-verified without a PG server.
* Disabled flag → loop returns immediately, no scan.
* Per-parent error isolation — one parent's scan raising does not
  block processing of others.
* Graceful shutdown — the loop ``run_waiting_children_watchdog_loop``
  cancels cleanly on ``asyncio.CancelledError``, no leaked task,
  no raise.
* Scheduler tick behavior — the loop calls ``run_once`` on each tick
  and sleeps between ticks (verified via the ``run_once`` call count
  + the post-cycle sleep; we patch ``asyncio.sleep`` so the test
  finishes in milliseconds, not hours).

REGRESSION: v1 delivered the notice via ``manager.set_injection`` —
a RAM FIFO append that NEVER wakes a quiesced WAITING_CHILDREN
parent (no Task, no status flip, no ``notify_work``); the notice
stranded and was TTL-deleted ~1h later (deep-review 2026-08-27,
range 85ae6e72..fe076043, REJECTED). The delivery primitive is now
``enqueue_message`` — the house wake path. The pure-hang acceptance
test below is the test that would have caught the v1 defect.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql, sqlite as sqlite_dialect
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskStatus
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.waiting_children_watchdog import (
    WATCHDOG_SOURCE,
    WaitingChildrenWatchdog,
    _build_hang_notice,
    _format_age_human,
    run_waiting_children_watchdog_loop,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine with the Instance table created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> SQLModelInstanceRepository:
    """Repository bound to the in-memory engine."""
    return SQLModelInstanceRepository(engine=engine)


@pytest.fixture
def manager() -> AsyncMock:
    """An async mock manager that records ``enqueue_message`` calls.

    Uses ``AsyncMock`` because the production delivery call is
    ``await manager.enqueue_message(...)`` — the waking path that
    writes the MessageQueue + Task rows and flips the parked parent
    to RUNNING. The pure-hang acceptance test replaces this mock
    with the REAL ``InstanceMessagingService``.
    """
    m = AsyncMock()
    m.enqueue_message = AsyncMock()
    return m


@pytest.fixture
def make_instance(repo):
    """Factory that inserts an ``Instance`` row directly via SQL.

    Bypasses the full ``create`` flow because the watchdog only
    cares about ``status``, ``parent_id``, ``last_activity_at`` —
    the other columns are noise for these tests. Returns the
    instance_id so callers can wire parent/child relationships.

    Args:
        status: One of ``InstanceStatus`` values.
        parent_id: ``None`` for a root, an existing instance_id
            for a child.
        age_seconds: How long ago ``last_activity_at`` was set.
            ``None`` leaves ``last_activity_at`` NULL (rare but
            supported by the schema).
        instance_id: Optional explicit ID. When omitted, an
            auto-incrementing ``inst-NNNN`` id is used.
    """
    counter = {"value": 0}

    def _factory(
        *,
        status: InstanceStatus = InstanceStatus.RUNNING,
        parent_id: str | None = None,
        age_seconds: int | None = 0,
        instance_id: str | None = None,
    ) -> str:
        counter["value"] += 1
        if instance_id is None:
            instance_id = f"inst-{counter['value']:04d}"
        now = datetime.now(timezone.utc)
        last_activity = (
            (now - timedelta(seconds=age_seconds)).replace(tzinfo=None)
            if age_seconds is not None
            else None
        )
        with repo.engine.begin() as conn:
            conn.execute(
                Instance.__table__.insert().values(
                    instance_id=instance_id,
                    agent_id="test-agent",
                    agent_dir="/tmp/test-agent",
                    agent_name="test",
                    parent_id=parent_id,
                    status=status.value,
                    last_activity_at=last_activity,
                    created_at=now.replace(tzinfo=None).isoformat(),
                    updated_at=now.replace(tzinfo=None).isoformat(),
                )
            )
        return instance_id

    return _factory


# ─── Helper-function tests (cheap, deterministic) ────────────────────────────


class TestFormatAgeHuman:
    """``_format_age_human`` is a small formatter; pin its output."""

    def test_seconds_below_minute(self) -> None:
        assert _format_age_human(0.0) == "0s"
        assert _format_age_human(45.0) == "45s"
        assert _format_age_human(59.0) == "59s"

    def test_minutes(self) -> None:
        assert _format_age_human(60.0) == "1m"
        assert _format_age_human(125.0) == "2m"
        assert _format_age_human(3599.0) == "59m"

    def test_hours_only(self) -> None:
        # When minutes round to 0, suppress them.
        assert _format_age_human(3600.0) == "1h"
        assert _format_age_human(7200.0) == "2h"

    def test_hours_and_minutes(self) -> None:
        assert _format_age_human(3725.0) == "1h2m"
        assert _format_age_human(90061.0) == "25h1m"


class TestBuildHangNotice:
    """``_build_hang_notice`` content + structure."""

    def test_lists_each_hung_child_with_age(self) -> None:
        notice = _build_hang_notice(
            parent_id="parent-1234",
            hung_children=[
                ("child-aaaa1111", 3725.0),
                ("child-bbbb2222", 7325.0),
            ],
            hang_threshold_seconds=3600,
        )
        assert "[system:watchdog]" in notice
        # Both children listed with their 8-char prefix + "..." + age.
        assert "child-aa..." in notice
        assert "child-bb..." in notice
        assert "1h2m" in notice
        # 7325s = 2h2m5s; formatter truncates to "2h2m".
        assert "2h2m" in notice
        # Threshold surfaced.
        assert "1h" in notice

    def test_includes_playbook(self) -> None:
        notice = _build_hang_notice(
            parent_id="parent-1234",
            hung_children=[("child-aaaa1111", 4000.0)],
            hang_threshold_seconds=3600,
        )
        # All four playbook steps present.
        assert "subtree_messages" in notice
        assert "send_message" in notice
        assert "spawn a replacement" in notice
        assert "escalate" in notice


# ─── Constructor guards ─────────────────────────────────────────────────────


class TestConstructor:
    """Argument validation in the watchdog ctor."""

    def test_rejects_non_positive_interval(self, repo, manager) -> None:
        with pytest.raises(ValueError, match="interval_seconds must be > 0"):
            WaitingChildrenWatchdog(repo, manager, interval_seconds=0)

    def test_rejects_negative_threshold(self, repo, manager) -> None:
        with pytest.raises(
            ValueError, match="hang_threshold_seconds must be >= 0"
        ):
            WaitingChildrenWatchdog(
                repo, manager, hang_threshold_seconds=-1
            )

    def test_accepts_threshold_zero(self, repo, manager) -> None:
        # ``>= 0`` includes 0 — every non-paused non-terminal child
        # is immediately hung. This is degenerate but the API allows
        # it for tests / explicit "always-on" watchdog deployments.
        w = WaitingChildrenWatchdog(
            repo, manager, hang_threshold_seconds=0
        )
        assert w.hang_threshold_seconds == 0


# ─── Repository helper boundary tests (SQLite) ──────────────────────────────


class TestListHungChildrenBoundary:
    """Boundary exactness for the SQL-side age predicate.

    The brief requires "threshold boundary: child at 59m vs 61m with
    60m threshold (assert boundary exactness — hung means age >
    threshold, decide and assert strictly-greater or greater-equal
    consistently)".

    We commit to STRICTLY GREATER. Documented in the repo helper's
    docstring and pinned here.
    """

    def test_child_at_threshold_minus_one_not_hung(
        self, repo, make_instance
    ) -> None:
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=59 * 60,  # 59 min — strictly below threshold
        )
        hung = repo.list_hung_children_for_parent(parent, 60 * 60)
        assert hung == []

    def test_child_at_threshold_plus_one_is_hung(
        self, repo, make_instance
    ) -> None:
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        child_id = make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=61 * 60,  # 61 min — strictly above threshold
        )
        hung = repo.list_hung_children_for_parent(parent, 60 * 60)
        assert len(hung) == 1
        assert hung[0][0] == child_id
        # Age is computed in seconds; allow ±5s for clock drift between
        # the make_instance insert and the SQL read.
        age = hung[0][1]
        assert (61 * 60) - 5 <= age <= (61 * 60) + 5

    def test_paused_child_excluded_even_when_old(
        self, repo, make_instance
    ) -> None:
        """A paused child is not hung — parent must NOT be nagged."""
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        make_instance(
            status=InstanceStatus.PAUSED,
            parent_id=parent,
            age_seconds=24 * 3600,  # very old
        )
        hung = repo.list_hung_children_for_parent(parent, 3600)
        assert hung == []

    def test_waiting_children_child_excluded_even_when_old(
        self, repo, make_instance
    ) -> None:
        """A child parked in WAITING_CHILDREN is not hung — it is
        blocked on ITS children (waiting by design), and parking
        does not refresh ``last_activity_at`` so the age predicate
        would otherwise misread it as hung. Nudging the grandparent
        to revive/replace it duplicates a still-working subtree —
        the same duplicate-work hazard the paused exclusion avoids,
        one level down (deep-review warning 1)."""
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        make_instance(
            status=InstanceStatus.WAITING_CHILDREN,
            parent_id=parent,
            age_seconds=24 * 3600,  # very old — still not hung
        )
        hung = repo.list_hung_children_for_parent(parent, 3600)
        assert hung == []

    async def test_waiting_children_child_excluded_in_watchdog_run(
        self, repo, manager, make_instance
    ) -> None:
        """End-to-end: a WC-parked child alone does NOT trigger a
        notice — the watchdog stays silent for a legitimately
        waiting subtree."""
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        make_instance(
            status=InstanceStatus.WAITING_CHILDREN,
            parent_id=parent,
            age_seconds=24 * 3600,
        )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        stats = await w.run_once()
        assert stats["notices_enqueued"] == 0
        manager.enqueue_message.assert_not_called()

    def test_completed_child_excluded_even_when_old(
        self, repo, make_instance
    ) -> None:
        """Terminal-status children are excluded — by definition they
        cannot be hung."""
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        make_instance(
            status=InstanceStatus.COMPLETED,
            parent_id=parent,
            age_seconds=24 * 3600,
        )
        hung = repo.list_hung_children_for_parent(parent, 3600)
        assert hung == []

    def test_returns_oldest_first(self, repo, make_instance) -> None:
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        newer = make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=61 * 60,
        )
        older = make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=181 * 60,  # older
        )
        hung = repo.list_hung_children_for_parent(parent, 60 * 60)
        assert [c for c, _ in hung] == [older, newer]


# ─── List waiting-children parents ──────────────────────────────────────────


class TestListWaitingChildrenParents:
    def test_returns_only_waiting_children_instances(
        self, repo, make_instance
    ) -> None:
        waiting = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        make_instance(status=InstanceStatus.RUNNING)
        make_instance(status=InstanceStatus.PAUSED)
        ids = repo.list_waiting_children_parents()
        assert ids == [waiting]

    def test_empty_when_none_waiting(self, repo, make_instance) -> None:
        make_instance(status=InstanceStatus.RUNNING)
        assert repo.list_waiting_children_parents() == []


# ─── Watchdog core: run_once ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestRunOnceHealthyTree:
    """Healthy tree: children active recently → no-op."""

    async def test_no_injection_when_children_recent(
        self, repo, manager, make_instance
    ) -> None:
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=60,  # 1 min — well under 1h threshold
        )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        stats = await w.run_once()
        assert stats == {
            "parents_scanned": 1,
            "parents_skipped_paused": 0,
            "notices_enqueued": 0,
            "errors": 0,
        }
        manager.enqueue_message.assert_not_called()
        assert w.notified_episodes == frozenset()


@pytest.mark.asyncio
class TestRunOnceNoticeAndProvenance:
    """Notice content + provenance on the enqueue (waking) path."""

    async def test_enqueue_message_called_with_source_and_notice(
        self, repo, manager, make_instance
    ) -> None:
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        child_id = make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,  # > 1h
        )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        await w.run_once()

        assert manager.enqueue_message.call_count == 1
        kwargs = manager.enqueue_message.call_args.kwargs
        assert kwargs["instance_id"] == parent
        assert "[system:watchdog]" in kwargs["message"]
        assert child_id[:8] in kwargs["message"]
        # source MUST be the watchdog marker — the durable
        # MessageQueue-row provenance on the enqueue path.
        assert kwargs["source"] == WATCHDOG_SOURCE
        assert kwargs["source"] == "system:watchdog"
        # System priority lane + structured audit metadata.
        assert kwargs["priority"] == 0
        assert kwargs["metadata"]["watchdog_notice"] is True
        assert kwargs["metadata"]["hang_threshold_seconds"] == 3600
        assert kwargs["metadata"]["hung_children"][0]["child_id"] == child_id

    async def test_source_stamped_even_when_multiple_hung(
        self, repo, manager, make_instance
    ) -> None:
        """All children in a single notice share the same source —
        the source is per-ENQUEUE, not per-child."""
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        for _ in range(3):
            make_instance(
                status=InstanceStatus.RUNNING,
                parent_id=parent,
                age_seconds=4000,
            )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        await w.run_once()
        assert manager.enqueue_message.call_count == 1
        kwargs = manager.enqueue_message.call_args.kwargs
        assert kwargs["source"] == WATCHDOG_SOURCE
        assert len(kwargs["metadata"]["hung_children"]) == 3


@pytest.mark.asyncio
class TestRunOncePausedParent:
    """PAUSED parent → skipped, no ``enqueue_message`` call."""

    async def test_paused_parent_skipped_even_with_hung_children(
        self, repo, manager, make_instance
    ) -> None:
        # Parent is PAUSED — would-be hung children present.
        parent = make_instance(status=InstanceStatus.PAUSED)
        make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,
        )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        stats = await w.run_once()
        # Parent not enumerated in the first place (filter is on
        # status == WAITING_CHILDREN).
        assert stats["parents_scanned"] == 0
        manager.enqueue_message.assert_not_called()

    async def test_parent_transitioned_to_paused_between_enum_and_scan(
        self, repo, manager, make_instance, monkeypatch
    ) -> None:
        """Defensive: even if the repo enumerates a now-PAUSED parent
        (TOCTOU window), the watchdog re-checks status and skips.

        Verified by monkey-patching ``repo.get`` to flip status to
        PAUSED on first read, simulating a concurrent pause race
        between enumeration and per-parent scan.
        """
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,
        )

        real_get = repo.get
        call_count = {"value": 0}

        def flipping_get(instance_id: str):
            call_count["value"] += 1
            instance = real_get(instance_id)
            if instance is not None and instance.instance_id == parent:
                # Flip status to PAUSED before the watchdog sees it.
                with repo.engine.begin() as conn:
                    conn.execute(
                        Instance.__table__.update()
                        .where(Instance.instance_id == parent)
                        .values(status=InstanceStatus.PAUSED.value)
                    )
                # Refresh the in-memory row to reflect the flip.
                instance = real_get(instance_id)
            return instance

        monkeypatch.setattr(repo, "get", flipping_get)

        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        stats = await w.run_once()
        assert stats["parents_scanned"] == 1
        assert stats["parents_skipped_paused"] == 1
        manager.enqueue_message.assert_not_called()


@pytest.mark.asyncio
class TestRunOnceCooldown:
    """Anti-spam: one notice per (parent, child, episode)."""

    async def test_second_tick_does_not_renotify_same_episode(
        self, repo, manager, make_instance
    ) -> None:
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,
        )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        # Tick 1: notify.
        stats1 = await w.run_once()
        assert stats1["notices_enqueued"] == 1
        # Tick 2: same episode — no re-notify.
        stats2 = await w.run_once()
        assert stats2["notices_enqueued"] == 0
        # ``enqueue_message`` was called exactly once across both ticks.
        assert manager.enqueue_message.call_count == 1
        # Cooldown set holds the (parent, child) pair.
        assert len(w.notified_episodes) == 1

    async def test_renotifies_after_child_terminates_then_resumes(
        self, repo, manager, make_instance
    ) -> None:
        """Episode reset: child reaches terminal → cooldown clears →
        child returns to non-terminal non-paused → re-notify."""
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        child_id = make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,
        )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        # Tick 1: notify.
        await w.run_once()
        assert manager.enqueue_message.call_count == 1

        # Child completes — episode ends.
        with repo.engine.begin() as conn:
            conn.execute(
                Instance.__table__.update()
                .where(Instance.instance_id == child_id)
                .values(status=InstanceStatus.COMPLETED.value)
            )
        # Tick 2: no hung children → no notify, cooldown clears.
        await w.run_once()
        assert manager.enqueue_message.call_count == 1
        assert w.notified_episodes == frozenset()

        # Child comes back (e.g. revived) and is hung again.
        with repo.engine.begin() as conn:
            conn.execute(
                Instance.__table__.update()
                .where(Instance.instance_id == child_id)
                .values(
                    status=InstanceStatus.RUNNING.value,
                    last_activity_at=(
                        datetime.now(timezone.utc) - timedelta(seconds=4000)
                    ).replace(tzinfo=None),
                )
            )
        # Tick 3: new episode → re-notify.
        await w.run_once()
        assert manager.enqueue_message.call_count == 2

    async def test_parent_leaving_wc_purges_pairs_and_reentry_notifies_fresh(
        self, repo, manager, make_instance
    ) -> None:
        """Parent-side episode boundary (deep-review warning 2).

        Parent exits WAITING_CHILDREN while the child stays hung →
        the (P, C) pair is dropped from the cooldown (the notified
        episode belonged to the previous WC stay). A later WC
        re-entry must be able to notify FRESH — pre-fix, the pair
        stranded in ``_notified`` forever and re-entry got nothing.
        """
        parent = make_instance(
            status=InstanceStatus.WAITING_CHILDREN,
            instance_id="parent-exit",
        )
        make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,
            instance_id="child-still-hung",
        )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        # Tick 1: notify; pair lands in cooldown.
        await w.run_once()
        assert manager.enqueue_message.call_count == 1
        assert w.notified_episodes == frozenset({(parent, "child-still-hung")})

        # Parent leaves WC (e.g. an external message or report woke
        # it) — child stays hung.
        with repo.engine.begin() as conn:
            conn.execute(
                Instance.__table__.update()
                .where(Instance.instance_id == parent)
                .values(status=InstanceStatus.RUNNING.value)
            )
        # Tick 2: parent no longer enumerated → purge drops the pair.
        stats2 = await w.run_once()
        assert stats2["notices_enqueued"] == 0
        assert w.notified_episodes == frozenset()

        # Parent re-enters WC (still waiting on the same hung child).
        with repo.engine.begin() as conn:
            conn.execute(
                Instance.__table__.update()
                .where(Instance.instance_id == parent)
                .values(status=InstanceStatus.WAITING_CHILDREN.value)
            )
        # Tick 3: fresh notice fires — re-entry is a new episode.
        stats3 = await w.run_once()
        assert stats3["notices_enqueued"] == 1
        assert manager.enqueue_message.call_count == 2

    async def test_child_terminal_purges_pair_even_when_parent_scan_errors(
        self, repo, manager, make_instance
    ) -> None:
        """Child-terminal purge is independent of scan success
        (deep-review "also required" clause).

        The child terminates via a path the watchdog's own scan never
        observes (here: the parent's scan raises every tick). The
        pair must still clear so a future episode can re-notify —
        pre-fix only the scan-driven sweep could clear it, and a
        permanently-failing scan would strand the pair forever.
        """
        parent = make_instance(
            status=InstanceStatus.WAITING_CHILDREN,
            instance_id="parent-stuck",
        )
        child_id = make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,
            instance_id="child-recovering",
        )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        await w.run_once()
        assert (parent, child_id) in w.notified_episodes

        # Parent's scan now fails every tick (simulated DB blip).
        def failing_list_hung(*args: Any, **kwargs: Any):
            raise RuntimeError("simulated persistent DB blip")

        repo.list_hung_children_for_parent = failing_list_hung  # type: ignore[method-assign]

        # Child terminates (e.g. terminated by an operator).
        with repo.engine.begin() as conn:
            conn.execute(
                Instance.__table__.update()
                .where(Instance.instance_id == child_id)
                .values(status=InstanceStatus.TERMINATED.value)
            )
        # Tick: scan raises (errors=1) but the child-terminal purge
        # STILL drops the pair — the purge is DB-read-backed and
        # independent of per-parent scan success.
        stats = await w.run_once()
        assert stats["errors"] == 1
        assert w.notified_episodes == frozenset()

    async def test_only_new_hung_children_notified_in_same_tick(
        self, repo, manager, make_instance
    ) -> None:
        """If a parent already has notified children AND a new child
        becomes hung, the next tick injects ONLY for the new child
        and lists it in the notice (so the parent learns the new
        offender without being re-notified about the old one)."""
        parent = make_instance(
            status=InstanceStatus.WAITING_CHILDREN,
            instance_id="parent-old-new",
        )
        old_child = make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,
            instance_id="child-old-zzzz-aaaa",
        )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        await w.run_once()
        assert manager.enqueue_message.call_count == 1
        first_call_content = manager.enqueue_message.call_args.kwargs["message"]
        assert old_child[:8] in first_call_content

        # New child appears, also hung — distinct prefix so the
        # substring check below does not false-match the old child.
        new_child = make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=5000,
            instance_id="child-new-xxxx-bbbb",
        )
        await w.run_once()
        # Tick fires again (new child is new pair), but content
        # only includes the new child (old one is in cooldown).
        assert manager.enqueue_message.call_count == 2
        second_call_content = manager.enqueue_message.call_args.kwargs["message"]
        assert new_child[:8] in second_call_content
        # Old child NOT re-listed in this tick's notice (the
        # old child is in the cooldown set so ``new_pairs``
        # excludes it from the notice body).
        assert old_child[:8] not in second_call_content


@pytest.mark.asyncio
class TestRunOnceErrorIsolation:
    """Per-parent try/except — one bad parent must NOT block others."""

    async def test_one_parent_error_does_not_block_others(
        self, repo, manager, make_instance
    ) -> None:
        # Two WAITING_CHILDREN parents; whichever one's
        # ``list_hung_children_for_parent`` is called for
        # ``failing_parent`` raises. The other parent
        # (``good_parent``) MUST still get a notice.
        good_parent = make_instance(
            status=InstanceStatus.WAITING_CHILDREN,
            instance_id="parent-good",
        )
        make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=good_parent,
            age_seconds=4000,
        )
        failing_parent = make_instance(
            status=InstanceStatus.WAITING_CHILDREN,
            instance_id="parent-failing",
        )
        make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=failing_parent,
            age_seconds=4000,
        )

        # Patch ``list_hung_children_for_parent`` to raise for the
        # failing parent only — deterministic regardless of
        # iteration order.
        original = repo.list_hung_children_for_parent

        def maybe_raise(*args: Any, **kwargs: Any):
            if kwargs.get("parent_id") == failing_parent:
                raise RuntimeError("simulated DB blip")
            return original(*args, **kwargs)

        repo.list_hung_children_for_parent = maybe_raise  # type: ignore[method-assign]

        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        stats = await w.run_once()
        assert stats["parents_scanned"] == 2
        assert stats["errors"] == 1
        # The good parent still got its notice.
        assert manager.enqueue_message.call_count == 1
        # Confirm we notified the good parent, not the failing one.
        assert manager.enqueue_message.call_args.kwargs["instance_id"] == good_parent

    async def test_enumeration_failure_counted_not_raised(
        self, repo, manager, make_instance
    ) -> None:
        """If the top-level enumeration raises, ``run_once`` returns
        a stats dict with ``errors=1`` — it does NOT propagate."""
        repo.list_waiting_children_parents = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("simulated enum failure")
        )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )
        stats = await w.run_once()
        assert stats == {
            "parents_scanned": 0,
            "parents_skipped_paused": 0,
            "notices_enqueued": 0,
            "errors": 1,
        }


@pytest.mark.asyncio
class TestEpisodeEndScanErrorPreservesCooldown:
    """Anti-spam invariant: a transient per-parent scan error MUST
    NOT silently clear the cooldown.

    Regression target: prior behavior treated every parent absent
    from the freshly-computed ``currently_hung_pairs`` as an
    episode-end candidate. A parent whose ``list_hung_children_for_parent``
    raises was therefore wrongly considered "no longer hung" and
    its pairs were silently cleared from ``_notified``. The next
    healthy tick would then re-notify with no real episode change,
    violating the documented anti-spam invariant (one notice per
    parent/child episode). The fix tracks a ``scanned_ok`` set of
    parents whose scan completed cleanly and only allows pair
    clearance for those parents.

    Test scenario (per brief):

    1. Establish cooldown — tick 1 (healthy) injects a notice for
       parent ``P`` / child ``C``.
    2. Tick N — ``list_hung_children_for_parent`` raises
       ``RuntimeError`` for parent ``P`` only. The tick records
       ``errors += 1``, but ``(P, C)`` MUST stay in the cooldown
       set (NOT silently cleared).
    3. Tick N+1 — repo back to healthy. The watchdog MUST NOT
       re-notify: ``enqueue_message`` call count is still 1, and the
       cooldown set still holds ``(P, C)``.
    """

    async def test_transient_scan_error_preserves_cooldown_across_ticks(
        self, repo, manager, make_instance
    ) -> None:
        parent = make_instance(
            status=InstanceStatus.WAITING_CHILDREN,
            instance_id="parent-flaky",
        )
        child_id = make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,
            instance_id="child-flaky",
        )

        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=3600, hang_threshold_seconds=3600
        )

        # Tick 1 (healthy): establishes the cooldown. (P, C) is in
        # the cooldown set; ``enqueue_message`` called once.
        stats1 = await w.run_once()
        assert stats1["notices_enqueued"] == 1
        assert stats1["errors"] == 0
        assert manager.enqueue_message.call_count == 1
        assert (parent, child_id) in w.notified_episodes

        # Inject a transient scan error for parent ``parent`` only.
        # Subsequent calls to ``list_hung_children_for_parent`` with
        # ``parent_id == parent`` raise; calls for any other parent
        # delegate to the original. ``inject_failures`` is a flag so
        # the test can restore the healthy repo at tick N+1.
        original = repo.list_hung_children_for_parent
        state = {"inject_failures": True}

        def flaky_list_hung(*args: Any, **kwargs: Any):
            if (
                state["inject_failures"]
                and kwargs.get("parent_id") == parent
            ):
                raise RuntimeError("simulated transient DB blip")
            return original(*args, **kwargs)

        repo.list_hung_children_for_parent = flaky_list_hung  # type: ignore[method-assign]

        # Tick N (failing): the scan for parent raises and is
        # caught by the per-parent try/except. The cooldown set
        # MUST still hold ``(P, C)`` — the fix's invariant.
        stats_n = await w.run_once()
        assert stats_n["parents_scanned"] == 1
        assert stats_n["errors"] == 1
        assert stats_n["notices_enqueued"] == 0
        # Cooldown preserved — the regression target.
        assert (parent, child_id) in w.notified_episodes, (
            "transient scan error must NOT clear cooldown"
        )
        # And no spurious re-notify on the failing tick.
        assert manager.enqueue_message.call_count == 1

        # Restore the repo to healthy — tick N+1 sees fresh data.
        state["inject_failures"] = False

        # Tick N+1 (healthy): repo is back. The cooldown set
        # still holds ``(P, C)``; ``list_hung_children_for_parent``
        # returns the same hung child. The watchdog MUST NOT
        # re-notify because ``(P, C)`` is still in the set.
        stats_n_plus_1 = await w.run_once()
        assert stats_n_plus_1["parents_scanned"] == 1
        assert stats_n_plus_1["errors"] == 0
        # No new notice — the whole point of the regression guard.
        assert manager.enqueue_message.call_count == 1
        # Cooldown set still populated (episode has not actually ended).
        assert (parent, child_id) in w.notified_episodes


@pytest.mark.asyncio
class TestRunOnceDisabled:
    """Disabled flag → loop returns immediately, no scan."""

    async def test_disabled_returns_zero_stats(
        self, repo, manager, make_instance
    ) -> None:
        # Even with parents + hung children present, a disabled
        # watchdog must NOT call enqueue_message and must NOT touch
        # the repo.
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,
        )
        w = WaitingChildrenWatchdog(
            repo,
            manager,
            enabled=False,
            interval_seconds=3600,
            hang_threshold_seconds=3600,
        )
        stats = await w.run_once()
        assert stats == {
            "parents_scanned": 0,
            "parents_skipped_paused": 0,
            "notices_enqueued": 0,
            "errors": 0,
        }
        manager.enqueue_message.assert_not_called()


# ─── Loop scheduler tests (mocks time, no real sleep) ───────────────────────


@pytest.mark.asyncio
class TestLoopScheduler:
    """Periodic loop: cancellation semantics + tick behavior."""

    async def test_disabled_loop_returns_immediately(
        self, repo, manager
    ) -> None:
        w = WaitingChildrenWatchdog(
            repo, manager, enabled=False, interval_seconds=3600
        )
        # No exception, no waiting — the loop returns immediately
        # when disabled.
        await asyncio.wait_for(
            run_waiting_children_watchdog_loop(w, interval_seconds=3600),
            timeout=1.0,
        )

    async def test_loop_cancellation_is_clean(
        self, repo, manager, make_instance, monkeypatch
    ) -> None:
        """Cancel the task mid-loop → no leaked task, no raise.

        We patch ``asyncio.sleep`` to await a short, cancellable
        future so the test runs in milliseconds instead of waiting
        3600s between ticks.
        """
        parent = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,
        )
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=1, hang_threshold_seconds=3600
        )

        # Replace ``asyncio.sleep`` with a function that awaits a
        # short sleep — preserves cancellability but doesn't sit on
        # a 3600s timer.
        real_sleep = asyncio.sleep

        async def fast_sleep(seconds: float) -> None:
            await real_sleep(min(seconds, 0.01))

        monkeypatch.setattr(
            "daemon.services.waiting_children_watchdog.asyncio.sleep",
            fast_sleep,
        )

        task = asyncio.create_task(
            run_waiting_children_watchdog_loop(w, interval_seconds=1)
        )
        # Let it run a tick or two.
        await real_sleep(0.05)
        # Cancel cleanly.
        task.cancel()
        # Must NOT raise.
        try:
            await task
        except asyncio.CancelledError:
            pass  # expected when cancelled mid-tick (run_once raise)
        except Exception as e:
            pytest.fail(f"loop raised on cancel: {e!r}")
        assert task.done()

    async def test_loop_calls_run_once_periodically(
        self, repo, manager, make_instance, monkeypatch
    ) -> None:
        """Loop ticks: at least one ``run_once`` per sleep interval.

        Patches ``asyncio.sleep`` to drive the loop forward without
        waiting in real time.
        """
        make_instance(status=InstanceStatus.WAITING_CHILDREN)

        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=1, hang_threshold_seconds=3600
        )

        # Count calls to run_once via a wrapper.
        original_run_once = w.run_once
        call_count = {"value": 0}

        async def counting_run_once() -> dict[str, int]:
            call_count["value"] += 1
            result = await original_run_once()
            # Stop the loop after a few ticks.
            if call_count["value"] >= 3:
                raise asyncio.CancelledError("test stop")
            return result

        w.run_once = counting_run_once  # type: ignore[method-assign]

        real_sleep = asyncio.sleep

        async def fast_sleep(seconds: float) -> None:
            await real_sleep(0.01)

        monkeypatch.setattr(
            "daemon.services.waiting_children_watchdog.asyncio.sleep",
            fast_sleep,
        )

        with pytest.raises(asyncio.CancelledError):
            await run_waiting_children_watchdog_loop(
                w, interval_seconds=1
            )

        # Loop drove at least 3 ticks.
        assert call_count["value"] == 3

    async def test_loop_swallows_run_once_exception_and_continues(
        self, repo, manager, make_instance, monkeypatch
    ) -> None:
        """A cycle that raises is logged and the loop CONTINUES on
        the next interval — best-effort, never crashes the daemon."""
        w = WaitingChildrenWatchdog(
            repo, manager, interval_seconds=1, hang_threshold_seconds=3600
        )

        original_run_once = w.run_once
        call_count = {"value": 0}

        async def flaky_run_once() -> dict[str, int]:
            call_count["value"] += 1
            if call_count["value"] == 1:
                raise RuntimeError("simulated cycle failure")
            if call_count["value"] >= 2:
                raise asyncio.CancelledError("test stop")
            return await original_run_once()

        w.run_once = flaky_run_once  # type: ignore[method-assign]

        real_sleep = asyncio.sleep

        async def fast_sleep(seconds: float) -> None:
            await real_sleep(0.01)

        monkeypatch.setattr(
            "daemon.services.waiting_children_watchdog.asyncio.sleep",
            fast_sleep,
        )

        # Must NOT propagate the RuntimeError; the loop must keep
        # ticking. We cancel after the second tick (the
        # CancelledError branch) so the loop exits cleanly.
        with pytest.raises(asyncio.CancelledError):
            await run_waiting_children_watchdog_loop(
                w, interval_seconds=1
            )
        assert call_count["value"] == 2

# ─── Terminal-id helper (cooldown purge backing query) ─────────────────────


class TestListTerminalInstanceIds:
    """``list_terminal_instance_ids`` — the DB read behind the
    child-terminal cooldown purge."""

    def test_returns_only_terminal_subset(self, repo, make_instance) -> None:
        completed = make_instance(status=InstanceStatus.COMPLETED)
        errored = make_instance(status=InstanceStatus.ERROR)
        terminated = make_instance(status=InstanceStatus.TERMINATED)
        failed = make_instance(status=InstanceStatus.FAILED)
        running = make_instance(status=InstanceStatus.RUNNING)
        waiting = make_instance(status=InstanceStatus.WAITING_CHILDREN)
        paused = make_instance(status=InstanceStatus.PAUSED)

        result = repo.list_terminal_instance_ids(
            [completed, errored, terminated, failed, running, waiting, paused]
        )
        assert result == {completed, errored, terminated, failed}

    def test_missing_ids_absent_from_result(self, repo) -> None:
        """A missing row cannot be concluded terminal — the purge
        keeps cooldown entries for ids it cannot see."""
        assert repo.list_terminal_instance_ids(["no-such-id"]) == set()

    def test_empty_input_short_circuits(self, repo) -> None:
        assert repo.list_terminal_instance_ids([]) == set()


# ─── ACCEPTANCE: pure-hang wake via the real enqueue path ──────────────────


class TestWakeDeliveryAcceptance:
    """The test that would have caught the v1 defect.

    Scenario (deep-review "MANDATORY acceptance test"): ONE hung
    child, NO sibling termination, NO external message. The notice
    must reach the parked parent through the REAL wake path —
    ``manager.enqueue_message`` → ``_prepare_enqueued_message`` —
    not through a mock of the watchdog's own collaborators.

    Wiring: the watchdog's ``manager`` collaborator is the REAL
    ``InstanceMessagingService`` bound to the test SQLite engine
    via a minimal manager stub (engine + write guard + worker-pool
    recorder). Everything on the delivery path that touches durable
    state — the MessageQueue + Task + Event trio, the
    WAITING_CHILDREN → RUNNING flip, the ``last_activity_at`` /
    ``version`` bump, the worker-pool ``notify_work`` wake — runs
    real production code. The only fakes are the RAM-side manager
    attributes the enqueue path reads (guard/hub/pool), none of
    which decide whether the parent is woken.
    """

    @staticmethod
    def _build_real_messaging(engine):
        """Real ``InstanceMessagingService`` on a minimal manager stub."""

        class _WorkerPoolRecorder:
            notify_calls: list[bool] = []

            def notify_work(self) -> None:
                self.notify_calls.append(True)

        class _NotShuttingDown:
            is_shutting_down = False

        stub_manager = MagicMock()
        stub_manager.engine = engine
        stub_manager.write_guard = MagicMock()
        stub_manager._deferred_question_pause = {}
        stub_manager._job_queue_service = MagicMock()
        stub_manager._live_hub = MagicMock()
        stub_manager._live_hub.stream_status_change = AsyncMock()
        pool = _WorkerPoolRecorder()
        stub_manager._worker_pool = pool
        service = InstanceMessagingService(
            manager=stub_manager,
            cancellation_service=_NotShuttingDown(),
        )
        return service, stub_manager, pool

    async def test_pure_hang_notice_wakes_parked_parent(
        self, repo, engine, make_instance
    ) -> None:
        parent = make_instance(
            status=InstanceStatus.WAITING_CHILDREN,
            instance_id="parent-parked",
        )
        make_instance(
            status=InstanceStatus.RUNNING,  # hung: never terminates
            parent_id=parent,
            age_seconds=4000,  # > 1h threshold
            instance_id="child-wedged",
        )
        service, stub_manager, pool = self._build_real_messaging(engine)

        w = WaitingChildrenWatchdog(
            repo, service, interval_seconds=3600, hang_threshold_seconds=3600
        )
        stats = await w.run_once()

        assert stats["notices_enqueued"] == 1
        assert stats["errors"] == 0

        # (1) The parent is OBSERVABLY woken: WAITING_CHILDREN →
        #     RUNNING in the DB (the flip only the enqueue path
        #     performs — set_injection never touches status).
        parent_row = repo.get(parent)
        assert parent_row is not None
        assert parent_row.status == InstanceStatus.RUNNING.value

        # (2) The notice landed in the parent's durable message
        #     stream: a READY MessageQueue row carrying the notice
        #     and the provenance that survives the enqueue path.
        with repo.engine.connect() as conn:
            # NOTE: MessageQueue.message_metadata maps to the DB
            # column ``metadata``; the .label() re-keys the result
            # row back to the attribute name for clean access.
            msg_row = conn.execute(
                select(
                    MessageQueue.message_id,
                    MessageQueue.source,
                    MessageQueue.content,
                    MessageQueue.message_metadata.label("message_metadata"),
                    MessageQueue.priority,
                ).where(MessageQueue.instance_id == parent)
            ).one()
            task_row = conn.execute(
                select(
                    Task.status,
                    Task.instance_id,
                    Task.task_type,
                ).where(Task.message_id == msg_row.message_id)
            ).one()
        assert msg_row.source == "system:watchdog"
        assert "[system:watchdog]" in msg_row.content
        assert "child-wedged"[:8] in msg_row.content
        assert msg_row.message_metadata["watchdog_notice"] is True
        assert msg_row.message_metadata["hung_children"][0]["child_id"] == (
            "child-wedged"
        )
        assert msg_row.priority == 0

        # (3) A dispatchable Task claims the message — the row a
        #     WorkerPool worker claims to run the parent's turn.
        assert task_row.status == TaskStatus.PENDING.value
        assert task_row.instance_id == parent

        # (4) The worker pool was actually notified (the wake signal
        #     that ends the quiesced park).
        assert pool.notify_calls == [True]

        # (5) Regression guard: nothing was parked in the RAM
        #     injection FIFO (the v1 primitive that never woke).
        stub_manager.set_injection.assert_not_called()

    async def test_pure_hang_second_tick_no_duplicate_notice(
        self, repo, engine, make_instance
    ) -> None:
        """Cooldown holds on the waking path: the second tick does
        not enqueue a second notice for the same hung episode."""
        parent = make_instance(
            status=InstanceStatus.WAITING_CHILDREN,
            instance_id="parent-parked-2",
        )
        make_instance(
            status=InstanceStatus.RUNNING,
            parent_id=parent,
            age_seconds=4000,
            instance_id="child-wedged-2",
        )
        service, _stub, pool = self._build_real_messaging(engine)
        w = WaitingChildrenWatchdog(
            repo, service, interval_seconds=3600, hang_threshold_seconds=3600
        )
        # Tick 1 wakes the parent (WC → RUNNING). Tick 2 therefore
        # does not even enumerate it — and MUST NOT enqueue again.
        await w.run_once()
        first_wakes = len(pool.notify_calls)
        assert first_wakes == 1
        stats2 = await w.run_once()
        assert stats2["notices_enqueued"] == 0
        assert len(pool.notify_calls) == 1


# ─── Dialect parity: PG EXTRACT(EPOCH) vs SQLite julianday ─────────────────


class TestHungChildrenSqlDialectParity:
    """Compile-check / render-verify BOTH dialect branches of the
    hang-detection SQL (deep-review warning 4).

    The suite's engines are SQLite-only while PostgreSQL is the
    production primary — the PG branch would otherwise ship
    completely unexecuted. Pattern: dialect-specific SQL rendered
    through the dialect's compiler (mirrors the readiness.py
    constants split at ``daemon/services/readiness.py:100-107``).
    """

    def _shared_predicate_asserts(self, rendered: str) -> None:
        for terminal in ("completed", "error", "terminated", "failed"):
            assert f"'{terminal}'" in rendered
        assert "status != 'paused'" in rendered
        assert "status != 'waiting_children'" in rendered
        assert "last_activity_at IS NOT NULL" in rendered
        # Bind placeholders survive compilation under the target
        # dialect's paramstyle (named ``:x`` before compilation,
        # ``%(x)s`` for psycopg, ``?`` for SQLite qmark).
        assert (
            ":parent_id" in rendered
            or "%(parent_id)s" in rendered
            or "?" in rendered
        )
        assert (
            ":threshold_seconds" in rendered
            or "%(threshold_seconds)s" in rendered
            or "?" in rendered
        )

    def test_postgres_branch_compiles_and_renders_extract_epoch(self):
        sql = SQLModelInstanceRepository._build_hung_children_sql("postgresql")
        rendered = str(sql.compile(dialect=postgresql.dialect()))
        assert "EXTRACT(EPOCH FROM (now() - last_activity_at))" in rendered
        assert "julianday" not in rendered
        # Strictly-greater-than age predicate against the bind.
        # psycopg paramstyle renders named binds as ``%(name)s``.
        assert (
            "EXTRACT(EPOCH FROM (now() - last_activity_at))"
            " > %(threshold_seconds)s" in rendered
        )
        assert "%(parent_id)s" in rendered
        self._shared_predicate_asserts(rendered)

    def test_sqlite_branch_compiles_and_renders_julianday(self):
        sql = SQLModelInstanceRepository._build_hung_children_sql("sqlite")
        rendered = str(sql.compile(dialect=sqlite_dialect.dialect()))
        assert "(julianday('now') - julianday(last_activity_at)) * 86400" in rendered
        assert "EXTRACT" not in rendered
        # qmark paramstyle renders binds positionally as ``?``.
        assert (
            "(julianday('now') - julianday(last_activity_at))"
            " * 86400 > ?" in rendered
        )
        self._shared_predicate_asserts(rendered)

    def test_unknown_dialect_falls_back_to_sqlite_branch(self):
        """Anything that is not PostgreSQL renders the SQLite
        expression — the repository's production switch is
        ``engine.dialect.name == 'postgresql'``."""
        sql = SQLModelInstanceRepository._build_hung_children_sql("mysql")
        rendered = str(sql.compile(dialect=sqlite_dialect.dialect()))
        assert "julianday" in rendered
        assert "EXTRACT" not in rendered
