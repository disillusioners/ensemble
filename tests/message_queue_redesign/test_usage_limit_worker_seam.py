"""Usage-limit deferral path — worker seam (W4), task-processor
carve-out (W3), anchor clear on success (W6).

docs/plans/usage-limit-deferral-path.md acceptance tests 2, 5, 6 and
the §7 worker-seam / handler-robustness cases.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.llm_error_classifier import UsageLimitError
from daemon.services.task_processor import ProcessMessageProcessor
from daemon.services.usage_limit_schedule import (
    USAGE_LIMIT_FIRST_SEEN_METADATA_KEY,
)
from tests.helpers.fake_instance_repo import (
    FakeInstanceMetadataRepo,
)

UTC = timezone.utc


def _make_worker(anchor_repo, retry_retval, fail_retval=None):
    """Direct-construction Worker with mocks at the seam boundaries."""
    from daemon.services.worker_pool import Worker

    task_repo = MagicMock()
    task_repo.schedule_retry = MagicMock(return_value=retry_retval)
    task_repo.fail_task = MagicMock(return_value=fail_retval)
    manager = MagicMock()
    manager._instance_repository = anchor_repo
    task_processor = MagicMock()
    task_processor._task_repo = task_repo
    task_processor._manager = manager
    worker_pool = MagicMock()

    worker = Worker(
        worker_id="w-test",
        task_processor=task_processor,
        worker_pool=worker_pool,
        usage_limit_retry_jitter_fraction=0.0,
    )
    worker._work_resolver = object()  # enable _schedule_work_notification
    worker._watcher_repo = object()
    return worker, task_repo, manager


def _task(task_id=1, instance_id="inst-ep", message_id="msg-ep"):
    return SimpleNamespace(
        id=task_id,
        instance_id=instance_id,
        message_id=message_id,
        work_id="work-ep",
        retry_count=0,
        task_type="process_message",
    )


def _err():
    return UsageLimitError(ValueError("Token Plan usage limit reached (2056)"))


# ============================================================================
# W4 — in-window deferral
# ============================================================================


class TestInWindowDeferral:
    def test_fresh_anchor_defers_with_schedule_and_bypass(self):
        """First sighting: anchor stamped, retry scheduled with the W5
        slot + bypass, bus watchers released, NO failure lane."""
        anchor_repo = FakeInstanceMetadataRepo()
        retry_child = SimpleNamespace(id=2, retry_count=1)
        worker, task_repo, _ = _make_worker(anchor_repo, retry_child)

        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async_no_wait"
        ) as bridge:
            worker._handle_usage_limit(_task(), _err())

        # Anchor written BEFORE branching (set-once).
        stamp = anchor_repo._metadata[USAGE_LIMIT_FIRST_SEEN_METADATA_KEY]
        assert datetime.fromisoformat(stamp).tzinfo is not None

        # schedule_retry: bypass + derived slot (first slot: +180s
        # ± nothing — jitter disabled).
        kwargs = task_repo.schedule_retry.call_args.kwargs
        assert kwargs["bypass_retry_budget"] is True
        wake = kwargs["next_retry_at"]
        assert timedelta(seconds=150) < (wake - datetime.now(UTC)) < timedelta(
            seconds=190
        )
        # Bus-watcher release fired (parent + child ids).
        assert bridge.call_count == 1
        # No terminal composition.
        task_repo.fail_task.assert_not_called()
        manager = worker._task_processor._manager
        manager._send_error_report.assert_not_called()

    def test_persisted_anchor_used_not_reStamped(self):
        """Set-once: an existing anchor is read, not overwritten, and
        the deadline derives from the PERSISTED timestamp."""
        first_seen = datetime.now(UTC) - timedelta(seconds=60)
        anchor_repo = FakeInstanceMetadataRepo(
            {USAGE_LIMIT_FIRST_SEEN_METADATA_KEY: first_seen.isoformat()}
        )
        worker, task_repo, _ = _make_worker(anchor_repo, SimpleNamespace(id=9, retry_count=1))

        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async_no_wait"
        ):
            worker._handle_usage_limit(_task(), _err())

        # Anchor value unchanged.
        assert anchor_repo._metadata[USAGE_LIMIT_FIRST_SEEN_METADATA_KEY] == (
            first_seen.isoformat()
        )
        # Wake derived from first_seen: slot 180 → ~120s from now.
        wake = task_repo.schedule_retry.call_args.kwargs["next_retry_at"]
        expected_lower = datetime.now(UTC) + timedelta(seconds=110)
        expected_upper = datetime.now(UTC) + timedelta(seconds=130)
        assert expected_lower < wake < expected_upper

    def test_gate_closed_silent_no_terminal(self):
        """schedule_retry → None (W8 recovery child won the gate, or an
        operator cancel): log + return — NO parent notify, NO watcher
        notify, NO fail_task (reporting would zombie-kill the episode)."""
        anchor_repo = FakeInstanceMetadataRepo()
        worker, task_repo, manager = _make_worker(anchor_repo, None)

        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async_no_wait"
        ) as bridge:
            worker._handle_usage_limit(_task(), _err())  # must not raise

        task_repo.fail_task.assert_not_called()
        manager._send_error_report.assert_not_called()
        bridge.assert_not_called()


# ============================================================================
# W4.2 — past-deadline terminal (race-gated self-composed composition)
# ============================================================================


class TestTerminalComposition:
    def _expired_anchor(self):
        first_seen = datetime.now(UTC) - timedelta(seconds=7 * 3600)
        return FakeInstanceMetadataRepo(
            {USAGE_LIMIT_FIRST_SEEN_METADATA_KEY: first_seen.isoformat()}
        )

    def test_race_won_composes_exactly_once(self):
        """fail_task WON → ONE parent notify ('usage_limit_deadline'),
        watcher notify, anchor cleared (acceptance test 5)."""
        anchor_repo = self._expired_anchor()
        failed_task = SimpleNamespace(id=1, work_id="work-ep")
        worker, task_repo, manager = _make_worker(
            anchor_repo, None, fail_retval=failed_task
        )

        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async_no_wait"
        ) as bridge:
            worker._handle_usage_limit(_task(), _err())

        # The ONE parent report — self-composed.
        task_repo.fail_task.assert_called_once()
        assert "usage_limit_deadline" in task_repo.fail_task.call_args.args[1]
        manager._send_error_report.assert_called_once()
        report_kwargs = manager._send_error_report.call_args.kwargs
        assert report_kwargs["error_type"] == "usage_limit_deadline"
        assert report_kwargs["message_id"] == "msg-ep"
        # Watcher notify fired (bridge carries both the parent-report
        # coroutine and the notify_work_watchers coroutine).
        assert bridge.call_count == 2
        # Anchor cleared — terminal ENDS the episode.
        assert USAGE_LIMIT_FIRST_SEEN_METADATA_KEY not in anchor_repo._metadata

    def test_race_lost_silent_and_anchor_kept(self):
        """fail_task → None (race lost / gate closed with live episode):
        NO parent notify, NO watcher notify, ONE log line, anchor kept."""
        anchor_repo = self._expired_anchor()
        worker, task_repo, manager = _make_worker(anchor_repo, None, fail_retval=None)

        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async_no_wait"
        ) as bridge:
            worker._handle_usage_limit(_task(), _err())  # must not raise

        task_repo.fail_task.assert_called_once()
        manager._send_error_report.assert_not_called()
        bridge.assert_not_called()
        # Anchor NOT cleared — another actor owns the episode's fate.
        assert USAGE_LIMIT_FIRST_SEEN_METADATA_KEY in anchor_repo._metadata


# ============================================================================
# Handler robustness (review rev2 §3.3)
# ============================================================================


class TestHandlerRobustness:
    def test_anchor_io_failure_soft_fails_fresh_window(self):
        """Anchor read AND write both fail → degenerate first_seen=now;
        the in-window defer still schedules; the handler NEVER raises
        out of the except block."""
        anchor_repo = FakeInstanceMetadataRepo(fail=True)
        worker, task_repo, _ = _make_worker(anchor_repo, SimpleNamespace(id=3, retry_count=1))

        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async_no_wait"
        ):
            worker._handle_usage_limit(_task(), _err())  # no raise

        assert task_repo.schedule_retry.called

    def test_handler_body_failure_never_raises(self):
        """Even a total policy-body failure (schedule_retry explodes)
        is swallowed — it cannot escape the except UsageLimitError
        block into an wrong-cause unexpected error."""
        anchor_repo = FakeInstanceMetadataRepo()
        worker, task_repo, _ = _make_worker(anchor_repo, SimpleNamespace(id=3))
        task_repo.schedule_retry.side_effect = RuntimeError("boom")

        worker._handle_usage_limit(_task(), _err())  # must not raise


# ============================================================================
# W4 routing — the except-branch in _process_with_timeout
# ============================================================================


class TestWorkerSeamRouting:
    def test_usage_limit_routes_to_handler_not_failure_lane(self):
        from daemon.services.worker_pool import Worker

        task_processor = MagicMock()
        task_processor.run_task = MagicMock(side_effect=_err())
        worker = Worker(
            worker_id="w-route",
            task_processor=task_processor,
            worker_pool=MagicMock(),
        )
        worker._handle_usage_limit = MagicMock()
        worker._handle_task_failure = MagicMock()

        worker._process_with_timeout(_task())

        worker._handle_usage_limit.assert_called_once()
        worker._handle_task_failure.assert_not_called()

    def test_other_errors_still_hit_failure_lane(self):
        from daemon.services.worker_pool import Worker

        task_processor = MagicMock()
        task_processor.run_task = MagicMock(side_effect=RuntimeError("novel"))
        worker = Worker(
            worker_id="w-route2",
            task_processor=task_processor,
            worker_pool=MagicMock(),
        )
        worker._handle_usage_limit = MagicMock()
        worker._handle_task_failure = MagicMock()

        worker._process_with_timeout(_task())

        worker._handle_usage_limit.assert_not_called()
        worker._handle_task_failure.assert_called_once()


# ============================================================================
# W3 — task-processor carve-out
# ============================================================================


def _make_processor(pipeline):
    manager = MagicMock()
    manager._skill_metrics_service = None  # metrics hook short-circuits
    message_repo = MagicMock()
    message_repo.get.return_value = SimpleNamespace(
        status="pending",
        content="do the thing",
        source=None,
        images=None,
        message_metadata=None,
    )
    return ProcessMessageProcessor(
        instance_manager=manager,
        task_repo=MagicMock(),
        message_repository=message_repo,
        pipeline=pipeline,
    )


class TestCarveOut:
    @pytest.mark.asyncio
    async def test_usage_limit_re_raised_no_cascade(self):
        """The carve-out: typed quota error from the graph re-raises
        with NO handle_message_processing_error, no error event, no
        metrics bump (acceptance test 2's no-cascade assertions)."""
        pipeline = MagicMock()
        pipeline.execute = AsyncMock(side_effect=_err())
        processor = _make_processor(pipeline)
        processor._record_metrics_for_task = AsyncMock()

        with patch(
            "daemon.services.task_processor.handle_message_processing_error",
            new_callable=AsyncMock,
        ) as cascade:
            with pytest.raises(UsageLimitError):
                await processor.process(_task(task_id=7))

        cascade.assert_not_called()
        processor._record_metrics_for_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_other_exceptions_cascade_unchanged(self):
        """Every other exception type → the generic cascade still
        fires (regression guard for carve-out placement)."""
        pipeline = MagicMock()
        pipeline.execute = AsyncMock(side_effect=RuntimeError("genuine bug"))
        processor = _make_processor(pipeline)
        processor._record_metrics_for_task = AsyncMock()

        with patch(
            "daemon.services.task_processor.handle_message_processing_error",
            new_callable=AsyncMock,
        ) as cascade:
            with pytest.raises(RuntimeError):
                await processor.process(_task(task_id=8))

        cascade.assert_called_once()
        processor._record_metrics_for_task.assert_called_once()


# ============================================================================
# W6 — anchor clear on success
# ============================================================================


class TestAnchorClearOnSuccess:
    @pytest.mark.asyncio
    async def test_success_callback_clears_anchor(self):
        """A successful turn ends the episode: the success callback
        removes the anchor (soft-fail; a fresh window starts on the
        next quota hit)."""
        from daemon.services.message_processing_pipeline import ProcessingResult

        first_seen = datetime.now(UTC) - timedelta(seconds=60)
        anchor_repo = FakeInstanceMetadataRepo(
            {USAGE_LIMIT_FIRST_SEEN_METADATA_KEY: first_seen.isoformat()}
        )
        manager = MagicMock()
        manager._skill_metrics_service = None
        manager._instance_repository = anchor_repo
        task_repo = MagicMock()
        task_repo.complete_task.return_value = SimpleNamespace(work_id="work-ep")

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            message_repository=MagicMock(),
            pipeline=MagicMock(),
        )
        callbacks = processor._build_callbacks(_task())

        await callbacks.on_success(ProcessingResult(success=True, result_content="ok"))

        assert USAGE_LIMIT_FIRST_SEEN_METADATA_KEY not in anchor_repo._metadata

    @pytest.mark.asyncio
    async def test_clear_failure_never_breaks_finalize(self):
        """delete_metadata explodes → the success callback still
        completes (clearing must never break a finalize)."""
        from daemon.services.message_processing_pipeline import ProcessingResult

        anchor_repo = FakeInstanceMetadataRepo(fail=True)
        manager = MagicMock()
        manager._skill_metrics_service = None
        manager._instance_repository = anchor_repo
        task_repo = MagicMock()
        task_repo.complete_task.return_value = SimpleNamespace(work_id="work-ep")

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            message_repository=MagicMock(),
            pipeline=MagicMock(),
        )
        callbacks = processor._build_callbacks(_task())

        await callbacks.on_success(ProcessingResult(success=True, result_content="ok"))
        task_repo.complete_task.assert_called_once()
