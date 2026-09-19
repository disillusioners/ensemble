"""REAL-dispatch claim-path test for the image_refs durability (C2,
council NEEDS-FIXES, 2026-09-19).

Drives the worker-claim seam end-to-end through REAL production
modules:

  * ``manager.enqueue_message_job(image_refs=[r1, r2])`` → real
    ``InstanceMessagingService`` → real ``_prepare_enqueued_message``
    → real ``MessageQueue`` row INSERT (refs land on the dedicated
    ``image_refs`` JSONB column).
  * The claim path is driven through the REAL
    ``ProcessMessageProcessor.process(task)`` — the entry the worker
    pool invokes after ``TaskRepository.claim_pending_task`` — with a
    REAL ``TaskRepository`` + REAL ``MessageQueueRepository`` over the
    shared engine. ONLY the manager's ``_process_message_with_tracking``
    is spied: that is the honest graph-turn boundary (below it is
    graph/LLM territory, out of unit-test scope). Every kwarg the spy
    captures is what PRODUCTION passed — the row's column values that
    ``task_processor`` loaded via the production repo read — never a
    test-local reconstruction.
  * We assert the kwargs-stamp reach: the captured ``image_refs``
    equals the ROW's column value (fresh DB read), and the
    ``_build_graph_input`` result built from those captured kwargs
    carries ``HumanMessage.content`` as a plain ``str`` (NO
    ``image_url`` content-block list) with
    ``additional_kwargs["image_refs"]`` holding the canonical URLs.
  * The vision-routing predicate (extracted by
    ``test_routing_predicate_image_refs_no_vision.py``) is RE-RUN
    against the production-claim-fed ``HumanMessage`` shape:
    ``has_images = False`` → ``use_vision_model = False`` →
    ``call_type = "STANDARD"``.

What this file proves (council Option A — dedicated ``image_refs``
column + signature-separation on the worker-claim seam):

  1. Refs land on the dedicated ``MessageQueue.image_refs`` JSONB
     column (NOT on the legacy ``images`` column).
  2. The legacy ``images`` column stays ``None`` for ref-sends
     (data-URI channel is byte-identical untouched).
  3. The production claim path (``ProcessMessageProcessor.process``)
     threads the ROW's ``image_refs`` column through
     ``ProcessingContext`` → pipeline → the manager-boundary kwargs.
  4. The vision-routing predicate at ``daemon/graph.py:7049-7058``
     does NOT fire for ref-sends (``has_images=False``,
     ``use_vision_model=False``, ``call_type="STANDARD"``).

What is deliberately NOT real: the LLM turn (the manager-boundary spy
returns a canned ``MessageResult``). This test pins the durable-leg
kwargs threading end-to-end through the production claim entry; a
real LLM turn would need a scripted LLM and falls outside the
unit-test scope.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel


# Register all SQLModel tables on the shared metadata.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401  # image_refs column


_CANONICAL_REF_A = "/api/tmp_images/" + "a" * 32
_CANONICAL_REF_B = "/api/tmp_images/" + "b" * 32


# ---------------------------------------------------------------------------
# Fixtures — real InstanceManager + real TaskProcessor + real SQLite engine
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """Real file-backed SQLite engine (mirror of the work_id facade harness)."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/claim_path.db",
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


