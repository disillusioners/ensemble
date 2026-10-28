"""F2-j / F2-h pins — P2 oracle immutability + the asyncpg DSN split.

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F2.6 / F2.10 and §5.2; phase2-plan
T2.7 + T2.10 / AC18 / AC35 / D-36 / D-49.

What these pins assert:

* **F2-j** — the P2 psycopg SAVER pool's construction
  (``daemon/persistence.py``, ``create_postgres_checkpointer``) is
  byte-identical to its pre-lane form: ``conninfo=<the SHARED
  conn_string>``, ``open=False``, ``min_size=1``, ``max_size`` /
  ``timeout`` from the A4 sizing ladder, the ``check_connection``
  liveness probe, ``max_lifetime=3600``, ``max_idle=600``,
  ``reconnect_timeout=300``, and the ``autocommit`` /
  ``prepare_threshold=0`` / ``dict_row`` kwargs (C2: the ORACLE pool
  is provably untouched by this lane).
* **F2-h** — the SHARED ``conn_string``
  (``daemon/persistence.py::_build_pg_connection_string``) does NOT
  carry ``application_name``: the asyncpg adapter pool takes its name
  via asyncpg's OWN ``server_settings`` channel on a DSN built
  SEPARATELY from the shared variable (measured: a shared DSN cannot
  separate the two pools — BASELINE.md:325-338).

Mechanism: ``create_postgres_checkpointer`` is driven with FAKE pool
modules injected into ``sys.modules`` (the function imports
``psycopg_pool`` / ``langgraph.checkpoint.postgres.aio`` /
``psycopg.rows`` / ``psycopg`` / ``asyncpg`` lazily at call time), so
the REAL pool-construction code paths run and their kwargs are
captured without any network or DB. ``resolve_psycopg_pool`` (the
readiness oracle resolver) is additionally duck-type-proven on a
pool-shaped fake.

Plantable mutations:

* change any knob of the P2 saver pool (``min_size``, ``max_lifetime``,
  the kwargs dict, …) ⇒ the captured kwargs no longer match ⇒ RED
  (F2-j);
* add ``application_name`` to the shared ``conn_string`` (or feed the
  shared variable to ``asyncpg.create_pool``) ⇒ the P2-conninfo /
  separate-DSN assertions RED (F2-h).

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_p2_immutability.py --tb=short -q
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import daemon.persistence as persistence_mod
from daemon.constants import (
    CHECKPOINT_POOL_MAX_SIZE_DEFAULT,
    CHECKPOINT_POOL_TIMEOUT_S_DEFAULT,
)
from daemon.ensemble_config import EnsembleConfig, PostgresConfig
from daemon.persistence import CHECKPOINT_ADAPTER_APPLICATION_NAME
from daemon.services.readiness import resolve_psycopg_pool

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 6

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PERSISTENCE_SRC = (
    _REPO_ROOT / "daemon" / "persistence.py"
).read_text()

_PG_ENV_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_URL",
)


class _FakeAsyncConnectionPool:
    """Records the saver-pool construction kwargs."""

    last_kwargs: dict | None = None

    @staticmethod
    def check_connection(conn):  # pragma: no cover — never called
        return conn.execute("")

    def __init__(self, **kwargs) -> None:
        _FakeAsyncConnectionPool.last_kwargs = dict(kwargs)

    async def open(self) -> None:  # pragma: no cover — no-op fake
        return None

    async def close(self) -> None:  # pragma: no cover — no-op fake
        return None


class _FakeAsyncpgPool:
    """The object ``asyncpg.create_pool`` returns (attribute-tolerant)."""


class _FakeAsyncpgModule(types.ModuleType):
    """Records ``create_pool`` args/kwargs."""

    def __init__(self) -> None:
        super().__init__("asyncpg")
        self.create_pool_calls: list[dict] = []

    async def create_pool(self, dsn=None, **kwargs):
        self.create_pool_calls.append({"dsn": dsn, "kwargs": kwargs})
        return _FakeAsyncpgPool()


def _install_fakes(monkeypatch) -> tuple[_FakeAsyncpgModule, type]:
    """Inject the fake pool modules; return (asyncpg fake, saver class)."""
    fake_asyncpg = _FakeAsyncpgModule()

    pool_mod = types.ModuleType("psycopg_pool")
    pool_mod.AsyncConnectionPool = _FakeAsyncConnectionPool

    aio_mod = types.ModuleType("langgraph.checkpoint.postgres.aio")

    class FakeAsyncPostgresSaver:
        def __init__(self, conn) -> None:
            self.conn = conn

        async def setup(self) -> None:  # pragma: no cover — no-op fake
            return None

    aio_mod.AsyncPostgresSaver = FakeAsyncPostgresSaver

    rows_mod = types.ModuleType("psycopg.rows")
    rows_mod.dict_row = "dict_row-fake"
    psycopg_mod = types.ModuleType("psycopg")

    monkeypatch.setitem(sys.modules, "asyncpg", fake_asyncpg)
    monkeypatch.setitem(sys.modules, "psycopg_pool", pool_mod)
    monkeypatch.setitem(
        sys.modules, "langgraph.checkpoint.postgres.aio", aio_mod
    )
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows_mod)
    monkeypatch.setitem(sys.modules, "psycopg", psycopg_mod)
    return fake_asyncpg, FakeAsyncPostgresSaver


def _drive_checkpointer(monkeypatch) -> tuple[dict, _FakeAsyncpgModule]:
    fake_asyncpg, _ = _install_fakes(monkeypatch)
    for var in _PG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    config = EnsembleConfig(
        database="postgres",
        postgres=PostgresConfig(
            host="127.0.0.1",
            port=15432,
            db="ensemble_test",
            user="ensemble",
            password="ensemble_dev",
        ),
    )
    adapter = asyncio.run(persistence_mod.create_postgres_checkpointer(config))
    assert adapter is not None
    assert _FakeAsyncConnectionPool.last_kwargs is not None
    return _FakeAsyncConnectionPool.last_kwargs, fake_asyncpg


class TestF2jSaverPoolImmutability:
    """F2-j — the P2 ORACLE pool's construction is byte-identical."""

    def test_saver_pool_knobs_are_unchanged(self, monkeypatch):
        saver_kwargs, _ = _drive_checkpointer(monkeypatch)
        assert saver_kwargs["open"] is False
        assert saver_kwargs["min_size"] == 1
        assert saver_kwargs["max_size"] == CHECKPOINT_POOL_MAX_SIZE_DEFAULT
        assert saver_kwargs["timeout"] == CHECKPOINT_POOL_TIMEOUT_S_DEFAULT
        assert (
            saver_kwargs["check"] is _FakeAsyncConnectionPool.check_connection
        )
        assert saver_kwargs["check"].__name__ == "check_connection"
        assert saver_kwargs["max_lifetime"] == 3600
        assert saver_kwargs["max_idle"] == 600
        assert saver_kwargs["reconnect_timeout"] == 300
        assert saver_kwargs["kwargs"] == {
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": "dict_row-fake",
        }

    def test_saver_pool_conninfo_is_the_shared_conn_string(
        self, monkeypatch
    ):
        """The ORACLE pool's conninfo is the SHARED ``conn_string`` —
        and that string does NOT carry ``application_name`` (the
        asyncpg pool's name rides a separate DSN + server_settings)."""
        saver_kwargs, _ = _drive_checkpointer(monkeypatch)
        conninfo = saver_kwargs["conninfo"]
        assert conninfo == (
            "postgresql://ensemble:ensemble_dev@127.0.0.1:15432/"
            "ensemble_test"
        )
        assert "application_name" not in conninfo

    def test_resolve_psycopg_pool_still_duck_types_the_pool(self):
        """T2.7 — the readiness ORACLE resolver still resolves a
        pool-shaped ``conn`` (``get_stats`` + ``min_size``) and rejects
        a connection-shaped one (no silent degradation to the S9
        streak arm)."""
        pool_like = SimpleNamespace(get_stats=lambda: {}, min_size=1)
        saver_like = SimpleNamespace(conn=pool_like)
        checkpointer_like = SimpleNamespace(raw_saver=saver_like)
        assert resolve_psycopg_pool(checkpointer_like) is pool_like
        conn_like = SimpleNamespace()
        assert (
            resolve_psycopg_pool(
                SimpleNamespace(raw_saver=SimpleNamespace(conn=conn_like))
            )
            is None
        )


