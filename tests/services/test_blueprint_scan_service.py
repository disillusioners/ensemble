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
from datetime import datetime
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
    """Default fixture: two active projects, both opted in to the
    blueprint system (so existing scan tests still exercise the
    per-project scan path). The opt-in tests build their own project
    repo with ``get_metadata`` side effects to flip individual
    projects on/off."""
    from daemon.constants import BLUEPRINT_ACTIVE_METADATA_KEY
    repo = MagicMock()
    p1 = MagicMock(); p1.project_id = "proj-1"
    p2 = MagicMock(); p2.project_id = "proj-2"
    repo.list_projects = MagicMock(return_value=[p1, p2])
    # Opt both projects in by default. Tests that want to exercise
    # the inactive-by-default path override ``get_metadata`` per project.
    repo.get_metadata = MagicMock(
        side_effect=lambda pid, key: True if key == BLUEPRINT_ACTIVE_METADATA_KEY else None,
    )
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


# ── Default project exclusion ─────────────────────────────────────────


def test_scan_skips_default_project(
    blueprint_repo, pending_repo, coordinator, job_queue_service,
):
    """The system default project (``__system_default__``) is a virtual
    bookkeeping project — the scan must never invoke the coordinator
    or even the per-project repo methods for it.
    """
    # Build a custom project repo with three projects: the default
    # project mixed into the list alongside two real projects.
    repo = MagicMock()
    p1 = MagicMock(); p1.project_id = "proj-1"; p1.name = "Alice"
    p2 = MagicMock(); p2.project_id = "proj-2"; p2.name = "Bob"
    p_default = MagicMock()
    p_default.project_id = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
    p_default.name = "__system_default__"
    repo.list_projects = MagicMock(return_value=[p1, p_default, p2])

    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=True),
        project_repository=repo,
        job_queue_service=job_queue_service,
    )
    _run(svc.execute())

    # Coordinator was called exactly twice — for proj-1 and proj-2.
    # The default project's id must NOT appear in any call.
    assert coordinator.try_claim.call_count == 2
    claimed_project_ids = {call.args[0] for call in coordinator.try_claim.call_args_list}
    assert claimed_project_ids == {"proj-1", "proj-2"}
    assert "71931ae0-0f25-5fbf-853b-2a78cc978d7e" not in claimed_project_ids

    # The per-project repo methods must also never see the default id.
    list_call_ids = {call.args[0] for call in blueprint_repo.list_by_project.call_args_list}
    assert "71931ae0-0f25-5fbf-853b-2a78cc978d7e" not in list_call_ids
    pending_call_ids = {call.args[0] for call in pending_repo.get_pending_count.call_args_list}
    assert "71931ae0-0f25-5fbf-853b-2a78cc978d7e" not in pending_call_ids


# ── Per-project opt-in gate (Phase 7) ─────────────────────────────────


def test_scan_skips_project_not_opted_in(
    blueprint_repo, pending_repo, coordinator, job_queue_service,
):
    """A project without ``blueprint_active=true`` metadata is skipped
    (default: false). The scan must NEVER invoke the coordinator or
    even the per-project repo methods for it.
    """
    from daemon.constants import BLUEPRINT_ACTIVE_METADATA_KEY

    # Two projects: proj-1 opted in, proj-2 not opted in.
    repo = MagicMock()
    p1 = MagicMock(); p1.project_id = "proj-1"; p1.name = "Alice"
    p2 = MagicMock(); p2.project_id = "proj-2"; p2.name = "Bob"
    repo.list_projects = MagicMock(return_value=[p1, p2])
    repo.get_metadata = MagicMock(side_effect=lambda pid, key: (
        True if pid == "proj-1" and key == BLUEPRINT_ACTIVE_METADATA_KEY else None
    ))

    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=True),
        project_repository=repo,
        job_queue_service=job_queue_service,
    )
    _run(svc.execute())

    # Only the opted-in project reached the coordinator.
    assert coordinator.try_claim.call_count == 1
    assert coordinator.try_claim.call_args.args[0] == "proj-1"
    # The per-project repo methods must also never see proj-2.
    list_call_ids = {call.args[0] for call in blueprint_repo.list_by_project.call_args_list}
    assert "proj-2" not in list_call_ids
    pending_call_ids = {call.args[0] for call in pending_repo.get_pending_count.call_args_list}
    assert "proj-2" not in pending_call_ids


def test_scan_includes_project_opted_in(
    blueprint_repo, pending_repo, coordinator, job_queue_service,
):
    """A project with ``blueprint_active=true`` metadata is included
    in the scan (regression for the new gate).
    """
    from daemon.constants import BLUEPRINT_ACTIVE_METADATA_KEY

    # Both projects opted in.
    repo = MagicMock()
    p1 = MagicMock(); p1.project_id = "proj-1"; p1.name = "Alice"
    p2 = MagicMock(); p2.project_id = "proj-2"; p2.name = "Bob"
    repo.list_projects = MagicMock(return_value=[p1, p2])
    repo.get_metadata = MagicMock(side_effect=lambda pid, key: (
        True if key == BLUEPRINT_ACTIVE_METADATA_KEY else None
    ))

    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=True),
        project_repository=repo,
        job_queue_service=job_queue_service,
    )
    _run(svc.execute())

    # Both projects reached the coordinator (rebuild mode — empty corpus).
    assert coordinator.try_claim.call_count == 2
    claimed_ids = {call.args[0] for call in coordinator.try_claim.call_args_list}
    assert claimed_ids == {"proj-1", "proj-2"}


# ── last_run persistence (restart-survival) ─────────────────────────


