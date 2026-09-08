"""Integration tests — explorer Shared Context injection on ALL invocation
paths (acceptance items 3 & 4, flag-ON real-service proof + no-double-injection
audit for the three explorer paths).

Under test: branch ``feature/explorer-shared-context-injection``.

What these tests pin END-TO-END (no mock of the injection/partition layer):

* The REAL ``InstanceMessagingService`` enqueue path
  (``enqueue_message`` → ``_prepare_enqueued_message`` → real MessageQueue +
  Task rows + IDLE/COMPLETED → RUNNING transition on a real SQLite DB).
* The REAL messaging-path injection assembly
  (``_process_message_with_tracking`` → real row read of ``parent_id`` →
  real ``assemble_context_messages`` → real ``_resolve_tree_root_id`` via
  ``InstanceRepository.get_tree_root_id`` → real ``get_shared_context``
  filesystem matching → real ``build_shared_context_message``).
* The REAL agent registry: ``agents/explorer/meta.json`` (opt-in flag ON)
  and ``agents/developer/meta.json`` (no opt-in flag) are loaded through
  the real ``daemon.registry.get_registry()`` — no meta fixtures.

Mocked boundaries (external engines only, mirroring repo conventions):
the LangGraph graph object (``astream`` fake — no LLM), the checkpoint
probe (``_has_checkpoint`` → False, ``_maybe_compact_context`` no-op), and
SSE hub / worker-pool notifications. The instance + message-queue +
task tables are REAL (file-backed SQLite at ``tmp_path``, ``NullPool``,
``PRAGMA journal_mode=WAL``, ``PRAGMA busy_timeout=10000`` — repo recipe;
never ``StaticPool`` + ``WriteGuardSession``, QUARANTINE.md).

The shared-context partition directory is the REAL canonical location
``{tempdir}/ensemble/context/{context_key}`` (``resolve_context_dir``) —
seeded with a real ``.md`` file for the TREE ROOT id only, so a block that
fires proves the partition resolved from the tree root (the block body
carries ``context_key: {root_id}``), not from the child's own id.

ATTRIBUTION: tester evidence pack, 2026-09-08 — adopted into the
repo on branch ``feature/explorer-shared-context-injection`` as
durable end-to-end coverage for the explorer-shared-context
migration. This is the ONLY end-to-end tree-root assertion
coverage for the fix; the unit tests pin individual seams
(child / root / bug-exercising / flag-ON / flag-OFF) but only
this pack exercises the real ``enqueue_message`` →
``_process_message_with_tracking`` → orchestrator →
partition-read path together.

Covered acceptance rows:

* 3-ON: spawn-shaped project-linked child, first message via the enqueue
  path → exactly one ``[SYSTEM CONTEXT: Shared Context]`` block, resolved
  from the TREE-ROOT partition.
* 3-OFF: real agent WITHOUT the opt-in gate (``developer`` meta.json —
  no ``context_injection`` block) → no block, even though the partition
  directory exists with matchable files.
* 4c (revive, no-drop): terminal (COMPLETED) child with NO prior
  injection → revived via the enqueue path → block fires exactly once.
* 4c (revive, no-duplicate): terminal child whose first turn already
  injected (``project_injected=True``) → revived → NO new block this turn
  (once-per-instance contract prevents duplication).
* Root unchanged: a root instance (``parent_id=None``) still resolves its
  own partition the old way.
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.context_tools import resolve_context_dir
from daemon.services.instance_messaging import InstanceMessagingService

CONTEXT_KIND_SHARED_CONTEXT = "shared_context"
SHARED_BLOCK_PREFIX = "[SYSTEM CONTEXT: Shared Context]"

# A real agent WITH meta.json but WITHOUT the shared-context opt-in flag
# (verified: agents/developer/meta.json has no ``context_injection`` block).
NON_OPTED_AGENT_ID = "developer"
OPTED_IN_AGENT_ID = "explorer"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout — repo recipe)."""
    db_path = tmp_path / "explorer-ctx-paths.sqlite"
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


@asynccontextmanager
async def _null_semaphore():
    yield


class _RecordingGraph:
    """Fake LangGraph: records ``graph_input`` and ends the stream.

    This mocks ONLY the external LLM/graph engine — the injection layer
    upstream of ``astream`` runs for real. ``graph_input['messages']``
    is exactly what the messaging path assembled (persistent context
    block + user message).
    """

    def __init__(self):
        self.captured_inputs: list[dict] = []

    async def astream(self, graph_input, config=None, **kwargs):
        import copy

        self.captured_inputs.append(copy.deepcopy(graph_input))
        return
        yield  # pragma: no cover — makes this an async generator

    async def aget_state(self, config):
        return None


