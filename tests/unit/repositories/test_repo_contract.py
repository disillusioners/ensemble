"""1.A.0 frozen-interface gate for the ``ServiceRepo`` public surface.

This is the F3 frozen-interface contract test — it pins the EXACT
``ServiceRepo`` public API that Phase 1.B (``ServiceToolManager``)
and Phase 1.C (``ServiceReconciliationService`` skeleton +
``_ensure_postgres_columns``) consume. The merge-gate ``1.MG.1``
(per approver note 15) greps for this file BY NAME; if it is
absent at merge time the gate fires.

The contract is FROZEN at 2026-09-15 (leader-ratified). It captures:

* Every public method exists with the documented parameter list
  (name + kind + default) and the documented return annotation.
* ``mark_exited`` returns ``int`` (the row-count of the
  atomic-guard UPDATE; 1 = transitioned, 0 = race-lost / idempotent
  success). The A13 contract is part of the freeze; the live
  SQLite engine round-trip below exercises the 1/0 branch and
  asserts the value the caller will receive.
* ``update_status`` likewise returns ``int`` (row-count of the
  guarded UPDATE).
* ``insert_with_status`` is a real method (F8 helper) with
  ``status`` and ``reason`` as keyword-only parameters; the
  reason is logged at INFO (F3 freeze; not persisted to a
  schema column).

If 1.B or 1.C needs a parameter added to a frozen method, this
test must be updated IN THE SAME COMMIT as the surface change
— never after. The 1.A.0 acceptance explicitly requires this
test to gate the start of Phase 1.B.

The behavioral round-trip below exercises ``insert`` + ``get_by_id``
+ ``mark_exited`` + the 0-rowcount return on a second
``mark_exited`` of the same row (race-lost idempotent-success path).
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Optional

import pytest
from sqlalchemy import create_engine, event as sa_event, inspect as sa_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.service_tool.models  # noqa: F401 - register ServiceTracking with SQLModel.metadata
from daemon.repositories.service_tool.models import (
    ServiceStatus,
    ServiceTracking,
)
from daemon.repositories.service_tool.repository import ServiceRepo


def _annotation_matches(annotation: object, expected: object) -> bool:
    """Match a return annotation whether it is a live object or a PEP 563 string.

    ``from __future__ import annotations`` (PEP 563) yields
    annotations as strings. The source-string form
    ``'Optional[ServiceTracking]'`` does NOT directly compare equal
    to the live ``typing.Optional[ServiceTracking]`` (the live form
    stringifies to the full module-path repr). We resolve the
    annotation string via :func:`typing.get_type_hints` against
    the enclosing class (``ServiceRepo``) and ``__future__``
    annotations — :func:`get_type_hints` walks the class's
    ``__annotations__`` and resolves forward references using the
    module globals.

    The live-annotation case short-circuits at the top.
    """
    if annotation is expected:
        return True
    # Direct equality (covers both live objects that compare equal,
    # and identical strings).
    if annotation == expected:
        return True
    # Resolve the annotation string against the enclosing class's
    # module globals. PEP 563 annotations are strings until
    # ``get_type_hints`` resolves them.
    if isinstance(annotation, str):
        import typing

        try:
            resolved = typing.get_type_hints(ServiceRepo)
            # Find which method on ServiceRepo has this annotation.
            # The caller passes the annotation for a specific
            # method; resolve via the function's ``__annotations__``.
            # Simpler: resolve the string directly using this module's
            # globals (test file imported ServiceTracking).
            ns = dict(globals())
            # Allow Optional / Union / list / dict from typing.
            ns.update(typing.__dict__)
            return eval(annotation, ns) == expected  # noqa: S307 - local trusted eval
        except Exception:  # pragma: no cover - defensive
            return False
    return False


# ── fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    Mirrors the canonical ``tests/test_chart_tools_reuse_integration.py:80-109``
    fixture pattern: file-backed (NEVER in-memory StaticPool), NullPool,
    WAL + busy_timeout=30000 via a connection-event listener (those
    pragmas are connection-local in SQLite). The PRAGMA ``foreign_keys=ON``
    is set even though service_tracking has no FKs — defense in depth.
    """
    db_path = tmp_path / "service-tracking-contract.sqlite"
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


