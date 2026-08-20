"""Leader-digest item 9: wiring-failure tolerance for
``ReportDeliveryRecoveryService`` at boot (pause-report-recovery
Phase 3, task 3.6).

The wiring of ``ReportDeliveryRecoveryService`` in
``daemon/manager.py::InstanceManager.setup_worker_pool`` is wrapped
in a try/except block. The contract: a wiring failure (e.g. the
service constructor raises, the boot sweep raises, or
``service.start()`` raises) MUST NOT crash startup. The exception
is caught, a WARNING/ERROR log is emitted, and ``_report_recovery``
is set to ``None`` so the recovery feature is silently disabled
(the rest of the daemon boots).

This file covers two flavors of the contract:

1. **Source-structure check** — the existing pattern in
   ``tests/integration/test_boot_report_recovery.py::TestBootOrder::
   test_setup_worker_pool_wires_recovery_after_stale`` already
   source-scans manager.py. We extend that pattern here to pin
   the try/except structure around the
   ``ReportDeliveryRecoveryService(...)`` construction.

2. **Behavioral check** — Python introspection extracts the actual
   wiring code from manager.py and executes it against a stub
   manager. The behavioral test:

   * Monkeypatches ``daemon.manager.ReportDeliveryRecoveryService``
     (the symbol the manager imports) to raise during construction.
   * Drives the extracted try/except block against a minimal
     manager stub with the required attributes.
   * Asserts: no exception propagates, ``_report_recovery`` is
     ``None`` after the call, and an ERROR-level log line is
     emitted with the documented message format.

If the production code does NOT tolerate the wiring failure
(boot crashes instead of catching), the behavioral test will raise
the underlying exception — that is a real finding, captured here
as evidence, and the test fails until the production code is
fixed.

Test isolation:

* The test does NOT drive the full ``setup_worker_pool`` (which
  would require a full daemon bring-up: real engine, real
  task_repo, real event_repo, real StaleTaskRecovery, real
  WorkerPool, real TaskProcessor). Instead, the test extracts
  the specific ``try/except`` block that wraps the
  ``ReportDeliveryRecoveryService`` wiring and exercises it in
  isolation. The extracted code IS the production code (compiled
  from the source), so a pass proves the production code path
  catches the exception.

* The test does NOT start the periodic background thread (the
  wiring-failure path does not reach ``service.start()`` anyway
  — the construction failure short-circuits).

Reference docs:

* ``daemon/manager.py::InstanceManager.setup_worker_pool`` —
  wiring block at lines 5495-5643.
* ``daemon/services/report_delivery_recovery.py`` — class at line
  207.
* ``daemon/routers/recovery.py`` — the 503-if-disabled endpoint
  behavior (the safety net for the disabled-sweep state).
"""
from __future__ import annotations

import logging
import re
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register every table the service touches before create_all().
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.services.report_delivery_recovery import (
    ReportDeliveryRecoveryService,
)


# =============================================================================
# Source-structure check — the try/except tolerance is in the right place
# =============================================================================
#
# The check pins the structural contract: manager.py's
# ``setup_worker_pool`` must wrap the
# ``ReportDeliveryRecoveryService(...)`` construction in a
# try/except that sets ``_report_recovery = None`` on failure
# and emits an ERROR-level log. Brittle by design — the
# existing TestBootOrder test follows the same pattern.


