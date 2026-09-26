"""P0 regression test — incident 7d4a3bd9, NameError on
``ctx.is_fresh_episode_user_message`` inside
``_process_message_with_tracking``.

Pre-fix: every message raises ``NameError: name 'ctx' is not defined``
when ``_build_graph_input(...)`` is called at daemon/services/instance_messaging.py
lines 4047/4060/4088. The ``ctx`` referenced is a ``_PreparedEnqueueContext``
NamedTuple that is local to ``enqueue_message`` and is NOT threaded across
the enqueue→process boundary (the worker pool/task processor only pass
``message_source`` via ``ProcessingContext``).

The bug slipped past the ``test_lca_false_complete_fixes`` family because
those tests assert the ``fresh_episode_attestation_reset`` parameter on
``_build_graph_input`` directly — they never exercise the messaging
seam where the kwarg is *constructed*. This file is the seam-mocking
antidote: a real-path test that drives
``_process_message_with_tracking`` through the actual code path the
production daemon uses and asserts the flag is computed correctly
BOTH ways (fresh-episode user message vs. mid-mission internal
message).

Test contract:

1. ``test_no_name_error_on_user_message`` — a user-API message arrives
   on a fresh instance; the call must NOT raise NameError.
2. ``test_user_message_stamps_fresh_episode_sentinel_true`` — the user
   message's ``additional_kwargs`` carries
   ``fresh_episode_attestation_reset=True`` (this is the user-driven
   fresh-episode shape the ledger path was protecting).
3. ``test_internal_agent_message_does_not_stamp_sentinel`` — a
   ``internal_agent:`` message (parent dispatch) arrives mid-mission;
   the user message's ``additional_kwargs`` does NOT carry the
   sentinel (internal sources are NOT new missions).
4. ``test_internal_report_message_does_not_stamp_sentinel`` — an
   ``internal_report:`` message does NOT carry the sentinel.

Blueprint Testing&QC Conventions §3 (``ORDER-PIN EXCEPTION tools[-1]``)
notes that AsyncMock + ``inspect.getsource`` substring assertions stay
green at the InstanceManager/InstanceMessagingService facade seam.
This test bypasses that seam: ``_process_message_with_tracking`` is the
REAL function (no Mock wrapping it). The graph is a capturing stub
because LangGraph is the downstream consumer — the flag is stamped on
the HumanMessage BEFORE LangGraph runs.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all`` builds the
# full schema (matches the integration recipe; without these imports
# the Instance / Project tables are absent and the repositories raise
# ``sqlalchemy.exc.OperationalError``).
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401

from datetime import datetime, timezone

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.project.models import Project, ProjectStatus
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.services.instance_messaging import InstanceMessagingService


INSTANCE_ID = "iid-p0-ctx-regression-7d4a3bd9"
PROJECT_ID = "proj-p0-ctx-regression"


# ─── DB recipe (file-backed SQLite per BLUEPRINT §3) ──────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite at ``tmp_path`` (NullPool + FK on + WAL).

    BLUEPRINT §3 recipe — ``NullPool`` + file-backed SQLite at
    ``tmp_path`` + ``PRAGMA journal_mode=WAL`` +
    ``PRAGMA busy_timeout=10000`` + foreign-keys ON.
    """
    db_path = tmp_path / "p0_ctx_regression.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable(dbapi_conn, _connection_record):
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


# ─── Seed helpers ─────────────────────────────────────────────────────────────


def _seed_project(engine: Engine) -> None:
    now_iso = "2026-09-26T00:00:00+00:00"
    with Session(engine) as session:
        session.add(
            Project(
                project_id=PROJECT_ID,
                name="p0-ctx-regression",
                project_type="software",
                status=ProjectStatus.ACTIVE.value,
                description="P0 regression — ctx NameError fix pin",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()


def _seed_instance(engine: Engine) -> None:
    """Insert the Instance row that ``_process_message_with_tracking``
    reads via ``self._manager._instance_repository.get(instance_id)``.

    ``project_injected`` is False so the first-turn path enters the
    ``assemble_context_messages`` branch. The agent_meta lookup uses
    ``daemon.registry.get_registry`` (patched below).
    """
    now_iso = "2026-09-26T00:00:00+00:00"
    now_naive = datetime(2026, 9, 26, 0, 0, 0)
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=INSTANCE_ID,
                agent_id="worker",
                agent_dir="./agents/worker",
                status=InstanceStatus.IDLE.value,
                parent_id=None,
                project_id=PROJECT_ID,
                project_injected=False,
                instance_metadata={"project_id": PROJECT_ID},
                created_at=now_iso,
                updated_at=now_iso,
                last_activity_at=now_naive,
            )
        )
        session.commit()


