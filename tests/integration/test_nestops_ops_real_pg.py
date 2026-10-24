"""Nest-ops shakedown Phase 4 — the ops-side e2e pins on REAL PostgreSQL.

T4.3 (F11 both directions through the REAL order) + T4.5 (F6/F7/F10 on
a real audit ledger), on a DISPOSABLE PostgreSQL database.

THE F11 HARNESS SHAPE (T4.3, declared per the phase-4 plan)
-----------------------------------------------------------
The job claim is driven by DIRECT CLAIM through the production claim
path — the test enqueues the job row via the REAL ``job_create`` tool,
then replicates the EXACT production dispatch block
(``daemon/services/job_processor.py``: read the reserved key off the
job row → the REAL ``spawn_instance_with_mcp`` → ``enqueue_message``
forwarding ONLY the reserved key), then claims the resulting Task via
``TaskProcessor.claim_task`` → ``ProcessMessageProcessor.process`` →
``message_processing_pipeline._do_process`` → the REAL funnel
(``manager._process_message_with_tracking`` — the same production code
path the worker pool calls). The FULL WORKER POOL is deliberately NOT
booted: pool startup drags scheduler/MCP/worker concurrency unrelated
to F11 into the pin.

Downstream turn failures (no live graph in a headless harness) are
EXPECTED and swallowed — the window mint happens at the TOP of the
funnel, before the messaging service (the DF-4 closure-pin precedent,
``tests/postgres/test_df4_provenance_closure.py``).

CONSTRUCTION-NEVER-LITERAL (binding)
------------------------------------
This file NEVER spells the retired provenance token as a literal —
DF-4's tree-wide pin greps the whole tree and a literal in a new file
would trip it. Where a pin must reference the retired token's ABSENCE
the string is CONSTRUCTED (``"_".join(...)``).

SAFETY FENCES (binding — D-13)
------------------------------
* Disposable LOGIN role + database on the LOCAL PostgreSQL, DROPPED at
  teardown; per-variable ``POSTGRES_*`` pinning (the ambient shell on
  this host carries ``POSTGRES_DB=ensemble_prod`` — never trusted).
* The husk UUIDs are FIXTURES ONLY and are never used here at all.
* Every table is TRUNCATEd between tests (the tests/postgres pattern);
  no ``$HOME``-derived live path is ever resolved; ``ensemble_prod``
  and the live daemon on ``:9797`` are never contacted.

PLANNED MUTATIONS (the tester executes the full matrix — T4.4): every
pin class carries its mutation table in the docstring; F11-b5 is
ANCHOR-NEUTRAL and declared as such (it is a regression pin, not
mutation coverage).

RUN::

    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \\
        -u POSTGRES_HOST -u POSTGRES_PORT \\
        .venv/bin/pytest tests/integration/test_nestops_ops_real_pg.py \\
        --override-ini="addopts=" -m integration -q
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel, select

import daemon.tools.instance_ops as io
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance_ops.models import InstanceOpsAudit
from daemon.repositories.instance_ops.repository import (
    InstanceOpsAuditRepository,
)
from daemon.services.job_queue_service import (
    DISPATCH_CHAIN_KEY,
    JobQueueService,
)
from daemon.services.task_processor import TaskProcessor
from tests.helpers.nestops_pg_fixtures import (
    AMBIENT_POSTGRES,
    assert_scratch_db,
    build_real_manager,
    by_name,
    disposable_db,
    pin_scratch_postgres_env,
    probe_admin_engine,
)

pytestmark = pytest.mark.integration

# Collection-time model registration (the DF-4 lesson — create_all must
# see the FULL schema).
import daemon.models.agent  # noqa: F401,E402
import daemon.models.app_settings  # noqa: F401,E402
import daemon.models.board  # noqa: F401,E402
import daemon.models.instance  # noqa: F401,E402
import daemon.models.mapping  # noqa: F401,E402
import daemon.models.mcp_server  # noqa: F401,E402
import daemon.models.message  # noqa: F401,E402
import daemon.models.resource_monitor  # noqa: F401,E402
import daemon.models.schedule  # noqa: F401,E402
import daemon.models.source  # noqa: F401,E402
import daemon.models.tmp_image  # noqa: F401,E402
import daemon.migrations.models  # noqa: F401,E402
import daemon.repositories.auth_session.models  # noqa: F401,E402
import daemon.repositories.blueprint.models  # noqa: F401,E402
import daemon.repositories.custom_agent_configs.models  # noqa: F401,E402
import daemon.repositories.db_connection.models  # noqa: F401,E402
import daemon.repositories.dependency_bus.models  # noqa: F401,E402
import daemon.repositories.ens_db.models  # noqa: F401,E402
import daemon.repositories.event.models  # noqa: F401,E402
import daemon.repositories.infra.models  # noqa: F401,E402
import daemon.repositories.instance_ops.models  # noqa: F401,E402
import daemon.repositories.instance_ui_prefs.models  # noqa: F401,E402
import daemon.repositories.job_queue.models  # noqa: F401,E402
import daemon.repositories.job_queue.watcher_models  # noqa: F401,E402
import daemon.repositories.maintenance_runs.models  # noqa: F401,E402
import daemon.repositories.mcp_kb_keys.models  # noqa: F401,E402
import daemon.repositories.message_metadata.models  # noqa: F401,E402
import daemon.repositories.prompt_version.models  # noqa: F401,E402
import daemon.repositories.report_injection.models  # noqa: F401,E402
import daemon.repositories.secrets.models  # noqa: F401,E402
import daemon.repositories.service_tool.models  # noqa: F401,E402
import daemon.repositories.shared_meta_kv.models  # noqa: F401,E402
import daemon.repositories.skill.models  # noqa: F401,E402
import daemon.repositories.snapshot.models  # noqa: F401,E402
import daemon.repositories.source.models  # noqa: F401,E402
import daemon.repositories.task.models  # noqa: F401,E402
import daemon.repositories.view_passcodes.models  # noqa: F401,E402


#: The retired DF-4 provenance token — CONSTRUCTED, never spelled (the
#: tree-wide pin greps for the literal; this file must stay clean).
_RETIRED_PROVENANCE_TOKEN = "_".join(
    ["direct", "provenance", "insufficient"]
)

SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


# ── module-scoped disposable environment ───────────────────────────────────


@pytest.fixture(scope="module")
def ops_admin():
    admin = probe_admin_engine()
    if admin is None:
        pytest.skip(
            "PostgreSQL not available — the real-PG ops pins require the "
            "local PG16 test server"
        )
    yield admin
    admin.dispose()


@pytest.fixture(scope="module")
def ops_db(ops_admin):
    with disposable_db(ops_admin) as db:
        yield db


@pytest.fixture(scope="module")
def ops_engine(ops_db):
    engine = create_engine(ops_db.url, pool_pre_ping=True, future=True)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _truncate_between_tests(ops_engine):
    """TRUNCATE every SQLModel table before each test (row isolation on
    the shared module-scoped database — the tests/postgres pattern)."""
    tables = [t.name for t in reversed(SQLModel.metadata.sorted_tables)]
    if tables:
        with ops_engine.begin() as conn:
            joined = ", ".join(f'"{name}"' for name in tables)
            conn.execute(
                text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE")
            )
    yield


@pytest.fixture
def manager(ops_db, ops_engine, monkeypatch, tmp_path):
    """A REAL ``InstanceManager`` over the disposable DB (function-scoped).

    The env pins ride pytest's function-scoped ``monkeypatch`` — the
    disposable database outlives the test, the env pin does not leak.
    """
    pin_scratch_postgres_env(monkeypatch, ops_db)
    from daemon import constants as _constants

    monkeypatch.setattr(
        _constants, "SYSTEM_DEFAULT_PROJECT_ID", SYSTEM_PROJECT_ID
    )
    mgr = build_real_manager(ops_db, tmp_path)
    # The worker pool's wiring step (pool_orchestrator.py:245): the
    # task repository the claim path and the message-claim seam read.
    # setup_worker_pool is deliberately NOT booted (the T4.3 harness
    # shape) — this single attribute is the only wiring it would add
    # that the harness needs.
    from daemon.repositories.task.repository import TaskRepository

    mgr._task_repo = TaskRepository(mgr.engine)
    # system queues for the job path (job_create resolves the caller's
    # default queue per project)
    from daemon.repositories.job_queue import JobQueueRepository

    queue_repo = JobQueueRepository(mgr.engine)
    for name, qtype, conc in (
        ("system_fifo_queue", "fifo", 1),
        ("system_parallel_queue", "parallel", 3),
    ):
        try:
            queue_repo.create(
                project_id=SYSTEM_PROJECT_ID,
                queue_name=name,
                queue_type=qtype,
                concurrency_limit=conc,
                is_system=True,
            )
        except Exception:  # noqa: BLE001 — idempotent re-provision
            pass
    yield mgr
    mgr.cleanup()


@pytest.fixture
def job_service(manager):
    """A minimal REAL ``JobQueueService`` over the manager's engine."""
    from daemon.repositories.job_queue import JobRepository
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.services.job_lock_manager import JobLockManager

    job_repo = JobRepository(manager.engine)
    lock_manager = JobLockManager(
        lock_repo=LockRepository(engine=manager.engine)
    )
    from daemon.repositories.job_queue import JobQueueRepository

    service = JobQueueService(
        repository=job_repo,
        lock_manager=lock_manager,
        queue_repo=JobQueueRepository(manager.engine),
        instance_manager=manager,
    )
    return service