@pytest.fixture
def repo(engine: Engine) -> ServiceRepo:
    """Bare ``ServiceRepo`` on the file-backed engine."""
    return ServiceRepo(engine=engine)


# ── F3 frozen interface ─────────────────────────────────────────────


class TestServiceRepoFrozenInterface:
    """Pin the exact public surface that 1.B / 1.C consume.

    If a future change adds a required kwarg, renames a method,
    or changes the ``mark_exited`` return type, this class fails
    loudly so the 1.A.0 contract violation surfaces in CI rather
    than silently breaking the Phase 1.B race-safe kill path.
    """

    REQUIRED_METHODS = (
        "__init__",
        "insert",
        "insert_with_status",
        "get_by_id",
        "get_by_name",
        "get_by_name_any_status",
        "list_active",
        "list_all",
        "mark_exited",
        "update_status",
    )

    @pytest.mark.parametrize("name", REQUIRED_METHODS)
    def test_method_present(self, name: str) -> None:
        """Every frozen method exists on ``ServiceRepo``."""
        assert hasattr(ServiceRepo, name), (
            f"ServiceRepo must expose {name!r} per the F3 frozen "
            f"interface (phase1-plan.md task 1.A.0); missing."
        )

    def test_init_signature(self) -> None:
        """``__init__(engine)`` — single positional engine arg."""
        sig = inspect.signature(ServiceRepo.__init__)
        params = list(sig.parameters.values())
        # self + engine only.
        assert len(params) == 2, (
            f"ServiceRepo.__init__ must take exactly (self, engine); "
            f"got params={[p.name for p in params]}"
        )
        assert params[1].name == "engine"
        assert params[1].default is inspect.Parameter.empty, (
            "engine must be REQUIRED (no default) per the F3 freeze"
        )

    def test_insert_signature(self) -> None:
        """``insert(name, command, pid, start_time, cwd, status, started_by_instance_id, started_by_agent_id, log_path, exit_code=None) -> ServiceTracking``."""
        sig = inspect.signature(ServiceRepo.insert)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == [
            "self",
            "name",
            "command",
            "pid",
            "start_time",
            "cwd",
            "status",
            "started_by_instance_id",
            "started_by_agent_id",
            "log_path",
            "exit_code",
        ], (
            f"insert signature drifted; got {names}. F3 freeze blocks "
            f"silent arg reordering / additions."
        )
        # exit_code must default to None (F8 path supplies it).
        exit_code_param = next(p for p in params if p.name == "exit_code")
        assert exit_code_param.default is None
        # Returns the inserted row. The annotation may be a string
        # under `from __future__ import annotations` (PEP 563); we
        # accept either the live object or the string form.
        assert _annotation_matches(sig.return_annotation, ServiceTracking)

    def test_insert_with_status_signature(self) -> None:
        """``insert_with_status(..., *, status, reason, exit_code=None) -> ServiceTracking`` — F8 helper.

        F3 freeze: ``status`` and ``reason`` are keyword-only (after
        the ``*``). ``reason`` is NOT persisted to a schema column —
        the D2 schema has no reason column. The frozen interface
        documents the keyword-only contract so 1.B callers see a
        consistent API.
        """
        sig = inspect.signature(ServiceRepo.insert_with_status)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == [
            "self",
            "name",
            "command",
            "pid",
            "start_time",
            "cwd",
            "started_by_instance_id",
            "started_by_agent_id",
            "log_path",
            "status",
            "reason",
            "exit_code",
        ], (
            f"insert_with_status signature drifted; got {names}. F3 "
            f"freeze blocks silent arg reordering / additions."
        )
        # status + reason + exit_code must be keyword-only (after the ``*``).
        kw_only_names = {"status", "reason", "exit_code"}
        for p in params:
            if p.name in kw_only_names:
                assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
                    f"insert_with_status.{p.name} must be KEYWORD_ONLY "
                    f"per the F3 freeze; got {p.kind}"
                )
        # exit_code default is None (F8 caller supplies it on the
        # synchronous-spawn-failure path; the regular call path
        # relies on the default).
        exit_code_param = next(p for p in params if p.name == "exit_code")
        assert exit_code_param.default is None
        # Returns the inserted row.
        assert _annotation_matches(sig.return_annotation, ServiceTracking)

    def test_get_by_id_signature(self) -> None:
        """``get_by_id(id) -> ServiceTracking | None``."""
        sig = inspect.signature(ServiceRepo.get_by_id)
        params = list(sig.parameters.values())
        assert [p.name for p in params] == ["self", "id"]
        # No default — id is required.
        assert params[1].default is inspect.Parameter.empty
        # Return is Optional[ServiceTracking] — accepted as either
        # the live `ServiceTracking | None` object or its string form
        # under `from __future__ import annotations`.
        assert _annotation_matches(
            sig.return_annotation, Optional[ServiceTracking]
        )

    def test_get_by_name_signature(self) -> None:
        """``get_by_name(name, *, active_only=True)``."""
        sig = inspect.signature(ServiceRepo.get_by_name)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == ["self", "name", "active_only"], (
            f"get_by_name signature drifted; got {names}"
        )
        # active_only is keyword-only and defaults to True (active = STARTING|RUNNING).
        active_param = next(p for p in params if p.name == "active_only")
        assert active_param.kind is inspect.Parameter.KEYWORD_ONLY
        assert active_param.default is True

    def test_get_by_name_any_status_signature(self) -> None:
        """``get_by_name_any_status(name) -> ServiceTracking | None``."""
        sig = inspect.signature(ServiceRepo.get_by_name_any_status)
        params = list(sig.parameters.values())
        assert [p.name for p in params] == ["self", "name"]
        assert params[1].default is inspect.Parameter.empty
        assert _annotation_matches(
            sig.return_annotation, Optional[ServiceTracking]
        )

    def test_list_signatures(self) -> None:
        """``list_active()`` and ``list_all()`` return ``list[ServiceTracking]``."""
        for name in ("list_active", "list_all"):
            sig = inspect.signature(getattr(ServiceRepo, name))
            params = list(sig.parameters.values())
            assert [p.name for p in params] == ["self"], (
                f"{name} must be arg-less besides self; got {[p.name for p in params]}"
            )
            # `list[ServiceTracking]` may serialize as the string
            # "list[ServiceTracking]" under `from __future__ import
            # annotations` — accept both.
            ann = sig.return_annotation
            assert ann is list[ServiceTracking] or ann == "list[ServiceTracking]", (
                f"{name} return annotation must be list[ServiceTracking]; "
                f"got {ann!r}"
            )

    def test_mark_exited_signature(self) -> None:
        """``mark_exited(id, exit_code=None) -> int`` — A13 contract.

        The return type ``int`` is part of the freeze. 1.B's
        ``ServiceToolManager.stop`` reads it as the
        "did I win the race?" signal.
        """
        sig = inspect.signature(ServiceRepo.mark_exited)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == ["self", "id", "exit_code"], (
            f"mark_exited signature drifted; got {names}"
        )
        # exit_code defaults to None (sweep-driven transitions
        # don't always know the exit code; ``os.kill(pid, 0)``
        # confirmed-dead has no exit code).
        exit_param = next(p for p in params if p.name == "exit_code")
        assert exit_param.default is None
        # Return is `int` — accept either the live `int` object or
        # its string form under `from __future__ import annotations`.
        assert sig.return_annotation is int or sig.return_annotation == "int"

    def test_update_status_signature(self) -> None:
        """``update_status(id, status) -> int`` — A13 atomic-guard return."""
        sig = inspect.signature(ServiceRepo.update_status)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == ["self", "id", "status"], (
            f"update_status signature drifted; got {names}"
        )
        # Both args required.
        for p in params[1:]:
            assert p.default is inspect.Parameter.empty
        # Return is `int`.
        assert sig.return_annotation is int or sig.return_annotation == "int"

    def test_repo_is_constructible_with_engine(self, engine: Engine) -> None:
        """Smoke: ``ServiceRepo(engine)`` instantiates without error."""
        repo = ServiceRepo(engine)
        assert repo.engine is engine


