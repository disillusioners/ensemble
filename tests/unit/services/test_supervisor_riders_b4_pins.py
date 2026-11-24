"""Supervisor riders pins (fix/post-outage-recovery SCOPE b4).

* **NIT-1** — the triggered-class second-delivery window. A fire whose
  delivery LANDED but whose fail-soft ``dispatched``-receipt write was
  LOST rests at ``triggered`` with ``instance_id IS NULL``; the
  supervisor's instance-less leg used to RE-DISPATCH it — a SECOND
  delivery for the same execution (measured enqueues 1→2 on a
  recurring fire, which carries NO idempotency key by design: a
  constant key would collapse the whole schedule — arch §1.3 — and an
  execution-scoped key at the FIRE path was adjudicated OUT for the
  b2 matrix: the masquerade arm of a DONE prior JobItem would still
  re-deliver unless the shared compensation scope in
  ``instance_messaging`` were widened — a change to the surface b2
  just hardened, for a narrower gain than the evidence gate).

  The landed fix is the DELIVERY-EVIDENCE gate: the JobItem rows ARE
  the durable delivery evidence (the fire path stamps
  ``metadata.scheduler.execution_id`` on every scheduled enqueue), so
  an instance-less stale row is probed FIRST:
    - LIVE JobItem (queued/active) ⇒ the delivery is in flight — the
      row is never double-driven (the same protection the
      slow-start check gives instance-bearing rows);
    - DONE JobItem terminalized 'completed' ⇒ the delivery LANDED —
      the receipt is healed to ``executed`` (the truth) and NO second
      delivery is minted;
    - no evidence / dead evidence ⇒ the re-drive IS the recovery
      intent — proceeds, the cap still bounds it;
    - the probe itself failing ⇒ fail-OPEN (loud log) — the gate must
      not blind the lane.

* **NIT-3** — ``asyncio.CancelledError`` scoping. CancelledError is a
  BaseException and escaped BOTH the api re-drive callback's
  ``except Exception`` and the supervisor's per-candidate
  ``except Exception`` — a cancel inside one candidate's re-drive
  aborted the whole sweep. The house discriminator
  (``daemon/mcp/stdio_wrapper.py::_is_caller_cancelled`` /
  ``daemon/mcp/managed_session.py``) splits an INNER cancellation
  (log LOUD, count it, CONTINUE the sweep) from an OUTER cancel
  (shutdown — PROPAGATE, never swallow).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.source.models import ExecutionStatus
from daemon.repositories.source.repository import SQLModelSourceRepository
from daemon.services import scheduled_fire_supervisor as sup


# ---------------------------------------------------------------------------
# Fakes (mirrors test_scheduled_fire_supervisor_pins.py)
# ---------------------------------------------------------------------------


class FakeSourceRepo:
    def __init__(
        self,
        candidates: list[dict[str, Any]],
        claim: str = "claimed",
        *,
        evidence: list[dict[str, Any]] | None = None,
        evidence_raises: Exception | None = None,
    ) -> None:
        self.candidates = candidates
        self.claim = claim
        self.claimed: list[str] = []
        self.terminalized: list[tuple[str, str]] = []
        self.advanced: list[tuple[str, str | None, str | None]] = []
        self.evidence = evidence if evidence is not None else []
        self.evidence_raises = evidence_raises
        self.evidence_queries: list[str] = []
        self.healed: list[tuple[str, str, str | None]] = []

    def select_supervisor_candidates(self, stale_before, lookback_cutoff, limit):
        return self.candidates

    def claim_execution_redrive(self, eid, stamp, cap):
        self.claimed.append(eid)
        return self.claim

    def terminalize_execution_redrive_failed(self, eid, reason):
        self.terminalized.append((eid, reason))

    def advance_execution_redrive_dispatched(self, eid, instance_id, *, outcome=None):
        self.advanced.append((eid, instance_id, outcome))
        return object()

    # ── NIT-1 probe + heal ────────────────────────────────────────────────

    def find_scheduler_delivery_evidence(self, execution_id, lookback_cutoff, limit=5):
        self.evidence_queries.append(execution_id)
        if self.evidence_raises is not None:
            raise self.evidence_raises
        return self.evidence

    def heal_execution_receipt_executed(self, execution_id, outcome, instance_id=None):
        self.healed.append((execution_id, outcome, instance_id))
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


def _instance_less_candidate(status: str = "triggered") -> dict[str, Any]:
    return {
        "execution_id": "exec-nit1",
        "schedule_id": "sched-1",
        "instance_id": None,
        "status": status,
        "redrive_count": 0,
    }


# ---------------------------------------------------------------------------
# NIT-1 — the delivery-evidence gate
# ---------------------------------------------------------------------------


class TestNit1DeliveryEvidenceGate:
    @pytest.mark.asyncio
    async def test_landed_delivery_heals_receipt_no_second_dispatch(self):
        """A landed delivery (a done JobItem terminalized 'completed')
        heals the receipt to ``executed`` and NEVER re-dispatches —
        the measured 1→2 second-delivery defect is closed at ZERO."""
        repo = FakeSourceRepo(
            [_instance_less_candidate()],
            evidence=[
                {
                    "job_id": "job-e1",
                    "instance_id": "inst-1",
                    "admission_state": "done",
                    "terminal_reason": "completed",
                    "created_at": "2026-11-24T00:00:00+00:00",
                },
            ],
        )
        calls: list[dict] = []

        async def cb(candidate):
            calls.append(candidate)
            return {"status": "redriven", "instance_id": "x"}

        out = await _supervisor(repo, callback=cb).sweep_once()
        assert out.healed_receipt == 1, out
        assert out.redriven == 0, out
        assert calls == [], "the re-drive callback MUST NOT fire for a landed delivery"
        assert repo.healed == [("exec-nit1", "completed", "inst-1")]
        # The gate probed the execution exactly once.
        assert repo.evidence_queries == ["exec-nit1"]

    @pytest.mark.asyncio
    async def test_in_flight_delivery_is_never_double_driven(self):
        """A LIVE JobItem (queued/active) for the execution means the
        delivery is in flight — the instance-less leg must refuse
        (the same protection the slow-start check gives
        instance-bearing rows)."""
        repo = FakeSourceRepo(
            [_instance_less_candidate()],
            evidence=[
                {
                    "job_id": "job-live",
                    "instance_id": "inst-live",
                    "admission_state": "active",
                    "terminal_reason": None,
                    "created_at": "2026-11-24T00:00:00+00:00",
                },
            ],
        )
        calls: list[dict] = []

        async def cb(candidate):
            calls.append(candidate)
            return {"status": "redriven", "instance_id": "x"}

        out = await _supervisor(repo, callback=cb).sweep_once()
        assert out.skipped_delivery_in_flight == 1, out
        assert out.redriven == 0 and out.healed_receipt == 0, out
        assert calls == []

    @pytest.mark.asyncio
    async def test_no_evidence_re_drive_proceeds(self):
        """No evidence ⇒ the fire never dispatched — the re-drive IS
        the recovery (the b3 baseline behavior, unchanged)."""
        repo = FakeSourceRepo([_instance_less_candidate()], evidence=[])
        calls: list[dict] = []

        async def cb(candidate):
            calls.append(candidate)
            return {
                "status": "redriven",
                "instance_id": "inst-new",
                "job_item_id": "job-new",
            }

        out = await _supervisor(repo, callback=cb).sweep_once()
        assert out.redriven == 1, out
        assert out.healed_receipt == 0 and out.skipped_delivery_in_flight == 0
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_dead_evidence_re_drive_proceeds(self):
        """Evidence of a NON-completed terminal closure (the fire was
        cancelled/wiped without a completed turn) does NOT heal the
        receipt — the re-drive is the recovery intent and proceeds."""
        repo = FakeSourceRepo(
            [_instance_less_candidate()],
            evidence=[
                {
                    "job_id": "job-dead",
                    "instance_id": "inst-dead",
                    "admission_state": "done",
                    "terminal_reason": "cancelled",
                    "created_at": "2026-11-24T00:00:00+00:00",
                },
            ],
        )
        calls: list[dict] = []

        async def cb(candidate):
            calls.append(candidate)
            return {"status": "redriven", "instance_id": "inst-new"}

        out = await _supervisor(repo, callback=cb).sweep_once()
        assert out.redriven == 1, out
        assert out.healed_receipt == 0, out
        assert repo.healed == []
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_probe_failure_fails_open_under_the_cap(self):
        """The probe raising must not blind the lane: loud log, the
        re-drive proceeds exactly as before the gate existed, and the
        cap still bounds it."""
        repo = FakeSourceRepo(
            [_instance_less_candidate()],
            evidence_raises=RuntimeError("probe down"),
        )
        calls: list[dict] = []

        async def cb(candidate):
            calls.append(candidate)
            return {"status": "redriven", "instance_id": "inst-new"}

        out = await _supervisor(repo, callback=cb).sweep_once()
        assert out.redriven == 1, out
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_instance_bearing_rows_skip_the_gate(self):
        """The gate is instance-less-only: an instance-bearing row
        never pays the probe (its slow-start + dead-lineage legs own
        it)."""
        repo = FakeSourceRepo(
            [{
                "execution_id": "exec-bearing",
                "schedule_id": "sched-1",
                "instance_id": "inst-1",
                "status": "dispatched",
                "redrive_count": 0,
            }],
            evidence=[{"admission_state": "done",
                       "terminal_reason": "completed"}],
        )
        out = await _supervisor(repo).sweep_once()
        assert repo.evidence_queries == [], out
        assert out.recovered + out.redriven >= 1, out


class TestNit1RealSubstrate:
    """The REAL repository probe + heal over hermetic SQLite."""

    @pytest.fixture
    def src_engine(self):
        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(eng)
        yield eng
        eng.dispose()

    @pytest.fixture
    def repo(self, src_engine):
        return SQLModelSourceRepository(src_engine)

    def _seed_job(self, eng, job_id, execution_id, *, admission="done",
                  terminal_reason="completed", created_at="2026-11-24T01:00:00+00:00",
                  deleted_at=None, instance_id="inst-1"):
        import json

        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO job_queue_items
                        (job_id, agent_id, agent_dir, message, source,
                         project_id, queue_id, priority, admission_state,
                         created_at, instance_id, job_type, retry_count,
                         metadata, deleted_at, terminal_reason)
                    VALUES
                        (:job_id, 'leader', 'agents/leader', 'fire', 'scheduler',
                         'proj', NULL, 0, :admission, :created_at, :instance_id,
                         'message', 0, :metadata, :deleted_at, :terminal_reason)
                    """
                ),
                {
                    "job_id": job_id,
                    "admission": admission,
                    "created_at": created_at,
                    "instance_id": instance_id,
                    "metadata": json.dumps(
                        {"scheduler": {"execution_id": execution_id}}
                    ),
                    "deleted_at": deleted_at,
                    "terminal_reason": terminal_reason,
                },
            )

    def test_probe_finds_the_execution_job_item(self, src_engine, repo):
        self._seed_job(src_engine, "job-a", "exec-A")
        rows = repo.find_scheduler_delivery_evidence(
            "exec-A", "2026-11-23T00:00:00+00:00",
        )
        assert len(rows) == 1
        assert rows[0]["job_id"] == "job-a"
        assert rows[0]["admission_state"] == "done"
        assert rows[0]["terminal_reason"] == "completed"

    def test_probe_misses_other_executions(self, src_engine, repo):
        self._seed_job(src_engine, "job-a", "exec-A")
        assert repo.find_scheduler_delivery_evidence(
            "exec-B", "2026-11-23T00:00:00+00:00",
        ) == []

    def test_probe_respects_the_lookback_cutoff(self, src_engine, repo):
        self._seed_job(
            src_engine, "job-old", "exec-OLD",
            created_at="2026-11-01T00:00:00+00:00",
        )
        assert repo.find_scheduler_delivery_evidence(
            "exec-OLD", "2026-11-23T00:00:00+00:00",
        ) == []

    def test_probe_excludes_soft_deleted_rows(self, src_engine, repo):
        self._seed_job(
            src_engine, "job-del", "exec-DEL", deleted_at="2026-11-24T02:00:00+00:00",
        )
        assert repo.find_scheduler_delivery_evidence(
            "exec-DEL", "2026-11-23T00:00:00+00:00",
        ) == []

    def test_probe_is_scheduler_scoped(self, src_engine, repo):
        self._seed_job(src_engine, "job-api", "exec-API")
        import json

        from sqlalchemy import text
        # The same execution id under a NON-scheduler source is NOT
        # scheduler delivery evidence.
        with src_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE job_queue_items SET source = 'api' "
                    "WHERE job_id = 'job-api'"
                ),
            )
        assert repo.find_scheduler_delivery_evidence(
            "exec-API", "2026-11-23T00:00:00+00:00",
        ) == []

    def test_heal_closes_an_open_receipt(self, src_engine, repo):
        from sqlmodel import Session

        from daemon.repositories.source.models import ScheduleExecution
        with Session(src_engine) as session:
            session.add(ScheduleExecution(
                execution_id="exec-H",
                schedule_id="sched-1",
                status=ExecutionStatus.TRIGGERED.value,
                triggered_at="2026-11-24T00:00:00+00:00",
            ))
            session.commit()
        healed = repo.heal_execution_receipt_executed(
            "exec-H", "completed", "inst-9",
        )
        assert healed is not None
        assert healed.status == ExecutionStatus.EXECUTED.value
        assert healed.outcome == "completed"
        assert healed.instance_id == "inst-9"
        assert healed.completed_at is not None

    def test_heal_never_rewrites_a_terminal_receipt(self, src_engine, repo):
        from sqlmodel import Session

        from daemon.repositories.source.models import ScheduleExecution
        with Session(src_engine) as session:
            session.add(ScheduleExecution(
                execution_id="exec-T",
                schedule_id="sched-1",
                status=ExecutionStatus.EXECUTED.value,
                outcome="completed",
                triggered_at="2026-11-24T00:00:00+00:00",
                completed_at="2026-11-24T00:05:00+00:00",
            ))
            session.commit()
        healed = repo.heal_execution_receipt_executed(
            "exec-T", "completed", "inst-9",
        )
        assert healed.status == ExecutionStatus.EXECUTED.value
        assert healed.instance_id is None  # untouched

    def test_healed_receipt_leaves_the_candidate_set(self, src_engine, repo):
        """The loop CLOSES: a healed ('executed') row is neither an
        open-terminal nor a stale-open row — the supervisor never
        selects it again."""
        from sqlmodel import Session

        from daemon.repositories.source.models import ScheduleExecution
        with Session(src_engine) as session:
            session.add(ScheduleExecution(
                execution_id="exec-C",
                schedule_id="sched-1",
                status=ExecutionStatus.TRIGGERED.value,
                triggered_at="2026-11-24T00:00:00+00:00",
            ))
            session.commit()
        repo.heal_execution_receipt_executed("exec-C", "completed", None)
        stale_before = "2026-11-24T12:00:00+00:00"
        lookback = "2026-11-23T00:00:00+00:00"
        candidates = repo.select_supervisor_candidates(
            stale_before, lookback, 200,
        )
        assert [c["execution_id"] for c in candidates] == []