class TestF2hAsyncpgSeparateDsn:
    """F2-h / T2.10 — the asyncpg attribution lands via server_settings
    on a SEPARATELY-built DSN; the shared conn_string is untouched."""

    def test_asyncpg_pool_carries_server_settings_application_name(
        self, monkeypatch
    ):
        _, fake_asyncpg = _drive_checkpointer(monkeypatch)
        assert len(fake_asyncpg.create_pool_calls) == 1
        call = fake_asyncpg.create_pool_calls[0]
        assert call["kwargs"].get("server_settings") == {
            "application_name": CHECKPOINT_ADAPTER_APPLICATION_NAME
        }
        assert CHECKPOINT_ADAPTER_APPLICATION_NAME == (
            "ensemble-checkpoint-adapter"
        )
        assert call["kwargs"].get("min_size") == 1
        assert call["kwargs"].get("max_size") == (
            CHECKPOINT_POOL_MAX_SIZE_DEFAULT
        )

    def test_shared_conn_string_builder_carries_no_application_name(self):
        source = _PERSISTENCE_SRC
        builder = source.split("def _build_pg_connection_string", 1)[
            1
        ].split("def ", 1)[0]
        assert "application_name" not in builder, (
            "F2-h: the shared conn_string builder must NOT carry "
            "application_name — it also feeds the P2 psycopg ORACLE "
            "whose conninfo is pinned byte-identical (T2.7/AC18)"
        )

    def test_create_pool_argument_is_the_separate_adapter_dsn(self):
        """The asyncpg pool's DSN is built SEPARATELY (a distinct
        variable), never the shared ``conn_string`` identifier — a
        shared DSN cannot separate the two pools (measured)."""
        assert "adapter_pool_dsn = _build_pg_connection_string" in (
            _PERSISTENCE_SRC
        ), "F2-h: the adapter DSN must be built by its own expression"
        pool_call = _PERSISTENCE_SRC.split("asyncpg.create_pool(", 1)[1]
        call_head = pool_call.split(")", 1)[0]
        assert "adapter_pool_dsn" in call_head
        assert "conn_string" not in call_head, (
            "F2-h: asyncpg.create_pool must not receive the shared "
            "conn_string variable"
        )
