"""F2-f pins — the repair engine's isolation + explicit reset (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F2.5 / F2.2 and §5.2; phase2-plan
T2.6 / AC16 / D-17 / D-19.

What these pins assert:

* **F2-f** — the SHARED ``PG_SESSION_CONNECT_ARGS``
  (``daemon/repositories/factory.py``) carries NO ``application_name``
  and NO timeout clauses (the D-17 guard): it is interpolated LAST
  into the repair engine's options string, so extending it would
  silently retune the repair engine's ``statement_timeout`` 60 s → 30 s
  (PG's last ``-c`` wins) AND mis-attribute it (measured:
  ``repair_engine_reports_manager_name: true``, BASELINE.md T3 /
  ``raw/t3_pool_attribution.out:117``).
* The repair engine's complete knob set, pinned value-by-value against
  its own constants (AC16): ``pool_size=2``, ``max_overflow=3``,
  ``pool_pre_ping=True``, ``pool_recycle=3600``, ``pool_timeout=10``,
  plus the lane's ONLY knob addition — an explicit
  ``pool_reset_on_return="rollback"`` (D-19).
* The ``-c options`` literal is BYTE-IDENTICAL to its pre-F2 form
  (statement 60000 / lock 10000 / idle-in-txn 60000 / UTC — nothing
  appended, D-17), and the engine's ``application_name=ensemble-repair``
  rides the CONNINFO channel (its own ``connect_args`` key — D-49),
  NEVER the options string.

Plantable mutations:

* extend the shared constant with an ``application_name`` or a timeout
  ⇒ the rendered options string changes ⇒ RED (F2-f);
* drop ``pool_reset_on_return`` from ``_build_repair_engine`` ⇒ RED;
* append ``-c application_name=`` to the options literal (the weaker
  channel) ⇒ the channel assertion REDs;
* change any other knob ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_repair_engine_isolation.py --tb=short -q
"""
from __future__ import annotations

import daemon.tools.ens_db_tools as ens_db_tools
from daemon.repositories.factory import PG_SESSION_CONNECT_ARGS
from daemon.tools.ens_db_tools import (
    REPAIR_ENGINE_APPLICATION_NAME,
    REPAIR_IDLE_IN_TXN_TIMEOUT_MS,
    REPAIR_LOCK_TIMEOUT_MS,
    REPAIR_POOL_MAX_OVERFLOW,
    REPAIR_POOL_RECYCLE_S,
    REPAIR_POOL_SIZE,
    REPAIR_POOL_TIMEOUT_S,
    REPAIR_STATEMENT_TIMEOUT_MS,
)
from sqlalchemy.engine import URL

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 6


class _FakeSharedEngine:
    """The minimal shared-engine surface ``_build_repair_engine`` reads
    (``.url`` — a real SQLAlchemy URL object)."""

    def __init__(self, url: URL) -> None:
        self.url = url


def _capture_repair_kwargs(monkeypatch) -> dict:
    """Build the repair engine with ``create_engine`` spied out."""
    captured: dict = {}

    def spy(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeSharedEngine(args[0])

    monkeypatch.setattr(ens_db_tools, "create_engine", spy)
    ens_db_tools._REPAIR_ENGINE_CACHE.clear()
    shared = _FakeSharedEngine(
        URL.create(
            drivername="postgresql+psycopg",
            username="u",
            password="p",
            host="127.0.0.1",
            port=15432,
            database="ensemble_test",
        )
    )
    ens_db_tools._build_repair_engine(shared)
    return captured


def _expected_options_literal() -> str:
    """The BYTE-IDENTICAL pre-F2 options literal, reconstructed from the
    module's own constants in the same f-string shape (D-17)."""
    return (
        f"-c statement_timeout={REPAIR_STATEMENT_TIMEOUT_MS} "
        f"-c lock_timeout={REPAIR_LOCK_TIMEOUT_MS} "
        f"-c idle_in_transaction_session_timeout="
        f"{REPAIR_IDLE_IN_TXN_TIMEOUT_MS} "
        f"{PG_SESSION_CONNECT_ARGS['options']}"
    )


class TestF2fSharedConstantCarriesNothing:
    """F2-f — the D-17 guard on the SHARED connect-args constant."""

    def test_shared_constant_carries_no_application_name(self):
        assert "application_name" not in PG_SESSION_CONNECT_ARGS
        assert "application_name" not in PG_SESSION_CONNECT_ARGS.get(
            "options", ""
        )

    def test_shared_constant_carries_no_timeout_clauses(self):
        options = PG_SESSION_CONNECT_ARGS.get("options", "")
        for clause in (
            "statement_timeout",
            "lock_timeout",
            "idle_in_transaction_session_timeout",
        ):
            assert clause not in options, (
                f"F2-f: the shared constant must not carry {clause} — "
                "it is interpolated LAST into the repair engine's "
                "options string and would silently retune it (D-17)"
            )

    def test_shared_constant_shape_is_exactly_the_utc_clause(self):
        assert PG_SESSION_CONNECT_ARGS == {"options": "-c timezone=UTC"}


class TestRepairEngineKnobSet:
    """AC16 — the repair engine is pinned exactly: ``pool_reset_on_return``
    is the ONLY knob addition and the conninfo name is the ONLY
    connect-args change."""

    def test_complete_knob_set(self, monkeypatch):
        captured = _capture_repair_kwargs(monkeypatch)
        kwargs = captured["kwargs"]
        assert kwargs["pool_size"] == REPAIR_POOL_SIZE == 2
        assert kwargs["max_overflow"] == REPAIR_POOL_MAX_OVERFLOW == 3
        assert kwargs["pool_pre_ping"] is True
        assert kwargs["pool_recycle"] == REPAIR_POOL_RECYCLE_S == 3600
        assert kwargs["pool_timeout"] == REPAIR_POOL_TIMEOUT_S == 10
        assert kwargs["pool_reset_on_return"] == "rollback"

    def test_options_literal_is_byte_identical(self, monkeypatch):
        captured = _capture_repair_kwargs(monkeypatch)
        options = captured["kwargs"]["connect_args"]["options"]
        assert options == _expected_options_literal()
        # ...and nothing was appended by this lane (D-17).
        assert "application_name" not in options

    def test_application_name_rides_the_conninfo_channel(self, monkeypatch):
        """D-49 — the name is its OWN connect_args key (a psycopg3
        conninfo parameter merged from connect_args kwargs), NEVER a
        ``-c`` inside the options string (the weaker, last-wins
        channel)."""
        captured = _capture_repair_kwargs(monkeypatch)
        connect_args = captured["kwargs"]["connect_args"]
        assert (
            connect_args.get("application_name")
            == REPAIR_ENGINE_APPLICATION_NAME
            == "ensemble-repair"
        )
        assert "application_name" not in connect_args["options"]
