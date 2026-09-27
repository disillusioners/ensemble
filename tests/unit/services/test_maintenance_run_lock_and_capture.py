"""Unit tests — Section 1 single-flight gate + summary capture (T3 + T4).

``phase1-backend.md`` §4.2 "Lock & capture" (cases 10–17a):

* the in-process ``MaintenanceRunLock`` is FAIL-FAST (no await-queueing)
  and reports its holder's context (AM-4 — step 1 of the gate);
* the DB-side conditional INSERT is the REAL gate (AM-5): a seeded
  ``running`` row refuses the second insert with NO row written for
  the refused caller (11a, repo half — the service-level 409 body /
  INFO forensics ride the service suite, case 26/32);
* run ids match ``ckpt-YYYYMMDD_HHMMSSffffff-hex8`` (AM-8) and the PK
  collision retry regenerates the hex8 suffix only;
* the auto ``execute()`` wiring: unwired = byte-identical legacy flow
  (INV-1); wired = row lifecycle ``running → succeeded`` with
  ``kind='auto'`` / ``triggered_by='system'`` / ``env_flags_json``;
  gate-held = non-raising skip, NO row, DEBUG with the in-flight
  run_id, registry ``last_run`` re-arms (AM-4 / Focus Area 6);
* Op D summary capture + the manual dry-run's zero-delete contract +
  the 1000-entry ``skipped`` cap (AM-10/AM-15).

The ``maintenance_runs`` repo engine here is a file-backed SQLite
(``create_all`` from the model only — AM-15 makes the audit table
both-driver-legitimate; NO migrations are involved, so this is not the
broken "fresh SQLite boot" path).
"""
from __future__ import annotations

import asyncio
import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.maintenance_runs import (
    MaintenanceRun,
    MaintenanceRunsRepository,
)
from daemon.services.maintenance_run_identity import new_maintenance_run_id
from daemon.services.maintenance_run_lock import (
    MaintenanceRunContext,
    MaintenanceRunLock,
)
from daemon.services.maintenance import (
    CheckpointCleanupJob,
    CheckpointRowPruneSummary,
)
from daemon.services.checkpoint_prune import BlobPruneSummary
from daemon.services.timestamps import now_utc_iso
from daemon.config import PersistenceConfig

RUN_ID_RE = re.compile(r"^ckpt-\d{8}_\d{12}-[0-9a-f]{8}$")


# ── engine fixture ─────────────────────────────────────────────────────────────