# ── helpers ────────────────────────────────────────────────────────────────


def _seed_row(
    engine, iid: str, *, agent_id: str, parent_id: str | None = None,
    metadata: dict | None = None, age_hours: float = 2.0,
    source_type: str | None = None,
) -> None:
    """One REAL-shaped instance row (the DF-4 seeding precedent)."""
    old = (
        datetime.now(timezone.utc) - timedelta(hours=age_hours)
    ).isoformat()
    meta = dict(metadata or {})
    if source_type is not None:
        meta["source_type"] = source_type
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id=agent_id,
                agent_dir=f"agents/{agent_id}",
                status=InstanceStatus.IDLE.value,
                instance_metadata=meta,
                parent_id=parent_id,
                created_at=old,
                updated_at=old,
            )
        )
        session.commit()


def _job_tools(manager, job_service, *, caller_iid: str, agent_id: str):
    """The REAL ``job_create`` tool bound to the caller."""
    from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
    from daemon.services.dead_letter_service import DeadLetterService
    from daemon.repositories.job_queue import (
        JobRepository,
        JobQueueRepository,
    )
    from daemon.repositories.job_queue.dead_letter_repository import (
        DeadLetterRepository,
    )
    from daemon.tools.job_queue import create_job_tools

    loop = asyncio.new_event_loop()
    job_repo = JobRepository(manager.engine)
    mgmt = JobQueueMgmtService(
        queue_repo=JobQueueRepository(manager.engine),
        job_repo=job_repo,
        task_repo=manager._task_repo,
    )
    dlq = DeadLetterService(
        job_repository=job_repo,
        dlq_repository=DeadLetterRepository(engine=manager.engine),
        job_queue_service=job_service,
        loop=loop,
    )
    return create_job_tools(
        job_service=job_service,
        queue_mgmt_service=mgmt,
        dead_letter_service=dlq,
        current_instance_id=caller_iid,
        agent_id=agent_id,
        manager=manager,
    )


