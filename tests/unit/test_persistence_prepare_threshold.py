"""Unit test — the saver pool's psycopg3 ``prepare_threshold`` kwarg.

Plan Track-1, item 3: the checkpoint saver pool is built with
``prepare_threshold=None`` — psycopg3 prepared statements are DISABLED,
so the server plans every execution freshly (custom plan) instead of
serving a cached generic plan from the per-connection statement cache.

psycopg3 semantics (documented on ``psycopg.Connection``):

* ``0``  — prepare the query on its FIRST execution. The statement then
  lives in the per-connection statement cache and the server may serve
  it from the cached GENERIC plan once the statement has been executed
  5 times — the cached-generic-plan trap that the read-path audit
  measured as the 8.56 ms-vs-0.064 ms regression class on parameterized
  checkpoint reads.
* ``None`` — prepared statements are DISABLED: every execution is planned
  fresh by the server (custom plan).

HONESTY: the 8.56 ms state was NOT reproducible on prod at audit time
(autovacuum had analyzed; the live plan was an index scan) — the
pathology is EPISODIC, so the flip is PREVENTION, not a repair of an
observable regression.

Pure unit — no PostgreSQL server is contacted: the psycopg / psycopg_pool
/ asyncpg / langgraph-checkpoint modules are replaced with in-process
fakes and the pool's construction kwargs are captured.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.ensemble_config import EnsembleConfig
from daemon.persistence import create_postgres_checkpointer


@pytest.mark.asyncio
async def test_pool_kwargs_disable_prepared_statements():
    """The saver pool's per-connection kwargs carry
    ``prepare_threshold=None`` — prepared statements are DISABLED."""
    captured: dict = {}

    fake_saver_instance = MagicMock(name="AsyncPostgresSaver")
    fake_saver_instance.setup = AsyncMock()

    fake_aio_module = MagicMock()
    fake_aio_module.AsyncPostgresSaver = lambda conn: fake_saver_instance

    class _FakeAsyncpg:
        @staticmethod
        async def create_pool(*args, **kwargs):
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

    config = EnsembleConfig(database="postgres")

    with patch.dict(
        sys.modules,
        {
            "asyncpg": _FakeAsyncpg,
            "psycopg": _FakePsycopg,
            "psycopg.rows": _FakePsycopg.rows,
            "psycopg_pool": _FakePsycopgPoolModule,
            "langgraph.checkpoint.postgres.aio": fake_aio_module,
            # The adapter's retry-proxy wrapper lazily imports the
            # secret-scrub helper; stub it so this test never pulls the
            # SQLModel repositories into the import graph (the real
            # ``daemon.repositories.secrets`` package re-registers its
            # table on a re-import and blows up with
            # "Table 'secrets' is already defined for this MetaData
            # instance" — a pre-existing import-cycle landmine that is
            # irrelevant to the kwarg under test).
            "daemon.services.secret_persistence_scrub": MagicMock(),
        },
    ):
        await create_postgres_checkpointer(config)

    kwargs = captured["pool_kwargs"]
    assert "kwargs" in kwargs, (
        "the pool must carry the per-connection kwargs dict"
    )
    inner = kwargs["kwargs"]
    assert "prepare_threshold" in inner, (
        "prepare_threshold must be set EXPLICITLY — an absent kwarg "
        "falls back to psycopg3's default (5), which prepares the "
        "statement after 5 executions"
    )
    assert inner["prepare_threshold"] is None, (
        "prepare_threshold must be None (prepared statements disabled); "
        f"got {inner['prepare_threshold']!r}"
    )
    assert inner["prepare_threshold"] != 0, (
        "prepare_threshold=0 would keep the server-side statement cache "
        "(and with it the generic-plan switch) in play"
    )
    # The neighbouring per-connection kwargs are unchanged.
    assert inner["autocommit"] is True
    assert inner["row_factory"] is _FakeDictRow