class TestWiringSourceStructure:
    """Source-structure pin: the wiring block in
    ``daemon/manager.py`` is wrapped in a try/except that
    survives a construction failure.

    Matches the existing pattern at
    ``tests/integration/test_boot_report_recovery.py::
    TestBootOrder::test_setup_worker_pool_wires_recovery_after_stale``.
    """

    def test_wiring_block_is_wrapped_in_try_except(self) -> None:
        """The ``ReportDeliveryRecoveryService`` wiring block in
        ``manager.setup_worker_pool`` is wrapped in a try/except
        that catches ``Exception`` and sets ``_report_recovery =
        None`` on failure.
        """
        manager_path = Path("daemon/manager.py")
        text = manager_path.read_text()

        # Find the wiring assignment. The first
        # ``self._report_recovery = ReportDeliveryRecoveryService``
        # inside ``setup_worker_pool`` is the construction.
        construction_idx = text.find(
            "self._report_recovery = ReportDeliveryRecoveryService"
        )
        assert construction_idx != -1, (
            "manager.setup_worker_pool must wire "
            "self._report_recovery = ReportDeliveryRecoveryService"
        )

        # Walk backwards from the construction to find the
        # enclosing ``try:`` (we look for the most recent
        # ``try:`` that has not been closed before the
        # construction). The search window is the prior 2000
        # chars — generous enough to skip past the stale_recovery
        # try/except (which wraps recover_on_startup).
        search_start = max(0, construction_idx - 2000)
        preceding = text[search_start:construction_idx]
        # The most recent ``try:`` line is the wiring block's
        # opening.
        try_idx = preceding.rfind("\n        try:")
        # Some files use ``    try:`` (4-space) or ``try:`` at
        # column 0. Match all reasonable indentations.
        if try_idx == -1:
            try_idx = preceding.rfind("\n    try:")
        if try_idx == -1:
            try_idx = preceding.rfind("\ntry:")
        assert try_idx != -1, (
            "wiring block must be inside a ``try:`` block — "
            "the contract is that wiring failures are caught, "
            "not propagated"
        )

        # Walk forwards from the construction to find the
        # closing ``except Exception as exc:`` (or similar) +
        # the ``self._report_recovery = None`` assignment that
        # follows. The search window is the next 10000 chars
        # (the wiring block is ~150 lines long).
        search_end = min(len(text), construction_idx + 10000)
        following = text[construction_idx:search_end]
        # Find the first ``except`` after the construction. The
        # block has nested try/except (boot-sweep failure
        # is logged at WARNING; construction failure is logged
        # at ERROR). We want the OUTER except.
        # The OUTER except is the LAST ``except`` in the window.
        # We find the LAST ``except Exception`` (or ``except Exception as``).
        # Simpler: find the LAST ``except`` line that
        # corresponds to the OUTER try/except.
        outer_except_idx = following.rfind("\n        except Exception as exc:")
        if outer_except_idx == -1:
            outer_except_idx = following.rfind("\n    except Exception as exc:")
        if outer_except_idx == -1:
            outer_except_idx = following.rfind("\nexcept Exception as exc:")
        assert outer_except_idx != -1, (
            "wiring block must have an outer ``except Exception as exc:`` "
            "to catch construction failures"
        )

        # After the outer except, the ``self._report_recovery = None``
        # assignment must follow.
        after_except = following[outer_except_idx:]
        assert "self._report_recovery = None" in after_except, (
            "wiring failure MUST set ``self._report_recovery = None`` "
            "so the rest of the daemon sees the recovery feature as "
            "disabled (and the endpoint returns 503 instead of 500)"
        )

        # The log line must mention "ReportDeliveryRecoveryService wiring
        # failed (non-fatal)" or similar wording.
        assert (
            "ReportDeliveryRecoveryService wiring failed" in after_except
        ), (
            "wiring failure log MUST include the documented phrase "
            "``ReportDeliveryRecoveryService wiring failed`` for "
            "operator triage"
        )


# =============================================================================
# Behavioral check — drive the actual wiring block via introspection
# =============================================================================
#
# This test extracts the wiring try/except block from manager.py
# (using Python's ``compile`` + ``exec`` against the source text)
# and executes it against a minimal manager stub. The extracted
# code IS the production code, so a pass proves the production
# code path catches the exception.
#
# Strategy:
# 1. Read manager.py source.
# 2. Locate the wiring block (the OUTER try/except that wraps
#    ``ReportDeliveryRecoveryService(...)`` construction).
# 3. Extract the text of the try/except.
# 4. Build a function that takes a manager stub and runs the
#    extracted try/except (compiled via exec, with the
#    ``ReportDeliveryRecoveryService`` name in the function's
#    scope pointing at the monkeypatched class).
# 5. Drive it with a stub manager and assert the contract.