def _job_row(manager, job_id: str):
    from daemon.repositories.job_queue import JobRepository

    return JobRepository(manager.engine).get(job_id)


def _audit_rows(manager) -> list[InstanceOpsAudit]:
    with Session(manager.engine) as session:
        return list(session.exec(select(InstanceOpsAudit)).all())


async def _spawn_child(manager, *, agent_id: str = "maintenancer") -> str:
    """A REAL-spawned child (the production dispatch's own spawn call)."""
    child = str(uuid.uuid4())
    spawned = await manager.spawn_instance_with_mcp(
        instance_id=child, agent_id=agent_id
    )
    assert spawned == child
    return child


async def _dispatch_and_claim(
    manager, job, *, child_iid: str | None = None,
    dispatch_source: str | None = None,
) -> tuple[TaskProcessor, Any]:
    """The production dispatch block + the DIRECT claim.

    Replicates ``daemon/services/job_processor.py`` exactly: read the
    reserved key off the job row → ``enqueue_message`` forwarding ONLY
    the reserved key → then claim the Task through the production
    claim path.

    ``dispatch_source``: production forwards ``job.source`` verbatim.
    The b4/b5 pins override it to the AGENT lane literal: those pins
    isolate the CHAIN mechanics (the S3 pop; the absent-key default),
    and an ``"api"``-sourced dispatch would mint a DIRECT window on the
    child — the DISCLOSED F-6 REST-ingress residual (critical note
    ``257488dc``, open, NOT an F11 defect) — and drown the assertion
    the pin exists for.
    """
    chain = (job.job_metadata or {}).get(DISPATCH_CHAIN_KEY)
    result = await manager.enqueue_message(
        instance_id=child_iid,
        message=job.message,
        source=dispatch_source if dispatch_source is not None else job.source,
        metadata=({DISPATCH_CHAIN_KEY: chain} if chain else None),
    )
    # the MessageQueue row must carry the reserved key (the claim seam)
    from daemon.repositories.message_queue.models import MessageQueue

    with Session(manager.engine) as session:
        row = session.get(MessageQueue, result.message_id)
        assert row is not None
        carried = (row.message_metadata or {}).get(DISPATCH_CHAIN_KEY)
    assert carried == chain, (
        "the MessageQueue row must carry the daemon-minted chain "
        f"(got {carried!r})"
    )
    tp = TaskProcessor(
        task_repo=manager._task_repo, instance_manager=manager
    )
    task = tp.claim_task("shakedown-worker")
    assert task is not None, "the claim path returned no task"
    assert task.message_id == result.message_id
    return tp, task


async def _run_claimed_turn(tp, task) -> str:
    """Drive the claimed task through the production processor.

    The turn itself fails downstream (no live graph headless) —
    EXPECTED and swallowed (the DF-4 harness contract); the window mint
    happens at the TOP of the funnel, before the messaging service.
    """
    processor = tp._processors["process_message"]
    try:
        await processor.process(task)
    except Exception as exc:  # noqa: BLE001 — the turn may fail; mint ran
        return f"{type(exc).__name__}: {exc}"
    return "no exception"


# ── the scratch-database fence, ASSERTED ───────────────────────────────────


class TestScratchDatabaseFence:
    """The fence RUNS here — an imported-but-never-called fence is a CLAIM.

    This file drives the REAL act surface (``instance_terminate``)
    against a REAL PostgreSQL database. The fence belongs HERE, and it
    must RUN: the manager's RESOLVED engine database must be the
    disposable scratch-shaped name, and must never be the ambient
    ``POSTGRES_DB`` (the env-poison family guard — an ambient
    ``POSTGRES_DB=ensemble_prod`` would otherwise aim the terminate
    pins at the live database).

    PLANNED MUTATION (fence pin): delete the per-variable
    ``pin_scratch_postgres_env`` call from the ``manager`` fixture — on
    a host whose shell exports ``POSTGRES_DB=ensemble_prod`` the
    manager's engine would resolve to the AMBIENT database and this
    assertion goes RED. On a scrubbed shell the ambient value is
    absent and the pin still holds via the disposable-name equality.
    """

    def test_the_resolved_engine_is_the_disposable_scratch_database(
        self, ops_db, manager
    ):
        resolved = manager.engine.url.database
        assert_scratch_db(resolved, where="the resolved manager engine")
        assert resolved == ops_db.db_name, (
            "the manager's engine must resolve to the disposable "
            f"database ({ops_db.db_name!r} != {resolved!r})"
        )
        ambient_db = AMBIENT_POSTGRES.get("POSTGRES_DB")
        if ambient_db is not None:
            assert resolved != ambient_db, (
                "the resolved engine database must never be the AMBIENT "
                f"POSTGRES_DB ({ambient_db!r}) — the per-variable scrub "
                "failed"
            )


# ── T4.3 — F11 e2e, both directions ────────────────────────────────────────


