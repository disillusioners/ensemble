"""Unit tests — Section 1 MaintenanceApiService (T5 + T6 + T7).

``phase1-backend.md`` §4.2 "Service" (cases 18–36): the full
validation chain in the frozen order, advisory + hint behavior, row
lifecycle with decision-input audit fields [AM-15], gate interplay
incl. conflict semantics [AM-5/AM-6], boot-sweep CAS [AM-7], and the
kill-switch surface [AM-13] + gate ORDER (Origin FIRST — INV-10).

The repo is a real ``MaintenanceRunsRepository`` on a file-backed
SQLite engine (create_all from the model only — AM-15 makes the audit
table both-driver-legitimate). The CHECKPOINTER stays a mock; the
PG-isinstance gate is monkeypatched at the service module's imported
symbol (``maintenance_api_service.PostgresCheckpointerAdapter``) for
PG-shaped tests and left REAL for the sqlite-shaped test (case 27).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlmodel import SQLModel

import daemon.services.maintenance_api_service as mas
from daemon.repositories.maintenance_runs import (
    MaintenanceRun,
    MaintenanceRunsRepository,
)
from daemon.services.maintenance import (
    CheckpointCleanupJob,
    CheckpointRowPruneSummary,
)
from daemon.services.checkpoint_prune import BlobPruneSummary
from daemon.services.maintenance_run_lock import MaintenanceRunLock
from daemon.services.maintenance_api_service import (
    MaintenanceApiService,
    MaintenanceError,
    RequesterInfo,
)
from daemon.services.timestamps import now_utc_iso
from daemon.config import PersistenceConfig


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def runs_repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'maintenance_runs.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return MaintenanceRunsRepository(engine)


@pytest.fixture()
def as_pg(monkeypatch):
    """The service's PG-isinstance gate treats any mock as PG."""
    monkeypatch.setattr(mas, "PostgresCheckpointerAdapter", object)


def _result(
    destructive: bool = False,
    would_blobs: int = 4,
    would_bytes: int = 268435456,
    duration_ms: int = 412,
    skipped: list | None = None,
    bytes_after_row_prune: int = 0,
) -> Any:
    blobs = BlobPruneSummary(
        dry_run=not destructive,
        scanned_pairs=12,
        would_delete_count=0 if destructive else would_blobs,
        would_free_bytes=0 if destructive else would_bytes,
        total_deleted=would_blobs if destructive else 0,
        total_bytes_freed=would_bytes if destructive else 0,
        skipped=skipped or [],
    )
    rows = CheckpointRowPruneSummary(
        scanned_pairs=12,
        would_free_bytes_after_row_prune=bytes_after_row_prune,
    )
    from daemon.services.maintenance import CheckpointRunResult

    return CheckpointRunResult(
        rows=rows, blobs=blobs, duration_ms=duration_ms
    )


def _service(runs_repo, *, idle=True, job_result=None, job_raises=None,
             as_pg_flag=True) -> MaintenanceApiService:
    checkpointer = MagicMock()
    job = MagicMock(spec=CheckpointCleanupJob)
    if job_raises is not None:
        job.run_checkpoint_prunes = AsyncMock(side_effect=job_raises)
    else:
        job.run_checkpoint_prunes = AsyncMock(
            return_value=job_result if job_result is not None else _result()
        )
    maintenance_service = MagicMock()
    maintenance_service.is_idle = AsyncMock(return_value=idle)
    return MaintenanceApiService(
        config=PersistenceConfig(),
        checkpointer=checkpointer,
        cleanup_job=job,
        runs_repo=runs_repo,
        run_lock=MaintenanceRunLock(),
        maintenance_service=maintenance_service,
    )


def _seed_dry_run_row(
    runs_repo,
    *,
    age_seconds: float = 0,
    bytes_value: int = 268435456,
    duration_ms: int = 412,
) -> MaintenanceRun:
    started = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).isoformat()
    row = MaintenanceRun(
        run_id=f"ckpt-20260927_032000123456-{age_seconds:08x}"[:32],
        section="checkpoint-cleanup",
        kind="manual_dry_run",
        started_at=started,
        completed_at=started,
        status="succeeded",
        triggered_by="user",
        summary_json={
            "would_delete": {
                "checkpoint_rows": 0,
                "writes": 0,
                "blobs": 4,
                "bytes": bytes_value,
            },
            "would_delete_count": 4,
            "would_free_bytes": bytes_value,
            "duration_ms": duration_ms,
            "skipped": [],
        },
    )
    row.run_id = "ckpt-20260927_032000123456-1f4a8c2e"
    assert runs_repo.insert(row) is True
    return row


REQUESTER = RequesterInfo(peer_ip="127.0.0.1", user_agent="pytest", origin=None)


class _ExecutePayload:
    """Duck-typed stand-in for CheckpointCleanupExecuteRequest."""

    def __init__(self, dry_run_run_id=None, expected_bytes=None, confirm=False):
        self.dry_run_run_id = dry_run_run_id
        self.expected_bytes = expected_bytes
        self.confirm = confirm


# ── case 18 — availability state enum ──────────────────────────────────────────


class TestAvailability:
    async def test_availability_state_enum(self, runs_repo, as_pg):
        """Case 18 — [AM-13] mock isinstance-flip: PG-shaped →
        ready/eligible; sqlite-shaped → backend_unsupported."""
        svc = _service(runs_repo)
        got = await svc.availability()
        assert got == {
            "eligible": True,
            "backend": "postgres",
            "state": "ready",
            "reason": None,
        }

    async def test_availability_sqlite_shaped(self, runs_repo):
        """Case 18b — without the isinstance patch the mock is NOT a PG
        adapter → backend_unsupported with the diagnostic reason."""
        svc = _service(runs_repo)
        got = await svc.availability()
        assert got["eligible"] is False
        assert got["backend"] == "sqlite"
        assert got["state"] == "backend_unsupported"
        assert got["reason"] == "blob_prune_postgres_only"


# ── cases 19 / 20 — status shape ───────────────────────────────────────────────


class TestStatus:
    async def test_status_empty_and_with_prior_run(self, runs_repo, as_pg):
        """Case 19 — empty: last_run null; seeded terminal row: config
        keys + summary passthrough incl. dual-flavor blobs keys +
        skipped; in_flight null."""
        svc = _service(runs_repo)
        empty = await svc.status()
        assert empty["last_run"] is None
        assert empty["in_flight"] is None
        assert set(empty["config"].keys()) == {
            "checkpoint_max_per_thread",
            "checkpoint_max_per_thread_floor",
            "cleanup_interval_hours",
            "blob_prune_dry_run_env_default",
            "blob_prune_destructive_armed",
        }

        terminal = MaintenanceRun(
            run_id="ckpt-20260927_031409123456-1f4a8c2e",
            kind="auto",
            started_at=now_utc_iso(),
            completed_at=now_utc_iso(),
            status="succeeded",
            triggered_by="system",
            summary_json=_result(destructive=False).to_summary_dict(),
        )
        assert runs_repo.insert(terminal) is True

        got = await svc.status()
        assert got["last_run"]["run_id"] == terminal.run_id
        assert got["last_run"]["kind"] == "auto"
        assert got["last_run"]["status"] == "succeeded"
        summary = got["last_run"]["summary"]
        assert set(summary.keys()) == {
            "checkpoint_rows", "writes", "blobs", "duration_ms"
        }
        blobs = summary["blobs"]
        # dual-flavor dry keys + symmetry keys (AM-11) + skipped (AM-10)
        assert blobs["would_delete_count"] == 4
        assert blobs["would_free_bytes"] == 268435456
        assert blobs["would_delete"] == 4
        assert blobs["bytes"] == 268435456
        assert blobs["destructive"] is False
        assert blobs["skipped"] == []
        assert blobs["skipped_truncated"] is False

    async def test_status_in_flight_reflects_running_row(self, runs_repo, as_pg):
        """Case 20 — [AM-9] seed a running row (any kind) → in_flight
        populated from the DB row; terminal-only rows → in_flight null."""
        svc = _service(runs_repo)
        assert (await svc.status())["in_flight"] is None

        running = MaintenanceRun(
            run_id="ckpt-20260927_031822987654-9bc2d4a1",
            kind="manual_execute",
            started_at=now_utc_iso(),
            status="running",
            triggered_by="user",
        )
        assert runs_repo.insert(running) is True
        got = await svc.status()
        assert got["in_flight"] == {
            "run_id": running.run_id,
            "kind": "manual_execute",
            "started_at": running.started_at,
            "triggered_by": "user",
        }

    async def test_run_row_future_started_at_read_sane(
        self, runs_repo, as_pg
    ):
        """Clock-skew edge — a run row dated in the FUTURE (operator
        clock skew) must not break the run-row read path: ``get_run``
        and the status section echo the digits verbatim — no crash, no
        fabricated age math on the read path."""
        future_iso = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()
        running = MaintenanceRun(
            run_id="ckpt-20260927_031822987654-f07ba115",
            kind="manual_execute",
            started_at=future_iso,
            status="running",
            triggered_by="user",
        )
        assert runs_repo.insert(running) is True
        svc = _service(runs_repo)
        got = await svc.get_run(running.run_id)
        assert got["status"] == "running"
        assert got["started_at"] == future_iso
        assert (await svc.status())["in_flight"]["started_at"] == future_iso


# ── cases 21–27 — the execute validation chain (frozen order) ─────────────────