# A minimal in-memory engine fixture for the manager stub.
@pytest.fixture
def sqlite_engine() -> Engine:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _extract_wiring_block() -> str:
    """Extract the OUTER try/except that wraps
    ``ReportDeliveryRecoveryService(...)`` construction from
    ``daemon/manager.py``.

    Returns the source text of the try/except block. The text is
    expected to compile as a function body when prefixed with
    ``def _wiring(_stub):\n    ...\n`` and given the right
    bindings (logger, ReportDeliveryRecoveryService).

    The extraction is heuristic — it locates the construction
    line, then walks outward to find the enclosing ``try:`` (the
    most recent one in the preceding window), then walks forward
    to find the corresponding ``except`` (the LAST one in the
    following window, since the block is nested).
    """
    manager_path = Path("daemon/manager.py")
    text = manager_path.read_text()

    construction_idx = text.find(
        "self._report_recovery = ReportDeliveryRecoveryService"
    )
    if construction_idx == -1:
        raise AssertionError(
            "manager.setup_worker_pool must wire "
            "self._report_recovery = ReportDeliveryRecoveryService"
        )

    # Walk back to find the OUTER ``try:`` opening. We
    # accept any indentation level.
    search_start = max(0, construction_idx - 2500)
    preceding = text[search_start:construction_idx]

    # Find all ``try:`` lines. The wiring block is the
    # most recent ``try:`` BEFORE the construction whose
    # indentation matches the rest of the wiring block.
    try_positions: list[tuple[int, str]] = []
    for m in re.finditer(r"\n([ \t]+)try:\s*\n", preceding):
        # m.start() is the position of the leading \n in
        # ``preceding``. The ``try:`` keyword starts at
        # m.start() + 1 + len(indent). We capture the indent
        # string for later use.
        indent = m.group(1)
        try_positions.append((m.end(), indent))
    if not try_positions:
        raise AssertionError(
            "no ``try:`` block found in the window before the "
            "ReportDeliveryRecoveryService construction"
        )

    # The wiring block is the LAST ``try:`` whose indent
    # matches the typical method-block indent (>= 4 spaces).
    # Filter to method-level indent.
    method_level_tries = [
        (pos, indent)
        for pos, indent in try_positions
        if indent.count(" ") >= 4 or indent.count("\t") >= 1
    ]
    if not method_level_tries:
        raise AssertionError(
            "no method-level ``try:`` block found before the "
            "ReportDeliveryRecoveryService construction"
        )
    outer_try_pos_in_preceding, outer_try_indent = method_level_tries[-1]

    # The ``try:`` keyword starts at (m.start() + 1 + len(indent))
    # in ``preceding``: skip the leading \n, then skip the
    # captured indent.
    try_keyword_start_in_preceding = (
        method_level_tries[-1][0]
        - (m.end() - m.start())  # back up to m.start()
        + 1                       # skip the leading \n
        + len(outer_try_indent)   # skip the indent
    )

    # Walk forward to find the OUTER ``except`` (the LAST one
    # in the following window, since the block is nested).
    search_end = min(len(text), construction_idx + 10000)
    following = text[construction_idx:search_end]

    # Find the LAST ``except Exception as exc:`` line — that's
    # the outer except.
    except_matches = list(
        re.finditer(
            r"\n([ \t]+)except Exception as exc:", following
        )
    )
    if not except_matches:
        raise AssertionError(
            "no ``except Exception as exc:`` found after the "
            "ReportDeliveryRecoveryService construction"
        )
    # The OUTER except is the LAST one.
    outer_except_match = except_matches[-1]
    outer_except_pos_in_following = outer_except_match.start()

    # Find the ``self._report_recovery = None`` assignment
    # AFTER the outer except.
    after_except = following[outer_except_pos_in_following:]
    none_assignment_idx = after_except.find(
        "self._report_recovery = None"
    )
    if none_assignment_idx == -1:
        raise AssertionError(
            "wiring block must set ``self._report_recovery = "
            "None`` on failure"
        )
    # The assignment is followed by a newline. End the block
    # just past the newline that terminates the assignment line.
    eol_idx = after_except.find("\n", none_assignment_idx)
    if eol_idx == -1:
        eol_idx = len(after_except)
    block_end_in_following = (
        outer_except_pos_in_following + eol_idx + 1
    )

    # Compute absolute positions in the original text.
    outer_try_start = search_start + try_keyword_start_in_preceding
    block_end = construction_idx + block_end_in_following

    raw_block = text[outer_try_start:block_end]

    # Strip the leading ``try:`` indent from each line so the
    # block can be re-indented to match a function body.
    stripped_lines = []
    for line in raw_block.splitlines(keepends=True):
        # Remove the outer_try_indent prefix if present.
        if line.lstrip("\n").startswith(outer_try_indent):
            stripped_lines.append(line[len(outer_try_indent):])
        else:
            stripped_lines.append(line)
    return "".join(stripped_lines)


