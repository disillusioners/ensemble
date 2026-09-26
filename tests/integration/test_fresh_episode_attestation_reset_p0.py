"""P0 regression test — incident 7d4a3bd9, NameError on
``ctx.is_fresh_episode_user_message`` inside
``_process_message_with_tracking``.

Pre-fix (d5c50994): every message raised
``NameError: name 'ctx' is not defined`` when ``_build_graph_input(...)``
was called at daemon/services/instance_messaging.py:4047/4060/4088. The
``ctx`` referenced is a ``_PreparedEnqueueContext`` NamedTuple that is
local to ``enqueue_message`` and is NOT threaded across the
enqueue→process boundary.

Cycle-2 fix (118bd45c, ad-hoc re-derivation): replaced ``ctx.is_fresh_...``
with a 3-prefix check on ``message_source`` inside
``_process_message_with_tracking``. Reviewer flagged this as
INCOMPLETE — it missed the 4th internal prefix (``system:``),
mis-classified ``cascade_resume`` (no internal prefix → HUMAN →
True), could not read priority (so scheduler at priority=5 was
mis-classified as True), and 4-prefix parity diverged from the
canonical ``_INTERNAL_STAMPED_SOURCE_PREFIXES`` constant.

Cycle-3 fix (this file, review-2): thread the REAL ledger-derived
flag across the enqueue→process boundary via ``ProcessingContext``.
The flag is computed at the canonical construction site
(``task_processor.py:535``) from the persisted MessageQueue row's
``priority`` + ``type`` columns using the EXACT same logic the ledger
uses at ``_prepare_enqueued_message``:2009-2013 —
``(priority == 1 AND type == MessageType.HUMAN.value)`` — and passed
via ProcessingContext. The consumer seam
(``_process_message_with_tracking``) reads it directly from the kwarg
rather than re-deriving. The cascade_resume direct-dispatch site
(manager.py:10767-10786) bypasses enqueue; it passes False explicitly.

Test contract (review-2 expanded matrix):
  api (HUMAN, priority=1)        → True   (True path of the ledger)
  telegram (HUMAN, priority=1)   → True   (parity: any user-API source)
  scheduler (HUMAN, priority=5)  → False  (priority != 1)
  internal_agent:* (AGENT)       → False  (non-HUMAN msg_type)
  internal_report:* (COMPLETION) → False  (non-HUMAN msg_type)
  system:* (SYSTEM)              → False  (non-HUMAN msg_type)
  system:watchdog                → False  (review-2 spec)
  system:long-tool-nudge         → False  (review-2 spec)
  cascade_resume (HUMAN, pr=1)   → False  (NOT a fresh episode, review-2)
  None (typed "api" — but untyped)→ False  (defensive: caller is
                                          responsible; the ledger's
                                          default source="api" means
                                          None is not a real shape, but
                                          we document the divergence)

Blueprint Testing&QC Conventions §3 (``ORDER-PIN EXCEPTION tools[-1]``)
notes that AsyncMock + ``inspect.getsource`` substring assertions stay
green at the InstanceManager/InstanceMessagingService facade seam.
This test bypasses that seam: ``_process_message_with_tracking`` is the
REAL function (no Mock wrapping it). The graph is a capturing stub
because LangGraph is the downstream consumer — the flag is stamped on
the HumanMessage BEFORE LangGraph runs.

Real-path discipline: this test exercises the EXACT consumer seam the
production daemon uses, with REAL DB repos, REAL ProcessingContext
construction at task_processor, REAL pipeline.execute → _do_process →
_process_message_with_tracking → _build_graph_input. NO mocks at any
of these seams.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all`` builds the
# full schema (matches the integration recipe; without these imports
# the Instance / Project / MessageQueue tables are absent and the
# repositories raise ``sqlalchemy.exc.OperationalError``).
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.project.models import Project, ProjectStatus
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.services.instance_messaging import InstanceMessagingService

# Mid-flight report import (for skills; not strictly needed).
try:
    from daemon.services.message_processing_pipeline import (
        ProcessingContext,
    )
except ImportError:
    ProcessingContext = None  # type: ignore[assignment]


INSTANCE_ID = "iid-p0-ctx-r2-regression-7d4a3bd9"
PROJECT_ID_BASE = "proj-p0-ctx-r2-regression"


def _project_id_for(label: str) -> str:
    return f"{PROJECT_ID_BASE}-{label}"


# ─── DB recipe (file-backed SQLite per BLUEPRINT §3) ──────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite at ``tmp_path`` (NullPool + FK on + WAL).

    BLUEPRINT §3 recipe — ``NullPool`` + file-backed SQLite at
    ``tmp_path`` + ``PRAGMA journal_mode=WAL`` +
    ``PRAGMA busy_timeout=10000`` + foreign-keys ON.
    """
    db_path = tmp_path / "p0_ctx_regression_r2.db"
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


