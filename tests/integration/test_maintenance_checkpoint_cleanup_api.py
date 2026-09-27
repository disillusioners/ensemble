"""Integration — Section 1 Checkpoint Cleanup API on DISPOSABLE PostgreSQL.

``phase1-backend.md`` §4.3 (cases 37–52, 54, 56–64; case 53 retired by
AM-15) + §4.4 wiring pin (case 55 lives in
``test_checkpoint_cleanup_job_wiring_pin.py``).

Harness mirrors ``tests/integration/checkpoint_prune_real_saver.py``:
``evict_langgraph_mocks`` autouse, disposable DB per test, REAL
production-shaped checkpointer stack (psycopg → AsyncPostgresSaver +
asyncpg pool → PostgresCheckpointerAdapter), real graph writes for
blob staging, drift staged for zero-refs. HTTP layer: minimal
``FastAPI()`` + ``include_router(maintenance_router)`` +
``app.state.maintenance_api_service`` +
``httpx.AsyncClient(transport=ASGITransport(app))``.

HONESTY CONTRACT: if PostgreSQL is unreachable these tests SKIP LOUDLY
— cases 44–50 / 56–64 REQUIRE real PG (or the real router); a skip is
NEVER a pass. PG creds via ``PG_TEST_*`` env only
(``tests/helpers/checkpoint_prune_pg.py:32-36``); the admin DSN is
asserted never to contain ``ensemble_prod`` at import time.
"""
from __future__ import annotations

import asyncio
import logging
import operator
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Annotated, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, inspect as sqlinspect, text
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from tests.helpers.checkpoint_prune_pg import (
    ADMIN_DSN,
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)

# DSN hygiene (R-10): disposable DBs only — never the live prod DB.
assert "ensemble_prod" not in ADMIN_DSN, (
    f"PG_TEST_* misconfiguration: admin DSN targets ensemble_prod ({ADMIN_DSN})"
)

SECTION_PREFIX = "/maintenance/checkpoint-cleanup"

# Plan §4 runner convention: this suite is selected by
# ``--override-ini="addopts=" -m "integration and postgres"`` — the
# markers make the default partition (``-m 'not integration and not
# postgres'``) skip it automatically.
pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@pytest.fixture(autouse=True)
def _real_langgraph():
    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


@pytest.fixture(autouse=True)
def _default_ladder(monkeypatch):
    """Env ladder fixture — every test starts with BOTH env flags deleted."""
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", raising=False)
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", raising=False)


def _arm_destructive(monkeypatch):
    monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", "0")
    monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", "1")


# ── disposable-PG + app fixtures ───────────────────────────────────────────────


def _sync_pg_url(dsn: str) -> str:
    """Normalize a postgresql:// DSN for the SYNC psycopg3 driver
    (SQLAlchemy defaults to psycopg2, which is not installed)."""
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://"):]
    return dsn


async def _probe_pg_or_skip():
    import asyncpg

    try:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=5)
        await conn.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"SECTION-1 GATE SKIPPED — PostgreSQL not available at {ADMIN_DSN} "
            f"({type(exc).__name__}: {exc}). Cases 44-50/56-64 REQUIRE real "
            "PG — a skip is NOT a pass; start PostgreSQL and re-run "
            "tests/integration/test_maintenance_checkpoint_cleanup_api.py."
        )


@pytest.fixture
async def pg_db():
    from tests.helpers.checkpoint_prune_pg import create_disposable_db, drop_database

    await _probe_pg_or_skip()
    name, dsn = await create_disposable_db()
    try:
        assert "ensemble_prod" not in dsn
        yield name, dsn
    finally:
        await drop_database(name)


def build_graph(saver):
    """Real StateGraph (real-saver pattern): messages + notes channels."""
    from langchain_core.messages import AIMessage
    from langgraph.graph import START, StateGraph

    class PruneState(MessagesState := __import__(
        "langgraph.graph", fromlist=["MessagesState"]
    ).MessagesState):
        notes: Annotated[list[dict[str, Any]], operator.add]

    def step(state: PruneState):
        n = len(state.get("notes") or [])
        return {
            "messages": [AIMessage(f"reply-{n}")],
            "notes": [{"turn": n, "filler": "z" * 64}],
        }

    g = StateGraph(PruneState)
    g.add_node("step", step)
    g.add_edge(START, "step")
    g.add_edge("step", "__end__")
    return g.compile(checkpointer=saver)


async def write_turns(saver, thread_id: str, turns: int) -> None:
    """Real aput-driven turns: one checkpoint (+blobs) per turn."""
    from langchain_core.messages import HumanMessage

    graph = build_graph(saver)
    config = {"configurable": {"thread_id": thread_id}}
    for i in range(turns):
        await graph.ainvoke({"messages": [HumanMessage(f"turn-{i}")]}, config)


@asynccontextmanager
async def api_stack(pg_db, *, maintenance_service=None, cleanup_job=None):
    """Full Section-1 stack on the disposable DB: real checkpointer +
    maintenance_runs repo (create_all) + wired job + service + router +
    httpx client."""
    from tests.helpers.checkpoint_prune_pg import real_pg_checkpointer
    from daemon.config import PersistenceConfig
    from daemon.repositories.maintenance_runs import (
        MaintenanceRunsRepository,
    )
    from daemon.services.maintenance import CheckpointCleanupJob
    from daemon.services.maintenance_api_service import MaintenanceApiService
    from daemon.services.maintenance_run_lock import MaintenanceRunLock
    from daemon.routers.maintenance import router as maintenance_router

    name, dsn = pg_db
    runs_engine = create_engine(
        _sync_pg_url(dsn), poolclass=NullPool
    )
    SQLModel.metadata.create_all(runs_engine)
    runs_repo = MaintenanceRunsRepository(runs_engine)
    run_lock = MaintenanceRunLock()

    async with real_pg_checkpointer(name, dsn) as (saver, pool, adapter):
        job = cleanup_job or CheckpointCleanupJob(
            config=PersistenceConfig(),
            checkpointer=adapter,
            instance_repo=MagicMock(),
            run_lock=run_lock,
            runs_repo=runs_repo,
        )
        svc = MaintenanceApiService(
            config=PersistenceConfig(),
            checkpointer=adapter,
            cleanup_job=job,
            runs_repo=runs_repo,
            run_lock=run_lock,
            maintenance_service=maintenance_service,
        )
        app = FastAPI()
        app.include_router(maintenance_router)
        app.state.maintenance_api_service = svc
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield SimpleNamespace(
                saver=saver,
                pool=pool,
                adapter=adapter,
                job=job,
                svc=svc,
                repo=runs_repo,
                runs_engine=runs_engine,
                lock=run_lock,
                app=app,
                client=client,
            )


# ── direct-SQL staging / verification helpers ─────────────────────────────────


async def fetchrow(pool, sql: str, *args):
    async with pool.acquire() as conn:
        return await conn.fetchrow(sql, *args)


async def fetchval(pool, sql: str, *args):
    async with pool.acquire() as conn:
        return await conn.fetchval(sql, *args)


async def execute(pool, sql: str, *args):
    async with pool.acquire() as conn:
        return await conn.execute(sql, *args)


