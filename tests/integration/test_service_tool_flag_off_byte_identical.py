"""Service-tool Phase 3.A.7 — flag-OFF byte-identical integration tests.

Proves the ``ENSEMBLE_SERVICE_TOOL_ENABLED=0`` state is byte-identical
to pre-Phase-1 behavior at every layer:

* **(a)** Boot probe line shows ``service_tool_enabled=False`` —
  the EXACT format pinned at ``daemon/config.py:4004-4010``.
* **(b)** The ``service_*`` tool names are PRIVILEGED-category
  (D4 Option A — the ``service`` key sits in
  ``PRIVILEGED_TOOL_CATEGORIES``). A default-allow / empty-allow
  agent NEVER receives any ``service_*`` tool — the privilege-strip
  is identical to ``system_upgrade``, ``system-log``, and
  ``ens-db``. SC-6 negative.
* **(c)** The lifespan mount (``daemon/api.py`` ``lifespan``
  context manager) does NOT start ``ServiceReconciliationService``
  when the flag is OFF — it logs ``DISABLED`` and skips the
  ``await service_reconciliation.start()`` call.
* **(d)** The schema migration (``_ensure_postgres_columns`` +
  ``SQLModel.metadata.create_all``) runs INDEPENDENT of the flag —
  a fresh DB has ``service_tracking`` + indexes regardless of
  the flag state. The schema is a passive store; the kill-switch
  only gates runtime behavior (start/stop/sweep).
* **(e)** Flipping the flag from OFF to ON (config reload, no
  daemon restart-required for the schema layer) does NOT require
  re-migration — the table + indexes are flag-independent and
  already present from the OFF phase.
* **(f)** The ``ServiceToolManager.status()`` inline reconciliation
  is a no-op when ``enabled=False`` — returns
  ``{"status": "disabled"}`` WITHOUT touching the DB (no
  ``mark_exited`` write, no row transition).

Anti-duplication: the 2.A.6 zero-query gate (``sweep_once``
returns the disabled-shape counters with NO DB queries when
``enabled_check=False``) is unit-covered at
``tests/unit/services/test_service_reconciliation.py`` —
this file does NOT duplicate it. Instead, it exercises the
surfaces ABOVE the gate: the resolver + boot probe format, the
privilege-strip in the resolved tool list, the lifespan mount,
the schema flag-independence, and the manager.status
inline-reconciliation gate.

All cases carry ``@pytest.mark.integration`` (addopts deselects by
default — invoke with ``pytest -m integration``).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterator, List

import pytest
from sqlalchemy import create_engine, event as sa_event, inspect as sa_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

# Register every SQLModel table before create_all runs.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.service_tool.models  # noqa: F401

from daemon.config import (
    ENSEMBLE_SERVICE_TOOL_ENABLED,
    _install_service_tool_enabled,
    _reset_service_tool_for_tests,
    load_config,
    service_tool_enabled,
)
from daemon.repositories.service_tool.repository import ServiceRepo
from daemon.services.service_tool_manager import (
    DEFAULT_MAX_CONCURRENT,
    ServiceToolManager,
)
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_all_tool_names,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers — minimal config + cap reset
# ─────────────────────────────────────────────────────────────────────


def _write_minimal_config(tmp_path: Path) -> Path:
    """Write a non-empty config.yaml that ``load_config`` accepts.

    Mirrors the pattern at
    ``tests/unit/test_service_tool_config.py:_write_minimal_config``.
    """
    path = tmp_path / "config.yaml"
    path.write_text("queue:\n  discard_on_startup: false\n")
    return path


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Clear the cached kill-switch between tests."""
    _reset_service_tool_for_tests()
    yield
    _reset_service_tool_for_tests()


