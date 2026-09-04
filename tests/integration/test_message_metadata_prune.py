"""T5.19 — ``message_metadata`` side-table prune acceptance test (real PG).

🔴 MERGE PRECONDITION (plan-overview.md Scope bullet + architect §3 /
§8.1, phase5-plan.md T5.19): without the prune, PR2's side table ships
unbounded growth to v2 prod (≈ 2–4 rows/turn × turns × instances,
forever — no FK on either backend, so nothing cascades into the table).

Deliberate-non-action semantics (PR2 review §3 — documented here per
T5.19 acceptance item 4):

* Operation D checkpoint-prune orphans are TOLERATED. When Operation D
  prunes old checkpoints, the instance is still ALIVE, so its
  ``message_metadata`` rows stay — those rows are over-record-only and
  NEVER join the read path (the PR3 read flip resolves timestamps only
  for messages surfaced from LIVE checkpoints). A sweep of them is a
  possible follow-up, not a correctness requirement.
* Pinned / revivable instances keep their rows permanently by design.
  Terminal-but-revivable instances must keep their timestamps so a
  revive-on-send render is identical to the pre-terminate read; there
  is no FK from ``message_metadata`` to ``instances`` on either
  backend, so the ONLY sanctioned bulk removal is the explicit
  ``delete_for_thread`` call wired into
  ``maintenance.py::_cleanup_instance`` (AFTER ``adelete_thread``,
  BEFORE the in-memory callback) for instances that are being FULLY
  deleted.

OPTIONAL orphan sweep (W3 — non-gating follow-up, do NOT gate anything
on this; documented per phase5-plan.md T5.19 "OPTIONAL orphan sweep")::

    SELECT count(*) FROM message_metadata mm
      LEFT JOIN checkpoints ck ON ck.thread_id = mm.thread_id
      WHERE ck.thread_id IS NULL;

Harness honesty contract (mirrors
``tests/integration/checkpoint_prune_real_saver.py``): a REAL
``AsyncPostgresSaver`` on a disposable per-test database creates the
real checkpoint rows; ``MessageMetadataRepository.upsert_batch`` (the
tap's storage call) deterministically simulates the tap writing the
side table; the REAL ``CheckpointCleanupJob._cleanup_instance`` runs
its actual ordering (instance delete → ``adelete_thread`` → prune →
in-memory callback) against a real instance repository (file-backed
SQLite, the repo's file-backed recipe) and the real adapter. The
Dijkstra point of the suite is the REAL ordering — ``adelete_thread``
is never mocked away. PG unreachable → loud skip; a skip is NOT green
for the merge gate.

Failure semantics proven (W3): ``adelete_thread`` success + prune
failure MUST NOT raise out of ``_cleanup_instance`` — the guard case
below monkeypatches ``delete_for_thread`` to raise and asserts the
cleanup still completes, checkpoints are gone, the orphaned side-table
rows are tolerated, and the WARNING is logged.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from tests.helpers.checkpoint_prune_pg import (
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)


# ── fixtures (binding-gate pattern: real PG, disposable DB per test) ──────────


@pytest.fixture(autouse=True)
def _real_langgraph():
    """Evict the root-conftest langgraph mocks for this module (repo pattern)."""
    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


async def _probe_pg_or_skip():
    import asyncpg

    from tests.helpers.checkpoint_prune_pg import ADMIN_DSN

    try:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=5)
        await conn.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"T5.19 prune test SKIPPED — PostgreSQL not available at {ADMIN_DSN} "
            f"({type(exc).__name__}: {exc}). A skip is NOT green for the T5.19 "
            "merge precondition: start PostgreSQL and re-run "
            "tests/integration/test_message_metadata_prune.py."
        )


@pytest.fixture
async def pg_db():
    """Disposable database per test (safe under xdist; never touches shared DBs)."""
    from tests.helpers.checkpoint_prune_pg import create_disposable_db, drop_database

    await _probe_pg_or_skip()
    name, dsn = await create_disposable_db()
    try:
        yield name, dsn
    finally:
        await drop_database(name)


@pytest.fixture
async def stack(pg_db):
    """The production-shaped checkpointer stack on the disposable DB."""
    from tests.helpers.checkpoint_prune_pg import real_pg_checkpointer

    name, dsn = pg_db
    async with real_pg_checkpointer(name, dsn) as (saver, pool, adapter):
        yield saver, pool, adapter


@pytest.fixture
def meta_repo(pg_db):
    """SYNC MessageMetadataRepository on the SAME disposable PG database.

    The engine is the daemon's sync convention (``postgresql+psycopg://``,
    matching ``factory.create_postgres_engine``). Only the
    ``message_metadata`` table is created here — the checkpoint schema
    belongs to the saver's ``setup()``.
    """
    from daemon.repositories.message_metadata.models import MessageMetadata
    from daemon.repositories.message_metadata.repository import (
        MessageMetadataRepository,
    )

    _name, dsn = pg_db
    url = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    eng = create_engine(url, poolclass=NullPool, pool_pre_ping=True)
    SQLModel.metadata.create_all(eng, tables=[MessageMetadata.__table__])
    try:
        yield MessageMetadataRepository(engine=eng)
    finally:
        eng.dispose()


@pytest.fixture
def instance_repo_engine(tmp_path):
    """Real instance repository engine — file-backed SQLite (repo recipe).

    File-backed SQLite at ``tmp_path`` with NullPool + WAL pragmas — NOT
    ``StaticPool``/``:memory:`` (the forbidden cross-thread lost-write
    pattern). Importing the instance repository module registers every
    cascade-dependency model, so ``create_all`` builds the full schema
    the real ``delete()`` cascade touches.
    """
    import daemon.repositories.instance.repository  # noqa: F401 (register tables)

    eng = create_engine(
        f"sqlite:///{tmp_path}/message_metadata_prune.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


# ── helpers ───────────────────────────────────────────────────────────────────


def build_graph(saver):
    """Real single-node StateGraph on the real saver (proven binding-gate shape)."""
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    def step(state: MessagesState):
        return {"messages": [AIMessage(f"reply-{len(state['messages'])}")]}

    g = StateGraph(MessagesState)
    g.add_node("step", step)
    g.add_edge(START, "step")
    g.add_edge("step", END)
    return g.compile(checkpointer=saver)


async def populate_real_thread(saver, thread_id: str, turns: int = 2) -> None:
    """Run real turns so the saver writes REAL checkpoint rows for the thread."""
    from langchain_core.messages import HumanMessage

    graph = build_graph(saver)
    config = {"configurable": {"thread_id": thread_id}}
    for i in range(turns):
        await graph.ainvoke({"messages": [HumanMessage(f"hello-{i}")]}, config)


def write_tap_rows(repo, thread_id: str, n: int = 3) -> list[str]:
    """Simulate the tap deterministically: N side-table rows via upsert_batch.

    ``upsert_batch`` IS the tap's storage call — invoking it directly is
    the deterministic simulation of the MessageTapSlot firing (no graph
    tap placement needed to prove the prune).

    Note: the upsert's RETURN VALUE is deliberately not asserted — on
    the PG dialect a multi-row insert runs as insertmanyvalues/
    executemany, where ``rowcount`` is not aggregated (returns -1).
    Presence is asserted via ``get_for_thread`` instead (pre-existing
    repo behavior; not a T5.19 regression).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    items = [(f"{uuid.uuid4()}", now_iso, None) for _ in range(n)]
    repo.upsert_batch(thread_id, items)
    return [mid for (mid, _ts, _seq) in items]


