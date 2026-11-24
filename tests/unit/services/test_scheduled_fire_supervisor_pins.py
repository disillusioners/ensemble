"""Scheduled-fire supervisor pins (Addendum A.4, SCOPE b3).

Every disposition is pinned with a fake repo/manager harness — no live DB,
no daemon boot. Kill-switch and lookback are exercised via env monkeypatch.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from daemon.services import scheduled_fire_supervisor as sup


class FakeSourceRepo:
    def __init__(self, candidates: list[dict[str, Any]], claim: str = "claimed") -> None:
        self.candidates = candidates
        self.claim = claim
        self.claimed: list[str] = []
        self.terminalized: list[tuple[str, str]] = []
        self.selection_args: tuple | None = None

    def select_supervisor_candidates(self, stale_before, lookback_cutoff, limit):
        self.selection_args = (stale_before, lookback_cutoff, limit)
        return self.candidates

    def claim_execution_redrive(self, eid, stamp, cap):
        self.claimed.append(eid)
        return self.claim

    def terminalize_execution_redrive_failed(self, eid, reason):
        self.terminalized.append((eid, reason))


class FakeTaskRepo:
    def __init__(self, running: set[str] | None = None) -> None:
        self.running = running or set()

    def find_running_by_instance(self, iid):
        return object() if iid in self.running else None


class FakeInstanceRepo:
    def __init__(self, statuses: dict[str, str] | None = None) -> None:
        self.statuses = statuses or {}

    def get(self, iid):
        status = self.statuses.get(iid)
        if status is None:
            return None
        row = type("Row", (), {})()
        row.status = status
        return row


def _supervisor(repo, *, task_repo=None, instance_repo=None, callback=None, claim="claimed"):
    if claim != "claimed":
        repo.claim = claim
    return sup.ScheduledFireSupervisor(
        object(),
        source_repo=repo,
        task_repo=task_repo or FakeTaskRepo(),
        instance_repo=instance_repo or FakeInstanceRepo(),
        redrive_callback=callback or _accept,
    )


async def _accept(candidate):
    return {"status": "pending_redrive", "instance_id": candidate.get("instance_id"),
            "task_id": "task-x"}


@pytest.fixture(autouse=True)
def _kill_switch_on(monkeypatch):
    monkeypatch.setenv(sup.KILL_SWITCH_ENV, "1")
    yield
    monkeypatch.delenv(sup.KILL_SWITCH_ENV, raising=False)


# ---------------------------------------------------------------------------
# Supervisor pins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_never_started_redriven_row_to_dispatched():
    repo = FakeSourceRepo([{"execution_id": "e1", "schedule_id": "s",
                            "instance_id": None, "status": "never-started"}])
    out = await _supervisor(repo).sweep_once()
    assert out.redriven == 1 and out.errors == 0
    assert repo.claimed == ["e1"]   # CAS advanced the row to dispatched


@pytest.mark.asyncio
async def test_failed_to_dispatch_redriven():
    repo = FakeSourceRepo([{"execution_id": "e2", "schedule_id": "s",
                            "instance_id": None, "status": "failed-to-dispatch"}])
    out = await _supervisor(repo).sweep_once()
    assert out.redriven == 1 and repo.claimed == ["e2"]


@pytest.mark.asyncio
async def test_dispatched_stale_recovered_found_task_on_instance():
    repo = FakeSourceRepo([{"execution_id": "e3", "schedule_id": "s",
                            "instance_id": "inst-3", "status": "dispatched"}])
    out = await _supervisor(repo).sweep_once()
    assert out.recovered == 1 and out.redriven == 0
    assert repo.claimed == ["e3"]


@pytest.mark.asyncio
async def test_slow_start_task_processing_not_redriven():
    repo = FakeSourceRepo([{"execution_id": "e4", "schedule_id": "s",
                            "instance_id": "inst-4", "status": "dispatched"}])
    out = await _supervisor(repo, task_repo=FakeTaskRepo(running={"inst-4"})).sweep_once()
    assert out.skipped_processing == 1
    assert out.redriven == 0 and out.recovered == 0
    assert repo.claimed == []   # never CAS-stamped a slow start


@pytest.mark.asyncio
async def test_terminal_instance_failed_loud_no_resurrection():
    repo = FakeSourceRepo([{"execution_id": "e5", "schedule_id": "s",
                            "instance_id": "inst-5", "status": "dispatched"}])
    out = await _supervisor(
        repo, instance_repo=FakeInstanceRepo({"inst-5": "terminated"})
    ).sweep_once()
    assert out.terminal_dead_lineage == 1
    assert repo.terminalized and "dead_lineage" in repo.terminalized[0][1]
    assert repo.claimed == []   # never re-driven a dead instance


@pytest.mark.asyncio
async def test_redrive_cap_loud_terminal_no_loop():
    repo = FakeSourceRepo([{"execution_id": "e6", "schedule_id": "s",
                            "instance_id": None, "status": "never-started"}], claim="cap-exceeded")
    out = await _supervisor(repo).sweep_once()
    assert out.terminal_cap == 1
    assert repo.terminalized and "redrive_cap" in repo.terminalized[0][1]
    assert out.redriven == 0


@pytest.mark.asyncio
async def test_kill_switch_off_zero_actions(monkeypatch):
    monkeypatch.setenv(sup.KILL_SWITCH_ENV, "0")
    repo = FakeSourceRepo([{"execution_id": "e7", "schedule_id": "s",
                            "instance_id": None, "status": "never-started"}])
    out = await _supervisor(repo).sweep_once()
    assert out.skipped_kill_switch == 1 and out.candidates == 0
    assert repo.claimed == [] and repo.selection_args is None


@pytest.mark.asyncio
async def test_lookback_bound_legacy_rows_untouched(monkeypatch):
    """The selector is always called with a recency cutoff — legacy rows that
    fall outside it are excluded by the repo query, not re-driven en masse."""
    monkeypatch.setenv(sup.LOOKBACK_ENV, "24")
    repo = FakeSourceRepo([])
    out = await _supervisor(repo).sweep_once()
    assert out.candidates == 0
    stale_before, lookback_cutoff, limit = repo.selection_args
    # The deadline (now-180s) is NEWER than the lookback floor (now-24h), so
    # rows older than the lookback are excluded by the selector.
    assert stale_before > lookback_cutoff
    assert limit == sup.DEFAULT_BATCH_LIMIT


@pytest.mark.asyncio
async def test_refused_redrive_is_surfaced_not_silent():
    async def refuse(candidate):
        return {"status": "refused", "error": "no_work"}

    repo = FakeSourceRepo([{"execution_id": "e8", "schedule_id": "s",
                            "instance_id": None, "status": "never-started"}])
    out = await _supervisor(repo, callback=refuse).sweep_once()
    assert out.skipped_redrive_refused == 1 and out.redriven == 0
