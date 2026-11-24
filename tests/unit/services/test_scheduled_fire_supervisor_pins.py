"""Scheduled-fire supervisor pins (Addendum A.4, SCOPE b3).

Every disposition is pinned with a fake repo/manager harness — no live DB,
no daemon boot. Kill-switch and lookback are exercised via env monkeypatch.

THE BLOCKER PINS (Addendum A.4 remediation pass) are fail-honest: each one
REDs against the pre-remediation HEAD (049424873) and GREENs after:

* **B1** — the REAL api.py re-drive callback (the module-level factory the
  lifespan wiring passes to the supervisor) is driven with a manager whose
  F2 methods carry the REAL production signatures. The old call site used
  the KEYWORD-ONLY ``_schedule_explicit_handle_resume`` positionally → a
  real ``TypeError`` on EVERY attempt → the leg never repaired anything.
* **B2** — a claimed-then-REFUSED row must NOT rest at ``dispatched`` with
  a NULL instance handle (the CAS is COUNT-ONLY; the status advances only
  after a successful re-drive).
* **B3** — the adapter's execution semaphore value is IDENTICAL before and
  after a supervisor re-dispatch (the cap is never inflated).
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.source.models import ExecutionStatus
from daemon.repositories.source.repository import SQLModelSourceRepository
from daemon.services import scheduled_fire_supervisor as sup


class FakeSourceRepo:
    def __init__(self, candidates: list[dict[str, Any]], claim: str = "claimed") -> None:
        self.candidates = candidates
        self.claim = claim
        self.claimed: list[str] = []
        self.terminalized: list[tuple[str, str]] = []
        self.selection_args: tuple | None = None
        self.advanced: list[tuple[str, str | None, str | None]] = []

    def select_supervisor_candidates(self, stale_before, lookback_cutoff, limit):
        self.selection_args = (stale_before, lookback_cutoff, limit)
        return self.candidates

    def claim_execution_redrive(self, eid, stamp, cap):
        self.claimed.append(eid)
        return self.claim

    def terminalize_execution_redrive_failed(self, eid, reason):
        self.terminalized.append((eid, reason))

    def advance_execution_redrive_dispatched(self, eid, instance_id, *, outcome=None):
        self.advanced.append((eid, instance_id, outcome))
        return object()


class FakeTaskRepo:
    def __init__(self, running: set[str] | None = None) -> None:
        self.running = running or set()

    def find_running_by_instance(self, iid):
        return object() if iid in self.running else None

    def get_by_work_id(self, work_id):
        return None


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


# ---------------------------------------------------------------------------
# BLOCKER 1 — the REAL api.py re-drive callback path
# ---------------------------------------------------------------------------


def _load_api_redrive_factory():
    """Import the REAL callback factory from ``daemon.api``.

    A clean FAIL (not an ImportError at collection time) when the module
    does not expose it — the pin then says what production must expose.
    """
    try:
        from daemon.api import _make_scheduled_fire_redrive
    except ImportError as exc:  # pragma: no cover — the pre-fix shape
        pytest.fail(
            "daemon.api must expose the scheduled-fire re-drive callback "
            f"factory so the REAL callback path can be pinned: {exc}"
        )
    return _make_scheduled_fire_redrive


class _RealSignatureManager:
    """A manager whose F2 methods carry the REAL production signatures.

    ``_schedule_explicit_handle_resume`` is bound FROM the production
    class: it is KEYWORD-ONLY and carries FOUR MORE required kwargs, so
    the old positional call raised the REAL ``TypeError`` (not a simulated
    one). ``resume_processing_job`` is the F2 entry the callback must use.
    """

    def __init__(self) -> None:
        from daemon.manager import InstanceManager

        self.calls: list[dict[str, Any]] = []

        class _Holder:
            pass

        _Holder._schedule_explicit_handle_resume = (
            InstanceManager._schedule_explicit_handle_resume
        )
        self._schedule_explicit_handle_resume = (
            _Holder._schedule_explicit_handle_resume.__get__(self)
        )

    async def resume_processing_job(
        self, instance_id, message="resume", silent=False, images=None, *, image_refs=None
    ):
        self.calls.append(
            {
                "instance_id": instance_id,
                "message": message,
                "silent": silent,
                "images": images,
            }
        )
        return {
            "instance_id": instance_id,
            "job_id": "job-1",
            "message_id": None,
            "status": "pending_redrive",
            "task_id": 7,
        }


@pytest.mark.asyncio
async def test_blocker1_real_api_callback_drives_the_f2_entry():
    """B1 — the instance-bearing re-drive leg NEVER worked.

    The callback called the KEYWORD-ONLY ``_schedule_explicit_handle_resume``
    with POSITIONAL args (four required kwargs missing) → a real
    ``TypeError`` on EVERY attempt → the supervisor logged
    ``[SCHEDULED_SUPERVISOR_REDRIVE_REFUSED]`` and repaired nothing.

    The pin drives the REAL api.py callback (the module-level factory the
    lifespan wiring passes to the supervisor) and asserts the F2 entry is
    called with the correct signature.
    """
    mgr = _RealSignatureManager()

    # The premise, proven against the REAL signature: the old call shape
    # cannot bind (this is the TypeError the old call site hit).
    from daemon.manager import InstanceManager

    params = inspect.signature(
        InstanceManager._schedule_explicit_handle_resume
    ).parameters
    kwonly = [
        name
        for name, p in params.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY and name != "self"
    ]
    assert len(kwonly) >= 4, (
        "the premise: the F2 internal entry is keyword-only with several "
        f"required kwargs — got {kwonly!r}"
    )
    with pytest.raises(TypeError):
        await mgr._schedule_explicit_handle_resume(
            "inst-1", message="", silent=True, images=None
        )

    # The REAL callback path (the module-level factory the lifespan wiring
    # passes to the supervisor).
    factory = _load_api_redrive_factory()
    cb = factory(mgr)

    out = await cb(
        {"execution_id": "e-b1", "schedule_id": "s-1", "instance_id": "inst-1"}
    )

    # The callback must use the F2 ENTRY (resume_processing_job) — and the
    # outcome must be a truthful re-drive, not a refusal.
    assert out.get("status") == "pending_redrive", (
        f"the F2 leg must actually re-drive — got {out!r}"
    )
    assert mgr.calls == [
        {"instance_id": "inst-1", "message": "", "silent": True, "images": None}
    ], (
        "the F2 entry must be called with the silent-resume signature "
        f"(got {mgr.calls!r})"
    )


@pytest.mark.asyncio
async def test_blocker1_no_task_found_is_a_truthful_refusal():
    """A missing durable task is a REFUSAL — never a fake dispatch."""
    factory = _load_api_redrive_factory()

    class _NoTaskManager:
        async def resume_processing_job(self, instance_id, message="resume",
                                        silent=False, images=None, *, image_refs=None):
            return None

    cb = factory(_NoTaskManager())
    out = await cb({"execution_id": "e-b1b", "schedule_id": "s-1",
                    "instance_id": "inst-1"})
    assert out.get("status") == "refused"
    assert out.get("error") == "no_task_found"


# ---------------------------------------------------------------------------
# BLOCKER 2 — the CAS is COUNT-ONLY; the status advances only on success
# ---------------------------------------------------------------------------


@pytest.fixture()
def receipt_repo(tmp_path):
    """A FILE-backed SQLite source repo — the REAL receipt writes."""
    engine = create_engine(f"sqlite:///{tmp_path / 'supervisor_receipts.db'}")
    SQLModel.metadata.create_all(engine)
    return SQLModelSourceRepository(engine=engine)


def test_blocker2_claimed_refused_row_keeps_its_truthful_status(receipt_repo):
    """B2 — the CAS must NOT manufacture a false ``dispatched``.

    The old CAS stamped ``status='dispatched'`` BEFORE the re-drive was
    attempted: a claimed-then-refused row rested at ``dispatched`` with a
    NULL instance handle (A.1: ``dispatched`` means a turn started).
    """
    receipt_repo.record_execution_never_started(
        "sched-b2", "exec-b2", "no turn began"
    )
    before = receipt_repo.get_latest_execution("sched-b2")
    assert before.status == ExecutionStatus.NEVER_STARTED.value
    assert before.instance_id is None

    claim = receipt_repo.claim_execution_redrive(
        "exec-b2", "2026-11-24T00:00:00+00:00", 2
    )
    assert claim == "claimed"

    after = receipt_repo.get_latest_execution("sched-b2")
    assert after.status == ExecutionStatus.NEVER_STARTED.value, (
        "a claimed row keeps its truthful status until a re-drive dispatches "
        f"— got {after.status!r}"
    )
    assert after.instance_id is None, (
        "a claimed row never carries a handle it did not earn"
    )
    assert after.redrive_count == 1
    assert after.redriven_at == "2026-11-24T00:00:00+00:00"


def test_blocker2_status_advances_only_after_a_successful_redrive(receipt_repo):
    """The successful re-drive advances the row — with a NON-NULL handle."""
    receipt_repo.record_execution_never_started(
        "sched-b2c", "exec-b2c", "no turn began"
    )
    receipt_repo.claim_execution_redrive(
        "exec-b2c", "2026-11-24T00:00:00+00:00", 2
    )
    row = receipt_repo.advance_execution_redrive_dispatched(
        "exec-b2c", "inst-b2c"
    )
    assert row is not None
    assert row.status == ExecutionStatus.DISPATCHED.value
    assert row.instance_id == "inst-b2c"
    assert row.task_id is None and row.job_item_id is None


def test_blocker2_advance_refuses_a_null_instance_handle(receipt_repo):
    """A.1 — no path may leave ``dispatched`` with a NULL instance_id."""
    receipt_repo.record_execution_never_started(
        "sched-b2d", "exec-b2d", "no turn began"
    )
    receipt_repo.claim_execution_redrive(
        "exec-b2d", "2026-11-24T00:00:00+00:00", 2
    )
    receipt_repo.advance_execution_redrive_dispatched("exec-b2d", None)
    row = receipt_repo.get_latest_execution("sched-b2d")
    assert row.status == ExecutionStatus.NEVER_STARTED.value, (
        "the advance must refuse a handle-less dispatch — the row keeps its "
        f"truthful status (got {row.status!r})"
    )
    assert row.instance_id is None


@pytest.mark.asyncio
async def test_blocker2_supervisor_advances_only_on_success():
    """The supervisor advances the row ONLY when the re-drive succeeded."""
    async def accept_with_handle(candidate):
        # The adapter's re-dispatch names the instance it dispatched to.
        return {
            "status": "redriven",
            "instance_id": candidate.get("instance_id") or "inst-new",
        }

    repo = FakeSourceRepo([{"execution_id": "e9", "schedule_id": "s",
                            "instance_id": None, "status": "never-started"}])
    out = await _supervisor(repo, callback=accept_with_handle).sweep_once()
    assert out.redriven == 1
    assert repo.advanced == [("e9", "inst-new", None)], (
        "a successful re-drive advances the row with the handle it earned — "
        f"got {repo.advanced!r}"
    )

    # A REFUSED re-drive advances nothing (the prior status stays).
    async def refuse(candidate):
        return {"status": "refused", "error": "no_work"}

    repo2 = FakeSourceRepo([{"execution_id": "e10", "schedule_id": "s",
                             "instance_id": None, "status": "never-started"}])
    out2 = await _supervisor(repo2, callback=refuse).sweep_once()
    assert out2.skipped_redrive_refused == 1
    assert repo2.advanced == [], (
        "a refused re-drive must not advance the row to 'dispatched'"
    )


@pytest.mark.asyncio
async def test_blocker2_handle_less_dispatch_is_never_advanced():
    """A re-drive that names NO instance handle never advances the row."""
    async def accept_without_handle(candidate):
        return {"status": "redriven"}   # no instance_id — nothing was proven

    repo = FakeSourceRepo([{"execution_id": "e11", "schedule_id": "s",
                            "instance_id": None, "status": "never-started"}])
    out = await _supervisor(repo, callback=accept_without_handle).sweep_once()
    assert out.redriven == 1
    assert repo.advanced == [], (
        "a 'dispatched' receipt must never be written without the instance "
        "handle (A.1) — got "
        f"{repo.advanced!r}"
    )


# ---------------------------------------------------------------------------
# BLOCKER 3 — the concurrency cap is never inflated by a re-dispatch
# ---------------------------------------------------------------------------


def _semaphore_value(sem: Any) -> int:
    """The semaphore's free-slot count (the cap's current headroom)."""
    value = getattr(sem, "_value", None)
    if value is None:  # pragma: no cover — defensive
        pytest.fail("the adapter's execution semaphore exposes no _value")
    return value


def _adapter_with_semaphore(monkeypatch, source_id: str = "sched-b3"):
    """A REAL SchedulerAdapter with a live execution semaphore."""
    from unittest.mock import MagicMock

    from daemon.sources.adapters.scheduler import SchedulerAdapter
    from tests.conftest import make_config

    config = make_config(source_id, {
        "interval_seconds": 3600,
        "agent": "./agents/developer",
        "message": "B3 fire",
    })
    manager = _B3Manager()
    source_repo = MagicMock()
    source_repo.get_instance_mapping = MagicMock(return_value=None)
    adapter = SchedulerAdapter(
        config, _noop_on_message, None, source_repo=source_repo,
        manager=manager,
    )
    adapter._manager = manager
    return adapter, manager


async def _noop_on_message(*args, **kwargs):
    return None


class _B3Manager:
    """A manager whose enqueue lands a durable dispatch."""

    def __init__(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        self.spawn_instance_with_mcp = AsyncMock(return_value="inst-b3")
        self.enqueue_message_job = AsyncMock(
            return_value=MagicMock(
                job_id="job-b3", message_id="msg-b3",
                instance_id="inst-b3", status="queued",
            )
        )

    def get_source_registry(self):
        registry = _B3Registry(self)
        return registry


class _B3Registry:
    def __init__(self, manager: _B3Manager) -> None:
        self._manager = manager

    def get(self, source_id):
        return getattr(self._manager, "_adapter", None)


@pytest.mark.asyncio
async def test_blocker3_redrive_never_inflates_the_adapter_cap(monkeypatch):
    """B3 — the instance-less re-dispatch must not grow the cap.

    The old path called ``adapter._execute_run(...)`` directly: the
    ``finally`` RELEASED a slot that was never acquired, and an
    ``asyncio.Semaphore`` over-release does NOT raise — so the cap grew by
    one per re-drive, forever (the verifier measured 1 → 2).
    """
    adapter, manager = _adapter_with_semaphore(monkeypatch)
    await adapter.start()
    try:
        sem = adapter._execution_semaphore
        assert sem is not None
        before = _semaphore_value(sem)

        out = await adapter.redrive_execution("exec-b3")

        after = _semaphore_value(sem)
        assert after == before, (
            "the execution semaphore value must be IDENTICAL before and "
            f"after a re-dispatch (before={before}, after={after})"
        )
        assert out.get("status") == "redriven", (
            f"the guarded re-dispatch must report the dispatch — got {out!r}"
        )
        assert out.get("instance_id") == "inst-b3"
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_blocker3_execute_run_acquires_the_slot_it_releases(monkeypatch):
    """B3 — ``_execute_run`` balances acquire/release for EVERY caller.

    The over-release proof: a caller that does NOT hold the slot used to
    leave the ``finally``'s release unbalanced — the cap grew by one per
    call (the verifier measured 1 → 2). The default path (no slot held)
    must now acquire its own slot, so the value is IDENTICAL afterwards.
    """
    adapter, manager = _adapter_with_semaphore(monkeypatch)
    await adapter.start()
    try:
        sem = adapter._execution_semaphore
        before = _semaphore_value(sem)
        # No slot held by the caller → _execute_run acquires its own.
        await adapter._execute_run("exec-b3b", "scheduled")
        after = _semaphore_value(sem)
        assert after == before, (
            "an unheld caller must not release a slot it never took "
            f"(before={before}, after={after})"
        )
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_blocker3_supervisor_callback_never_inflates_the_cap(monkeypatch):
    """B3 — the REAL supervisor callback path keeps the cap intact."""
    factory = _load_api_redrive_factory()
    adapter, manager = _adapter_with_semaphore(monkeypatch)
    manager._adapter = adapter
    await adapter.start()
    try:
        sem = adapter._execution_semaphore
        before = _semaphore_value(sem)
        cb = factory(manager)
        out = await cb({"execution_id": "exec-b3c", "schedule_id": "sched-b3",
                        "instance_id": None})
        after = _semaphore_value(sem)
        assert out.get("status") == "redriven", (
            f"the instance-less leg must re-dispatch — got {out!r}"
        )
        assert after == before, (
            "the supervisor's re-dispatch must not inflate the adapter cap "
            f"(before={before}, after={after})"
        )
    finally:
        await adapter.stop()


# ---------------------------------------------------------------------------
# FIX (iv) — resting-'triggered' rows are selectable + terminalize at cap
# ---------------------------------------------------------------------------


def _seed_triggered(repo, execution_id: str, schedule_id: str, triggered_at: str):
    """Seed a legacy ``triggered`` row (the receipt write never landed)."""
    from daemon.repositories.source.models import ScheduleExecution
    from sqlmodel import Session

    with Session(repo.engine) as session:
        session.add(
            ScheduleExecution(
                execution_id=execution_id,
                schedule_id=schedule_id,
                status=ExecutionStatus.TRIGGERED.value,
                triggered_at=triggered_at,
            )
        )
        session.commit()


def test_triggered_rows_join_the_stale_class(receipt_repo):
    """A resting ``triggered`` row past the deadline IS a candidate."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    stale = (now - timedelta(hours=3)).isoformat()
    fresh = (now - timedelta(seconds=5)).isoformat()
    _seed_triggered(receipt_repo, "exec-trg-stale", "sched-trg", stale)
    _seed_triggered(receipt_repo, "exec-trg-fresh", "sched-trg", fresh)

    stale_before = (now - timedelta(seconds=180)).isoformat()
    lookback = (now - timedelta(hours=24)).isoformat()
    rows = receipt_repo.select_supervisor_candidates(
        stale_before, lookback, 50
    )
    ids = {r["execution_id"] for r in rows}
    assert "exec-trg-stale" in ids, (
        "a resting 'triggered' row past the deadline must be selectable — "
        f"got {sorted(ids)!r}"
    )
    assert "exec-trg-fresh" not in ids, (
        "the stale deadline still bounds the selection — a fresh row is not "
        "a candidate"
    )


