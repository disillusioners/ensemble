"""Unit tests — the checkpoint-pool knobs reach the saver pool as EXPLICIT
kwargs (plan Track-1 item 5, severed-knob repair D5).

THE BUG (plan D5): ``create_postgres_checkpointer`` used to read
``getattr(config, "limits", None)`` off ``EnsembleConfig`` — a class with
NO ``limits`` field (``daemon/ensemble_config.py``: fields
``database``/``postgres``/``sqlite`` only). Every tier of the house
4-tier ladder therefore silently degraded to the constants defaults
(10 / 5.0), while the boot log implied the ``limits.*`` knobs were
honored.

THE FIX: the knobs are keyword-only parameters resolved by the BOOT
CALLER (``InstanceManager.initialize``) from the FULL YAML ``Config.limits``
— the same explicit-threading shape as the manager engine pool
(``create_postgres_engine``). These tests pin the persistence-level half:

* explicit kwargs reach BOTH pools (psycopg saver pool ``max_size`` /
  ``timeout`` AND the adapter asyncpg pool ``max_size``);
* the keyword-only defaults are the constants (default tier intact for
  callers that hold no full Config — e.g. the migration worker);
* ``get_checkpointer`` forwards the kwargs (the middle hop of the boot
  chain is not dropped);
* the boot log prints the values ACTUALLY APPLIED (log-truthfulness).

Pure unit — no PostgreSQL server is contacted: the psycopg / psycopg_pool
/ asyncpg / langgraph-checkpoint modules are replaced with in-process
fakes and the pools' construction kwargs are captured (the proven pattern
from ``tests/unit/test_persistence_prepare_threshold.py``).
"""
from __future__ import annotations

import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon import constants
from daemon.ensemble_config import EnsembleConfig
from daemon.persistence import create_postgres_checkpointer, get_checkpointer


def _build_fakes(captured: dict) -> dict:
    """In-process fakes for the lazy-import layer of
    ``create_postgres_checkpointer``. Pool construction kwargs are
    captured into ``captured``."""
    fake_saver_instance = MagicMock(name="AsyncPostgresSaver")
    fake_saver_instance.setup = AsyncMock()

    fake_aio_module = MagicMock()
    fake_aio_module.AsyncPostgresSaver = lambda conn: fake_saver_instance

    class _FakeAsyncpg:
        @staticmethod
        async def create_pool(*args, **kwargs):
            captured["asyncpg_args"] = args
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
        # The adapter's retry-proxy wrapper lazily imports the
        # secret-scrub helper; stub it so this test never pulls the
        # SQLModel repositories into the import graph (the real
        # ``daemon.repositories.secrets`` package re-registers its
        # table on a re-import — see the matching stub in
        # test_persistence_prepare_threshold.py).
        "daemon.services.secret_persistence_scrub": MagicMock(),
    }


class TestExplicitKwargsReachThePools:
    @pytest.mark.asyncio
    async def test_explicit_kwargs_reach_both_pools(self, caplog):
        """Custom limits values (as the boot caller resolves them from
        ``Config.limits``) flow into the saver pool's ``max_size`` /
        ``timeout`` AND the adapter asyncpg pool's ``max_size``."""
        captured: dict = {}
        config = EnsembleConfig(database="postgres")

        with patch.dict(sys.modules, _build_fakes(captured)):
            with caplog.at_level(logging.INFO, logger="daemon.persistence"):
                adapter = await create_postgres_checkpointer(
                    config,
                    checkpoint_pool_max_size=13,
                    checkpoint_pool_timeout_s=7.5,
                )

        pool_kwargs = captured["pool_kwargs"]
        assert pool_kwargs["max_size"] == 13, (
            "saver pool max_size must be the EXPLICIT kwarg, not the "
            "severed default 10"
        )
        assert pool_kwargs["timeout"] == 7.5, (
            "saver pool acquisition timeout must be the EXPLICIT kwarg, "
            "not the severed default 5.0"
        )
        assert captured["asyncpg_kwargs"]["max_size"] == 13, (
            "the adapter asyncpg pool shares the max-size knob"
        )
        # The postgres branch really ran (adapter type + pool opened).
        from daemon.checkpoint_adapter import PostgresCheckpointerAdapter

        assert isinstance(adapter, PostgresCheckpointerAdapter)
        assert captured["pool_open_called"] is True

    @pytest.mark.asyncio
    async def test_defaults_are_the_constants(self):
        """Callers that hold no full YAML Config (e.g. the migration
        worker) get the documented default tier — the constants, applied
        exactly as before, by keyword-only default."""
        captured: dict = {}
        config = EnsembleConfig(database="postgres")

        with patch.dict(sys.modules, _build_fakes(captured)):
            await create_postgres_checkpointer(config)

        pool_kwargs = captured["pool_kwargs"]
        assert pool_kwargs["max_size"] == (
            constants.CHECKPOINT_POOL_MAX_SIZE_DEFAULT
        )
        assert pool_kwargs["timeout"] == constants.CHECKPOINT_POOL_TIMEOUT_S_DEFAULT
        assert pool_kwargs["max_size"] == 10
        assert pool_kwargs["timeout"] == 5.0
        assert captured["asyncpg_kwargs"]["max_size"] == (
            constants.CHECKPOINT_POOL_MAX_SIZE_DEFAULT
        )

    @pytest.mark.asyncio
    async def test_get_checkpointer_forwards_the_kwargs(self):
        """The middle hop of the boot chain — ``get_checkpointer`` must
        forward the kwargs to ``create_postgres_checkpointer``, not drop
        them at the dispatch boundary."""
        captured: dict = {}
        config = EnsembleConfig(database="postgres")

        with patch.dict(sys.modules, _build_fakes(captured)):
            await get_checkpointer(
                config,
                checkpoint_pool_max_size=17,
                checkpoint_pool_timeout_s=2.5,
            )

        assert captured["pool_kwargs"]["max_size"] == 17
        assert captured["pool_kwargs"]["timeout"] == 2.5


class TestBootLogTruthfulness:
    @pytest.mark.asyncio
    async def test_boot_log_prints_the_applied_values(self, caplog):
        """The repaired log line prints the values ACTUALLY APPLIED and
        no longer implies the ladder was honored inside this function."""
        captured: dict = {}
        config = EnsembleConfig(database="postgres")

        with patch.dict(sys.modules, _build_fakes(captured)):
            with caplog.at_level(logging.INFO, logger="daemon.persistence"):
                await create_postgres_checkpointer(
                    config,
                    checkpoint_pool_max_size=13,
                    checkpoint_pool_timeout_s=7.5,
                )

        sizing_records = [
            r for r in caplog.records
            if "Checkpoint pool sizing" in r.getMessage()
        ]
        assert sizing_records, "the applied-sizing INFO line must be logged"
        message = sizing_records[-1].getMessage()
        assert "max_size=13" in message
        assert "timeout_s=7.5" in message
        # Truthfulness: the provenance is the explicit kwarg, not the
        # (severed) in-function ladder read.
        assert "explicit kwargs" in message
