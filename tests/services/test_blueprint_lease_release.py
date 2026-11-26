"""Regression tests for the blueprint build-lease RELEASE path.

The C7 blueprint build lease (``blueprint_build_lease`` project metadata)
is released by TWO seams:

* **AGENT seam** — the blueprinter calls ``blueprint_release_lease`` with
  the coordinator's ``run_token``. The token reaches the agent through the
  JOB MESSAGE body (the ``[blueprint-trigger]`` block).
* **DAEMON seam** — :meth:`JobFeedbackObserver._finalize_job` releases the
  lease when the blueprinter job terminalizes (the safety net for an agent
  that never calls the tool).

Both seams were DEAD before this suite existed:

* the daemon seam read ``project_id`` from ``job_metadata`` — but
  ``project_id`` is a JobItem COLUMN and is NEVER written into
  ``job_metadata``, so the guard could not pass;
* the agent seam had no way to obtain the token: the job→agent delivery
  forwards only ``DISPATCH_CHAIN_KEY`` in the message metadata, and the
  blueprinter has no job-reading tool.

And with no expiry on the lease, a leaked lease blocked every subsequent
trigger until a daemon restart.

Everything here runs on SQLite (in-memory, ``StaticPool``) — no
PostgreSQL, no live DB, no network.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.constants
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.services.blueprint_job_helper import enqueue_blueprinter_job
from daemon.services.blueprint_trigger_coordinator import (
    LEASE_META_KEY,
    BlueprintTriggerCoordinator,
)
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _FinalizeJobResult,
    _ProcessingJobContext,
)
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
from daemon.services.job_queue_service import JobQueueService
from daemon.tools.blueprint import create_blueprint_tools


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _system_default_project(monkeypatch):
    """``normalize_project_id`` needs the system default to be set.

    The job-queue ``enqueue`` path normalizes ``project_id`` through
    :mod:`daemon.services.project_normalizer`, which raises when the
    system default is unset. Mirrors the fixture in
    ``tests/job_queue/conftest.py``.
    """
    monkeypatch.setattr(
        daemon.constants,
        "SYSTEM_DEFAULT_PROJECT_ID",
        "71931ae0-0f25-5fbf-853b-2a78cc978d7e",
        raising=False,
    )


@pytest.fixture
def engine():
    """Fresh in-memory SQLite with all SQLModel tables created.

    Same shape as ``tests/services/test_blueprint_trigger_coordinator.py``.
    """
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def project_repo(engine):
    return SQLModelProjectRepository(engine)


@pytest.fixture
def project(project_repo):
    return project_repo.create(name="proj", project_type="general")


@pytest.fixture
def pid(project):
    return project.project_id


@pytest.fixture
def coordinator(project_repo):
    """Real coordinator — no job_queue_service (unit-test default)."""
    return BlueprintTriggerCoordinator(project_repository=project_repo)


@pytest.fixture
def job_repo(engine):
    return JobRepository(engine)


@pytest.fixture
def job_queue_service(engine, job_repo, pid):
    """REAL JobQueueService on SQLite, with the system queues provisioned.

    The queues are provisioned for the project under test — the helper
    resolves ``system_background_queue`` BY PROJECT ID.
    """
    queue_repo = JobQueueRepository(engine)
    lock_manager = JobLockManager(lock_repo=LockRepository(engine))
    mgmt = JobQueueMgmtService(queue_repo=queue_repo, job_repo=job_repo)
    # The helper looks up ``system_background_queue`` by name.
    asyncio.run(mgmt.auto_provision_system_queues(pid))
    return JobQueueService(
        repository=job_repo,
        lock_manager=lock_manager,
        queue_repo=queue_repo,
    )


@pytest.fixture
def tool_manager(coordinator, pid):
    """Manager double exposing the REAL coordinator to the tool layer."""
    manager = MagicMock()
    manager._blueprint_trigger_coordinator = coordinator
    manager._instance_repository.get.return_value = MagicMock(project_id=pid)
    return manager


# ── Helpers ───────────────────────────────────────────────────────────


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _claim_and_enqueue(
    *,
    coordinator: BlueprintTriggerCoordinator,
    job_queue_service: Any,
    project_id: str,
    mode: str = "rebuild",
    trigger_type: str = "rebuild",
    blueprint_id: str | None = None,
    job_id: str | None = None,
):
    """Claim a lease and enqueue a REAL blueprinter job for it.

    Returns ``(claim_result, enqueued_job_id)``.
    """
    job_id = job_id or f"job-{mode}-{project_id[:8]}"
    claim = await coordinator.try_claim(project_id, mode, job_id)
    assert claim.claimed is True
    enqueued = await enqueue_blueprinter_job(
        job_queue_service=job_queue_service,
        project_id=project_id,
        trigger_type=trigger_type,
        message="Rebuild all project blueprints.",
        run_token=claim.run_token,
        job_id=job_id,
        source="admin-endpoint",
        blueprint_id=blueprint_id,
    )
    return claim, enqueued


def _make_observer(
    *,
    job_repo: JobRepository,
    coordinator: BlueprintTriggerCoordinator,
) -> JobFeedbackObserver:
    """A real observer whose DB half is stubbed (house test pattern).

    ``_finalize_job_db_sync`` opens a ``WriteGuardSession`` over the
    instance manager's engine — unavailable in a unit test — so it is
    replaced by a stub that returns the same payload shape the real
    helper returns (see ``tests/job_queue/test_job_feedback_observer.py``).
    The ASYNC half — which owns the blueprint lease release — runs for
    real.
    """
    instance_manager = MagicMock()
    instance_manager._blueprint_trigger_coordinator = coordinator
    instance_manager._events_service = None
    instance_manager._task_repo = None
    instance_manager._get_last_assistant_message_raw = AsyncMock(
        return_value="blueprint build finished"
    )

    job_queue_service = AsyncMock()
    # These are looked up via ``getattr(..., None)`` on the service. Left
    # as auto-created mock children they would return coroutines where
    # the observer expects plain data (the watcher fetch would yield an
    # un-iterable coroutine). None = "no watcher repo / no resolver",
    # which is the production shape for a service that has not been
    # wired with them.
    job_queue_service._watcher_repo = None
    job_queue_service._work_resolver = None

    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=job_queue_service,
        job_repo=job_repo,
        lock_repo=MagicMock(),
        project_repo=MagicMock(),
        instance_manager=instance_manager,
    )

    def _fake_sync(
        job_id,
        instance_id,
        terminal_status,
        result_summary,
        error_message,
        already_finalized_job_id=None,
    ):
        return _FinalizeJobResult(
            skip=False,
            terminal_status=terminal_status,
            job_id=job_id,
            instance_id=instance_id,
            parent_id=None,
            agent_id="blueprinter",
            result_summary=result_summary,
            error_message=error_message,
            locks_released=1,
            instance_was_terminal=False,
        )

    observer._finalize_job_db_sync = _fake_sync
    return observer


def _trigger_block(message: str) -> dict[str, Any]:
    """Parse the ``[blueprint-trigger]`` block out of a job message."""
    marker = "[blueprint-trigger]"
    assert marker in message, f"no trigger block in message: {message!r}"
    payload = message.split(marker, 1)[1].strip()
    return json.loads(payload)


def _release_lease_tool(manager: MagicMock):
    """Return the ``blueprint_release_lease`` tool from the factory."""
    tools = create_blueprint_tools(manager, "inst-1", "blueprinter")
    return tools[-1]


# ── 1. The daemon-side safety net (SEAM B) ────────────────────────────


class TestDaemonSideRelease:
    """The daemon releases the lease when the blueprinter job terminalizes."""

    def test_lease_is_released_when_job_terminalizes(
        self,
        coordinator,
        job_queue_service,
        job_repo,
        project_repo,
        pid,
    ):
        """THE PRIMARY PRODUCTION PATH.

        claim → enqueue a real job → the job terminalizes → the observer's
        release seam runs → the lease is FREE.
        """
        claim, job_id = _run(
            _claim_and_enqueue(
                coordinator=coordinator,
                job_queue_service=job_queue_service,
                project_id=pid,
            )
        )

        # Precondition: the lease is held and the job row carries the
        # token in its metadata.
        assert _run(coordinator.is_active(pid)) is True
        row = job_repo.get(job_id)
        assert row is not None
        assert row.job_metadata.get("run_token") == claim.run_token
        # The job row's project lives in the COLUMN — never in the
        # metadata map (this is what made the daemon seam dead).
        assert row.project_id == pid
        assert "project_id" not in row.job_metadata

        observer = _make_observer(job_repo=job_repo, coordinator=coordinator)
        ctx = _ProcessingJobContext(instance_id="inst-1", job_id=job_id)
        _run(observer._finalize_job(ctx, "inst-1", "completed"))

        # The lease must be FREE after the job terminalized.
        assert _run(coordinator.is_active(pid)) is False
        assert project_repo.get_metadata(pid, LEASE_META_KEY) is None

    def test_lease_is_released_when_job_errors(
        self,
        coordinator,
        job_queue_service,
        job_repo,
        project_repo,
        pid,
    ):
        """The FAILED/ERROR terminal branch releases the lease too."""
        claim, job_id = _run(
            _claim_and_enqueue(
                coordinator=coordinator,
                job_queue_service=job_queue_service,
                project_id=pid,
            )
        )
        assert claim.run_token

        observer = _make_observer(job_repo=job_repo, coordinator=coordinator)
        ctx = _ProcessingJobContext(instance_id="inst-1", job_id=job_id)
        _run(observer._finalize_job(ctx, "inst-1", "error", "boom"))

        assert _run(coordinator.is_active(pid)) is False
        assert project_repo.get_metadata(pid, LEASE_META_KEY) is None


# ── 2. The agent-side seam (SEAM A) ───────────────────────────────────


class TestAgentSideRelease:
    """The job message carries the lease token; the agent can release it."""

    def test_job_message_carries_the_lease_token(
        self,
        coordinator,
        job_queue_service,
        job_repo,
        pid,
    ):
        """The ``[blueprint-trigger]`` block carries the COORDINATOR token."""
        claim, job_id = _run(
            _claim_and_enqueue(
                coordinator=coordinator,
                job_queue_service=job_queue_service,
                project_id=pid,
            )
        )
        row = job_repo.get(job_id)
        payload = _trigger_block(row.message)
        # The token in the message IS the coordinator's run_token.
        assert payload["lease_token"] == claim.run_token
        # And it is NOT the pending-queue claim token — the name must be
        # unambiguous against ``blueprint_claim_pending``'s run_token.
        assert "run_token" not in payload
        assert payload["trigger"] == "rebuild"

    def test_single_mode_block_carries_blueprint_id(
        self,
        coordinator,
        job_queue_service,
        job_repo,
        pid,
    ):
        """The ``single`` trigger mode carries its blueprint id."""
        claim, job_id = _run(
            _claim_and_enqueue(
                coordinator=coordinator,
                job_queue_service=job_queue_service,
                project_id=pid,
                mode="single",
                trigger_type="single",
                blueprint_id="bp-123",
            )
        )
        row = job_repo.get(job_id)
        payload = _trigger_block(row.message)
        assert payload["trigger"] == "single"
        assert payload["blueprint_id"] == "bp-123"
        assert payload["lease_token"] == claim.run_token

    def test_agent_can_release_with_the_token_from_the_message(
        self,
        coordinator,
        job_queue_service,
        job_repo,
        project_repo,
        tool_manager,
        pid,
    ):
        """An agent that reads the message can release the lease."""
        claim, job_id = _run(
            _claim_and_enqueue(
                coordinator=coordinator,
                job_queue_service=job_queue_service,
                project_id=pid,
            )
        )
        row = job_repo.get(job_id)
        payload = _trigger_block(row.message)

        release = _release_lease_tool(tool_manager)
        result = _run(release.ainvoke({"lease_token": payload["lease_token"]}))

        assert "released" in result.lower()
        assert _run(coordinator.is_active(pid)) is False
        assert project_repo.get_metadata(pid, LEASE_META_KEY) is None


# ── 3. Idempotency + cross-lease safety ───────────────────────────────


class TestReleaseSemantics:
    """A double release is benign; a stale token never frees a live lease."""

    def test_double_release_is_a_benign_noop(
        self, coordinator, tool_manager, pid,
    ):
        """Second release: no exception, benign wording, lease stays free."""
        claim = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        release = _release_lease_tool(tool_manager)

        first = _run(release.ainvoke({"lease_token": claim.run_token}))
        assert "released" in first.lower()

        second = _run(release.ainvoke({"lease_token": claim.run_token}))
        # Benign — NOT an error-flavoured message.
        assert not second.lower().startswith("error")
        assert "not found" not in second.lower()
        assert "no lease" in second.lower()
        # The lease stays free.
        assert _run(coordinator.is_active(pid)) is False

    def test_legacy_run_token_argument_still_works(
        self, coordinator, tool_manager, pid,
    ):
        """The existing prompt contract (``run_token=``) keeps working."""
        claim = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        release = _release_lease_tool(tool_manager)
        result = _run(release.ainvoke({"run_token": claim.run_token}))
        assert "released" in result.lower()
        assert _run(coordinator.is_active(pid)) is False

    def test_release_without_any_token_is_benign(
        self, coordinator, tool_manager, pid,
    ):
        """The token argument is optional — a call with none is a no-op."""
        _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        release = _release_lease_tool(tool_manager)
        result = _run(release.ainvoke({}))
        assert not result.lower().startswith("error")
        # The lease is NOT released — no token, no release.
        assert _run(coordinator.is_active(pid)) is True

    def test_stale_token_does_not_release_a_later_lease(
        self, coordinator, tool_manager, pid,
    ):
        """A token from an EARLIER run must not free a LATER run's lease."""
        first = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        release = _release_lease_tool(tool_manager)
        assert "released" in _run(
            release.ainvoke({"lease_token": first.run_token})
        ).lower()

        # A later run claims the lease again.
        second = _run(coordinator.try_claim(pid, "rebuild", "job-B"))
        assert second.claimed is True
        assert second.run_token != first.run_token

        # The stale token must NOT release the new lease.
        stale = _run(release.ainvoke({"lease_token": first.run_token}))
        assert "no lease" in stale.lower()
        assert _run(coordinator.is_active(pid)) is True

        # The current token still works.
        assert "released" in _run(
            release.ainvoke({"lease_token": second.run_token})
        ).lower()
        assert _run(coordinator.is_active(pid)) is False