def test_triggered_rows_are_claimable_and_terminalize_at_the_cap(receipt_repo):
    """The 'triggered' class is re-drive-able and terminalizes LOUDLY."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    stale = (now - timedelta(hours=3)).isoformat()
    _seed_triggered(receipt_repo, "exec-trg-cap", "sched-trg-cap", stale)

    assert receipt_repo.claim_execution_redrive(
        "exec-trg-cap", "2026-11-24T00:00:00+00:00", 2
    ) == "claimed"
    row = receipt_repo.get_latest_execution("sched-trg-cap")
    assert row.status == ExecutionStatus.TRIGGERED.value, (
        "the count-only CAS keeps the truthful status — got "
        f"{row.status!r}"
    )
    # The cap terminalizes the row LOUDLY (never a silent loop).
    assert receipt_repo.claim_execution_redrive(
        "exec-trg-cap", "2026-11-24T00:05:00+00:00", 1
    ) == "cap-exceeded"
    receipt_repo.terminalize_execution_redrive_failed(
        "exec-trg-cap", "redrive_cap: exceeded 1 re-drives"
    )
    row = receipt_repo.get_latest_execution("sched-trg-cap")
    assert row.status == ExecutionStatus.FAILED_TO_DISPATCH.value
    assert "redrive_cap" in (row.redrive_reason or "")
    assert row.completed_at is not None
