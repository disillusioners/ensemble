"""Tests for :class:`BlueprintScanService` (G5, Phase 3).

Covers the daemon-side daily scan's smart trigger logic:

* Gated by the ``auto_rebuild_enabled`` flag (default ON since Phase 6).
* Empty corpus → enqueue ``rebuild`` via the coordinator.
* Existing blueprints + pending updates → enqueue ``incremental``.
* Existing blueprints + no pending → skip (no coordinator call).
* Per-project failures are isolated (one bad project does not
  break the sweep).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.services.blueprint_scan_service import (
    MODE_INCREMENTAL,
    MODE_REBUILD,
    BlueprintScanService,
)
from daemon.services.blueprint_trigger_coordinator import ClaimResult


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def project_repo(engine):
    repo = MagicMock()
    # Two active projects + one project that raises on lookup.
    p1 = MagicMock(); p1.project_id = "proj-1"
    p2 = MagicMock(); p2.project_id = "proj-2"
    repo.list_projects = MagicMock(return_value=[p1, p2])
    return repo


@pytest.fixture
def blueprint_repo():
    return MagicMock()


@pytest.fixture
def pending_repo():
    return MagicMock()


@pytest.fixture
def coordinator():
    """MagicMock coordinator whose ``try_claim`` is an AsyncMock that
    returns a successful ClaimResult by default. Tests can override
    the AsyncMock's ``return_value`` / ``side_effect`` per case."""
    coord = MagicMock()
    coord.try_claim = AsyncMock()
    coord.try_claim.return_value = ClaimResult(claimed=True, run_token="tok")
    coord.release = AsyncMock()
    return coord


@pytest.fixture
def job_queue_service():
    """Minimal stand-in for ``JobQueueService`` used by the scan service.

    Mirrors the ``_FakeJobService`` in the API tests:
      * ``_queue_repo.get_by_name`` (sync) → returns a fake queue
      * ``enqueue`` (async) → returns a fake job
    """
    svc = MagicMock()
    fake_queue = MagicMock()
    fake_queue.queue_id = "bg-queue-123"
    svc._queue_repo = MagicMock()
    svc._queue_repo.get_by_name = MagicMock(return_value=fake_queue)
    fake_job = MagicMock()
    fake_job.job_id = "enqueued-job-456"
    svc.enqueue = AsyncMock(return_value=fake_job)
    return svc


def _set_claim_result(coord: Any, result: ClaimResult) -> None:
    """Re-bind ``coord.try_claim`` to an AsyncMock returning ``result``."""
    coord.try_claim = AsyncMock()
    coord.try_claim.return_value = result


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_config(enabled: bool) -> Any:
    cfg = MagicMock()
    cfg.auto_rebuild_enabled = enabled
    cfg.daily_scan_hour = 2
    return cfg


# ── Flag gate ──────────────────────────────────────────────────────────


def test_scan_disabled_by_flag(blueprint_repo, pending_repo, coordinator, project_repo):
    """``auto_rebuild_enabled=False`` → no coordinator calls."""
    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=False),
        project_repository=project_repo,
    )
    _run(svc.execute())
    coordinator.try_claim.assert_not_called()
    blueprint_repo.list_by_project.assert_not_called()
    pending_repo.get_pending_count.assert_not_called()


def test_scan_enabled_flag_runs(blueprint_repo, pending_repo, coordinator, project_repo, job_queue_service):
    """``auto_rebuild_enabled=True`` → the scan fires and enqueues."""
    # Empty corpus for both projects.
    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=True),
        project_repository=project_repo,
        job_queue_service=job_queue_service,
    )
    _run(svc.execute())
    # Two projects, two rebuilds enqueued.
    assert coordinator.try_claim.call_count == 2
    for call in coordinator.try_claim.call_args_list:
        assert call.args[1] == MODE_REBUILD
    # Both claims resulted in enqueue calls.
    assert job_queue_service.enqueue.call_count == 2


# ── Smart trigger logic ────────────────────────────────────────────────


def test_scan_empty_corpus_triggers_rebuild(blueprint_repo, pending_repo, coordinator, project_repo, job_queue_service):
    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)
    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=True),
        project_repository=project_repo,
        job_queue_service=job_queue_service,
    )
    _run(svc.execute())
    # Each project has no blueprints → both get a "rebuild" claim.
    modes = [call.args[1] for call in coordinator.try_claim.call_args_list]
    assert modes == [MODE_REBUILD, MODE_REBUILD]