def make_job(checkpointer, instance_repo, meta_repo, on_deleted=None):
    """The REAL CheckpointCleanupJob with the real production wiring shape."""
    from daemon.config import PersistenceConfig
    from daemon.services.maintenance import CheckpointCleanupJob

    return CheckpointCleanupJob(
        config=PersistenceConfig(),
        checkpointer=checkpointer,
        instance_repo=instance_repo,
        on_instance_deleted=on_deleted,
        message_metadata_repo=meta_repo,
    )


def seed_terminal_instance(engine, instance_id: str):
    """Seed a terminal (completed) instance row the TOCTOU guard accepts."""
    from daemon.repositories.instance.models import Instance, InstanceStatus
    from sqlmodel import Session

    inst = Instance(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        status=InstanceStatus.COMPLETED.value,
        version=1,
        instance_metadata={},
    )
    with Session(engine) as session:
        session.add(inst)
        session.commit()
    return inst


def build_instance_repo(instance_repo_engine):
    from daemon.repositories.instance.repository import SQLModelInstanceRepository

    return SQLModelInstanceRepository(instance_repo_engine)


async def thread_row_counts(pool, thread_id: str) -> tuple[int, int]:
    """(checkpoints, checkpoint_blobs) row counts for the thread, via real SQL."""
    async with pool.acquire() as conn:
        n_ck = await conn.fetchval(
            "SELECT count(*) FROM checkpoints WHERE thread_id=$1", thread_id
        )
        n_blobs = await conn.fetchval(
            "SELECT count(*) FROM checkpoint_blobs WHERE thread_id=$1", thread_id
        )
    return int(n_ck), int(n_blobs)