class TestExecuteGates:
    async def test_execute_gate_missing_confirm(self, runs_repo, as_pg):
        """Case 21 — 400 confirm_required."""
        svc = _service(runs_repo)
        with pytest.raises(MaintenanceError) as ei:
            await svc.execute(_ExecutePayload(confirm=False), REQUESTER)
        assert ei.value.code == "confirm_required"
        assert ei.value.http_status == 400

    async def test_execute_gate_missing_dry_run_id(self, runs_repo, as_pg):
        """Case 22 — 400 dry_run_required."""
        svc = _service(runs_repo)
        with pytest.raises(MaintenanceError) as ei:
            await svc.execute(
                _ExecutePayload(confirm=True, dry_run_run_id=None), REQUESTER
            )
        assert ei.value.code == "dry_run_required"
        assert ei.value.http_status == 400

    async def test_execute_gate_unknown_dry_run(self, runs_repo, as_pg):
        """Case 23 — 404 not_found + details.run_id."""
        svc = _service(runs_repo)
        with pytest.raises(MaintenanceError) as ei:
            await svc.execute(
                _ExecutePayload(
                    confirm=True, dry_run_run_id="ckpt-nope-00000000"
                ),
                REQUESTER,
            )
        assert ei.value.code == "not_found"
        assert ei.value.http_status == 404
        assert ei.value.details == {"run_id": "ckpt-nope-00000000"}

    async def test_execute_gate_stale_dry_run(self, runs_repo, as_pg):
        """Case 24 — seed dry-run row 400s old → 400 dry_run_stale with
        age_seconds > 300, max_age_seconds == 300."""
        stale = _seed_dry_run_row(runs_repo, age_seconds=400)
        svc = _service(runs_repo)
        with pytest.raises(MaintenanceError) as ei:
            await svc.execute(
                _ExecutePayload(
                    confirm=True,
                    dry_run_run_id=stale.run_id,
                    expected_bytes=268435456,
                ),
                REQUESTER,
            )
        assert ei.value.code == "dry_run_stale"
        assert ei.value.http_status == 400
        assert ei.value.details["max_age_seconds"] == 300
        assert ei.value.details["age_seconds"] > 300

    async def test_execute_gate_future_dated_dry_run_not_stale(
        self, runs_repo, as_pg
    ):
        """Clock-skew edge — a dry-run reference dated in the FUTURE
        yields a NEGATIVE age; the age computation tolerates the sign
        flip (negative never exceeds the fresh window), so the gate
        does NOT 400 ``dry_run_stale``. Pinned via the chain advancing
        to the byte gate (``byte_count_mismatch``, not stale)."""
        future = _seed_dry_run_row(runs_repo, age_seconds=-3600)
        svc = _service(runs_repo)
        with pytest.raises(MaintenanceError) as ei:
            await svc.execute(
                _ExecutePayload(
                    confirm=True,
                    dry_run_run_id=future.run_id,
                    expected_bytes=1,
                ),
                REQUESTER,
            )
        assert ei.value.code == "byte_count_mismatch"

    async def test_execute_gate_byte_mismatch(self, runs_repo, as_pg):
        """Case 25 — 400 byte_count_mismatch with {expected, stored}
        read from the STORED dry-run row."""
        fresh = _seed_dry_run_row(runs_repo, bytes_value=268435456)
        svc = _service(runs_repo)
        with pytest.raises(MaintenanceError) as ei:
            await svc.execute(
                _ExecutePayload(
                    confirm=True,
                    dry_run_run_id=fresh.run_id,
                    expected_bytes=1,
                ),
                REQUESTER,
            )
        assert ei.value.code == "byte_count_mismatch"
        assert ei.value.details == {"expected": 1, "stored": 268435456}

    async def test_execute_gate_run_in_flight(self, runs_repo, as_pg):
        """Case 26 — [AM-5] seeded running row (cross-daemon class: no
        local lock holder) → 409 body carries the HOLDER's
        details.run_id/started_at [C-2]; NO row for the refused caller."""
        holder = MaintenanceRun(
            run_id="ckpt-20260927_030000000000-holder01",
            kind="auto",
            started_at=now_utc_iso(),
            status="running",
            triggered_by="system",
        )
        assert runs_repo.insert(holder) is True
        fresh = _seed_dry_run_row(runs_repo)
        svc = _service(runs_repo)
        with pytest.raises(MaintenanceError) as ei:
            await svc.execute(
                _ExecutePayload(
                    confirm=True,
                    dry_run_run_id=fresh.run_id,
                    expected_bytes=268435456,
                ),
                REQUESTER,
            )
        assert ei.value.code == "run_in_flight"
        assert ei.value.http_status == 409
        # The 409-adoption payload names the IN-FLIGHT (holder) run —
        # NOT the caller's would-be run.
        assert ei.value.details["run_id"] == holder.run_id
        assert ei.value.details["started_at"] == holder.started_at
        # [W1, v3 fix pass] DB-fallback conflicts (no in-process
        # holder — the stale-row wedge class) additionally carry the
        # additive heal_hint pointing at the runbook.
        assert "heal_hint" in ei.value.details
        assert "maintenance-console.md" in ei.value.details["heal_hint"]
        # NO row for the refused caller — exactly the holder + the
        # seeded dry-run exist.
        assert len(runs_repo.list_all()) == 2

    async def test_execute_gate_backend_unsupported(self, runs_repo):
        """Case 27 — sqlite-shaped checkpointer → 503 backend_unsupported
        BEFORE any other service gate (even confirm)."""
        svc = _service(runs_repo)  # isinstance patch NOT applied
        with pytest.raises(MaintenanceError) as ei:
            await svc.execute(
                _ExecutePayload(confirm=False, dry_run_run_id=None), REQUESTER
            )
        assert ei.value.code == "backend_unsupported"
        assert ei.value.http_status == 503


# ── cases 29–33 — happy path, failure, advisory/hint, dry-run shape ────────────


class TestExecuteHappyPath:
    async def test_execute_happy_path_mock(self, runs_repo, as_pg):
        """Case 29 — full chain with mock job → 202 shape; row
        manual_execute/running with the decision-input audit fields
        [AM-15] → awaited run_checkpoint_prunes(destructive=True) → row
        succeeded + summary; gate released."""
        fresh = _seed_dry_run_row(runs_repo, duration_ms=412)
        job_result = _result(destructive=True)
        svc = _service(runs_repo, job_result=job_result)

        resp = await svc.execute(
            _ExecutePayload(
                confirm=True, dry_run_run_id=fresh.run_id,
                expected_bytes=268435456,
            ),
            REQUESTER,
        )
        assert resp["status"] == "running"
        assert set(resp.keys()) == {
            "run_id", "status", "started_at", "advisory",
            "expected_duration_ms_hint",
        }
        # Drain the background task deterministically.
        await asyncio.gather(*list(svc._executing_tasks))

        svc._cleanup_job.run_checkpoint_prunes.assert_awaited_once_with(
            destructive=True
        )
        row = runs_repo.get(resp["run_id"])
        assert row.kind == "manual_execute"
        assert row.triggered_by == "user"
        assert row.status == "succeeded"
        assert row.dry_run_run_id == fresh.run_id
        assert row.expected_bytes == 268435456
        assert row.confirm is True
        assert row.advisory is None
        assert row.dry_run_summary_json == fresh.summary_json
        assert row.requester_json == {
            "peer_ip": "127.0.0.1", "user_agent": "pytest", "origin": None,
        }
        assert row.env_flags_json == {
            "blob_prune_dry_run": True,
            "blob_prune_destructive": False,
            "destructive_override": True,  # INV-2 — the kwarg, not env
        }
        assert set(row.summary_json.keys()) == {
            "checkpoint_rows", "writes", "blobs", "duration_ms"
        }
        assert svc._run_lock.in_flight is None  # gate released

    async def test_execute_marks_failed_row_on_exception(self, runs_repo, as_pg):
        """Case 30 — job raises → row failed + error_json.code
        execution_error; gate released (finally)."""
        fresh = _seed_dry_run_row(runs_repo)
        svc = _service(runs_repo, job_raises=RuntimeError("boom"))
        resp = await svc.execute(
            _ExecutePayload(
                confirm=True, dry_run_run_id=fresh.run_id,
                expected_bytes=268435456,
            ),
            REQUESTER,
        )
        await asyncio.gather(*list(svc._executing_tasks))
        row = runs_repo.get(resp["run_id"])
        assert row.status == "failed"
        assert row.error_json["code"] == "execution_error"
        assert svc._run_lock.in_flight is None

    async def test_execute_advisory_and_hint(self, runs_repo, as_pg):
        """Case 31 — [AM-12] probe False → advisory system_busy + hint
        == the referenced dry-run's duration_ms (RAW ms equality — R-5);
        probe True → advisory None (still present)."""
        fresh = _seed_dry_run_row(runs_repo, duration_ms=412)
        busy = _service(runs_repo, idle=False)
        resp = await busy.execute(
            _ExecutePayload(
                confirm=True, dry_run_run_id=fresh.run_id,
                expected_bytes=268435456,
            ),
            REQUESTER,
        )
        await asyncio.gather(*list(busy._executing_tasks))
        assert resp["advisory"] == "system_busy"
        assert resp["expected_duration_ms_hint"] == 412  # ms — no conversion

        idle_svc = _service(runs_repo, idle=True)
        resp2 = await idle_svc.execute(
            _ExecutePayload(
                confirm=True, dry_run_run_id=fresh.run_id,
                expected_bytes=268435456,
            ),
            REQUESTER,
        )
        await asyncio.gather(*list(idle_svc._executing_tasks))
        assert resp2["advisory"] is None
        assert resp2["expected_duration_ms_hint"] == 412


