"""T5.11 — D-3 retry-recovery test (FR-13 / AC-13.1 / AC-13.3).

Two parts:

* **Part 1 (AC-13.1, AC-13.2 — D-3 never-under-record):** start a turn,
  pause mid-tap between ``tap_node_return`` await and node return,
  resume via ``is_retry=True`` (COMPLETED→RUNNING revive with
  checkpoint reuse per ``daemon/services/instance_messaging.py``
  reuse-revive semantics), assert NO ``message_metadata`` row is
  MISSING (the never-under-record invariant).

* **Part 2 (AC-13.3 — read→revive→read):** for a COMPLETED instance,
  pre-revive snapshot via the ``get_instance_messages`` read path,
  dispatch revive-on-send (send_message to the COMPLETED instance
  triggers the COMPLETED→RUNNING transition with checkpoint reuse
  per the cardinal #2 scoping discipline), post-revive snapshot,
  assert:
    a) snapshots BYTE-IDENTICAL pre/post revive for the shared prefix
    b) new tail message has non-NULL created_at
    c) ``synthetic-system-{iid}`` id is identical both reads
    d) ``alist_count == 0`` on BOTH reads (FR-2 invariant preserved
       across the revive — the second read does NOT regress to the
       alist walk)

Per Risk 6: Part 1 uses a deterministic pause injection. If the
deterministic pause injection proves unreproducible in this
environment, the gap is documented + a follow-up is proposed. Part 2
is load-bearing — it MUST pass.

Harness honesty contract: every operation uses a real PG
disposable DB + a real ``AsyncPostgresSaver`` + the real
``daemon.persistence.get_instance_messages`` function (the same
production code the router calls). The manager harness is minimal but
real-repo-backed: a REAL ``MessageMetadataRepository`` on the same
disposable PG (rows written via the production ``upsert_batch`` write
path) + the REAL ``agents/worker`` prompt files loaded through the
real ``load_and_cache_prompt`` / ``PromptCache`` — only the instance
ROW is synthesized (the disposable DB carries no ensemble schema).
The alist live-path gate is enforced TWICE: the T5.6 metric capture
AND the T5.4 armed-absence fixture (class-patched AsyncMock whose
invocation raises AssertionError).
"""
from __future__ import annotations

import asyncio
import json
import time
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
# ``append_current_time`` documents ``now`` as "Provide a fixed value for
# deterministic tests". Without the freeze, two reads seconds apart embed
# different timestamps in the synthetic system message and the AC-13.3 (a)
# byte-identical check would flake on the clock, not on the code.
_FIXED_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


# ── F4 manager harness (minimal, real-repo-backed) ──────────────────────────


class _StaticInstanceRepo:
    """Real-file-backed minimal stand-in for the instance repository.

    ``get()`` returns a metadata namespace built from the REAL
    ``agents/worker/`` directory on disk, so
    ``_reconstruct_full_system_prompt`` → ``load_and_cache_prompt`` loads
    the actual agent prompt files (no prompt text is mocked). Only the
    DB-backed instance ROW is synthesized — the disposable checkpoint DB
    has no ensemble schema, and creating one would test migrations, not
    the read path.
    """

    def __init__(self, instance_meta: SimpleNamespace) -> None:
        self._meta = instance_meta

    def get(self, instance_id: str):
        return self._meta

    def get_tree_root_id(self, parent_id: str):
        return parent_id


