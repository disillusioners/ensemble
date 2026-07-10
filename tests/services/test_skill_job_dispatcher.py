"""Tests for ``SkillJobDispatcher`` (Phase 5 of Skill Evolution).

The dispatcher is the single front-door for skill-related
:py:class:`JobItem` rows. Tests pin three invariants:

1. **Routing** — every ``enqueue_*`` method resolves the
   ``system_parallel_queue`` ID via ``queue_repo.get_by_name`` and
   passes it as ``queue_id=`` on every ``job_service.enqueue`` call.
   Skill jobs MUST NOT fall back to the default FIFO routing.

2. **Job type / metadata** — each public method enqueues with the
   right ``job_type`` literal and the expected metadata keys.

3. **Graceful degradation** — missing queue / lookup exceptions
   return ``None`` so the dispatcher doesn't crash the trigger
   engine; a missing queue still allows the job to enqueue
   (degraded to FIFO routing).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# =============================================================================
# Fixtures
# =============================================================================


class FakeQueue:
    """Minimal stand-in for :class:`JobQueue` (only ``queue_id`` is read)."""

    def __init__(self, queue_id: str, name: str = "system_parallel_queue"):
        self.queue_id = queue_id
        self.queue_name = name
        self.project_id = "test-project"


class FakeJob:
    """Minimal stand-in for :class:`JobItem` (only ``job_id`` is read)."""

    def __init__(self, job_id: str):
        self.job_id = job_id


def _make_job_service(job_id: str = "job-1") -> Any:
    """A mock :class:`JobQueueService` whose ``enqueue`` returns a fake job."""
    svc = MagicMock()
    svc.enqueue = AsyncMock(return_value=FakeJob(job_id))
    return svc


def _make_queue_repo(queue: FakeQueue | None = FakeQueue("q-parallel-1")) -> Any:
    """A mock :class:`JobQueueRepository` (sync ``get_by_name``)."""
    repo = MagicMock()
    repo.get_by_name = MagicMock(return_value=queue)
    return repo


@pytest.fixture
def job_service():
    """Mock :class:`JobQueueService` with a deterministic ``job_id`` return."""
    return _make_job_service(job_id="job-abc")


@pytest.fixture
def queue_repo():
    """Mock :class:`JobQueueRepository` returning a known parallel queue."""
    return _make_queue_repo(FakeQueue("q-parallel-1"))


@pytest.fixture
def dispatcher(job_service, queue_repo):
    """A :class:`SkillJobDispatcher` wired against the mock collaborators."""
    from daemon.services.skill_job_dispatcher import SkillJobDispatcher

    return SkillJobDispatcher(
        job_service=job_service, queue_repo=queue_repo
    )


# =============================================================================
# _resolve_parallel_queue_id
# =============================================================================


class TestResolveParallelQueueId:
    """Tests for ``SkillJobDispatcher._resolve_parallel_queue_id``."""

    async def test_resolve_parallel_queue_id(
        self, dispatcher, queue_repo
    ):
        """``get_by_name`` is called with ``(project, 'system_parallel_queue')``."""
        from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID

        queue_id = await dispatcher._resolve_parallel_queue_id(
            "my-project"
        )

        # Queue ID returned matches the FakeQueue's queue_id.
        assert queue_id == "q-parallel-1"
        # Lookup used the system_parallel_queue name and a normalized project.
        queue_repo.get_by_name.assert_called_once()
        call_args = queue_repo.get_by_name.call_args
        # (project_id, name) positional args.
        assert call_args.args[1] == "system_parallel_queue"
        assert call_args.args[0] == "my-project"

    async def test_resolve_parallel_queue_id_none_project(
        self, dispatcher, queue_repo
    ):
        """``project_id=None`` normalizes to ``SYSTEM_DEFAULT_PROJECT_ID``."""
        from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID

        queue_id = await dispatcher._resolve_parallel_queue_id(None)

        assert queue_id == "q-parallel-1"
        call_args = queue_repo.get_by_name.call_args
        # Project ID normalized to system default.
        assert call_args.args[0] == SYSTEM_DEFAULT_PROJECT_ID
        assert call_args.args[1] == "system_parallel_queue"

    async def test_resolve_parallel_queue_id_empty_string(
        self, dispatcher, queue_repo
    ):
        """Empty ``project_id`` also normalizes (no short-circuit)."""
        from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID

        queue_id = await dispatcher._resolve_parallel_queue_id("")

        assert queue_id == "q-parallel-1"
        assert queue_repo.get_by_name.call_args.args[0] == (
            SYSTEM_DEFAULT_PROJECT_ID
        )

    async def test_resolve_parallel_queue_id_not_found(
        self, job_service,
    ):
        """Queue lookup returns None → resolver returns None (no crash)."""
        from daemon.services.skill_job_dispatcher import SkillJobDispatcher

        queue_repo = _make_queue_repo(queue=None)
        dispatcher = SkillJobDispatcher(
            job_service=job_service, queue_repo=queue_repo
        )

        queue_id = await dispatcher._resolve_parallel_queue_id("my-project")

        assert queue_id is None

    async def test_resolve_parallel_queue_id_lookup_exception(
        self, job_service,
    ):
        """Repository raises → resolver returns None (graceful degradation)."""
        from daemon.services.skill_job_dispatcher import SkillJobDispatcher

        queue_repo = MagicMock()
        queue_repo.get_by_name = MagicMock(
            side_effect=RuntimeError("DB hiccup")
        )
        dispatcher = SkillJobDispatcher(
            job_service=job_service, queue_repo=queue_repo
        )

        queue_id = await dispatcher._resolve_parallel_queue_id("my-project")

        assert queue_id is None


# =============================================================================
# enqueue_analysis
# =============================================================================


class TestEnqueueAnalysis:
    """Tests for ``SkillJobDispatcher.enqueue_analysis``."""

    async def test_enqueue_analysis(self, dispatcher, job_service):
        """Enqueues with job_type='skill_analysis' + correct metadata."""
        job_id = await dispatcher.enqueue_analysis(
            project_id="my-project",
            skill_id="skill-abc",
            reason="low_completion_rate",
            stats={"completion_rate": 0.42},
        )

        assert job_id == "job-abc"
        job_service.enqueue.assert_awaited_once()
        kwargs = job_service.enqueue.await_args.kwargs
        assert kwargs["job_type"] == "skill_analysis"
        assert kwargs["agent_id"] == "skill-keeper"
        assert kwargs["source"] == "skill_evolution"
        assert kwargs["project_id"] == "my-project"
        # Parallel-queue ID was resolved + passed.
        assert kwargs["queue_id"] == "q-parallel-1"
        # Metadata carries skill_id + reason + stats.
        meta = kwargs["metadata"]
        assert meta["skill_id"] == "skill-abc"
        assert meta["reason"] == "low_completion_rate"
        assert meta["stats"] == {"completion_rate": 0.42}

    async def test_enqueue_analysis_no_reason_no_stats(
        self, dispatcher, job_service
    ):
        """Defaults: ``reason=''`` and ``stats={}``."""
        await dispatcher.enqueue_analysis(
            project_id="my-project", skill_id="skill-xyz"
        )

        kwargs = job_service.enqueue.await_args.kwargs
        meta = kwargs["metadata"]
        assert meta["reason"] == ""
        assert meta["stats"] == {}
        assert meta["skill_id"] == "skill-xyz"

    async def test_enqueue_analysis_normalizes_project_id(
        self, dispatcher, job_service
    ):
        """``project_id=None`` is normalized to ``SYSTEM_DEFAULT_PROJECT_ID``."""
        from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID

        await dispatcher.enqueue_analysis(
            project_id=None, skill_id="skill-1"
        )

        kwargs = job_service.enqueue.await_args.kwargs
        assert kwargs["project_id"] == SYSTEM_DEFAULT_PROJECT_ID


# =============================================================================
# enqueue_evolution
# =============================================================================


class TestEnqueueEvolution:
    """Tests for ``SkillJobDispatcher.enqueue_evolution``."""

    async def test_enqueue_evolution_fix(
        self, dispatcher, job_service
    ):
        """Enqueues with job_type='skill_evolution' + correct metadata."""
        job_id = await dispatcher.enqueue_evolution(
            project_id="my-project",
            skill_id="skill-abc",
            evolution_type="FIX",
            direction="tighten error handling",
        )

        assert job_id == "job-abc"
        kwargs = job_service.enqueue.await_args.kwargs
        assert kwargs["job_type"] == "skill_evolution"
        assert kwargs["queue_id"] == "q-parallel-1"
        assert kwargs["agent_id"] == "skill-keeper"
        meta = kwargs["metadata"]
        assert meta["skill_id"] == "skill-abc"
        assert meta["evolution_type"] == "FIX"
        assert meta["direction"] == "tighten error handling"

    async def test_enqueue_evolution_derived(
        self, dispatcher, job_service
    ):
        """``evolution_type='DERIVED'`` passes through verbatim."""
        await dispatcher.enqueue_evolution(
            project_id="my-project",
            skill_id="skill-abc",
            evolution_type="DERIVED",
            direction="specialize for sub-task",
        )

        kwargs = job_service.enqueue.await_args.kwargs
        assert kwargs["metadata"]["evolution_type"] == "DERIVED"

    async def test_enqueue_evolution_captured(
        self, dispatcher, job_service
    ):
        """``evolution_type='CAPTURED'`` passes through verbatim."""
        await dispatcher.enqueue_evolution(
            project_id="my-project",
            skill_id="skill-abc",
            evolution_type="CAPTURED",
            direction="auto-capture from successful task",
        )

        kwargs = job_service.enqueue.await_args.kwargs
        assert kwargs["metadata"]["evolution_type"] == "CAPTURED"


# =============================================================================
# enqueue_capture
# =============================================================================


class TestEnqueueCapture:
    """Tests for ``SkillJobDispatcher.enqueue_capture``."""

    async def test_enqueue_capture(self, dispatcher, job_service):
        """Enqueues with job_type='skill_capture' + task_details in metadata."""
        task_details = {
            "instance_id": "inst-abc",
            "agent_id": "agent-x",
            "project_id": "proj-1",
            "task_message": "extract a skill from this task",
            "iterations": 8,
            "duration_seconds": 90,
        }

        job_id = await dispatcher.enqueue_capture(
            project_id="my-project",
            task_details=task_details,
        )

        assert job_id == "job-abc"
        kwargs = job_service.enqueue.await_args.kwargs
        assert kwargs["job_type"] == "skill_capture"
        assert kwargs["queue_id"] == "q-parallel-1"
        assert kwargs["agent_id"] == "skill-keeper"
        # task_details lives verbatim in metadata.
        assert kwargs["metadata"] == {"task_details": task_details}
        assert kwargs["metadata"]["task_details"]["iterations"] == 8
        assert kwargs["metadata"]["task_details"]["duration_seconds"] == 90


# =============================================================================
# enqueue_metric_scan
# =============================================================================


class TestEnqueueMetricScan:
    """Tests for ``SkillJobDispatcher.enqueue_metric_scan``."""

    async def test_enqueue_metric_scan(self, dispatcher, job_service):
        """Enqueues with job_type='skill_metric_scan' + scan_target in metadata."""
        job_id = await dispatcher.enqueue_metric_scan(
            project_id="my-project",
        )

        assert job_id == "job-abc"
        kwargs = job_service.enqueue.await_args.kwargs
        assert kwargs["job_type"] == "skill_metric_scan"
        assert kwargs["queue_id"] == "q-parallel-1"
        assert kwargs["agent_id"] == "skill-keeper"
        assert kwargs["metadata"]["scan_target"] == "my-project"

    async def test_enqueue_metric_scan_default_project(
        self, dispatcher, job_service
    ):
        """``project_id=None`` defaults to scan_target='all' but normalizes."""
        from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID

        await dispatcher.enqueue_metric_scan()

        kwargs = job_service.enqueue.await_args.kwargs
        # scan_target defaults to "all" — used as a human-readable hint.
        assert kwargs["metadata"]["scan_target"] == "all"
        # project_id itself was normalized to system default.
        assert kwargs["project_id"] == SYSTEM_DEFAULT_PROJECT_ID


# =============================================================================
# Routing Invariant — ALL jobs must use parallel queue, NOT FIFO
# =============================================================================


class TestRoutingInvariant:
    """Phase 5 critical routing rule — every skill job → ``system_parallel_queue``.

    Regression test for the FIFO-vs-parallel routing bug: if a caller
    bypasses :class:`SkillJobDispatcher` and calls
    ``job_service.enqueue(job_type='skill_*', queue_id=None)`` directly,
    the default FIFO routing serializes every skill job behind
    concurrency=1. The dispatcher is the single chokepoint that fixes
    this — every test below asserts the resolved queue_id was passed.
    """

    async def test_all_jobs_use_parallel_queue_id(
        self, job_service,
    ):
        """Run all 4 enqueue_* methods; verify each passes ``q-parallel-1``."""
        from daemon.services.skill_job_dispatcher import SkillJobDispatcher

        queue_repo = _make_queue_repo(FakeQueue("q-parallel-1"))
        d = SkillJobDispatcher(job_service=job_service, queue_repo=queue_repo)

        await d.enqueue_analysis(
            project_id="p1", skill_id="s1", reason="r", stats={}
        )
        await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="FIX", direction="d"
        )
        await d.enqueue_capture(project_id="p1", task_details={})
        await d.enqueue_metric_scan(project_id="p1")

        # 4 enqueue calls total.
        assert job_service.enqueue.await_count == 4
        # Each one passed queue_id='q-parallel-1' (NOT None).
        for call in job_service.enqueue.await_args_list:
            assert call.kwargs["queue_id"] == "q-parallel-1"
            assert call.kwargs["queue_id"] is not None

    async def test_all_jobs_use_skill_keeper_agent(
        self, job_service,
    ):
        """Run all 4 enqueue_* methods; verify ``agent_id='skill-keeper'``."""
        from daemon.services.skill_job_dispatcher import SkillJobDispatcher

        queue_repo = _make_queue_repo(FakeQueue("q-parallel-1"))
        d = SkillJobDispatcher(job_service=job_service, queue_repo=queue_repo)

        await d.enqueue_analysis(project_id="p1", skill_id="s1")
        await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="FIX", direction="d"
        )
        await d.enqueue_capture(project_id="p1", task_details={})
        await d.enqueue_metric_scan(project_id="p1")

        for call in job_service.enqueue.await_args_list:
            assert call.kwargs["agent_id"] == "skill-keeper"

    async def test_all_jobs_use_skill_evolution_source(
        self, job_service,
    ):
        """``source='skill_evolution'`` distinguishes these jobs in the UI."""
        from daemon.services.skill_job_dispatcher import SkillJobDispatcher

        queue_repo = _make_queue_repo(FakeQueue("q-parallel-1"))
        d = SkillJobDispatcher(job_service=job_service, queue_repo=queue_repo)

        await d.enqueue_analysis(project_id="p1", skill_id="s1")
        await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="FIX", direction="d"
        )
        await d.enqueue_capture(project_id="p1", task_details={})
        await d.enqueue_metric_scan(project_id="p1")

        for call in job_service.enqueue.await_args_list:
            assert call.kwargs["source"] == "skill_evolution"

    async def test_missing_parallel_queue_falls_back_to_none(
        self, job_service,
    ):
        """When the parallel queue doesn't exist, ``queue_id=None`` is passed.

        The default FIFO routing in :class:`JobQueueService.enqueue` then
        kicks in (degraded but non-fatal — the system isn't paralyzed by
        a missing parallel queue).
        """
        from daemon.services.skill_job_dispatcher import SkillJobDispatcher

        queue_repo = _make_queue_repo(queue=None)
        d = SkillJobDispatcher(job_service=job_service, queue_repo=queue_repo)

        await d.enqueue_analysis(project_id="p1", skill_id="s1")

        kwargs = job_service.enqueue.await_args.kwargs
        # queue_id is None so enqueue() falls back to FIFO routing.
        assert kwargs["queue_id"] is None


# =============================================================================
# Module-level constants — Job type literals
# =============================================================================


class TestJobTypeConstants:
    """The 4 ``JOB_TYPE_*`` constants must match the strings the agent expects."""

    def test_job_type_constants_are_correct(self):
        from daemon.services.skill_job_dispatcher import (
            JOB_TYPE_ANALYSIS,
            JOB_TYPE_CAPTURE,
            JOB_TYPE_EVOLUTION,
            JOB_TYPE_METRIC_SCAN,
        )

        assert JOB_TYPE_ANALYSIS == "skill_analysis"
        assert JOB_TYPE_EVOLUTION == "skill_evolution"
        assert JOB_TYPE_CAPTURE == "skill_capture"
        assert JOB_TYPE_METRIC_SCAN == "skill_metric_scan"

    def test_skill_keeper_agent_id_constant(self):
        from daemon.services.skill_job_dispatcher import SKILL_KEEPER_AGENT_ID

        assert SKILL_KEEPER_AGENT_ID == "skill-keeper"

    def test_parallel_queue_name_constant(self):
        from daemon.services.skill_job_dispatcher import PARALLEL_QUEUE_NAME

        assert PARALLEL_QUEUE_NAME == "system_parallel_queue"

    def test_source_tag_constant(self):
        from daemon.services.skill_job_dispatcher import SOURCE_TAG

        assert SOURCE_TAG == "skill_evolution"