async def checkpoint_count(pool, thread_id: str, ns: str = "") -> int:
    return await fetchval(
        pool,
        "SELECT COUNT(*) FROM checkpoints WHERE thread_id=$1 AND checkpoint_ns=$2",
        thread_id, ns,
    )


async def writes_count(pool, thread_id: str, ns: str = "") -> int:
    return await fetchval(
        pool,
        "SELECT COUNT(*) FROM checkpoint_writes WHERE thread_id=$1 "
        "AND checkpoint_ns=$2",
        thread_id, ns,
    )


async def blob_stats(pool, thread_id: str, ns: str = ""):
    return await fetchrow(
        pool,
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(OCTET_LENGTH(blob)),0) AS bytes "
        "FROM checkpoint_blobs WHERE thread_id=$1 AND checkpoint_ns=$2",
        thread_id, ns,
    )


async def orphan_stats(pool, thread_id: str, ns: str = ""):
    """Direct-SQL mirror of the anti-join count arm (cases 41/57)."""
    return await fetchrow(
        pool,
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(OCTET_LENGTH(b.blob)),0) AS bytes "
        "FROM checkpoint_blobs b WHERE b.thread_id=$1 AND b.checkpoint_ns=$2 "
        "AND NOT EXISTS (SELECT 1 FROM checkpoints c WHERE c.thread_id=b.thread_id "
        "AND c.checkpoint_ns=b.checkpoint_ns "
        "AND (c.checkpoint->'channel_versions'->>b.channel)=b.version)",
        thread_id, ns,
    )


async def total_blob_bytes(pool) -> int:
    return await fetchval(
        pool, "SELECT COALESCE(SUM(OCTET_LENGTH(blob)),0) FROM checkpoint_blobs"
    )


async def stage_drifted_pair(pool, thread_id: str) -> None:
    """Zero-refs drift staging (real-saver pattern): keep the thread's
    checkpoint ROWS but strip their ``channel_versions`` key — the pair
    stays in the enumeration (checkpoints remain) while ref extraction
    yields 0 → the fail-safe SKIPS the pair (ZERO_REFS_FAIL_SAFE) and
    its blobs are never counted or deleted."""
    await execute(
        pool,
        "UPDATE checkpoints SET checkpoint = checkpoint - 'channel_versions' "
        "WHERE thread_id = $1",
        thread_id,
    )


def seed_row(repo, **kwargs) -> None:
    """Insert a row via the SYNC repo (sync by design — callers stage
    rows outside async flow; forgetting an ``await`` here would silently
    no-op)."""
    from daemon.repositories.maintenance_runs import MaintenanceRun
    from daemon.services.timestamps import now_utc_iso

    defaults = dict(
        run_id=f"ckpt-20260927_000000000000-{uuid.uuid4().hex[:8]}",
        section="checkpoint-cleanup",
        kind="auto",
        started_at=now_utc_iso(),
        status="running",
        triggered_by="system",
    )
    defaults.update(kwargs)
    assert repo.insert(MaintenanceRun(**defaults)) is True


async def drain_tasks(svc) -> None:
    if svc._executing_tasks:
        await asyncio.gather(*list(svc._executing_tasks))


# ── cases 37–39 — availability + not_initialized ───────────────────────────────


class TestAvailabilityEndpoints:
    async def test_availability_endpoint_pg(self, pg_db):
        """Case 37 — 200 {eligible: true, backend: postgres, state: ready}."""
        async with api_stack(pg_db) as st:
            r = await st.client.get(f"{SECTION_PREFIX}/availability")
            assert r.status_code == 200, r.text
            assert r.json() == {
                "eligible": True,
                "backend": "postgres",
                "state": "ready",
                "reason": None,
            }

    async def test_availability_endpoint_sqlite(self):
        """Case 38 — SQLite-shaped checkpointer mock → backend_unsupported;
        menu-probe contract honored (FE branches on state, not reason)."""
        from sqlalchemy import create_engine as ce
        from daemon.config import PersistenceConfig
        from daemon.repositories.maintenance_runs import (
            MaintenanceRunsRepository,
        )
        from daemon.services.maintenance import CheckpointCleanupJob
        from daemon.services.maintenance_api_service import MaintenanceApiService
        from daemon.services.maintenance_run_lock import MaintenanceRunLock
        from daemon.routers.maintenance import router as maintenance_router

        import tempfile, os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        engine = ce(f"sqlite:///{db}")
        SQLModel.metadata.create_all(engine)
        svc = MaintenanceApiService(
            config=PersistenceConfig(),
            checkpointer=MagicMock(),  # NOT a PostgresCheckpointerAdapter
            cleanup_job=MagicMock(spec=CheckpointCleanupJob),
            runs_repo=MaintenanceRunsRepository(engine),
            run_lock=MaintenanceRunLock(),
        )
        app = FastAPI()
        app.include_router(maintenance_router)
        app.state.maintenance_api_service = svc
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.get(f"{SECTION_PREFIX}/availability")
                assert r.status_code == 200
                body = r.json()
                assert body["eligible"] is False
                assert body["state"] == "backend_unsupported"
                assert body["reason"] == "blob_prune_postgres_only"
        finally:
            engine.dispose()
            os.unlink(db)

    async def test_503_not_initialized_all_endpoints(self):
        """Case 39 — app WITHOUT app.state.maintenance_api_service → every
        route 503 not_initialized (structured body, not the SPA fallback)."""
        from daemon.routers.maintenance import router as maintenance_router

        app = FastAPI()
        app.include_router(maintenance_router)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            cases = [
                ("GET", "/availability"),
                ("GET", "/status"),
                ("POST", "/dry-run"),
                ("POST", "/execute"),
                ("GET", "/runs/ckpt-x-00000000"),
            ]
            for method, suffix in cases:
                r = await c.request(
                    method,
                    f"{SECTION_PREFIX}{suffix}",
                    json={} if method == "POST" else None,
                )
                assert r.status_code == 503, f"{method} {suffix}: {r.text}"
                body = r.json()
                assert body["detail"]["error"] == "not_initialized"


# ── cases 40–43 — dry-run over real PG ─────────────────────────────────────────