def test_scan_pending_triggers_incremental(blueprint_repo, pending_repo, coordinator, project_repo, job_queue_service):
    # Project 1: has blueprints + pending → incremental.
    # Project 2: has blueprints + no pending → skip.
    by_project = {
        "proj-1": [MagicMock(id="bp1")],
        "proj-2": [MagicMock(id="bp2")],
    }
    pending_by_project = {"proj-1": 5, "proj-2": 0}
    blueprint_repo.list_by_project = MagicMock(side_effect=lambda pid: by_project[pid])
    pending_repo.get_pending_count = MagicMock(side_effect=lambda pid: pending_by_project[pid])

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=True),
        project_repository=project_repo,
        job_queue_service=job_queue_service,
    )
    _run(svc.execute())
    # Only one coordinator call: incremental for proj-1.
    assert coordinator.try_claim.call_count == 1
    call = coordinator.try_claim.call_args
    assert call.args[0] == "proj-1"
    assert call.args[1] == MODE_INCREMENTAL


def test_scan_no_pending_skips(blueprint_repo, pending_repo, coordinator, project_repo):
    """Has blueprints + no pending → coordinator NOT called at all."""
    blueprint_repo.list_by_project = MagicMock(return_value=[MagicMock(id="bp1")])
    pending_repo.get_pending_count = MagicMock(return_value=0)
    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=True),
        project_repository=project_repo,
    )
    _run(svc.execute())
    coordinator.try_claim.assert_not_called()


# ── Coordinator outcomes ──────────────────────────────────────────────


def test_scan_coalesce_does_not_double_enqueue(blueprint_repo, pending_repo, project_repo):
    """When the coordinator returns coalesced=True, we still don't re-enqueue."""
    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)

    coord = MagicMock()
    _set_claim_result(
        coord, ClaimResult(claimed=False, coalesced=True, job_id="other-job"),
    )

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coord,
        config=_make_config(enabled=True),
        project_repository=project_repo,
    )
    _run(svc.execute())
    # We just observe; the coordinator is the one true enqueue point.
    # Each project still gets one try_claim call.
    assert coord.try_claim.call_count == 2


def test_scan_conflict_is_observed_not_retried(blueprint_repo, pending_repo, project_repo):
    """When the coordinator returns conflict_mode, we surface it; no retry."""
    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)

    coord = MagicMock()
    _set_claim_result(
        coord, ClaimResult(claimed=False, conflict_mode="incremental"),
    )

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coord,
        config=_make_config(enabled=True),
        project_repository=project_repo,
    )
    _run(svc.execute())
    # Exactly one attempt per project; the scan never retries on conflict.
    assert coord.try_claim.call_count == 2


# ── Enqueue after claim ───────────────────────────────────────────────


def test_trigger_enqueues_after_claim(
    blueprint_repo, pending_repo, coordinator, project_repo, job_queue_service,
):
    """A successful coordinator claim MUST be followed by an enqueue call."""
    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)
    # Coordinator claims for both projects.
    coordinator.try_claim.return_value = ClaimResult(
        claimed=True, run_token="tok-abc",
    )

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=True),
        project_repository=project_repo,
        job_queue_service=job_queue_service,
    )
    _run(svc.execute())

    # Two projects → two enqueues.
    assert job_queue_service.enqueue.call_count == 2
    # Verify the enqueue params for the first call.
    first_call = job_queue_service.enqueue.call_args_list[0]
    assert first_call.kwargs["agent_id"] == "blueprinter"
    assert first_call.kwargs["project_id"] == "proj-1"
    # trigger_type is mapped into the metadata dict, not an enqueue kwarg.
    assert first_call.kwargs["metadata"]["trigger"] == MODE_REBUILD
    assert first_call.kwargs["metadata"]["source"] == "auto-scan"
    assert first_call.kwargs["metadata"]["run_token"] == "tok-abc"
    assert first_call.kwargs["source"] == "auto-scan"
    assert first_call.kwargs["priority"] == 9
    # job_id must be forwarded so lease and queue agree.
    assert first_call.kwargs["job_id"] is not None


