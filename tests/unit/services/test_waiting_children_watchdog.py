"""Tests for the WAITING_CHILDREN hang watchdog (issue #8).

Covers the watchdog contract end-to-end:

* Threshold boundary exactness — child at ``threshold-1`` seconds is
  NOT hung, child at ``threshold+epsilon`` IS hung (the repo helper
  uses ``age > threshold``).
* Healthy tree (children active recently) → no-op, no injection.
* PAUSED parent → skipped, ``set_injection`` NOT called.
* Notice content + provenance — the ``source`` kwarg IS stamped onto
  the injected message.
* Anti-spam — once per (parent, child, episode); episode reset when
  the child reaches terminal OR becomes paused.
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

REGRESSION: the watchdog's notice flows through ``set_injection`` to
the graph drain site where ``_ensure_tool_result_pairing`` guards
against poisoned tool-call tails. We do NOT duplicate that coverage
here — ``tests/unit/graph/test_injection_tool_pairing.py`` is the
authoritative guard for the drain. The watchdog itself only writes
to ``set_injection``; the regression is pinned via the targeted
test pair specified in the brief.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
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
def manager() -> MagicMock:
    """A mock manager that records ``set_injection`` calls.

    Uses ``MagicMock`` (not ``AsyncMock``) because ``set_injection``
    is sync in the production manager — the drain site is in the
    asyncio LangGraph node, but the FIFO append itself is sync.
    """
    m = MagicMock()
    m.set_injection = MagicMock()
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
            "notices_injected": 0,
            "errors": 0,
        }
        manager.set_injection.assert_not_called()
        assert w.notified_episodes == frozenset()


@pytest.mark.asyncio
class TestRunOnceNoticeAndProvenance:
    """Notice content + provenance."""

    async def test_set_injection_called_with_source_and_notice(
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

        assert manager.set_injection.call_count == 1
        kwargs = manager.set_injection.call_args.kwargs
        positional = manager.set_injection.call_args.args
        # First positional is parent_id; second is content; kwarg source.
        assert positional[0] == parent
        assert "[system:watchdog]" in positional[1]
        assert child_id[:8] in positional[1]
        # source MUST be the watchdog marker.
        assert kwargs.get("source") == WATCHDOG_SOURCE
        assert kwargs.get("source") == "system:watchdog"

    async def test_source_stamped_even_when_multiple_hung(
        self, repo, manager, make_instance
    ) -> None:
        """All children in a single notice share the same source —
        the source is per-INJECTION, not per-child."""
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
        assert manager.set_injection.call_count == 1
        kwargs = manager.set_injection.call_args.kwargs
        assert kwargs.get("source") == WATCHDOG_SOURCE


@pytest.mark.asyncio
class TestRunOncePausedParent:
    """PAUSED parent → skipped, no ``set_injection`` call."""

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
        manager.set_injection.assert_not_called()

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
        manager.set_injection.assert_not_called()


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
        assert stats1["notices_injected"] == 1
        # Tick 2: same episode — no re-notify.
        stats2 = await w.run_once()
        assert stats2["notices_injected"] == 0
        # ``set_injection`` was called exactly once across both ticks.
        assert manager.set_injection.call_count == 1
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
        assert manager.set_injection.call_count == 1

        # Child completes — episode ends.
        with repo.engine.begin() as conn:
            conn.execute(
                Instance.__table__.update()
                .where(Instance.instance_id == child_id)
                .values(status=InstanceStatus.COMPLETED.value)
            )
        # Tick 2: no hung children → no notify, cooldown clears.
        await w.run_once()
        assert manager.set_injection.call_count == 1
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
        assert manager.set_injection.call_count == 2

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
        assert manager.set_injection.call_count == 1
        first_call_content = manager.set_injection.call_args.args[1]
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
        assert manager.set_injection.call_count == 2
        second_call_content = manager.set_injection.call_args.args[1]
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
        # The good parent still got an injection.
        assert manager.set_injection.call_count == 1
        # Confirm we notified the good parent, not the failing one.
        assert manager.set_injection.call_args.args[0] == good_parent

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
            "notices_injected": 0,
            "errors": 1,
        }


@pytest.mark.asyncio
class TestRunOnceDisabled:
    """Disabled flag → loop returns immediately, no scan."""

    async def test_disabled_returns_zero_stats(
        self, repo, manager, make_instance
    ) -> None:
        # Even with parents + hung children present, a disabled
        # watchdog must NOT call set_injection and must NOT touch
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
            "notices_injected": 0,
            "errors": 0,
        }
        manager.set_injection.assert_not_called()


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


# ─── Async manager fixture (for ad-hoc scenarios) ──────────────────────────


@pytest.fixture
def async_manager() -> Iterator[AsyncMock]:
    """An ``AsyncMock`` manager — handy for forward-compat tests if
    a future refactor makes ``set_injection`` async. Currently
    unused by the test bodies but kept for documentation."""
    am = AsyncMock()
    yield am