def _compile_wiring_block(block_text: str) -> Any:
    """Compile the extracted wiring block as a function.

    The compiled function takes the bindings the wiring block
    needs: ``_stub`` (the manager stub), ``ReportDeliveryRecoveryService``
    (the class — monkeypatched for the construction-failure test),
    ``logger`` (the module logger), and ``task_repo`` (a
    pre-constructed ``TaskRepository`` that the wiring block
    passes to the recovery service). Replace ``self.`` and
    keyword-arg uses of ``self`` (``= self``) with ``_stub.``
    / ``_stub`` in the block so it works against a stub.
    """
    # Replace ``self.`` (attribute access) with ``_stub.``.
    block_text = re.sub(r"\bself\.", "_stub.", block_text)
    # Replace keyword-arg / value uses of ``self`` — e.g.
    # ``manager_ref=self,`` and ``_mgr: "InstanceManager" = self,``
    # — with ``_stub``. Use a negative lookbehind to avoid
    # double-replacing ``_stub.`` we just inserted.
    block_text = re.sub(
        r"(?<![\w.])self(?=[\s,)\]])",
        "_stub",
        block_text,
    )
    indented = textwrap.indent(block_text, "        ")
    function_src = (
        "def _wiring_block(_stub, ReportDeliveryRecoveryService, "
        "logger, task_repo, svc):\n"
        + indented
        + "\n"
    )
    compiled = compile(function_src, "<wiring-block>", "exec")
    namespace: dict[str, Any] = {}
    exec(compiled, namespace)
    return namespace["_wiring_block"]


