"""Integration test — the checkpoint-pool knobs reach the saver pool FROM
THE BOOT-CONFIG RESOLUTION SITE (plan Track-1 item 5, severed-knob repair
D5).

The defect class here is the seam-drift class the house facade-forwarding
discipline guards: the resolved knobs can be silently dropped BETWEEN the
resolution site (``InstanceManager.initialize``, which holds the full YAML
``Config``) and the consumer (``create_postgres_checkpointer``'s pool
construction). An ``inspect.getsource`` substring pin or an ``AsyncMock``
on the boundary stays green through exactly that drop — so this test
drives the REAL chain instead:

    InstanceManager.initialize()          (real; reads self.config.limits)
      → get_checkpointer(...)             (real; forwards kwargs)
        → create_postgres_checkpointer(…) (real; builds the pools)
          → AsyncConnectionPool(max_size=…, timeout=…)   [captured]
          → asyncpg.create_pool(max_size=…)              [captured]

No PostgreSQL server is contacted: only the checkpoint lazy-import layer
is replaced with in-process fakes; everything else (the real manager, the
real YAML ``Config`` with custom ``LimitsConfig`` values, the real
SQLite-backed repositories, the real boot sweep / maintenance wiring) is
production code.

NOTE (facade-forwarding discipline applicability): this fix threads NO
``InstanceManager`` facade method over ``InstanceMessagingService`` —
``initialize`` calls the module-level ``get_checkpointer`` directly. The
seam-drift risk is nevertheless real (kwargs dropped between resolution
and consumption), so the house rule is honored in spirit: a real-dispatch
test through the entire chain, not a source-text assertion.
"""
from __future__ import annotations

import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[2]


def _boot_config():
    """A REAL YAML ``Config`` (mission-record test pattern) whose
    ``limits`` carry CUSTOM checkpoint-pool values — chosen to differ
    from the constants defaults (10 / 5.0) so a severed read can never
    coincide with a passing assertion."""
    from daemon.config import (
        AgentsConfig,
        Config,
        DaemonConfig,
        LLMConfig,
        LimitsConfig,
        PersistenceConfig,
    )

    return Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7,
        ),
        limits=LimitsConfig(
            checkpoint_pool_max_size=13,
            checkpoint_pool_timeout_s=7.5,
        ),
        persistence=PersistenceConfig(
            db_path=str(Path("/tmp") / f"ckpt-knob-{uuid.uuid4().hex[:8]}.db"),
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
        ),
        daemon=DaemonConfig(host="127.0.0.1", port=8079),
        agents=AgentsConfig(directory=str(REPO_ROOT / "agents")),
    )


def _build_fakes(captured: dict) -> dict:
    """In-process fakes for ``create_postgres_checkpointer``'s lazy-import
    layer (same proven shape as
    ``tests/unit/test_persistence_prepare_threshold.py``)."""
    fake_saver_instance = MagicMock(name="AsyncPostgresSaver")
    fake_saver_instance.setup = AsyncMock()

    fake_aio_module = MagicMock()
    fake_aio_module.AsyncPostgresSaver = lambda conn: fake_saver_instance

    class _FakeAsyncpg:
        @staticmethod
        async def create_pool(*args, **kwargs):
            captured["asyncpg_kwargs"] = kwargs
            return MagicMock(name="asyncpg.Pool")

    class _FakeDictRow:
        pass

    class _FakeAsyncConnectionPool:
        check_connection = staticmethod(lambda conn: conn.execute(""))

        def __init__(self, **kwargs):
            captured["pool_kwargs"] = kwargs

        async def open(self):
            captured["pool_open_called"] = True

        async def close(self):
            pass

    class _FakePsycopg:
        AsyncConnection = MagicMock()
        rows = MagicMock()
        rows.dict_row = _FakeDictRow

    class _FakePsycopgPoolModule:
        AsyncConnectionPool = _FakeAsyncConnectionPool

    return {
        "asyncpg": _FakeAsyncpg,
        "psycopg": _FakePsycopg,
        "psycopg.rows": _FakePsycopg.rows,
        "psycopg_pool": _FakePsycopgPoolModule,
        "langgraph.checkpoint.postgres.aio": fake_aio_module,
        "daemon.services.secret_persistence_scrub": MagicMock(),
    }


