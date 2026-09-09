"""8th test file (W1-P2, task 2.10 enumeration):

This file carries the pinned assertions the spec marks as
"remaining" beyond the 7 enumerated (a)-(g). The spec says:

    > The 8th file: enumerate-by-grep the spec for any additional
    > test path; if only 7 are named, author the 8th to carry the
    > remaining pinned assertions — kill-switch flag-OFF byte-
    > identical (no-op response) pin AND R13 ensemble_prod-never-in-
    > ConnectionPoolManager standing guard.

Spec acceptance line for the kill-switch:

    > Kill-switch flag-OFF returns byte-identical (no-op response)

Spec acceptance line for R13:

    > NEVER register ``ensemble_prod`` in ``ConnectionPoolManager``
    > (Risk R13 — R-13 standing guard)

Two acceptance lines, one test file. The file-list closure for
W1-P2 test files is documented in the coder's report.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from daemon.tools.ens_db_tools import (
    KILL_SWITCH_ENV,
    _resolve_repair_enabled,
    create_ens_db_tools,
)
from daemon.tools._tool_registry import CATEGORY_MODULES


# ── Part 1: kill-switch flag-OFF byte-identical (no-op response) ────────────


class TestRepairKillSwitchFlagOffByteIdentical:
    """``ENSEMBLE_REPAIR_ENABLED=0`` (or ``=false``/``=off``/``=no``)
    must return a BYTE-IDENTICAL no-op response from
    ``ens_db_repair_execute`` — regardless of the supplied SQL,
    nonce, or confirm flag.

    The contract ("kill-switch OFF = byte-identical regression test")
    is the load-bearing guarantee that the kill-switch is a
    SAFE-DEFAULT short-circuit, not an exception path. Pin test
    per repo convention.

    Also: flag-OFF must short-circuit BEFORE the input-validation
    gates — so even an obviously-bad SQL (e.g. ``*.sql`` filename,
    non-idempotent DML, self-surgery) returns the same no-op. This
    proves the kill-switch is the first check, not an afterthought
    appended at the end.
    """

    @pytest.fixture
    def engine(self, tmp_path: Path) -> Engine:
        db_path = tmp_path / "ens_db_kswitch.db"
        eng = create_engine(
            f"sqlite:///{db_path}",
            poolclass=NullPool,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(eng, "connect")
        def _set_pragma(dbapi_conn, connection_record):  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()
        return eng

    @pytest.fixture(autouse=True)
    def _disable_repair(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default: kill-switch OFF for every test in this class."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "0")

    @pytest.mark.asyncio
    async def test_killswitch_off_returns_byte_identical_noop(
        self, engine: Engine
    ) -> None:
        """Any invocation while the kill-switch is OFF returns the
        SAME string — byte-identical (per repo convention)."""
        mgr = MagicMock()
        mgr.engine = engine
        tools = create_ens_db_tools(mgr, "inst-ks", agent_id="maintenancer")
        repair_tool = next(t for t in tools if t.name == "ens_db_repair_execute")

        # Two different inputs that differ in every dimension.
        out_a = await repair_tool.ainvoke({
            "sql": "UPDATE foo SET n = 1 WHERE id = 1",
            "target_label": "foo:noop-a",
            "dry_run": True,
        })
        out_b = await repair_tool.ainvoke({
            "sql": "DROP TABLE bar",
            "target_label": "bar:noop-b",
            "dry_run": False,
            "confirm": True,
            "nonce": "CONFIRM-XXXXXXXX",
        })

        assert out_a == out_b, (
            f"Kill-switch OFF responses must be byte-identical; "
            f"out_a={out_a!r} vs out_b={out_b!r}"
        )
        # The message names the kill-switch explicitly so operators
        # can see WHY the tool is a no-op.
        assert KILL_SWITCH_ENV in out_a
        assert "kill-switch OFF" in out_a
        assert "No-op response" in out_a
        # Exact-string pin (repo convention): relative equality alone
        # stays green if the no-op message is ever reworded — both
        # outputs change together. Pin the LITERAL so any drift in the
        # no-op wording (or the KILL_SWITCH_ENV value it embeds) fails
        # loudly here.
        assert out_a == (
            "ens_db_repair_execute: kill-switch OFF "
            "(env ENSEMBLE_REPAIR_ENABLED=0). No-op response per "
            "kill-switch flag-OFF byte-identical contract."
        ), f"kill-switch OFF no-op literal drifted: {out_a!r}"

    @pytest.mark.parametrize(
        "bad_sql",
        [
            # *.sql filename (migration-runner trap)
            "SELECT * FROM repair_data.sql",
            # Non-idempotent DML
            "INSERT INTO foo (id) VALUES (gen_random_uuid())",
            # Self-surgery
            "UPDATE foo SET x = 1 WHERE id = 'inst-ks'",
            # Empty SQL
            "",
        ],
    )
    @pytest.mark.asyncio
    async def test_killswitch_off_short_circuits_before_validation(
        self, engine: Engine, bad_sql: str
    ) -> None:
        """The kill-switch is the FIRST gate — even bad inputs that
        would normally be refused get the byte-identical no-op."""
        mgr = MagicMock()
        mgr.engine = engine
        tools = create_ens_db_tools(mgr, "inst-ks", agent_id="maintenancer")
        repair_tool = next(t for t in tools if t.name == "ens_db_repair_execute")

        out = await repair_tool.ainvoke({
            "sql": bad_sql,
            "target_label": "any",
            "dry_run": True,
        })

        # The kill-switch short-circuit — NOT the input-validation
        # error path.
        assert "kill-switch OFF" in out, (
            f"Kill-switch must short-circuit BEFORE validation; "
            f"got: {out[:200]}"
        )
        # None of the validation-error tokens appear.
        assert "non-idempotent-DML" not in out
        assert "migration-runner-trap" not in out
        assert "self-surgery-refused" not in out


class TestRepairKillSwitchFlagOnExecutes:
    """Kill-switch flag-ON path runs the real repair tool — pin test
    per the kill-switch flag-ON coverage convention.

    This is the dual of the flag-OFF byte-identical pin: with the
    switch ON, the tool runs through the REAL input-validation +
    audit path. (Same as the other tests in this PR's suite —
    ``test_ens_db_repair_audit.py`` etc. — exercise the ON path.)
    """

    @pytest.fixture
    def engine(self, tmp_path: Path) -> Engine:
        db_path = tmp_path / "ens_db_kswitch_on.db"
        eng = create_engine(
            f"sqlite:///{db_path}",
            poolclass=NullPool,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(eng, "connect")
        def _set_pragma(dbapi_conn, connection_record):  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()
        return eng

    @pytest.mark.asyncio
    async def test_killswitch_on_runs_real_path(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flag-ON: the tool does NOT return the no-op; it proceeds
        past the kill-switch gate and runs validation. We assert
        it reaches the validation step (a non-idempotent DML is
        then refused — but the refusal token comes from the
        validator, NOT the kill-switch).
        """
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        mgr = MagicMock()
        mgr.engine = engine
        tools = create_ens_db_tools(mgr, "inst-kson", agent_id="maintenancer")
        repair_tool = next(t for t in tools if t.name == "ens_db_repair_execute")

        out = await repair_tool.ainvoke({
            "sql": "INSERT INTO foo (id) VALUES (random())",
            "target_label": "foo:nonidemp",
            "dry_run": True,
        })

        # Kill-switch did NOT short-circuit.
        assert "kill-switch OFF" not in out
        # Validation DID refuse — but with the validation token, not
        # the kill-switch token.
        assert "non-idempotent-DML" in out


