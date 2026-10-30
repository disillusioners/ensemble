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
  ``prepare_threshold=None`` / ``dict_row`` kwargs (C2: the ORACLE pool
  is provably untouched by this lane).
* **F2-h** — the SHARED ``conn_string``
  (``daemon/persistence.py::_build_pg_connection_string``) does NOT
  carry ``application_name``: the asyncpg adapter pool takes its name
  via asyncpg's OWN ``server_settings`` channel on a DSN built
  SEPARATELY from the shared variable (measured: a shared DSN cannot
  separate the two pools — BASELINE.md:325-338).

**§7.3 RE-BASELINE (2026-10-29 — one deliberate knob change, NOT a
silent mutation; the pin's ORIGINAL INTENT is preserved and, where it
is affected, made EXPLICIT):** the checkpoint lane changed ONE saver-pool
knob — ``prepare_threshold`` — from the lane's ``0`` to ``None``.
**CODE DRIFT** (the pin's oracle is a knob oracle; there is no scanner
involved, so scanner drift is not applicable). Classification table of
one:

* ``daemon/persistence.py:412`` — the saver pool's ``kwargs=`` dict,
  ``"prepare_threshold": None`` — family **F2-j saver-pool knob** —
  **DELIBERATE / DOCUMENTED** — psycopg3 semantics: ``None``
  **DISABLES prepared statements entirely**, so every execution is
  planned FRESH by the server (a custom plan) and the server can never
  serve a cached GENERIC plan. The previous value ``0`` is NOT psycopg3's
  default of ``5``: ``0`` means "prepare IMMEDIATELY on the first
  execution", which put the statement in the per-connection statement
  cache and let PG serve a cached generic plan after the 5-execution
  threshold — the mechanism the read-path audit attributed the
  8.56 ms-vs-0.064 ms regression class to. Provenance: commit
  ``dcfcbba82`` ("perf(checkpoint): disable psycopg3 prepared
  statements; ANALYZE after destructive prune", checkpoint-read-path
  plan Track-1 item 3) and the merge ``e356d3657`` (EXPLAIN parity:
  0 non-timing EXPLAIN leaf differences across 10 EXPLAINs per leg,
  both arms on the SAME plan; 25-round latency matrix). Measured cost:
  **+0.229…+0.254 ms per read, 95 % CI [+0.220, +0.261]**, 4
  repetitions with varied leg order — a load-dependent parse-time
  trade for plan freshness. HONESTY: the 8.56 ms state was NOT
  reproducible on prod at audit time (autovacuum had analyzed; the live
  plan was an index scan) ⇒ this is **PREVENTION, not a repair of an
  observable regression**.

**The pin is NOT weakened into a no-op.** Its original intent — *the
saver-pool knobs are not SILENTLY MUTATED by unrelated code* — is
carried forward STRICTLY:

* every knob the lane pinned keeps its lane-pinned VALUE (``open``,
  ``min_size``, ``max_size`` / ``timeout`` from the A4 sizing ladder,
  the ``check_connection`` probe, ``max_lifetime``, ``max_idle``,
  ``reconnect_timeout``, ``autocommit``, ``row_factory``);
* the knob **SET** is now asserted EXACTLY
  (:data:`SAVER_POOL_KNOB_SET`) — a **silently ADDED or REMOVED** knob
  is a loud failure (this leg is NEW: the lane's oracle asserted the
  values but never the key set, so a new pool knob could have appeared
  unnoticed);
* the connection-``kwargs`` dict is still asserted as an EXACT dict
  equality, so a silently added/removed connection kwarg is RED too;
* the ONE deliberate divergence is asserted **explicitly and by name**
  (:data:`SAVER_PREPARE_THRESHOLD`), not deleted and not folded into a
  looser assertion.

Collected count UNCHANGED (6) — the re-baseline EXTENDS the existing
oracle in place; no test was added, removed, or renamed.

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

#: The saver pool's EXACT constructor-knob SET (the F2-j oracle's
#: original intent: the saver-pool knobs are not SILENTLY MUTATED by
#: unrelated code). The lane's oracle asserted the VALUES but never the
#: KEY SET ⇒ a silently ADDED (or REMOVED) pool knob could have
#: appeared unnoticed. This leg closes that hole: the set is exact, so
#: a new knob is a loud failure. Measured at this tree: 10 keys.
SAVER_POOL_KNOB_SET = frozenset(
    {
        "check",
        "conninfo",
        "kwargs",
        "max_idle",
        "max_lifetime",
        "max_size",
        "min_size",
        "open",
        "reconnect_timeout",
        "timeout",
    }
)

#: The ONE deliberate, DOCUMENTED divergence from the db-pool lane's
#: original F2-j oracle (audit-method.md §7.3, code-drift path) — the
#: saver pool's ``prepare_threshold`` knob.
#:
#: ``None`` DISABLES psycopg3 prepared statements entirely (every
#: execution is planned FRESH by the server — a custom plan; the server
#: can never serve a cached GENERIC plan). The lane's oracle pinned
#: ``0``, which means "prepare IMMEDIATELY on the first execution" and
#: therefore leaves the statement in the per-connection statement cache
#: where PG may serve a cached generic plan after the 5-execution
#: threshold. Provenance: ``dcfcbba82`` (checkpoint-read-path plan
#: Track-1 item 3) + the merge ``e356d3657`` (EXPLAIN parity + the
#: 25-round latency matrix); measured cost +0.229…+0.254 ms per read,
#: 95 % CI [+0.220, +0.261]. See the module docstring's §7.3 record for
#: the full classification.
#:
#: This constant EXISTS so the deliberate value is asserted BY NAME —
#: the change is explicit and documented, never deleted from the pin
#: and never weakened into a looser assertion.
SAVER_PREPARE_THRESHOLD = None

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
        # The knob SET is exact — a silently ADDED or REMOVED pool knob
        # is a loud failure (the original intent of this oracle: the
        # saver-pool knobs are not SILENTLY MUTATED by unrelated code).
        assert set(saver_kwargs) == set(SAVER_POOL_KNOB_SET), (
            "F2-j: the saver pool's knob SET drifted — expected exactly "
            f"{sorted(SAVER_POOL_KNOB_SET)}, observed "
            f"{sorted(saver_kwargs)}. A knob added to (or removed from) "
            "the P2 oracle pool is a SILENT mutation unless it is "
            "deliberate, audited, and re-baselined per "
            "audit-method.md §7.3."
        )
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
        # The ONE deliberate divergence (documented in the module
        # docstring's §7.3 record) is asserted BY NAME — explicitly,
        # not deleted and not weakened into a looser assertion.
        assert (
            saver_kwargs["kwargs"]["prepare_threshold"]
            is SAVER_PREPARE_THRESHOLD
        ), (
            "F2-j: the saver pool's prepare_threshold is NOT the "
            "deliberate, documented value (None — psycopg3 prepared "
            "statements disabled; commit dcfcbba82, plan Track-1 item "
            "3). Any OTHER value is an un-audited mutation — re-audit "
            "and re-baseline per audit-method.md §7.3."
        )
        assert saver_kwargs["kwargs"] == {
            "autocommit": True,
            "prepare_threshold": SAVER_PREPARE_THRESHOLD,
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