class TestDryRunIntegration:
    async def test_dry_run_counts_derive_from_excess_enumeration(self, pg_db):
        """Case 40 — pair A over keep-3, pair B at keep-3 →
        would_delete.checkpoint_rows == cnt_A - 3, writes parity,
        scanned == 2 (verified against direct SQL on the disposable DB;
        each graph turn writes ~3 checkpoints — counts are DERIVED, not
        assumed)."""
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-a", 6)
            await write_turns(st.saver, "thread-b", 1)  # == keep-3 exactly

            r = await st.client.post(f"{SECTION_PREFIX}/dry-run")
            assert r.status_code == 200, r.text
            body = r.json()

            cnt_a = await checkpoint_count(st.pool, "thread-a")
            cnt_b = await checkpoint_count(st.pool, "thread-b")
            assert cnt_a > 3, "6 turns must exceed keep-3 on thread-a"
            assert cnt_b == 3, "1 turn stages an exactly-at-cap pair"
            assert body["scanned"]["thread_ns_pairs"] == 2
            assert body["would_delete"]["checkpoint_rows"] == cnt_a - 3
            # writes parity: count_writes_excluding for thread-a keep-3
            keep = await st.adapter.get_checkpoint_ids("thread-a", "", 3)
            expected_writes = await st.adapter.count_writes_excluding(
                "thread-a", "", set(keep)
            )
            assert body["would_delete"]["writes"] == expected_writes
            assert expected_writes > 0

    async def test_dry_run_bytes_from_anti_join_count_arm(self, pg_db):
        """Case 41 — stage unreferenced blobs (manual retention prune
        orphans them) → would_delete.blobs/bytes + canonical fields match
        a direct anti-join SQL sum; delete NOT called (rows unchanged)."""
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-o", 8)
            keep = set(await st.adapter.get_checkpoint_ids("thread-o", "", 3))
            await st.adapter.delete_checkpoints_excluding("thread-o", "", keep)
            await st.adapter.delete_writes_excluding("thread-o", "", keep)

            before = await blob_stats(st.pool, "thread-o")
            expected = await orphan_stats(st.pool, "thread-o")
            assert expected["cnt"] > 0, "retention prune must orphan blobs"

            r = await st.client.post(f"{SECTION_PREFIX}/dry-run")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["would_delete"]["blobs"] == expected["cnt"]
            assert body["would_delete"]["bytes"] == expected["bytes"]
            assert body["would_delete_count"] == expected["cnt"]
            assert body["would_free_bytes"] == expected["bytes"]
            # Provably NOT deleted: byte-identical blob rows.
            assert await blob_stats(st.pool, "thread-o") == before

    async def test_dry_run_freshness_window(self, pg_db):
        """Case 42 — fresh_until == started_at + 300s (±2s, TEXT ISO
        +00:00); manual_dry_run row persisted."""
        async with api_stack(pg_db) as st:
            r = await st.client.post(f"{SECTION_PREFIX}/dry-run")
            body = r.json()
            row = st.repo.get(body["run_id"])
            assert row.kind == "manual_dry_run"
            assert row.status == "succeeded"
            s = datetime.fromisoformat(row.started_at)
            f = datetime.fromisoformat(body["fresh_until"])
            assert f.strftime("%z") == "+0000"  # +00:00 suffix
            assert abs((f - s).total_seconds() - 300) <= 2.0

    async def test_dry_run_surfaces_zero_refs_skips(self, pg_db):
        """Case 43 — [re-freeze 3(a)/AM-16a] drifted pair → response
        skipped carries {"reason": "ZERO_REFS_FAIL_SAFE"} (exact literal)
        and would_delete EXCLUDES that pair's blobs."""
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-drift", 4)
            await stage_drifted_pair(st.pool, "thread-drift")
            before = await blob_stats(st.pool, "thread-drift")
            assert before["cnt"] > 0

            r = await st.client.post(f"{SECTION_PREFIX}/dry-run")
            assert r.status_code == 200, r.text
            body = r.json()
            reasons = [s["reason"] for s in body["skipped"]]
            assert "ZERO_REFS_FAIL_SAFE" in reasons
            skipped_pairs = {(s["thread_id"], s["checkpoint_ns"]) for s in body["skipped"]}
            assert ("thread-drift", "") in skipped_pairs
            # The drifted pair contributes NOTHING to the would-delete
            # totals (fail-safe skipped, never deleted) — and it still
            # owns all of its blobs afterwards.
            assert await blob_stats(st.pool, "thread-drift") == before


# ── cases 44–47 — execute + auto-cycle (AM-2 end-to-end) ──────────────────────


class TestExecuteIntegration:
    async def test_execute_happy_path_disposable_pg(self, pg_db):
        """Case 44 — dry-run → execute(confirm, expected_bytes) → 202 →
        poll succeeded; checkpoints reduced to keep-N, writes pruned,
        unreferenced blobs gone, freed bytes == dry-run estimate (AM-2
        end-to-end); run row succeeded with audit fields; /status
        last_run populated kind=manual_execute.

        Two threads stage the two arms: ``thread-h2`` keeps its excess
        rows (Op D prunes them to keep-N); ``thread-h`` is pre-pruned so
        its blobs are orphaned (Op E frees them)."""
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-h2", 6)
            await write_turns(st.saver, "thread-h", 6)
            keep = set(await st.adapter.get_checkpoint_ids("thread-h", "", 3))
            await st.adapter.delete_checkpoints_excluding("thread-h", "", keep)
            await st.adapter.delete_writes_excluding("thread-h", "", keep)
            cnt_h2 = await checkpoint_count(st.pool, "thread-h2")

            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()
            expected_bytes = dry["would_delete"]["bytes"]
            assert expected_bytes > 0

            r = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": expected_bytes,
                    "confirm": True,
                },
            )
            assert r.status_code == 202, r.text
            accepted = r.json()
            assert accepted["advisory"] is None  # no service → idle
            assert isinstance(accepted["expected_duration_ms_hint"], int)
            await drain_tasks(st.svc)

            # Poll to terminal.
            run = (await st.client.get(
                f"{SECTION_PREFIX}/runs/{accepted['run_id']}"
            )).json()
            assert run["status"] == "succeeded", run
            assert run["completed_at"] is not None

            # DB asserts: keep-N enforced on the excess thread, writes pruned.
            assert await checkpoint_count(st.pool, "thread-h2") == 3
            # Unreferenced blobs gone — freed == dry-run estimate (AM-2).
            orphans_after = await orphan_stats(st.pool, "thread-h")
            assert orphans_after["cnt"] == 0
            summary = run["summary"]
            assert summary["blobs"]["deleted"] == dry["would_delete"]["blobs"]
            assert summary["blobs"]["bytes_freed"] == expected_bytes
            assert summary["blobs"]["destructive"] is True
            assert summary["checkpoint_rows"]["deleted"] == cnt_h2 - 3

            # Audit row carries the decision inputs [AM-15].
            row = st.repo.get(accepted["run_id"])
            assert row.kind == "manual_execute"
            assert row.dry_run_run_id == dry["run_id"]
            assert row.expected_bytes == expected_bytes
            assert row.env_flags_json["destructive_override"] is True
            assert row.dry_run_summary_json["would_delete"]["bytes"] == (
                expected_bytes
            )

            # /status last_run populated with the manual execute.
            status = (await st.client.get(f"{SECTION_PREFIX}/status")).json()
            assert status["last_run"]["run_id"] == accepted["run_id"]
            assert status["last_run"]["kind"] == "manual_execute"
            assert status["in_flight"] is None

    async def test_execute_zero_refs_no_mass_delete(self, pg_db):
        """Case 45 — destructive execute against drifted staging → the
        zero-ref pair's blobs survive (INV-3 live proof)."""
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-z", 4)
            await stage_drifted_pair(st.pool, "thread-z")
            before = await blob_stats(st.pool, "thread-z")

            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()
            assert dry["would_delete"]["bytes"] == 0  # drift excluded

            r = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": 0,
                    "confirm": True,
                },
            )
            assert r.status_code == 202, r.text
            await drain_tasks(st.svc)
            run = (await st.client.get(
                f"{SECTION_PREFIX}/runs/{r.json()['run_id']}"
            )).json()
            assert run["status"] == "succeeded"
            # The drifted pair's blobs SURVIVED the destructive run.
            assert await blob_stats(st.pool, "thread-z") == before

    async def test_auto_cycle_unchanged_default_dry_run(self, pg_db):
        """Case 46 — env scrubbed; wired job; run execute() → blob rows
        byte-identical; auto row written with blobs.destructive=false
        (INV-1 live proof)."""
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-auto", 6)
            keep = set(await st.adapter.get_checkpoint_ids("thread-auto", "", 3))
            await st.adapter.delete_checkpoints_excluding(
                "thread-auto", "", keep
            )
            await st.adapter.delete_writes_excluding("thread-auto", "", keep)
            before = await blob_stats(st.pool, "thread-auto")
            assert before["cnt"] > 0

            await st.job.execute()

            rows = st.repo.list_all()
            assert len(rows) == 1
            row = rows[0]
            assert row.kind == "auto"
            assert row.triggered_by == "system"
            assert row.status == "succeeded"
            assert row.summary_json["blobs"]["destructive"] is False
            # Blob rows byte-identical — the auto cycle stayed dry-run.
            assert await blob_stats(st.pool, "thread-auto") == before

    async def test_auto_env_dual_arm_unchanged(self, pg_db, monkeypatch):
        """Case 47 — BOTH flags armed; no-kwarg auto path → deletes happen
        (env path intact); source pin: auto never passes the destructive
        kwarg; auto order stays D→E (INV-9)."""
        import inspect as _inspect

        from daemon.services import maintenance as maintenance_mod

        # Source pin 1: execute()'s blob-arm call passes NO kwarg.
        execute_src = _inspect.getsource(maintenance_mod.CheckpointCleanupJob.execute)
        assert "self._prune_unreferenced_blobs()" in execute_src
        assert "self._prune_unreferenced_blobs(destructive=" not in execute_src
        # Source pin 2: auto order D→E — _prune_per_thread_checkpoints is
        # awaited BEFORE _prune_unreferenced_blobs in execute()'s body.
        d_pos = execute_src.index("await self._prune_per_thread_checkpoints()")
        e_pos = execute_src.index("await self._prune_unreferenced_blobs()")
        assert d_pos < e_pos, "auto cycle must keep D→E (INV-9)"
        # Source pin 3: execute() NEVER routes through the manual entry
        # point (the T1.7 AST pin's textual witness).
        assert "run_checkpoint_prunes" not in execute_src

        _arm_destructive(monkeypatch)
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-arm", 6)
            keep = set(await st.adapter.get_checkpoint_ids("thread-arm", "", 3))
            await st.adapter.delete_checkpoints_excluding("thread-arm", "", keep)
            await st.adapter.delete_writes_excluding("thread-arm", "", keep)
            orphans = await orphan_stats(st.pool, "thread-arm")
            assert orphans["cnt"] > 0

            await st.job.execute()

            # Env path intact: the armed dual-arm deleted the orphans.
            after = await orphan_stats(st.pool, "thread-arm")
            assert after["cnt"] == 0
            row = st.repo.list_all()[0]
            assert row.summary_json["blobs"]["destructive"] is True
            assert row.env_flags_json["destructive_override"] is False


