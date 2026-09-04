"""Tests for the defer-blocked transparency surface (docs §8.5).

``GET /api/queues/defer-blocked`` — the read-only mirror of the defer
gate's busy-set (2026-09-04, ``feature/queue-status-missions-badge``).
The surface exists because the gate can hold on a witness no surface
shows (live case: a paused instance occupying the gate's busy-set).

What this file pins
-------------------

* **Route registration** — the route serves on the system-scoped
  queues router at exactly ``/api/queues/defer-blocked``; unwired
  resolver ⇒ 503 (the queues.py DI convention).
* **Three severity shapes** — (a) AMBER: a paused instance is a holder
  (``kind == "paused"``), both via the legacy clause and via the
  Fix-B settled-mirror clause; (b) INFO: holders live-only;
  (c) RED anomaly: ``pending_count > 0`` AND ``holders == []``.
* **THE CONSISTENCY PIN** — holders enumeration == the gate's
  busy-set, pinned in three independent layers:
    1. *Static derivation*: the witness bodies are byte-derived from
       the gate body constants (``_unwrap_exists_body``); the
       FROM/JOIN/WHERE tails are identical; the bind contract is
       shared (``defer_busy_witness_binds`` delegates to
       ``defer_busy_binds``). A re-implementation of the predicate
       anywhere breaks these.
    2. *Runtime path*: the SQL actually executed during an HTTP call
       IS the derived witness body (engine statement capture) — the
       constant existing is not enough; the route must execute it.
    3. *Behavioral matrix*: across fixture sets (empty / paused /
       live / mixed / terminal / settled-mirror / instance-less /
       deleted / defer-lane), the gate's own admission decision
       (``JobRepository.has_active_non_deferred_work(None)`` — the
       exact method whose body the surface composes from) matches
       ``defer_blocked`` AND holders-non-empty EXACTLY, both
       directions.
* **Purity** — zero DML on the endpoint path: engine-level
  write-statement listener + full row-value snapshot before/after
  (the ``test_mission_resolver.py::TestPurity`` dual-spy shape).
  Census contract: ``KNOWN_ADMISSION_STATE_WRITERS`` stays frozen at
  23 — this surface adds ZERO writers.
* **Bounded queries** — exactly 2 SELECTs per request (witnesses +
  defer-lane pending count), FLAT as the witness count doubles (no
  N+1), pinned via a ``before_cursor_execute`` engine listener (NOT
  mock counting — the §8.4 convention).

Harness notes
-------------

File-backed SQLite at ``tmp_path`` with ``NullPool`` + WAL +
``busy_timeout`` (the Testing & QC conventions recipe; the
QUARANTINE.md ``StaticPool + WriteGuardSession`` trap is not used).
Real repositories + the real gate method — no mocking of gate
internals anywhere in this file.
"""

from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401  (transitive dep)

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import _idle_predicate_sql
from daemon.repositories.job_queue.models import AdmissionState, JobItem, JobQueue
from daemon.repositories.job_queue.repository import JobRepository
from daemon.routers import queues as queues_router_module
from daemon.routers.queues import (
    router as project_queues_router,
    system_queues_router,
    set_defer_block_resolver,
)
from daemon.services.defer_block_resolver import DeferBlockResolver


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout)."""
    db_path = tmp_path / "defer-blocked-api-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def resolver(job_repo: JobRepository) -> DeferBlockResolver:
    return DeferBlockResolver(job_repo=job_repo)


@pytest.fixture
def client(resolver: DeferBlockResolver) -> TestClient:
    """TestClient with BOTH queues routers mounted under /api.

    Mirrors the api.py registration (the per-project router keeps
    ``/projects/{project_id}/queues``; the system router adds
    ``/queues/defer-blocked``) and the lifespan wiring
    (``set_defer_block_resolver``).
    """
    set_defer_block_resolver(resolver)
    app = FastAPI()
    app.include_router(project_queues_router, prefix="/api")
    app.include_router(system_queues_router, prefix="/api")
    return TestClient(app)


# ─── Seed helpers ───────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    agent_id: str = "developer",
    project_id: str | None = "test-project",
    status: str = InstanceStatus.RUNNING.value,
    last_activity_at: datetime | None = None,
    paused_at: str | None = None,
    updated_at: str | None = None,
    created_at: str | None = None,
) -> str:
    """Insert a populated ``Instance`` row; return its id."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=instance_id,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            project_id=project_id,
            status=status,
            created_at=created_at or now_iso,
            updated_at=updated_at or now_iso,
            last_activity_at=last_activity_at,
            paused_at=paused_at,
        )
        s.add(inst)
        s.commit()
    return instance_id