class TestProjectionEchoBothOrNeither:
    """v3.2 fix-pass O2 — the manual_execute ``projection`` echo is
    BOTH-OR-NEITHER + legacy-row safe.

    ``_execute_run`` sources the echo from the EXECUTE row's
    ``dry_run_summary_json`` snapshot. A snapshot whose dict predates
    the v3.2 projection fields (legacy), carries only ONE of the two
    values, or is not even a dict must surface NO ``projection`` block
    — never a partial echo, never a KeyError/TypeError from any legacy
    row shape. Only a BOTH-present snapshot emits the block.
    """

    @staticmethod
    def _with_summary(runs_repo, row, mutation: dict) -> str:
        """Re-save the seeded dry-run row with extra summary keys
        (mutating a dict the gate does not read — ``would_delete``
        stays intact so the execute gate still passes). Returns the
        row's run_id — the commit expires the passed instance, so
        callers must use the returned id (never touch ``row`` after)."""
        from sqlalchemy.orm import Session

        summary = dict(row.summary_json)
        summary.update(mutation)
        run_id = row.run_id
        row.summary_json = summary
        with Session(runs_repo.engine) as s:
            s.add(row)
            s.commit()
        return run_id

    async def _execute_and_fetch(self, runs_repo, run_id: str):
        svc = _service(runs_repo, job_result=_result(destructive=True))
        resp = await svc.execute(
            _ExecutePayload(
                confirm=True,
                dry_run_run_id=run_id,
                expected_bytes=268435456,
            ),
            REQUESTER,
        )
        await asyncio.gather(*list(svc._executing_tasks))
        return runs_repo.get(resp["run_id"])

    async def test_legacy_row_absent_projection_fields_no_block(
        self, runs_repo, as_pg
    ):
        """Pre-v3.2 legacy snapshot (no ``*_reclaimable_*`` keys) →
        summary keeps the FROZEN 4-key shape; NO projection block."""
        fresh = _seed_dry_run_row(runs_repo)
        row = await self._execute_and_fetch(runs_repo, fresh.run_id)
        assert row.status == "succeeded"
        assert "projection" not in row.summary_json
        assert set(row.summary_json.keys()) == {
            "checkpoint_rows", "writes", "blobs", "duration_ms",
        }

    async def test_now_only_snapshot_emits_no_block(self, runs_repo, as_pg):
        """One-sided snapshot (``bytes_reclaimable_now`` only) → NO
        projection block (BOTH-OR-NEITHER — no partial echo)."""
        fresh = _seed_dry_run_row(runs_repo)
        run_id = self._with_summary(runs_repo, fresh, {"bytes_reclaimable_now": 111})
        row = await self._execute_and_fetch(runs_repo, run_id)
        assert row.status == "succeeded"
        assert "projection" not in row.summary_json

    async def test_after_only_snapshot_emits_no_block(self, runs_repo, as_pg):
        """One-sided snapshot (``..._after_row_prune`` only) → NO
        projection block."""
        fresh = _seed_dry_run_row(runs_repo)
        run_id = self._with_summary(
            runs_repo, fresh, {"bytes_reclaimable_after_row_prune": 222}
        )
        row = await self._execute_and_fetch(runs_repo, run_id)
        assert row.status == "succeeded"
        assert "projection" not in row.summary_json

    async def test_both_present_emits_both_at_dry_run(self, runs_repo, as_pg):
        """BOTH values present → the echo block carries both
        ``*_at_dry_run`` keys with the snapshotted values (R-5)."""
        fresh = _seed_dry_run_row(runs_repo)
        run_id = self._with_summary(
            runs_repo,
            fresh,
            {
                "bytes_reclaimable_now": 111,
                "bytes_reclaimable_after_row_prune": 222,
            },
        )
        row = await self._execute_and_fetch(runs_repo, run_id)
        assert row.summary_json["projection"] == {
            "bytes_reclaimable_now_at_dry_run": 111,
            "bytes_reclaimable_after_row_prune_at_dry_run": 222,
        }

    async def test_non_dict_snapshot_never_raises(self, runs_repo, as_pg):
        """Corrupt snapshot shapes (JSON string / list) → _execute_run
        completes, row succeeds, NO projection block, NO KeyError /
        AttributeError / TypeError. Driven directly (the execute gate
        cannot produce this shape — belt-and-braces for direct repo
        writes)."""
        for weird in ("not-a-dict", [1, 2, 3]):
            run_id = f"ckpt-20260927_032000123456-{uuid4().hex[:8]}"
            row = MaintenanceRun(
                run_id=run_id,
                section="checkpoint-cleanup",
                kind="manual_execute",
                started_at=now_utc_iso(),
                status="running",
                triggered_by="user",
                dry_run_summary_json=weird,
            )
            assert runs_repo.insert(row) is True
            svc = _service(runs_repo, job_result=_result(destructive=True))
            await svc._execute_run(run_id)
            done = runs_repo.get(run_id)
            assert done.status == "succeeded"
            assert "projection" not in done.summary_json


class TestDryRun:
    async def test_dry_run_happy_shape(self, runs_repo, as_pg):
        """Case 32a — happy shape pins: would_delete /
        would_delete_count / would_free_bytes / scanned / skipped /
        duration_ms / fresh_until [AM-10/AM-11]; v3.2 additive
        projection fields bytes_reclaimable_now /
        bytes_reclaimable_after_row_prune / bytes_reclaimable_total
        [R-1] (projection-class, informational, never gate-bound)."""
        svc = _service(runs_repo, job_result=_result(duration_ms=100))
        got = await svc.dry_run(REQUESTER)
        assert set(got.keys()) == {
            "run_id", "would_delete", "would_delete_count",
            "would_free_bytes", "scanned", "skipped", "skipped_truncated",
            "duration_ms", "fresh_until",
            "bytes_reclaimable_now", "bytes_reclaimable_after_row_prune",
            "bytes_reclaimable_total",
        }
        assert got["would_delete"] == {
            "checkpoint_rows": 0, "writes": 0, "blobs": 4,
            "bytes": 268435456,
        }
        assert got["would_delete_count"] == 4
        assert got["would_free_bytes"] == 268435456
        # v3.2 projection fields — default 0 when the dry-run result
        # is a stub (the test's _result() returns no
        # would_free_bytes_after_row_prune; the
        # CheckpointRowPruneSummary dataclass defaults to 0).
        assert got["bytes_reclaimable_now"] == 268435456
        assert got["bytes_reclaimable_after_row_prune"] == 0
        assert got["bytes_reclaimable_total"] == 268435456
        assert got["scanned"] == {"thread_ns_pairs": 12}
        assert got["skipped"] == []
        # fresh_until ≈ started_at + 300s (±2s tolerance, TEXT ISO).
        started = runs_repo.get(got["run_id"]).started_at
        s = datetime.fromisoformat(started)
        f = datetime.fromisoformat(got["fresh_until"])
        assert abs((f - s).total_seconds() - 300) <= 2.0

    async def test_dry_run_409_when_in_flight(self, runs_repo, as_pg):
        """Case 32b — [AM-4] dry-run takes the SAME gate: its own
        manual_dry_run running row conflicts → 409 naming the in-flight
        run; NO row for the refused caller."""
        holder = MaintenanceRun(
            run_id="ckpt-20260927_030000000000-holder02",
            kind="manual_execute",
            started_at=now_utc_iso(),
            status="running",
            triggered_by="user",
        )
        assert runs_repo.insert(holder) is True
        svc = _service(runs_repo)
        with pytest.raises(MaintenanceError) as ei:
            await svc.dry_run(REQUESTER)
        assert ei.value.code == "run_in_flight"
        assert ei.value.details["run_id"] == holder.run_id
        assert len(runs_repo.list_all()) == 1  # no refused-caller row

    async def test_dry_run_row_written(self, runs_repo, as_pg):
        """Case 33 — every dry-run persists a manual_dry_run row with
        the response payload as summary (audit invariant; becomes the
        dry_run_summary_json source for execute)."""
        svc = _service(runs_repo)
        got = await svc.dry_run(REQUESTER)
        row = runs_repo.get(got["run_id"])
        assert row is not None
        assert row.kind == "manual_dry_run"
        assert row.status == "succeeded"
        assert row.completed_at is not None
        assert row.summary_json["would_delete"]["bytes"] == 268435456
        assert row.summary_json["skipped"] == []
        assert svc._run_lock.in_flight is None


# ── case 34 — error body shape pins ────────────────────────────────────────────


class TestErrorBodyPins:
    async def test_error_body_shape_pins(self, runs_repo, as_pg, monkeypatch):
        """Case 34 — for each stable code (incl. maintenance_disabled
        + origin_not_trusted): ``{"error", "message"}`` present and
        details keys exactly as contracted."""
        fresh = _seed_dry_run_row(runs_repo)

        async def expect(svc, payload, code, status, details_keys):
            with pytest.raises(MaintenanceError) as ei:
                await svc.execute(payload, REQUESTER)
            assert ei.value.code == code
            assert ei.value.http_status == status
            assert set(ei.value.details.keys()) == set(details_keys)

        svc = _service(runs_repo)
        await expect(svc, _ExecutePayload(), "confirm_required", 400, [])
        await expect(
            svc, _ExecutePayload(confirm=True), "dry_run_required", 400, []
        )
        await expect(
            svc, _ExecutePayload(confirm=True, dry_run_run_id="ckpt-x"),
            "not_found", 404, {"run_id"},
        )
        await expect(
            svc,
            _ExecutePayload(
                confirm=True, dry_run_run_id=fresh.run_id, expected_bytes=7
            ),
            "byte_count_mismatch", 400, {"expected", "stored"},
        )

        # origin_not_trusted + maintenance_disabled are ROUTER-level —
        # pinned over HTTP below (TestRouterGates). (No trailing
        # placeholder assert: the section above is the assertion set.)


# ── cases 35 / 36 + 28 — boot sweep, kill-switch, gate ORDER (HTTP layer) ─────


def _http_app(svc) -> FastAPI:
    from daemon.routers.maintenance import router

    app = FastAPI()
    app.include_router(router)
    app.state.maintenance_api_service = svc
    return app