# ── Part 2: R13 standing guard (ensemble_prod never in ConnectionPoolManager) ─


class TestEnsembleProdNeverInConnectionPoolManager:
    """R13 standing guard: ``ensemble_prod`` must NEVER be registered
    into :class:`ConnectionPoolManager`.

    The ``db`` category resolves against user-registered external
    connections (architect §3.1, §7.5). A synthetic ``ensemble_prod``
    registration would make the daemon's own DB SELECT-able by every
    ``db``-category holder — bypassing the ``ens-db`` exclusivity
    this whole plan exists to build.

    The pin: the ``ens_db_tools`` module must construct its dedicated
    repair engine directly from ``manager.engine.url`` via
    ``create_engine(...)`` — NOT through ``ConnectionPoolManager``.
    The repair engine is a standalone SQLAlchemy Engine, separate
    from any pool in the manager.

    Implementation pin: the CATEGORY_MODULES entry for ``ens-db``
    points to ``daemon.tools.ens_db_tools`` — the module that owns
    the dedicated-engine factory. If a future maintainer moves
    repair-engine construction into ``ConnectionPoolManager``, the
    exclusivity pin is at risk.
    """

    def test_category_modules_ens_db_points_to_standalone_module(self) -> None:
        """``CATEGORY_MODULES["ens-db"]`` must point to the standalone
        ``ens_db_tools`` module — NOT to ``db_tools`` (which is the
        user-facing external-connection tool surface)."""
        assert CATEGORY_MODULES["ens-db"] == "daemon.tools.ens_db_tools"

    def test_ens_db_module_does_not_import_connection_pool_manager(self) -> None:
        """Static check: ``ens_db_tools.py`` must NOT import
        :class:`ConnectionPoolManager`. If a future maintainer routes
        the repair engine through the pool manager, this test fails
        and the standing guard is broken."""
        import daemon.tools.ens_db_tools as t
        module_source = Path(t.__file__).read_text(encoding="utf-8")
        assert "ConnectionPoolManager" not in module_source, (
            "ens_db_tools.py imports ConnectionPoolManager — R13 "
            "standing guard violated. The repair engine must be a "
            "standalone SQLAlchemy Engine, NOT a pool in the manager."
        )
        # Also: must not import the pool manager's package.
        assert "db_pool_manager" not in module_source, (
            "ens_db_tools.py references db_pool_manager — R13 "
            "standing guard violated."
        )

    def test_ens_db_module_owns_standalone_create_engine(self) -> None:
        """Static check: the module must own a ``create_engine`` call
        for the dedicated repair engine — the standing-guard
        counterpart to the negative import check above."""
        import daemon.tools.ens_db_tools as t
        module_source = Path(t.__file__).read_text(encoding="utf-8")
        # The module imports ``create_engine`` from sqlalchemy.
        assert "from sqlalchemy import create_engine" in module_source or \
               "create_engine" in module_source, (
            "ens_db_tools.py does not call create_engine — the dedicated "
            "repair engine must be created standalone, not via the pool "
            "manager (R13 standing guard)."
        )

    def test_repair_engine_cache_keyed_by_engine_url_not_name(self) -> None:
        """The ``_REPAIR_ENGINE_CACHE`` is keyed by the shared engine's
        URL — NOT by ``"ensemble_prod"`` connection name. A
        ``ConnectionPoolManager`` registration would key by connection
        name; this is the structural distinction.

        The cache is populated across tests (each test creates its
        own SQLite engine — keyed by URL). The structural pin is the
        KEY SHAPE: a 2-tuple ``(url, backend_name)``, not a string
        connection name. (If the cache is empty in this process,
        skip the structural check — the type definition still holds
        in the source.)
        """
        import daemon.tools.ens_db_tools as t
        assert hasattr(t, "_REPAIR_ENGINE_CACHE")
        assert isinstance(t._REPAIR_ENGINE_CACHE, dict)
        # Every cached key is a 2-tuple of (url_str, backend_name_str).
        # A ``ConnectionPoolManager``-style registry would key by
        # the connection NAME (a string) — not a tuple.
        for key in t._REPAIR_ENGINE_CACHE:
            assert isinstance(key, tuple) and len(key) == 2, (
                f"Repair engine cache key shape violated: {key!r} — "
                f"keyed by connection NAME would be a string, not a 2-tuple"
            )
            assert isinstance(key[0], str)
            assert isinstance(key[1], str)


# ── Part 3: kill-switch resolver default + value parsing ────────────────────


class TestKillSwitchResolver:
    """``_resolve_repair_enabled`` is the env-level provisioner.

    Pin contract:

    * Default ON (no env) — see the wave dispatch contract.
    * ``=0`` / ``=false`` / ``=no`` / ``=off`` → OFF.
    * ``=1`` / ``=true`` / ``=yes`` / ``=on`` / ``=`` → ON.
    * Invalid values raise ``ValueError`` (fail-closed on
      misconfiguration).
    """

    def test_default_is_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
        monkeypatch.delenv("ENSEMBLE_REPAIR_DISABLED", raising=False)
        monkeypatch.delenv("ENSEMBLE_REPAIR_OFF", raising=False)
        assert _resolve_repair_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off"])
    def test_off_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, value)
        assert _resolve_repair_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", ""])
    def test_on_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, value)
        assert _resolve_repair_enabled() is True

    @pytest.mark.parametrize("value", ["maybe", "auto", "enabled", "y"])
    def test_invalid_values_raise(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, value)
        with pytest.raises(ValueError):
            _resolve_repair_enabled()
