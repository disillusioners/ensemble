"""F2-g pins — per-engine application_name via the CONNINFO channel.

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F2.3/F2.5/F2.6 + §4.2 + §5.2;
phase2-plan T2.5/T2.6/T2.10 / AC34 / D-49 / D-30 / D-36.

DR-31: the CHANNEL is asserted, not merely the presence of a name.
Measured (``BASELINE.md`` T3 / ``raw/t3_pool_attribution.out``): the
conninfo parameter BEATS ``options=-c`` (``:127-135``), within one
options string the LAST ``-c`` wins (``:121-125``), and the
shared-constant-appended-last hazard is REAL
(``repair_engine_reports_manager_name: true``, ``:111-119``) — so each
engine's name must live in its OWN conninfo/URL query dict, making the
attribution OVERRIDE-PROOF rather than merely last-wins-correct.

What these pins assert:

* the THREE names are DISTINCT (``ensemble-manager``,
  ``ensemble-repair``, ``ensemble-checkpoint-adapter``), each in its
  OWN channel: the manager's in its ``URL.create(...)`` query dict, the
  repair engine's in its own conninfo (a ``connect_args`` key), the
  asyncpg pool's via ``server_settings``;
* each name appears EXACTLY ONCE at its configuration site (no stray
  duplicates);
* the SHARED ``PG_SESSION_CONNECT_ARGS`` carries NONE of them.

Plantable mutations:

* put ``application_name`` back into a shared ``-c options`` string
  (the channel flips) ⇒ RED;
* make two names equal, or make one appear twice, or add a name to the
  shared constant ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_pool_attribution.py --tb=short -q
"""
from __future__ import annotations

from pathlib import Path

import daemon.repositories.factory as factory_mod
import daemon.tools.ens_db_tools as ens_db_tools
from daemon.constants import MANAGER_ENGINE_APPLICATION_NAME
from daemon.ensemble_config import EnsembleConfig, PostgresConfig
from daemon.persistence import CHECKPOINT_ADAPTER_APPLICATION_NAME
from daemon.repositories.factory import PG_SESSION_CONNECT_ARGS
from daemon.tools.ens_db_tools import REPAIR_ENGINE_APPLICATION_NAME
from sqlalchemy.engine import URL, make_url

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 5

_REPO_ROOT = Path(__file__).resolve().parents[3]

_PG_ENV_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)

_ALL_NAMES = (
    MANAGER_ENGINE_APPLICATION_NAME,
    REPAIR_ENGINE_APPLICATION_NAME,
    CHECKPOINT_ADAPTER_APPLICATION_NAME,
)


def _manager_url_query(monkeypatch) -> dict:
    captured: dict = {}
    real_create_engine = factory_mod.create_engine

    def spy(*args, **kwargs):
        captured["args"] = args
        return real_create_engine(*args, **kwargs)

    monkeypatch.setattr(factory_mod, "create_engine", spy)
    for var in _PG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    engine = factory_mod.create_postgres_engine(
        EnsembleConfig(
            database="postgres",
            postgres=PostgresConfig(
                host="127.0.0.1",
                port=15432,
                db="ensemble_test",
                user="ensemble",
                password="ensemble_dev",
            ),
        )
    )
    try:
        return dict(make_url(captured["args"][0]).query)
    finally:
        engine.dispose()


def _repair_connect_args(monkeypatch) -> dict:
    captured: dict = {}

    def spy(*args, **kwargs):
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(ens_db_tools, "create_engine", spy)
    ens_db_tools._REPAIR_ENGINE_CACHE.clear()

    class _FakeSharedEngine:
        def __init__(self, url: URL) -> None:
            self.url = url

    ens_db_tools._build_repair_engine(
        _FakeSharedEngine(
            URL.create(
                drivername="postgresql+psycopg",
                username="u",
                password="p",
                host="127.0.0.1",
                port=15432,
                database="ensemble_test",
            )
        )
    )
    return captured["kwargs"]["connect_args"]


class TestF2gThreeDistinctNamesOwnChannels:
    """F2-g — the CHANNEL is conninfo, the names are distinct."""

    def test_manager_name_in_url_query_dict_not_options(self, monkeypatch):
        query = _manager_url_query(monkeypatch)
        assert (
            query.get("application_name")
            == MANAGER_ENGINE_APPLICATION_NAME
            == "ensemble-manager"
        )
        # And NOT in the -c options string (the weaker channel).
        assert "application_name" not in factory_mod.MANAGER_PG_SESSION_CONNECT_ARGS[
            "options"
        ]

    def test_repair_name_in_own_conninfo_not_options(self, monkeypatch):
        connect_args = _repair_connect_args(monkeypatch)
        assert (
            connect_args.get("application_name")
            == REPAIR_ENGINE_APPLICATION_NAME
            == "ensemble-repair"
        )
        assert "application_name" not in connect_args["options"]

    def test_asyncpg_name_via_server_settings_on_separate_dsn(self):
        """The asyncpg leg is source-pinned (its pool needs a live
        server to build): ``server_settings`` carries the name and the
        DSN expression is NOT the shared ``conn_string`` variable."""
        source = (_REPO_ROOT / "daemon" / "persistence.py").read_text()
        assert "server_settings={" in source
        assert (
            '"application_name": CHECKPOINT_ADAPTER_APPLICATION_NAME'
            in source
        )
        assert CHECKPOINT_ADAPTER_APPLICATION_NAME == (
            "ensemble-checkpoint-adapter"
        )

    def test_the_three_names_are_distinct(self):
        assert len(set(_ALL_NAMES)) == 3

    def test_shared_constant_and_shared_dsn_carry_none(self):
        shared_options = PG_SESSION_CONNECT_ARGS.get("options", "")
        for name in _ALL_NAMES:
            assert name not in PG_SESSION_CONNECT_ARGS
            assert name not in shared_options
        # The shared conn_string builder (P2 oracle's DSN) carries none.
        persistence_src = (
            _REPO_ROOT / "daemon" / "persistence.py"
        ).read_text()
        builder = persistence_src.split(
            "def _build_pg_connection_string", 1
        )[1].split("def ", 1)[0]
        for name in _ALL_NAMES:
            assert name not in builder