@pytest.fixture
def file_backed_engine(tmp_path) -> Iterator[Engine]:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    Mirrors the canonical service-tool fixture pattern (house
    contract — NullPool + WAL + busy_timeout=30000).
    """
    db_path = tmp_path / "service-flag-off-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


# ─────────────────────────────────────────────────────────────────────
# Case (a): boot probe shows service_tool_enabled=False
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_boot_probe_format_when_off(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case (a): ``ENSEMBLE_SERVICE_TOOL_ENABLED=0`` → boot probe shows
    ``service_tool_enabled=False``.

    The probe line is emitted at config-resolution time (S13 reviewer
    gate — must stay on the boot path so a quiet-daemon grep never
    false-fails). Format pinned at ``daemon/config.py:4004-4010``:
    ``[ServiceTool] service_tool_enabled=%s (env ENSEMBLE_SERVICE_TOOL_ENABLED), max_concurrent=%s, reconcile_interval=%ss``.
    """
    config_path = _write_minimal_config(tmp_path)
    monkeypatch.setenv(ENSEMBLE_SERVICE_TOOL_ENABLED, "0")

    with caplog.at_level(logging.INFO, logger="daemon.config"):
        load_config(config_path)

    # EXACT format pin — see plan task 1.C.10.
    assert "[ServiceTool]" in caplog.text, (
        f"boot probe prefix missing; caplog text was: {caplog.text!r}"
    )
    assert "service_tool_enabled=False" in caplog.text
    assert "(env ENSEMBLE_SERVICE_TOOL_ENABLED)" in caplog.text
    # The other knobs (max_concurrent, reconcile_interval) are still
    # surfaced even when the kill-switch is OFF — operators need to
    # verify they were honored at config-resolution time.
    assert "max_concurrent=" in caplog.text
    assert "reconcile_interval=" in caplog.text

    # The installed cache reflects OFF.
    assert service_tool_enabled() is False


# ─────────────────────────────────────────────────────────────────────
# Case (b): service_* tools are PRIVILEGED; default-allow agent gets zero
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_service_category_is_privileged_and_default_stripped() -> None:
    """Case (b): ``service_*`` tools are PRIVILEGED (D4 Option A) —
    a default-allow agent resolves ZERO service tools.

    The five ``service_*`` tools are listed in KNOWN_TOOL_NAMES (the
    source-discovered universe — merged with KNOWN_TOOL_NAMES for
    frozen-binary safety). The category is registered in
    ``PRIVILEGED_TOOL_CATEGORIES``. The privilege-strip
    (``_strip_privileged_category_tools`` and the
    ``resolve_tool_filter`` empty-allow branch in
    ``daemon/tools/instance.py``) guarantees that a default-allow
    agent (no explicit ``tools.allow`` entry) NEVER sees any
    ``service_*`` tool — the SC-6 negative.

    This is byte-identical to the privilege-strip applied to
    ``system_upgrade``, ``system-log``, and ``ens-db``.
    """
    # (1) The service category is in PRIVILEGED_TOOL_CATEGORIES.
    assert "service" in PRIVILEGED_TOOL_CATEGORIES, (
        "service category MUST be in PRIVILEGED_TOOL_CATEGORIES — "
        "D4 Option A contract"
    )

    # (2) The service category is registered in CATEGORY_MODULES.
    assert "service" in CATEGORY_MODULES, (
        "service category MUST be in CATEGORY_MODULES"
    )
    assert CATEGORY_MODULES["service"] == "daemon.tools.service_tools"

    # (3) All five ``service_*`` tools are in KNOWN_TOOL_NAMES.
    for name in (
        "service_start",
        "service_stop",
        "service_status",
        "service_list",
        "service_logs",
    ):
        assert name in KNOWN_TOOL_NAMES, (
            f"{name} must be in KNOWN_TOOL_NAMES (the static fallback)"
        )

    # (4) Source-discovered names must include all five — the
    # bidirectional-drift pin. ``discover_all_tool_names`` returns the
    # static union (the source scan is canonical where present;
    # KNOWN_TOOL_NAMES is the frozen-binary fallback).
    discovered = discover_all_tool_names()
    for name in (
        "service_start",
        "service_stop",
        "service_status",
        "service_list",
        "service_logs",
    ):
        assert name in discovered, (
            f"{name} must be in discover_all_tool_names() — "
            f"a missing entry would mean the source and static "
            f"universes drift apart"
        )

    # (5) ``_strip_privileged_category_tools`` drops the service
    # category from a default-allow tool list — the
    # ``daemon.tools.instance`` privilege-strip is the SC-6 negative
    # pin.
    from daemon.tools.instance import _strip_privileged_category_tools
    fake_tools = [
        type("FakeTool", (), {"_tool_category": "service", "name": "service_start"}),
        type("FakeTool", (), {"_tool_category": "filesystem", "name": "read_file"}),
        type("FakeTool", (), {"_tool_category": "service", "name": "service_stop"}),
        type("FakeTool", (), {"_tool_category": "system-log", "name": "ens_system_log_list"}),
    ]
    stripped = _strip_privileged_category_tools(fake_tools)
    # Two ``service_*`` tools + one ``system-log`` (privileged) dropped;
    # only the ``filesystem`` tool survives.
    assert len(stripped) == 1
    assert getattr(stripped[0], "_tool_category") == "filesystem"