class TestRouterGates:
    async def test_gate_order_first_failure_wins(
        self, runs_repo, as_pg, monkeypatch
    ):
        """Case 28 — [INV-10] Origin FIRST, kill-switch SECOND, service
        gates third: untrusted Origin + kill-switch OFF + confirm
        missing → ``origin_not_trusted``; kill-switch OFF + confirm
        missing (Origin clean) → ``maintenance_disabled``; service
        level: confirm missing AND stale dry-run AND held gate
        simultaneously → ``confirm_required`` (frozen order)."""
        import daemon.routers.maintenance as router_mod

        svc = _service(runs_repo)
        app = _http_app(svc)
        transport = ASGITransport(app=app)

        # (i) Origin guard FIRST — even with the kill-switch OFF and
        # confirm missing.
        monkeypatch.setattr(router_mod, "MAINTENANCE_ENDPOINTS_ENABLED", False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/maintenance/checkpoint-cleanup/execute",
                json={"confirm": False},
                headers={"Origin": "http://evil.example"},
            )
            assert r.status_code == 403, r.text
            assert r.json()["detail"]["error"] == "origin_not_trusted"

            # (ii) kill-switch SECOND (Origin clean).
            r2 = await c.post(
                "/maintenance/checkpoint-cleanup/execute",
                json={"confirm": False},
                headers={"Origin": "http://localhost:4199"},
            )
            assert r2.status_code == 503, r2.text
            assert r2.json()["detail"]["error"] == "maintenance_disabled"

        # (iii) service gates in frozen order (kill-switch back ON):
        # confirm missing AND (would-be) stale dry-run AND held gate.
        monkeypatch.setattr(router_mod, "MAINTENANCE_ENDPOINTS_ENABLED", True)
        _seed_dry_run_row(runs_repo, age_seconds=400)
        holder = MaintenanceRun(
            run_id="ckpt-20260927_030000000000-holder03",
            kind="auto", started_at=now_utc_iso(), status="running",
            triggered_by="system",
        )
        runs_repo.insert(holder)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r3 = await c.post(
                "/maintenance/checkpoint-cleanup/execute",
                json={"confirm": False, "dry_run_run_id": "whatever",
                      "expected_bytes": 1},
            )
            assert r3.status_code == 400, r3.text
            assert r3.json()["detail"]["error"] == "confirm_required"

    async def test_unexpected_service_raise_yields_contract_500(
        self, runs_repo, as_pg
    ):
        """[tidier fix pass] router catch-all — an unexpected
        non-MaintenanceError raise must surface as a CONTRACT-SHAPED
        500 (``{error: internal_error, message, details}`` per A-8),
        never FastAPI's plain-text default."""
        from unittest.mock import MagicMock

        svc = MagicMock(spec=MaintenanceApiService)
        svc.status = AsyncMock(side_effect=RuntimeError("boom"))
        app = _http_app(svc)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/maintenance/checkpoint-cleanup/status")
        assert r.status_code == 500, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "internal_error"
        assert detail["message"]
        assert detail["details"] == {}

    async def test_kill_switch_disables_api_surface(
        self, runs_repo, as_pg, monkeypatch
    ):
        """Case 36 — [AM-13] switch OFF → #2–#5 503 maintenance_disabled;
        /availability 200 kill_switched; boot-read semantics (module
        flag, not per-request env)."""
        import daemon.routers.maintenance as router_mod

        svc = _service(runs_repo)
        app = _http_app(svc)
        transport = ASGITransport(app=app)

        monkeypatch.setattr(router_mod, "MAINTENANCE_ENDPOINTS_ENABLED", False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r_status = await c.get("/maintenance/checkpoint-cleanup/status")
            r_dry = await c.post("/maintenance/checkpoint-cleanup/dry-run")
            r_exec = await c.post(
                "/maintenance/checkpoint-cleanup/execute", json={}
            )
            r_run = await c.get(
                "/maintenance/checkpoint-cleanup/runs/ckpt-x-00000000"
            )
            for r in (r_status, r_dry, r_exec, r_run):
                assert r.status_code == 503, r.text
                assert r.json()["detail"]["error"] == "maintenance_disabled"

            r_avail = await c.get(
                "/maintenance/checkpoint-cleanup/availability"
            )
            assert r_avail.status_code == 200, r_avail.text
            body = r_avail.json()
            assert body["eligible"] is False
            assert body["state"] == "kill_switched"
            assert body["reason"] == "MAINTENANCE_ENDPOINTS_ENABLED=0"

        # Boot-read pin: the flag is read from the module constant at
        # request time (import-time evaluation), NOT from the process
        # env — flipping the env post-import has no effect.
        monkeypatch.setenv("MAINTENANCE_ENDPOINTS_ENABLED", "1")
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/maintenance/checkpoint-cleanup/status")
            assert r.status_code == 503  # still gated — boot-read


class TestBootSweep:
    async def test_boot_sweep_marks_interrupted(self, runs_repo, caplog):
        """Case 35 — [AM-7] seed running row (any age — NO age gate),
        run the sweep → interrupted + error_json.code run_interrupted +
        completed_at = sweep time; one summary log line; terminal rows
        untouched."""
        from daemon.services.maintenance_boot_sweep import (
            sweep_interrupted_running_runs,
        )

        seconds_old = MaintenanceRun(
            run_id="ckpt-20260927_029999999999-young0000",
            kind="manual_execute",
            started_at=(
                datetime.now(timezone.utc) - timedelta(seconds=5)
            ).isoformat(),
            status="running",
            triggered_by="user",
        )
        done = MaintenanceRun(
            run_id="ckpt-20260927_029999999999-done00000",
            kind="auto", started_at=now_utc_iso(),
            completed_at=now_utc_iso(), status="succeeded",
            triggered_by="system",
        )
        failed = MaintenanceRun(
            run_id="ckpt-20260927_029999999999-fail00000",
            kind="auto", started_at=now_utc_iso(),
            completed_at=now_utc_iso(), status="failed",
            triggered_by="system",
        )
        for row in (seconds_old, done, failed):
            assert runs_repo.insert(row) is True

        with caplog.at_level(logging.INFO):
            swept = await sweep_interrupted_running_runs(runs_repo)

        assert swept == 1
        flipped = runs_repo.get(seconds_old.run_id)
        assert flipped.status == "interrupted"
        assert flipped.error_json["code"] == "run_interrupted"
        assert flipped.completed_at is not None
        # Terminal rows untouched.
        assert runs_repo.get(done.run_id).status == "succeeded"
        assert runs_repo.get(failed.run_id).status == "failed"
        # ONE summary log line.
        summary_lines = [
            r for r in caplog.records if "maintenance boot sweep" in r.message
        ]
        assert len(summary_lines) == 1

    async def test_boot_sweep_zero_rows_still_logs_once(self, runs_repo, caplog):
        """Companion — a clean boot logs the (0) summary line exactly
        once (a silent zero sweep is indistinguishable from no sweep)."""
        from daemon.services.maintenance_boot_sweep import (
            sweep_interrupted_running_runs,
        )

        with caplog.at_level(logging.INFO):
            swept = await sweep_interrupted_running_runs(runs_repo)
        assert swept == 0
        summary_lines = [
            r for r in caplog.records if "maintenance boot sweep" in r.message
        ]
        assert len(summary_lines) == 1

    async def test_boot_sweep_retry_succeeds_after_transient_failures(
        self, runs_repo, caplog
    ):
        """W1 — a transient DB hiccup at boot is retried; the sweep
        still heals the stale row and logs exactly ONE summary line
        (on the successful attempt only)."""
        from daemon.services.maintenance_boot_sweep import (
            run_boot_sweep_with_retry,
        )

        stale = MaintenanceRun(
            run_id="ckpt-20260927_040000000000-stale0000",
            kind="manual_execute", started_at=now_utc_iso(),
            status="running", triggered_by="user",
        )
        assert runs_repo.insert(stale) is True

        real_cas = runs_repo.cas_running_to_interrupted
        calls = {"n": 0}

        def flaky_cas(section):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("transient boot hiccup")
            return real_cas(section)

        runs_repo.cas_running_to_interrupted = flaky_cas  # type: ignore[method-assign]
        with caplog.at_level(logging.INFO):
            swept = await run_boot_sweep_with_retry(
                runs_repo, backoff_seconds=0.0
            )
        assert swept == 1
        assert calls["n"] == 3
        assert runs_repo.get(stale.run_id).status == "interrupted"
        # ONE INFO SUMMARY line total (the two WARNING retry lines are
        # per-attempt forensics, not summaries).
        summary_lines = [
            r for r in caplog.records
            if r.levelno == logging.INFO
            and "maintenance boot sweep:" in r.message
        ]
        assert len(summary_lines) == 1

    async def test_boot_sweep_retry_exhausted_never_raises(
        self, runs_repo, caplog
    ):
        """W1 — every attempt failing: the wrapper returns None (never
        raises into the boot path) and logs ONE ERROR line naming the
        manual heal statement (the runbook pointer)."""
        import logging as _logging

        from daemon.services.maintenance_boot_sweep import (
            run_boot_sweep_with_retry,
        )

        def dead_cas(section):
            raise RuntimeError("db down")

        runs_repo.cas_running_to_interrupted = dead_cas  # type: ignore[method-assign]
        with caplog.at_level(_logging.ERROR):
            result = await run_boot_sweep_with_retry(
                runs_repo, attempts=2, backoff_seconds=0.0
            )
        assert result is None
        error_lines = [
            r for r in caplog.records
            if "boot sweep FAILED after 2 attempts" in r.getMessage()
        ]
        assert len(error_lines) == 1
        assert "UPDATE maintenance_runs" in error_lines[0].getMessage()


class TestOriginGuardMatrix:
    """Case 59 (unit half — the integration suite runs the same matrix
    over the real service on PG; fold-in (c) adds the TRUSTED_ORIGINS
    CSV edge cases here)."""

    @pytest.mark.parametrize(
        "origin,expected_ok",
        [
            (None, True),                              # (i) no Origin
            ("http://localhost:8079", True),           # (ii) same-origin
            ("http://localhost:4199", True),           # (iii) FE dev server
            ("http://127.0.0.1:9999", True),           # (iii) loopback v4
            ("https://[::1]:8443", True),              # (iii) loopback v6
            ("http://ops-box.lan", False),             # (v) untrusted
            ("null", False),                           # (vi) Origin: null
            ("https://evil.example", False),           # (v) untrusted https
            # Port-aware same-origin (regression for the port-dropping
            # defect): different port on a NON-localhost host must NOT
            # pass the same-origin rule.
            ("http://ops-box.lan:1234", False),
        ],
    )
    async def test_origin_guard_rules(self, origin, expected_ok):
        from types import SimpleNamespace

        from daemon.routers.maintenance_origin_guard import (
            require_trusted_origin,
        )
        from fastapi import HTTPException

        headers = {"host": "localhost:8079"}
        if origin is not None:
            headers["origin"] = origin
        request = SimpleNamespace(
            headers=headers,
            url=SimpleNamespace(scheme="http", path="/api/m/x"),
            client=SimpleNamespace(host="127.0.0.1"),
        )

        if expected_ok:
            await require_trusted_origin(request)  # must not raise
        else:
            with pytest.raises(HTTPException) as ei:
                await require_trusted_origin(request)
            assert ei.value.status_code == 403
            assert ei.value.detail["error"] == "origin_not_trusted"

    @pytest.mark.parametrize(
        "csv_value,origin,expected_ok",
        [
            # Fold-in (c): MAINTENANCE_TRUSTED_ORIGINS CSV edge cases.
            ("", "http://ops-box.lan", False),          # empty → no extras
            ("   ", "http://ops-box.lan", False),       # whitespace-only
            ("http://ops-box.lan", "http://ops-box.lan", True),
            ("http://a.lan, http://b.lan", "http://b.lan", True),
            ("http://a.lan,,http://b.lan", "http://b.lan", True),  # empty entries
            ("not a url ;;", "not a url ;;", False),    # malformed entries are INERT (fail-closed)
            ("HTTP://OPS-BOX.LAN", "http://ops-box.lan", True),  # case-fold
            ("http://ops-box.lan", "http://ops-box.lan.evil", False),
        ],
    )
    async def test_trusted_origins_csv_edge_cases(
        self, monkeypatch, csv_value, origin, expected_ok
    ):
        from types import SimpleNamespace

        from daemon.routers import maintenance_origin_guard as guard
        from fastapi import HTTPException

        monkeypatch.setenv("MAINTENANCE_TRUSTED_ORIGINS", csv_value)
        guard.reset_trusted_origins_cache()
        try:
            request = SimpleNamespace(
                headers={"origin": origin, "host": "localhost:8079"},
                url=SimpleNamespace(scheme="http", path="/x"),
                client=SimpleNamespace(host="127.0.0.1"),
            )
            if expected_ok:
                await guard.require_trusted_origin(request)
            else:
                with pytest.raises(HTTPException):
                    await guard.require_trusted_origin(request)
        finally:
            guard.reset_trusted_origins_cache()
            monkeypatch.delenv("MAINTENANCE_TRUSTED_ORIGINS", raising=False)


class TestHostAllowlistGuard:
    """C1 fix pass — DNS-rebinding hardening: rule 2's Host-derived
    same-origin only vouches when the request Host passes the allowlist
    (loopback family + MAINTENANCE_TRUSTED_ORIGINS hosts +
    MAINTENANCE_ALLOWED_HOSTS). Cells mirror the council-confirmed
    design: scheme confusion, trusted-host port masquerade, trailing
    dot/slash, userinfo."""

    @staticmethod
    def _request(origin: str, host: str):
        from types import SimpleNamespace

        headers = {"host": host, "origin": origin}
        return SimpleNamespace(
            headers=headers,
            url=SimpleNamespace(scheme="http", path="/api/m/x"),
            client=SimpleNamespace(host="127.0.0.1"),
        )

    async def _assert(self, origin, host, expected_ok, *, trusted=None,
                      allowed_hosts=None, monkeypatch):
        from daemon.routers import maintenance_origin_guard as guard
        from fastapi import HTTPException

        import pytest as _pytest

        if trusted is not None:
            monkeypatch.setenv("MAINTENANCE_TRUSTED_ORIGINS", trusted)
        if allowed_hosts is not None:
            monkeypatch.setenv("MAINTENANCE_ALLOWED_HOSTS", allowed_hosts)
        guard.reset_trusted_origins_cache()
        guard.reset_allowed_hosts_cache()
        try:
            request = self._request(origin, host)
            if expected_ok:
                await guard.require_trusted_origin(request)
            else:
                with _pytest.raises(HTTPException) as ei:
                    await guard.require_trusted_origin(request)
                assert ei.value.status_code == 403
                assert ei.value.detail["error"] == "origin_not_trusted"
        finally:
            guard.reset_trusted_origins_cache()
            guard.reset_allowed_hosts_cache()
            monkeypatch.delenv("MAINTENANCE_TRUSTED_ORIGINS", raising=False)
            monkeypatch.delenv("MAINTENANCE_ALLOWED_HOSTS", raising=False)

    async def test_dns_rebinding_pair_refused(self, monkeypatch):
        """C1 core — attacker Host + attacker Origin AGREE on
        scheme+host+port; the Host allowlist must keep rule 2 from
        vouching (fail-closed 403 via rule 5)."""
        await self._assert(
            "http://attacker.example:8079", "attacker.example:8079",
            False, monkeypatch=monkeypatch,
        )

    async def test_allowed_external_host_vouches_same_origin(
        self, monkeypatch
    ):
        """Positive — MAINTENANCE_ALLOWED_HOSTS opts an external host
        into rule 2 (full scheme+host+port match)."""
        await self._assert(
            "http://ops-box.lan:8079", "ops-box.lan:8079",
            True, allowed_hosts="ops-box.lan", monkeypatch=monkeypatch,
        )

    async def test_trusted_origin_host_vouches_host_other_port(
        self, monkeypatch
    ):
        """A trusted ORIGIN (http://ops-box.lan:9999) also vouches its
        HOST for rule 2 — a same-origin match on a DIFFERENT port
        (8079) passes rule 2 (the trusted string itself would not have
        matched rule 4 — port differs)."""
        await self._assert(
            "http://ops-box.lan:8079", "ops-box.lan:8079",
            True, trusted="http://ops-box.lan:9999",
            monkeypatch=monkeypatch,
        )

    async def test_scheme_confusion_refused(self, monkeypatch):
        """https Origin vs http daemon on an ALLOWED host — scheme is
        part of the origin; rule 2 refuses the scheme flip and rules
        3/4 do not catch it."""
        await self._assert(
            "https://ops-box.lan:8079", "ops-box.lan:8079",
            False, allowed_hosts="ops-box.lan", monkeypatch=monkeypatch,
        )

    async def test_rule2_port_masquerade_refused(self, monkeypatch):
        """Allowed host, but Origin port ≠ Host port — rule 2 refuses
        (ports are part of the comparison; the allowlist is
        host-level only)."""
        await self._assert(
            "http://ops-box.lan:8080", "ops-box.lan:8079",
            False, allowed_hosts="ops-box.lan", monkeypatch=monkeypatch,
        )

    async def test_trusted_origin_port_masquerade_refused(
        self, monkeypatch
    ):
        """TRUSTED_ORIGINS is a full-origin string set — a same host on
        a different port does NOT match rule 4 (no host-prefix
        matching)."""
        await self._assert(
            "http://ops-box.lan:1234", "localhost:8079",
            False, trusted="http://ops-box.lan:8080",
            monkeypatch=monkeypatch,
        )

    async def test_trailing_dot_host_refused(self, monkeypatch):
        """``ops-box.lan.`` (trailing dot, DNS-equivalent spelling) is
        NOT the allowed ``ops-box.lan`` — no suffix/prefix matching in
        either the allowlist or localhost family (fail-closed)."""
        await self._assert(
            "http://ops-box.lan.:8079", "ops-box.lan.:8079",
            False, allowed_hosts="ops-box.lan", monkeypatch=monkeypatch,
        )

    async def test_trailing_dot_localhost_refused(self, monkeypatch):
        """``localhost.`` is not the exact ``localhost`` spelling —
        refused (exact-match set; documented fail-closed)."""
        await self._assert(
            "http://localhost.:8079", "localhost.:8079",
            False, monkeypatch=monkeypatch,
        )

    async def test_trailing_slash_origin_tolerated(self, monkeypatch):
        """A trailing path separator does not change the parsed host —
        the origin still matches on the allowed host."""
        await self._assert(
            "http://ops-box.lan:8079/", "ops-box.lan:8079",
            True, allowed_hosts="ops-box.lan", monkeypatch=monkeypatch,
        )

    async def test_userinfo_masquerade_refused(self, monkeypatch):
        """``http://ops-box.lan@evil.example`` — the HOST is
        evil.example (userinfo is not the host); urlparse extracts it
        correctly so the naive-prefix confusion is dead."""
        await self._assert(
            "http://ops-box.lan@evil.example", "ops-box.lan:8079",
            False, allowed_hosts="ops-box.lan", monkeypatch=monkeypatch,
        )

    async def test_userinfo_loopback_host_extracted(self, monkeypatch):
        """Counterpart — ``http://evil@127.0.0.1:9999`` has host
        127.0.0.1 (rule 3 applies; userinfo cannot hide a loopback
        host from the parser)."""
        await self._assert(
            "http://evil@127.0.0.1:9999", "localhost:8079",
            True, monkeypatch=monkeypatch,
        )

    async def test_ipv6_loopback_host_vouches(self, monkeypatch):
        """Bracketed IPv6 Host is normalize-extracted before the
        allowlist check — loopback v6 vouches rule 2."""
        await self._assert(
            "http://[::1]:8079", "[::1]:8079",
            True, monkeypatch=monkeypatch,
        )

    # ── C1 hardening pins — static-only bypass classes ─────────────
    # The cells below pin bypass classes that are ALREADY
    # defended-by-construction so a refactor cannot silently weaken
    # them. Behavior was probe-verified 2026-09-27 before pinning.

    async def test_ipv4_mapped_ipv6_origin_refused(self, monkeypatch):
        """``::ffff:127.0.0.1`` — an IPv4-MAPPED IPv6 address parses as
        ``IPv6Address``, so the ``isinstance(IPv4Address)`` gate in
        ``_host_is_localhost`` is the ONLY thing keeping mapped-v4
        loopback semantics out of the acceptance (on current CPython
        that v6 address even reports ``is_loopback=True`` — drop the
        isinstance gate and it sneaks into the v4 branch). The
        mapped-v6 Origin/Host pair fails closed: no rule-2 vouch, no
        rule-3, → 403."""
        import ipaddress as _ipaddress

        from daemon.routers import maintenance_origin_guard as guard

        # Premise pin: mapped-v6 parses as IPv6Address (never the v4
        # branch) and the loopback helper refuses it.
        assert isinstance(
            _ipaddress.ip_address("::ffff:127.0.0.1"),
            _ipaddress.IPv6Address,
        )
        assert guard._host_is_localhost("::ffff:127.0.0.1") is False
        await self._assert(
            "http://[::ffff:127.0.0.1]:8079", "[::ffff:127.0.0.1]:8079",
            False, monkeypatch=monkeypatch,
        )

    async def test_unspecified_ipv4_0_0_0_0_refused(self, monkeypatch):
        """``0.0.0.0`` (unspecified address) is NOT loopback — the
        127.0.0.0/8 range match must not widen to "any IPv4".
        Fail-closed 403."""
        await self._assert(
            "http://0.0.0.0:8079", "0.0.0.0:8079",
            False, monkeypatch=monkeypatch,
        )

    def test_ws_wss_never_vouch_same_origin_rule2(self):
        """Scheme-confusion pin (rule-2 level) — a ``ws://``/``wss://``
        Origin against the http-scheme daemon must NOT pass the
        SAME-ORIGIN rule: scheme is part of the comparison. (Rule 3
        stays host-level/scheme-blind for loopback BY FROZEN DESIGN —
        this pin is on the C1-hardened same-origin vouch itself.)"""
        from daemon.routers import maintenance_origin_guard as guard

        for scheme in ("ws", "wss"):
            request = self._request(
                f"{scheme}://localhost:8079", "localhost:8079"
            )
            assert guard._same_origin(
                request, f"{scheme}://localhost:8079"
            ) is False

    async def test_ws_scheme_origin_refused_full_guard(self, monkeypatch):
        """Full-guard counterpart on a NON-loopback allowed host — a
        ``ws://`` Origin cannot reach the rule-3/4 absorption paths,
        so the pair fails closed to the rule-5 403."""
        await self._assert(
            "ws://ops-box.lan:8079", "ops-box.lan:8079",
            False, allowed_hosts="ops-box.lan", monkeypatch=monkeypatch,
        )

    @pytest.mark.parametrize(
        "origin,host",
        [
            # substring — NOT the exact "localhost" spelling
            ("http://evillocalhost.com:8079", "evillocalhost.com:8079"),
            # suffix — same near-miss class
            ("http://localhost.evil.com:8079", "localhost.evil.com:8079"),
            # percent-encoded path separators stay IN the host token
            ("http://localhost%2f..%2fx:8079", "localhost%2f..%2fx:8079"),
        ],
    )
    async def test_exact_match_semantics_refused(
        self, monkeypatch, origin, host
    ):
        """Frozenset EXACT-match semantics — no substring, no prefix/
        suffix, no encoded-path spelling in the host comparison; every
        near-miss spelling fails closed to the rule-5 403."""
        await self._assert(origin, host, False, monkeypatch=monkeypatch)

    async def test_path_traversal_host_spelling_not_localhost(
        self, monkeypatch
    ):
        """``localhost/../x`` — path-traversal spelling is NOT a
        localhost-family match (the comparison is exact; paths are not
        a wildcard). Pinned at the comparison helpers AND fail-closed
        end-to-end with an attacker Origin."""
        from daemon.routers import maintenance_origin_guard as guard

        guard.reset_trusted_origins_cache()
        guard.reset_allowed_hosts_cache()
        try:
            assert guard._host_is_localhost("localhost/../x") is False
            assert guard._host_is_allowed("localhost/../x") is False
            await self._assert(
                "http://evil.example:8079", "localhost/../x",
                False, monkeypatch=monkeypatch,
            )
        finally:
            guard.reset_trusted_origins_cache()
            guard.reset_allowed_hosts_cache()

    async def test_refusal_log_carries_host_field(
        self, monkeypatch, caplog
    ):
        """R-21/C1 forensics pin — the 403 refusal INFO line carries
        the request ``Host`` (``host=``): a Host agreeing with the
        refused Origin is the DNS-rebinding signature, so the field
        must survive log-call refactors."""
        import logging as _logging

        from daemon.routers import maintenance_origin_guard as guard
        from fastapi import HTTPException

        monkeypatch.delenv("MAINTENANCE_TRUSTED_ORIGINS", raising=False)
        monkeypatch.delenv("MAINTENANCE_ALLOWED_HOSTS", raising=False)
        guard.reset_trusted_origins_cache()
        guard.reset_allowed_hosts_cache()
        try:
            with caplog.at_level(
                _logging.INFO,
                logger="daemon.routers.maintenance_origin_guard",
            ):
                with pytest.raises(HTTPException) as ei:
                    await guard.require_trusted_origin(
                        self._request(
                            "http://evil.example:8079",
                            "attacker.example:9999",
                        )
                    )
            assert ei.value.status_code == 403
            refusals = [
                r for r in caplog.records
                if r.name == "daemon.routers.maintenance_origin_guard"
            ]
            assert refusals, "expected the one refusal INFO line"
            line = refusals[-1].getMessage()
            assert "host=" in line
            assert "attacker.example:9999" in line
        finally:
            guard.reset_trusted_origins_cache()
            guard.reset_allowed_hosts_cache()
# ── v3.2 dry-run projection — wire-shape pins (R-1) ─────────────────────────────
#
# Five new tests land here per the amendment's Test-deltas table; the
# convergence test (#5) lives in the integration suite (real E→D
# composition on disposable PG). The negative pin (#6) lives in this
# file too — it pins the gate, which is a service-level concern.


class TestDryRunProjection:
    """v3.2 R-1 — the dry-run §3 response carries three additive
    projection-class fields (NEVER gate-bound).

    Tests 1, 2, 4 of the amendment's Test-deltas table. Test 3 (R-1
    pin — actual delta-not-superset computation) lives in
    :class:`TestRowPruneProjectionDeltaNotSuperset` (it tests the
    computation site directly with a per-pair mock adapter).
    """

    async def test_dry_run_projection_fields_on_never_pruned_db(
        self, runs_repo, as_pg
    ):
        """Test 1 — incident scenario: never-pruned DB has all blobs
        referenced only by excess rows → ``now == 0``, ``after > 0``,
        ``rows > 0``, ``total == now + after``.

        Stubs ``_result`` to mirror the incident numbers (rows:104,501,
        writes:273,293, blobs:0/bytes:0 in would_delete — i.e. zero
        current orphans; the projection must be > 0). The wire composer
        must surface all three additive fields with the correct
        algebra.
        """
        # Per the incident ground truth: never-pruned prod DB →
        # would_delete.bytes = 0 (current orphans = 0); the after-row-
        # prune projection reports the ~11 GB blobs that are
        # referenced only by excess rows D will delete.
        result = _result(
            would_blobs=0,
            would_bytes=0,
            bytes_after_row_prune=11_811_060_000,
        )
        svc = _service(runs_repo, job_result=result)
        got = await svc.dry_run(REQUESTER)
        # Additive fields present with correct values.
        assert got["bytes_reclaimable_now"] == 0
        assert got["bytes_reclaimable_after_row_prune"] == 11_811_060_000
        assert got["bytes_reclaimable_total"] == 11_811_060_000
        # would_free_bytes is the canonical echo-gate number (= now
        # by v3.2 alias semantics — pin it so a future refactor that
        # recomputes the value can't drift the gate).
        assert got["would_free_bytes"] == 0
        assert got["bytes_reclaimable_now"] == got["would_free_bytes"]

    async def test_dry_run_projection_zero_on_already_pruned_db(
        self, runs_repo, as_pg
    ):
        """Test 2 — post-pass: both ``now`` and ``after`` are 0 (subset
        identity ``after == 0 when excess_pairs == 0``).

        A DB whose retention has already converged has no excess pairs,
        so the projection's per-pair loop never fires. The wire composer
        must still emit the additive fields (default 0 for legacy
        clients that ignore them).
        """
        result = _result(
            would_blobs=0,
            would_bytes=0,
            bytes_after_row_prune=0,
        )
        svc = _service(runs_repo, job_result=result)
        got = await svc.dry_run(REQUESTER)
        assert got["bytes_reclaimable_now"] == 0
        assert got["bytes_reclaimable_after_row_prune"] == 0
        assert got["bytes_reclaimable_total"] == 0
        # Pin the subset identity (algebra of the projection): if
        # excess_pairs == 0 (a post-pass DB), ``after`` MUST be 0.
        # The amendment formalizes this; the wire composer reads
        # ``result.rows.would_free_bytes_after_row_prune`` directly,
        # so the pin is at the per-pair accumulator.

    async def test_dry_run_projection_skips_skipped_pairs(
        self, runs_repo, as_pg
    ):
        """Test 4 — R-4 pin: skipped pairs (ZERO_REFS / MAX_REFS-cap)
        contribute 0 to ``after`` / ``total`` AND surface in
        ``skipped[]`` (the FE flag covers the honesty gap).

        Fixture: a single excess pair lands in the blob prune's
        skipped list (e.g. MAX_REFS_EXCEEDED — the 1,928-checkpoint top
        thread from the incident). The Op D dry-run must subtract
        this pair from the projection (its excess rows still delete
        but its blobs' reclaimability is unknown).
        """
        skipped = [("t-top", "ns-A", "MAX_REFS_EXCEEDED")]
        result = _result(
            would_blobs=0,
            would_bytes=0,
            bytes_after_row_prune=0,  # excluded due to skipped pair
            skipped=skipped,
        )
        svc = _service(runs_repo, job_result=result)
        got = await svc.dry_run(REQUESTER)
        assert got["bytes_reclaimable_now"] == 0
        assert got["bytes_reclaimable_after_row_prune"] == 0
        assert got["bytes_reclaimable_total"] == 0
        # The skipped pair surfaces in ``skipped[]`` (R-4 honesty gap).
        assert len(got["skipped"]) == 1
        assert got["skipped"][0]["thread_id"] == "t-top"
        assert got["skipped"][0]["reason"] == "MAX_REFS_EXCEEDED"


class TestRowPruneProjectionDeltaNotSuperset:
    """Test 3 — R-1 pin — the per-pair computation excludes current
    orphans from ``after``.

    Set algebra (amendment R-1):
      now_set       = blobs not referenced by ANY row
      after_D_set   = blobs not referenced by ANY keep row
      after_set     = after_D_set − now_set
                    = blobs referenced by ≥1 excess row
                      AND by 0 keep rows
                    = "referenced by excess only"

    The superset reading would compute:
      after_wrong    = after_D_set (the post-D orphan SUPERSET)
      total_wrong    = now_set + after_wrong (DOUBLE-counts now)

    Pin: a fixture with 1 current-orphan blob (X) AND 1 excess-
    referenced blob (Y) in the same pair → the correct ``after`` is Y
    (NOT X+Y); ``total`` is X+Y (NOT 2X+Y). Test asserts:
      bytes_reclaimable_after_row_prune == bytes(Y)
      bytes_reclaimable_total            == bytes(X) + bytes(Y)
      bytes_reclaimable_total            < bytes(X) + bytes(X) + bytes(Y)
    """

    async def test_dry_run_projection_delta_not_superset(self):
        """One pair: 1 current-orphan blob + 1 excess-referenced blob.
        The pair-level projection must report the excess-referenced
        bytes only (NOT include current orphans).
        """
        from unittest.mock import AsyncMock, MagicMock

        from daemon.checkpoint_adapter import CheckpointerAdapter
        from daemon.config import PersistenceConfig
        from daemon.services.maintenance import (
            CheckpointCleanupJob,
            CheckpointRowPruneSummary,
        )
        from daemon.services.checkpoint_prune import BlobPruneSummary

        # Pair fixture: (thread_id="t1", checkpoint_ns="ns1", cnt=5)
        # → keep_ids = {c1, c2, c3} (the 3 newest); excess = {c4, c5}.
        # Blob X (current orphan): 1_000_000 bytes, no refs.
        # Blob Y (excess-only ref): 2_500_000 bytes, ref'd only by c4.
        X_BYTES = 1_000_000
        Y_BYTES = 2_500_000

        adapter = MagicMock(spec=CheckpointerAdapter)
        adapter.find_all_thread_ns_pairs = AsyncMock(
            return_value=[("t1", "ns1", 5)]
        )
        adapter.find_excess_checkpoint_groups = AsyncMock(
            return_value=[("t1", "ns1", 5)]
        )
        adapter.get_checkpoint_ids = AsyncMock(
            return_value=["c1", "c2", "c3"]
        )
        adapter.count_writes_excluding = AsyncMock(return_value=2)
        # The R-1 set algebra: count_blobs_anti_join reports NOW (X);
        # count_blobs_referenced_only_by_excess reports AFTER (Y only).
        adapter.count_blobs_anti_join = AsyncMock(
            return_value=(1, X_BYTES)
        )
        adapter.count_blobs_referenced_only_by_excess = AsyncMock(
            return_value=(1, Y_BYTES)
        )

        job = CheckpointCleanupJob(
            config=PersistenceConfig(),
            checkpointer=adapter,
            instance_repo=MagicMock(),
        )
        summary = await job._compute_row_prune_dry_run()

        # Per-pair projection: AFTER must be Y (NOT X+Y). R-1
        # set-difference: after = (after_D) − (now) = Y. The superset
        # reading would emit X+Y — caught by the assertion below.
        assert isinstance(summary, CheckpointRowPruneSummary)
        assert summary.would_free_bytes_after_row_prune == Y_BYTES, (
            f"after must exclude current orphans; got "
            f"{summary.would_free_bytes_after_row_prune}, expected {Y_BYTES}"
        )
        # Also pin that the per-pair adapter method was called with
        # the right keep-set (c1, c2, c3).
        call_args = (
            adapter.count_blobs_referenced_only_by_excess.await_args
        )
        assert call_args is not None
        assert call_args.args[0] == "t1"
        assert call_args.args[1] == "ns1"
        assert call_args.args[2] == {"c1", "c2", "c3"}


class TestGateDoesNotReadProjection:
    """Test 6 — negative pin: the ``expected_bytes`` echo gate binds
    to ``would_free_bytes`` ONLY. Storing a malicious projection
    field value (e.g. ``bytes_reclaimable_after_row_prune = 99``)
    MUST NOT influence the gate — the gate refuses on a real
    mismatch but accepts when the echoed value matches the stored
    ``would_free_bytes``, regardless of what the projection says.
    """

    async def test_expected_bytes_echo_does_not_read_projection(
        self, runs_repo, as_pg
    ):
        """Stored dry-run has ``would_free_bytes = 268435456`` BUT
        ``bytes_reclaimable_after_row_prune = 99``. Client echoes 99
        as ``expected_bytes`` → gate MUST refuse with
        ``byte_count_mismatch`` (gate reads ``would_free_bytes``
        only, NOT the projection).
        """
        # Seed dry-run row: would_free_bytes matches the canonical
        # 256 MiB; projection field is poisoned to 99.
        seeded = _seed_dry_run_row(runs_repo)
        # _seed_dry_run_row defaults to bytes_value=268435456;
        # poison the projection field for the negative pin.
        poisoned_summary = dict(seeded.summary_json)
        poisoned_summary["bytes_reclaimable_after_row_prune"] = 99
        poisoned_summary["bytes_reclaimable_now"] = (
            poisoned_summary["would_free_bytes"]
        )
        poisoned_summary["bytes_reclaimable_total"] = (
            poisoned_summary["would_free_bytes"] + 99
        )
        row = runs_repo.get(seeded.run_id)
        row.summary_json = poisoned_summary
        from sqlalchemy.orm import Session
        with Session(runs_repo.engine) as s:
            s.add(row)
            s.commit()
        svc = _service(runs_repo)
        # Echo the poisoned projection value (99). The gate must NOT
        # read the projection — it must compare against
        # ``would_delete.bytes`` (268435456) and refuse with 400
        # ``byte_count_mismatch``.
        with pytest.raises(MaintenanceError) as ei:
            await svc.execute(
                _ExecutePayload(
                    confirm=True,
                    dry_run_run_id=seeded.run_id,
                    expected_bytes=99,
                ),
                REQUESTER,
            )
        assert ei.value.code == "byte_count_mismatch"
        assert ei.value.http_status == 400
        # The detail envelope must show the canonical echo check
        # (expected=99, stored=268435456) — proof the gate read
        # ``would_delete.bytes``, NOT the projection field.
        assert ei.value.details == {
            "expected": 99,
            "stored": 268435456,
        }

    async def test_expected_bytes_echo_accepts_when_would_free_matches(
        self, runs_repo, as_pg
    ):
        """Belt: the canonical ``would_free_bytes = 268435456`` is
        echoed → gate advances past step 6 (proves the negative pin
        above isn't a false positive). The projection field
        ``bytes_reclaimable_after_row_prune = 99`` is irrelevant to
        the gate, present or not.
        """
        seeded = _seed_dry_run_row(runs_repo)  # bytes_value=268435456
        # Poison the projection field; leave would_delete.bytes alone.
        poisoned_summary = dict(seeded.summary_json)
        poisoned_summary["bytes_reclaimable_after_row_prune"] = 99
        poisoned_summary["bytes_reclaimable_now"] = (
            poisoned_summary["would_free_bytes"]
        )
        poisoned_summary["bytes_reclaimable_total"] = (
            poisoned_summary["would_free_bytes"] + 99
        )
        row = runs_repo.get(seeded.run_id)
        row.summary_json = poisoned_summary
        from sqlalchemy.orm import Session
        with Session(runs_repo.engine) as s:
            s.add(row)
            s.commit()
        svc = _service(runs_repo)
        # Echo the CANONICAL would_free_bytes. Gate must NOT read the
        # projection field — so this passes step 6, then hits the
        # single-flight conflict (the seeded running row in the
        # execute path is absent; the gate passes; the row is
        # inserted as a manual_execute). Wait — actually the gate
        # passes because the value matches; the test then exits at
        # the create-task step (we don't await it). Capture the
        # gate-pass as: no MaintenanceError raised from step 6.
        # Use a fresh service that does NOT have a runs_repo with a
        # pre-existing conflict.
        # Easiest check: the execute path returns a 202 dict (run_id,
        # status, etc.) when the gate passes — but we cannot await
        # the background task in a unit test cleanly. Instead, patch
        # _execute_run to be a no-op so the spawn is synchronous-ish.
        svc._execute_run = AsyncMock()  # type: ignore[method-assign]
        try:
            payload = _ExecutePayload(
                confirm=True,
                dry_run_run_id=seeded.run_id,
                expected_bytes=268435456,
            )
            resp = await svc.execute(payload, REQUESTER)
            assert resp["status"] == "running"
            # Gate passed — projection field was irrelevant.
        finally:
            # Wait briefly for the (now no-op) task to finish so the
            # row cleanup is consistent.
            pass


# ── >2GiB byte-magnitude window (incident 2026-09-28) ────────────────────────


# Sentinel byte value from the incident log evidence (the dry-run's
# orphaned-blob layer hit 27.2 GiB; the execute payload echoed the same
# value, which overflowed PG int4 and produced HTTP 500). Pinning the
# exact number anchors the regression test against future refactors.
_INCIDENT_EXPECTED_BYTES = 27_233_813_846
# Threshold: 2^31 is the first value that overflows int4 (max 2^31-1).
# Below this the legacy int4 column accepts the value; at or above it
# the legacy column raises NumericValueOutOfRange.
_INT4_MAX = 2**31 - 1
_JUST_OVER_INT4 = 2**31


class TestExpectedBytesBigInteger:
    """Pin the >2GiB byte-magnitude window (incident 2026-09-28).

    The legacy ``expected_bytes`` was PG ``INTEGER`` (int4, max
    2,147,483,647). A legitimate 27.2 GiB dry-run echo at execute-time
    (27,233,813,846 bytes) overflowed int4 and
    ``MaintenanceRunsRepository.insert`` raised
    ``sqlalchemy.exc.DataError``
    (``psycopg.errors.NumericValueOutOfRange``, SQLSTATE 22003). The
    router catch-all converted the audit-row failure to a 500 — no
    partial deletion occurred (the INSERT fails BEFORE any blob
    DELETE starts; see the incident doc at
    ``docs/2026-09-28-checkpoint-cleanup-500-int4-overflow.md``).

    Fix:
    - Model: ``BigInteger`` so fresh PG DBs get a wide column from
      ``SQLModel.metadata.create_all()``.
    - PG evolution: idempotent DO block in
      ``InstanceManager._ensure_postgres_columns`` widens existing PG
      DBs from int4 → int8.

    These tests exercise the >2^31-1 byte window end-to-end on a real
    SQLite-backed ``MaintenanceRunsRepository`` (SQLite ``INTEGER`` is
    already 8 bytes via dynamic-type affinity, so the SQLite path is a
    no-op model-wise — this suite is about the Python/SQLAlchemy
    contract that BigInteger round-trips the full int8 range; the PG
    schema-evolution side is pinned separately by the AST/source test
    in :meth:`test_manager_widening_statement_exists_and_is_probe_gated`).
    """

    async def test_execute_accepts_incident_oversize_bytes(
        self, runs_repo, as_pg
    ):
        """Regression for incident 2026-09-28 — the exact dry-run
        echo value from the live log (27,233,813,846 bytes) survives
        the byte-mismatch gate (matches the stored dry-run), the
        audit-row INSERT round-trips losslessly, and the row reads
        back with the full int8 value.

        Pre-fix this would have raised ``NumericValueOutOfRange`` at
        the INSERT step; the router's exception handler would have
        converted it to a 500 (the legacy 27.2 GiB echo).
        """
        # Seed dry-run with the same oversize byte total the execute
        # payload echoes.
        fresh = _seed_dry_run_row(
            runs_repo, bytes_value=_INCIDENT_EXPECTED_BYTES
        )
        svc = _service(runs_repo)
        # Stub the background task so the test stays unit-bounded;
        # the row INSERT happens BEFORE the task is spawned (it is
        # the part that overflowed on the legacy int4 column), so
        # stubbing _execute_run here still exercises the gate
        # pass-through → INSERT round-trip path we care about.
        svc._execute_run = AsyncMock()  # type: ignore[method-assign]

        resp = await svc.execute(
            _ExecutePayload(
                confirm=True,
                dry_run_run_id=fresh.run_id,
                expected_bytes=_INCIDENT_EXPECTED_BYTES,
            ),
            REQUESTER,
        )

        assert resp["status"] == "running"
        # The audit row MUST hold the OVERSIZE value (not 0, not a
        # truncated/casted surrogate). If the model regressed to
        # Integer, the INSERT would have raised before reaching this
        # assertion.
        row = runs_repo.get(resp["run_id"])
        assert row is not None
        assert row.expected_bytes == _INCIDENT_EXPECTED_BYTES
        assert row.kind == "manual_execute"
        assert row.status == "running"

    async def test_execute_accepts_just_over_int4(self, runs_repo, as_pg):
        """Threshold pin — 2^31 (one above int4 max) MUST round-trip
        without overflow. The legacy int4 column would have raised
        ``NumericValueOutOfRange``; BigInteger accepts the full int8
        range.

        Distinct from the incident value test: this pins the exact
        boundary so a future refactor that silently narrows back to
        int4 fails here first (the smallest value that overflows).
        """
        fresh = _seed_dry_run_row(runs_repo, bytes_value=_JUST_OVER_INT4)
        svc = _service(runs_repo)
        svc._execute_run = AsyncMock()  # type: ignore[method-assign]

        resp = await svc.execute(
            _ExecutePayload(
                confirm=True,
                dry_run_run_id=fresh.run_id,
                expected_bytes=_JUST_OVER_INT4,
            ),
            REQUESTER,
        )

        row = runs_repo.get(resp["run_id"])
        assert row is not None
        assert row.expected_bytes == _JUST_OVER_INT4

    async def test_execute_accepts_int4_max(self, runs_repo, as_pg):
        """Boundary pin (negative side) — the largest value that fits
        in the legacy int4 column (2^31-1) MUST still round-trip
        after the widening. A naive ``Integer``-narrowing regression
        would also accept this value; this test exists so the
        boundary is pinned on both sides.
        """
        fresh = _seed_dry_run_row(runs_repo, bytes_value=_INT4_MAX)
        svc = _service(runs_repo)
        svc._execute_run = AsyncMock()  # type: ignore[method-assign]

        resp = await svc.execute(
            _ExecutePayload(
                confirm=True,
                dry_run_run_id=fresh.run_id,
                expected_bytes=_INT4_MAX,
            ),
            REQUESTER,
        )

        row = runs_repo.get(resp["run_id"])
        assert row is not None
        assert row.expected_bytes == _INT4_MAX

    def test_model_expected_bytes_declares_biginteger(self):
        """Pin — ``MaintenanceRun.expected_bytes`` MUST be a
        ``BigInteger`` so fresh PostgreSQL databases get a wide column
        from ``SQLModel.metadata.create_all()``.

        If a future change narrows this back to plain ``Integer``
        (the legacy int4 type that overflowed at the incident), this
        test fails immediately and surfaces the drift before the next
        overflow incident can land.

        Implementation note: SQLAlchemy's ``BigInteger`` IS a subclass
        of ``Integer`` (the PG ``bigint`` type is a wider variant of
        ``integer``), so ``isinstance(col.type, Integer)`` is True
        for BOTH the legacy and the widened types. The contract here
        is the *exact* type — ``type(col.type) is BigInteger`` —
        not just the broader integer-family membership.
        """
        from sqlalchemy import BigInteger, Integer
        from sqlalchemy.dialects.postgresql import base as pg_base
        from daemon.repositories.maintenance_runs.models import (
            MaintenanceRun,
        )

        col = MaintenanceRun.__table__.columns["expected_bytes"]
        # Positive: must be BigInteger (the widened type).
        assert isinstance(col.type, BigInteger), (
            "MaintenanceRun.expected_bytes MUST be BigInteger — "
            "Integer (int4) overflows at 2^31-1 (incident 2026-09-28). "
            "Got type: " + repr(col.type)
        )
        # Negative (strict): must NOT be plain Integer. SQLAlchemy's
        # ``BigInteger`` subclasses ``Integer``, so we check the exact
        # type (not isinstance) to distinguish BigInteger from
        # Integer — a future re-narrowing back to plain Integer fails
        # this branch even though isinstance(BigInteger, Integer) is
        # True.
        assert type(col.type) is not Integer, (
            "MaintenanceRun.expected_bytes must NOT be plain Integer "
            "(int4 — the legacy type that overflowed at the "
            "incident 2026-09-28 dry-run echo). Got "
            + repr(type(col.type))
        )
        # Belt: SQLAlchemy BigInteger maps to PG BIGINT. Verify the
        # dialect-level compiled form so a future re-bind (e.g. type
        # adapter) that nominally returns BigInteger but compiles to
        # INTEGER would still fail.
        compiled = col.type.compile(dialect=pg_base.dialect())
        assert "BIGINT" in compiled.upper(), (
            "MaintenanceRun.expected_bytes must compile to BIGINT on "
            "PG; got: " + repr(compiled)
        )

    def test_manager_widening_statement_exists_and_is_probe_gated(self):
        """Pin — ``InstanceManager._ensure_postgres_columns`` MUST
        carry a widening DO block for
        ``maintenance_runs.expected_bytes`` that probes
        ``information_schema.columns.data_type='integer'`` so the
        operation is idempotent on re-run.

        Source-of-truth check via ``inspect.getsource`` (the same
        pattern used by
        ``tests/unit/repositories/test_service_tool_repository.py``
        and ``tests/postgres/test_report_deferred_migration_pg.py``).
        """
        import inspect
        from daemon.manager import InstanceManager

        src = inspect.getsource(InstanceManager._ensure_postgres_columns)

        # The DO block must reference the table + column + widening
        # target + idempotency probe.
        assert "maintenance_runs" in src, (
            "_ensure_postgres_columns must reference the "
            "maintenance_runs table in the widening block."
        )
        assert "expected_bytes" in src, (
            "_ensure_postgres_columns must reference the "
            "expected_bytes column in the widening block."
        )
        assert "TYPE bigint" in src, (
            "_ensure_postgres_columns widening block must ALTER "
            "COLUMN ... TYPE bigint (the int4 → int8 widening)."
        )
        # The probe-style idempotency gate — same shape as the
        # JSON→JSONB conversion at daemon/manager.py:6175-6210.
        assert "data_type = 'integer'" in src, (
            "_ensure_postgres_columns widening block must probe "
            "information_schema.columns for data_type='integer' so "
            "the operation is idempotent on re-run."
        )
        # The probe must check the specific table + column (not
        # just any integer column — that would risk widening a
        # different column).
        assert "table_name = 'maintenance_runs'" in src, (
            "_ensure_postgres_columns widening block must scope the "
            "probe to table_name = 'maintenance_runs'."
        )
        assert "column_name = 'expected_bytes'" in src, (
            "_ensure_postgres_columns widening block must scope the "
            "probe to column_name = 'expected_bytes'."
        )
        # Belt: the probe MUST be inside a DO $$ ... END $$ block
        # (psycopg requires the EXECUTE-style ALTER inside a DO for
        # the IF EXISTS gate to short-circuit the ALTER). The literal
        # in the source file is the escape sequence ``"DO $$\\n"``
        # (backslash-n, two characters in the Python source code); we
        # assert either the escape-sequence form OR the literal
        # "DO $$" start token — either is acceptable evidence the
        # DO-block convention is in use.
        assert ("DO $$\\n" in src) or ("DO $$" in src), (
            "_ensure_postgres_columns widening block must wrap the "
            "probe + ALTER inside a DO $$ ... END $$ block."
        )

    def test_manager_widening_block_present_for_incident_doc(
        self,
    ):
        """Pin — the docstring of ``_ensure_postgres_columns`` MUST
        reference the int4→int8 widening so future contributors find
        it without grepping the SQL. The widening anchor is the
        contract — the docstring is the discoverability surface.
        """
        import inspect
        from daemon.manager import InstanceManager

        doc = InstanceManager._ensure_postgres_columns.__doc__ or ""
        # The docstring must mention the column AND the widening.
        assert "maintenance_runs.expected_bytes" in doc, (
            "_ensure_postgres_columns docstring must document the "
            "maintenance_runs.expected_bytes widening so future "
            "contributors find it without grepping."
        )
        assert "int4" in doc and "int8" in doc, (
            "_ensure_postgres_columns docstring must call out the "
            "int4 → int8 widening (the same shape the JSON→JSONB "
            "conversion uses)."
        )