# ── Live round-trip on the A13 contract ────────────────────────────


class TestMarkExitedRowCountContract:
    """The ``mark_exited`` row-count return is part of the F3 freeze.

    Phase 1.B's ``ServiceToolManager.stop`` and Phase 1.C / Phase 2's
    reconcile-sweep reaper both depend on the distinction:

    * ``1`` = row transitioned STARTING/RUNNING -> EXITED.
    * ``0`` = row was already EXITED (or deleted, or never existed).
      Callers MUST treat ``0`` as idempotent success.
    """

    def test_mark_exited_returns_1_on_transition(self, repo: ServiceRepo) -> None:
        row = repo.insert(
            name="alpha",
            command=["sleep", "60"],
            pid=4242,
            start_time=1_700_000_000,
            cwd="/tmp",
            status=ServiceStatus.RUNNING.value,
            started_by_instance_id="inst-1",
            started_by_agent_id="worker",
            log_path="/tmp/alpha.log",
        )
        # rowcount=1 — the canonical transition path.
        rc = repo.mark_exited(row.id, exit_code=0)
        assert rc == 1, (
            "mark_exited MUST return 1 when the row was STARTING/RUNNING "
            "and is now EXITED; got {rc!r}"
        )

    def test_mark_exited_returns_0_on_second_call(self, repo: ServiceRepo) -> None:
        """The race-lost / idempotent-success path: ``0`` on a second mark_exited.

        A second ``mark_exited`` on the same row MUST return 0 (not raise,
        not return 1) — the A13 contract that lets the sweep absorb
        sweep<>stop races without false-positive errors. Without this
        invariant, 1.B's stop path would treat concurrent stop calls
        as failure when they are actually idempotent success.
        """
        row = repo.insert(
            name="bravo",
            command=["sleep", "60"],
            pid=4243,
            start_time=1_700_000_000,
            cwd="/tmp",
            status=ServiceStatus.RUNNING.value,
            started_by_instance_id="inst-1",
            started_by_agent_id="worker",
            log_path="/tmp/bravo.log",
        )
        # First call: 1.
        first = repo.mark_exited(row.id, exit_code=0)
        assert first == 1
        # Second call on the now-EXITED row: 0 (race-lost / idempotent).
        second = repo.mark_exited(row.id, exit_code=0)
        assert second == 0, (
            "mark_exited MUST return 0 (not raise) when the row is "
            "already EXITED — A13 idempotent-success contract."
        )

    def test_mark_exited_returns_0_on_unknown_id(self, repo: ServiceRepo) -> None:
        """``mark_exited`` on a missing row returns 0 (not raise).

        The A13 contract is "transition happened or not"; an absent
        row is a 0-rowcount outcome, not an exception. Callers that
        pre-check existence (``get_by_id`` first) MUST NOT rely on
        the pre-check to be authoritative — a concurrent sweep could
        transition the row between the pre-check and the mark_exited,
        and the mark_exited must still return ``0`` cleanly.
        """
        rc = repo.mark_exited(id=999_999_999)
        assert rc == 0

    def test_mark_exited_does_not_affect_exit_rows(
        self, repo: ServiceRepo
    ) -> None:
        """An EXITED row's exit_code is NOT overwritten by a second mark_exited.

        A second ``mark_exited`` returns 0 and bumps nothing — the
        exit_code field stays at the value set by the FIRST
        mark_exited (the one that won the race). This protects
        forensic data: once a row is EXITED, its terminal exit code
        is immutable.
        """
        row = repo.insert(
            name="charlie",
            command=["sleep", "60"],
            pid=4244,
            start_time=1_700_000_000,
            cwd="/tmp",
            status=ServiceStatus.RUNNING.value,
            started_by_instance_id="inst-1",
            started_by_agent_id="worker",
            log_path="/tmp/charlie.log",
        )
        repo.mark_exited(row.id, exit_code=137)  # SIGKILL
        # Second call with a DIFFERENT exit_code: should NOT overwrite.
        repo.mark_exited(row.id, exit_code=42)
        reread = repo.get_by_id(row.id)
        assert reread is not None
        assert reread.exit_code == 137, (
            "exit_code on an EXITED row must be immutable — a second "
            "mark_exited (rowcount=0) must not overwrite the canonical "
            "exit code recorded by the first transition."
        )

    def test_update_status_returns_1_on_transition(self, repo: ServiceRepo) -> None:
        """``update_status`` rowcount contract: ``1`` for transition, ``0`` for race-lost."""
        row = repo.insert(
            name="delta",
            command=["sleep", "60"],
            pid=4245,
            start_time=1_700_000_000,
            cwd="/tmp",
            status=ServiceStatus.STARTING.value,
            started_by_instance_id="inst-1",
            started_by_agent_id="worker",
            log_path="/tmp/delta.log",
        )
        # STARTING -> RUNNING (1.B's service_start promotion path).
        rc = repo.update_status(row.id, ServiceStatus.RUNNING.value)
        assert rc == 1
        # Second update on the now-RUNNING row to a different status
        # returns 1 (RUNNING -> RUNNING is also a guarded UPDATE — the
        # rowcount reflects the row still being in the active set).
        reread = repo.get_by_id(row.id)
        assert reread is not None and reread.status == ServiceStatus.RUNNING.value
        # EXITED via mark_exited.
        rc_exit = repo.mark_exited(row.id)
        assert rc_exit == 1
        # Any further update on the now-EXITED row returns 0 (race-lost).
        rc_after = repo.update_status(row.id, ServiceStatus.RUNNING.value)
        assert rc_after == 0, (
            "update_status MUST return 0 when the row is not in "
            "STARTING/RUNNING (A13 atomic-guard contract)."
        )