def _seed_queue(
    engine: Engine,
    *,
    queue_id: str,
    project_id: str = "test-project",
    queue_type: str = "parallel",
    queue_name: str | None = None,
) -> str:
    """Insert a ``JobQueue`` row; return its id."""
    name = queue_name or queue_id
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        q = JobQueue(
            queue_id=queue_id,
            project_id=project_id,
            queue_name=name,
            queue_name_lower=name.lower(),
            queue_type=queue_type,
            concurrency_limit=1,
            is_system=queue_type in ("defer", "background"),
            is_paused=False,
            description=None,
            created_at=now_iso,
            updated_at=now_iso,
        )
        s.add(q)
        s.commit()
    return queue_id


def _seed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    instance_id: str | None = None,
    project_id: str | None = "test-project",
    queue_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    job_type: str = "task",
    deleted_at: str | None = None,
    agent_id: str = "developer",
    created_at: str | None = None,
) -> str:
    """Insert a ``JobItem`` row; return its id.

    ``queue_id=None`` ⇒ queue-less work (the busy body's
    ``q.queue_type IS NULL`` arm — still a non-defer witness).
    """
    jid = job_id or f"job-{uuid.uuid4().hex[:10]}"
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            message="defer-blocked surface test job",
            source="api",
            project_id=project_id,
            queue_id=queue_id,
            priority=5,
            admission_state=admission_state,
            terminal_reason=None,
            instance_id=instance_id,
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
            deleted_at=deleted_at,
            job_metadata={},
            job_type=job_type,
        )
        s.add(job)
        s.commit()
    return jid


def _seed_defer_queue_with_pending_job(
    engine: Engine,
    *,
    job_id: str = "job-defer-pending",
) -> str:
    """Seed the ``system_defer_queue`` lane with one PENDING JobItem."""
    qid = _seed_queue(
        engine, queue_id="q-defer", queue_type="defer",
        queue_name="system_defer_queue",
    )
    return _seed_job(
        engine,
        job_id=job_id,
        queue_id=qid,
        admission_state=AdmissionState.QUEUED.value,
    )


# ─── Statement capture helper (engine listener, NOT mock counting) ─────────


def _capture_statements(engine: Engine):
    """Attach a ``before_cursor_execute`` spy; return (captured, detach)."""
    captured: list[str] = []

    def _before_cursor_execute(  # noqa: ANN001 — SQLAlchemy hook
        conn, cursor, statement, parameters, context, executemany  # noqa: ARG001
    ):
        captured.append(statement)

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)

    def _detach() -> None:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)

    return captured, _detach


_SQL_PARAM_TOKEN = re.compile(r"__\[POSTCOMPILE_\w+\]|:\w+|\?")
_SQL_EXPANDED_INLIST = re.compile(r"\?(?:\s*,\s*\?)+")


def _normalize_sql(sql: str) -> str:
    """Collapse every bind token (named ``:name``, SQLAlchemy
    ``__[POSTCOMPILE_name]``, or dialect ``?``) to a single ``?`` — and
    collapse a driver-expanded IN-list (``?, ?, ?``) back to one token
    — so a ``str(TextClause)`` (one POSTCOMPILE token per expanding
    bind) can be compared with a driver-level statement (N ``?`` per
    expanded list) regardless of paramstyle or list cardinality."""
    sql = _SQL_PARAM_TOKEN.sub("?", sql)
    return _SQL_EXPANDED_INLIST.sub("?", sql)


# ═══════════════════════════════════════════════════════════════════════════
# Route registration + DI
# ═══════════════════════════════════════════════════════════════════════════