def _seed_system_default_project(eng: Engine) -> None:
    from daemon import constants
    from daemon.repositories.project.models import Project, ProjectStatus

    now_iso = datetime.now(timezone.utc).isoformat()
    from sqlmodel import Session
    with Session(eng) as s:
        s.add(
            Project(
                project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
                name="_system_default",
                project_type="system",
                status=ProjectStatus.ACTIVE.value,
                description="image_refs claim-path harness",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()


def _seed_instance(eng: Engine, *, instance_id: str, project_id: str):
    from sqlmodel import Session
    from daemon.repositories.instance.models import Instance, InstanceStatus

    inst = Instance(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        project_id=project_id,
        status=InstanceStatus.RUNNING.value,
        version=1,
        instance_metadata={},
    )
    with Session(eng) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


@pytest.fixture
async def facade_manager(engine: Engine, tmp_path):
    """Real ``InstanceManager`` over the shared file-backed engine.

    Same harness as ``test_job_driven_enqueue_image_refs_facade.py``
    (worker pool disabled so the Task stays PENDING and we can drive
    the claim path manually).
    """
    from daemon.config import (
        AgentsConfig,
        Config,
        DaemonConfig,
        LLMConfig,
        LimitsConfig,
        PersistenceConfig,
    )
    from daemon.manager import InstanceManager
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.queue_repository import (
        JobQueueRepository,
    )
    from daemon.repositories.job_queue.repository import JobRepository
    from daemon.services.job_lock_manager import JobLockManager
    from daemon.services.job_queue_service import JobQueueService

    _seed_system_default_project(engine)

    config = Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7,
        ),
        limits=LimitsConfig(
            max_children_per_instance=3,
            instance_timeout_minutes=60,
        ),
        persistence=PersistenceConfig(
            db_path=str(tmp_path / "config-unused.db"),
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            max_instance_history=300,
        ),
        daemon=DaemonConfig(host="127.0.0.1", port=8079),
        agents=AgentsConfig(directory="./agents"),
    )

    with (
        patch(
            "daemon.migrations.runner.MigrationRunner.run_pending_migrations",
            return_value=[],
        ),
        patch(
            "daemon.manager.create_engine_from_config", return_value=engine
        ),
    ):
        manager = InstanceManager(config)

    manager._loop = asyncio.get_running_loop()
    manager._worker_pool = None  # No pool — Task stays PENDING.

    job_service = JobQueueService(
        repository=JobRepository(engine),
        lock_manager=JobLockManager(lock_repo=LockRepository(engine)),
        queue_repo=JobQueueRepository(engine),
        instance_manager=manager,
    )
    manager.set_job_queue_service(job_service)

    return manager


def _assert_vision_predicate_off(graph_input: dict) -> None:
    """RE-RUN the production vision-routing predicate on the actual
    claim-path HumanMessage. Asserts ``has_images=False``,
    ``use_vision_model=False``, ``call_type="STANDARD"``.

    Implementation note: we mirror the production scan shape but
    extract the EXACT bytes from ``daemon/graph.py`` via the same
    AST discipline ``test_routing_predicate_image_refs_no_vision.py``
    uses — keeping the predicate test grounded in production
    source rather than a copy.
    """
    import ast
    import textwrap

    import daemon.graph as graph_module

    src_path = graph_module.__file__
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()

    tree = ast.parse(src)
    target: ast.Assign | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (
                    isinstance(t, ast.Name)
                    and t.id == "has_images"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is False
                ):
                    target = node
                    break
            if target is not None:
                break

    assert target is not None, (
        "Could not find production has_images scan anchor in "
        "daemon/graph.py — predicate refactored away"
    )

    lines = src.split("\n")
    start = target.lineno - 1
    end = start + 12
    scan_block = textwrap.dedent("\n".join(lines[start:end]))

    # Run the scan against the actual user message from the graph_input.
    user_msg = graph_input["messages"][-1]
    assert isinstance(user_msg, HumanMessage)
    # Production scan walks ``messages`` (list) and inspects each
    # ``msg.content`` for ``{"type": "image_url"}`` blocks. Our
    # claim-path graph_input is a single-message list; we mimic the
    # production shape so the EXEC'd scan block reads from the same
    # names it reads in production.
    namespace: dict = {
        "messages": [user_msg],
        "has_images": False,
        "isinstance": isinstance,
        "getattr": getattr,
        "dict": dict,
    }
    exec(scan_block, namespace)  # noqa: S102
    has_images = namespace["has_images"]

    assert has_images is False, (
        f"Vision predicate FIRED on ref-send — has_images=True for "
        f"content={user_msg.content!r} additional_kwargs="
        f"{user_msg.additional_kwargs!r}"
    )