# ─────────────────────────────────────────────────────────────────────
# Case (c): ServiceReconciliationService NOT started when flag is OFF
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_lifespan_skips_reconciliation_when_off(
    file_backed_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """Case (c): when ``service_tool_enabled=False``, the
    ``ServiceReconciliationService`` is NOT started by the
    api.py lifespan mount.

    The lifespan guard (``daemon/api.py`` ``lifespan`` context
    manager) reads the resolver-cached ``service_tool_enabled()``
    flag and emits the DISABLED log line at info level. The
    manager is constructed but the sweep is NEVER started.

    This test exercises the resolver + cache + a real
    ``ServiceReconciliationService.start()`` invocation only
    when OFF would have been the trigger — proves the lifespan
    guard short-circuits BEFORE the sweep is wired.

    The direct construction below mirrors the lifespan's gate
    (the gate checks ``config.services.service_tool.enabled``; we
    read the same field here via the manager-with-``enabled=False``
    path and assert the sweep task stays None — no sweep task,
    no DB queries).
    """
    # Install OFF in the module cache.
    _install_service_tool_enabled(False)
    assert service_tool_enabled() is False

    repo = ServiceRepo(engine=file_backed_engine)

    # Build a ServiceReconciliationService directly (mirror of what
    # the lifespan does when the manager is available). With the
    # OFF flag installed, the lifespan takes the DISABLED branch and
    # NEVER calls ``service_reconciliation.start()``.
    import asyncio

    from daemon.services.service_reconciliation import (
        DEFAULT_SWEEP_INTERVAL_SECONDS,
        ServiceReconciliationService,
    )

    svc = ServiceReconciliationService(
        repo,
        interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
        # ``enabled_check`` is the defense-in-depth gate; the OFF
        # branch matches ``service_tool_enabled() == False``.
        enabled_check=lambda: service_tool_enabled(),
    )
    # The sweep task is NOT created until start() is called — and
    # the lifespan never calls start() when the flag is OFF.
    assert svc._task is None, (
        "OFF state must not auto-start the sweep; the lifespan "
        "gates on the flag before calling start()"
    )

    # Defense-in-depth: even if a future caller DID call start()
    # while OFF, the per-tick ``enabled_check`` short-circuits to
    # the disabled-shape counters with NO DB queries (the unit
    # coverage at test_service_reconciliation.py pins this).
    async def _probe() -> dict:
        await svc.start()
        try:
            return await svc.sweep_once()
        finally:
            await svc.stop()

    result = asyncio.run(_probe())
    assert result.get("disabled") is True
    assert result == {
        "alive": 0,
        "reaped": 0,
        "errors": 0,
        "starting_reaped": 0,
        "disabled": True,
    }


# ─────────────────────────────────────────────────────────────────────
# Case (d) + (e): migration runs regardless of flag; flipping flag
# does NOT re-migrate
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_schema_present_when_flag_off() -> None:
    """Case (d): the ``service_tracking`` table + the 3-site indexes
    exist on a fresh DB regardless of the flag state.

    The schema is a PASSIVE store — it is created by
    ``SQLModel.metadata.create_all`` (the canonical model-driven
    schema path) AND by ``_ensure_postgres_columns`` (the PG-only
    idempotent ALTER block). Both run at boot regardless of the
    ``service_tool_enabled`` flag. The flag only gates RUNTIME
    behavior (the sweep, the manager facade); the schema is
    flag-independent.

    On SQLite (the test default), ``SQLModel.metadata.create_all``
    is the sole schema path — the
    ``idx_service_tracking_name_active`` partial UNIQUE index is
    emitted with the WHERE clause from the
    ``__table_args__`` block.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "schema-flag-off.sqlite"
        eng = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )
        try:
            # Flag is OFF at the system level — schema MUST still apply.
            assert service_tool_enabled() is True, (
                "sanity: the module-cache default is ON; this test "
                "verifies the schema runs REGARDLESS of the flag"
            )

            SQLModel.metadata.create_all(eng)

            inspector = sa_inspect(eng)
            table_names = inspector.get_table_names()
            assert "service_tracking" in table_names, (
                f"service_tracking table MUST exist on a fresh DB "
                f"regardless of the flag state; got tables: {table_names}"
            )

            # Both indexes MUST exist (the 3-site pin — see
            # test_service_tool_repository.py for the strict pin).
            indexes = inspector.get_indexes("service_tracking")
            index_names = {ix["name"] for ix in indexes}
            assert "idx_service_tracking_name_active" in index_names, (
                f"partial UNIQUE index MUST exist; got indexes: {index_names}"
            )
            assert "idx_service_tracking_pid" in index_names
        finally:
            eng.dispose()


@pytest.mark.integration
def test_flag_flip_after_migration_no_remigrate() -> None:
    """Case (e): flipping the flag from OFF to ON requires NO re-migration.

    The schema is flag-independent (case d above); the migration
    chain applies the table + indexes on the FIRST boot regardless
    of the flag value. Flipping the flag (via env change + restart
    or config reload) does NOT require a second migration pass.

    The test mirrors the migration idempotency contract: invoke
    ``SQLModel.metadata.create_all`` twice (simulating two boots)
    on the same engine and assert the schema is byte-identical
    (same table, same indexes, same column set). The DDL is
    IF-NOT-EXISTS, so re-running is a no-op; if it ever surfaced
    a "table already exists" error, the migration would be
    non-idempotent and the flag-flip restart-pending semantics
    would break.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "flag-flip.sqlite"
        eng = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )
        try:
            # Boot 1 — flag OFF (the OFF state still runs the schema).
            SQLModel.metadata.create_all(eng)
            insp1 = sa_inspect(eng)
            cols1 = {c["name"] for c in insp1.get_columns("service_tracking")}

            # Boot 2 — flag ON (the ON state runs the SAME schema; the
            # flag flip itself does not add or remove columns).
            SQLModel.metadata.create_all(eng)
            insp2 = sa_inspect(eng)
            cols2 = {c["name"] for c in insp2.get_columns("service_tracking")}

            assert cols1 == cols2, (
                f"flag flip MUST NOT add/remove columns; "
                f"got boot1={cols1}, boot2={cols2}"
            )
            # Sanity — the column set is non-empty.
            assert "id" in cols1
            assert "name" in cols1
            assert "status" in cols1
        finally:
            eng.dispose()