class TestRouteRegistration:
    """The route exists at the contract path; unwired resolver ⇒ 503."""

    def test_route_path_is_exact_contract_path(self, client):
        """``GET /api/queues/defer-blocked`` — the exact FE contract path."""
        schema = client.get("/openapi.json").json()
        assert "/api/queues/defer-blocked" in schema["paths"]

    def test_empty_db_serves_200_idle(self, client):
        """Empty DB ⇒ 200, not blocked, nothing pending, no holders."""
        resp = client.get("/api/queues/defer-blocked")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "defer_blocked": False,
            "pending_count": 0,
            "holders": [],
        }

    def test_unwired_resolver_is_503(self, engine, job_repo):
        """The 503-if-unwired DI convention (the queues.py shape)."""
        import contextlib

        app = FastAPI()
        app.include_router(project_queues_router, prefix="/api")
        app.include_router(system_queues_router, prefix="/api")
        # Simulate pre-lifespan state: resolver global unset.
        with contextlib.ExitStack() as stack:
            stack.callback(
                setattr,
                queues_router_module,
                "_defer_block_resolver",
                queues_router_module._defer_block_resolver,
            )
            queues_router_module._defer_block_resolver = None
            client = TestClient(app)
            resp = client.get("/api/queues/defer-blocked")
            assert resp.status_code == 503
            assert "not initialized" in resp.json()["detail"]["error"]


# ═══════════════════════════════════════════════════════════════════════════
# The three severity shapes
# ═══════════════════════════════════════════════════════════════════════════