# ---------------------------------------------------------------------------
# NIT-3 — CancelledError scoping
# ---------------------------------------------------------------------------


def _load_api_redrive_factory():
    from daemon.api import _make_scheduled_fire_redrive
    return _make_scheduled_fire_redrive


class TestNit3CancelledErrorScoping:
    @pytest.mark.asyncio
    async def test_inner_cancel_does_not_abort_the_sweep(self):
        """A CancelledError raised INSIDE one candidate's re-drive is
        logged LOUD, counted, and the sweep CONTINUES with the
        remaining candidates."""
        repo = FakeSourceRepo([
            {"execution_id": "e-cancel", "schedule_id": "s",
             "instance_id": "i1", "status": "never-started"},
            {"execution_id": "e-2", "schedule_id": "s",
             "instance_id": "i2", "status": "never-started"},
            {"execution_id": "e-3", "schedule_id": "s",
             "instance_id": "i3", "status": "never-started"},
        ])
        processed: list[str] = []

        async def cb(candidate):
            processed.append(candidate["execution_id"])
            if candidate["execution_id"] == "e-cancel":
                # An INNER cancellation escaping a nested await — this
                # task itself is NOT being cancelled.
                raise asyncio.CancelledError()
            return {"status": "pending_redrive",
                    "instance_id": candidate["instance_id"]}

        out = await _supervisor(repo, callback=cb).sweep_once()
        assert out.skipped_redrive_cancelled == 1, out
        assert out.redriven == 2, out
        assert processed == ["e-cancel", "e-2", "e-3"], processed

    @pytest.mark.asyncio
    async def test_api_callback_returns_refusal_on_inner_cancel(self):
        """The REAL api callback: an inner CancelledError escaping the
        F2 entry (this task NOT cancelled) is returned as a truthful
        refusal — not raised, not swallowed silently (the marker is
        carried in ``error``)."""
        factory = _load_api_redrive_factory()

        class _Mgr:
            async def resume_processing_job(self, *a, **k):
                raise asyncio.CancelledError()

        cb = factory(_Mgr())
        outcome = await cb({
            "execution_id": "exec-x", "instance_id": "inst-x",
        })
        assert outcome["status"] == "refused", outcome
        assert outcome["error"] == "cancelled_in_redrive", outcome

    @pytest.mark.asyncio
    async def test_api_callback_propagates_an_outer_cancel(self):
        """The REAL callback must never swallow a SHUTDOWN cancel: when
        THIS task is cancelled while awaiting the F2 entry, the
        CancelledError propagates (the task ends cancelled)."""
        factory = _load_api_redrive_factory()
        started = asyncio.Event()

        class _Mgr:
            async def resume_processing_job(self, *a, **k):
                started.set()
                await asyncio.sleep(30)

        cb = factory(_Mgr())
        task = asyncio.create_task(
            cb({"execution_id": "exec-x", "instance_id": "inst-x"})
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_adapter_leg_inner_cancel_is_a_refusal(self):
        """The instance-less leg: an inner CancelledError escaping the
        adapter re-dispatch is a truthful refusal, not a raise."""
        factory = _load_api_redrive_factory()

        class _Adapter:
            async def redrive_execution(self, execution_id):
                raise asyncio.CancelledError()

        class _Registry:
            def get(self, schedule_id):
                return _Adapter()

        class _Mgr:
            def get_source_registry(self):
                return _Registry()

        cb = factory(_Mgr())
        outcome = await cb({
            "execution_id": "exec-y", "schedule_id": "sched-y",
            "instance_id": None,
        })
        assert outcome["status"] == "refused", outcome
        assert outcome["error"] == "cancelled_in_redrive", outcome

    @pytest.mark.asyncio
    async def test_caller_cancel_requested_discriminator(self):
        """The house discriminator (stdio_wrapper /
        managed_session pattern): a task WITH a pending cancel request
        reports True; a clean task reports False; no running loop is
        not a crash."""
        assert sup._caller_cancel_requested() is False

        async def _cancelled_probe():
            task = asyncio.current_task()
            task.cancel()  # request, do not deliver
            assert sup._caller_cancel_requested() is True
            # Un-request to leave the loop clean.
            task.uncancel()
            assert sup._caller_cancel_requested() is False

        await _cancelled_probe()

    @pytest.mark.asyncio
    async def test_sweep_level_still_propagates_a_shutdown_cancel(self):
        """The other half of the contract: a REAL shutdown cancel of
        the sweep task is NOT swallowed — the CancelledError reaches
        the caller."""
        repo = FakeSourceRepo([
            {"execution_id": "e1", "schedule_id": "s",
             "instance_id": "i1", "status": "never-started"},
        ])

        async def cb(candidate):
            await asyncio.sleep(30)
            return {"status": "pending_redrive"}

        supervisor = _supervisor(repo, callback=cb)
        task = asyncio.create_task(supervisor.sweep_once())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