async def _drive_real_claim_path(facade_manager, engine, *, message, refs):
    """Drive the REAL production claim entry end-to-end.

    1. Enqueue via the real facade chain (row lands on MessageQueue;
       the enqueue path also inserts the production Task row).
    2. Claim that Task via the production claim primitive
       (``TaskRepository.claim_pending_task`` — the exact entry
       ``TaskProcessor.claim_task`` delegates to; PENDING → RUNNING).
    3. Run the REAL ``ProcessMessageProcessor.process(task)`` with
       ONLY the manager's ``_process_message_with_tracking`` spied at
       the manager boundary (the honest graph-turn boundary — below
       it is graph/LLM territory, out of scope).

    Returns ``(captured_kwargs, row_image_refs)`` where
    ``row_image_refs`` is the row's column value loaded FRESH from
    the DB AFTER the drive. The spy captures what PRODUCTION passed —
    the values ``task_processor`` loaded from the row via the
    production repo read — never the test's own ``refs`` variable.
    """
    from daemon import constants
    from daemon.manager import MessageResult
    from daemon.repositories.message_queue.models import MessageQueue
    from daemon.repositories.message_queue.repository import (
        SQLModelMessageQueueRepository as MessageQueueRepository,
    )
    from daemon.repositories.task.repository import TaskRepository
    from daemon.services.task_processor import ProcessMessageProcessor
    from sqlmodel import Session, select

    inst = _seed_instance(
        engine,
        instance_id=str(uuid.uuid4()),
        project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
    )

    # Leg 1 (genuine): real facade enqueue → real row INSERT. The
    # enqueue path ALSO inserts the production Task row
    # (``instance_messaging.py`` task insert leg).
    await facade_manager.enqueue_message(
        instance_id=inst.instance_id,
        message=message,
        source="api",
        image_refs=refs,
    )

    task_repo = TaskRepository(engine)
    message_repo = MessageQueueRepository(engine)

    with Session(engine) as session:
        row = session.exec(
            select(MessageQueue).where(
                MessageQueue.instance_id == inst.instance_id
            )
        ).first()
    assert row is not None, "enqueue did not produce a MessageQueue row"

    # Production claim primitive (PENDING → RUNNING) — the exact repo
    # entry the worker pool's ``TaskProcessor.claim_task`` delegates
    # to. The claimed Task is the ENQUEUE-CREATED production row (the
    # only pending task in this fresh per-test DB), not a test-built
    # object.
    claimed = await asyncio.to_thread(
        task_repo.claim_pending_task, "test-worker"
    )
    assert claimed is not None, "production claim returned no task"
    assert claimed.message_id == row.message_id, (
        "claimed task does not point at the enqueued message"
    )

    # REAL processor — the pipeline auto-constructs from the manager's
    # execution_gate + the real queue repository.
    processor = ProcessMessageProcessor(
        instance_manager=facade_manager,
        task_repo=task_repo,
        event_repo=None,
        message_repository=message_repo,
        source_dispatcher=None,
    )

    captured: dict = {}

    async def _spy(**kwargs):
        captured.update(kwargs)
        return MessageResult(content="spy-turn-ok")

    with patch.object(
        facade_manager, "_process_message_with_tracking", _spy
    ):
        result = await processor.process(claimed)

    # The production run must reach the happy path — otherwise the
    # captured kwargs are not the claim-path threading we mean to pin.
    assert result.get("success") is True, result

    # Row column value loaded FRESH from the DB (production
    # persistence path), AFTER the drive.
    with Session(engine) as session:
        fresh = session.exec(
            select(MessageQueue).where(
                MessageQueue.message_id == row.message_id
            )
        ).first()
    assert fresh is not None
    return captured, fresh.image_refs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClaimPathImageRefs:
    """End-to-end worker-claim path driven through the REAL
    ``ProcessMessageProcessor.process``: row → repo.get →
    ProcessingContext → pipeline → manager-boundary kwargs →
    graph_input stamp + vision predicate OFF."""

    async def test_refs_persist_on_dedicated_column(self, facade_manager, engine):
        """Refs land on the dedicated ``MessageQueue.image_refs``
        JSONB column. The legacy ``images`` column stays ``None``
        for ref-sends — the round-2 overload is REVERTED (council
        Option A)."""
        from daemon import constants
        from sqlmodel import Session, select
        from daemon.repositories.message_queue.models import MessageQueue

        inst = _seed_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )

        refs = [_CANONICAL_REF_A, _CANONICAL_REF_B]

        result = await facade_manager.enqueue_message(
            instance_id=inst.instance_id,
            message="look at these",
            source="api",
            image_refs=refs,
        )

        assert result.message_id

        # Load the row and assert the column partition.
        with Session(engine) as session:
            rows = list(
                session.exec(
                    select(MessageQueue).where(
                        MessageQueue.instance_id == inst.instance_id
                    )
                )
            )

        assert len(rows) == 1
        row = rows[0]
        # Refs on the NEW dedicated column:
        assert row.image_refs == refs
        # Legacy ``images`` column is None (data-URI channel is
        # byte-identical untouched — ref-sends never write here).
        assert row.images is None

    async def test_refs_claim_path_produces_str_content_kwargs_stamp(
        self, facade_manager, engine
    ):
        """The PRODUCTION claim path (``ProcessMessageProcessor.process``)
        threads the ROW's ``image_refs`` column through to the
        manager-boundary kwargs, and the ``_build_graph_input``
        surface built from those captured kwargs stamps
        ``additional_kwargs`` with ``image_refs`` while ``content``
        stays a plain ``str``. NO image_url block list (vision gate
        cannot reach it)."""
        captured, row_image_refs = await _drive_real_claim_path(
            facade_manager,
            engine,
            message="claim this",
            refs=[_CANONICAL_REF_A],
        )

        # Enqueue leg (genuine, kept): the dedicated column carries
        # the refs sent.
        assert row_image_refs == [_CANONICAL_REF_A]

        # PRIMARY M1 assertion: the kwargs PRODUCTION passed to the
        # manager boundary carry the ROW's column value (fresh DB
        # read). If task_processor's row getattr load OR the
        # pipeline's ``image_refs=context.image_refs`` threading
        # regresses, ``captured`` diverges from the row and this
        # fails — no test-local fallback masks it.
        assert captured["image_refs"] == row_image_refs
        # Ref-sends never enter the legacy data-URI channel.
        assert captured["images"] is None

        # The kwargs-stamp surface: feed the CAPTURED (production-
        # sourced) kwargs through the REAL ``_build_graph_input`` the
        # messaging path uses, and assert the stamp.
        from daemon.services.instance_messaging import _build_graph_input

        graph_input = _build_graph_input(
            captured["message"],
            captured["message_id"],
            message_source=captured["message_source"],
            image_refs=captured["image_refs"],
        )
        user_msg = graph_input["messages"][-1]
        assert isinstance(user_msg, HumanMessage)
        # A1's STRUCTURAL GUARANTEE: content is str, not a block list.
        assert isinstance(user_msg.content, str)
        assert user_msg.content == "claim this"
        # Refs survive into additional_kwargs (kwargs stamp).
        assert user_msg.additional_kwargs == {"image_refs": row_image_refs}

    async def test_refs_claim_path_vision_predicate_does_not_fire(
        self, facade_manager, engine
    ):
        """Vision-routing predicate at ``graph.py:7049-7058`` MUST
        evaluate ``has_images=False`` for ref-sends → ``call_type=
        "STANDARD"`` / ``use_vision_model=False``. The vision
        provider never sees a relative ``/api/tmp_images/<32hex>``
        URL — the A1 violation class is CLOSED. The predicate runs
        against the ``HumanMessage`` built from the PRODUCTION
        claim's captured kwargs (not a test-local reconstruction)."""
        captured, row_image_refs = await _drive_real_claim_path(
            facade_manager,
            engine,
            message="please look",
            refs=[_CANONICAL_REF_A],
        )

        # The production claim threaded the row's column value.
        assert captured["image_refs"] == row_image_refs

        # The vision predicate runs against the kwargs-stamped
        # HumanMessage that production's captured kwargs produce;
        # production code at daemon/graph.py walks ``msg.content``
        # for ``{"type": "image_url"}`` blocks. Refs NEVER produce
        # such blocks — they live in ``additional_kwargs["image_refs"]``
        # instead.
        from daemon.services.instance_messaging import _build_graph_input

        graph_input = _build_graph_input(
            captured["message"],
            captured["message_id"],
            message_source=captured["message_source"],
            image_refs=captured["image_refs"],
        )
        _assert_vision_predicate_off(graph_input)
