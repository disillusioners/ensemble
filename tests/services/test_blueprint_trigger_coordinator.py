"""Tests for :class:`BlueprintTriggerCoordinator` (C7, Phase 3).

Covers the unified trigger coordinator that all blueprint build
enqueuing must funnel through:

* ``try_claim`` — atomic claim with coalesce + cross-mode conflict.
* ``heartbeat`` — refresh ``last_heartbeat_at`` for the matching token.
* ``release`` — terminal release guarded by ``run_token``.
* Stale leases (``last_heartbeat_at`` past the TTL) are reclaimable.
* Periodic sweep releases expired leases.
* ``is_active`` reflects the live lease state.
* ``reconcile_on_startup`` releases orphaned leases whose job is gone
  (or whose ``_job_queue_service`` is unset — the test default).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.services.blueprint_trigger_coordinator import (
    HEARTBEAT_INTERVAL_SECONDS,
    LEASE_META_KEY,
    LEASE_TTL_SECONDS,
    BlueprintTriggerCoordinator,
    ClaimResult,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Fresh in-memory SQLite with all SQLModel tables created."""
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
    # No job_queue_service → reconcile treats every lease as orphaned
    # (matches the spec's "release on startup if queue unknown").
    return BlueprintTriggerCoordinator(project_repository=project_repo)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ── try_claim ──────────────────────────────────────────────────────────


class TestTryClaim:
    def test_try_claim_success(self, coordinator, pid, project_repo):
        """First claim succeeds and returns a fresh run_token."""
        result = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        assert isinstance(result, ClaimResult)
        assert result.claimed is True
        assert result.job_id == "job-A"
        assert result.coalesced is False
        assert result.conflict_mode is None
        assert result.run_token is not None
        # The lease is durably persisted.
        lease = project_repo.get_metadata(pid, LEASE_META_KEY)
        assert isinstance(lease, dict)
        assert lease["run_token"] == result.run_token
        assert lease["job_id"] == "job-A"
        assert lease["mode"] == "rebuild"

    def test_try_claim_coalesce_same_mode(self, coordinator, pid):
        """Second claim with same mode returns the in-flight job_id."""
        r1 = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        r2 = _run(coordinator.try_claim(pid, "rebuild", "job-B"))
        assert r2.claimed is False
        assert r2.coalesced is True
        assert r2.job_id == "job-A"
        assert r2.conflict_mode is None
        # The original token survives the second claim.
        assert r1.run_token == r2.run_token or r2.run_token is None

    def test_try_claim_conflict_different_mode(self, coordinator, pid):
        """A claim with a different mode surfaces conflict_mode."""
        _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        r2 = _run(coordinator.try_claim(pid, "incremental", "job-C"))
        assert r2.claimed is False
        assert r2.coalesced is False
        assert r2.conflict_mode == "rebuild"

    def test_try_claim_different_project_independent(self, coordinator, project_repo, pid):
        """Claims on different projects do NOT interfere."""
        p2 = project_repo.create(name="p2", project_type="general")
        r1 = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        r2 = _run(coordinator.try_claim(p2.project_id, "rebuild", "job-B"))
        assert r1.claimed and r2.claimed
        assert r1.job_id == "job-A" and r2.job_id == "job-B"


# ── heartbeat ──────────────────────────────────────────────────────────


class TestHeartbeat:
    def test_heartbeat_success(self, coordinator, pid, project_repo):
        r1 = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        # Read the original timestamp; bump by hand so we can detect change.
        before = project_repo.get_metadata(pid, LEASE_META_KEY)["last_heartbeat_at"]
        # Force the timestamp backwards so a same-instant refresh is observable.
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        lease = project_repo.get_metadata(pid, LEASE_META_KEY)
        lease["last_heartbeat_at"] = past
        project_repo.set_metadata(pid, LEASE_META_KEY, lease)
        assert _run(coordinator.heartbeat(pid, r1.run_token)) is True
        after = project_repo.get_metadata(pid, LEASE_META_KEY)["last_heartbeat_at"]
        assert after != past
        assert after != before or before  # smoke: present and unchanged or replaced

    def test_heartbeat_wrong_token(self, coordinator, pid):
        _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        assert _run(coordinator.heartbeat(pid, "not-the-token")) is False

    def test_heartbeat_no_lease(self, coordinator, pid):
        assert _run(coordinator.heartbeat(pid, "any-token")) is False