# ── tests ─────────────────────────────────────────────────────────────────────


class TestMessageMetadataPrune:
    """T5.19 acceptance: populate → tap → N rows → _cleanup_instance → 0 rows."""

    @pytest.mark.asyncio
    async def test_prune_drops_side_table_rows_after_cleanup(
        self, stack, meta_repo, instance_repo_engine
    ):
        """THE acceptance test: real checkpoints + N tap rows → cleanup → 0 rows."""
        saver, pool, adapter = stack

        thread_id = uuid.uuid4().hex
        await populate_real_thread(saver, thread_id, turns=2)
        n_ck_before, _ = await thread_row_counts(pool, thread_id)
        assert n_ck_before > 0, "harness bug: real saver wrote no checkpoints"

        write_tap_rows(meta_repo, thread_id, n=3)
        assert len(meta_repo.get_for_thread(thread_id)) == 3

        instance_repo = build_instance_repo(instance_repo_engine)
        seed_terminal_instance(instance_repo_engine, thread_id)

        # Ordering proof: when the in-memory callback fires, the side
        # table must ALREADY be pruned (prune sits BEFORE the callback).
        observed: dict = {}

        def on_deleted(instance_id: str) -> None:
            observed["called"] = True
            observed["side_rows_at_callback"] = dict(
                meta_repo.get_for_thread(instance_id)
            )

        job = make_job(adapter, instance_repo, meta_repo, on_deleted)
        await job._cleanup_instance(thread_id)  # the REAL function, real ordering

        # Side table pruned to zero for the deleted thread.
        assert meta_repo.get_for_thread(thread_id) == {}
        # The thread's checkpoints are gone (real adelete_thread ran).
        n_ck, n_blobs = await thread_row_counts(pool, thread_id)
        assert n_ck == 0
        assert n_blobs == 0
        # The instance row is really gone (real repo delete cascade ran).
        assert instance_repo.get(thread_id) is None
        # The callback fired AFTER the prune (rows already 0 at callback time).
        assert observed.get("called") is True
        assert observed["side_rows_at_callback"] == {}

    @pytest.mark.asyncio
    async def test_prune_failure_never_raises_out_of_cleanup(
        self, stack, meta_repo, instance_repo_engine, monkeypatch, caplog
    ):
        """W3 guard: prune failure → no raise, orphans tolerated, WARNING logged."""
        from daemon.repositories.message_metadata.repository import (
            MessageMetadataRepository,
        )

        saver, pool, adapter = stack

        thread_id = uuid.uuid4().hex
        await populate_real_thread(saver, thread_id, turns=2)
        write_tap_rows(meta_repo, thread_id, n=3)
        assert len(meta_repo.get_for_thread(thread_id)) == 3

        instance_repo = build_instance_repo(instance_repo_engine)
        seed_terminal_instance(instance_repo_engine, thread_id)
        observed: dict = {}

        def on_deleted(instance_id: str) -> None:
            observed["called"] = True

        def explode(self, thread_id: str) -> int:
            raise RuntimeError("prune backend exploded")

        monkeypatch.setattr(MessageMetadataRepository, "delete_for_thread", explode)

        job = make_job(adapter, instance_repo, meta_repo, on_deleted)
        with caplog.at_level(logging.WARNING, logger="daemon.services.maintenance"):
            await job._cleanup_instance(thread_id)  # MUST NOT raise

        # adelete_thread still ran — checkpoints are gone.
        n_ck, _n_blobs = await thread_row_counts(pool, thread_id)
        assert n_ck == 0
        # The prune failed → rows stay (over-record-only orphans, tolerated).
        assert len(meta_repo.get_for_thread(thread_id)) == 3
        # Cleanup continued past the prune to the in-memory callback.
        assert observed.get("called") is True
        # The never-raise WARNING is logged with the tolerance note.
        assert "orphans tolerated (never-raise guard)" in caplog.text

    @pytest.mark.asyncio
    async def test_prune_repo_none_is_backward_compatible_no_op(
        self, stack, meta_repo, instance_repo_engine
    ):
        """Default ``message_metadata_repo=None``: cleanup runs, prune skipped."""
        saver, pool, adapter = stack

        thread_id = uuid.uuid4().hex
        await populate_real_thread(saver, thread_id, turns=2)
        write_tap_rows(meta_repo, thread_id, n=3)

        instance_repo = build_instance_repo(instance_repo_engine)
        seed_terminal_instance(instance_repo_engine, thread_id)
        observed: dict = {}

        job = make_job(adapter, instance_repo, None, lambda iid: observed.update(called=True))
        await job._cleanup_instance(thread_id)

        # Checkpoints still cleaned; side-table rows untouched (repo absent).
        n_ck, _ = await thread_row_counts(pool, thread_id)
        assert n_ck == 0
        assert len(meta_repo.get_for_thread(thread_id)) == 3
        assert observed.get("called") is True