@pytest.mark.asyncio
async def test_boot_config_limits_reach_the_saver_pool(caplog):
    """``InstanceManager.initialize`` must resolve BOTH checkpoint-pool
    knobs from ``self.config.limits`` (the boot-applied YAML Config) and
    they must survive the whole chain into the pool construction kwargs.

    RED at the pre-fix code: ``initialize`` called
    ``get_checkpointer(self._ensemble_config)`` with NO kwargs and the
    factory's severed ``getattr(config, "limits", None)`` read always
    yielded 10 / 5.0 — the custom 13 / 7.5 could never appear here.
    """
    from daemon import constants
    from daemon.ensemble_config import EnsembleConfig
    from daemon.manager import InstanceManager
    from daemon.repositories.project.models import Project, ProjectStatus

    config = _boot_config()
    captured: dict = {}

    # File-backed SQLite engine for the manager's repositories (the ctor
    # takes the sqlite branch because ``ensemble_config`` is not yet set —
    # mirroring the production order where the lifespan assigns
    # ``_ensemble_config`` before ``initialize()``).
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    # The daemon provisions this row at boot — the manager reads it while
    # wiring the repositories (mission-record test pattern).
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Project(
                project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
                name="_system_default",
                project_type="system",
                status=ProjectStatus.ACTIVE.value,
                description="checkpoint-pool boot-resolution test",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()

    with (
        patch(
            "daemon.migrations.runner.MigrationRunner.run_pending_migrations",
            return_value=[],
        ),
        patch(
            "daemon.manager.create_engine_from_config",
            return_value=engine,
        ),
    ):
        manager = InstanceManager(config)

    # Production wiring order: the lifespan sets the ensemble config
    # BEFORE ``initialize()``. Postgres selection routes initialize into
    # ``get_checkpointer`` → ``create_postgres_checkpointer``.
    manager._ensemble_config = EnsembleConfig(database="postgres")

    with patch.dict(sys.modules, _build_fakes(captured)):
        with caplog.at_level(logging.INFO, logger="daemon.persistence"):
            await manager.initialize()

    try:
        pool_kwargs = captured["pool_kwargs"]
        assert pool_kwargs["max_size"] == 13, (
            "boot Config.limits.checkpoint_pool_max_size (13) must reach "
            "the saver pool's max_size — a 10 here means the knob was "
            "severed/dropped between the resolution site and the pool"
        )
        assert pool_kwargs["timeout"] == 7.5, (
            "boot Config.limits.checkpoint_pool_timeout_s (7.5) must "
            "reach the saver pool's acquisition timeout — a 5.0 here "
            "means the knob was severed/dropped"
        )
        assert captured["asyncpg_kwargs"]["max_size"] == 13
        assert captured["pool_open_called"] is True

        # Log truthfulness at boot: the applied values are printed.
        sizing = [
            r for r in caplog.records
            if "Checkpoint pool sizing" in r.getMessage()
        ]
        assert sizing, "the applied-sizing INFO line must be logged at boot"
        message = sizing[-1].getMessage()
        assert "max_size=13" in message and "timeout_s=7.5" in message
    finally:
        # Teardown: stop the maintenance cycle and cancel the boot
        # background tasks so the test leaves no pending-task noise.
        try:
            await manager._maintenance_service.stop()
        except Exception:
            pass
        for task in getattr(manager, "_background_tasks", []):
            task.cancel()
        warmup = getattr(manager, "_warmup_task", None)
        if warmup is not None:
            warmup.cancel()
        engine.dispose()