# ── cases 48–50 — contention on the single global lane ─────────────────────────


def _blocked_job(config, adapter, runs_repo, lock):
    """A job whose manual entry point blocks on an event (case 48/50)."""
    from daemon.services.maintenance import CheckpointCleanupJob

    job = CheckpointCleanupJob(
        config=config, checkpointer=adapter,
        instance_repo=MagicMock(), run_lock=lock, runs_repo=runs_repo,
    )
    release = asyncio.Event()

    async def blocked_run(*, destructive: bool):
        await release.wait()
        from daemon.services.maintenance import (
            CheckpointRunResult, CheckpointRowPruneSummary,
        )
        from daemon.services.checkpoint_prune import BlobPruneSummary

        return CheckpointRunResult(
            rows=CheckpointRowPruneSummary(),
            blobs=BlobPruneSummary(dry_run=not destructive),
        )

    job.run_checkpoint_prunes = blocked_run  # type: ignore[method-assign]
    return job, release


class TestContention:
    async def test_overlap_manual_holds_auto_defers(self, pg_db, caplog):
        """Case 48 — manual holds the gate (lock + running row) → auto
        execute(): no ops, NO row, DEBUG with in-flight run_id,
        job.last_run updated (via registry semantics — non-raising)."""
        from daemon.config import PersistenceConfig

        async with api_stack(pg_db) as st:
            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()
            # Dry-run holds the gate only during its scan; hold durably
            # via a blocked manual execute.
            r = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": dry["would_delete"]["bytes"],
                    "confirm": True,
                },
            )
            assert r.status_code == 202, r.text
            manual_run_id = r.json()["run_id"]

            with caplog.at_level(
                logging.DEBUG, logger="daemon.services.maintenance"
            ):
                await st.job.execute()  # auto tick loses the gate

            assert manual_run_id[:22] in caplog.text or "in flight" in caplog.text
            rows = st.repo.list_all()
            # The auto tick wrote NO row: dry-run + manual execute rows only.
            assert not any(row.kind == "auto" for row in rows)
            # Release + drain.
            st.svc._executing_tasks and await asyncio.gather(
                *list(st.svc._executing_tasks)
            )

    async def test_overlap_auto_holds_manual_409(self, pg_db):
        """Case 49 — auto holds the gate (kind='auto' running row) → POST
        /execute → 409 body run_id/started_at reflect the auto holder
        (gates 4–6 must pass first, so the execute references a VALID
        fresh dry-run row)."""
        async with api_stack(pg_db) as st:
            # A valid fresh dry-run FIRST (it completes — terminal row);
            # the auto running row is seeded AFTER so the dry-run does
            # not 409 against it (the dry-run takes the same gate).
            await write_turns(st.saver, "thread-49", 4)
            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()

            auto_row_id = "ckpt-20260927_011111111111-auto000001"
            started = datetime.now(timezone.utc).isoformat()
            seed_row(
                st.repo,
                run_id=auto_row_id,
                kind="auto",
                started_at=started,
                status="running",
                triggered_by="system",
            )

            r = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": dry["would_delete"]["bytes"],
                    "confirm": True,
                },
            )
            assert r.status_code == 409, r.text
            body = r.json()["detail"]
            assert body["error"] == "run_in_flight"
            assert body["details"]["run_id"] == auto_row_id
            assert body["details"]["started_at"] == started
            # NO row for the refused caller (dry-run + seeded auto only).
            assert len(st.repo.list_all()) == 2

    async def test_overlap_two_manuals_409(self, pg_db):
        """Case 50 — a slow execute holds the lane → second POST /execute
        → 409 naming the first run; dry-run-while-execute → 409 too.

        Sequence care: the dry-run runs FIRST on the REAL job (the
        dry-run path awaits the job synchronously); the blocked job is
        swapped in only for the execute, which runs it in the
        BACKGROUND task."""
        from daemon.config import PersistenceConfig

        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-c", 4)
            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()

            job, release = _blocked_job(
                PersistenceConfig(), st.adapter, st.repo, st.lock
            )
            st.svc._cleanup_job = job

            r1 = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": dry["would_delete"]["bytes"],
                    "confirm": True,
                },
            )
            assert r1.status_code == 202, r1.text
            first_run_id = r1.json()["run_id"]

            r2 = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": dry["would_delete"]["bytes"],
                    "confirm": True,
                },
            )
            assert r2.status_code == 409, r2.text
            assert r2.json()["detail"]["details"]["run_id"] == first_run_id

            r3 = await st.client.post(f"{SECTION_PREFIX}/dry-run")
            assert r3.status_code == 409, r3.text
            assert r3.json()["detail"]["details"]["run_id"] == first_run_id

            release.set()
            await drain_tasks(st.svc)
            row = st.repo.get(first_run_id)
            assert row.status == "succeeded"