# Deterministic system default project ID — must match
# ``daemon.services.blueprint_scan_service._SYSTEM_DEFAULT_PID`` and
# the manager.py registration site.
_SYSTEM_DEFAULT_PID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
_SCAN_LAST_RUN_KEY = "blueprint_scan_last_run"


def _set_metadata_calls(repo: Any) -> list[tuple[str, str, Any]]:
    """Return the recorded ``set_metadata`` calls as a list of
    ``(project_id, key, value)`` tuples for easy filtering in
    assertions."""
    calls = []
    for call in repo.set_metadata.call_args_list:
        # set_metadata signature: (project_id, key, value)
        args, _ = call
        if len(args) >= 3:
            calls.append((args[0], args[1], args[2]))
        elif len(args) == 2:
            calls.append((args[0], args[1], None))
    return calls


def test_scan_persists_last_run_after_execution(
    blueprint_repo, pending_repo, coordinator, project_repo, job_queue_service,
):
    """When the scan runs (flag enabled, regardless of project count),
    it MUST persist the current UTC timestamp under the
    ``blueprint_scan_last_run`` key on the system default project. The
    manager loads this on registration to keep the 24h interval clock
    honest across restarts.
    """
    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)
    # ``set_metadata`` is auto-tracked on the MagicMock fixture; ensure
    # it's a regular Mock (sync) so the service's ``asyncio.to_thread``
    # wrapper accepts it.
    project_repo.set_metadata = MagicMock()

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=True),
        project_repository=project_repo,
        job_queue_service=job_queue_service,
    )
    _run(svc.execute())

    # Exactly one scan-related metadata write — on the system default
    # project, with the well-known key. There may be 0 other writes
    # because the fixtures do not call ``set_metadata`` for any
    # blueprint-related key.
    scan_calls = [
        c for c in _set_metadata_calls(project_repo)
        if c[0] == _SYSTEM_DEFAULT_PID and c[1] == _SCAN_LAST_RUN_KEY
    ]
    assert len(scan_calls) == 1, (
        f"expected 1 last_run persist call, got {len(scan_calls)}: {scan_calls}"
    )
    # Value must be an ISO 8601 UTC string parseable by
    # ``datetime.fromisoformat`` — the contract the manager reads.
    ts_str = scan_calls[0][2]
    assert isinstance(ts_str, str)
    parsed = datetime.fromisoformat(ts_str)
    assert parsed.tzinfo is not None  # timezone-aware UTC


def test_scan_does_not_persist_when_disabled(
    blueprint_repo, pending_repo, coordinator, project_repo,
):
    """When ``auto_rebuild_enabled=False``, ``execute()`` short-circuits
    before the scan loop. The persist step must NOT run — there was no
    scan to record, and writing a fresh ``last_run`` would silently
    delay the first real scan by a full 24h interval.
    """
    project_repo.set_metadata = MagicMock()

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=False),
        project_repository=project_repo,
    )
    _run(svc.execute())

    # No set_metadata call touched the scan last_run key.
    scan_calls = [
        c for c in _set_metadata_calls(project_repo)
        if c[0] == _SYSTEM_DEFAULT_PID and c[1] == _SCAN_LAST_RUN_KEY
    ]
    assert scan_calls == [], (
        f"expected no last_run persist when disabled, got {scan_calls}"
    )
    # The whole scan was a no-op: no coordinator, no per-project calls.
    coordinator.try_claim.assert_not_called()
    blueprint_repo.list_by_project.assert_not_called()
    pending_repo.get_pending_count.assert_not_called()


def test_scan_persists_even_with_no_active_projects(
    blueprint_repo, pending_repo, coordinator, project_repo, job_queue_service,
):
    """Edge case: the scan ran but no projects opted in. The persist
    must still fire — the scan DID run, and the next restart should
    honor that 24h interval regardless of how many projects it touched.
    """
    # No projects in the repo at all.
    project_repo.list_projects = MagicMock(return_value=[])
    project_repo.set_metadata = MagicMock()
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

    # Coordinator saw nothing (no active projects) — but the persist
    # MUST have happened anyway.
    coordinator.try_claim.assert_not_called()
    scan_calls = [
        c for c in _set_metadata_calls(project_repo)
        if c[0] == _SYSTEM_DEFAULT_PID and c[1] == _SCAN_LAST_RUN_KEY
    ]
    assert len(scan_calls) == 1, (
        f"expected persist to run even with zero projects, got {scan_calls}"
    )


def test_scan_persist_failure_does_not_abort_execute(
    blueprint_repo, pending_repo, coordinator, project_repo, job_queue_service,
):
    """A failure in the metadata write must NOT propagate. The scan
    itself succeeded; the persist is best-effort bookkeeping.
    """
    blueprint_repo.list_by_project = MagicMock(return_value=[])
    pending_repo.get_pending_count = MagicMock(return_value=0)

    # Make set_metadata raise — simulate a transient DB hiccup.
    project_repo.set_metadata = MagicMock(
        side_effect=RuntimeError("DB temporarily unavailable"),
    )

    svc = BlueprintScanService(
        blueprint_repo=blueprint_repo,
        pending_repo=pending_repo,
        coordinator=coordinator,
        config=_make_config(enabled=True),
        project_repository=project_repo,
        job_queue_service=job_queue_service,
    )
    # Must not raise — the catch in execute() swallows the persist
    # error so the rest of the scan and the in-memory last_run update
    # still succeed.
    _run(svc.execute())

    # The scan still did its work despite the persist failure.
    assert coordinator.try_claim.call_count == 2
    assert project_repo.set_metadata.call_count == 1