class _ManagerHarness:
    """The manager shape ``get_instance_messages`` actually consumes.

    Attributes (verbatim names per ``daemon/persistence.py``):
    * ``message_metadata_repo``   — REAL ``MessageMetadataRepository`` on
      the disposable PG (side table created via ``MessageMetadata``
      metadata create; rows written via the production ``upsert_batch``
      write path — the same call ``MessageTapSlot`` makes).
    * ``_instance_repository``    — :class:`_StaticInstanceRepo`.
    * ``prompt_cache``            — REAL ``PromptCache``.
    * ``shared_meta_kv_repo`` / ``_project_repository`` /
      ``_skill_injection_service`` — deliberately ABSENT/None so the
      Phase-4 context rebuild deterministically emits zero context
      messages (project=None, no KV repo, no skill service →
      ``_run_skill_search`` returns ``(None, [])``).
    """

    def __init__(
        self,
        message_metadata_repo,
        instance_repo,
        prompt_cache,
    ) -> None:
        self.message_metadata_repo = message_metadata_repo
        self._instance_repository = instance_repo
        self.prompt_cache = prompt_cache


def _build_manager_harness(dsn: str) -> tuple:
    """Build the manager harness on the disposable PG + real agent files.

    Creates the ``message_metadata`` side table in the SAME disposable
    database the checkpoints live in (the production layout is one PG
    instance serving both), and returns ``(manager, metadata_repo,
    engine)``.
    """
    from sqlalchemy import create_engine

    from daemon.loader import PromptCache
    from daemon.repositories.message_metadata.models import MessageMetadata
    from daemon.repositories.message_metadata.repository import (
        MessageMetadataRepository,
    )

    engine = create_engine(
        dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    )
    MessageMetadata.__table__.create(engine, checkfirst=True)
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
    manager = _ManagerHarness(
        message_metadata_repo=metadata_repo,
        instance_repo=_StaticInstanceRepo(instance_meta),
        prompt_cache=PromptCache(),
    )
    return manager, metadata_repo, engine


# ── fixtures ─────────────────────────────────────────────────────────────────


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


# ── helpers ─────────────────────────────────────────────────────────────────


def _vid(n):
    return f"{n:032x}.{n:016x}"


async def _populate_completed_thread(saver, thread_id: str, n_messages: int) -> str:
    """Populate a thread with ``n_messages`` via graph.ainvoke + return instance_id (==thread_id).

    Uses the binding-gate pattern: a single graph.ainvoke with all N
    messages in one batch + the ``add_messages`` reducer. The thread
    has 1 real checkpoint (post-batch) + the reducer-applied writes.
    """
    from langchain_core.messages import HumanMessage

    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages

    # Inject ``add_messages`` into this test module's globals so that
    # ``get_type_hints`` (called by ``StateGraph._add_schema``) can
    # resolve ``Annotated[list, add_messages]``. The annotation is
    # evaluated lazily against the SCHEMA CLASS's ``__module__.__globals__``
    # — which is this test module — but the function-local import
    # does NOT add ``add_messages`` to the module's globals. Without
    # this injection, ``StateGraph._add_schema`` falls back to
    # ``LastValue`` semantics (overwrite-on-write) instead of the
    # ``add_messages`` reducer — which BREAKS the AC-13.3 assertion
    # that the post-revive snapshot has +1 message (the second
    # ainvoke would overwrite rather than append).
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


# ── Part 1 (AC-13.1 / AC-13.2) — DOCUMENTED DEVIATION per Risk 6 ───


class TestNeverUnderRecordInvariant:
    """AC-13.1 / AC-13.2 — DOCUMENTED DEVIATION per Risk 6.

    Per the brief: "If the deterministic pause injection proves
    unreproducible, do NOT fake it — document the gap + propose
    follow-up (accepted per Risk 6) and still deliver Part 2 which
    must pass."

    The deterministic pause injection (between tap_node_return
    await and node return) requires a debug hook in MessageTapSlot
    that yields a deterministic seam. Adding such a hook to
    production code for testability is out of Phase 5 scope. The
    hook is PROPOSED as a v3 follow-up (see the DEVIATIONS docstring
    below).

    The Part 1 contribution to Phase 5 closure is the COMPILE-TIME
    evidence that the harness CAN exercise the read-after-revive
    path. The LOAD-BEARING acceptance is Part 2 below (AC-13.3
    read→revive→read), which DOES pass.

    The "easier direction" of the never-under-record invariant
    (rows present before revive are still present after) is
    implicitly verified by the binding-gate integration test
    (tests/integration/test_message_metadata_lifecycle_wiring.py)
    which exercises the full MessageTapSlot write path across many
    revive scenarios on real PG.
    """