# ── 4. Lease expiry (SEAM C) ──────────────────────────────────────────


class TestLeaseExpiry:
    """The lease expires on an absolute TTL measured from ``claimed_at``."""

    def test_expired_lease_is_treated_as_free_by_try_claim(
        self, coordinator, project_repo, pid, monkeypatch,
    ):
        """An expired lease does not coalesce forever — it is reclaimed."""
        # A lease claimed 10 hours ago, with the TTL at 180 minutes.
        old = "2020-01-01T00:00:00+00:00"
        project_repo.set_metadata(
            pid,
            LEASE_META_KEY,
            {
                "run_token": "stale-token",
                "job_id": "job-stale",
                "mode": "rebuild",
                "claimed_at": old,
            },
        )
        monkeypatch.setenv("BLUEPRINT_LEASE_TTL_MINUTES", "180")

        result = _run(coordinator.try_claim(pid, "rebuild", "job-NEW"))
        assert result.claimed is True
        assert result.job_id == "job-NEW"
        assert result.coalesced is False
        # The stale lease is gone — replaced by the fresh claim.
        lease = project_repo.get_metadata(pid, LEASE_META_KEY)
        assert lease["run_token"] == result.run_token

    def test_fresh_lease_still_coalesces(
        self, coordinator, pid,
    ):
        """A lease inside the TTL still coalesces (no behaviour change)."""
        first = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        second = _run(coordinator.try_claim(pid, "rebuild", "job-B"))
        assert second.claimed is False
        assert second.coalesced is True
        assert second.job_id == "job-A"
        assert first.run_token

    def test_sweep_releases_an_expired_lease(
        self, coordinator, project_repo, pid, monkeypatch,
    ):
        """The periodic sweep frees leases past the TTL."""
        project_repo.set_metadata(
            pid,
            LEASE_META_KEY,
            {
                "run_token": "stale-token",
                "job_id": "job-stale",
                "mode": "rebuild",
                "claimed_at": "2020-01-01T00:00:00+00:00",
            },
        )
        monkeypatch.setenv("BLUEPRINT_LEASE_TTL_MINUTES", "180")

        released = _run(coordinator.sweep_expired_leases())
        assert released == 1
        assert project_repo.get_metadata(pid, LEASE_META_KEY) is None
        assert _run(coordinator.is_active(pid)) is False

    def test_sweep_keeps_a_live_lease(
        self, coordinator, project_repo, pid, monkeypatch,
    ):
        """The sweep does NOT free a lease inside the TTL."""
        _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        monkeypatch.setenv("BLUEPRINT_LEASE_TTL_MINUTES", "180")

        released = _run(coordinator.sweep_expired_leases())
        assert released == 0
        assert _run(coordinator.is_active(pid)) is True

    def test_ttl_is_env_tunable_per_run(
        self, coordinator, project_repo, pid, monkeypatch,
    ):
        """``BLUEPRINT_LEASE_TTL_MINUTES`` is read PER RUN."""
        project_repo.set_metadata(
            pid,
            LEASE_META_KEY,
            {
                "run_token": "stale-token",
                "job_id": "job-stale",
                "mode": "rebuild",
                "claimed_at": "2020-01-01T00:00:00+00:00",
            },
        )
        # A huge TTL keeps the (ancient) lease alive. ~19 years — the
        # lease below is stamped 2020-01-01.
        monkeypatch.setenv("BLUEPRINT_LEASE_TTL_MINUTES", "10000000")
        assert _run(coordinator.sweep_expired_leases()) == 0
        assert project_repo.get_metadata(pid, LEASE_META_KEY) is not None

        # A tiny TTL expires it — the knob is read per run, not cached.
        monkeypatch.setenv("BLUEPRINT_LEASE_TTL_MINUTES", "1")
        assert _run(coordinator.sweep_expired_leases()) == 1
        assert project_repo.get_metadata(pid, LEASE_META_KEY) is None
