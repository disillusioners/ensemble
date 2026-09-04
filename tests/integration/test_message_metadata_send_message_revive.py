"""Residual #4 — send_message-revive coverage for COMPLETED instances on
the aget-only read path (langgraph-checkpoint-perf-v2 port).

Closes the coverage gap flagged in
``tests/integration/test_message_metadata_retry_recovery.py`` (its
"Production-revive coverage note"): the sibling's Part 2
(``TestReadReviveRead``) drives the COMPLETED→RUNNING revive by invoking
a second ``graph.ainvoke`` DIRECTLY because "invoking the production
``send_message`` path would require a full manager fixture". This file
supplies that missing half: the message is sent through the REAL
``InstanceMessagingService.enqueue_message`` (the production entry point
behind ``send_message``; the ``InstanceManager.enqueue_message`` facade
forwards to it per facade-forwarding discipline), so the ACTUAL
revive-on-send branch runs:

    ``_prepare_enqueued_message`` (daemon/services/instance_messaging.py)
    → ``is_terminal_revival`` predicate (COMPLETED / TERMINATED / ERROR /
    FAILED) → ``instance.status = RUNNING`` + ``last_activity_at`` /
    ``version`` bump + ``MESSAGE_RECEIVED`` event, in the SAME
    transaction that writes the ``MessageQueue`` + ``Task`` rows.

…after which the read path must still be correct and alist-free:

    a) pre-revive snapshot BYTE-IDENTICAL to the post-revive snapshot
       (full list equality — every field: message_id, content,
       created_at, instance_id, …; no graph turn runs between the
       reads, so the lists must be EXACTLY equal, not merely
       shared-prefix equal).
    b) ``alist_count == 0`` on BOTH reads (FR-2 invariant preserved
       across the live revive-on-send transition), plus the ARMED
       alist fixture (F5) making any ``saver.alist`` call a hard
       failure independent of the counter.
    c) the instance row left COMPLETED: DB status == RUNNING (the
       transitional state the revive branch establishes) and the
       ``status_changed_to_running`` SSE emit fired exactly once.
    d) the message was accepted: ``AsyncMessageResult.status ==
       "queued"`` with a non-null ``job_id`` (== ``Task.work_id``),
       a ``task`` row (type ``process_message``, status ``pending``,
       ``work_id`` == ``job_id``) and a READY ``message_queue`` row
       carrying the sent content.

BOUNDARY (documented per the brief, item 2g): ``enqueue_message``
performs the revive + durable queue write ONLY — it does NOT execute
the graph. The sent message therefore does NOT enter checkpoint state
on this path; it lives in ``message_queue`` / ``task`` until the
worker pool claims the Task and the resulting graph turn's
``MessageTapSlot`` writes its side-table row. This test asserts the
queued presence (READY message_queue row + pending task row) and
asserts the read-back is UNCHANGED (the sent message absent from
checkpoint state) — pinning the exact production boundary between
"revive + enqueue" and "the graph turn that follows".

Harness honesty contract (mirrors the sibling): every operation uses a
real PG disposable DB + a real ``AsyncPostgresSaver`` + the real
``daemon.persistence.get_instance_messages`` function. The manager
harness is minimal but real-repo-backed — REAL ``MessageMetadataRepository``
on the same disposable PG (rows written via the production
``upsert_batch`` write path), the REAL ``agents/worker`` prompt files
via the real ``load_and_cache_prompt`` / ``PromptCache``, a REAL
``WritePauseGuard`` and REAL ``CancellationService``, and the four
minimal ensemble tables the enqueue prelude writes (``instances``,
``message_queue``, ``task``, ``event`` — created via the production
SQLModel metadata, no migrations). Only the instance ROW is
synthesized. The ONE deliberate stub is ``_live_hub`` (a recording
no-op — the production live hub needs a running SSE bus); it is
load-bearing as an ASSERTION target (the revive emit must fire for the
COMPLETED case and must NOT fire for the RUNNING control).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, TypedDict

import pytest

from tests.helpers.armed_absence import armed_alist_fixture, armed_alist_mock  # noqa: F401  (F5: fixture wiring)
from tests.helpers.checkpoint_prune_pg import (
    create_disposable_db,
    drop_database,
    evict_langgraph_mocks,
    real_pg_checkpointer,
    require_postgres,
    restore_langgraph_mocks,
)


REPO_ROOT = Path(__file__).resolve().parents[2]

# Fixed clock for the synthetic-system prompt's "Current Time" append —
# same freeze as the sibling: without it, two reads seconds apart embed
# different timestamps in the synthetic system message and the
# byte-identical check would flake on the clock, not on the code.
_FIXED_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


# ── F4 manager harness (minimal, real-repo-backed — revive-path shape) ──────


class _StaticInstanceRepo:
    """Real-file-backed minimal stand-in for the instance repository.

    ``get()`` returns a metadata namespace built from the REAL
    ``agents/worker/`` directory on disk, so
    ``_reconstruct_full_system_prompt`` → ``load_and_cache_prompt`` loads
    the actual agent prompt files (no prompt text is mocked). Only the
    DB-backed instance ROW is synthesized — the disposable checkpoint DB
    has no ensemble schema beyond the four tables the enqueue prelude
    writes, and creating the full schema would test migrations, not the
    read path.
    """

    def __init__(self, instance_meta: SimpleNamespace) -> None:
        self._meta = instance_meta

    def get(self, instance_id: str):
        return self._meta

    def get_tree_root_id(self, parent_id: str):
        return parent_id


class _RecordingLiveHub:
    """Recording no-op stand-in for the live SSE hub.

    ``enqueue_message`` calls ``_live_hub.stream_status_change`` when the
    revive branch flips a status to RUNNING. The production hub needs a
    running SSE bus; this recorder exists so the test can ASSERT the emit
    fired (COMPLETED case) or did not fire (RUNNING control) instead of
    silently dropping the signal.
    """

    def __init__(self) -> None:
        self.status_changes: list[tuple[str, str]] = []

    async def stream_status_change(self, instance_id: str, status: str, agent_id=None):
        self.status_changes.append((instance_id, status))


class _ManagerHarness:
    """The manager shape ``enqueue_message`` + ``get_instance_messages`` consume.

    Attributes (verbatim names per ``daemon/services/instance_messaging.py``
    and ``daemon/persistence.py``):

    Read path (identical to the sibling harness):
    * ``message_metadata_repo``   — REAL ``MessageMetadataRepository`` on
      the disposable PG (rows written via the production ``upsert_batch``
      write path — the same call ``MessageTapSlot`` makes).
    * ``_instance_repository``    — :class:`_StaticInstanceRepo`.
    * ``prompt_cache``            — REAL ``PromptCache``.
    * ``shared_meta_kv_repo`` / ``_project_repository`` /
      ``_skill_injection_service`` — deliberately ABSENT/None so the
      Phase-4 context rebuild deterministically emits zero context
      messages.

    Enqueue/revive path (NEW — the send_message surface):
    * ``engine``                  — REAL sync engine on the disposable PG
      (``postgresql+psycopg://``, the daemon's sync convention); carries
      the four production SQLModel tables the enqueue prelude writes.
    * ``write_guard``             — REAL ``WritePauseGuard`` (fresh =
      writes allowed).
    * ``_shutting_down``          — False (shutdown gate must pass).
    * ``_deferred_question_pause``— empty set (marker guard must not fire;
      the Task row MUST be created).
    * ``_live_hub``               — :class:`_RecordingLiveHub` (assertion
      target for the revive emit; the one deliberate stub).
    * ``_worker_pool``            — None (notify_work is skipped; no
      worker pool under test).
    * ``_job_queue_service``      — deliberately ABSENT: the
      ``stamp_message_id`` mirror-stamp is best-effort (try/except
      Exception in ``enqueue_message``), and the ``_job_repository``
      property resolves the AttributeError to None per its contract.
    """

    def __init__(
        self,
        message_metadata_repo,
        instance_repo,
        prompt_cache,
        engine,
    ) -> None:
        self.message_metadata_repo = message_metadata_repo
        self._instance_repository = instance_repo
        self.prompt_cache = prompt_cache
        self.engine = engine
        self.write_guard = _write_pause_guard()
        self._shutting_down = False
        self._deferred_question_pause: set[str] = set()
        self._live_hub = _RecordingLiveHub()
        self._worker_pool = None


def _write_pause_guard():
    """Build a REAL WritePauseGuard (imports lazily, sibling-style)."""
    from daemon.write_pause_guard import WritePauseGuard

    return WritePauseGuard()


def _build_revive_harness(dsn: str, instance_id: str, status: str) -> tuple:
    """Build the manager + messaging harness on the disposable PG.

    Creates the ``message_metadata`` side table (sibling layout) PLUS the
    four minimal ensemble tables the enqueue prelude writes
    (``instances``, ``message_queue``, ``task``, ``event``) via the
    production SQLModel metadata, inserts the instance ROW with
    ``status``, and returns ``(messaging_service, manager, metadata_repo,
    engine)``.
    """
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel

    from daemon.loader import PromptCache
    from daemon.repositories.event.models import Event
    from daemon.repositories.instance.models import Instance
    from daemon.repositories.message_metadata.models import MessageMetadata
    from daemon.repositories.message_metadata.repository import (
        MessageMetadataRepository,
    )
    from daemon.repositories.message_queue.models import MessageQueue
    from daemon.repositories.task.models import Task

    engine = create_engine(
        dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    )
    # The side table (read path) + the four enqueue-prelude tables (revive
    # path), all from the production SQLModel metadata — no migrations.
    SQLModel.metadata.create_all(
        engine,
        tables=[
            MessageMetadata.__table__,
            Instance.__table__,
            MessageQueue.__table__,
            Task.__table__,
            Event.__table__,
        ],
        checkfirst=True,
    )
    metadata_repo = MessageMetadataRepository(engine)

    instance_meta = SimpleNamespace(
        agent_id="worker",
        agent_dir=str(REPO_ROOT / "agents" / "worker"),
        agent_tag=None,
        parent_id=None,
        project_id=None,
        instance_metadata={"mcp_tool_names": None, "source_type": None},
        created_at=_FIXED_NOW,
    )

    from sqlmodel import Session

    from daemon.repositories.instance.models import InstanceStatus

    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="worker",
                agent_dir=str(REPO_ROOT / "agents" / "worker"),
                status=status,
                instance_metadata={"mcp_tool_names": None, "source_type": None},
                created_at=_FIXED_NOW.isoformat(),
                updated_at=_FIXED_NOW.isoformat(),
            )
        )
        session.commit()

    manager = _ManagerHarness(
        message_metadata_repo=metadata_repo,
        instance_repo=_StaticInstanceRepo(instance_meta),
        prompt_cache=PromptCache(),
        engine=engine,
    )

    # REAL messaging service + REAL cancellation service over the harness.
    from daemon.services.cancellation import CancellationService
    from daemon.services.instance_messaging import InstanceMessagingService

    messaging = InstanceMessagingService(
        manager, CancellationService(manager)
    )
    return messaging, manager, metadata_repo, engine


# ── fixtures (sibling convention) ────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _real_langgraph():
    """Evict root-conftest langgraph mocks (binding-gate idiom)."""
    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


@pytest.fixture
def _probe_pg():
    require_postgres()


# ── helpers ──────────────────────────────────────────────────────────────────


async def _populate_completed_thread(saver, thread_id: str, n_messages: int) -> str:
    """Populate a thread with ``n_messages`` via graph.ainvoke + return instance_id (==thread_id).

    Uses the sibling's binding-gate pattern verbatim: a single
    graph.ainvoke with all N messages in one batch + the ``add_messages``
    reducer. The thread has 1 real checkpoint (post-batch) + the
    reducer-applied writes. The ``add_messages`` global injection (see the
    sibling for the ``get_type_hints`` rationale) is load-bearing for the
    reducer semantics.
    """
    from langchain_core.messages import HumanMessage

    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages

    import sys
    _this_module = sys.modules[__name__]
    if not hasattr(_this_module, "add_messages"):
        setattr(_this_module, "add_messages", add_messages)

    class _State(TypedDict):
        messages: Annotated[list, add_messages]

    def _echo(state: _State) -> _State:
        return {"messages": []}

    graph = StateGraph(_State)
    graph.add_node("echo", _echo)
    graph.add_edge(START, "echo")
    graph.add_edge("echo", END)
    compiled = graph.compile(checkpointer=saver)

    messages = [
        HumanMessage(content=f"msg-{thread_id}-{i}", id=f"m-{thread_id}-{i:06d}")
        for i in range(n_messages)
    ]
    await compiled.ainvoke(
        {"messages": messages},
        {"configurable": {"thread_id": thread_id}},
    )
    return thread_id  # instance_id == thread_id (project invariant)


def _read_instance_status(engine, instance_id: str) -> str:
    """Read the instance status back through a FRESH session (post-commit truth)."""
    from sqlmodel import Session

    from daemon.repositories.instance.models import Instance

    with Session(engine) as session:
        row = session.get(Instance, instance_id)
        assert row is not None, f"instance row {instance_id} vanished"
        return row.status


# ── Residual #4 — send_message → COMPLETED→RUNNING revive → read ────────────


class TestSendMessageRevive:
    """The live send_message-revive path preserves the aget-only read flip.

    Steps (per the brief):
      1. Populate a thread with N messages on real PG; the thread is
         COMPLETED (the graph turn finished).
      2. Wire the harness: REAL ``MessageMetadataRepository`` + real
         ``agents/worker`` prompt files + real ``PromptCache`` + the four
         enqueue-prelude tables; the instance ROW is created COMPLETED
         (the terminal state under test — the sibling's thread-level
         completion plus a DB row, since the revive branch reads the DB).
      3. Write the pre-revive side-table rows via the production
         ``upsert_batch`` write path (the tap's write).
      4. Pre-revive snapshot: ``get_instance_messages`` + alist_count == 0.
      5. THE REVIVE: ``messaging_service.enqueue_message`` — the REAL
         production messaging path behind ``send_message`` — runs the
         ``is_terminal_revival`` branch against the DB row.
      6. Post-revive snapshot: same ``get_instance_messages`` call.
      7. Assert (a)-(d) from the module docstring, plus the queued-presence
         boundary (2g).
    """

    @pytest.mark.asyncio
    async def test_send_message_revives_completed_instance_and_read_stays_aget_only(
        self, _probe_pg, armed_alist_fixture, monkeypatch
    ):
        """COMPLETED instance + enqueue_message → revived, read unchanged, alist-free."""
        from daemon.checkpoint_metrics import checkpoint_list_total, reset_for_tests
        from daemon.persistence import get_instance_messages
        from sqlmodel import Session, select

        from daemon.repositories.message_queue.models import (
            MessageQueue,
            MessageStatus,
            MessageType,
        )
        from daemon.repositories.task.models import Task, TaskStatus, TaskType

        # Clock freeze (sibling): deterministic synthetic-system message.
        import daemon.services.instance_lifecycle as lifecycle_mod

        real_append_current_time = lifecycle_mod.append_current_time
        monkeypatch.setattr(
            lifecycle_mod,
            "append_current_time",
            lambda prompt, now=None: real_append_current_time(
                prompt, now=_FIXED_NOW
            ),
        )

        name, dsn = await create_disposable_db()
        try:
            async with real_pg_checkpointer(name, dsn) as (saver, _pool, _adapter):
                thread_id = f"thr-send-revive-{uuid.uuid4().hex[:8]}"
                await _populate_completed_thread(saver, thread_id, n_messages=5)

                # Harness with the instance ROW forced COMPLETED (the
                # terminal state the revive branch consumes).
                messaging, manager, metadata_repo, engine = _build_revive_harness(
                    dsn, thread_id, status="completed"
                )

                # Pre-revive side-table rows — the production tap write
                # (MessageTapSlot → repo.upsert_batch), fixed timestamps.
                pre_ids = [f"m-{thread_id}-{i:06d}" for i in range(5)]
                pre_rows = [
                    (mid, f"2026-09-04T11:00:0{i}+00:00", i)
                    for i, mid in enumerate(pre_ids)
                ]
                metadata_repo.upsert_batch(thread_id, pre_rows)

                # PRE-revive snapshot (full production read shape).
                reset_for_tests()
                msgs_before = await get_instance_messages(
                    saver, thread_id, manager=manager
                )
                alist_count_before = checkpoint_list_total.get()

                assert alist_count_before == 0, (
                    f"alist_count BEFORE revive = {alist_count_before}, "
                    f"must be 0 (FR-2 invariant violation on initial read)"
                )
                assert len(msgs_before) == 6, (
                    f"pre-revive msgs = {len(msgs_before)}, expected 6 "
                    f"(1 synthetic-system + 5 persisted)"
                )
                assert (
                    msgs_before[0]["message_id"]
                    == f"synthetic-system-{thread_id}"
                ), "manager harness did NOT wire the synthetic injection"
                # Side-table join proof: timestamps come from the REAL repo
                # rows, not the state.ts fallback.
                for i, mid in enumerate(pre_ids):
                    assert (
                        msgs_before[i + 1]["created_at"]
                        == f"2026-09-04T11:00:0{i}+00:00"
                    ), f"side-table join broken pre-revive for {mid}"

                # Sanity: the row is COMPLETED right before the send.
                assert _read_instance_status(engine, thread_id) == "completed"

                # ── THE REVIVE: the REAL production messaging path ──
                # (send_message → enqueue_message; the
                # is_terminal_revival branch inside
                # _prepare_enqueued_message flips COMPLETED→RUNNING).
                sent_content = f"revive-me-{thread_id}"
                result = await messaging.enqueue_message(
                    thread_id, sent_content, source="api"
                )

                # (c) instance left COMPLETED → RUNNING (the transitional
                # state the revive branch establishes), version bumped,
                # and the revive emit fired exactly once.
                assert result.status == "queued", (
                    f"AsyncMessageResult.status = {result.status!r}, "
                    f"expected 'queued' (message accepted)"
                )
                assert result.message_id, "result.message_id must be set"
                assert result.job_id, (
                    "result.job_id must be non-null (Task.work_id linkage)"
                )
                assert _read_instance_status(engine, thread_id) == "running", (
                    "COMPLETED instance was NOT revived to RUNNING by "
                    "enqueue_message (is_terminal_revival branch did not fire)"
                )
                assert manager._live_hub.status_changes == [
                    (thread_id, "running")
                ], (
                    f"revive emit = {manager._live_hub.status_changes!r}, "
                    f"expected exactly one (thread_id, 'running')"
                )

                # (d) durable acceptance: Task + MessageQueue rows.
                with Session(engine) as session:
                    task = session.exec(
                        select(Task).where(Task.work_id == result.job_id)
                    ).one_or_none()
                    assert task is not None, (
                        "enqueue_message did not write the Task row "
                        "(deferred-pause marker guard fired unexpectedly?)"
                    )
                    assert task.task_type == TaskType.PROCESS_MESSAGE.value
                    assert task.status == TaskStatus.PENDING.value
                    assert task.instance_id == thread_id
                    assert task.message_id == result.message_id
                    mq = session.exec(
                        select(MessageQueue).where(
                            MessageQueue.message_id == result.message_id
                        )
                    ).one_or_none()
                    assert mq is not None, "MessageQueue row missing"
                    assert mq.status == MessageStatus.READY.value
                    assert mq.content == sent_content
                    assert mq.type == MessageType.HUMAN.value
                    assert mq.instance_id == thread_id

                # POST-revive snapshot (the sent message did NOT enter
                # checkpoint state — no graph turn ran on this path).
                reset_for_tests()
                msgs_after = await get_instance_messages(
                    saver, thread_id, manager=manager
                )
                alist_count_after = checkpoint_list_total.get()

                # (b) alist_count == 0 on BOTH reads (FR-2 invariant
                # preserved across the LIVE revive-on-send transition) —
                # plus the ARMED gate makes any alist call a hard failure.
                assert alist_count_after == 0, (
                    f"alist_count AFTER revive = {alist_count_after}, "
                    f"must be 0 (read path regressed to the alist walk "
                    f"across the COMPLETED→RUNNING revive)"
                )
                armed_alist_fixture.assert_not_called()

                # (a) post-revive read BYTE-IDENTICAL to pre-revive: full
                # list equality (contents + created_at + every field) — no
                # graph turn ran, so there is no sanctioned +1 tail here.
                assert msgs_after == msgs_before, (
                    "post-revive read differs from pre-revive read "
                    "(created_at drift, state.ts fallback leak, or the "
                    "sent message leaked into checkpoint state without a "
                    "graph turn)"
                )
                # 2g boundary: the sent message is NOT in checkpoint state.
                read_ids = [m["message_id"] for m in msgs_after]
                assert result.message_id not in read_ids, (
                    "the enqueued message appeared in checkpoint state "
                    "without a graph turn — boundary violated"
                )

                # (c) synthetic-system id identical on BOTH reads.
                synthetic_id = f"synthetic-system-{thread_id}"
                assert msgs_before[0]["message_id"] == synthetic_id
                assert msgs_after[0]["message_id"] == synthetic_id
        finally:
            await drop_database(name)

    @pytest.mark.asyncio
    async def test_send_message_on_running_instance_does_not_fire_revive_branch(
        self, _probe_pg, armed_alist_fixture, monkeypatch
    ):
        """Control: RUNNING instance + enqueue_message → NO revive emit.

        Pins the discriminator the main test relies on: the revive branch
        fires because the row was COMPLETED (``is_terminal_revival``), not
        merely because a message was enqueued. For an already-RUNNING
        instance the status stays RUNNING with NO ``status_changed_to_running``
        SSE emit (``enqueue_message`` only bumps ``last_activity_at`` /
        ``version`` there), while the message is still accepted (queued).
        """
        from sqlmodel import Session, select

        from daemon.repositories.message_queue.models import MessageQueue
        from daemon.repositories.task.models import Task

        name, dsn = await create_disposable_db()
        try:
            async with real_pg_checkpointer(name, dsn) as (_saver, _pool, _adapter):
                thread_id = f"thr-running-ctl-{uuid.uuid4().hex[:8]}"
                messaging, manager, _metadata_repo, engine = _build_revive_harness(
                    dsn, thread_id, status="running"
                )

                result = await messaging.enqueue_message(
                    thread_id, "control-message", source="api"
                )

                assert result.status == "queued"
                # Status was ALREADY running: no transition, no revive emit.
                assert _read_instance_status(engine, thread_id) == "running"
                assert manager._live_hub.status_changes == [], (
                    f"revive emit fired for an already-RUNNING instance: "
                    f"{manager._live_hub.status_changes!r} (the emit is the "
                    f"status_changed_to_running signal and must fire only on "
                    f"a real transition)"
                )
                # Still durably accepted.
                with Session(engine) as session:
                    assert (
                        session.exec(
                            select(Task).where(Task.work_id == result.job_id)
                        ).one_or_none()
                        is not None
                    )
                    mq = session.exec(
                        select(MessageQueue).where(
                            MessageQueue.message_id == result.message_id
                        )
                    ).one_or_none()
                    assert mq is not None and mq.content == "control-message"
        finally:
            await drop_database(name)


# ── Deviations / Notes ───────────────────────────────────────────────────────


DEVIATIONS = """
* The harness extends (not replaces) the sibling's: the read-path shape is
  identical; the enqueue/revive path adds ``engine`` / ``write_guard`` /
  ``_shutting_down`` / ``_deferred_question_pause`` / ``_live_hub`` /
  ``_worker_pool`` and four production SQLModel tables. ``_live_hub`` is
  the only stub and doubles as an assertion target.
* The sent message is asserted QUEUED (message_queue READY + task PENDING),
  not read back from checkpoint state: ``enqueue_message`` revives and
  queues but does not execute the graph — the MessageTapSlot write happens
  at the start of the worker-claimed graph turn, which is out of scope
  here (the sibling's TestReadReviveRead covers the post-turn read).
"""