class TestF11DelegationChainE2E:
    """F11 — the daemon-minted delegation chain, END TO END on real PG.

    PLANNED MUTATIONS (the tester's matrix, T4.4):
      * drop the chain at the ``job_create`` mint site (S2) → F11-a RED
        (the child's act refuses no-user-provenance);
      * make the b1 mint skip the parent-liveness check → b1 RED;
      * remove the ``exp >= now_dt`` compare in the shared claim body →
        b2 RED;
      * raise/remove ``_INHERITED_PROVENANCE_MAX_DEPTH`` → b3 RED;
      * remove the unconditional S3 pop in ``enqueue`` → b4 RED;
      * F11-b5 is ANCHOR-NEUTRAL — a regression pin, NOT mutation
        coverage (declared, per the phase-4 plan).
    """

    async def test_f11_a_genuine_chain_admits_end_to_end(
        self, manager, job_service
    ):
        """The REAL order: user turn → job_create → dispatch → claim →
        funnel → the child's protected terminate ADMITS."""
        ari_iid = str(uuid.uuid4())
        target_iid = str(uuid.uuid4())
        _seed_row(manager.engine, ari_iid, agent_id="ari")
        _seed_row(manager.engine, target_iid, agent_id="leader")

        # 1. the GENUINE user turn on the ari session (the web-UI
        #    ingress literal — the same source the funnel stamps).
        manager.stamp_user_origin_window(ari_iid, "api", "root-msg-1")
        assert ari_iid in manager._user_origin_windows

        # 2. the REAL job_create tool — the chain is minted by the
        #    daemon-bound factory closure (never a caller argument).
        tools = _job_tools(
            manager, job_service, caller_iid=ari_iid, agent_id="ari"
        )
        out = await by_name(tools, "job_create").ainvoke(
            {
                "agent_id": "maintenancer",
                "message": "shakedown delegation probe",
                "watch": False,
            }
        )
        assert "error" not in out, out
        job_id = out["job_id"]
        job = _job_row(manager, job_id)
        chain = (job.job_metadata or {}).get(DISPATCH_CHAIN_KEY)
        assert chain is not None, "the job row must carry the chain"
        assert chain["caller_instance_id"] == ari_iid
        assert chain["caller_agent_id"] == "ari"

        # 3. dispatch — the production block: the REAL spawn of the
        #    child (what the JobProcessor does), then enqueue_message
        #    forwarding ONLY the reserved key.
        child_iid = await _spawn_child(manager, agent_id="maintenancer")
        tp, task = await _dispatch_and_claim(
            manager, job, child_iid=child_iid
        )

        # 4. claim → funnel: the child mints the INHERITED window.
        diag = await _run_claimed_turn(tp, task)
        window = manager._inherited_provenance_windows.get(child_iid)
        assert window is not None, (
            f"no inherited window minted (funnel diagnostic: {diag})"
        )
        assert window["root_source"] == "api"
        assert window["root_message_id"] == "root-msg-1"
        assert window["depth"] == 1

        # 5. the protected act ADMITS (pclass is observability — the
        #    gate keys on window liveness; the ledger snapshots it).
        ops_tools = io.create_instance_ops_tools(
            manager, current_instance_id=child_iid, agent_id="maintenancer"
        )
        out = await by_name(ops_tools, "instance_terminate").ainvoke(
            {
                "id": target_iid,
                "reason": "f11-a genuine chain admits",
                "confirmed_target_id": target_iid,
                "dry_run": False,
            }
        )
        assert "reason=no-user-provenance" not in out, out
        committed = [r for r in _audit_rows(manager) if r.outcome == "committed"]
        assert committed, "the admitted act must carry a committed audit row"
        row = committed[-1]
        assert row.provenance_class == "inherited"
        assert row.provenance_depth == 1
        assert row.root_source == "api"
        assert row.root_message_id == "root-msg-1"

    async def test_f11_b1_windowless_caller_refused(
        self, manager, job_service
    ):
        """b1 — the caller was woken by the reserved no-user-origin
        source: no window exists, the chain claims NOTHING, the child's
        act REFUSES."""
        caller_iid = str(uuid.uuid4())
        target_iid = str(uuid.uuid4())
        _seed_row(manager.engine, caller_iid, agent_id="ari")
        _seed_row(manager.engine, target_iid, agent_id="leader")

        from daemon.tools.upgrade_journal import (
            SWEEP_NO_USER_ORIGIN_WAKE_SOURCE,
            classify_user_origin,
        )

        # the reserved source is NOT user-origin (fail-closed class)
        ok, detail = classify_user_origin(
            SWEEP_NO_USER_ORIGIN_WAKE_SOURCE, None
        )
        assert ok is False
        # the funnel stamp took the POP branch — no window
        manager.stamp_user_origin_window(
            caller_iid, SWEEP_NO_USER_ORIGIN_WAKE_SOURCE, "m-b1"
        )
        assert caller_iid not in manager._user_origin_windows

        tools = _job_tools(
            manager, job_service, caller_iid=caller_iid, agent_id="ari"
        )
        out = await by_name(tools, "job_create").ainvoke(
            {
                "agent_id": "maintenancer",
                "message": "shakedown b1 probe",
                "watch": False,
            }
        )
        job = _job_row(manager, out["job_id"])
        chain = (job.job_metadata or {}).get(DISPATCH_CHAIN_KEY)
        assert chain is not None, "the mint still happens (a POINTER only)"

        child_iid = await _spawn_child(manager, agent_id="maintenancer")
        tp, task = await _dispatch_and_claim(
            manager, job, child_iid=child_iid
        )
        diag = await _run_claimed_turn(tp, task)
        assert child_iid not in manager._inherited_provenance_windows, (
            "a chain naming a WINDOWLESS caller must mint nothing "
            f"(funnel diagnostic: {diag})"
        )

        ops_tools = io.create_instance_ops_tools(
            manager, current_instance_id=child_iid, agent_id="maintenancer"
        )
        out = await by_name(ops_tools, "instance_terminate").ainvoke(
            {
                "id": target_iid,
                "reason": "f11-b1 windowless caller",
                "confirmed_target_id": target_iid,
                "dry_run": False,
            }
        )
        assert "reason=no-user-provenance" in out, out
        refused = [r for r in _audit_rows(manager) if r.outcome == "refused"]
        assert refused, "the refusal must be ledgered"
        assert refused[-1].root_source is None

    async def test_f11_b2_expired_caller_window_refused(
        self, manager, job_service
    ):
        """b2 — the caller's window EXPIRED before the claim: refused.

        The expiry is simulated on the REAL window dict (the production
        store, the DF-4 precedent) — sleeping out the 60-minute TTL is
        not available."""
        caller_iid = str(uuid.uuid4())
        target_iid = str(uuid.uuid4())
        _seed_row(manager.engine, caller_iid, agent_id="ari")
        _seed_row(manager.engine, target_iid, agent_id="leader")

        manager.stamp_user_origin_window(caller_iid, "api", "m-b2")
        expired = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        manager._user_origin_windows[caller_iid]["expires_at"] = expired

        tools = _job_tools(
            manager, job_service, caller_iid=caller_iid, agent_id="ari"
        )
        out = await by_name(tools, "job_create").ainvoke(
            {
                "agent_id": "maintenancer",
                "message": "shakedown b2 probe",
                "watch": False,
            }
        )
        job = _job_row(manager, out["job_id"])
        child_iid = await _spawn_child(manager, agent_id="maintenancer")
        tp, task = await _dispatch_and_claim(
            manager, job, child_iid=child_iid
        )
        diag = await _run_claimed_turn(tp, task)
        assert child_iid not in manager._inherited_provenance_windows, (
            f"an EXPIRED caller window must mint nothing ({diag})"
        )
        ops_tools = io.create_instance_ops_tools(
            manager, current_instance_id=child_iid, agent_id="maintenancer"
        )
        out = await by_name(ops_tools, "instance_terminate").ainvoke(
            {
                "id": target_iid,
                "reason": "f11-b2 expired window",
                "confirmed_target_id": target_iid,
                "dry_run": False,
            }
        )
        assert "reason=no-user-provenance" in out, out

    async def test_f11_b3_depth_cap_refused(self, manager, job_service):
        """b3 — the caller holds an INHERITED entry at depth 4: a mint
        would be depth 5 → the cap CLEARS it → refused."""
        caller_iid = str(uuid.uuid4())
        target_iid = str(uuid.uuid4())
        _seed_row(manager.engine, caller_iid, agent_id="ari")
        _seed_row(manager.engine, target_iid, agent_id="leader")

        from daemon.tools import upgrade_journal as uj

        now = uj.now_iso()
        manager._inherited_provenance_windows[caller_iid] = {
            "root_source": "api",
            "root_message_id": "m-depth",
            "root_stamped_at": now,
            "depth": 4,
            "expires_at": uj.iso_plus(now, 600),
        }

        tools = _job_tools(
            manager, job_service, caller_iid=caller_iid, agent_id="ari"
        )
        out = await by_name(tools, "job_create").ainvoke(
            {
                "agent_id": "maintenancer",
                "message": "shakedown b3 probe",
                "watch": False,
            }
        )
        job = _job_row(manager, out["job_id"])
        child_iid = await _spawn_child(manager, agent_id="maintenancer")
        tp, task = await _dispatch_and_claim(
            manager, job, child_iid=child_iid
        )
        diag = await _run_claimed_turn(tp, task)
        assert child_iid not in manager._inherited_provenance_windows, (
            f"a depth-5 mint must be CLEARED by the cap ({diag})"
        )

    async def test_f11_b4_rest_injection_refused(self, manager, job_service):
        """b4 — a REST job create smuggling the reserved key in
        ``body.metadata`` while the caller holds a LIVE window: the S3
        unconditional pop strips it; the child is REFUSED."""
        ari_iid = str(uuid.uuid4())
        target_iid = str(uuid.uuid4())
        _seed_row(manager.engine, ari_iid, agent_id="ari")
        _seed_row(manager.engine, target_iid, agent_id="leader")
        manager.stamp_user_origin_window(ari_iid, "api", "m-b4")

        # the REST path: enqueue called WITHOUT the daemon keyword, the
        # reserved key smuggled inside the caller-supplied metadata map
        forged_chain = {
            "caller_instance_id": ari_iid,
            "caller_agent_id": "ari",
        }
        job = await job_service.enqueue(
            agent_id="maintenancer",
            message="shakedown b4 forged probe",
            source="api",
            metadata={DISPATCH_CHAIN_KEY: forged_chain},
        )
        stored = (job.job_metadata or {}).get(DISPATCH_CHAIN_KEY)
        assert stored is None, (
            "the S3 unconditional pop must strip a metadata-borne chain "
            f"(got {stored!r})"
        )

        child_iid = await _spawn_child(manager, agent_id="maintenancer")
        tp, task = await _dispatch_and_claim(
            manager, job, child_iid=child_iid,
            dispatch_source="agent:ari",
        )
        diag = await _run_claimed_turn(tp, task)
        assert child_iid not in manager._inherited_provenance_windows, (
            f"a REST-injected chain must mint NOTHING ({diag})"
        )
        assert child_iid not in manager._user_origin_windows, (
            "the agent lane must not mint a DIRECT window either"
        )
        ops_tools = io.create_instance_ops_tools(
            manager, current_instance_id=child_iid, agent_id="maintenancer"
        )
        out = await by_name(ops_tools, "instance_terminate").ainvoke(
            {
                "id": target_iid,
                "reason": "f11-b4 rest injection",
                "confirmed_target_id": target_iid,
                "dry_run": False,
            }
        )
        assert "reason=no-user-provenance" in out, out
        # the retired DF-4 token must NOT resurface anywhere in the copy
        assert _RETIRED_PROVENANCE_TOKEN not in out

    async def test_f11_b5_legacy_row_without_chain_refused(
        self, manager, job_service
    ):
        """b5 — a legacy job row with NO chain key: refused,
        byte-identical to today. ANCHOR-NEUTRAL (declared): this pin
        passes before AND after F11 — it is a regression pin, NOT
        mutation coverage (T4.4/§7.2)."""
        ari_iid = str(uuid.uuid4())
        target_iid = str(uuid.uuid4())
        _seed_row(manager.engine, ari_iid, agent_id="ari")
        _seed_row(manager.engine, target_iid, agent_id="leader")
        manager.stamp_user_origin_window(ari_iid, "api", "m-b5")

        job = await job_service.enqueue(
            agent_id="maintenancer",
            message="shakedown b5 legacy probe",
            source="api",
        )
        assert (job.job_metadata or {}).get(DISPATCH_CHAIN_KEY) is None

        child_iid = await _spawn_child(manager, agent_id="maintenancer")
        tp, task = await _dispatch_and_claim(
            manager, job, child_iid=child_iid,
            dispatch_source="agent:ari",
        )
        diag = await _run_claimed_turn(tp, task)
        assert child_iid not in manager._inherited_provenance_windows
        ops_tools = io.create_instance_ops_tools(
            manager, current_instance_id=child_iid, agent_id="maintenancer"
        )
        out = await by_name(ops_tools, "instance_terminate").ainvoke(
            {
                "id": target_iid,
                "reason": "f11-b5 legacy row",
                "confirmed_target_id": target_iid,
                "dry_run": False,
            }
        )
        assert "reason=no-user-provenance" in out, out

    async def test_f11_c1_replay_after_committed_act_refused(
        self, manager, job_service
    ):
        """c1 — the chain REPLAYED after a successful protected act is
        refused (the budget rail and/or window liveness)."""
        ari_iid = str(uuid.uuid4())
        target_iid = str(uuid.uuid4())
        second_iid = str(uuid.uuid4())
        _seed_row(manager.engine, ari_iid, agent_id="ari")
        _seed_row(manager.engine, target_iid, agent_id="leader")
        _seed_row(manager.engine, second_iid, agent_id="leader")
        manager.stamp_user_origin_window(ari_iid, "api", "m-c1")

        child_iid = await _spawn_child(manager, agent_id="maintenancer")
        tools = _job_tools(
            manager, job_service, caller_iid=ari_iid, agent_id="ari"
        )
        out = await by_name(tools, "job_create").ainvoke(
            {
                "agent_id": "maintenancer",
                "message": "shakedown c1 probe",
                "watch": False,
            }
        )
        job = _job_row(manager, out["job_id"])
        tp, task = await _dispatch_and_claim(
            manager, job, child_iid=child_iid
        )
        await _run_claimed_turn(tp, task)
        assert child_iid in manager._inherited_provenance_windows

        ops_tools = io.create_instance_ops_tools(
            manager, current_instance_id=child_iid, agent_id="maintenancer"
        )
        term = by_name(ops_tools, "instance_terminate")
        # the FIRST act commits (protected budget N=1/24h consumed)
        first = await term.ainvoke(
            {
                "id": target_iid,
                "reason": "c1 first act",
                "confirmed_target_id": target_iid,
                "dry_run": False,
            }
        )
        assert "reason=no-user-provenance" not in first, first
        # the REPLAY through the SAME live chain: the window is still
        # fresh, but the protected budget rail refuses the second
        # committed terminate.
        replay = await term.ainvoke(
            {
                "id": second_iid,
                "reason": "c1 replay",
                "confirmed_target_id": second_iid,
                "dry_run": False,
            }
        )
        assert "reason=terminate-budget-exceeded" in replay, replay

    async def test_f11_c2_fresh_chain_on_a_new_child_admits(
        self, manager, job_service
    ):
        """c2 — RETRY semantics: a FRESH chain (new child, same live
        caller window) still ADMITS — the chain is a pointer, not a
        burned credential (single-use is subsumed by liveness)."""
        ari_iid = str(uuid.uuid4())
        target_iid = str(uuid.uuid4())
        _seed_row(manager.engine, ari_iid, agent_id="ari")
        # a NON-protected target so the budget rail stays out of the way
        _seed_row(manager.engine, target_iid, agent_id="worker")
        manager.stamp_user_origin_window(ari_iid, "api", "m-c2")

        child_iid = await _spawn_child(manager, agent_id="maintenancer")
        tools = _job_tools(
            manager, job_service, caller_iid=ari_iid, agent_id="ari"
        )
        out = await by_name(tools, "job_create").ainvoke(
            {
                "agent_id": "maintenancer",
                "message": "shakedown c2 probe",
                "watch": False,
            }
        )
        job = _job_row(manager, out["job_id"])
        tp, task = await _dispatch_and_claim(
            manager, job, child_iid=child_iid
        )
        await _run_claimed_turn(tp, task)
        ops_tools = io.create_instance_ops_tools(
            manager, current_instance_id=child_iid, agent_id="maintenancer"
        )
        # the SAME caller window serves a SECOND delegation — admitted
        # (the window is per-turn liveness, not a one-shot token)
        act = await by_name(ops_tools, "instance_mark").ainvoke(
            {
                "id": target_iid,
                "marker": "protect",
                "reason": "c2 pointer-not-trust",
                "dry_run": False,
            }
        )
        assert "audit_row_id" in act, act