def _seed_instance(
    engine: Engine,
    instance_repo: SQLModelInstanceRepository,
    *,
    instance_id: str,
    agent_id: str,
    parent_id: str | None,
    status: str = InstanceStatus.IDLE.value,
    project_id: str | None = "proj-integ-1",
    metadata: dict | None = None,
) -> str:
    """Insert a REAL instance row (spawn-shaped child / root)."""
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata=metadata
                if metadata is not None
                else ({"project_id": project_id} if project_id else {}),
            )
        )
        session.commit()
    return instance_id


def _build_manager(engine: Engine, instance_repo, queue_repo) -> MagicMock:
    """Manager harness: REAL engine + REAL instance/queue repos; everything
    outside the injection/partition layer mocked (SSE hub, worker pool,
    project repo stubs — all safe-empty returns)."""
    manager = MagicMock()
    manager.engine = engine
    from daemon.write_pause_guard import WritePauseGuard

    manager.write_guard = WritePauseGuard()
    manager.config.limits.graph_recursion_limit = 50
    manager.config.compaction = MagicMock()
    manager._instance_repository = instance_repo
    manager._queue_repository = queue_repo
    manager._graph_tasks = {}
    manager.source_dispatcher = None
    manager._llm_semaphore = _null_semaphore()
    manager._worker_pool = None
    manager._skill_injection_service = None
    manager._skill_repo = None
    manager._skill_clone_service = None
    manager._deferred_question_pause = {}
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._live_hub.stream_error = AsyncMock()
    manager._live_hub.stream_tool_result = AsyncMock()

    # Project repo: safe-empty (no project payload → no project block;
    # keeps the assertions focused on the Shared Context block).
    manager._project_repository = MagicMock()
    manager._project_repository.get = MagicMock(return_value=None)
    manager._project_repository.list_critical_notes = MagicMock(return_value=[])
    manager._project_repository.get_recent_history = MagicMock(return_value=[])
    manager._project_repository.get_metadata = MagicMock(return_value=False)
    manager._project_repository.match_by_keywords = MagicMock(return_value=[])

    # Shared-meta KV repo: empty partition metadata.
    manager._shared_meta_kv_repo = MagicMock()
    manager._shared_meta_kv_repo.get_all_as_dict = MagicMock(return_value={})

    # Job queue service: MagicMock (stamp_message_id no-op) — nothing on
    # this seam is under test.
    manager._job_queue_service = MagicMock()
    return manager


def _build_service(manager: MagicMock, graph: _RecordingGraph) -> InstanceMessagingService:
    """Real InstanceMessagingService wired to the manager harness.

    Checkpoint probe + compaction stubbed (external checkpoint engine);
    the injection/partition layer is NOT touched.
    """
    svc = InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )
    svc._has_checkpoint = AsyncMock(return_value=False)
    svc._maybe_compact_context = AsyncMock()
    manager.get_instance = AsyncMock(return_value=graph)
    return svc


class _SharedContextPartition:
    """Create + clean the REAL canonical shared-context dir for a key."""

    def __init__(self, context_key: str):
        self.context_key = context_key
        self.dir = resolve_context_dir(context_key)
        self._created = False

    def __enter__(self) -> "_SharedContextPartition":
        self.dir.mkdir(parents=True, exist_ok=True)
        self._created = True
        (self.dir / "database-architecture-guide.md").write_text(
            "# Test Shared Note\n\n"
            "Pinned integration-test content about the database layer.\n",
            encoding="utf-8",
        )
        return self

    def __exit__(self, *exc) -> None:
        if self._created:
            shutil.rmtree(self.dir, ignore_errors=True)


def _shared_context_messages(graph_input: dict) -> list:
    """Extract the Shared Context blocks from an assembled graph_input."""
    return [
        m
        for m in graph_input.get("messages", [])
        if getattr(m, "additional_kwargs", {}).get("context_kind")
        == CONTEXT_KIND_SHARED_CONTEXT
    ]


def _make_tree(engine: Engine, repo: SQLModelInstanceRepository) -> tuple[str, str, str]:
    """Seed a 3-level tree: root ← mid ← child. Returns (root, mid, child)."""
    root_id = f"root-{uuid.uuid4()}"
    mid_id = f"mid-{uuid.uuid4()}"
    child_id = f"child-{uuid.uuid4()}"
    _seed_instance(engine, repo, instance_id=root_id, agent_id="leader", parent_id=None)
    _seed_instance(engine, repo, instance_id=mid_id, agent_id="worker", parent_id=root_id)
    _seed_instance(
        engine,
        repo,
        instance_id=child_id,
        agent_id=OPTED_IN_AGENT_ID,
        parent_id=mid_id,
    )
    return root_id, mid_id, child_id