# ── Part 2 (AC-13.3) — read→revive→read ─────────────────────────────────────


class TestReadReviveRead:
    """AC-13.3: COMPLETED→RUNNING revive preserves the read flip.

    Steps:
      1. Populate a thread with N messages; the thread is COMPLETED.
      2. Wire the manager harness (F4): REAL ``MessageMetadataRepository``
         on the disposable PG + real ``agents/worker`` prompt files +
         real ``PromptCache``, so ``get_instance_messages`` runs the
         FULL production read shape (side-table enrichment + synthetic
         system message) — not the bare-saver degradation.
      3. Write the pre-revive side-table rows via the production
         ``upsert_batch`` write path (the tap's write).
      4. Pre-revive snapshot: ``get_instance_messages`` returns the
         messages list + the alist_count metric (= 0, FR-2 invariant).
      5. Simulate the revive: a second ``graph.ainvoke`` against the
         SAME thread (in production this would be triggered by
         ``send_message`` to a COMPLETED instance — the reuse-revive
         path transitions COMPLETED→RUNNING with checkpoint reuse);
         then write the revive-turn side-table row (the tap fires at
         message entry, i.e. BEFORE the post-revive read).
      6. Post-revive snapshot: same ``get_instance_messages`` call.
      7. Assert the FOUR plan sub-assertions (T5.11):
         a) pre-revive snapshot BYTE-IDENTICAL to the shared prefix of
            the post-revive snapshot (full dict equality — every field,
            not just content; read path shares zero code with the
            revive path; the synthetic-system id is deterministic per
            instance, and the Current-Time append is clock-frozen).
         b) the new tail message has non-NULL ``created_at`` — and it
            equals the side-table row (proving the REAL repo join, not
            the state.ts fallback).
         c) ``synthetic-system-{iid}`` id identical on BOTH reads.
         d) ``alist_count == 0`` on BOTH reads (FR-2 invariant preserved
            across the revive) — plus the ARMED alist fixture (F5)
            makes any alist call a hard failure regardless of counters.

    The armed-absence fixture (T5.4 / F5) is wired via test parameter —
    ``AsyncPostgresSaver.alist`` is class-patched with an AsyncMock
    whose side_effect raises AssertionError, so ANY alist invocation on
    this live path fails the test LOUDLY, independent of the metric
    counter in (d).
    """

    @pytest.mark.asyncio
    async def test_read_revive_read_preserves_shared_prefix(
        self, _probe_pg, armed_alist_fixture, monkeypatch
    ):
        """Read → revive → read: the FOUR AC-13.3 sub-assertions (a)-(d)."""
        from daemon.persistence import get_instance_messages
        from daemon.checkpoint_metrics import (
            checkpoint_list_total,
            reset_for_tests,
        )

        # Freeze the Current-Time append inside the synthetic-system
        # reconstruction (the deterministic-tests seam documented on
        # ``append_current_time`` itself). Patching the module attribute
        # is effective because ``_apply_post_cache_appends`` resolves the
        # name from its module globals at call time.
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
                thread_id = f"thr-revive-{uuid.uuid4().hex[:8]}"
                await _populate_completed_thread(saver, thread_id, n_messages=5)

                # F4 manager harness: real side-table repo + real prompt
                # files (agents/worker) + real PromptCache.
                manager, metadata_repo, _engine = _build_manager_harness(dsn)

                # Pre-revive side-table rows — the production tap write
                # (MessageTapSlot → repo.upsert_batch), fixed timestamps.
                pre_ids = [f"m-{thread_id}-{i:06d}" for i in range(5)]
                pre_rows = [
                    (mid, f"2026-09-04T11:00:0{i}+00:00", i)
                    for i, mid in enumerate(pre_ids)
                ]
                metadata_repo.upsert_batch(
                    thread_id,
                    [(mid, ts, seq) for (mid, ts, seq) in pre_rows],
                )

                # PRE-revive snapshot (full production read shape).
                reset_for_tests()
                msgs_before = await get_instance_messages(
                    saver, thread_id, manager=manager
                )
                alist_count_before = checkpoint_list_total.get()
                ids_before = [m["message_id"] for m in msgs_before]

                # (d) pre-revive: alist_count == 0 (FR-2 invariant).
                assert alist_count_before == 0, (
                    f"alist_count BEFORE revive = {alist_count_before}, "
                    f"must be 0 (FR-2 invariant violation on initial read)"
                )
                # F4 wiring proof: the synthetic system message IS present
                # (this is what the bare-saver path could not assert).
                assert len(msgs_before) == 6, (
                    f"pre-revive msgs = {len(msgs_before)}, expected 6 "
                    f"(1 synthetic-system + 5 persisted)"
                )
                assert ids_before[0] == f"synthetic-system-{thread_id}", (
                    f"first message_id = {ids_before[0]!r}, expected "
                    f"synthetic-system-{thread_id} (manager harness did "
                    f"NOT wire the synthetic injection)"
                )
                # Side-table join proof: persisted timestamps come from
                # the REAL repo rows, not the state.ts fallback.
                for i, mid in enumerate(pre_ids):
                    msg = msgs_before[i + 1]
                    assert msg["message_id"] == mid
                    assert msg["created_at"] == f"2026-09-04T11:00:0{i}+00:00", (
                        f"created_at for {mid} = {msg['created_at']!r}, "
                        f"expected the upserted side-table row "
                        f"(state.ts fallback leaked through pre-revive)"
                    )

                # REVIVE: a second graph.ainvoke against the same
                # thread simulates the COMPLETED→RUNNING transition
                # with checkpoint reuse (per
                # ``daemon/services/instance_messaging.py`` reuse-revive
                # semantics, cardinal #2 scoping). The langgraph
                # saver transitions the thread from "completed" to
                # "running" via the standard pregel-loop flow.
                from langchain_core.messages import HumanMessage
                from langgraph.graph import END, START, StateGraph
                from langgraph.graph.message import add_messages

                class _State(TypedDict):
                    messages: Annotated[list, add_messages]

                def _echo(state: _State) -> _State:
                    return {"messages": []}

                graph = StateGraph(_State)
                graph.add_node("echo", _echo)
                graph.add_edge(START, "echo")
                graph.add_edge("echo", END)
                compiled = graph.compile(checkpointer=saver)

                # The new message after revive — this is the "tail"
                # that should appear in the post-revive snapshot.
                tail_id = f"m-{thread_id}-post"
                new_message = HumanMessage(
                    content=f"msg-{thread_id}-after-revive",
                    id=tail_id,
                )
                await compiled.ainvoke(
                    {"messages": [new_message]},
                    {"configurable": {"thread_id": thread_id}},
                )

                # The revive turn's tap write — fires at message entry
                # (BEFORE the post-revive read), per the production order.
                revive_ts = "2026-09-04T11:05:00+00:00"
                metadata_repo.upsert_batch(thread_id, [(tail_id, revive_ts, 5)])

                # POST-revive snapshot.
                msgs_after = await get_instance_messages(
                    saver, thread_id, manager=manager
                )
                alist_count_after = checkpoint_list_total.get()
                ids_after = [m["message_id"] for m in msgs_after]

                # AC-13.3 (d) — alist_count == 0 on BOTH reads.
                assert alist_count_after == 0, (
                    f"alist_count AFTER revive = {alist_count_after}, "
                    f"must be 0 (FR-2 invariant violation post-revive — "
                    f"the read flip did NOT survive the COMPLETED→RUNNING "
                    f"transition)"
                )
                # F5 — the ARMED gate: zero alist calls on the live path,
                # enforced by the fixture itself (AssertionError on any
                # invocation), independent of the metric counter.
                armed_alist_fixture.assert_not_called()

                # AC-13.3 (a) — the pre-revive snapshot is BYTE-IDENTICAL
                # to the shared prefix of the post-revive snapshot: full
                # dict equality, every field (message_id, content,
                # created_at, instance_id, ...). The read path shares
                # zero code with the revive path; the only sanctioned
                # difference is the +1 tail message.
                assert len(msgs_after) == len(msgs_before) + 1, (
                    f"expected post-revive to have +1 message "
                    f"(pre={len(msgs_before)} post={len(msgs_after)})"
                )
                assert msgs_after[: len(msgs_before)] == msgs_before, (
                    "shared prefix is NOT byte-identical across the "
                    "revive (AC-13.3a violated)"
                )
                # Shared-prefix membership, explicit (redundant with the
                # list equality above but keeps the historical contract
                # readable in failure output).
                for pre_id in ids_before:
                    assert pre_id in ids_after, (
                        f"message_id {pre_id} disappeared post-revive "
                        f"(shared prefix not preserved)"
                    )

                # AC-13.3 (b) — the NEW tail message has non-NULL
                # created_at, stamped by the REAL side-table row.
                tail = msgs_after[-1]
                assert tail["message_id"] == tail_id, (
                    f"tail message_id = {tail['message_id']!r}, expected "
                    f"{tail_id!r}"
                )
                assert tail["created_at"] is not None, (
                    "AC-13.3b violated: new tail message has NULL "
                    "created_at"
                )
                assert tail["created_at"] == revive_ts, (
                    f"tail created_at = {tail['created_at']!r}, expected "
                    f"the side-table row {revive_ts!r} (state.ts fallback "
                    f"leaked through — side-table join broken post-revive)"
                )

                # AC-13.3 (c) — the synthetic-system-{iid} id is
                # identical on BOTH reads (deterministic per instance).
                synthetic_id = f"synthetic-system-{thread_id}"
                assert ids_before[0] == synthetic_id
                assert ids_after[0] == synthetic_id, (
                    f"synthetic-system id drift: pre={ids_before[0]!r} "
                    f"post={ids_after[0]!r}"
                )
                assert msgs_after[0] == msgs_before[0], (
                    "synthetic-system message content drift across the "
                    "revive (clock-freeze or prompt reconstruction is "
                    "non-deterministic)"
                )
        finally:
            await drop_database(name)


# ── Deviations / Follow-ups ────────────────────────────────────────────────


DEVIATIONS = """
Part 1 (AC-13.1 / AC-13.2 — never-under-record via pause injection):
DOCUMENTED GAP per Risk 6. Adding a deterministic pause between
tap_node_return await and node return requires a
MessageTapSlot debug hook (``_PAUSE_AFTER_TAP_FOR_TESTING`` env var)
that yields a deterministic seam — this would touch production code
for testability and is out of Phase 5 scope. The easier direction
(rows that exist before the revive still exist after) IS verified
by ``test_completed_instance_retains_all_metadata_rows``. The
harder direction (rows added DURING the pause are preserved across
the resume) is proposed as a v3 follow-up:

  * Add ``MessageTapSlot._debug_pause_after_tap`` (env-gated,
    default off) that yields ``asyncio.sleep(0)`` (or a configurable
    delay) after the tap writes but before the call site returns.
  * Wire a pytest fixture that sets the env var + pauses mid-turn
    via ``pause_instance_cascade`` semantics.
  * Assert that after revive + read, the rows from the paused turn
    are all present (no MISSING row) and possibly duplicate rows are
    tolerated (over-record tolerance, per PR2 review §3).
"""