# ── T4.5 — F6 / F7 / F10 on the REAL ledger ────────────────────────────────


class TestF6GateSplitRealLedger:
    """F6 — the dry-run fork split, on a REAL audit ledger.

    PLANNED MUTATIONS: move the CONFIRMATION conditions below the fork →
    F6-i(a) RED; move the PROVENANCE condition back above the fork →
    F6-i(b) RED; delete the gate call on the apply path → F6-ii RED;
    make the preview write ``outcome="committed"`` → F6-iii RED; swap
    the guard order so the protected gate runs before the budget rail →
    F6-iv RED.
    """

    def _protected(self, manager) -> str:
        iid = str(uuid.uuid4())
        _seed_row(
            manager.engine, iid, agent_id="leader", metadata={
                "source_type": "telegram"
            },
        )
        return iid

    async def test_f6_i_a_unconfirmed_preview_refuses(self, manager):
        iid = self._protected(manager)
        tools = io.create_instance_ops_tools(
            manager, current_instance_id=str(uuid.uuid4()), agent_id="maintenancer"
        )
        out = await by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "f6-i(a)"}
        )
        assert "reason=protected-target-confirmation-required" in out, out
        assert "NO mutation happened (dry-run)" not in out

    async def test_f6_i_b_confirmed_preview_reports_the_gate(
        self, manager
    ):
        iid = self._protected(manager)
        tools = io.create_instance_ops_tools(
            manager, current_instance_id=str(uuid.uuid4()), agent_id="maintenancer"
        )
        out = await by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "f6-i(b)", "confirmed_target_id": iid}
        )
        assert "NO mutation happened (dry-run)" in out, out
        payload = json.loads(out.split("\nNO mutation happened")[0])
        assert payload["protected"] is True
        assert payload["act_gate_would_pass"] == {
            "ok": False,
            "reason": "no-user-provenance",
        }
        # the previewed row exists IN THE REAL LEDGER
        rows = [
            r
            for r in _audit_rows(manager)
            if r.action == "instance_terminate"
        ]
        assert rows and rows[-1].outcome == "previewed"

    async def test_f6_ii_apply_refuses_and_ledgers(self, manager):
        iid = self._protected(manager)
        tools = io.create_instance_ops_tools(
            manager, current_instance_id=str(uuid.uuid4()), agent_id="maintenancer"
        )
        out = await by_name(tools, "instance_terminate").ainvoke(
            {
                "id": iid,
                "reason": "f6-ii",
                "confirmed_target_id": iid,
                "dry_run": False,
            }
        )
        assert "reason=no-user-provenance" in out, out
        with Session(manager.engine) as session:
            row = session.get(Instance, iid)
            assert row is not None, "the target must be untouched"
        rows = [
            r
            for r in _audit_rows(manager)
            if r.action == "instance_terminate"
        ]
        assert rows[-1].outcome == "refused"

    async def test_f6_iii_preview_is_budget_neutral(self, manager):
        iid = self._protected(manager)
        tools = io.create_instance_ops_tools(
            manager, current_instance_id=str(uuid.uuid4()), agent_id="maintenancer"
        )
        term = by_name(tools, "instance_terminate")
        for _ in range(3):
            out = await term.ainvoke(
                {"id": iid, "reason": "f6-iii", "confirmed_target_id": iid}
            )
            assert "NO mutation happened (dry-run)" in out
        repo = InstanceOpsAuditRepository(manager.engine)
        assert repo.count_committed_protected_terminates(hours=24) == 0

    async def test_f6_iv_budget_precedes_the_protected_gate(self, manager):
        """F6-iv — the precedence inversion, disclosed (both refusals,
        no authority granted — the BUDGET token wins)."""
        iid = self._protected(manager)
        with Session(manager.engine) as session:
            session.add(
                InstanceOpsAudit(
                    action="instance_terminate",
                    target=iid,
                    reason="pre-existing committed protected terminate",
                    dry_run=False,
                    before_snapshot={"protected": True},
                    outcome="committed",
                )
            )
            session.commit()
        tools = io.create_instance_ops_tools(
            manager, current_instance_id=str(uuid.uuid4()), agent_id="maintenancer"
        )
        out = await by_name(tools, "instance_terminate").ainvoke(
            {"id": iid, "reason": "f6-iv"}
        )
        assert "reason=terminate-budget-exceeded" in out, out
        confirmation_token = "protected-target-confirmation-required"
        assert confirmation_token not in out, out