class TestSeverityShapes:
    """(a) AMBER — a paused holder; (b) INFO — live-only holders;
    (c) RED anomaly — pending defer work with zero holders."""

    def test_amber_paused_holder_via_legacy_clause(self, client, engine):
        """Paused instance + active non-defer job ⇒ kind='paused' holder."""
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        iid = _seed_instance(
            engine,
            instance_id="inst-paused",
            status=InstanceStatus.PAUSED.value,
            paused_at="2026-09-04T10:00:00+00:00",
            updated_at="2026-09-04T10:00:01+00:00",
        )
        _seed_job(engine, instance_id=iid, queue_id="q-par")
        resp = client.get("/api/queues/defer-blocked")
        assert resp.status_code == 200
        body = resp.json()
        assert body["defer_blocked"] is True
        assert len(body["holders"]) == 1
        holder = body["holders"][0]
        assert holder["kind"] == "paused"
        assert holder["instance_id"] == iid
        assert holder["status"] == "paused"
        assert holder["agent"] == "developer"
        # since = paused_at (the pause transition stamp), normalized.
        assert holder["since"] == "2026-09-04T10:00:00+00:00"

    def test_amber_paused_holder_via_settled_mirror(
        self, client, engine
    ):
        """THE live case: a Fix-B SETTLED message mirror whose parent
        instance is paused still holds the gate — surfaced as AMBER."""
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        iid = _seed_instance(
            engine,
            instance_id="inst-paused-mirror",
            status=InstanceStatus.PAUSED.value,
            paused_at="2026-09-03T08:30:00+00:00",
        )
        _seed_job(
            engine,
            instance_id=iid,
            queue_id="q-par",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )
        body = client.get("/api/queues/defer-blocked").json()
        assert body["defer_blocked"] is True
        assert [h["kind"] for h in body["holders"]] == ["paused"]
        assert body["holders"][0]["instance_id"] == iid

    def test_info_live_only_holders(self, client, engine):
        """Live instances only ⇒ all holders kind='live', since from
        last_activity_at."""
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        for n, status in enumerate(
            (InstanceStatus.RUNNING.value, InstanceStatus.WAITING_CHILDREN.value)
        ):
            iid = _seed_instance(
                engine,
                instance_id=f"inst-live-{n}",
                status=status,
                last_activity_at=datetime(2026, 9, 4, 9, 30 + n, tzinfo=timezone.utc),
            )
            _seed_job(engine, instance_id=iid, queue_id="q-par")
        body = client.get("/api/queues/defer-blocked").json()
        assert body["defer_blocked"] is True
        assert {h["kind"] for h in body["holders"]} == {"live"}
        assert {h["status"] for h in body["holders"]} == {
            "running",
            "waiting_children",
        }
        # since = last_activity_at, normalized ISO-8601 UTC.
        assert {h["since"] for h in body["holders"]} == {
            "2026-09-04T09:30:00+00:00",
            "2026-09-04T09:31:00+00:00",
        }

    def test_red_anomaly_pending_without_holders(self, client, engine):
        """Pending defer work + no witnesses ⇒ RED anomaly shape:
        pending_count > 0, holders empty, gate NOT blocked."""
        _seed_defer_queue_with_pending_job(engine)
        body = client.get("/api/queues/defer-blocked").json()
        assert body["pending_count"] == 1
        assert body["holders"] == []
        assert body["defer_blocked"] is False

    def test_paused_since_falls_back_to_updated_at(self, client, engine):
        """Paused holder with NULL paused_at ⇒ since falls back to
        updated_at (the cheap status-change timestamp)."""
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        iid = _seed_instance(
            engine,
            instance_id="inst-paused-nop stamp".replace(" ", "-"),
            status=InstanceStatus.PAUSED.value,
            paused_at=None,
            updated_at="2026-09-04T11:11:11+00:00",
        )
        _seed_job(engine, instance_id=iid, queue_id="q-par")
        body = client.get("/api/queues/defer-blocked").json()
        assert body["holders"][0]["since"] == "2026-09-04T11:11:11+00:00"

    def test_naive_timestamps_normalized_to_utc(self, client, engine):
        """PG-style tz-NAIVE TEXT timestamps (e.g. paused_at written
        naive) are normalized to UTC-aware ISO-8601 — the
        ``_parse_job_created_at`` TEXT-tz trap."""
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        iid = _seed_instance(
            engine,
            instance_id="inst-naive-ts",
            status=InstanceStatus.PAUSED.value,
            paused_at="2026-09-04T10:00:00",  # naive — PG tz-naive shape
        )
        _seed_job(engine, instance_id=iid, queue_id="q-par")
        body = client.get("/api/queues/defer-blocked").json()
        assert body["holders"][0]["since"] == "2026-09-04T10:00:00+00:00"

    def test_instance_less_witness_surfaces_with_empty_instance_id(
        self, client, engine
    ):
        """Legacy clause: an ACTIVE queue-less JobItem with NO instance
        row is a witness. It must surface as its own holder
        (instance_id='', status='', agent from the JobItem) — dropping
        it would break holders-non-empty == gate-blocked."""
        _seed_job(engine, instance_id=None, queue_id=None, agent_id="worker")
        body = client.get("/api/queues/defer-blocked").json()
        assert body["defer_blocked"] is True
        assert len(body["holders"]) == 1
        holder = body["holders"][0]
        assert holder["instance_id"] == ""
        assert holder["status"] == ""
        assert holder["agent"] == "worker"
        assert holder["kind"] == "live"
        # since falls back to the JobItem's own created_at.
        assert holder["since"] is not None

    def test_paused_holders_sort_first_and_dedupe_by_instance(
        self, client, engine
    ):
        """Ordering: paused first (AMBER priority), then live; multiple
        busy jobs on ONE instance dedupe to ONE holder."""
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        paused = _seed_instance(
            engine,
            instance_id="b-paused",
            status=InstanceStatus.PAUSED.value,
            paused_at="2026-09-04T10:00:00+00:00",
        )
        live_a = _seed_instance(engine, instance_id="a-live")
        live_b = _seed_instance(engine, instance_id="c-live")
        _seed_job(engine, instance_id=paused, queue_id="q-par",
                  job_id="job-p-1")
        _seed_job(engine, instance_id=live_a, queue_id="q-par")
        _seed_job(engine, instance_id=live_b, queue_id="q-par")
        # Second busy job on the SAME live instance — must dedupe.
        _seed_job(engine, instance_id=live_a, queue_id="q-par",
                  job_id="job-a-2")
        body = client.get("/api/queues/defer-blocked").json()
        kinds = [h["kind"] for h in body["holders"]]
        assert kinds == ["paused", "live", "live"]
        ids = [h["instance_id"] for h in body["holders"]]
        assert ids == ["b-paused", "a-live", "c-live"]


# ═══════════════════════════════════════════════════════════════════════════
# THE CONSISTENCY PIN — holders enumeration == the gate's busy-set
# ═══════════════════════════════════════════════════════════════════════════