# ─── Capturing graph (LangGraph seam — NOT the messaging seam) ────────────────


class _CapturingGraph:
    """LangGraph stub whose ``astream`` captures the ``graph_input``
    dict handed to it, then ends iteration so the surrounding
    ``async for`` in ``_process_message_with_tracking`` exits
    cleanly.

    The captured ``graph_input['messages']`` is what the test
    inspects — those are the messages LangGraph's ``add_messages``
    reducer would checkpoint (the persistent block + the user
    message). The user message's ``additional_kwargs`` carries the
    ``fresh_episode_attestation_reset`` sentinel under test.
    """

    def __init__(self) -> None:
        self.captured: dict = {}
        self.astream_calls = 0

    async def astream(self, graph_input=None, *args, **kwargs):
        self.astream_calls += 1
        if graph_input is not None:
            self.captured["graph_input"] = graph_input
        return
        yield  # pragma: no cover

    async def aget_state(self, *args, **kwargs):
        return None


def _null_semaphore():
    sem = MagicMock()
    sem.lock = asynccontextmanager(lambda: (yield))
    sem.unlock = AsyncMock()
    return sem


def _build_manager_mock(engine: Engine):
    """REAL repositories + stubbed external sinks.

    The contract under pin is the messaging path itself
    (``_process_message_with_tracking``); the DB reads go through the
    REAL repository so the row layout is faithful. Externals
    (live_hub, queue_repository, graph_tasks, source_dispatcher) are
    stubbed because they are not the contract under pin.
    """
    manager = MagicMock()
    manager.config.limits.graph_recursion_limit = 50
    manager.config.compaction = MagicMock()

    # REAL repos.
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._project_repository = SQLModelProjectRepository(engine)
    manager._shared_meta_kv_repo = MagicMock()
    manager.message_metadata_repo = MagicMock()

    # Sink stubs.
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._queue_repository = MagicMock()
    manager._graph_tasks = {}
    manager.source_dispatcher = None
    manager._llm_semaphore = _null_semaphore()
    manager._skill_injection_service = None
    manager.clear_injection = MagicMock(return_value=None)
    manager.requeue_injections = MagicMock(return_value=None)
    manager._emitted_message_content = {}
    manager.get_context_skill_result = MagicMock(return_value=None)
    return manager


def _build_service(manager) -> InstanceMessagingService:
    """REAL ``InstanceMessagingService`` + stubbed checkpoint helpers.

    Both helpers are infrastructure concerns, NOT the contract under
    pin; the test targets the ``_build_graph_input`` call sites where
    the buggy ``ctx.is_fresh_episode_user_message`` reference lived.
    """
    svc = InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )
    svc._has_checkpoint = AsyncMock(return_value=False)
    svc._maybe_compact_context = AsyncMock()
    return svc


def _captured_user_message(graph: _CapturingGraph):
    """Return the trailing user ``HumanMessage`` from the captured
    ``graph_input``.

    ``_build_graph_input`` lays out
    ``[persistent..., prepended..., user]``; the LAST element is the
    user message — that is where the
    ``fresh_episode_attestation_reset`` sentinel is stamped on its
    ``additional_kwargs``.
    """
    gi = graph.captured.get("graph_input") or {}
    msgs = gi.get("messages") or []
    from langchain_core.messages import RemoveMessage

    real_msgs = [m for m in msgs if not isinstance(m, RemoveMessage)]
    assert real_msgs, (
        f"expected at least the user message; got 0 messages. "
        f"The harness must wire graph.astream to capture (see "
        f"_CapturingGraph). graph.astream_calls={graph.astream_calls}"
    )
    return real_msgs[-1]


# ─── The pin ──────────────────────────────────────────────────────────────────