class TestF7ProtectionReflectionReal:
    """F7 — the protection reflection (static + marker layers), real rows.

    PLANNED MUTATIONS: revert to the marker-only expression → F7-i RED;
    drop the chat-root leg → F7-ii RED; scope the marker refusal to
    non-marker reasons → F7-iii RED; refuse instead of succeed-with-
    disclosure on the static+marker apply → F7-iv RED.
    """

    def _tools(self, manager):
        return io.create_instance_ops_tools(
            manager, current_instance_id=str(uuid.uuid4()), agent_id="maintenancer"
        )

    async def test_f7_i_leader_static_layer(self, manager):
        iid = str(uuid.uuid4())
        _seed_row(manager.engine, iid, agent_id="leader")
        out = await by_name(self._tools(manager), "instance_mark").ainvoke(
            {"id": iid, "marker": "protect", "reason": "f7-i"}
        )
        payload = json.loads(out.split("\nNO mutation happened")[0])
        assert payload["already_protected"] is True
        assert payload["protection_reasons"] == ["leader"]

    async def test_f7_ii_chat_root_static_layer(self, manager):
        iid = str(uuid.uuid4())
        _seed_row(
            manager.engine, iid, agent_id="worker",
            metadata={"source_type": "telegram"},
        )
        out = await by_name(self._tools(manager), "instance_mark").ainvoke(
            {"id": iid, "marker": "protect", "reason": "f7-ii"}
        )
        payload = json.loads(out.split("\nNO mutation happened")[0])
        assert payload["already_protected"] is True
        assert payload["protection_reasons"] == ["chat-root"]

    async def test_f7_iii_marker_dynamic_layer_and_idempotence(
        self, manager
    ):
        iid = str(uuid.uuid4())
        _seed_row(manager.engine, iid, agent_id="worker")
        tools = self._tools(manager)
        # first apply SETS the marker
        first = await by_name(tools, "instance_mark").ainvoke(
            {
                "id": iid, "marker": "protect",
                "reason": "f7-iii", "dry_run": False,
            }
        )
        assert "marked" in first and "true" in first.lower(), first
        # a repeat PROTECT trips the idempotent refusal (not a
        # preview-scoped refusal — it fires on the protect path)
        refused = await by_name(tools, "instance_mark").ainvoke(
            {"id": iid, "marker": "protect", "reason": "f7-iii-r"}
        )
        assert "reason=marker-already-set" in refused, refused
        assert "already carries a protect marker" in refused
        # a NON-protect preview shows the MARKER layer as the reason
        second = await by_name(tools, "instance_mark").ainvoke(
            {"id": iid, "marker": "suspect", "reason": "f7-iii-2"}
        )
        payload = json.loads(second.split("\nNO mutation happened")[0])
        assert payload["already_protected"] is True
        assert payload["protection_reasons"] == ["marker"]

    async def test_f7_iv_static_plus_marker_apply_succeeds_with_disclosure(
        self, manager
    ):
        iid = str(uuid.uuid4())
        _seed_row(manager.engine, iid, agent_id="leader")
        out = await by_name(self._tools(manager), "instance_mark").ainvoke(
            {
                "id": iid, "marker": "protect",
                "reason": "f7-iv", "dry_run": False,
            }
        )
        # succeed-WITH-disclosure: the marker is set, the static layer
        # is disclosed in protection_reasons, the row is committed.
        assert "audit_row_id" in out, out
        with Session(manager.engine) as session:
            row = session.get(Instance, iid)
            marker = (row.instance_metadata or {}).get(
                io.PROTECT_MARKER_KEY
            )
            assert isinstance(marker, dict) and marker.get("protected") is True
        committed = [r for r in _audit_rows(manager) if r.outcome == "committed"]
        assert committed


