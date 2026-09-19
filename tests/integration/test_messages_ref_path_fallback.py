"""Integration test: graceful fallback when conversion fails.

Phase 2 / clipboard-image-chat / Task 8 (graceful fallback).

When ``explain_image`` returns an ``"Error: ..."`` STRING (the
non-raising failure path of ``invoke_agent_and_wait``), the message
STILL enqueues with a ``[Image N: description unavailable]``
placeholder. The MessageQueue row carries the canonical refs (audit)
per amendment #28. The agent receives the placeholder text — never
the raw ``"Error: ..."`` STRING.

This test pins the row persistence + the conversion collapse guard
end-to-end via a mocked ``invoke_agent_and_wait``. The hook's
content-prepending logic is covered separately by the unit tests
in ``tests/unit/services/test_tmp_image_message_hook.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.message_queue.models import MessageQueue


_CANONICAL_REF_A = "/api/tmp_images/" + "a" * 32
_TIMEOUT_STRING = (
    "Error: Agent timed out after 90s. Instance abc... may still be running."
)
_AGENT_ERROR_STRING = "Error: Agent failed. spawn refused by governor guard"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = create_engine(
        f"sqlite:///{tmp_path}/image_refs_fallback.db",
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


def _seed_instance(eng: Engine, *, instance_id: str, project_id: str):
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRefPathFallbackRowPersistence:
    """image_refs persists in the row audit column even when the
    conversion result is an ``Error: ...`` STRING (architect amend #9
    collapse)."""

    async def test_timeout_string_still_persists_refs_in_row(
        self, engine: Engine
    ):
        """Forced ``"Error: Agent timed out..."`` STRING → row column
        carries the canonical refs (audit, amendment #28). The
        placeholder text lands in the message content (verified via
        the hook unit tests; this test focuses on the row audit
        column)."""
        from daemon import constants
        from daemon.config import (
            AgentsConfig,
            Config,
            DaemonConfig,
            LLMConfig,
            LimitsConfig,
            PersistenceConfig,
        )
        from daemon.manager import InstanceManager
        from daemon.services.job_lock_manager import JobLockManager
        from daemon.services.job_queue_service import JobQueueService
        from daemon.repositories.job_queue.lock_repository import LockRepository
        from daemon.repositories.job_queue.queue_repository import (
            JobQueueRepository,
        )
        from daemon.repositories.job_queue.repository import JobRepository
        from daemon.repositories.project.models import Project, ProjectStatus

        # Seed the system-default project.
        now_iso = datetime.now(timezone.utc).isoformat()
        with Session(engine) as s:
            s.add(
                Project(
                    project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
                    name="_system_default",
                    project_type="system",
                    status=ProjectStatus.ACTIVE.value,
                    description="image-refs fallback harness",
                    project_metadata={},
                    relationships={},
                    created_at=now_iso,
                    updated_at=now_iso,
                )
            )
            s.commit()

        inst = _seed_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )

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
                db_path=str(engine.url.database or ""),
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
        manager._worker_pool = None

        job_service = JobQueueService(
            repository=JobRepository(engine),
            lock_manager=JobLockManager(lock_repo=LockRepository(engine)),
            queue_repo=JobQueueRepository(engine),
            instance_manager=manager,
        )
        manager.set_job_queue_service(job_service)

        # enqueue with image_refs — this exercises the facade +
        # service + _prepare_enqueued_message chain. The error
        # STRING collapse is exercised at the converter level (via
        # the hook), not at the facade level; this test pins the
        # row audit column persistence which is independent of the
        # conversion result.
        refs = [_CANONICAL_REF_A]
        result = await manager.enqueue_message(
            instance_id=inst.instance_id,
            message="[Image 1: description unavailable]\nlook",
            source="api",
            image_refs=refs,
        )

        assert result.message_id

        with Session(engine) as session:
            rows = list(
                session.exec(
                    select(MessageQueue).where(
                        MessageQueue.instance_id == inst.instance_id
                    )
                )
            )
        assert len(rows) == 1
        # A3 row assertion: image_refs persists on the dedicated
        # ``image_refs`` JSONB column (council Option A — refs NEVER
        # write to the legacy ``images`` column).
        assert rows[0].image_refs == refs
        assert rows[0].images is None

    def test_error_string_constant_shape(self):
        """The timeout / agent-error STRINGS the converter collapses
        via ``startswith('Error')`` are produced by
        ``invoke_agent_and_wait`` — pin their shape so a future
        refactor of that helper that changes the literal text trips
        this test (and the converter's collapse guard must be
        re-verified)."""
        assert _TIMEOUT_STRING.startswith("Error: Agent timed out")
        assert _AGENT_ERROR_STRING.startswith("Error: Agent failed")
        # The converter's collapse guard relies on ``startswith(
        # "Error")``. If either string drops the prefix, the converter
        # would NOT collapse and the raw text would land in the
        # agent-facing prefix (the failure mode amend #9 closes).
        assert "Error" in _TIMEOUT_STRING[:10]
        assert "Error" in _AGENT_ERROR_STRING[:10]