# ── cases 51 / 52 / 54 — audit rows, error shapes over HTTP, ordering ─────────


class TestAuditAndShapes:
    async def test_audit_row_on_every_manual_run(self, pg_db):
        """Case 51 — after dry-run + execute: ≥1 manual_dry_run, ≥1
        manual_execute, all terminal; execute row carries requester_json /
        env_flags_json / dry_run_summary_json [AM-15]."""
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-a51", 5)
            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()
            r = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": dry["would_delete"]["bytes"],
                    "confirm": True,
                },
            )
            await drain_tasks(st.svc)

            rows = st.repo.list_all()
            kinds = [row.kind for row in rows]
            assert kinds.count("manual_dry_run") >= 1
            assert kinds.count("manual_execute") >= 1
            assert all(row.status in {"succeeded", "failed"} for row in rows)
            exec_row = next(
                row for row in rows if row.kind == "manual_execute"
            )
            assert exec_row.requester_json is not None
            assert exec_row.env_flags_json["destructive_override"] is True
            assert exec_row.dry_run_summary_json is not None

    async def test_structured_error_body_shape_integration(self, pg_db):
        """Case 52 — replay the service gates over HTTP: exact body keys
        per code (catches router-level shape drift)."""
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-e52", 4)
            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()

            # confirm_required
            r = await st.client.post(
                f"{SECTION_PREFIX}/execute", json={"confirm": False}
            )
            assert r.status_code == 400
            assert r.json()["detail"]["error"] == "confirm_required"

            # dry_run_required
            r = await st.client.post(
                f"{SECTION_PREFIX}/execute", json={"confirm": True}
            )
            assert r.status_code == 400
            assert r.json()["detail"]["error"] == "dry_run_required"

            # not_found
            r = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={"confirm": True, "dry_run_run_id": "ckpt-nope"},
            )
            assert r.status_code == 404
            detail = r.json()["detail"]
            assert detail["error"] == "not_found"
            assert detail["details"] == {"run_id": "ckpt-nope"}

            # byte_count_mismatch
            r = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "confirm": True,
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": dry["would_delete"]["bytes"] + 1,
                },
            )
            assert r.status_code == 400
            assert r.json()["detail"]["error"] == "byte_count_mismatch"

            # runs/{id} 404
            r = await st.client.get(f"{SECTION_PREFIX}/runs/ckpt-missing")
            assert r.status_code == 404
            assert r.json()["detail"]["error"] == "not_found"

    async def test_runs_repo_ordering_and_filters(self, pg_db):
        """Case 54 — seeded running/succeeded/failed/dry-run rows →
        latest_completed_for_section returns the newest terminal
        non-dry-run row (NULL-handling + kinds filter — AM-9)."""
        async with api_stack(pg_db) as st:
            base = datetime.now(timezone.utc) - timedelta(minutes=10)
            seed_row(
                st.repo, kind="manual_execute", status="succeeded",
                started_at=(base).isoformat(),
                completed_at=(base + timedelta(seconds=30)).isoformat(),
            )
            newest = (
                base + timedelta(seconds=60)
            ).isoformat()
            seed_row(
                st.repo, kind="auto", status="failed",
                started_at=base.isoformat(),
                completed_at=newest,
            )
            # A manual_dry_run row completed NEWER than everything.
            seed_row(
                st.repo, kind="manual_dry_run", status="succeeded",
                started_at=base.isoformat(),
                completed_at=(base + timedelta(seconds=120)).isoformat(),
            )
            seed_row(
                st.repo, kind="auto", status="running",
                started_at=datetime.now(timezone.utc).isoformat(),
            )

            winner = st.repo.latest_completed_for_section("checkpoint-cleanup")
            assert winner is not None
            assert winner.kind == "auto"  # newest terminal non-dry-run
            assert winner.status == "failed"
            assert winner.completed_at == newest


# ── cases 56–58 — AM-16 regression pins ────────────────────────────────────────


