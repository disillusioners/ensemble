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
from typing import Final

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
    """Attach a ``before_cursor_execute`` spy; return (captured, detach).

    Captures BOTH the rendered SQL statement AND the driver-bound
    ``parameters`` payload — the runtime shape of the latter is dialect-
    dependent (e.g. SQLite's positional-expanded tuple from
    ``expanding=True`` bindparams vs the dict the resolver originally
    passed). The hardened listener returns ``captured_binds`` as a list
    parallel to ``captured``, so consumers can pin BOTH the statement
    text (via :func:`_normalize_sql`) AND the bind VALUES (via
    :func:`_expected_runtime_binds`).
    """
    captured: list[str] = []
    captured_binds: list[object] = []

    def _before_cursor_execute(  # noqa: ANN001 — SQLAlchemy hook
        conn, cursor, statement, parameters, context, executemany  # noqa: ARG001
    ):
        captured.append(statement)
        captured_binds.append(parameters)

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)

    def _detach() -> None:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)

    return captured, captured_binds, _detach


#: Body-order bindparam sequence for the system-wide defer witness
#: statement (and the gate statement it is derived from). The list
#: honors each *occurrence* of a named bind in the SQL body — the same
#: rule the SQLAlchemy ``expanding`` bindparam expansion follows when
#: serializing to a driver positional tuple. Matches the textual order
#: of the ``__[POSTCOMPILE_…]`` tokens in
#: ``_idle_predicate_sql.JOB_DEFER_BUSY_BODY_SYSTEM``:
#: ``excluded_queue_types`` (1×) → ``terminal_statuses`` (2× — once in
#: the legacy clause, once in the mirror clause).
_DEFER_SYSTEM_BIND_BODY_ORDER: tuple[str, ...] = (
    "excluded_queue_types",
    "terminal_statuses",
    "terminal_statuses",
)


def _expected_runtime_binds(
    binds_dict: dict[str, object],
    body_order: tuple[str, ...] = _DEFER_SYSTEM_BIND_BODY_ORDER,
) -> tuple[object, ...]:
    """Translate a bind-DICT into the positional tuple the driver sees.

    SQLAlchemy expands ``expanding=True`` bindparams into positional
    placeholders at execution time, so the driver-bound ``parameters``
    delivered to ``before_cursor_execute`` is a tuple (not the dict the
    resolver passed in). The expansion walks the bind names in the order
    they appear in the SQL body — each occurrence contributes one
    slot per element in the bound list (a non-list bind contributes
    one slot). This helper derives the expected tuple from the bind
    dict in exactly that order, so the listener capture can be
    compared value-for-value regardless of paramstyle (named / qmark /
    pyformat).
    """
    expanded: list[object] = []
    for name in body_order:
        value = binds_dict[name]
        if isinstance(value, list):
            expanded.extend(value)
        else:
            expanded.append(value)
    return tuple(expanded)


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
# FRESH-ENGINE NEGATIVE FIXTURE — terminal instance + residual active work
# ═══════════════════════════════════════════════════════════════════════════