def test_trigger_enqueue_failure_releases_lease(
    blueprint_repo, pending_repo, project_repo,
):
    """When enqueue raises, the coordinator lease must be released."""
    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)

    coord = MagicMock()
    coord.try_claim = AsyncMock(
        return_value=ClaimResult(claimed=True, run_token="lease-xyz"),
    )
    coord.release = AsyncMock()

    # job_queue_service.enqueue raises → enqueue_blueprinter_job wraps it
    # in BlueprintEnqueueError → scan service catches and releases.
    bad_svc = MagicMock()
    bad_svc._queue_repo = MagicMock()
    bad_svc._queue_repo.get_by_name = MagicMock(
        return_value=MagicMock(queue_id="q1"),
    )
    bad_svc.enqueue = AsyncMock(side_effect=RuntimeError("DB down"))

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coord,
        config=_make_config(enabled=True),
        project_repository=project_repo,
        job_queue_service=bad_svc,
    )
    _run(svc.execute())

    # Both projects claimed → both released on enqueue failure.
    assert coord.try_claim.call_count == 2
    assert coord.release.call_count == 2
    # release(project_id, run_token) per call.
    for call in coord.release.call_args_list:
        assert call.args[1] == "lease-xyz"


def test_trigger_no_job_service_does_not_leak(
    blueprint_repo, pending_repo, project_repo,
):
    """When _job_queue_service is None, a claim must be released (no leak)."""
    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)

    coord = MagicMock()
    coord.try_claim = AsyncMock(
        return_value=ClaimResult(claimed=True, run_token="tok-no-svc"),
    )
    coord.release = AsyncMock()

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coord,
        config=_make_config(enabled=True),
        project_repository=project_repo,
        # job_queue_service intentionally NOT wired (None).
    )
    _run(svc.execute())

    # Claims happened, but enqueue failed → leases released.
    assert coord.try_claim.call_count == 2
    assert coord.release.call_count == 2


# ── Per-project failure isolation ─────────────────────────────────────


def test_scan_per_project_failure_does_not_abort_sweep(blueprint_repo, pending_repo, project_repo, job_queue_service):
    """One project's exception must NOT stop subsequent projects from being scanned."""
    # list_by_project raises on proj-1 but returns [] on proj-2.
    def _list(pid):
        if pid == "proj-1":
            raise RuntimeError("simulated DB error")
        return []
    blueprint_repo.list_by_project = MagicMock(side_effect=_list)
    pending_repo.get_pending_count = MagicMock(return_value=0)

    coord = MagicMock()
    _set_claim_result(coord, ClaimResult(claimed=True, run_token="tok"))

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coord,
        config=_make_config(enabled=True),
        project_repository=project_repo,
        job_queue_service=job_queue_service,
    )
    # Must not raise.
    _run(svc.execute())
    # Only proj-2 successfully reached the coordinator.
    assert coord.try_claim.call_count == 1
    assert coord.try_claim.call_args.args[0] == "proj-2"


def test_scan_coordinator_raises_swallowed(blueprint_repo, pending_repo, project_repo):
    """A coordinator exception is logged + swallowed per project."""
    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)

    coord = MagicMock()
    coord.try_claim = MagicMock()
    coord.try_claim.side_effect = RuntimeError("coordinator down")

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coord,
        config=_make_config(enabled=True),
        project_repository=project_repo,
    )
    # Must not raise out of execute().
    _run(svc.execute())
    # Both projects attempted (no early abort).
    assert coord.try_claim.call_count == 2


def test_scan_project_list_failure_swallowed(blueprint_repo, pending_repo, coordinator):
    """A top-level list_projects failure logs + returns without re-raise."""
    project_repo = MagicMock()
    project_repo.list_projects = MagicMock(side_effect=RuntimeError("list down"))

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=True),
        project_repository=project_repo,
    )
    _run(svc.execute())
    coordinator.try_claim.assert_not_called()


def test_scan_calls_list_projects_with_limit_keyword():
    """C1 regression: list_projects must be called with limit=, not positional."""
    from unittest.mock import Mock
    mock_repo = Mock()
    mock_repo.list_projects = Mock(return_value=[])
    service = BlueprintScanService(
        blueprint_repo=Mock(), pending_repo=Mock(),
        coordinator=Mock(), config=Mock(auto_rebuild_enabled=True),
        project_repository=mock_repo,
    )
    asyncio.run(service.execute())
    mock_repo.list_projects.assert_called_once_with(limit=10_000)


def test_scan_bare_core_only_triggers_rebuild(blueprint_repo, pending_repo, coordinator, project_repo, job_queue_service):
    """C3 regression: a core-only corpus needs a rebuild."""
    blueprint_repo.list_by_project = MagicMock(return_value=[MagicMock(kind="core")])
    pending_repo.get_pending_count = MagicMock(return_value=0)
    svc = BlueprintScanService(blueprint_repo, pending_repo, coordinator,
                               _make_config(True), project_repo,
                               job_queue_service=job_queue_service)
    _run(svc.execute())
    assert [call.args[1] for call in coordinator.try_claim.call_args_list] == [MODE_REBUILD, MODE_REBUILD]