class TestRegressionPins:
    async def test_execute_succeeds_with_new_skip_pair_in_window(
        self, pg_db
    ):
        """Case 56 — [re-freeze 3(b)/AM-16b/AM-3] dry-run → stage a NEW
        drifted pair → execute with the ORIGINAL expected_bytes →
        succeeds (skip pairs contribute 0 bytes to both runs)."""
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-56", 5)
            keep = set(await st.adapter.get_checkpoint_ids("thread-56", "", 3))
            await st.adapter.delete_checkpoints_excluding("thread-56", "", keep)
            await st.adapter.delete_writes_excluding("thread-56", "", keep)
            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()
            original_bytes = dry["would_delete"]["bytes"]
            assert original_bytes > 0

            # NEW skip pair appears inside the freshness window.
            await write_turns(st.saver, "thread-56-new", 3)
            await stage_drifted_pair(st.pool, "thread-56-new")

            r = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": original_bytes,
                    "confirm": True,
                },
            )
            assert r.status_code == 202, r.text
            await drain_tasks(st.svc)
            run = (await st.client.get(
                f"{SECTION_PREFIX}/runs/{r.json()['run_id']}"
            )).json()
            assert run["status"] == "succeeded", run
            # The new skip pair's blobs survive; freed == the original
            # estimate exactly (skip pairs contribute 0 bytes).
            assert run["summary"]["blobs"]["bytes_freed"] == original_bytes
            assert (await blob_stats(st.pool, "thread-56-new"))["cnt"] > 0

    async def test_execute_excess_rows_no_mismatch(self, pg_db):
        """Case 57 — [re-freeze 3(c)/AM-2 BLOCKING regression pin] excess
        checkpoint rows (6 on one thread, keep-N=3, blob-bearing superseded
        versions) → dry-run → execute → succeeds with byte counts matching
        the dry-run EXACTLY. Under the old D→E manual order this FAILS BY
        SILENT OVER-DELETION: every server check passes (the byte gate
        compares the echo against the STORED dry-run row) and Op D
        unreferences blobs the subsequent blob pass deletes beyond the
        confirmed echo. INV-13: no post-run completion gate exists — this
        pin (with case 44's freed==estimate) is the ONLY catcher."""
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-57", 6)

            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()
            promised_bytes = dry["would_delete"]["bytes"]
            promised_blobs = dry["would_delete"]["blobs"]
            # Sanity: the dry-run counted ONLY the pre-D orphans (there
            # are none — the 6-checkpoint thread is fully referenced) so
            # the promise covers 0 blobs; Op D will then orphan 3
            # checkpoints' worth of blobs.
            assert promised_blobs == 0

            r = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": promised_bytes,
                    "confirm": True,
                },
            )
            assert r.status_code == 202, r.text
            await drain_tasks(st.svc)
            run = (await st.client.get(
                f"{SECTION_PREFIX}/runs/{r.json()['run_id']}"
            )).json()
            assert run["status"] == "succeeded", run

            # THE regression assertion: execute freed EXACTLY the promised
            # bytes — NOT ONE BYTE MORE. Under D-first the blob pass would
            # additionally delete the blobs the row-prune just unreference
            # (silent over-deletion beyond the confirmed echo).
            summary = run["summary"]
            assert summary["blobs"]["bytes_freed"] == promised_bytes
            assert summary["blobs"]["deleted"] == promised_blobs
            # The E-first residual (conservative under-delete): blobs
            # referenced only by the excess rows SURVIVE this run —
            # they become the next cycle's orphans (self-healing).
            assert await checkpoint_count(st.pool, "thread-57") == 3
            orphans_after = await orphan_stats(st.pool, "thread-57")
            assert orphans_after["cnt"] > 0, (
                "E-first must leave excess-row blobs for the NEXT cycle"
            )

    async def test_last_run_excludes_manual_dry_run(self, pg_db):
        """Case 58 — [re-freeze 3(d)/AM-9] manual_dry_run row NEWER than a
        terminal manual_execute row → /status.last_run shows the execute
        row; dry-run queryable via GET /runs/{id}."""
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-58", 4)
            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()
            r = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": dry["would_delete"]["bytes"],
                    "confirm": True,
                },
            )
            await drain_tasks(st.svc)
            exec_id = r.json()["run_id"]

            # A NEWER dry-run row (post-execute).
            newer_dry = (await st.client.post(
                f"{SECTION_PREFIX}/dry-run"
            )).json()

            status = (await st.client.get(f"{SECTION_PREFIX}/status")).json()
            assert status["last_run"]["run_id"] == exec_id
            assert status["last_run"]["kind"] == "manual_execute"
            # Dry-run history IS queryable by id.
            got = await st.client.get(
                f"{SECTION_PREFIX}/runs/{newer_dry['run_id']}"
            )
            assert got.status_code == 200
            assert got.json()["kind"] == "manual_dry_run"


# ── case 59 — Origin guard matrix (integration half) ──────────────────────────


class TestOriginGuardIntegration:
    @pytest.mark.parametrize(
        "origin,expected_status",
        [
            (None, 200),                       # (i) no Origin → allowed
            ("http://testserver", 200),        # (ii) same-origin (host match)
            ("http://localhost:4199", 200),    # (iii) FE dev server
            ("http://127.0.0.1:9999", 200),    # (iii) loopback any port
            ("https://[::1]:8443", 200),       # (iii) IPv6 loopback
            ("http://evil.example", 403),      # (v) untrusted
            ("null", 403),                     # (vi) Origin: null
        ],
    )
    async def test_origin_guard_matrix(
        self, pg_db, monkeypatch, origin, expected_status
    ):
        """Case 59 — [AM-1/AM-16e] parametrized matrix on /status; the
        /availability endpoint is EXEMPT (reachable with untrusted Origin)."""
        from daemon.routers import maintenance_origin_guard as guard

        monkeypatch.setenv("MAINTENANCE_TRUSTED_ORIGINS", "http://ops-box.lan")
        guard.reset_trusted_origins_cache()
        try:
            async with api_stack(pg_db) as st:
                headers = {"Host": "testserver"}
                if origin is not None:
                    headers["Origin"] = origin
                r = await st.client.get(
                    f"{SECTION_PREFIX}/status", headers=headers
                )
                assert r.status_code == expected_status, r.text
                if expected_status == 403:
                    assert (
                        r.json()["detail"]["error"] == "origin_not_trusted"
                    )
                # /availability EXEMPT even for untrusted origins.
                r2 = await st.client.get(
                    f"{SECTION_PREFIX}/availability", headers=headers
                )
                assert r2.status_code == 200
        finally:
            guard.reset_trusted_origins_cache()
            monkeypatch.delenv("MAINTENANCE_TRUSTED_ORIGINS", raising=False)

    async def test_origin_guard_trusted_origins_match(self, pg_db, monkeypatch):
        """Case 59 (iv) — MAINTENANCE_TRUSTED_ORIGINS match → allowed."""
        from daemon.routers import maintenance_origin_guard as guard

        monkeypatch.setenv("MAINTENANCE_TRUSTED_ORIGINS", "http://ops-box.lan")
        guard.reset_trusted_origins_cache()
        try:
            async with api_stack(pg_db) as st:
                r = await st.client.get(
                    f"{SECTION_PREFIX}/status",
                    headers={"Origin": "http://ops-box.lan"},
                )
                assert r.status_code == 200, r.text
        finally:
            guard.reset_trusted_origins_cache()
            monkeypatch.delenv("MAINTENANCE_TRUSTED_ORIGINS", raising=False)

    # ── audit G1 — Origin guard coverage on the OTHER guarded routes ──────────
    #
    # The matrix test above covers ``/status`` (the simplest of the four
    # guarded endpoints). ``/dry-run`` (POST, source ~:213) and
    # ``/runs/{id}`` (GET, source ~:310) NEVER see an untrusted Origin in
    # any test — a silent-drop seam (this repo's known bug class): if
    # ``dependencies=[Depends(_require_origin)]`` were dropped from either
    # route's decorator, the existing suite would still pass. This
    # parametrized test asserts the FIRST-CHECK guarantee (INV-10) on
    # both routes; evil/null Origins must 403 ``origin_not_trusted`` before
    # any other gate is consulted.
    @pytest.mark.parametrize(
        "method,path,origin",
        [
            ("POST", "/dry-run", "http://evil.example"),
            ("POST", "/dry-run", "null"),
            ("GET", "/runs/ckpt-x-00000000", "http://evil.example"),
            ("GET", "/runs/ckpt-x-00000000", "null"),
        ],
    )
    async def test_origin_guard_untrusted_other_routes(
        self, pg_db, monkeypatch, method, path, origin
    ):
        """Audit G1 — UNTRUSTED Origin → 403 ``origin_not_trusted`` on
        ``/dry-run`` (POST) and ``/runs/{id}`` (GET), same as ``/status``.

        Mirrors the matrix's allowed/refused split but scoped narrowly
        to the two routes the matrix does NOT touch; ``Origin: null``
        added on each (the matrix covers it on ``/status``). Per
        INV-10, the guard runs BEFORE kill-switch + service gates, so
        a refused Origin can never see 503 ``not_initialized`` or any
        other body — that contract is the whole point of the audit.
        """
        from daemon.routers import maintenance_origin_guard as guard

        monkeypatch.setenv("MAINTENANCE_TRUSTED_ORIGINS", "http://ops-box.lan")
        guard.reset_trusted_origins_cache()
        try:
            async with api_stack(pg_db) as st:
                headers = {"Host": "testserver", "Origin": origin}
                url = f"{SECTION_PREFIX}{path}"
                if method == "POST":
                    r = await st.client.post(url, headers=headers)
                else:
                    r = await st.client.get(url, headers=headers)
                assert r.status_code == 403, r.text
                assert (
                    r.json()["detail"]["error"] == "origin_not_trusted"
                )
        finally:
            guard.reset_trusted_origins_cache()
            monkeypatch.delenv("MAINTENANCE_TRUSTED_ORIGINS", raising=False)


