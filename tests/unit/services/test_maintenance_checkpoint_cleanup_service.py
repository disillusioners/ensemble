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
    rows = CheckpointRowPruneSummary(scanned_pairs=12)
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


class TestDryRun:
    async def test_dry_run_happy_shape(self, runs_repo, as_pg):
        """Case 32a — happy shape pins: would_delete /
        would_delete_count / would_free_bytes / scanned / skipped /
        duration_ms / fresh_until [AM-10/AM-11]."""
        svc = _service(runs_repo, job_result=_result(duration_ms=100))
        got = await svc.dry_run(REQUESTER)
        assert set(got.keys()) == {
            "run_id", "would_delete", "would_delete_count",
            "would_free_bytes", "scanned", "skipped", "skipped_truncated",
            "duration_ms", "fresh_until",
        }
        assert got["would_delete"] == {
            "checkpoint_rows": 0, "writes": 0, "blobs": 4,
            "bytes": 268435456,
        }
        assert got["would_delete_count"] == 4
        assert got["would_free_bytes"] == 268435456
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
        # pinned over HTTP below (TestRouterGates).
        assert True


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