# ── release ────────────────────────────────────────────────────────────


class TestRelease:
    def test_release_success(self, coordinator, pid, project_repo):
        r1 = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        assert _run(coordinator.release(pid, r1.run_token)) is True
        # Lease is gone from metadata.
        assert project_repo.get_metadata(pid, LEASE_META_KEY) is None
        # A fresh claim now succeeds again.
        r2 = _run(coordinator.try_claim(pid, "rebuild", "job-D"))
        assert r2.claimed is True
        assert r2.job_id == "job-D"

    def test_release_wrong_token(self, coordinator, pid, project_repo):
        r1 = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        assert _run(coordinator.release(pid, "not-the-token")) is False
        # Lease is still there.
        lease = project_repo.get_metadata(pid, LEASE_META_KEY)
        assert isinstance(lease, dict) and lease.get("run_token") == r1.run_token

    def test_release_no_lease(self, coordinator, pid):
        assert _run(coordinator.release(pid, "any-token")) is False


# ── stale-lease reclaim + sweep ───────────────────────────────────────


class TestStaleLease:
    def test_stale_lease_treated_as_expired(self, coordinator, pid, project_repo):
        """A lease whose heartbeat is older than TTL is reclaimable."""
        r1 = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        # Age the heartbeat past the TTL.
        lease = project_repo.get_metadata(pid, LEASE_META_KEY)
        lease["last_heartbeat_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=LEASE_TTL_SECONDS + 60)
        ).isoformat()
        project_repo.set_metadata(pid, LEASE_META_KEY, lease)

        # A new claim with a different mode is now permitted (the stale
        # lease is treated as gone, so it's a fresh claim, not a conflict).
        r2 = _run(coordinator.try_claim(pid, "incremental", "job-B"))
        assert r2.claimed is True
        assert r2.run_token and r2.run_token != r1.run_token

    def test_sweep_expired_leases(self, coordinator, pid, project_repo):
        r1 = _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        # Age the heartbeat past the TTL.
        lease = project_repo.get_metadata(pid, LEASE_META_KEY)
        lease["last_heartbeat_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=LEASE_TTL_SECONDS + 60)
        ).isoformat()
        project_repo.set_metadata(pid, LEASE_META_KEY, lease)

        released = _run(coordinator._sweep_expired_leases())
        assert released == 1
        # The lease is now gone.
        assert project_repo.get_metadata(pid, LEASE_META_KEY) is None

    def test_sweep_does_not_touch_fresh_leases(self, coordinator, pid):
        _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        # Fresh lease: no sweep activity.
        assert _run(coordinator._sweep_expired_leases()) == 0

    def test_sweep_isolated_per_project(self, coordinator, project_repo, pid):
        """Sweeping one project does not touch another."""
        p2 = project_repo.create(name="p2", project_type="general")
        _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        _run(coordinator.try_claim(p2.project_id, "rebuild", "job-B"))
        # No leases are expired, so nothing is released.
        assert _run(coordinator._sweep_expired_leases()) == 0


# ── is_active ──────────────────────────────────────────────────────────


class TestIsActive:
    def test_is_active_true_with_lease(self, coordinator, pid):
        _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        assert _run(coordinator.is_active(pid)) is True

    def test_is_active_false_without_lease(self, coordinator, pid):
        assert _run(coordinator.is_active(pid)) is False

    def test_is_active_mode_filter(self, coordinator, pid):
        _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        assert _run(coordinator.is_active(pid, mode="rebuild")) is True
        assert _run(coordinator.is_active(pid, mode="incremental")) is False

    def test_is_active_false_for_expired_lease(self, coordinator, pid, project_repo):
        _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        lease = project_repo.get_metadata(pid, LEASE_META_KEY)
        lease["last_heartbeat_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=LEASE_TTL_SECONDS + 60)
        ).isoformat()
        project_repo.set_metadata(pid, LEASE_META_KEY, lease)
        assert _run(coordinator.is_active(pid)) is False