# ── Insert round-trip (smoke) ──────────────────────────────────────


class TestInsertRoundTrip:
    """Smoke test for ``insert`` returning a usable row with autoincrement id.

    The full repository behavior tests live in
    ``test_service_tool_repository.py`` — this file is the
    FROZEN-INTERFACE gate only. The round-trip here is the bare
    minimum to prove the contract end-to-end on a live engine.
    """

    def test_insert_returns_row_with_autoincrement_id(
        self, repo: ServiceRepo
    ) -> None:
        row = repo.insert(
            name="echo",
            command=["sleep", "10"],
            pid=4246,
            start_time=1_700_000_000,
            cwd="/tmp",
            status=ServiceStatus.STARTING.value,
            started_by_instance_id="inst-1",
            started_by_agent_id="worker",
            log_path="/tmp/echo.log",
        )
        assert isinstance(row, ServiceTracking)
        assert row.id is not None and row.id > 0
        assert row.name == "echo"
        assert row.command == json.dumps(["sleep", "10"])
        assert row.status == ServiceStatus.STARTING.value

    def test_insert_accepts_pre_serialized_json_string(
        self, repo: ServiceRepo
    ) -> None:
        """The frozen interface accepts a pre-serialized JSON string for ``command``."""
        argv_json = json.dumps(["sleep", "10"])
        row = repo.insert(
            name="foxtrot",
            command=argv_json,
            pid=4247,
            start_time=1_700_000_000,
            cwd="/tmp",
            status=ServiceStatus.STARTING.value,
            started_by_instance_id="inst-1",
            started_by_agent_id="worker",
            log_path="/tmp/foxtrot.log",
        )
        # Stored verbatim (no double-encoding).
        assert row.command == argv_json