class TestFreshEpisodeAttestationReset:
    """Pin: ``_process_message_with_tracking`` does NOT NameError on
    any source shape, and stamps the
    ``fresh_episode_attestation_reset`` sentinel on the user message's
    ``additional_kwargs`` ONLY for the user-driven fresh-episode
    shape (the same condition the ledger path at
    ``_prepare_enqueued_message``:2009-2013 uses).

    Pre-fix (commit ``d5c50994``): every message raised
    ``NameError: name 'ctx' is not defined`` because the
    ``ctx.is_fresh_episode_user_message`` reference points at a
    ``_PreparedEnqueueContext`` NamedTuple local to
    ``enqueue_message`` that is not threaded across the
    enqueue→process boundary.
    """

    async def _drive(
        self,
        engine: Engine,
        message_source: str,
        message_id: str = "msg-1",
        message: str = "hello",
    ) -> _CapturingGraph:
        """Drive ``_process_message_with_tracking`` through the
        real path with the given source and return the capturing
        graph for assertions."""
        _seed_project(engine)
        _seed_instance(engine)

        manager = _build_manager_mock(engine)
        graph = _CapturingGraph()
        manager.get_instance = AsyncMock(return_value=graph)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_version = MagicMock(return_value=None)
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(
                    context_injection_mode="human_messages"
                )
            )
            mock_get_registry.return_value = registry

            svc = _build_service(manager)

            await svc._process_message_with_tracking(
                instance_id=INSTANCE_ID,
                message=message,
                message_id=message_id,
                is_retry=False,
                message_source=message_source,
            )

        return graph

    async def test_no_name_error_on_user_message(self, engine: Engine):
        """Pre-fix NameError trigger: a user-API message arrives on
        a fresh instance. ``_process_message_with_tracking`` must
        NOT raise ``NameError`` — the call site at line 4088 must
        reach ``_build_graph_input`` without referencing the
        out-of-scope ``ctx`` NamedTuple.

        Pre-fix, this test raises:
            NameError: name 'ctx' is not defined
        at ``_build_graph_input(... fresh_episode_attestation_reset=
        ctx.is_fresh_episode_user_message)`` (line 4088 in the
        pre-fix tree).
        """
        graph = await self._drive(
            engine, message_source="api"
        )

        assert graph.astream_calls >= 1, (
            "graph.astream was never invoked — the messaging path "
            "did not reach the build-graph-input step. Pre-fix this "
            "raises NameError at line 4088."
        )
        assert "graph_input" in graph.captured, (
            "graph.astream was called but no graph_input was "
            "captured. The harness must assign graph_input to "
            "self.captured (see _CapturingGraph.astream)."
        )

    async def test_user_message_stamps_fresh_episode_sentinel_true(
        self, engine: Engine
    ):
        """A user-API message (HUMAN-type, default priority) must
        stamp ``fresh_episode_attestation_reset=True`` on the user
        message's ``additional_kwargs`` — this matches the ledger
        path's flag (``priority==1 AND msg_type==HUMAN``) so the
        ``attestation_gate_node`` clears the SessionState channels
        on the first post-revival turn.
        """
        graph = await self._drive(
            engine, message_source="api"
        )

        user_msg = _captured_user_message(graph)
        kwargs = getattr(user_msg, "additional_kwargs", None) or {}
        assert (
            kwargs.get("fresh_episode_attestation_reset") is True
        ), (
            f"A1 — user-API message must stamp "
            f"fresh_episode_attestation_reset=True on the user "
            f"message (matches ledger path semantics). Got "
            f"additional_kwargs={kwargs!r}"
        )

    async def test_internal_agent_message_does_not_stamp_sentinel(
        self, engine: Engine
    ):
        """A parent-dispatch ``internal_agent:`` message (AGENT
        msg_type) must NOT stamp the sentinel — internal
        agent-to-agent messages are NOT new missions and must not
        reset the attestation counter.

        The ledger path's flag is ``priority==1 AND msg_type==
        HUMAN``; ``internal_agent:`` source maps to AGENT
        msg_type, so the flag is False.
        """
        graph = await self._drive(
            engine, message_source="internal_agent:leader"
        )

        user_msg = _captured_user_message(graph)
        kwargs = getattr(user_msg, "additional_kwargs", None) or {}
        # Either absent, or present-but-False: both are
        # semantically correct (the sentinel's downstream consumer
        # treats False/absent identically). The strict contract
        # is "not True".
        assert (
            kwargs.get("fresh_episode_attestation_reset") is not True
        ), (
            f"A1 — internal_agent: message must NOT stamp "
            f"fresh_episode_attestation_reset=True (only HUMAN-type "
            f"user messages reset the counter). Got "
            f"additional_kwargs={kwargs!r}"
        )

    async def test_internal_report_message_does_not_stamp_sentinel(
        self, engine: Engine
    ):
        """An ``internal_report:`` message (COMPLETION_REPORT
        msg_type) must NOT stamp the sentinel — completion reports
        are NOT new missions.

        The ledger path's flag is ``priority==1 AND msg_type==
        HUMAN``; ``internal_report:`` source maps to
        COMPLETION_REPORT, so the flag is False.
        """
        graph = await self._drive(
            engine, message_source="internal_report:child-1"
        )

        user_msg = _captured_user_message(graph)
        kwargs = getattr(user_msg, "additional_kwargs", None) or {}
        assert (
            kwargs.get("fresh_episode_attestation_reset") is not True
        ), (
            f"A1 — internal_report: message must NOT stamp "
            f"fresh_episode_attestation_reset=True. Got "
            f"additional_kwargs={kwargs!r}"
        )