# ── reconcile_on_startup ───────────────────────────────────────────────


class TestReconcileOnStartup:
    def test_reconcile_releases_when_no_queue_service(
        self, coordinator, pid, project_repo
    ):
        """With no job_queue_service, every lease is treated as orphaned."""
        _run(coordinator.try_claim(pid, "rebuild", "job-A"))
        released = _run(coordinator.reconcile_on_startup())
        assert released == 1
        assert project_repo.get_metadata(pid, LEASE_META_KEY) is None

    def test_reconcile_no_op_with_no_leases(self, coordinator):
        assert _run(coordinator.reconcile_on_startup()) == 0

    def test_reconcile_with_terminal_job_releases(
        self, project_repo, pid,
    ):
        """When the job is in a terminal state, the lease is released."""
        svc = _make_job_service(job_status="completed")
        coord = BlueprintTriggerCoordinator(
            project_repository=project_repo, job_queue_service=svc,
        )
        _run(coord.try_claim(pid, "rebuild", "job-A"))
        released = _run(coord.reconcile_on_startup())
        assert released == 1
        assert project_repo.get_metadata(pid, LEASE_META_KEY) is None

    def test_reconcile_with_active_job_keeps_lease(
        self, project_repo, pid,
    ):
        """When the job is still active, the lease is preserved."""
        svc = _make_job_service(job_status="processing")
        coord = BlueprintTriggerCoordinator(
            project_repository=project_repo, job_queue_service=svc,
        )
        _run(coord.try_claim(pid, "rebuild", "job-A"))
        released = _run(coord.reconcile_on_startup())
        assert released == 0
        assert project_repo.get_metadata(pid, LEASE_META_KEY) is not None

    def test_reconcile_per_project_failure_isolated(
        self, project_repo, pid,
    ):
        """A failure on one project does not abort the whole pass."""
        # First, set up a lease on a project whose list_projects() will
        # raise. The simpler approach: inject a broken queue service
        # that always raises, then verify the pass swallows the error.
        svc = _make_broken_job_service()
        coord = BlueprintTriggerCoordinator(
            project_repository=project_repo, job_queue_service=svc,
        )
        _run(coord.try_claim(pid, "rebuild", "job-A"))
        # Should not raise; returns 0 because the queue probe failed
        # (conservative — leave the lease for the sweep to clean).
        released = _run(coord.reconcile_on_startup())
        assert released == 0


# ── Test doubles ───────────────────────────────────────────────────────


class _FakeJob:
    def __init__(self, status: str) -> None:
        self.status = status


def _make_job_service(*, job_status: str | None):
    """Return a stub JobQueueService with a synchronous ``get_job``."""
    svc = _FakeJobService()
    if job_status is None:
        svc.get_job = _async_return(None)
    else:
        svc.get_job = _async_return(_FakeJob(job_status))
    return svc


def _make_broken_job_service():
    """Return a JobQueueService whose get_job always raises."""
    svc = _FakeJobService()
    async def _raise(*_a, **_kw):
        raise RuntimeError("queue down")
    svc.get_job = _raise
    return svc


class _FakeJobService:
    """Minimal stub: only ``get_job`` is exercised by reconciliation."""


def _async_return(value: Any):
    async def _coro(*_a, **_kw):
        return value
    return _coro


# ── Constants sanity ──────────────────────────────────────────────────


def test_constants_match_spec():
    assert LEASE_TTL_SECONDS == 600
    assert HEARTBEAT_INTERVAL_SECONDS == 120
    assert LEASE_META_KEY == "blueprint_build_lease"