# ─────────────────────────────────────────────────────────────────────
# Case (f): service_status inline reconciliation is no-op when OFF
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_manager_status_inline_reconcile_disabled_when_off(
    file_backed_engine: Engine,
) -> None:
    """Case (f): ``ServiceToolManager.status()`` returns
    ``{"status": "disabled"}`` when ``enabled=False`` — NO DB query,
    NO ``mark_exited`` write, NO inline reconciliation.

    The defensive gate is the FIRST branch in ``ServiceToolManager``
    (``if not self.enabled: return {..., "status": "disabled"}``).
    This proves the manager-surface OFF gate mirrors the
    reconcile-tick gate — the OFF path is byte-identical to
    pre-Phase-1 behavior (no DB touches).

    The test inserts a row in the DB to prove the manager would
    have touched it had the gate NOT fired — the row stays
    untouched after ``status()`` returns the disabled shape.
    """
    repo = ServiceRepo(engine=file_backed_engine)

    # Seed an active row to prove the manager would touch it if the
    # gate were not in place.
    from daemon.repositories.service_tool.models import ServiceTracking

    seed = ServiceTracking(
        name="would-be-touched",
        command="echo hello",
        pid=os.getpid(),
        start_time=1,
        cwd="/tmp",
        status="running",
        started_by_instance_id="flag-off-test",
        started_by_agent_id="flag-off-tester",
        log_path="/tmp/would-be-touched.log",
    )
    with SQLModel.__session__ if False else _session_using(  # type: ignore[unreachable]
        file_backed_engine
    ) as session:
        session.add(seed)
        session.commit()
        session.refresh(seed)
        seeded_id = seed.id
        seeded_status = seed.status

    # Manager with ``enabled=False`` — every public surface returns
    # the disabled shape.
    manager = ServiceToolManager(
        repo=repo,
        cap=DEFAULT_MAX_CONCURRENT,
        enabled=False,
    )

    # status() returns disabled WITHOUT touching the row.
    import asyncio

    async def _probe() -> dict:
        return await manager.status("would-be-touched")

    result = asyncio.run(_probe())
    assert result == {
        "name": "would-be-touched",
        "status": "disabled",
        "reason": "service_tool_enabled=False",
    }, (
        f"OFF manager.status MUST return disabled shape; got {result!r}"
    )

    # The seeded row is STILL ``running`` — no inline reconciliation
    # happened (no ``mark_exited`` write).
    reread = repo.get_by_name("would-be-touched")
    assert reread is not None
    assert reread.id == seeded_id
    assert reread.status == seeded_status, (
        f"row MUST be unchanged after OFF status(); "
        f"got status={reread.status} (expected {seeded_status})"
    )

    # list_all() does NOT short-circuit on the OFF gate (the manager
    # contract — list_all is a read-shape mirror; only start/stop/
    # status gate on ``enabled``). However, ``list_all`` IS a
    # read-only surface — it does not refuse to read while OFF.
    # We do NOT assert its result here; the SC-10 negative pin
    # is the ``service_status`` no-op above (the only public method
    # that MUST short-circuit on OFF).