class TestConsistencyPin:
    """The point of the change: display truth == gate truth.

    Three independent layers:
      1. static derivation (byte-shared predicate text),
      2. runtime path (the route executes the derived witness body),
      3. behavioral matrix (gate decision == defer_blocked ==
         holders-non-empty across fixture sets, both directions).
    """

    # ── Layer 1: static shared-composition pins ────────────────────────────

    def test_witness_body_is_derived_from_gate_body_system(self):
        """The system-wide witness body IS the gate body with the
        EXISTS wrapper unwrapped — byte-derivation, not re-statement."""
        gate = _idle_predicate_sql.JOB_DEFER_BUSY_BODY_SYSTEM
        witness = _idle_predicate_sql.JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM
        assert gate.startswith("SELECT EXISTS ( SELECT 1")
        assert gate.endswith(")")
        expected = "SELECT" + _idle_predicate_sql._WITNESS_SELECT_LIST + gate[
            len(_idle_predicate_sql._GATE_BODY_WITNESS_PREFIX):-1
        ]
        assert witness == expected

    def test_witness_body_is_derived_from_gate_body_project(self):
        """Same derivation pin for the project-scoped body pair."""
        gate = _idle_predicate_sql.JOB_DEFER_BUSY_BODY_PROJECT
        witness = _idle_predicate_sql.JOB_DEFER_BUSY_WITNESS_BODY_PROJECT
        assert gate.startswith("SELECT EXISTS ( SELECT 1")
        assert gate.endswith(")")
        expected = "SELECT" + _idle_predicate_sql._WITNESS_SELECT_LIST + gate[
            len(_idle_predicate_sql._GATE_BODY_WITNESS_PREFIX):-1
        ]
        assert witness == expected

    def test_busy_set_tail_is_byte_shared_gate_vs_witness(self):
        """FROM/JOIN/WHERE busy-set text identical on gate and witness
        bodies — an independent re-implementation cannot pass this."""
        for gate_body, witness_body in (
            (
                _idle_predicate_sql.JOB_DEFER_BUSY_BODY_SYSTEM,
                _idle_predicate_sql.JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM,
            ),
            (
                _idle_predicate_sql.JOB_DEFER_BUSY_BODY_PROJECT,
                _idle_predicate_sql.JOB_DEFER_BUSY_WITNESS_BODY_PROJECT,
            ),
        ):
            gate_tail = gate_body[len("SELECT EXISTS ( SELECT 1"):-1]
            wit_tail = witness_body[len("SELECT" + _idle_predicate_sql._WITNESS_SELECT_LIST):]
            assert gate_tail == wit_tail

    def test_witness_bind_contract_is_shared_with_gate(self):
        """``defer_busy_witness_binds`` delegates to ``defer_busy_binds``
        — the parameter contract cannot drift between the two paths."""
        for project_id in (None, "proj-1"):
            assert (
                _idle_predicate_sql.defer_busy_witness_binds(project_id)
                == _idle_predicate_sql.defer_busy_binds(project_id)
            )
        # Raw text bodies name the SAME bindparams…
        gate_body = _idle_predicate_sql.JOB_DEFER_BUSY_BODY_SYSTEM
        wit_body = _idle_predicate_sql.JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM
        for param in (":terminal_statuses", ":excluded_queue_types"):
            assert param in gate_body
            assert param in wit_body
        # …and the compiled statements carry identical bindparam
        # declarations (structural parameter contract, not substrings).
        gate_stmt = _idle_predicate_sql.defer_busy_statement(None)
        wit_stmt = _idle_predicate_sql.defer_busy_witness_statement(None)
        assert set(gate_stmt._bindparams) == set(wit_stmt._bindparams)
        for name, bind in wit_stmt._bindparams.items():
            assert bind.expanding, f"bindparam {name!r} must stay expanding"

    # ── Layer 2: the runtime path executes the shared body ────────────────

    def test_route_executes_the_derived_witness_statement(
        self, client, engine
    ):
        """The constant existing is not enough: capture the SQL actually
        executed during the HTTP call and assert it IS the derived
        witness body (someone swapping in hand-rolled SQL fails here
        even if a lookalike constant survives elsewhere).

        Driver statements render binds in the dialect paramstyle
        (``?`` on SQLite) while ``str(TextClause)`` keeps named params
        (``:name`` / ``__[POSTCOMPILE_name]``) — both sides are
        normalized to a single ``?`` token before comparison, so the
        pin is on the statement TEXT (clause structure + literals),
        not the paramstyle.
        """
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        iid = _seed_instance(engine, instance_id="inst-rt")
        _seed_job(engine, instance_id=iid, queue_id="q-par")

        captured, detach = _capture_statements(engine)
        try:
            resp = client.get("/api/queues/defer-blocked")
        finally:
            detach()
        assert resp.status_code == 200

        expected = _normalize_sql(
            str(_idle_predicate_sql.defer_busy_witness_statement(None))
        )
        witness_executions = [
            s for s in captured if _normalize_sql(s) == expected
        ]
        assert witness_executions, (
            "The defer-blocked route did NOT execute the shared-composition "
            "witness statement. Executed SELECTs: "
            f"{[s for s in captured if s.lstrip().upper().startswith('SELECT')]!r}"
        )

    # ── Layer 3: behavioral matrix — gate decision == surface, EXACTLY ────

    @staticmethod
    def _assert_surface_matches_gate(client, job_repo, note: str) -> None:
        """Gate admission decision vs surface, both directions, EXACTLY."""
        gate_busy = job_repo.has_active_non_deferred_work(None)
        body = client.get("/api/queues/defer-blocked").json()
        # (1) defer_blocked mirrors the gate's own verdict.
        assert body["defer_blocked"] is gate_busy, (
            f"{note}: defer_blocked={body['defer_blocked']} but the gate "
            f"decides {gate_busy}"
        )
        # (2) holders-non-empty == blocked — the enumeration sees the
        # same witnesses the gate's EXISTS ranges over.
        assert (len(body["holders"]) > 0) is gate_busy, (
            f"{note}: holders={len(body['holders'])} but gate={gate_busy}"
        )

    def test_gate_vs_surface_matrix(self, client, engine, job_repo):
        """Fixture matrix: empty / paused / live / mixed / terminal /
        settled-mirror / instance-less / deleted / queued-only /
        defer-lane. On EVERY fixture, gate == defer_blocked ==
        holders-non-empty."""
        # 1. empty
        self._assert_surface_matches_gate(client, job_repo, "empty")

        _seed_queue(engine, queue_id="q-par", queue_type="parallel")

        # 2. live instance + active job → blocked (INFO)
        live = _seed_instance(engine, instance_id="m-live")
        _seed_job(engine, instance_id=live, queue_id="q-par")
        self._assert_surface_matches_gate(client, job_repo, "live")

        # 3. paused instance + active job → blocked (AMBER)
        paused = _seed_instance(
            engine,
            instance_id="m-paused",
            status=InstanceStatus.PAUSED.value,
            paused_at="2026-09-04T10:00:00+00:00",
        )
        _seed_job(engine, instance_id=paused, queue_id="q-par")
        self._assert_surface_matches_gate(client, job_repo, "paused")

        # 4. terminal instance + residual active job → NOT blocked
        _seed_instance(
            engine,
            instance_id="m-terminal",
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_job(engine, instance_id="m-terminal", queue_id="q-par",
                  job_id="job-m-terminal")
        self._assert_surface_matches_gate(client, job_repo, "terminal-instance")

        # 5. settled mirror of a live instance → blocked
        _seed_instance(
            engine,
            instance_id="m-mirror",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _seed_job(
            engine,
            instance_id="m-mirror",
            queue_id="q-par",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
            job_id="job-m-mirror",
        )
        self._assert_surface_matches_gate(client, job_repo, "settled-mirror")

        # 6. settled mirror of a PAUSED instance → still blocked (live case)
        _seed_instance(
            engine,
            instance_id="m-mirror-paused",
            status=InstanceStatus.PAUSED.value,
            paused_at="2026-09-04T09:00:00+00:00",
        )
        _seed_job(
            engine,
            instance_id="m-mirror-paused",
            queue_id="q-par",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
            job_id="job-m-mirror-paused",
        )
        self._assert_surface_matches_gate(client, job_repo, "mirror-paused")

        # 7. instance-less active queue-less job → blocked (edge)
        _seed_job(engine, instance_id=None, queue_id=None,
                  job_id="job-m-instless")
        self._assert_surface_matches_gate(client, job_repo, "instance-less")

        # 8. soft-deleted active job → ignored by both sides
        _seed_job(engine, instance_id=live, queue_id="q-par",
                  deleted_at=datetime.now(timezone.utc).isoformat(),
                  job_id="job-m-deleted")
        self._assert_surface_matches_gate(client, job_repo, "deleted-row")

        # 9. QUEUED (pending) non-defer job, no active witness left —
        #    the legacy clause counts ONLY 'active'; queued-only must
        #    NOT block on either side. (Clear the earlier witnesses by
        #    terminating their instances to terminal.)
        #    m-live / m-paused still block here, so instead assert the
        #    queued-only shape on a FRESH engine via the dedicated RED
        #    test — here we keep the fixture additive and just confirm
        #    agreement still holds with the queued row present.
        _seed_job(engine, instance_id=None, queue_id="q-par",
                  admission_state=AdmissionState.QUEUED.value,
                  job_id="job-m-queued")
        self._assert_surface_matches_gate(client, job_repo, "queued-present")

    def test_queued_only_fixture_agrees_both_directions(
        self, client, engine, job_repo
    ):
        """Queued non-defer job alone is NOT a busy-set witness (the
        legacy clause counts 'active' only) — gate False AND holders
        empty AND pending_count still 0 (not a defer-lane job)."""
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        _seed_job(
            engine,
            instance_id=None,
            queue_id="q-par",
            admission_state=AdmissionState.QUEUED.value,
        )
        self._assert_surface_matches_gate(client, job_repo, "queued-only")
        body = client.get("/api/queues/defer-blocked").json()
        assert body["pending_count"] == 0
        assert body["holders"] == []

    def test_defer_lane_job_neither_blocks_nor_holds_but_counts_pending(
        self, client, engine, job_repo
    ):
        """A PENDING job on the defer lane is excluded from the busy-set
        (the gate's own-lane exclusion — defer self-deadlock is
        structurally impossible) AND excluded from holders, but IS the
        pending_count."""
        _seed_defer_queue_with_pending_job(engine)
        self._assert_surface_matches_gate(client, job_repo, "defer-lane")
        body = client.get("/api/queues/defer-blocked").json()
        assert body["pending_count"] == 1
        assert body["holders"] == []
        assert body["defer_blocked"] is False


# ═══════════════════════════════════════════════════════════════════════════
# PURITY — zero DML on the endpoint path
# ═══════════════════════════════════════════════════════════════════════════


class TestPurity:
    """The surface never INSERTs / UPDATEs / DELETEs.

    Census invariant: ``KNOWN_ADMISSION_STATE_WRITERS`` is frozen at 23;
    this surface adds ZERO writers. Dual spy (the
    ``test_mission_resolver.py::TestPurity`` shape): engine write-
    statement listener + full row-value snapshot before/after the HTTP
    call.
    """

    @staticmethod
    def _assert_no_writes(captured: list[str]) -> None:
        write_prefixes = ("INSERT", "UPDATE", "DELETE", "SAVEPOINT", "REPLACE")
        offenders = [
            stmt
            for stmt in captured
            if stmt.lstrip().upper().startswith(write_prefixes)
        ]
        assert not offenders, (
            "PURITY violated: the defer-blocked surface emitted a write "
            f"statement(s): {offenders!r}. It is a read-only transparency "
            "surface; census stays frozen at 23."
        )

    def test_endpoint_emits_zero_dml(self, client, engine):
        """Populated busy-set + pending defer lane — the full endpoint
        path emits SELECTs only."""
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        iid = _seed_instance(
            engine,
            instance_id="inst-purity",
            status=InstanceStatus.PAUSED.value,
            paused_at="2026-09-04T10:00:00+00:00",
        )
        _seed_job(engine, instance_id=iid, queue_id="q-par")
        _seed_defer_queue_with_pending_job(engine)

        # Snapshot every Instance + JobItem row before the call.
        with Session(engine) as s:
            inst_before = list(s.exec(select(Instance)).all())
            job_before = list(s.exec(select(JobItem)).all())

        captured, detach = _capture_statements(engine)
        try:
            resp = client.get("/api/queues/defer-blocked")
        finally:
            detach()
        assert resp.status_code == 200
        self._assert_no_writes(captured)

        # Snapshot after: byte-equal columns (catches in-place UPDATEs
        # that keep row counts steady).
        with Session(engine) as s:
            inst_after = list(s.exec(select(Instance)).all())
            job_after = list(s.exec(select(JobItem)).all())

        assert len(inst_before) == len(inst_after)
        assert len(job_before) == len(job_after)
        inst_cols = [c.key for c in Instance.__table__.columns]
        job_cols = [c.key for c in JobItem.__table__.columns]
        for a, b in zip(inst_before, inst_after, strict=False):
            assert {c: getattr(a, c, None) for c in inst_cols} == {
                c: getattr(b, c, None) for c in inst_cols
            }
        for a, b in zip(job_before, job_after, strict=False):
            assert {c: getattr(a, c, None) for c in job_cols} == {
                c: getattr(b, c, None) for c in job_cols
            }

    def test_resolver_direct_call_emits_zero_dml(self, engine, resolver):
        """The resolver layer alone (no HTTP) is also write-free — the
        census contract lives at the service layer."""
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        iid = _seed_instance(engine, instance_id="inst-purity-2")
        _seed_job(engine, instance_id=iid, queue_id="q-par")

        captured, detach = _capture_statements(engine)
        try:
            snapshot = resolver.resolve()
        finally:
            detach()

        assert snapshot.defer_blocked is True
        self._assert_no_writes(captured)


# ═══════════════════════════════════════════════════════════════════════════
# BOUNDED QUERIES — exactly 2 SELECTs, flat (no N+1)
# ═══════════════════════════════════════════════════════════════════════════


class TestBoundedQueryCount:
    """Exactly 2 SELECTs per request: witness SELECT + pending count.
    FLAT as the witness count doubles — zero per-row lookups. Pinned
    with an engine event listener (the §8.4 convention, NOT mock
    counting)."""

    @staticmethod
    def _count_selects(engine: Engine):
        counts = {"selects": 0}

        def _before_cursor_execute(  # noqa: ANN001 — SQLAlchemy hook
            conn, cursor, statement, parameters, context, executemany  # noqa: ARG001
        ):
            if statement.strip().upper().startswith("SELECT"):
                counts["selects"] += 1

        event.listen(engine, "before_cursor_execute", _before_cursor_execute)

        def _detach() -> None:
            event.remove(engine, "before_cursor_execute", _before_cursor_execute)

        return counts, _detach

    def test_exactly_two_selects_per_request(self, client, engine):
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        iid = _seed_instance(engine, instance_id="inst-q1")
        _seed_job(engine, instance_id=iid, queue_id="q-par")

        counts, detach = self._count_selects(engine)
        try:
            resp = client.get("/api/queues/defer-blocked")
        finally:
            detach()

        assert resp.status_code == 200
        assert counts["selects"] == 2, (
            "defer-blocked must issue exactly 2 SELECTs (witnesses + "
            f"defer-lane pending count); got {counts}"
        )

    def test_select_count_flat_as_witnesses_double(self, client, engine):
        """1 witness ⇒ 2 SELECTs; 6 witnesses ⇒ still 2 SELECTs."""
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        iid = _seed_instance(engine, instance_id="inst-flat-0")
        _seed_job(engine, instance_id=iid, queue_id="q-par",
                  job_id="job-flat-0")

        counts, detach = self._count_selects(engine)
        try:
            resp = client.get("/api/queues/defer-blocked")
        finally:
            detach()
        assert resp.status_code == 200
        assert len(resp.json()["holders"]) == 1
        assert counts["selects"] == 2, (
            f"1 witness must cost exactly 2 SELECTs; got {counts}"
        )

        # Grow the busy-set 1 → 6 witnesses: the bound is FLAT.
        for n in range(1, 6):
            iid = _seed_instance(engine, instance_id=f"inst-flat-{n}")
            _seed_job(engine, instance_id=iid, queue_id="q-par",
                      job_id=f"job-flat-{n}")

        counts, detach = self._count_selects(engine)
        try:
            resp = client.get("/api/queues/defer-blocked")
        finally:
            detach()
        assert resp.status_code == 200
        assert len(resp.json()["holders"]) == 6
        assert counts["selects"] == 2, (
            f"6 witnesses must still cost exactly 2 SELECTs (no N+1); "
            f"got {counts}"
        )