@pytest.fixture()
def runs_repo(tmp_path):
    """File-backed SQLite repo with the table + both indexes built via
    ``create_all`` (the AM-15 both-driver path itself)."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'maintenance_runs.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return MaintenanceRunsRepository(engine)


def _ctx(kind: str = "manual_execute") -> MaintenanceRunContext:
    return MaintenanceRunContext(
        run_id=new_maintenance_run_id(),
        kind=kind,
        started_at=now_utc_iso(),
        triggered_by="user",
    )


# ── cases 10 / 11 — the in-process lock ────────────────────────────────────────


class TestMaintenanceRunLock:
    async def test_lock_fail_fast_returns_false_when_held(self):
        """Case 10 — ``wait_for(acquire, 0.1)`` returns False, NOT a
        timeout: the acquire is non-blocking (proven by completion,
        not just speed). [AM-4] the lock is step 1 only — the DB claim
        is asserted separately (11a)."""
        lock = MaintenanceRunLock()
        assert await lock.acquire(_ctx()) is True
        second = await asyncio.wait_for(lock.acquire(_ctx()), timeout=0.1)
        assert second is False

    async def test_lock_in_flight_reports_holder_context(self):
        """Case 11 — the holder's run_id/kind/started_at are visible via
        ``in_flight``; ``None`` after release."""
        lock = MaintenanceRunLock()
        assert lock.in_flight is None
        holder = _ctx(kind="manual_dry_run")
        assert await lock.acquire(holder) is True
        assert lock.in_flight is holder
        assert lock.in_flight.run_id == holder.run_id
        assert lock.in_flight.kind == "manual_dry_run"
        lock.release()
        assert lock.in_flight is None
        # Idempotent release + re-acquire works
        lock.release()
        assert await lock.acquire(_ctx()) is True
        lock.release()

    async def test_lock_release_without_acquire_is_noop(self):
        lock = MaintenanceRunLock()
        lock.release()  # must not raise
        assert lock.in_flight is None
        assert lock.locked() is False


# ── case 11a — the conditional INSERT is the real gate (AM-5) ──────────────────


class TestConditionalInsertConflict:
    def test_conditional_insert_conflict_yields_no_row(self, runs_repo):
        """Case 11a (repo half) — seed a ``running`` row; the second
        insert (same section, ``status='running'``) is refused by the
        partial unique index: ``insert`` returns ``False`` and NO row
        exists for the refused caller. The service maps ``False`` to a
        409 ``run_in_flight`` naming the IN-FLIGHT run (service suite
        cases 26/32 pin the body + the ONE INFO forensics line)."""
        first = MaintenanceRun(
            run_id=new_maintenance_run_id(),
            section="checkpoint-cleanup",
            kind="auto",
            started_at=now_utc_iso(),
            status="running",
            triggered_by="system",
        )
        assert runs_repo.insert(first) is True

        second = MaintenanceRun(
            run_id=new_maintenance_run_id(),
            section="checkpoint-cleanup",
            kind="manual_execute",
            started_at=now_utc_iso(),
            status="running",
            triggered_by="user",
        )
        assert runs_repo.insert(second) is False

        # NO row written for the refused caller — only the in-flight
        # row exists.
        all_rows = runs_repo.list_all()
        assert len(all_rows) == 1
        assert all_rows[0].run_id == first.run_id

        # The conflict source is visible for the 409 body forensics.
        running = runs_repo.get_running("checkpoint-cleanup")
        assert running is not None
        assert running.run_id == first.run_id

    def test_terminal_rows_do_not_conflict(self, runs_repo):
        """AM-6 complement — terminal rows never block: a ``succeeded``
        row coexists with a fresh ``running`` claim."""
        done = MaintenanceRun(
            run_id=new_maintenance_run_id(),
            kind="auto",
            started_at=now_utc_iso(),
            completed_at=now_utc_iso(),
            status="succeeded",
            triggered_by="system",
        )
        assert runs_repo.insert(done) is True
        fresh = MaintenanceRun(
            run_id=new_maintenance_run_id(),
            kind="manual_execute",
            started_at=now_utc_iso(),
            status="running",
            triggered_by="user",
        )
        assert runs_repo.insert(fresh) is True

    def test_pk_collision_retry_regenerates_suffix(self, runs_repo):
        """PK collision (same run_id, terminal statuses so the partial
        index stays out of the way) → hex8 suffix regenerated, prefix
        preserved, retry succeeds."""
        rid = new_maintenance_run_id()
        first = MaintenanceRun(
            run_id=rid,
            kind="auto",
            started_at=now_utc_iso(),
            completed_at=now_utc_iso(),
            status="succeeded",
            triggered_by="system",
        )
        assert runs_repo.insert(first) is True
        dup = MaintenanceRun(
            run_id=rid,
            kind="auto",
            started_at=now_utc_iso(),
            completed_at=now_utc_iso(),
            status="failed",
            triggered_by="system",
        )
        assert runs_repo.insert(dup) is True
        assert dup.run_id != rid
        assert dup.run_id.startswith(rid.rsplit("-", 1)[0])
        assert RUN_ID_RE.match(dup.run_id)


# ── case 12 — run id format (AM-8) ─────────────────────────────────────────────


class TestRunIdFormat:
    def test_run_id_format_matches_contract(self):
        """Case 12 — ``^ckpt-\\d{8}_\\d{12}-[0-9a-f]{8}$`` (colon-free,
        URL-clean, lexicographically sortable)."""
        for _ in range(200):
            rid = new_maintenance_run_id()
            assert RUN_ID_RE.match(rid), rid
        # Lexicographic sortability smoke: two ids minted in order sort
        # in order (the timestamp prefix dominates).
        a = new_maintenance_run_id()
        b = new_maintenance_run_id()
        assert a < b or a.split("-")[1] == b.split("-")[1]


# ── cases 13–15 — the auto-cycle wiring (T3) ───────────────────────────────────


def _mock_adapter(excess=None, all_pairs=None):
    adapter = MagicMock()
    adapter.list_thread_ids = AsyncMock(return_value=[])
    adapter.find_all_thread_ns_pairs = AsyncMock(return_value=all_pairs or [])
    adapter.find_excess_checkpoint_groups = AsyncMock(return_value=excess or [])
    adapter.get_checkpoint_ids = AsyncMock(return_value=["ck-keep"])
    adapter.delete_checkpoints_excluding = AsyncMock(return_value=2)
    adapter.delete_writes_excluding = AsyncMock(return_value=5)
    return adapter


def _wired_job(runs_repo, adapter, lock=None):
    lock = lock or MaintenanceRunLock()
    job = CheckpointCleanupJob(
        config=PersistenceConfig(),
        checkpointer=adapter,
        instance_repo=MagicMock(),
        run_lock=lock,
        runs_repo=runs_repo,
    )
    return job, lock


class TestAutoCycleWiring:
    async def test_execute_unwired_job_legacy_behavior(self):
        """Case 13 — job without lock/repo kwargs: existing ops run, no
        rows anywhere (INV-1 byte-identical legacy flow)."""
        adapter = _mock_adapter()
        job = CheckpointCleanupJob(
            config=PersistenceConfig(),
            checkpointer=adapter,
            instance_repo=MagicMock(),
        )
        result = await job.execute()
        assert result is None
        adapter.find_excess_checkpoint_groups.assert_awaited_once()
        # No runs table at all — nothing to write to; must not raise.

    async def test_execute_auto_row_lifecycle(self, runs_repo):
        """Case 14 — wired job: row ``auto/running → succeeded`` with
        summary; ``triggered_by='system'``; ``env_flags_json`` stamped
        (the raw dual-arm state + ``destructive_override: False``)."""
        adapter = _mock_adapter()
        job, _lock = _wired_job(runs_repo, adapter)
        # [AM-2 counterpart pin, reviewer cheap fix] behavioral twin of
        # the MANUAL-ONLY AST pin: the AUTO path must NEVER route
        # through the manual entry point (INV-1/INV-9).
        job.run_checkpoint_prunes = AsyncMock()  # type: ignore[method-assign]
        await job.execute()
        job.run_checkpoint_prunes.assert_not_called()

        rows = runs_repo.list_all()
        assert len(rows) == 1
        row = rows[0]
        assert row.kind == "auto"
        assert row.triggered_by == "system"
        assert row.status == "succeeded"
        assert row.completed_at is not None
        # env_flags: default env → dry_run arm on, destructive arm off,
        # no override on the auto path (INV-1).
        assert row.env_flags_json == {
            "blob_prune_dry_run": True,
            "blob_prune_destructive": False,
            "destructive_override": False,
        }
        # summary_json carries the FROZEN shape.
        summary = row.summary_json
        assert set(summary.keys()) == {
            "checkpoint_rows",
            "writes",
            "blobs",
            "duration_ms",
        }
        assert summary["checkpoint_rows"]["deleted"] == 0  # no excess staged
        assert summary["blobs"]["destructive"] is False

    async def test_execute_auto_skips_when_gate_held(
        self, runs_repo, caplog
    ):
        """Case 15 — external holder (lock + running row) → no ops, NO
        row (AM-6), DEBUG log carrying the in-flight run_id, and the
        registry re-arms ``last_run`` (non-raising skip; the
        MaintenanceService harness proves the stamping)."""
        adapter = _mock_adapter()
        lock = MaintenanceRunLock()
        holder = _ctx(kind="manual_execute")
        assert await lock.acquire(holder) is True
        # The holder's running row (what a 409 would name).
        assert runs_repo.insert(
            MaintenanceRun(
                run_id=holder.run_id,
                section="checkpoint-cleanup",
                kind=holder.kind,
                started_at=holder.started_at,
                status="running",
                triggered_by="user",
            )
        ) is True

        job, _ = _wired_job(runs_repo, adapter, lock=lock)
        with caplog.at_level(logging.DEBUG, logger="daemon.services.maintenance"):
            await job.execute()  # must NOT raise

        assert holder.run_id in caplog.text
        assert "skipped" in caplog.text
        # No ops ran — the adapter was never asked for excess pairs.
        adapter.find_excess_checkpoint_groups.assert_not_awaited()
        # NO new row: only the holder's row exists.
        rows = runs_repo.list_all()
        assert len(rows) == 1
        assert rows[0].run_id == holder.run_id
        # The lock is still held by the manual run (auto did not steal it).
        assert lock.in_flight is holder

        # Registry semantics (AM-4/Focus Area 6): a non-raising skip
        # lets MaintenanceService._run_pending_jobs stamp the job's
        # ``last_run`` (re-arming the interval — NOT a failure retry).
        # Proven through the real registry harness: register the wired
        # job's execute, force it due + idle, run the pending pass.
        from daemon.services.maintenance import MaintenanceService

        svc = MaintenanceService(check_interval_minutes=1)
        svc.register("checkpoint_cleanup", 24.0, job.execute)
        reg_job = svc._jobs[-1]
        reg_job.last_run = None  # force due
        async def _idle_true():
            return True
        svc._is_idle = _idle_true  # type: ignore[method-assign]
        await svc._run_pending_jobs()
        assert reg_job.last_run is not None, (
            "non-raising gate skip must still re-arm the interval via "
            "the caller's last_run stamp"
        )
        # And still: no second row after the registry pass.
        assert len(runs_repo.list_all()) == 1

    async def test_execute_releases_lock_on_success(self, runs_repo):
        adapter = _mock_adapter()
        job, lock = _wired_job(runs_repo, adapter)
        await job.execute()
        assert lock.in_flight is None

    async def test_execute_releases_lock_on_failure(self, runs_repo):
        """Belt: an infra exception inside the ops still releases the
        lock and marks the row ``failed`` (finalize-in-finally)."""
        adapter = _mock_adapter()
        adapter.find_all_thread_ns_pairs = AsyncMock(
            side_effect=RuntimeError("enumeration exploded")
        )
        # find_all_thread_ns_pairs is ALSO used by Op D (scanned_pairs)
        # → _prune_per_thread_checkpoints swallows internally, so force
        # the failure through an op that raises outward: patch Op A.
        job, lock = _wired_job(runs_repo, adapter)

        async def boom():
            raise RuntimeError("op A exploded")

        job._cleanup_orphaned_threads = boom
        with pytest.raises(RuntimeError):
            await job.execute()
        assert lock.in_flight is None
        row = runs_repo.list_all()[0]
        assert row.status == "failed"
        assert row.error_json["code"] == "execution_error"


# ── cases 16 / 17 / 17a — Op D capture + manual dry-run + skipped cap ─────────


class TestOpDSummaryCapture:
    async def test_prune_per_thread_returns_summary(self):
        """Case 16 — destructive Op D: counters match the delete returns;
        ``excess_pairs`` / ``scanned_pairs`` populated."""
        adapter = _mock_adapter(
            excess=[("t-1", "", 5), ("t-2", "snap:x", 4)],
            all_pairs=[("t-1", "", 5), ("t-2", "snap:x", 4), ("t-3", "", 1)],
        )
        job = CheckpointCleanupJob(
            config=PersistenceConfig(),
            checkpointer=adapter,
            instance_repo=MagicMock(),
        )
        summary = await job._prune_per_thread_checkpoints()
        assert isinstance(summary, CheckpointRowPruneSummary)
        assert summary.scanned_pairs == 3
        assert summary.excess_pairs == 2
        assert summary.deleted_checkpoints == 4  # 2 + 2
        assert summary.deleted_writes == 10  # 5 + 5
        wire = summary.to_summary_dict()
        assert wire == {
            "checkpoint_rows": {
                "scanned_pairs": 3,
                "deleted": 4,
                "excess_pairs": 2,
            },
            "writes": {"deleted": 10},
        }

    async def test_run_checkpoint_prunes_dry_run_has_zero_deletes(self):
        """Case 17 — ``destructive=False``: DELETE sentinels untouched;
        ``would_delete_*`` populated via ``count_writes_excluding``."""
        adapter = _mock_adapter(
            excess=[("t-1", "", 6)],
            all_pairs=[("t-1", "", 6)],
        )
        adapter.count_writes_excluding = AsyncMock(return_value=7)
        adapter.delete_checkpoints_excluding = AsyncMock(
            side_effect=AssertionError("DELETE checkpoints reached")
        )
        adapter.delete_writes_excluding = AsyncMock(
            side_effect=AssertionError("DELETE writes reached")
        )
        adapter.delete_blobs_anti_join = AsyncMock(
            side_effect=AssertionError("DELETE blobs reached")
        )
        # The blob arm iterates find_all_thread_ns_pairs — keep the
        # per-pair scan safe (mock PG shape: the job calls the wrapper,
        # which gates on isinstance; give it a dry summary directly by
        # keeping the adapter a MagicMock that passes as PG via the
        # maintenance module's own import of the gate... simplest:
        # patch the wrapper-level blob arm? No — pin the real path with
        # a sentinel-free adapter and assert no deletes at the end.
        job = CheckpointCleanupJob(
            config=PersistenceConfig(),
            checkpointer=adapter,
            instance_repo=MagicMock(),
        )
        result = await job.run_checkpoint_prunes(destructive=False)
        adapter.count_writes_excluding.assert_awaited_once()
        assert result.rows.would_delete_checkpoints == 3  # 6 - keep-3
        assert result.rows.would_delete_writes == 7
        # No deletes anywhere in the manual dry-run.
        adapter.delete_checkpoints_excluding.assert_not_awaited()
        adapter.delete_writes_excluding.assert_not_awaited()
        # The blob arm ran as a dry-run (no delete_blobs_anti_join).
        adapter.delete_blobs_anti_join.assert_not_awaited()

    async def test_summary_skipped_cap_and_truncation_flag(self):
        """Case 17a — >1000 skipped pairs staged → ``skipped`` truncated
        to 1000 entries + ``skipped_truncated: true`` in the summary
        dict (AM-10/AM-15)."""
        big_skipped = [
            (f"t-{i:05d}", "", "MAX_REFS_EXCEEDED") for i in range(1200)
        ]
        blobs = BlobPruneSummary(
            dry_run=True,
            scanned_pairs=1200,
            skipped=big_skipped,
        )
        from daemon.services.maintenance import CheckpointRunResult

        result = CheckpointRunResult(rows=CheckpointRowPruneSummary(), blobs=blobs)
        d = result.to_summary_dict()
        assert len(d["blobs"]["skipped"]) == 1000
        assert d["blobs"]["skipped_truncated"] is True
        assert d["blobs"]["skipped"][0] == {
            "thread_id": "t-00000",
            "checkpoint_ns": "",
            "reason": "MAX_REFS_EXCEEDED",
        }
        # Under the cap: no truncation flag.
        small = BlobPruneSummary(
            skipped=[("t-1", "", "ZERO_REFS_FAIL_SAFE")] * 3
        )
        d2 = CheckpointRunResult(
            rows=CheckpointRowPruneSummary(), blobs=small
        ).to_summary_dict()
        assert len(d2["blobs"]["skipped"]) == 3
        assert d2["blobs"]["skipped_truncated"] is False