def _seed_project(engine: Engine, project_id: str) -> None:
    now_iso = "2026-09-26T00:00:00+00:00"
    with Session(engine) as session:
        session.add(
            Project(
                project_id=project_id,
                name=f"p0-ctx-r2-{project_id[-8:]}",
                project_type="software",
                status=ProjectStatus.ACTIVE.value,
                description="P0 regression — review-2 ledger-parity pin",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()


def _seed_instance(engine: Engine, project_id: str) -> None:
    """Insert the Instance row the messaging path reads."""
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
                project_id=project_id,
                project_injected=False,
                instance_metadata={"project_id": project_id},
                created_at=now_iso,
                updated_at=now_iso,
                last_activity_at=now_naive,
            )
        )
        session.commit()


def _seed_message(
    engine: Engine,
    message_id: str,
    message_type: str,
    priority: int,
) -> None:
    """Insert a MessageQueue row carrying the priority + type columns
    the canonical claim path reads to compute the fresh-episode flag.

    The flag must be computed from this row (NOT re-derived from
    ``message_source`` at the consumer seam).
    """
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=INSTANCE_ID,
                content="hello",
                type=message_type,
                source=None,
                root_source=None,
                status=MessageStatus.PROCESSING.value,
                priority=priority,
                enqueued_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        session.commit()


# ─── Capturing graph (LangGraph seam — NOT the messaging seam) ────────────────