class TestFreshEngineNegativeFixture:
    """P0-1(b) hardening (2026-09-04): the deliberate-edit divergence case.

    A TERMINATED (or ERROR) instance with a residual active work row is
    the canary shape for a deliberate-edit regression on
    :data:`JOB_TERMINAL_STATUSES` (e.g. someone drops ``"terminated"``
    from the terminal set to silence noise). The shared predicate
    ``i.status NOT IN :terminal_statuses`` excludes terminal instances
    on BOTH the gate (``has_active_non_deferred_work``) AND the
    enumeration (``defer_busy_witness_statement``) — proving the
    dead-instance exclusion holds on both legs of the consistency
    invariant at once.

    Uses a fresh engine + fresh resolver wiring + fresh listener wiring
    (NOT the file's module-level fixtures) so this test is hermetic:
    any cross-contamination from a sibling test that holds a stale
    ``_defer_block_resolver`` global cannot poison the assertion. The
    fixture file (P0-1 hearing review) called this out as the gap
    class — the assertion was already in ``test_gate_vs_surface_matrix``
    fixture row 4 ("terminal-instance"), but that test re-uses the
    module-scoped engine. This fresh-engine shape is the redundancy
    that makes the canary a sentinel rather than a side-effect.
    """

    def test_terminated_instance_with_residual_active_job_is_not_a_witness(
        self, tmp_path
    ):
        """TERMINATED instance + active JobItem ⇒ gate open, holders empty."""
        from fastapi.testclient import TestClient
        from daemon.routers.queues import (
            router as project_queues_router,
            system_queues_router,
            set_defer_block_resolver,
        )

        # Fresh engine — file-backed SQLite, NullPool + WAL +
        # busy_timeout (the standard recipe; StaticPool + WriteGuardSession
        # is QUARANTINE.md-flagged).
        db_path = tmp_path / "defer-blocked-negative.sqlite"
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
            # Fresh resolver + fresh client (NOT the module-level
            # ``resolver`` / ``client`` fixtures — hermetic by design).
            job_repo = JobRepository(eng)
            fresh_resolver = DeferBlockResolver(job_repo=job_repo)
            set_defer_block_resolver(fresh_resolver)
            app = FastAPI()
            app.include_router(project_queues_router, prefix="/api")
            app.include_router(system_queues_router, prefix="/api")
            client = TestClient(app)

            # Two flavors of dead instance, both with residual active
            # work rows. The shape must surface as "not a witness" on
            # either side of the equality.
            _seed_queue(eng, queue_id="q-par-neg", queue_type="parallel")
            _seed_instance(
                eng,
                instance_id="inst-terminated",
                status=InstanceStatus.TERMINATED.value,
            )
            _seed_job(
                eng,
                instance_id="inst-terminated",
                queue_id="q-par-neg",
                job_id="job-on-terminated",
            )
            _seed_instance(
                eng,
                instance_id="inst-error",
                status=InstanceStatus.ERROR.value,
            )
            _seed_job(
                eng,
                instance_id="inst-error",
                queue_id="q-par-neg",
                job_id="job-on-error",
            )

            # Listener attached to the fresh engine, distinct from the
            # module-level listener machinery — proves the canary is
            # the SHARED predicate, not any listener artifact.
            captured, captured_binds, detach = _capture_statements(eng)
            try:
                resp = client.get("/api/queues/defer-blocked")
            finally:
                detach()
            assert resp.status_code == 200
            body = resp.json()

            # Both legs of the consistency invariant agree: gate NOT
            # blocked, holders empty. The shared ``i.status NOT IN
            # :terminal_statuses`` filter is the single truthmaker for
            # both sides — a deliberate edit that drops "terminated" or
            # "error" from the set breaks this assertion on BOTH the
            # gate call (``has_active_non_deferred_work``) AND the
            # witness enumeration.
            assert body["defer_blocked"] is False, (
                "TERMINATED/ERROR instance + residual active JobItem "
                "must NOT block the gate; the shared predicate excludes "
                f"terminal-instance rows by ``i.status NOT IN :terminal_statuses``. "
                f"body={body!r}"
            )
            assert body["holders"] == [], (
                "TERMINATED/ERROR instance + residual active JobItem "
                "must NOT enumerate as a holder; the witness body is "
                "derived from the gate body so the exclusion holds "
                f"here too. body={body!r}"
            )

            # The listener captured the SAME shared-composition
            # witness body — proving the assertion is anchored to the
            # production predicate, not to a test-local shortcut.
            expected_sql = _normalize_sql(
                str(_idle_predicate_sql.defer_busy_witness_statement(None))
            )
            assert any(
                _normalize_sql(s) == expected_sql for s in captured
            ), (
                "Fresh-engine listener did NOT see the shared-composition "
                "witness body — divergence risk; assert that the "
                "assertion is anchored to the production predicate."
            )
            # And the bind VALUES pin holds on this fresh engine too —
            # the same body, the same binds, no leakage from the
            # module-level engine.
            expected_binds = _expected_runtime_binds(
                _idle_predicate_sql.defer_busy_binds(None)
            )
            paired = [
                (s, b)
                for s, b in zip(captured, captured_binds, strict=True)
                if _normalize_sql(s) == expected_sql
            ]
            assert any(b == expected_binds for _, b in paired), (
                "Fresh-engine bind VALUES drifted from "
                f"defer_busy_binds(None) — expected={expected_binds!r}; "
                f"observed={[b for _, b in paired]!r}"
            )
        finally:
            eng.dispose()


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

    # ── Layer 1-LITERAL: byte-equality against a hardcoded expected ────────
    # P0-8-BE(c) hardening (2026-09-04): the derivation pin above is
    # tautological — both sides of the assertion come from the same
    # ``_unwrap_exists_body`` helper, so a bug in the helper (or a
    # future reshaping of the wrapper) keeps the assertion passing on
    # BOTH sides at once. These two tests anchor the witness bodies to
    # a LITERAL expected body string (with provenance comment to the
    # round-1 commit that introduced them), so a derivation bug that
    # survives the helper-paired pin above still flips the literal
    # byte-equality pin below.

    #: Literal expected system-wide witness body — generated from
    #: ``6d021a8c`` (round-1 BE commit, ``feat(queues): read-only
    #: GET /api/queues/defer-blocked`` — the defer-blocked
    #: transparency surface) on top of the two-body split landed in
    #: ``5e75b24f`` (round-1 hotfix ``hotfix(defer-gate):
    #: un-collapse PG ambiguous-parameter scope body``). DO NOT edit
    #: without updating the producing commit + rebuilding the literal
    #: from ``_idle_predicate_sql.JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM``
    #: via the helper at the bottom of the class. NOTE: the disjunction
    #: ``(\n      <legacy>\n      OR\n      <mirror>\n )`` becomes
    #: ``(       <legacy>       OR       <mirror>  )`` after the
    #: ``replace("\\n", " ")`` call (each ``\n`` is replaced by ONE
    #: space; the original space before/after each ``\n`` is
    #: preserved, yielding 7 spaces around ``OR`` and TWO spaces
    #: before the closing paren — preserved verbatim here).
    _EXPECTED_WITNESS_SYSTEM_BODY: Final[str] = (
        "SELECT j.job_id AS job_id, j.instance_id AS instance_id,"
        " j.agent_id AS job_agent_id, j.created_at AS job_created_at,"
        " i.status AS instance_status, i.agent_id AS instance_agent_id,"
        " i.paused_at AS instance_paused_at,"
        " i.last_activity_at AS instance_last_activity_at,"
        " i.updated_at AS instance_updated_at,"
        " i.created_at AS instance_created_at"
        " FROM job_queue_items j"
        " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
        " LEFT JOIN instances i ON j.instance_id = i.instance_id"
        " WHERE j.deleted_at IS NULL"
        " AND (q.queue_type IS NULL"
        "      OR q.queue_type NOT IN :excluded_queue_types)"
        " AND ("
        "       (j.admission_state = 'active'"
        " AND (j.instance_id IS NULL"
        " OR i.status NOT IN :terminal_statuses))"
        "       OR"
        "       (j.job_type = 'message'"
        " AND j.admission_state = 'done'"
        " AND j.instance_id IS NOT NULL"
        " AND i.status NOT IN :terminal_statuses)"
        "  )"
    )

    #: Literal expected project-scoped witness body — generated from
    #: ``6d021a8c`` (round-1 BE commit) on top of the two-body split
    #: in ``5e75b24f`` (round-1 hotfix). Same provenance rule + same
    #: trailing-whitespace note as the system-wide body above.
    _EXPECTED_WITNESS_PROJECT_BODY: Final[str] = (
        "SELECT j.job_id AS job_id, j.instance_id AS instance_id,"
        " j.agent_id AS job_agent_id, j.created_at AS job_created_at,"
        " i.status AS instance_status, i.agent_id AS instance_agent_id,"
        " i.paused_at AS instance_paused_at,"
        " i.last_activity_at AS instance_last_activity_at,"
        " i.updated_at AS instance_updated_at,"
        " i.created_at AS instance_created_at"
        " FROM job_queue_items j"
        " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
        " LEFT JOIN instances i ON j.instance_id = i.instance_id"
        " WHERE j.project_id = :project_id"
        " AND j.deleted_at IS NULL"
        " AND (q.queue_type IS NULL"
        "      OR q.queue_type NOT IN :excluded_queue_types)"
        " AND ("
        "       (j.admission_state = 'active'"
        " AND (j.instance_id IS NULL"
        " OR i.status NOT IN :terminal_statuses))"
        "       OR"
        "       (j.job_type = 'message'"
        " AND j.admission_state = 'done'"
        " AND j.instance_id IS NOT NULL"
        " AND i.status NOT IN :terminal_statuses)"
        "  )"
    )

    def test_witness_body_byte_matches_literal_system_body(self):
        """The system-wide witness body byte-matches a hardcoded
        LITERAL expected string — kills the derivation-vs-derivation
        tautology in :meth:`test_witness_body_is_derived_from_gate_body_system`.

        Provenance: generated from ``6d021a8c`` (round-1 BE commit) +
        ``5e75b24f`` (round-1 hotfix); the helper at the bottom of the
        class can rebuild the literal from the production constant
        when a deliberate edit moves the body — run that, paste the
        output into :data:`_EXPECTED_WITNESS_SYSTEM_BODY`, and the
        pin updates without losing the byte-equality sentinel."""
        assert (
            _idle_predicate_sql.JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM
            == self._EXPECTED_WITNESS_SYSTEM_BODY
        ), (
            "JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM drifted from the "
            "hardcoded literal expected — provenance: 6d021a8c + "
            "5e75b24f. If the change is intentional, run the helper "
            "``_regen_expected_witness_bodies`` (below) to rebuild "
            "the literal; if accidental, the derivation bug must be "
            "fixed in ``_idle_predicate_sql._unwrap_exists_body``."
        )

    def test_witness_body_byte_matches_literal_project_body(self):
        """Project-scoped equivalent of
        :meth:`test_witness_body_byte_matches_literal_system_body` —
        kills the tautology on the project-scoped derivation pin."""
        assert (
            _idle_predicate_sql.JOB_DEFER_BUSY_WITNESS_BODY_PROJECT
            == self._EXPECTED_WITNESS_PROJECT_BODY
        ), (
            "JOB_DEFER_BUSY_WITNESS_BODY_PROJECT drifted from the "
            "hardcoded literal expected — provenance: 6d021a8c + "
            "5e75b24f. Rebuild via the helper below if intentional."
        )

    @staticmethod
    def _regen_expected_witness_bodies() -> tuple[str, str]:
        """Print the current production bodies as Python-source-ready
        triple-quoted strings — paste into
        :data:`_EXPECTED_WITNESS_SYSTEM_BODY` /
        :data:`_EXPECTED_WITNESS_PROJECT_BODY` after a deliberate
        edit. NOT a test — call it from a REPL.

        Run as::

            uv run python -c "
            from tests.unit.routers.test_defer_blocked_api import TestConsistencyPin
            sys, proj = TestConsistencyPin._regen_expected_witness_bodies()
            print('SYSTEM:'); print(repr(sys))
            print('PROJECT:'); print(repr(proj))
            "
        """
        sys = _idle_predicate_sql.JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM
        proj = _idle_predicate_sql.JOB_DEFER_BUSY_WITNESS_BODY_PROJECT
        return sys, proj

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

        P0-1 hardening (2026-09-04): the listener also captures the
        driver-bound ``parameters`` payload and asserts it byte-matches
        the runtime expansion of :func:`defer_busy_binds` for the same
        scope. A hand-rolled statement that happens to share the
        predicate TEXT with a different bind contract fails this pin —
        e.g. someone re-binding ``excluded_queue_types`` to
        ``("defer", "background")`` would render the same statement
        but a different positional tuple.
        """
        _seed_queue(engine, queue_id="q-par", queue_type="parallel")
        iid = _seed_instance(engine, instance_id="inst-rt")
        _seed_job(engine, instance_id=iid, queue_id="q-par")

        captured, captured_binds, detach = _capture_statements(engine)
        try:
            resp = client.get("/api/queues/defer-blocked")
        finally:
            detach()
        assert resp.status_code == 200

        expected_sql = _normalize_sql(
            str(_idle_predicate_sql.defer_busy_witness_statement(None))
        )
        # Pair each captured statement with its captured binds so a
        # SQL match can be matched to the exact bind VALUES the driver
        # saw (the two arrays are kept in lockstep by the listener).
        paired = [
            (s, b) for s, b in zip(captured, captured_binds, strict=True)
        ]
        witness_executions = [
            (s, b) for s, b in paired if _normalize_sql(s) == expected_sql
        ]
        assert witness_executions, (
            "The defer-blocked route did NOT execute the shared-composition "
            "witness statement. Executed SELECTs: "
            f"{[s for s in captured if s.lstrip().upper().startswith('SELECT')]!r}"
        )

        # Bind VALUES pin: the driver saw the exact positional tuple
        # the canonical helper produces for ``project_id=None``. Any
        # value-level divergence (extra bind, missing bind, wrong list
        # cardinality, swapped list) fails here. The body-order
        # expansion rule is pinned in :func:`_expected_runtime_binds`
        # (matches the bind-NAME occurrence order in
        # ``JOB_DEFER_BUSY_BODY_SYSTEM``).
        expected_binds = _expected_runtime_binds(
            _idle_predicate_sql.defer_busy_binds(None)
        )
        assert any(b == expected_binds for _, b in witness_executions), (
            "The defer-blocked route executed the shared-composition "
            "witness statement but with WRONG bound parameters — "
            "statement-text drift is impossible without bind drift on "
            "the same body, so this is a sentinel for either a manual "
            "re-bind or a hand-rolled lookalike. "
            f"expected={expected_binds!r}; observed="
            f"{[b for _, b in witness_executions]!r}"
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

        captured, _captured_binds, detach = _capture_statements(engine)
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

        captured, _captured_binds, detach = _capture_statements(engine)
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