# ── case 60 — kill-switch over the integration stack ──────────────────────────


class TestKillSwitchIntegration:
    async def test_kill_switch_off_integration(self, pg_db, monkeypatch):
        """Case 60 — [AM-13/AM-16f] switch OFF → /status, /dry-run,
        /execute, /runs/{id} all 503 maintenance_disabled; /availability
        → 200 {eligible: false, state: kill_switched}."""
        import daemon.routers.maintenance as router_mod

        monkeypatch.setattr(router_mod, "MAINTENANCE_ENDPOINTS_ENABLED", False)
        try:
            async with api_stack(pg_db) as st:
                r1 = await st.client.get(f"{SECTION_PREFIX}/status")
                r2 = await st.client.post(f"{SECTION_PREFIX}/dry-run")
                r3 = await st.client.post(f"{SECTION_PREFIX}/execute", json={})
                r4 = await st.client.get(
                    f"{SECTION_PREFIX}/runs/ckpt-x-00000000"
                )
                for r in (r1, r2, r3, r4):
                    assert r.status_code == 503, r.text
                    assert (
                        r.json()["detail"]["error"] == "maintenance_disabled"
                    )
                avail = await st.client.get(f"{SECTION_PREFIX}/availability")
                assert avail.status_code == 200
                body = avail.json()
                assert body["eligible"] is False
                assert body["state"] == "kill_switched"
                assert body["reason"] == "MAINTENANCE_ENDPOINTS_ENABLED=0"
        finally:
            monkeypatch.setattr(router_mod, "MAINTENANCE_ENDPOINTS_ENABLED", True)


# ── case 61 — boot sweep ───────────────────────────────────────────────────────


class TestBootSweepIntegration:
    async def test_boot_sweep_unconditional_cas(self, pg_db, caplog):
        """Case 61 — [AM-7/AM-16g] seed a phantom running row SECONDS OLD
        (proving NO age gate), run the sweep → flipped to interrupted +
        error.code=run_interrupted + completed_at = sweep time; one
        summary log line; /status shows no phantom in_flight.

        Note: the partial unique claim index structurally allows at most
        ONE ``running`` row per section — "phantom rows" (plural, plan
        wording) cannot coexist under the AM-5 claim; the no-age-gate
        proof needs exactly one YOUNG phantom. An old TERMINAL row rides
        along to prove the CAS touches ONLY ``running`` rows."""
        from daemon.services.maintenance_boot_sweep import (
            sweep_interrupted_running_runs,
        )

        async with api_stack(pg_db) as st:
            seed_row(
                st.repo,
                run_id="ckpt-20260927_022222222222-young0000",
                kind="manual_execute",
                started_at=(
                    datetime.now(timezone.utc) - timedelta(seconds=3)
                ).isoformat(),
                status="running",
                triggered_by="user",
            )
            seed_row(
                st.repo,
                run_id="ckpt-20260927_011111111111-old000000",
                kind="auto",
                started_at=(
                    datetime.now(timezone.utc) - timedelta(hours=2)
                ).isoformat(),
                completed_at=(
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
                status="succeeded",
                triggered_by="system",
            )

            with caplog.at_level(logging.INFO):
                swept = await sweep_interrupted_running_runs(st.repo)
            assert swept == 1
            summary_lines = [
                rec for rec in caplog.records
                if "maintenance boot sweep" in rec.getMessage()
            ]
            assert len(summary_lines) == 1

            phantom = st.repo.get("ckpt-20260927_022222222222-young0000")
            assert phantom.status == "interrupted"
            assert phantom.error_json["code"] == "run_interrupted"
            assert phantom.completed_at is not None
            # The terminal row is untouched.
            assert (
                st.repo.get("ckpt-20260927_011111111111-old000000").status
                == "succeeded"
            )

            status = (await st.client.get(f"{SECTION_PREFIX}/status")).json()
            assert status["in_flight"] is None


# ── case 62 — dual-arm contention ──────────────────────────────────────────────


class TestDualArmContention:
    async def test_dual_arm_contention_409_and_auto_skip(
        self, pg_db, monkeypatch, caplog
    ):
        """Case 62 — [AM-16h/Focus Area 6] (i) auto running + manual
        dry-run/execute → 409 naming the auto run; (ii) manual running
        (blocked execute holds the lane) + auto tick → auto skips (no
        row, DEBUG, last_run re-arm semantics); (iii) dual-arm env armed
        during both — auto stays destructive-if-armed while the manual
        path 409s (INV-1 cell)."""
        from daemon.services.maintenance_run_lock import MaintenanceRunContext

        _arm_destructive(monkeypatch)
        async with api_stack(pg_db) as st:
            await write_turns(st.saver, "thread-62", 4)
            # A terminal dry-run FIRST (valid reference for the execute).
            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()

            # (i) auto holds the lane → manual dry-run 409s (env armed:
            # the manual API still refuses — the claim gates the surface).
            auto_row_id = "ckpt-20260927_033333333333-auto000001"
            seed_row(
                st.repo, run_id=auto_row_id, kind="auto", status="running",
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            r = await st.client.post(f"{SECTION_PREFIX}/dry-run")
            assert r.status_code == 409
            assert r.json()["detail"]["details"]["run_id"] == auto_row_id
            # Mark the auto row terminal so the lane frees.
            st.repo.mark_terminal(
                auto_row_id, "succeeded", datetime.now(timezone.utc).isoformat()
            )

            # (ii) manual holds (blocked execute) → auto tick defers.
            from daemon.config import PersistenceConfig

            job, release = _blocked_job(
                PersistenceConfig(), st.adapter, st.repo, st.lock
            )
            st.svc._cleanup_job = job
            r2 = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": dry["would_delete"]["bytes"],
                    "confirm": True,
                },
            )
            assert r2.status_code == 202
            manual_id = r2.json()["run_id"]
            auto_rows_before = {
                row.run_id
                for row in st.repo.list_all()
                if row.kind == "auto"
            }
            with caplog.at_level(
                logging.DEBUG, logger="daemon.services.maintenance"
            ):
                await st.job.execute()  # auto tick with the REAL job
            auto_rows_after = {
                row.run_id
                for row in st.repo.list_all()
                if row.kind == "auto"
            }
            assert auto_rows_after == auto_rows_before, (
                "auto tick must write NO row while the manual run holds"
            )
            release.set()
            await drain_tasks(st.svc)
            assert st.repo.get(manual_id).status == "succeeded"

            # (iii) INV-1 cell — with the dual-arm env armed and the lane
            # free, the AUTO tick (real job, no kwarg) IS destructive.
            await st.job.execute()
            auto_rows = [
                row for row in st.repo.list_all() if row.kind == "auto"
            ]
            assert auto_rows
            assert auto_rows[-1].summary_json["blobs"]["destructive"] is True