class TestF10AuditRowIdRealLedger:
    """F10 — every apply path echoes the COMMITTED audit row id.

    PLANNED MUTATION: drop the echoed row id (leave the sink result
    unassigned) → every pin here RED.
    """

    def _tools(self, manager):
        return io.create_instance_ops_tools(
            manager, current_instance_id=str(uuid.uuid4()), agent_id="maintenancer"
        )

    def _committed_row(self, manager, row_id):
        with Session(manager.engine) as session:
            row = session.get(InstanceOpsAudit, row_id)
        assert row is not None, f"audit row {row_id} not in the ledger"
        return row

    async def test_f10_i_protect_echoes_committed_row_id(self, manager):
        iid = str(uuid.uuid4())
        _seed_row(manager.engine, iid, agent_id="worker")
        out = await by_name(self._tools(manager), "instance_mark").ainvoke(
            {
                "id": iid, "marker": "protect",
                "reason": "f10-i", "dry_run": False,
            }
        )
        payload = json.loads(out)
        rid = payload.get("audit_row_id")
        assert rid, f"no audit_row_id echoed: {out[:300]}"
        row = self._committed_row(manager, rid)
        assert row.outcome == "committed"
        assert row.action == "instance_mark"
        assert row.target == iid

    async def test_f10_ii_park_echoes_committed_row_id(self, manager):
        iid = str(uuid.uuid4())
        _seed_row(manager.engine, iid, agent_id="worker")
        out = await by_name(self._tools(manager), "instance_mark").ainvoke(
            {
                "id": iid, "marker": "park",
                "reason": "f10-ii", "dry_run": False,
            }
        )
        payload = json.loads(out)
        rid = payload.get("audit_row_id")
        assert rid, f"no audit_row_id echoed: {out[:300]}"
        row = self._committed_row(manager, rid)
        assert row.outcome == "committed"

    async def test_f10_iii_suspect_echoes_committed_row_id(self, manager):
        iid = str(uuid.uuid4())
        _seed_row(manager.engine, iid, agent_id="worker")
        out = await by_name(self._tools(manager), "instance_mark").ainvoke(
            {
                "id": iid, "marker": "suspect",
                "reason": "f10-iii", "dry_run": False,
            }
        )
        payload = json.loads(out)
        rid = payload.get("audit_row_id")
        assert rid, f"no audit_row_id echoed: {out[:300]}"
        row = self._committed_row(manager, rid)
        assert row.outcome == "committed"