class TestWiringFailureTolerance:
    """Behavioral check: a wiring failure in
    ``ReportDeliveryRecoveryService(...)`` is caught, logged,
    and the manager's ``_report_recovery`` is set to ``None``
    so the rest of the daemon boots.

    The test extracts the actual wiring block from
    ``daemon/manager.py`` (via introspection) and executes it
    against a minimal manager stub. The compiled code IS the
    production code, so a pass proves the production code path
    tolerates a wiring failure.
    """

    def test_construction_failure_caught_and_disabled(
        self,
        sqlite_engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When ``ReportDeliveryRecoveryService.__init__`` raises
        (simulated via monkeypatching the constructor), the
        wiring block MUST:

        * NOT propagate the exception (boot survives).
        * Set ``_report_recovery = None`` on the manager stub.
        * Emit an ERROR-level log line with the documented
          "ReportDeliveryRecoveryService wiring failed (non-fatal)"
          prefix.
        """
        caplog.set_level(
            logging.ERROR, logger="daemon.manager"
        )

        # Build a manager stub with the minimum surface the
        # wiring block touches.
        stub = MagicMock()
        stub._report_injection_repo = MagicMock()
        stub._queue_repository = MagicMock()
        stub._instance_repository = MagicMock()
        stub._loop = None  # sync-fallback branch
        # The services config is read via
        # ``self.config.services`` + many attributes
        # (interval_seconds, age_bound_minutes, etc.). Use a
        # default ServicesConfig.
        from daemon.config import ServicesConfig

        stub.config.services = ServicesConfig()

        # Wire the manager_ref arg.
        # (The stub IS the manager_ref in production; the
        # wiring block passes ``self`` as the manager_ref arg.)

        # Monkeypatch the constructor (as imported by
        # manager.py) to raise on construction. The import in
        # manager.py is local to ``setup_worker_pool`` (``from
        # .services.report_delivery_recovery import
        # ReportDeliveryRecoveryService`` at line 5492), so the
        # name is NOT a module-level attribute on
        # ``daemon.manager``. We patch the source module —
        # ``daemon.services.report_delivery_recovery`` —
        # instead.
        from daemon.services import report_delivery_recovery as rdr_mod

        original_class = rdr_mod.ReportDeliveryRecoveryService

        class _Boom:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                raise RuntimeError(
                    "simulated wiring failure (test)"
                )

        monkeypatch.setattr(
            rdr_mod, "ReportDeliveryRecoveryService", _Boom
        )

        # Extract + compile the wiring block.
        try:
            block_text = _extract_wiring_block()
        except AssertionError as exc:
            pytest.fail(
                f"Could not extract the wiring block from "
                f"manager.py — source structure may have moved. "
                f"Original: {exc}"
            )
        wiring_block = _compile_wiring_block(block_text)

        # Build a minimal ``TaskRepository`` (the wiring block
        # requires ``task_repo`` to be defined in scope).
        from daemon.repositories.task.repository import TaskRepository

        task_repo = TaskRepository(engine=sqlite_engine)

        # The wiring block also uses ``svc`` (a local defined
        # just above the try block as ``svc = self.config.services``).
        # We bind the same ServicesConfig here.
        from daemon.config import ServicesConfig

        svc = ServicesConfig()

        # Drive the wiring block against the stub. The
        # function takes ``(_stub, ReportDeliveryRecoveryService,
        # logger, task_repo, svc)`` — the latter args override
        # the resolved names + supply the local ``task_repo``
        # and ``svc`` bindings.
        wiring_block(
            stub,
            _Boom,
            logging.getLogger("daemon.manager"),
            task_repo,
            svc,
        )

        # Assert: the exception did NOT propagate (we got
        # here), ``_report_recovery`` was set to ``None``, and
        # an ERROR log was emitted.
        assert stub._report_recovery is None, (
            "wiring failure MUST set ``_report_recovery = None`` "
            "so the rest of the daemon sees the recovery "
            "feature as disabled; got "
            f"type={type(stub._report_recovery).__name__}, "
            f"value={stub._report_recovery!r}"
        )

        # The stub's ``_report_recovery.start()`` was NEVER
        # called (the construction failed, so we never reach
        # ``self._report_recovery.start()``).
        # On a MagicMock, accessing a non-existent attr
        # returns a child MagicMock. We assert by inspecting
        # the call list of the parent (we set _report_recovery
        # to None, so any later ``.start()`` call would be on
        # None — which would raise). Easier: just check
        # ``_report_recovery is None``.

        # Verify the ERROR log was emitted with the documented
        # message.
        error_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR
            and r.name == "daemon.manager"
        ]
        assert any(
            "ReportDeliveryRecoveryService wiring failed" in r.getMessage()
            for r in error_records
        ), (
            "wiring failure MUST emit an ERROR log mentioning "
            "the documented phrase for operator triage; got "
            f"{[r.getMessage() for r in error_records]}"
        )

        # Verify the exception type/name appears in the log
        # message (operators need to see WHAT failed).
        assert any(
            "RuntimeError" in r.getMessage()
            and "simulated wiring failure" in r.getMessage()
            for r in error_records
        ), (
            "wiring failure log MUST include the original "
            "exception type + message for operator triage; got "
            f"{[r.getMessage() for r in error_records]}"
        )

    def test_endpoint_returns_503_when_recovery_disabled(
        self,
        sqlite_engine: Engine,
    ) -> None:
        """The crash-recovery endpoint
        ``POST /api/recovery/recover_report_delivery`` returns
        503 when ``_report_recovery is None``.

        This is the SAFETY NET for the disabled state — if the
        wiring failed and ``_report_recovery = None``, an
        operator's manual recovery request gets a 503 (not a
        500) so the operator sees the recovery is disabled
        rather than a generic server error.
        """
        from fastapi import HTTPException
        from unittest.mock import MagicMock as _MM

        from daemon.routers.recovery import recover_report_delivery

        # Build a fake request whose manager has
        # ``_report_recovery = None`` (the disabled state).
        request = _MM()
        request.app.state.manager._report_recovery = None
        request.app.state.manager.is_write_paused = False

        with pytest.raises(HTTPException) as exc:
            import asyncio

            asyncio.run(recover_report_delivery(request))
        assert exc.value.status_code == 503, (
            "the recovery endpoint MUST return 503 (not 500) "
            "when the recovery feature is disabled — operators "
            "see 'disabled' not 'broken'; got status_code="
            f"{exc.value.status_code}"
        )
        # The detail message mentions the disabled state.
        assert "not wired" in str(exc.value.detail).lower() or (
            "disabled" in str(exc.value.detail).lower()
        ), (
            "503 detail MUST mention the disabled state for "
            "operator triage; got detail="
            f"{exc.value.detail!r}"
        )
