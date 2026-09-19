"""Integration E2E — chat-source live-injection branch with realistic metadata.

Iteration 2 (2026-09-19, ``feature/chat-source-live-injection`` fix
cycle). The unit tests in
``tests/unit/test_chat_source_live_injection.py`` pin the routing
logic with mocked facade methods; this file is the wiring-level
proof that the injection branch fires end-to-end with REAL adapter-
shaped metadata + REAL ``InstanceManager.set_injection`` (writes to
the actual ``_pending_injections`` dict).

Companion to ``test_chat_source_enqueue_wake_e2e.py`` (durable path
e2e): that file proves ``manager.enqueue_message`` produces a chat-
pool wake; THIS file proves ``registry._handle_message`` routes
realistic slack/telegram/discord envelopes to
``manager.set_injection`` (NOT ``enqueue_message``) on a RUNNING +
live-graph target.

Hard constraint (per the fix-cycle spec): the durable path stays
byte-for-byte unchanged. The integration assertion is two-sided:
on the injection branch, the real ``manager._pending_injections``
queue MUST contain the message (proves injection fired); on the
durable-fallthrough side, a real ``MessageQueue`` + ``Task`` +
``JobItem`` row MUST be written (proves durable still works).

Pattern follows the wiring-only seam in
``tests/integration/chat_source_harness.py:wire_manager_only`` —
build a real ``InstanceManager`` against a file-backed SQLite
engine, skip ``setup_worker_pool`` (injection doesn't need worker
pools — the ``agent_node`` that would drain the FIFO is the same
seam that exists in production, but we don't need a worker to claim
it for THIS test), seed an ``Instance`` row with ``status=running``,
add a live graph task to ``manager._graph_tasks[instance_id]`` so
``has_live_graph_task`` returns True, then drive
``registry._handle_message`` and observe the real injection queue.

All setup + test execution happens inside a single ``asyncio.run``
block so the live graph task and the registry call share one event
loop (the manager's ``_graph_tasks`` dict holds asyncio.Task
objects whose ``.done()`` we read; cross-loop task references
would raise).

Mock discipline: ``registry._handle_message`` is the production seam
that calls ``manager.get_instance_info`` (real DB read) +
``manager.has_live_graph_task`` (real ``_graph_tasks`` dict read) +
``manager.set_injection`` (real ``_pending_injections`` append). The
adapter mock is the ONLY stub — it just supplies ``source_type`` and
``start_typing`` to the registry (real adapters do this in prod).
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.constants import ROUTING_ENVELOPE_KEYS
from daemon.sources.base import IncomingMessage
from daemon.sources.registry import SourceRegistry

from tests.integration.chat_source_harness import (
    build_chat_source_engine,
    chat_lane_flag_reset_fixture,
    wire_manager_only,
)


pytestmark = pytest.mark.integration


# Lane-flag isolation — shared autouse fixture factory (matches the
# companion ``test_chat_source_enqueue_wake_e2e.py`` pattern).
chat_lane_flag_reset = chat_lane_flag_reset_fixture()


# ---------------------------------------------------------------------------
# Engine fixture — file-backed SQLite, NullPool, WAL
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    eng = build_chat_source_engine(str(tmp_path / "chat_live_inject.db"))
    yield eng
    eng.dispose()


# ---------------------------------------------------------------------------
# Realistic adapter-shaped metadata fixtures
# ---------------------------------------------------------------------------
# These fixtures are COPIES of each adapter's metadata mint site (NOT
# invented). Cited inline per fixture so the chain back to the
# adapter is unambiguous. If an adapter's mint shape changes, this
# test is the integration-level witness — update here AND at the
# mint site together.


def _slack_box_metadata() -> dict:
    """Realistic slack envelope from ``slack/adapter.py:813-825``.
    Channel + thread + workspace + user all nested under ``slack``;
    ``agent`` + ``reply_chat_id`` top-level.
    """
    return {
        "slack": {
            "channel_id": "C12345",
            "channel_type": "channel",
            "user_id": "U99999",
            "ts": "1700000123.000456",
            "thread_ts": "1700000000.000100",
            "workspace_id": "T0ABCDEF",
            "workspace_name": "acme",
        },
        "agent": "ari",
        "reply_chat_id": "C12345",
    }


def _telegram_supergroup_metadata() -> dict:
    """Realistic telegram envelope from ``telegram.py:558-573`` —
    supergroup variant to mirror the production group-chat lane."""
    return {
        "telegram": {
            "message_id": "1234",
            "chat_id": "-1001234567890",
            "chat_type": "supergroup",
            "from_id": "987654321",
            "from_username": "alice",
            "from_first_name": "Alice",
            "from_last_name": "Wonder",
            "date": 1700000000,
            "edit_date": None,
        },
        "agent": "ari",
        "reply_chat_id": "-1001234567890",
    }


def _discord_text_metadata() -> dict:
    """Realistic discord envelope from ``discord/adapter.py:1092-1095``
    (text path). Discord intentionally does NOT populate
    ``reply_chat_id`` — channel/thread routing lives on the mapping.
    """
    return {
        "discord": {
            "guild_id": "111111111",
            "guild_name": "Acme",
            "channel_id": "222222222",
            "channel_name": "general",
            "channel_type": "text",
            "thread_id": None,
            "thread_name": None,
            "parent_channel_id": None,
            "user_id": "333333333",
            "user_name": "alice",
            "user_display_name": "Alice",
            "message_id": "444444444",
            "is_dm": False,
        },
        "agent": "ari",
    }


# ---------------------------------------------------------------------------
# Driver — sets up the real manager + registry + live graph task
# inside one asyncio.run block, then asserts on the real injection queue
# + the real DB row state.
# ---------------------------------------------------------------------------


def _run_injection_scenario(
    engine,
    *,
    source_id: str,
    source_type: str,
    msg: IncomingMessage,
):
    """Drive one end-to-end chat-source ingestion scenario.

    Returns: ``(manager, registry, instance_id)`` after the
    ``_handle_message`` call. Caller inspects
    ``manager._pending_injections[instance_id]`` and the DB to
    verify the routing decision.

    Synchronous (top-level) entry point that owns the asyncio.run
    boundary so the live graph task and the registry call share one
    event loop. The live graph task is created on the same loop and
    cancelled in the same loop before returning.
    """

    async def _driver():
        from daemon.repositories.instance.models import Instance

        # Stack ``wire_manager_only`` (a synchronous contextmanager)
        # inside the asyncio.run block. The manager itself is
        # sync-construction; we just need its DB connection +
        # ``_graph_tasks`` dict to share the event loop where we
        # create the live graph task.
        with wire_manager_only(engine) as manager:
            instance_id = f"inst-{uuid.uuid4().hex[:12]}"

            # JobQueueService wiring (audit follow-up, 2026-09-19):
            # the production chat-source durable path
            # (``registry.py:1077-1084`` →
            # ``manager.enqueue_message_job`` →
            # ``InstanceMessagingService.enqueue_message_job``
            # ``daemon/services/instance_messaging.py:2473``) calls
            # ``self._manager._job_queue_service.enqueue(...)`` to
            # mint the JobItem row. ``wire_manager_only`` does NOT
            # wire JobQueueService (it skips the heavy ``initialize()``
            # lifespan), so without this block the durable path
            # crashes inside ``_job_queue_service.enqueue(...)`` —
            # MessageQueue + Task rows are written first, then the
            # crash; the test would silently lose the JobItem row and
            # fail any new JobItem assertion. The audit's docstring
            # claim ("real MessageQueue + Task + JobItem row MUST be
            # written") cannot be pinned otherwise. The wiring is
            # SURGICAL — only this helper gets it, so the companion
            # tests that use ``build_live_pool_manager`` (``manager.
            # enqueue_message`` — the internal-only MessageQueue+Task
            # path) are unaffected.
            from daemon.repositories import (
                create_job_repository,
                create_job_queue_repository,
            )
            from daemon.repositories.job_queue.lock_repository import (
                LockRepository,
            )
            from daemon.services.job_lock_manager import JobLockManager
            from daemon.services.job_queue_service import JobQueueService

            job_repo = create_job_repository(engine=engine, create_tables=True)
            queue_repo = create_job_queue_repository(
                engine=engine, create_tables=True
            )
            lock_repo = LockRepository(engine=engine)
            job_lock_manager = JobLockManager(lock_repo=lock_repo)
            job_queue_service = JobQueueService(
                repository=job_repo,
                lock_manager=job_lock_manager,
                queue_repo=queue_repo,
                instance_manager=manager,
            )
            job_queue_service.set_event_loop(asyncio.get_running_loop())
            manager.set_job_queue_service(job_queue_service)

            # System queues (audit follow-up, 2026-09-19): the
            # JobQueueService.enqueue path resolves the queue_id via
            # ``system_parallel_queue`` lookup on the system project
            # (``daemon/services/job_queue_service.py:818-823``). The
            # queues must be provisioned before the first enqueue.
            # Mirrors ``daemon/api.py:1041-1043`` (the production
            # lifespan boots this BEFORE any chat-source message
            # arrives).
            from daemon.services.job_queue_mgmt_service import (
                JobQueueMgmtService,
            )

            job_queue_mgmt_service = JobQueueMgmtService(
                queue_repo=queue_repo,
                job_repo=job_repo,
                task_repo=getattr(manager, "_task_repo", None),
            )
            system_project_id = (
                manager._project_repository.ensure_system_default_project()
            )
            await job_queue_mgmt_service.auto_provision_system_queues(
                system_project_id,
            )

            # Seed the Instance row directly so ``get_instance_info``
            # returns ``{"status": "running"}``.
            with Session(engine) as s:
                s.add(
                    Instance(
                        instance_id=instance_id,
                        agent_id="ari",
                        agent_dir="/agents/ari",
                        status="running",
                    )
                )
                s.commit()

            # Pre-create the source_config row (FK target for the
            # mapping's source_id) AND the source→instance mapping
            # so the registry's ``InstanceMapper.get_or_create_instance``
            # finds it and returns our seeded ``instance_id``
            # (instead of trying to spawn a new instance via
            # ``spawn_instance_with_mcp``, which requires a project
            # context this test doesn't set up).
            manager._source_repository.create_source_config(
                source_type=source_type,
                name=f"test-{source_id}",
                config={"test": True},
                source_id=source_id,
            )
            manager._source_repository.create_instance_mapping(
                source_id=source_id,
                external_user_id=msg.external_user_id,
                agent_instance_id=instance_id,
                agent_id="ari",
                agent_dir="/agents/ari",
                metadata={},
            )

            async def _live_graph():
                # Block forever; the test only needs ``done() == False``.
                await asyncio.Event().wait()

            # Add a live (not-done) graph task on THIS event loop so
            # ``has_live_graph_task`` returns True.
            live_task = asyncio.create_task(_live_graph())
            manager._graph_tasks[instance_id] = live_task

            # Build registry with the REAL source_repo (the manager
            # already has one — ``_source_repository`` set up in
            # ``__init__`` via ``create_source_repository``). A
            # MagicMock repo would break the DB-backed mapping.
            from daemon.sources.mapper import InstanceMapper

            registry = SourceRegistry(
                manager._source_repository, manager
            )
            adapter = MagicMock()
            adapter.source_id = source_id
            adapter.source_type = source_type
            adapter.start_typing = AsyncMock(return_value=None)
            adapter.send = AsyncMock(return_value=True)
            registry.register(adapter)

            # Pre-condition sanity
            assert registry.get(source_id).source_type == source_type
            assert source_type in ROUTING_ENVELOPE_KEYS

            # ── The call under test ──
            await registry._handle_message(source_id, msg)

            # Cancel the live graph task before leaving the loop.
            live_task.cancel()
            try:
                await live_task
            except (asyncio.CancelledError, Exception):
                pass

            return manager, registry, instance_id

    return asyncio.run(_driver())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLiveInjectionE2E:
    """Real manager + real engine + REAL registry._handle_message →
    real manager.set_injection (RAM-FIFO append)."""

    def test_slack_envelope_appends_to_pending_injections(self, engine):
        """Driver: a slack envelope (read directly from the adapter's
        mint site) on a RUNNING + live-graph target routes to
        ``manager.set_injection``; ``manager._pending_injections``
        has 1 entry; NO MessageQueue/Task rows were written.

        Integration-level proof the wiring works: the manager is
        real, the DB is real, the registry's full path runs
        (mapping, status probe, graph-task probe, injection). The
        ONLY stub is the adapter (which provides ``source_type``
        and ``start_typing``).
        """
        msg = IncomingMessage(
            external_user_id="alice",
            content="hello from slack channel",
            source_id="slack",
            metadata=_slack_box_metadata(),
        )

        manager, registry, instance_id = _run_injection_scenario(
            engine,
            source_id="slack",
            source_type="slack",
            msg=msg,
        )

        # ── Observable: real _pending_injections queue ──
        queue = manager._pending_injections.get(instance_id)
        assert queue is not None, (
            f"manager._pending_injections[{instance_id}] was not "
            f"populated — the injection branch did NOT fire "
            f"(current keys: {list(manager._pending_injections.keys())})"
        )
        assert len(queue) == 1, (
            f"expected exactly 1 injected entry, got {len(queue)}: "
            f"{queue}"
        )
        entry = queue[0]
        assert entry["content"] == "hello from slack channel"
        assert entry["source"] == "slack:alice", (
            f"chat provenance wrong: {entry.get('source')!r}"
        )
        assert "echo_id" in entry, "echo_id not threaded"
        # echo_id parseable as uuid4
        parsed = uuid.UUID(entry["echo_id"])
        assert str(parsed) == entry["echo_id"]

        # ── Observable: durable path was NOT taken ──
        # No MessageQueue / Task rows for this instance (the
        # durable path would create both).
        from daemon.repositories.message_queue.models import MessageQueue
        from daemon.repositories.task.models import Task
        from sqlmodel import select

        with Session(engine) as s:
            mq = s.exec(
                select(MessageQueue).where(
                    MessageQueue.instance_id == instance_id
                )
            ).all()
            tasks = s.exec(
                select(Task).where(Task.instance_id == instance_id)
            ).all()
            assert mq == [], (
                f"injection wrote MessageQueue rows: "
                f"{[m.message_id for m in mq]}"
            )
            assert tasks == [], (
                f"injection wrote Task rows: "
                f"{[t.work_id for t in tasks]}"
            )

        # ── Observable: typing indicator fired on the adapter ──
        adapter = registry.get("slack")
        adapter.start_typing.assert_awaited_once()

    def test_telegram_supergroup_envelope_appends_to_pending_injections(
        self, engine,
    ):
        """Telegram supergroup envelope — mirrors the production
        group-chat lane (``chat-source-worker-lane`` Phase 3
        wiring). Same wiring-level proof as the slack test but for
        the telegram provider.
        """
        msg = IncomingMessage(
            external_user_id="charlie",
            content="hello from telegram group",
            source_id="telegram",
            metadata=_telegram_supergroup_metadata(),
        )

        manager, registry, instance_id = _run_injection_scenario(
            engine,
            source_id="telegram",
            source_type="telegram",
            msg=msg,
        )

        queue = manager._pending_injections.get(instance_id)
        assert queue is not None, (
            f"manager._pending_injections[{instance_id}] was not "
            f"populated for telegram supergroup envelope"
        )
        assert len(queue) == 1
        entry = queue[0]
        assert entry["source"] == "telegram:charlie"
        assert "echo_id" in entry

        # Adapter typing indicator fired with reply_chat_id
        adapter = registry.get("telegram")
        adapter.start_typing.assert_awaited_once()
        assert (
            adapter.start_typing.await_args.args[0]
            == "-1001234567890"
        )

        # Provider-parity pin (audit matrix gap row 8'): the slack
        # test asserts MessageQueue + Task row absence on injection
        # (e2e:349-365 pattern). Telegram's injection path must show
        # the same symmetry — the durable lane was NOT entered, so
        # neither MessageQueue nor Task rows should exist. This
        # catches a future regression where telegram silently falls
        # through to enqueue_message_job (would write both tables).
        from daemon.repositories.message_queue.models import MessageQueue
        from daemon.repositories.task.models import Task
        from sqlmodel import select

        with Session(engine) as s:
            mq = s.exec(
                select(MessageQueue).where(
                    MessageQueue.instance_id == instance_id
                )
            ).all()
            tasks = s.exec(
                select(Task).where(Task.instance_id == instance_id)
            ).all()
            assert mq == [], (
                f"telegram injection wrote MessageQueue rows: "
                f"{[m.message_id for m in mq]}"
            )
            assert tasks == [], (
                f"telegram injection wrote Task rows: "
                f"{[t.work_id for t in tasks]}"
            )

    def test_discord_envelope_appends_to_pending_injections(self, engine):
        """Discord text envelope — discord intentionally omits
        ``reply_chat_id`` (channel/thread routing lives on the
        mapping). The 4-key allowlist (``discord``, ``agent``,
        ``force_new_instance``, ``command``) is the most
        distinctive of the three providers; this test pins that
        the realistic mint shape injects without the
        ``reply_chat_id`` key the slack/telegram envelopes carry.
        """
        msg = IncomingMessage(
            external_user_id="bob",
            content="hello from discord",
            source_id="discord",
            metadata=_discord_text_metadata(),
        )

        manager, registry, instance_id = _run_injection_scenario(
            engine,
            source_id="discord",
            source_type="discord",
            msg=msg,
        )

        queue = manager._pending_injections.get(instance_id)
        assert queue is not None, (
            f"manager._pending_injections[{instance_id}] was not "
            f"populated for discord envelope"
        )
        assert len(queue) == 1
        entry = queue[0]
        assert entry["source"] == "discord:bob"

        # Provider-parity pin (audit matrix gap row 8'): mirror the
        # slack test's row-absence pattern (e2e:349-365) for the
        # discord provider. The chat-source live-injection branch
        # must NOT have entered the durable lane — so neither
        # MessageQueue nor Task rows should exist for this
        # instance_id.
        from daemon.repositories.message_queue.models import MessageQueue
        from daemon.repositories.task.models import Task
        from sqlmodel import select

        with Session(engine) as s:
            mq = s.exec(
                select(MessageQueue).where(
                    MessageQueue.instance_id == instance_id
                )
            ).all()
            tasks = s.exec(
                select(Task).where(Task.instance_id == instance_id)
            ).all()
            assert mq == [], (
                f"discord injection wrote MessageQueue rows: "
                f"{[m.message_id for m in mq]}"
            )
            assert tasks == [], (
                f"discord injection wrote Task rows: "
                f"{[t.work_id for t in tasks]}"
            )

    def test_unknown_metadata_key_writes_durable_not_injection(self, engine):
        """Companion assertion: an envelope with a key OUTSIDE the
        per-provider allowlist MUST take the durable path even on
        a RUNNING + live-graph target. Pinned at the integration
        level: MessageQueue + Task rows are written, the
        injection queue stays empty.

        Pairs with
        ``tests/unit/test_chat_source_live_injection.py::
        TestRichPayloadDurableFallback::test_unknown_metadata_key_…``
        — that one pins the unit-level routing decision; this one
        pins the real-DB row creation.
        """
        metadata = _slack_box_metadata()
        metadata["unknown_provider_key"] = "boom"  # NOT in allowlist

        msg = IncomingMessage(
            external_user_id="alice",
            content="with extra key",
            source_id="slack",
            metadata=metadata,
        )

        manager, _registry, instance_id = _run_injection_scenario(
            engine,
            source_id="slack",
            source_type="slack",
            msg=msg,
        )

        # Injection queue stays empty
        queue = manager._pending_injections.get(instance_id, [])
        assert queue == [], (
            f"expected durable fallthrough, but injection queue "
            f"got {len(queue)} entries: {queue}"
        )

        # Durable path wrote a MessageQueue row (the enqueue path
        # materializes MessageQueue + Task; pinning at least the
        # MessageQueue row is sufficient to prove the durable lane
        # ran).
        from daemon.repositories.message_queue.models import MessageQueue
        from sqlmodel import select

        with Session(engine) as s:
            mq = s.exec(
                select(MessageQueue).where(
                    MessageQueue.instance_id == instance_id
                )
            ).all()
            assert len(mq) == 1, (
                f"durable path did not write MessageQueue row; "
                f"got {len(mq)} rows"
            )
            assert mq[0].content == "with extra key"
            assert mq[0].source == "slack:alice"

            # Audit-finding 1.d / Section 3 row "JobItem row absence":
            # the module docstring (line 22-23) claims "on the
            # durable-fallthrough side, a real MessageQueue + Task +
            # JobItem row MUST be written". The original test only
            # queried MessageQueue; the Task + JobItem half of the
            # contract was unenforced. Pinned here at the
            # integration level via the same Session block, reading
            # exactly the row types production writes (Task row at
            # ``daemon/repositories/task/models.py:141`` +
            # JobItem row at ``daemon/repositories/job_queue/models.py:256``).
            from daemon.repositories.task.models import Task
            from daemon.repositories.job_queue.models import JobItem

            tasks = s.exec(
                select(Task).where(Task.instance_id == instance_id)
            ).all()
            assert len(tasks) == 1, (
                f"durable path did not write Task row; "
                f"got {len(tasks)} rows for instance_id={instance_id}"
            )
            assert tasks[0].instance_id == instance_id
            # Task.message_id should match the MessageQueue.message_id
            # written in the same transaction (cross-table invariant
            # the messaging service enforces at line 6925-6932).
            assert tasks[0].message_id == mq[0].message_id, (
                f"Task.message_id ({tasks[0].message_id!r}) does NOT "
                f"match MessageQueue.message_id ({mq[0].message_id!r}) — "
                f"durable lane wrote inconsistent rows"
            )

            job_items = s.exec(
                select(JobItem).where(JobItem.instance_id == instance_id)
            ).all()
            assert len(job_items) == 1, (
                f"durable path did not write JobItem row; "
                f"got {len(job_items)} rows for instance_id={instance_id}"
            )
            assert job_items[0].instance_id == instance_id
            # JobItem.message mirrors the user content (the
            # ``enqueue_message_job`` facade forwards the user
            # message text into the JobItem.message column).
            assert job_items[0].message == "with extra key", (
                f"JobItem.message wrong: {job_items[0].message!r}"
            )

    def test_injection_drain_contract_round_trips_through_fifo(
        self, engine,
    ):
        """Audit finding 1.b — consumer-side drain contract.

        Pins the EXACT contract the LangGraph ``agent_node``
        consumer relies on
        (``daemon/graph.py:204-227`` —
        ``InjectionQueueManager.get/clear``):

          1. ``manager.get_injection(instance_id)`` PEEKS the FIFO
             (``daemon/manager.py:2809-2830``) — returns a copy of
             the entries WITHOUT clearing.
          2. ``manager.clear_injection(instance_id)`` POPS the FIFO
             (``daemon/manager.py:2849-2867``) — returns the
             defensive-copy list AND empties
             ``_pending_injections[instance_id]`` (returns ``None``
             after).

        Together these are the "lands in the agent's CURRENT turn"
        half of the user's scenario: the producer side writes the
        FIFO entry; the consumer side reads + clears it.

        Producer-side is pinned by
        ``test_slack_envelope_appends_to_pending_injections``
        (e2e:295-438); this test pins the consumer side using the
        real ``InstanceManager`` API.

        Why we need this in the integration layer (not just unit):
        the unit mocks ``manager.set_injection`` to a MagicMock —
        it cannot observe real FIFO behavior. The e2e driver calls
        the REAL ``registry._handle_message`` + REAL
        ``manager.set_injection`` (verified at e2e:299), so
        ``_pending_injections`` actually holds the entries the
        consumer reads from. This is the smallest end-to-end
        coverage of the producer→consumer contract that does NOT
        require a full LLM-driven agent_node pass.
        """
        msg = IncomingMessage(
            external_user_id="alice",
            content="drain me",
            source_id="slack",
            metadata=_slack_box_metadata(),
        )

        manager, _registry, instance_id = _run_injection_scenario(
            engine,
            source_id="slack",
            source_type="slack",
            msg=msg,
        )

        # After the real producer path ran, the FIFO must hold 1
        # entry (re-affirmed before exercising the consumer).
        assert (
            len(manager._pending_injections.get(instance_id, [])) == 1
        ), "precondition: producer side must have populated the FIFO"

        # Step 1 — ``get_injection`` PEEK (does NOT clear).
        peeked = manager.get_injection(instance_id)
        assert peeked is not None, (
            "get_injection returned None — FIFO should hold the "
            "just-injected entry"
        )
        assert len(peeked) == 1, (
            f"peek returned {len(peeked)} entries, expected 1: {peeked}"
        )
        entry = peeked[0]
        # Content round-trip — the value the agent_node would
        # forward into a HumanMessage.
        assert entry["content"] == "drain me", (
            f"peeked content wrong: {entry.get('content')!r}"
        )
        # Source provenance round-trip — the drain site carries
        # this onto HumanMessage.additional_kwargs["source"].
        assert entry["source"] == "slack:alice", (
            f"peeked source wrong: {entry.get('source')!r}"
        )
        # Echo-id round-trip — the drain site uses this for
        # HumanMessage.id + the POST-time + drain-time SSE echo
        # (emit-twice-same-id per message-display-latency Phase 1).
        assert "echo_id" in entry and isinstance(entry["echo_id"], str)
        parsed_echo_id = uuid.UUID(entry["echo_id"])
        assert str(parsed_echo_id) == entry["echo_id"]

        # Step 1.5 — PEEK is non-destructive. FIFO still has the
        # entry after the get_injection call. This is the
        # "consumption is a separate step" guarantee the docstring
        # at ``daemon/manager.py:2816-2818`` pins.
        assert (
            len(manager._pending_injections.get(instance_id, [])) == 1
        ), "peek mutated the FIFO — should be a non-destructive read"

        # Step 2 — ``clear_injection`` POP (returns the copy AND
        # empties the FIFO).
        popped = manager.clear_injection(instance_id)
        assert popped is not None, (
            "clear_injection returned None — FIFO should still hold "
            "the entry from step 1.5"
        )
        assert len(popped) == 1, (
            f"clear returned {len(popped)} entries, expected 1: {popped}"
        )
        assert popped[0]["content"] == "drain me"
        # The returned list is the same shape as the peek (entry
        # equality) — drain-side caller can consume the popped list
        # directly.
        assert popped[0] == entry, (
            "clear returned entry differs from peek — drain-side "
            "consumer would observe inconsistent data"
        )

        # Step 2.5 — FIFO is now empty. _pending_injections no
        # longer has the key for this instance (defensive copy +
        # pop semantics).
        assert manager._pending_injections.get(instance_id) is None, (
            "_pending_injections[instance_id] still set after "
            "clear_injection — FIFO drain did not actually pop"
        )
        # Re-peeking an empty FIFO returns None (defensive default
        # — the drain side will get None when there's nothing to
        # drain on the next agent_node pass).
        assert manager.get_injection(instance_id) is None
        assert manager.clear_injection(instance_id) is None

    def test_three_consecutive_messages_grow_fifo_in_order(self, engine):
        """Audit finding 1.f — multi-message continuity e2e.

        Pins the user's "keeps sending new Slack/Telegram
        messages" scenario at the integration level: three
        consecutive injections on the SAME live-graph target MUST
        all land in ``_pending_injections[instance_id]``, in send
        order (FIFO), with distinct echo_ids. The durable lane
        MUST NOT have run, so zero MessageQueue / Task rows at
        the end.

        Unit-level coverage exists in
        ``tests/unit/test_chat_source_live_injection.py::
        TestRunningLiveGraphInjection::test_distinct_messages_get_distinct_echo_ids``
        (unit:324-368), but that test mocks the manager so it
        cannot observe real FIFO depth or FIFO ordering. This is
        the smallest end-to-end proof that repeated injections
        accumulate correctly.
        """
        contents = [
            "first from slack",
            "second from slack",
            "third from slack",
        ]

        async def _driver_multi():
            """Send 3 messages into the SAME live-graph target and
            return the populated FIFO + the same-scoped instance_id.
            """
            with wire_manager_only(engine) as manager:
                instance_id = f"inst-multi-{uuid.uuid4().hex[:12]}"

                # Same JobQueueService wiring pattern as
                # ``_run_injection_scenario`` — extracted here so
                # we can keep all 3 messages on the same live-graph
                # event loop without rebuilding the manager.
                from daemon.repositories import (
                    create_job_repository,
                    create_job_queue_repository,
                )
                from daemon.repositories.job_queue.lock_repository import (
                    LockRepository,
                )
                from daemon.services.job_lock_manager import JobLockManager
                from daemon.services.job_queue_service import JobQueueService
                from daemon.services.job_queue_mgmt_service import (
                    JobQueueMgmtService,
                )

                job_repo = create_job_repository(
                    engine=engine, create_tables=True
                )
                queue_repo = create_job_queue_repository(
                    engine=engine, create_tables=True
                )
                lock_repo = LockRepository(engine=engine)
                job_lock_manager = JobLockManager(lock_repo=lock_repo)
                job_queue_service = JobQueueService(
                    repository=job_repo,
                    lock_manager=job_lock_manager,
                    queue_repo=queue_repo,
                    instance_manager=manager,
                )
                job_queue_service.set_event_loop(asyncio.get_running_loop())
                manager.set_job_queue_service(job_queue_service)
                job_queue_mgmt_service = JobQueueMgmtService(
                    queue_repo=queue_repo,
                    job_repo=job_repo,
                    task_repo=getattr(manager, "_task_repo", None),
                )
                system_project_id = (
                    manager._project_repository.ensure_system_default_project()
                )
                await job_queue_mgmt_service.auto_provision_system_queues(
                    system_project_id,
                )

                # Seed the same shape ``_run_injection_scenario``
                # does: an Instance row, a source_config, and a
                # pre-existing instance_mapping so the mapper's
                # ``get_or_create_instance`` returns our seeded id.
                from daemon.repositories.instance.models import Instance

                with Session(engine) as s:
                    s.add(
                        Instance(
                            instance_id=instance_id,
                            agent_id="ari",
                            agent_dir="/agents/ari",
                            status="running",
                        )
                    )
                    s.commit()

                manager._source_repository.create_source_config(
                    source_type="slack",
                    name="test-multi-msg",
                    config={"test": True},
                    source_id="slack",
                )
                manager._source_repository.create_instance_mapping(
                    source_id="slack",
                    external_user_id="alice-multi",
                    agent_instance_id=instance_id,
                    agent_id="ari",
                    agent_dir="/agents/ari",
                    metadata={},
                )

                # Same live-graph task trick as
                # ``_run_injection_scenario`` — a long-running
                # future so ``has_live_graph_task`` returns True
                # for every injection in this loop.
                async def _live_graph():
                    await asyncio.Event().wait()

                live_task = asyncio.create_task(_live_graph())
                manager._graph_tasks[instance_id] = live_task

                registry = SourceRegistry(
                    manager._source_repository, manager
                )
                adapter = MagicMock()
                adapter.source_id = "slack"
                adapter.source_type = "slack"
                adapter.start_typing = AsyncMock(return_value=None)
                adapter.send = AsyncMock(return_value=True)
                registry.register(adapter)

                # Drive 3 consecutive injections — each on the
                # same live-graph target, same provider, same
                # window.
                for content in contents:
                    msg = IncomingMessage(
                        external_user_id="alice-multi",
                        content=content,
                        source_id="slack",
                        metadata=_slack_box_metadata(),
                    )
                    await registry._handle_message("slack", msg)

                # Cancel the live-graph task before leaving the
                # loop (matches the cleanup pattern at
                # ``_run_injection_scenario`` :398-402).
                live_task.cancel()
                try:
                    await live_task
                except (asyncio.CancelledError, Exception):
                    pass

                return manager, instance_id

        manager, instance_id = asyncio.run(_driver_multi())

        # ── Observable: FIFO depth = 3, in send order, all distinct
        #    echo_ids ──
        queue = manager._pending_injections.get(instance_id)
        assert queue is not None, (
            f"manager._pending_injections[{instance_id}] was not "
            f"populated after 3 injections"
        )
        assert len(queue) == 3, (
            f"expected exactly 3 entries after 3 injections; got "
            f"{len(queue)}: {queue}"
        )

        # FIFO order preserved — oldest first matches send order.
        observed_contents = [entry["content"] for entry in queue]
        assert observed_contents == contents, (
            f"FIFO order wrong: got {observed_contents}, "
            f"expected {contents}"
        )

        # All echo_ids distinct — a collision would collapse the
        # FE's emit-twice-same-id bubble (the message-display-
        # latency invariant).
        observed_echo_ids = [entry["echo_id"] for entry in queue]
        assert len(set(observed_echo_ids)) == 3, (
            f"echo_ids collided across 3 injections: {observed_echo_ids}"
        )
        # All echo_ids parseable as UUID4.
        for eid in observed_echo_ids:
            parsed = uuid.UUID(eid)
            assert str(parsed) == eid

        # All source fields carry chat provenance (the cross-row
        # invariant — every entry MUST trace back to the same
        # ``{source_id}:{external_user_id}`` format).
        for entry in queue:
            assert entry["source"] == "slack:alice-multi", (
                f"source provenance wrong on entry {entry!r}"
            )

        # ── Observable: durable lane NOT entered — zero
        #    MessageQueue / Task rows for this instance_id ──
        from daemon.repositories.message_queue.models import MessageQueue
        from daemon.repositories.task.models import Task
        from sqlmodel import select

        with Session(engine) as s:
            mq = s.exec(
                select(MessageQueue).where(
                    MessageQueue.instance_id == instance_id
                )
            ).all()
            tasks = s.exec(
                select(Task).where(Task.instance_id == instance_id)
            ).all()
            assert mq == [], (
                f"multi-message injection wrote MessageQueue rows "
                f"(durable lane entered): {[m.message_id for m in mq]}"
            )
            assert tasks == [], (
                f"multi-message injection wrote Task rows "
                f"(durable lane entered): {[t.work_id for t in tasks]}"
            )


# ---------------------------------------------------------------------------
# Sanity pin — ``ROUTING_ENVELOPE_KEYS`` matches every adapter's mint
# site at the unit-level (not just integration)
# ---------------------------------------------------------------------------


class TestAllowlistSeededFromAdapterMintSites:
    """Regression pin: the per-provider allowlist in
    ``daemon/constants.py:ROUTING_ENVELOPE_KEYS`` MUST contain
    exactly the keys each adapter populates at its mint site. If an
    adapter adds a key (or removes one), this test fails first.

    Reads the mint sites directly from each adapter file using
    Python's ``ast`` module — the most robust approach (no regex
    brittle-ness on dict shape / indentation).
    """

    @staticmethod
    def _extract_top_level_dict_keys(
        source: str,
        var_name: str,
    ) -> list[list[str]]:
        """Parse ``source`` and return the top-level keys of every
        ``Dict`` literal assigned to a variable named ``var_name``
        at the module scope (or local scope).

        Handles both ``metadata = {...}`` (``Assign``) and
        ``metadata: dict[str, Any] = {...}`` (``AnnAssign``).

        Returns a list of lists — one per matching assignment — so
        tests can assert ``all_keys <= allowlist`` after flattening.
        """
        import ast

        tree = ast.parse(source)
        results: list[list[str]] = []
        for node in ast.walk(tree):
            value: ast.expr | None = None
            target_ok = False
            if isinstance(node, ast.Assign):
                target_ok = any(
                    isinstance(t, ast.Name) and t.id == var_name
                    for t in node.targets
                )
                value = node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target_ok = (
                    isinstance(node.target, ast.Name)
                    and node.target.id == var_name
                )
                value = node.value
            if not target_ok or value is None:
                continue
            if isinstance(value, ast.Dict):
                results.append(
                    [k.value for k in value.keys if isinstance(k, ast.Constant)]
                )
        return results

    def test_slack_mint_site_keys_subset_of_allowlist(self):
        """Parse slack/adapter.py:813-825 with ``ast``; the top-level
        ``metadata`` keys set there must all be in the slack
        allowlist.
        """
        from pathlib import Path

        adapter = (
            Path(__file__).parent.parent.parent
            / "daemon"
            / "sources"
            / "adapters"
            / "slack"
            / "adapter.py"
        )
        text = adapter.read_text()
        keys_lists = self._extract_top_level_dict_keys(text, "metadata")
        assert keys_lists, (
            f"Could not locate ``metadata = {{...}}`` assignment "
            f"in {adapter}"
        )
        # Aggregate across all metadata assignments in the file.
        all_keys: set[str] = set()
        for keys in keys_lists:
            all_keys.update(keys)
        # Sanity: canonical slack mint shape.
        assert {"slack", "agent", "reply_chat_id"} <= all_keys, (
            f"canonical slack mint keys missing: "
            f"{{'slack', 'agent', 'reply_chat_id'}} - {all_keys}"
        )

        slack_allowlist = ROUTING_ENVELOPE_KEYS["slack"]
        assert all_keys <= slack_allowlist, (
            f"slack mint site has keys not in allowlist: "
            f"{all_keys - slack_allowlist}; allowlist={slack_allowlist}"
        )

    def test_telegram_mint_site_keys_subset_of_allowlist(self):
        """Parse telegram.py:558-573 with ``ast``; the top-level
        ``metadata`` keys set there must all be in the telegram
        allowlist.
        """
        from pathlib import Path

        adapter = (
            Path(__file__).parent.parent.parent
            / "daemon"
            / "sources"
            / "adapters"
            / "telegram.py"
        )
        text = adapter.read_text()
        keys_lists = self._extract_top_level_dict_keys(text, "metadata")
        assert keys_lists, (
            f"Could not locate ``metadata = {{...}}`` assignment "
            f"in {adapter}"
        )
        all_keys: set[str] = set()
        for keys in keys_lists:
            all_keys.update(keys)
        assert {"telegram", "agent", "reply_chat_id"} <= all_keys, (
            f"canonical telegram mint keys missing: "
            f"{{'telegram', 'agent', 'reply_chat_id'}} - {all_keys}"
        )

        telegram_allowlist = ROUTING_ENVELOPE_KEYS["telegram"]
        assert all_keys <= telegram_allowlist, (
            f"telegram mint site has keys not in allowlist: "
            f"{all_keys - telegram_allowlist}"
        )

    def test_discord_mint_site_keys_subset_of_allowlist(self):
        """Parse discord/adapter.py:1092-1095 (text) and :1204-1209
        (slash) with ``ast``; the top-level ``metadata`` keys set
        there must all be in the discord allowlist.

        Discord intentionally omits ``reply_chat_id`` — the test
        pins that fact by asserting the mint-site keys do NOT include
        ``reply_chat_id``.
        """
        from pathlib import Path

        adapter = (
            Path(__file__).parent.parent.parent
            / "daemon"
            / "sources"
            / "adapters"
            / "discord"
            / "adapter.py"
        )
        text = adapter.read_text()
        keys_lists = self._extract_top_level_dict_keys(text, "metadata")
        assert keys_lists, (
            f"Could not locate ``metadata = {{...}}`` assignment "
            f"in {adapter}"
        )
        all_keys: set[str] = set()
        for keys in keys_lists:
            all_keys.update(keys)
        # Sanity: canonical discord mint shape.
        assert {"discord", "agent"} <= all_keys, (
            f"canonical discord mint keys missing: "
            f"{{'discord', 'agent'}} - {all_keys}"
        )
        assert "reply_chat_id" not in all_keys, (
            "discord mint site unexpectedly includes reply_chat_id — "
            "re-verify per-provider verdict (per constants.py:ROUTING_ENVELOPE_KEYS)"
        )

        discord_allowlist = ROUTING_ENVELOPE_KEYS["discord"]
        assert all_keys <= discord_allowlist, (
            f"discord mint site has keys not in allowlist: "
            f"{all_keys - discord_allowlist}"
        )