# ── case 63 — 409-adoption payload + AM-17 absence pin ─────────────────────────


class TestAdoptionContract:
    async def test_409_body_run_id_adoption_contract(self, pg_db):
        """Case 63 — [AM-17/AM-16i] any 409 run_in_flight body carries
        details.run_id + details.started_at of the IN-FLIGHT run;
        idempotency_key appears NOWHERE in the execute contract."""
        async with api_stack(pg_db) as st:
            # A valid fresh dry-run so the execute passes gates 4–6 and
            # reaches the single-flight gate (7).
            await write_turns(st.saver, "thread-63", 4)
            dry = (await st.client.post(f"{SECTION_PREFIX}/dry-run")).json()

            holder_id = "ckpt-20260927_044444444444-holder001"
            started = datetime.now(timezone.utc).isoformat()
            seed_row(
                st.repo, run_id=holder_id, kind="manual_execute",
                status="running", started_at=started, triggered_by="user",
            )
            r = await st.client.post(
                f"{SECTION_PREFIX}/execute",
                json={
                    "dry_run_run_id": dry["run_id"],
                    "expected_bytes": dry["would_delete"]["bytes"],
                    "confirm": True,
                },
            )
            assert r.status_code == 409
            detail = r.json()["detail"]
            assert detail["error"] == "run_in_flight"
            assert detail["details"]["run_id"] == holder_id
            assert detail["details"]["started_at"] == started

            # AM-17 absence pin: no idempotency_key anywhere in the
            # OpenAPI execute schema or the request echo.
            schema = st.app.openapi()["components"]["schemas"].get(
                "CheckpointCleanupExecuteRequest", {}
            )
            assert "idempotency_key" not in schema.get("properties", {})
            assert "idempotency_key" not in r.text


# ── case 64 — partial-index render on BOTH drivers + double-insert ─────────────


class TestPartialIndexBothDrivers:
    async def test_partial_index_render_both_drivers(self, pg_db):
        """Case 64 — [AM-5/AM-16j] create_all renders
        uq_maintenance_runs_running_section on disposable PG AND
        file-backed SQLite; concurrent double-insert → exactly ONE row
        wins on BOTH drivers (R-16)."""
        import os
        import tempfile

        async with api_stack(pg_db) as st:
            # PG render
            pg_indexes = {
                i["name"]: i for i in sqlinspect(st.runs_engine).get_indexes(
                    "maintenance_runs"
                )
            }
            assert "uq_maintenance_runs_running_section" in pg_indexes
            assert pg_indexes["uq_maintenance_runs_running_section"]["unique"]
            assert pg_indexes["uq_maintenance_runs_running_section"].get(
                "dialect_options", {}
            ).get("postgresql_where")

            # PG concurrent double-insert (two sessions, no app lock).
            dsn = st.runs_engine.url.render_as_string(hide_password=False)
            # already +psycopg normalized
            e1 = create_engine(dsn, poolclass=NullPool)
            e2 = create_engine(dsn, poolclass=NullPool)
            try:
                from daemon.repositories.maintenance_runs import (
                    MaintenanceRun, MaintenanceRunsRepository as R,
                )
                from daemon.services.timestamps import now_utc_iso

                r1, r2 = R(e1), R(e2)
                a = MaintenanceRun(
                    run_id="ckpt-20260927_055555555555-winner0001",
                    kind="auto", started_at=now_utc_iso(),
                    status="running", triggered_by="system",
                )
                b = MaintenanceRun(
                    run_id="ckpt-20260927_055555555555-loser00001",
                    kind="manual_execute", started_at=now_utc_iso(),
                    status="running", triggered_by="user",
                )
                results = await asyncio.gather(
                    asyncio.to_thread(r1.insert, a),
                    asyncio.to_thread(r2.insert, b),
                    return_exceptions=True,
                )
                booleans = [x for x in results if isinstance(x, bool)]
                assert sorted(booleans) == [False, True], results
                running = st.repo.get_running("checkpoint-cleanup")
                assert running is not None  # exactly one winner
            finally:
                e1.dispose()
                e2.dispose()

        # SQLite render + double-insert on a file DB.
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            eng = create_engine(f"sqlite:///{db}")
            SQLModel.metadata.create_all(eng)
            indexes = {
                i["name"]: i
                for i in sqlinspect(eng).get_indexes("maintenance_runs")
            }
            assert "uq_maintenance_runs_running_section" in indexes
            assert indexes["uq_maintenance_runs_running_section"]["unique"]

            from daemon.repositories.maintenance_runs import (
                MaintenanceRun, MaintenanceRunsRepository as R,
            )
            from daemon.services.timestamps import now_utc_iso

            repo = R(eng)
            a = MaintenanceRun(
                run_id="ckpt-20260927_055555555556-swinner001",
                kind="auto", started_at=now_utc_iso(),
                status="running", triggered_by="system",
            )
            assert repo.insert(a) is True
            b = MaintenanceRun(
                run_id="ckpt-20260927_055555555556-sloser0001",
                kind="auto", started_at=now_utc_iso(),
                status="running", triggered_by="system",
            )
            assert repo.insert(b) is False
            assert len(repo.list_all()) == 1
            eng.dispose()
        finally:
            os.unlink(db)

    async def test_sqlite_fallback_insert_where_not_exists(self):
        """[R-15, v3 fix pass] the documented fallback gets its OWN named
        test: seed a running row → a second insert via the NOT-EXISTS path
        is refused. Runs ONLY when the dual-dialect render fails — loudly
        skipped otherwise (the fallback is pinned-if-used, never silently
        substituted — AM-5)."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            eng = create_engine(f"sqlite:///{db}")
            SQLModel.metadata.create_all(eng)
            names = {
                i["name"] for i in sqlinspect(eng).get_indexes(
                    "maintenance_runs"
                )
            }
            if "uq_maintenance_runs_running_section" in names:
                pytest.skip(
                    "Dual-dialect partial-index render WORKS on this "
                    "SQLite — the NOT-EXISTS fallback is not in use "
                    "(case 64 covers the primary path)."
                )
            # Fallback path exercised: plain conditional insert.
            from daemon.repositories.maintenance_runs import (
                MaintenanceRun, MaintenanceRunsRepository as R,
            )
            from daemon.services.timestamps import now_utc_iso

            repo = R(eng)
            a = MaintenanceRun(
                run_id="ckpt-20260927_066666666666-fallback01",
                kind="auto", started_at=now_utc_iso(),
                status="running", triggered_by="system",
            )
            assert repo.insert(a) is True
            b = MaintenanceRun(
                run_id="ckpt-20260927_066666666666-fallback02",
                kind="auto", started_at=now_utc_iso(),
                status="running", triggered_by="system",
            )
            assert repo.insert(b) is False
            eng.dispose()
        finally:
            os.unlink(db)