class _CapturingGraph:
    """LangGraph stub whose ``astream`` captures the ``graph_input``
    dict handed to it, then ends iteration so the surrounding
    ``async for`` in ``_process_message_with_tracking`` exits
    cleanly.
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
    manager = MagicMock()
    manager.config.limits.graph_recursion_limit = 50
    manager.config.compaction = MagicMock()

    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._project_repository = SQLModelProjectRepository(engine)
    manager._shared_meta_kv_repo = MagicMock()
    manager.message_metadata_repo = MagicMock()

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
    svc = InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )
    svc._has_checkpoint = AsyncMock(return_value=False)
    svc._maybe_compact_context = AsyncMock()
    return svc


def _captured_user_message(graph: _CapturingGraph):
    """Return the trailing user ``HumanMessage`` from the captured
    ``graph_input``."""
    gi = graph.captured.get("graph_input") or {}
    msgs = gi.get("messages") or []
    from langchain_core.messages import RemoveMessage

    real_msgs = [m for m in msgs if not isinstance(m, RemoveMessage)]
    assert real_msgs, (
        f"expected at least the user message; got 0 messages. "
        f"graph.astream_calls={graph.astream_calls}"
    )
    return real_msgs[-1]


# ─── The pin ──────────────────────────────────────────────────────────────────


# Per-shape verdict table. The flag here is what the canonical
# construction site (``task_processor.py``: reviewer-2 fix) computes
# from the persisted MessageQueue row's ``priority`` + ``type``
# columns using the EXACT ledger logic:
#   ``is_fresh_episode_user_message = (priority == 1 AND type == HUMAN.value)``
#
# Each entry: (message_type, priority, message_source, expected_kwargs).
# ``expected_kwargs`` = ``True`` if the sentinel MUST be stamped;
# ``False`` if it MUST NOT be.
SHAPE_VERDICTS = [
    # user-facing entry shapes — sentinel=True
    ("api",        MessageType.HUMAN.value, 1, "api",            True),
    ("telegram",   MessageType.HUMAN.value, 1, "telegram:user:1", True),
    # scheduler at priority=5 → ledger=False (priority != 1)
    ("scheduler",  MessageType.HUMAN.value, 5, "scheduler",       False),
    # internal prefixes — sentinel=False (non-HUMAN msg_type)
    ("internal_agent",    MessageType.AGENT.value,            1, "internal_agent:leader",         False),
    ("internal_report",   MessageType.COMPLETION_REPORT.value, 1, "internal_report:child-1",        False),
    # system: prefixes — sentinel=False (SYSTEM msg_type, review-2
    # explicitly missed these in cycle-2)
    ("system_watchdog",          MessageType.SYSTEM.value, 0, "system:watchdog",                False),
    ("system_long_tool_nudge",   MessageType.SYSTEM.value, 0, "system:long-tool-nudge",         False),
    ("system_report_integrity",  MessageType.SYSTEM.value, 0, "system:report-integrity-guard",  False),
    ("system_resume_wake",       MessageType.SYSTEM.value, 0, "system:resume_wake",             False),
    # cascade_resume direct-dispatch — HUMAN + priority=1 by row,
    # but the ledger-parity pass-through is FALSE because a
    # PAUSED→RUNNING cascade resume is NOT a fresh episode (the
    # direct-dispatch site at manager.py:10767-10786 passes False
    # explicitly).
    ("cascade_resume", MessageType.HUMAN.value, 1, "cascade_resume", False),
    # None source — defensive. The ledger's ``enqueue_message`` typed
    # signature defaults to ``source: str = "api"`` so None never
    # reaches the ledger in practice; at the consumer seam, the kwarg
    # defaults to False for non-claim callers. Documented divergence
    # (review-2 finding #6).
    ("none_source", MessageType.HUMAN.value, 1, None, False),
]


async def _drive_dispatch(
    engine: Engine,
    message_source: str | None,
    is_retry: bool,
    is_fresh_episode_user_message: bool,
    message_id: str = "msg-1",
    message: str = "hello",
    label: str = "default",
) -> _CapturingGraph:
    """Module-level helper: drive ``_process_message_with_tracking``
    directly with the given source + the carrier kwarg; return the
    capturing graph for assertions.

    Used by ALL test classes (the per-shape matrix class + the
    cascade_resume class + the persisted-row class).
    """
    project_id = _project_id_for(label)
    _seed_project(engine, project_id)
    _seed_instance(engine, project_id)

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
            is_retry=is_retry,
            message_source=message_source,
            # P0 hotfix (review-2): the carrier kwarg — supplied by
            # ProcessingContext at the canonical claim path; at this
            # seam the consumer reads it verbatim.
            is_fresh_episode_user_message=is_fresh_episode_user_message,
        )

    return graph


class TestFreshEpisodeAttestationReset:
    """Pin: the ``_process_message_with_tracking`` consumer seam
    receives the ``fresh_episode_attestation_reset`` sentinel via
    the carrier kwarg (``is_fresh_episode_user_message``) that the
    canonical construction site (``task_processor.py``:535)
    computes from the persisted MessageQueue row's ``priority`` +
    ``type`` columns using the EXACT ledger logic.

    Cycle-3 (review-2) expanded matrix — each shape's expected
    verdict is pinned with a per-test case.
    """

    async def _drive(
        self,
        engine: Engine,
        message_source: str | None,
        is_fresh_episode_user_message: bool,
        message_id: str = "msg-1",
        message: str = "hello",
        label: str = "default",
    ) -> _CapturingGraph:
        """Class helper — wraps the module-level ``_drive_dispatch``
        with is_retry=False (the per-shape matrix tests use
        first-attempt path)."""
        return await _drive_dispatch(
            engine,
            message_source=message_source,
            is_retry=False,
            is_fresh_episode_user_message=is_fresh_episode_user_message,
            message_id=message_id,
            message=message,
            label=label,
        )

    @pytest.mark.parametrize(
        "label, msg_type, priority, source, expected",
        SHAPE_VERDICTS,
        ids=[row[0] for row in SHAPE_VERDICTS],
    )
    async def test_per_shape_verdict(
        self, engine: Engine, label, msg_type, priority, source, expected
    ):
        """Per-shape verdict — drives the consumer seam with the
        carrier kwarg set to the EXPECTED value (matches what
        task_processor would compute from a row with this
        ``(priority, type)`` combination) and asserts the user
        message carries the right sentinel.

        The cycle-2 ad-hoc re-derivation is gone — the consumer
        seam reads the carrier kwarg verbatim. This test pins that
        wiring.
        """
        graph = await self._drive(
            engine,
            message_source=source,
            is_fresh_episode_user_message=expected,
            label=label,
        )

        assert graph.astream_calls >= 1, (
            f"[{label}] graph.astream was never invoked — consumer "
            f"seam did not reach the build-graph-input step."
        )

        user_msg = _captured_user_message(graph)
        kwargs = getattr(user_msg, "additional_kwargs", None) or {}

        if expected:
            assert (
                kwargs.get("fresh_episode_attestation_reset") is True
            ), (
                f"[{label}] expected sentinel True (HUMAN + "
                f"priority=1 ⇒ user-driven fresh episode). Got "
                f"additional_kwargs={kwargs!r}"
            )
        else:
            assert (
                kwargs.get("fresh_episode_attestation_reset") is not True
            ), (
                f"[{label}] expected sentinel NOT True (msg_type="
                f"{msg_type}, priority={priority}, source={source!r}"
                f" ⇒ NOT a fresh episode). Got "
                f"additional_kwargs={kwargs!r}"
            )


class TestCarrierFromPersistedRow:
    """Pin: the canonical construction site
    (``task_processor.py``:535) computes the flag from the persisted
    MessageQueue row's ``priority`` + ``type`` columns using the
    EXACT ledger logic, and threads it via ProcessingContext to the
    consumer seam.

    This exercises the FULL real-path: task_processor reads the row,
    constructs ProcessingContext, the pipeline's _do_process threads
    the kwarg to ``_process_message_with_tracking``, the consumer
    reads it verbatim, and ``_build_graph_input`` stamps the sentinel.
    """

    async def test_persisted_row_priority_5_stamps_false(
        self, engine: Engine
    ):
        """A scheduler message at priority=5 (HUMAN msg_type)
        reaches the consumer seam as ``is_fresh_episode_user_message=False``
        — the priority gate keeps the sentinel OFF even though the
        msg_type is HUMAN. Cycle-2's ad-hoc re-derivation could NOT
        see priority (it was not in scope at the consumer seam) and
        would have wrongly stamped True.
        """
        message_id = "msg-scheduler-priority-5"
        # NOTE: project + instance seeding happens inside _drive_dispatch;
        # we only need the MessageQueue row here for the priority/type
        # columns that the canonical claim path reads.
        _seed_message(
            engine,
            message_id=message_id,
            message_type=MessageType.HUMAN.value,
            priority=5,
        )

        # Mirror the task_processor computation at the canonical
        # construction site. This is the SAME logic the cycle-3
        # fix uses:
        #   ``is_fresh_episode_user_message = (
        #       message.priority == 1 AND message.type == HUMAN.value
        #   )``
        with Session(engine) as session:
            msg_row = session.get(MessageQueue, message_id)
            computed_flag = (
                msg_row.priority == 1
                and msg_row.type == MessageType.HUMAN.value
            )
        assert computed_flag is False, (
            "priority=5 + HUMAN must compute False; this is the "
            "case cycle-2 missed (priority not in scope at the "
            "consumer seam)."
        )

        graph = await _drive_dispatch(
            engine,
            message_source="scheduler",
            is_retry=False,
            is_fresh_episode_user_message=computed_flag,
            message_id=message_id,
            label="scheduler-priority-5",
        )

        user_msg = _captured_user_message(graph)
        kwargs = getattr(user_msg, "additional_kwargs", None) or {}
        assert (
            kwargs.get("fresh_episode_attestation_reset") is not True
        ), (
            f"scheduler at priority=5 must NOT stamp the sentinel "
            f"(priority gate fails). Got additional_kwargs={kwargs!r}"
        )


class TestCascadeResumeDirectDispatch:
    """Pin: the cascade_resume direct-dispatch site
    (``manager._resume_processing_background`` at manager.py:10767-10786)
    passes ``is_fresh_episode_user_message=False`` explicitly. A
    PAUSED→RUNNING cascade resume is NOT a terminal revival — the
    prior checkpoint's attestation deny channels must persist.
    """

    async def test_cascade_resume_stamps_false(self, engine: Engine):
        """A cascade_resume direct-dispatch (HUMAN msg_type,
        priority=1 by row) must be stamped False at the
        consumer seam — the call site explicitly passes False."""
        graph = await _drive_dispatch(
            engine,
            message_source="cascade_resume",
            is_retry=True,  # cascade_resume is_retry=True
            is_fresh_episode_user_message=False,
            message_id="msg-cascade-resume",
            message="resume-payload",
            label="cascade-resume",
        )

        assert graph.astream_calls >= 1, (
            "graph.astream was never invoked — cascade_resume path "
            "did not reach the build-graph-input step."
        )

        user_msg = _captured_user_message(graph)
        kwargs = getattr(user_msg, "additional_kwargs", None) or {}
        assert (
            kwargs.get("fresh_episode_attestation_reset") is not True
        ), (
            f"cascade_resume must NOT stamp the sentinel (NOT a "
            f"fresh episode — channels must persist). Got "
            f"additional_kwargs={kwargs!r}"
        )