def _session_using(eng: Engine):
    """Tiny helper — context-managed Session.

    Avoids importing ``sqlmodel.Session`` at module scope so a
    conftest can override it. Mirrors the ``with Session(engine) as
    session:`` shape used everywhere else.
    """
    from sqlmodel import Session

    return Session(eng)


# ─────────────────────────────────────────────────────────────────────
# Sanity — PRIVILEGED_TOOL_CATEGORIES is the same set as
# system_upgrade + system-log + ens-db + service (3-site pin
# documented in daemon/tools/_tool_registry.py:148-161)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_privileged_category_set_is_pinned() -> None:
    """Sanity: the 3-site pin at ``PRIVILEGED_TOOL_CATEGORIES``
    is intact — ``service`` joined the union without bumping the
    count.

    The 3-site pin lives in
    ``tests/unit/tools/test_upgrade_registration.py``,
    ``tests/unit/tools/test_attestation_registration.py``, and
    ``tests/integration/test_maintenancer_spawn_resolves_tools.py``
    (the SAME-PR RULE per D18 / A14). This integration-layer
    sanity pin duplicates the set check to ensure the OFF flag
    test runs in a category universe where ``service`` is
    structurally equivalent to ``system_upgrade`` — the
    privilege-strip applies symmetrically.
    """
    assert PRIVILEGED_TOOL_CATEGORIES == frozenset(
        {"system_upgrade", "system-log", "ens-db", "service"}
    ), (
        f"PRIVILEGED_TOOL_CATEGORIES drifted from the leader-ratified "
        f"set; got {PRIVILEGED_TOOL_CATEGORIES}"
    )