# ─────────────────────────────────────────────────────────────────────────────
# Item 3 — flag-ON through the REAL service: tree-root partition resolution
# ─────────────────────────────────────────────────────────────────────────────


class TestFlagOnChildTreeRootPartitionViaEnqueue:
    """Spawn-shaped project-linked child + first enqueued message → the
    block fires, resolved from the TREE-ROOT partition (real service,
    real partition resolution, real explorer meta.json)."""

    @pytest.mark.asyncio
    async def test_opted_in_child_first_enqueue_block_from_tree_root_partition(
        self, engine
    ):
        repo = SQLModelInstanceRepository(engine)
        queue_repo = MagicMock()
        root_id, mid_id, child_id = _make_tree(engine, repo)

        manager = _build_manager(engine, repo, queue_repo)
        graph = _RecordingGraph()
        svc = _build_service(manager, graph)

        # Seed the partition under the TREE ROOT only. If the injection
        # resolved from the child's own (empty) partition, the block could
        # not carry the root's context_key.
        with _SharedContextPartition(root_id):
            enqueue_result = await svc.enqueue_message(
                child_id,
                "How does the database architecture guide say the database layer works?",
                source="api",
            )
            assert enqueue_result.message_id, "enqueue must mint a message id"

            await svc._process_message_with_tracking(
                instance_id=child_id,
                message="How does the database architecture guide say the database layer works?",
                message_id=enqueue_result.message_id,
                is_retry=False,
                message_source="api",
            )

        assert graph.captured_inputs, (
            "graph turn never ran — harness broke, injection never assembled"
        )
        graph_input = graph.captured_inputs[0]
        blocks = _shared_context_messages(graph_input)

        assert len(blocks) == 1, (
            f"expected EXACTLY ONE Shared Context block on the child's first "
            f"enqueued turn; got {len(blocks)}: "
            f"{[m.content[:80] for m in blocks]!r}"
        )
        block = blocks[0]
        assert block.content.startswith(SHARED_BLOCK_PREFIX), (
            f"block must use the canonical [SYSTEM CONTEXT: Shared Context] "
            f"prefix; got: {block.content[:120]!r}"
        )
        # Partition identity: the RAG payload stamps the resolved
        # context_key — it must be the TREE ROOT, not the child (and not
        # the mid node).
        assert f"context_key: {root_id}" in block.content, (
            f"Shared Context block must be resolved from the TREE-ROOT "
            f"partition {root_id!r}; block content was: {block.content[:300]!r}"
        )
        assert f"context_key: {child_id}" not in block.content, (
            "child's own partition must NOT be used (mispartition regression)"
        )
        assert f"context_key: {mid_id}" not in block.content

        # The enqueued message really is a first-class durable receipt:
        # the enqueue prelude wrote the Task row and flipped the child to
        # RUNNING (auto-resume) — the enqueue→process seam is real.
        row = repo.get(child_id)
        assert row is not None and row.status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_non_opted_in_agent_gets_no_shared_context_block(self, engine):
        """Negative: a real agent WITHOUT the opt-in gate gets NO block.

        ``agents/developer/meta.json`` has no ``context_injection`` block
        → the registry resolves ``heuristic_match_shared_md_files=False``
        → the orchestrator must not fire the block even though the
        partition directory exists with matchable files.
        """
        repo = SQLModelInstanceRepository(engine)
        queue_repo = MagicMock()
        root_id, mid_id, child_id = _make_tree(engine, repo)
        # Re-shape the child as the NON-opted agent (real meta.json).
        with Session(engine) as session:
            row = session.get(Instance, child_id)
            row.agent_id = NON_OPTED_AGENT_ID
            row.agent_name = NON_OPTED_AGENT_ID
            session.add(row)
            session.commit()

        manager = _build_manager(engine, repo, queue_repo)
        graph = _RecordingGraph()
        svc = _build_service(manager, graph)

        with _SharedContextPartition(root_id):
            result = await svc.enqueue_message(child_id, "database architecture?", source="api")
            await svc._process_message_with_tracking(
                instance_id=child_id,
                message="database architecture?",
                message_id=result.message_id,
                is_retry=False,
                message_source="api",
            )

        assert graph.captured_inputs, "graph turn never ran — harness broke"
        for gi in graph.captured_inputs:
            blocks = _shared_context_messages(gi)
            assert blocks == [], (
                f"non-opted agent ({NON_OPTED_AGENT_ID}) must NOT receive the "
                f"Shared Context block; got: {[m.content[:120] for m in blocks]!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Item 4c — revive / terminal→enqueue path: no drop, no duplicate
# ─────────────────────────────────────────────────────────────────────────────


class TestReviveTerminalEnqueueExactlyOnce:
    """Terminal (COMPLETED) child revived via the enqueue path."""

    @pytest.mark.asyncio
    async def test_terminal_child_revived_block_fires_exactly_once(self, engine):
        """No DROP: a terminal child that never had a first turn
        (no ``project_injected`` flag) gets the block on its revived
        first enqueued turn — exactly once."""
        repo = SQLModelInstanceRepository(engine)
        queue_repo = MagicMock()
        root_id, mid_id, child_id = _make_tree(engine, repo)
        # Terminal WITHOUT prior injection (never processed a turn).
        with Session(engine) as session:
            row = session.get(Instance, child_id)
            row.status = InstanceStatus.COMPLETED.value
            session.add(row)
            session.commit()

        manager = _build_manager(engine, repo, queue_repo)
        graph = _RecordingGraph()
        svc = _build_service(manager, graph)

        with _SharedContextPartition(root_id):
            result = await svc.enqueue_message(child_id, "database architecture follow-up?", source="api")
            assert result.message_id
            await svc._process_message_with_tracking(
                instance_id=child_id,
                message="database architecture follow-up?",
                message_id=result.message_id,
                is_retry=False,
                message_source="api",
            )

        assert graph.captured_inputs, "revived turn never ran — harness broke"
        graph_input = graph.captured_inputs[0]
        blocks = _shared_context_messages(graph_input)
        assert len(blocks) == 1, (
            f"revived terminal child must receive the block EXACTLY ONCE "
            f"(no drop); got {len(blocks)}: "
            f"{[m.content[:80] for m in blocks]!r}"
        )
        assert f"context_key: {root_id}" in blocks[0].content, (
            "revived child must still resolve the TREE-ROOT partition"
        )

    @pytest.mark.asyncio
    async def test_revived_child_with_prior_injection_gets_no_duplicate(self, engine):
        """No DUPLICATE: a terminal child whose first turn already
        injected (``project_injected=True`` checkpointed the block) gets
        NO second block on the revived turn — the once-per-instance gate
        holds across the terminal→revive boundary."""
        repo = SQLModelInstanceRepository(engine)
        queue_repo = MagicMock()
        root_id, mid_id, child_id = _make_tree(engine, repo)
        with Session(engine) as session:
            row = session.get(Instance, child_id)
            row.status = InstanceStatus.COMPLETED.value
            row.instance_metadata = {
                "project_id": "proj-integ-1",
                "project_injected": True,
            }
            session.add(row)
            session.commit()

        manager = _build_manager(engine, repo, queue_repo)
        graph = _RecordingGraph()
        svc = _build_service(manager, graph)

        with _SharedContextPartition(root_id):
            result = await svc.enqueue_message(child_id, "second turn after revive", source="api")
            await svc._process_message_with_tracking(
                instance_id=child_id,
                message="second turn after revive",
                message_id=result.message_id,
                is_retry=False,
                message_source="api",
            )

        assert graph.captured_inputs, "revived turn never ran — harness broke"
        for gi in graph.captured_inputs:
            blocks = _shared_context_messages(gi)
            assert blocks == [], (
                f"revived child with a checkpointed block must NOT get a "
                f"duplicate; got: {[m.content[:120] for m in blocks]!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Root unchanged — root still resolves its OWN partition
# ─────────────────────────────────────────────────────────────────────────────


class TestRootInstanceUnchanged:
    """Root instance (parent_id=None) keeps the legacy own-partition path."""

    @pytest.mark.asyncio
    async def test_root_instance_resolves_own_partition(self, engine):
        repo = SQLModelInstanceRepository(engine)
        queue_repo = MagicMock()
        root_id = f"root-{uuid.uuid4()}"
        _seed_instance(
            engine,
            repo,
            instance_id=root_id,
            agent_id=OPTED_IN_AGENT_ID,
            parent_id=None,
        )

        manager = _build_manager(engine, repo, queue_repo)
        graph = _RecordingGraph()
        svc = _build_service(manager, graph)

        with _SharedContextPartition(root_id):
            result = await svc.enqueue_message(root_id, "database architecture root query", source="api")
            await svc._process_message_with_tracking(
                instance_id=root_id,
                message="database architecture root query",
                message_id=result.message_id,
                is_retry=False,
                message_source="api",
            )

        assert graph.captured_inputs, "graph turn never ran — harness broke"
        graph_input = graph.captured_inputs[0]
        blocks = _shared_context_messages(graph_input)
        assert len(blocks) == 1, (
            f"root instance must still get exactly one block (unchanged "
            f"behavior); got {len(blocks)}"
        )
        assert f"context_key: {root_id}" in blocks[0].content, (
            "root instance must resolve its OWN id as the partition "
            "(legacy behavior unchanged)"
        )