class TestDeleteForThreadSQLiteBackend:
    """Dialect coverage: the SQLite path of ``delete_for_thread`` (B1 both-backends).

    The PG path is proven on the real disposable PG above; this class
    proves the SQLite path with the same SYNC repo + real engine (the
    DELETE is dialect-portable core SQL — one statement, two dialects).
    """

    def test_delete_for_thread_sqlite_dialect(self, tmp_path):
        from daemon.repositories.message_metadata.models import MessageMetadata
        from daemon.repositories.message_metadata.repository import (
            MessageMetadataRepository,
        )

        eng = create_engine(
            f"sqlite:///{tmp_path}/meta_sqlite_dialect.db",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _rec):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

        SQLModel.metadata.create_all(eng, tables=[MessageMetadata.__table__])
        try:
            repo = MessageMetadataRepository(engine=eng)
            thread_id = uuid.uuid4().hex
            other_thread = uuid.uuid4().hex

            write_tap_rows(repo, thread_id, n=3)
            write_tap_rows(repo, other_thread, n=1)

            # Scoped delete: only the target thread's rows go.
            deleted = repo.delete_for_thread(thread_id)
            assert deleted == 3
            assert repo.get_for_thread(thread_id) == {}
            assert len(repo.get_for_thread(other_thread)) == 1

            # Missing thread → no-op returning 0 (get_for_thread parity).
            assert repo.delete_for_thread(uuid.uuid4().hex) == 0
        finally:
            eng.dispose()
