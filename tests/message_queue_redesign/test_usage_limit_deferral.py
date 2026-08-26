"""Usage-limit deferral path: repo parameterization (W2) + stale
recovery anchor-gated bypass and post-retry fixes (W8, review rev3
§2.1/§2.2).

docs/plans/usage-limit-deferral-path.md acceptance tests 2, 7b, 8.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from daemon.repositories.task.models import TaskStatus
from daemon.services.stale_task_recovery import StaleTaskRecovery
from daemon.services.usage_limit_schedule import (
    USAGE_LIMIT_FIRST_SEEN_METADATA_KEY,
    next_usage_limit_retry_at,
)
from tests.helpers.fake_instance_repo import FakeInstanceMetadataRepo
from tests.message_queue_redesign.test_stale_recovery_v2 import (
    MockEventRepository,
    MockMessageRepository,
    create_stale_running_task,
)

UTC = timezone.utc


def _make_recovery(repository, message_repo=None, permanent_cb=None, **kwargs):
    return StaleTaskRecovery(
        task_repository=repository,
        message_repository=message_repo or MockMessageRepository(),
        threshold_minutes=15,
        cancel_grace_seconds=0,
        max_retries=3,
        on_task_permanently_failed=permanent_cb,
        **kwargs,
    )


# ============================================================================
# W2 — schedule_retry / force_cancel_and_schedule_retry keyword params
# ============================================================================


class TestScheduleRetryUsageLimitParams:
    """Keyword-only params: next_retry_at override + budget bypass."""

    def test_next_retry_at_override_stamps_caller_time(self, engine, repository):
        from .test_task_retry_repository import create_task_with_status

        parent = create_task_with_status(engine, status=TaskStatus.RUNNING.value)
        scheduled_at = datetime.now(UTC) + timedelta(seconds=1234)

        retry = repository.schedule_retry(
            task_id=parent.id,
            max_retries=3,
            next_retry_at=scheduled_at,
        )

        assert retry is not None
        assert retry.retry_count == 1
        parsed = datetime.fromisoformat(retry.next_retry_at.replace("Z", "+00:00"))
        assert parsed == scheduled_at

    def test_bypass_retry_budget_schedules_past_max(self, engine, repository):
        """4th+ deferral still schedules: bypass drops ONLY the budget
        term (acceptance test 2 — max_retries NOT consumed)."""
        from .test_task_retry_repository import create_task_with_status

        parent = create_task_with_status(
            engine,
            status=TaskStatus.RUNNING.value,
            retry_count=26,  # bypass-grown episode count, far past max
        )

        retry = repository.schedule_retry(
            task_id=parent.id,
            max_retries=3,
            next_retry_at=datetime.now(UTC) + timedelta(seconds=180),
            bypass_retry_budget=True,
        )

        assert retry is not None
        assert retry.retry_count == 27  # still increments (observability)
        assert retry.status == TaskStatus.PENDING.value

    def test_bypass_keeps_double_retry_guard(self, engine, repository):
        """Concurrency safety is not negotiable: retry_scheduled=True
        still returns None under bypass."""
        from .test_task_retry_repository import create_task_with_status

        parent = create_task_with_status(
            engine,
            status=TaskStatus.RUNNING.value,
            retry_count=26,
            retry_scheduled=True,
        )

        assert (
            repository.schedule_retry(
                task_id=parent.id,
                max_retries=3,
                bypass_retry_budget=True,
            )
            is None
        )

    def test_defaults_golden_path_budget_still_binds(self, engine, repository):
        """Default args → byte-identical: retry_count >= max_retries
        still returns None (acceptance test 8)."""
        from .test_task_retry_repository import create_task_with_status

        parent = create_task_with_status(
            engine,
            status=TaskStatus.RUNNING.value,
            retry_count=3,
        )

        assert repository.schedule_retry(task_id=parent.id, max_retries=3) is None

    def test_claim_gate_free_for_bypass_grown_counts(self, engine, repository):
        """Bypass-grown retry_count does not block the claim path
        (claim eligibility is next_retry_at-based only)."""
        from .test_task_retry_repository import create_task_with_status

        task = create_task_with_status(
            engine,
            status=TaskStatus.PENDING.value,
            instance_id="claim-instance",
            message_id="claim-msg",
            retry_count=26,
            next_retry_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        claimed = repository.claim_pending_task(worker_id="w-usage")
        assert claimed is not None
        assert claimed.id == task.id


class TestForceCancelUsageLimitParams:
    """Same parameter contract on the force-cancel variant."""

    def test_bypass_and_schedule_override(self, engine, repository):
        from .test_task_retry_repository import create_task_with_status

        parent = create_task_with_status(
            engine,
            status=TaskStatus.RUNNING.value,
            retry_count=26,
        )
        scheduled_at = datetime.now(UTC) + timedelta(seconds=300)

        retry = repository.force_cancel_and_schedule_retry(
            task_id=parent.id,
            max_retries=3,
            reason="Startup recovery: worker crash",
            next_retry_at=scheduled_at,
            bypass_retry_budget=True,
        )

        assert retry is not None
        parsed = datetime.fromisoformat(retry.next_retry_at.replace("Z", "+00:00"))
        assert parsed == scheduled_at
        # Parent cancelled atomically with the child insert.
        parent_after = repository.get(parent.id)
        assert parent_after.status == TaskStatus.CANCELLED.value

    def test_defaults_budget_still_binds(self, engine, repository):
        from .test_task_retry_repository import create_task_with_status

        parent = create_task_with_status(
            engine,
            status=TaskStatus.RUNNING.value,
            retry_count=3,
        )

        assert (
            repository.force_cancel_and_schedule_retry(
                task_id=parent.id,
                max_retries=3,
                reason="test",
            )
            is None
        )


# ============================================================================
# W8 — stale recovery: anchor-gated bypass + post-retry fixes
# ============================================================================


class TestGraceSweepEpisodeRecovery:
    """Acceptance test 7b — crash mid-episode (grace sweep)."""

    def test_live_anchor_bypass_and_episode_schedule(self, repository):
        first_seen = datetime.now(UTC) - timedelta(seconds=60)
        anchor_repo = FakeInstanceMetadataRepo(
            {USAGE_LIMIT_FIRST_SEEN_METADATA_KEY: first_seen.isoformat()}
        )
        instance_manager = SimpleNamespace(_instance_repository=anchor_repo)

        task = create_stale_running_task(
            repository,
            instance_id="episode-inst",
            message_id="episode-msg",
            age_minutes=20,
            retry_count=26,  # bypass-grown count: default gate would fail it
        )
        message_repo = MockMessageRepository()
        permanent_cb = Mock()
        recovery = _make_recovery(
            repository,
            message_repo=message_repo,
            permanent_cb=permanent_cb,
            instance_manager=instance_manager,
            usage_limit_retry_jitter_fraction=0.0,
        )

        recovered = recovery.recover_stale_tasks()

        assert recovered == 1
        # Retry child created WITH the bypass…
        children = repository.get_retry_chain("episode-inst", "episode-msg")
        child = [t for t in children if t.id != task.id][0]
        assert child.retry_count == 27
        # …waking on the W5 episode slot, NOT the 3600s backoff cap:
        # elapsed=60 → slot cumsum 180 → wake = first_seen + 180.
        parsed = datetime.fromisoformat(child.next_retry_at.replace("Z", "+00:00"))
        expected = next_usage_limit_retry_at(
            first_seen, datetime.now(UTC), jitter_fraction=0.0
        )
        assert parsed == expected
        # §2.2 — message status intact under the live recovery child.
        assert message_repo.failed_messages == []
        # §2.1-adjacent — no permanent-failure report.
        permanent_cb.assert_not_called()

    def test_no_anchor_byte_identical_default_budget(self, repository):
        """Non-episode stale task: bypass-grown count WITHOUT an anchor
        still permanent-fails through the default gate (the bypass
        cannot over-reach)."""
        task = create_stale_running_task(
            repository,
            instance_id="plain-inst",
            message_id="plain-msg",
            age_minutes=20,
            retry_count=3,  # at budget
        )
        recovery = _make_recovery(repository)

        recovered = recovery.recover_stale_tasks()

        assert recovered == 1
        assert repository.get(task.id).status == TaskStatus.FAILED.value

    def test_successful_timeout_recovery_message_survives(self, repository):
        """§2.2 TIMEOUT-side effect: a successful grace-sweep recovery
        (no anchor, retry child created) must NOT fail the inherited
        message anymore."""
        task = create_stale_running_task(
            repository,
            instance_id="timeout-inst",
            message_id="timeout-msg",
            age_minutes=20,
        )
        message_repo = MockMessageRepository()
        recovery = _make_recovery(repository, message_repo=message_repo)

        recovery.recover_stale_tasks()

        assert message_repo.failed_messages == []

    def test_permanent_fail_path_still_fails_message_and_reports(self, repository):
        """Terminal recoveries keep the message-fail + exactly-once
        report (gated on the fail_task outcome)."""
        task = create_stale_running_task(
            repository,
            instance_id="terminal-inst",
            message_id="terminal-msg",
            age_minutes=20,
            retry_count=3,
        )
        message_repo = MockMessageRepository()
        permanent_cb = Mock()
        recovery = _make_recovery(
            repository,
            message_repo=message_repo,
            permanent_cb=permanent_cb,
        )

        recovery.recover_stale_tasks()

        assert repository.get(task.id).status == TaskStatus.FAILED.value
        assert len(message_repo.failed_messages) == 1
        assert message_repo.failed_messages[0]["message_id"] == "terminal-msg"
        permanent_cb.assert_called_once()


class TestStartupRecoveryPostRetryFix:
    """§2.1 — the misindented Phase A notify (review rev3 §2.1)."""

    def test_successful_retry_no_permanent_failure_report(self, repository):
        """THE fix: startup Phase A with a successfully-created retry
        child emits NO `_on_task_permanently_failed` (previously fired
        on every recovery — spurious child-kill via
        `_send_error_report`)."""
        task = create_stale_running_task(
            repository,
            instance_id="startup-ok",
            message_id="startup-ok-msg",
            age_minutes=20,
        )
        permanent_cb = Mock()
        recovery = _make_recovery(repository, permanent_cb=permanent_cb)

        recovered = recovery.recover_on_startup()

        assert recovered == 1
        children = repository.get_retry_chain("startup-ok", "startup-ok-msg")
        assert any(t.id != task.id for t in children)
        permanent_cb.assert_not_called()

    def test_episode_startup_recovery_bypass_no_report(self, repository):
        """Test 7b startup variant: crash mid-episode → anchor-gated
        recovery child created → NO permanent-failure report."""
        first_seen = datetime.now(UTC) - timedelta(seconds=60)
        anchor_repo = FakeInstanceMetadataRepo(
            {USAGE_LIMIT_FIRST_SEEN_METADATA_KEY: first_seen.isoformat()}
        )
        instance_manager = SimpleNamespace(_instance_repository=anchor_repo)

        task = create_stale_running_task(
            repository,
            instance_id="startup-episode",
            message_id="startup-episode-msg",
            age_minutes=20,
            retry_count=26,
        )
        permanent_cb = Mock()
        recovery = _make_recovery(
            repository,
            permanent_cb=permanent_cb,
            instance_manager=instance_manager,
            usage_limit_retry_jitter_fraction=0.0,
        )

        recovery.recover_on_startup()

        children = repository.get_retry_chain(
            "startup-episode", "startup-episode-msg"
        )
        child = [t for t in children if t.id != task.id][0]
        assert child.retry_count == 27
        parsed = datetime.fromisoformat(child.next_retry_at.replace("Z", "+00:00"))
        expected = next_usage_limit_retry_at(
            first_seen, datetime.now(UTC), jitter_fraction=0.0
        )
        assert parsed == expected
        permanent_cb.assert_not_called()

    def test_permanent_fail_reports_exactly_once(self, repository):
        """Budget exhausted (no anchor): permanent fail → callback
        exactly once, watcher notify gated on the fail_task outcome."""
        task = create_stale_running_task(
            repository,
            instance_id="startup-fail",
            message_id="startup-fail-msg",
            age_minutes=20,
            retry_count=3,
        )
        permanent_cb = Mock()
        recovery = _make_recovery(repository, permanent_cb=permanent_cb)

        recovery.recover_on_startup()

        assert repository.get(task.id).status == TaskStatus.FAILED.value
        permanent_cb.assert_called_once()

    def test_past_deadline_anchor_not_live_default_path(self, repository):
        """A stale (past-window) anchor is NOT a live episode — the
        default-budget path applies; budget-exhausted → permanent fail
        (the interrupted terminal composition's correct outcome)."""
        old = (datetime.now(UTC) - timedelta(seconds=21601)).isoformat()
        anchor_repo = FakeInstanceMetadataRepo(
            {USAGE_LIMIT_FIRST_SEEN_METADATA_KEY: old}
        )
        instance_manager = SimpleNamespace(_instance_repository=anchor_repo)

        task = create_stale_running_task(
            repository,
            instance_id="stale-anchor",
            message_id="stale-anchor-msg",
            age_minutes=20,
            retry_count=3,
        )
        recovery = _make_recovery(
            repository,
            instance_manager=instance_manager,
        )

        recovery.recover_on_startup()

        assert repository.get(task.id).status == TaskStatus.FAILED.value
