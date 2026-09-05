"""Shared test fixtures for the leader-completion-attestation Phase 5 matrix.

The repository's root ``tests/conftest.py`` intentionally installs lightweight
``langgraph`` stubs for unit tests.  Attestation graph tests need the real
packages, so the fixtures in this module provide the same evict/restore pattern
used by the existing real-graph integration tests.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

from daemon.repositories.event.models import Event
from daemon.repositories.dependency_bus.models import DependencyWatcher, DependencyWatcherState
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from tests.helpers.checkpoint_prune_pg import evict_langgraph_mocks, restore_langgraph_mocks

from tests.support.scripted_chat_model import ScriptedChatModel


@pytest.fixture
def real_graph_module():
    """Yield ``daemon.graph`` with the real LangGraph modules installed."""
    saved = evict_langgraph_mocks()
    saved_daemon_graph = sys.modules.pop("daemon.graph", None)
    try:
        module = importlib.import_module("daemon.graph")
        yield module
    finally:
        sys.modules.pop("daemon.graph", None)
        if saved_daemon_graph is not None:
            sys.modules["daemon.graph"] = saved_daemon_graph
        restore_langgraph_mocks(saved)


@pytest.fixture
def scripted_chat_model():
    """Return a factory for a fail-loud, multi-turn scripted chat model."""
    def factory(responses) -> ScriptedChatModel:
        if isinstance(responses, ScriptedChatModel):
            return responses
        return ScriptedChatModel(responses=list(responses), i=0)

    return factory


@pytest.fixture
def memory_saver():
    """A small real LangGraph in-memory checkpointer for graph tests."""
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


@pytest.fixture
def file_sqlite_engine(tmp_path: Path):
    """Create the narrow file-backed SQLite schema used by attestation E2E.

    Full migration boot is intentionally avoided: the repository has a known
    fresh-SQLite incompatibility in an unrelated migration.  The tables in
    this matrix are created directly, with WAL and a bounded busy timeout.
    """
    db_path = tmp_path / "attestation_e2e.sqlite"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(
        engine,
        tables=[
            Instance.__table__,
            DependencyWatcher.__table__,
            MessageQueue.__table__,
            Task.__table__,
            Event.__table__,
        ],
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def attestation_repository(file_sqlite_engine):
    """Create a repository and a leader instance for graph/ledger tests."""
    repo = SQLModelInstanceRepository(file_sqlite_engine)
    instance = repo.create(
        instance_id="attestation-leader-e2e",
        agent_id="leader",
        agent_dir="./agents/leader",
    )
    return repo, instance


@pytest.fixture
def attestation_manager_factory():
    """Build a lightweight manager exposing the production gate surfaces.

    The optional ``pending_children`` and ``queued_wakeups`` values are
    deliberate overrides.  With ``pending_children=None`` (the default),
    the facade reads the real ``dependency_watchers`` table, allowing a test
    to assert the PENDING row before asking the gate to evaluate.
    """

    def factory(
        engine,
        repo: SQLModelInstanceRepository,
        *,
        instance_id: str = "attestation-leader-e2e",
        pending_children: int | None = None,
        queued_wakeups: int = 0,
    ):
        class GraphTestManager:
            _instance_repository = repo
            enqueue_message = MagicMock(name="enqueue_message")

            def count_pending_children(self, target_instance_id: str) -> int:
                if pending_children is not None:
                    return pending_children
                with Session(engine) as session:
                    return int(
                        session.scalar(
                            select(func.count())
                            .select_from(DependencyWatcher)
                            .where(
                                DependencyWatcher.target_instance_id == target_instance_id,
                                DependencyWatcher.state == DependencyWatcherState.PENDING.value,
                            )
                        )
                        or 0
                    )

            def get_queued_or_expected_wakeups(self, target_instance_id: str) -> int:
                return int(queued_wakeups)

            @staticmethod
            def is_watchover_enabled(_target_instance_id: str) -> bool:
                return False

            @staticmethod
            def is_question_pause_requested(_target_instance_id: str) -> bool:
                return False

            @staticmethod
            def set_deferred_watchover_terminate(_target_instance_id: str) -> None:
                return None

        manager = GraphTestManager()
        # Keep the real facade behavior while exposing call assertions to the
        # matrix tests (a plain function would not record invocation count).
        manager.count_pending_children = MagicMock(
            side_effect=manager.count_pending_children
        )
        manager.get_queued_or_expected_wakeups = MagicMock(
            side_effect=manager.get_queued_or_expected_wakeups
        )
        return manager

    return factory


@pytest.fixture
def patch_build_instance_llms(real_graph_module, monkeypatch):
    """Patch the real graph seam to use a scripted model for both LLM slots."""
    def install(model: ScriptedChatModel):
        monkeypatch.setattr(
            real_graph_module,
            "build_instance_llms",
            lambda **_: (model, model),
        )
        return model

    return install
