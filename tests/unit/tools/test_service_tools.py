"""Tool-factory + decorator-order + _full_doc_ tests for ``service_tools`` (Phase 1.B).

Covers the 1.B.4 acceptance criteria:

* :func:`create_service_tools` returns ``[]`` for a falsy
  ``current_instance_id`` (the loader ``None``-manager stub pattern).
* The factory returns five tools when ``current_instance_id`` is
  truthy: ``service_start``, ``service_stop``, ``service_status``,
  ``service_list``, ``service_logs``.
* Each tool carries a ``_full_doc_`` attribute (the
  :mod:`daemon.tools.proc_tools` precedent at line 2154 — long-form
  documentation reachable via ``tool_help``).
* Decorator order is PINNED: ``@register_tool_category("service")``
  OUTER, ``@tool`` INNER. The pin is asserted by source-grepping the
  decorator-order in the module (the canonical test pattern is
  ``test_attestation_registration.py:128-140``).
* The factory dereferences ``manager._service_tool_manager`` ONLY at
  CALL time, not at construction time — the
  :func:`daemon.tools.proc_tools.create_proc_tools` precedent.

The factory is called with a stub manager that exposes
``_service_tool_manager`` via ``getattr``-style access; we use a
``SimpleNamespace`` with a real :class:`ServiceToolManager` instance
so the tools have something to call into (the manager-level behavior
itself is exercised by ``tests/unit/services/test_service_tool_manager.py``).
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.service_tool.models  # noqa: F401
from daemon.repositories.service_tool.models import ServiceTracking
from daemon.repositories.service_tool.repository import ServiceRepo
from daemon.services.service_tool_manager import ServiceToolManager


# ── fixtures ────────────────────────────────────────────────────────


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine for the manager stub.

    Mirrors the canonical fixture pattern at
    ``tests/test_chart_tools_reuse_integration.py:80-109`` (file-
    backed, NullPool, WAL + busy_timeout).
    """
    db_path = tmp_path / "service-tools-test.sqlite"
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
def service_manager(engine: Engine) -> ServiceToolManager:
    """A real :class:`ServiceToolManager` on the file-backed engine."""
    return ServiceToolManager(repo=ServiceRepo(engine=engine), cap=10)


@pytest.fixture
def manager_stub(service_manager: ServiceToolManager) -> SimpleNamespace:
    """A stub ``InstanceManager`` exposing only ``_service_tool_manager``."""
    return SimpleNamespace(_service_tool_manager=service_manager)


@pytest.fixture
def manager_stub_none() -> SimpleNamespace:
    """A stub ``InstanceManager`` whose ``_service_tool_manager`` is None
    (the early-boot shape — exercises the manager-unavailable gate)."""
    return SimpleNamespace(_service_tool_manager=None)


# ── factory schema ──────────────────────────────────────────────────


EXPECTED_TOOL_NAMES: frozenset[str] = frozenset({
    "service_start",
    "service_stop",
    "service_status",
    "service_list",
    "service_logs",
})


def test_factory_returns_empty_for_falsy_current_instance_id() -> None:
    """``create_service_tools`` returns ``[]`` when ``current_instance_id`` is falsy."""
    from daemon.tools.service_tools import create_service_tools

    # The loader calls the factory with ``current_instance_id=""``
    # before the manager is constructed (the warm-list pattern). The
    # factory MUST return ``[]`` rather than half-configured tools
    # that would crash on the first call.
    for falsy in ("", None, 0):
        tools = create_service_tools(
            manager=None,
            current_instance_id=falsy,  # type: ignore[arg-type]
        )
        assert tools == [], (
            f"factory returned non-empty for falsy "
            f"current_instance_id={falsy!r}: {tools!r}"
        )


def test_factory_returns_five_tools_for_truthy_id(manager_stub: SimpleNamespace) -> None:
    """Five tools are produced when ``current_instance_id`` is truthy."""
    from daemon.tools.service_tools import create_service_tools

    tools = create_service_tools(
        manager=manager_stub,
        current_instance_id="inst-test-1",
        agent_id="worker",
    )
    names = {getattr(t, "name", None) for t in tools}
    assert names == EXPECTED_TOOL_NAMES, (
        f"tool names {names} != expected {EXPECTED_TOOL_NAMES}"
    )
    assert len(tools) == 5


def test_factory_dereferences_manager_at_call_time() -> None:
    """Manager is dereferenced via ``getattr(manager, ...)`` at CALL time.

    Construct a stub whose ``_service_tool_manager`` is ``None``;
    calling ``service_start`` should NOT raise at construction, and
    should return the ``disabled`` shape at CALL time (defensive
    fail-safe when the manager isn't wired).
    """
    from daemon.tools.service_tools import create_service_tools

    stub = SimpleNamespace(_service_tool_manager=None)
    tools = create_service_tools(
        manager=stub,
        current_instance_id="inst-test",
        agent_id="worker",
    )
    assert len(tools) == 5
    # Find the start tool — it's a StructuredTool; ainvoke returns
    # the manager's "disabled" shape without raising.
    import asyncio

    start_tool = next(t for t in tools if getattr(t, "name", None) == "service_start")
    result = asyncio.run(
        start_tool.ainvoke(
            {"name": "test", "command": ["echo", "hi"], "cwd": None}
        )
    )
    assert isinstance(result, dict)
    assert result.get("status") == "disabled"


def test_manager_none_shapes_unified_across_all_five(
    manager_stub_none: SimpleNamespace,
) -> None:
    """Council F4(b) lockstep pin: ALL FIVE tools return the SAME
    disabled-marker convention when ``_service_tool_manager`` is
    missing — ONE stable reason token, rendered per return type.

    * dict surfaces (start / stop / status): ``{"name": ..., "status":
      "disabled", "reason": "service_tool_manager_not_available"}``.
    * list surface (list): a SINGLE-ELEMENT list carrying the marker
      dict (mirrors the manager's own ``list_all`` OFF shape) — never
      ``[]``, which would be indistinguishable from a healthy
      no-services answer.
    * str surface (logs): the marker dict rendered as JSON text —
      never ``""``, which would masquerade as an empty log.
    """
    import asyncio

    from daemon.tools.service_tools import (
        MANAGER_UNAVAILABLE_REASON,
        create_service_tools,
    )

    tools = {
        t.name: t
        for t in create_service_tools(
            manager=manager_stub_none,
            current_instance_id="inst-test",
            agent_id="worker",
        )
    }
    marker = {
        "status": "disabled",
        "reason": MANAGER_UNAVAILABLE_REASON,
    }

    start_result = asyncio.run(
        tools["service_start"].ainvoke(
            {"name": "n", "command": ["echo"], "cwd": None}
        )
    )
    assert start_result == {"name": "n", **marker}

    stop_result = asyncio.run(
        tools["service_stop"].ainvoke({"name": "n", "force": False})
    )
    assert stop_result == {"name": "n", **marker}

    status_result = asyncio.run(tools["service_status"].ainvoke({"name": "n"}))
    assert status_result == {"name": "n", **marker}

    list_result = asyncio.run(tools["service_list"].ainvoke({}))
    assert list_result == [marker], (
        f"council F4b: service_list manager=None must return the "
        f"single-element marker list, got {list_result!r}"
    )

    logs_result = asyncio.run(
        tools["service_logs"].ainvoke({"name": "n", "tail_lines": 5})
    )
    assert logs_result == (
        '{"status": "disabled", "reason": "service_tool_manager_not_available"}'
    ), f"council F4b: service_logs manager=None must return the marker text, got {logs_result!r}"


# ── _full_doc_ ──────────────────────────────────────────────────────


def test_each_tool_has_full_doc(manager_stub: SimpleNamespace) -> None:
    """Every service tool carries a ``_full_doc_`` string (1.B.4 acceptance)."""
    from daemon.tools.service_tools import create_service_tools

    tools = create_service_tools(
        manager=manager_stub,
        current_instance_id="inst-test",
        agent_id="worker",
    )
    for tool in tools:
        name = getattr(tool, "name", "<unknown>")
        full_doc = getattr(tool, "_full_doc_", None)
        assert full_doc is not None, f"tool {name!r} missing _full_doc_"
        assert isinstance(full_doc, str)
        assert len(full_doc) > 100, (
            f"tool {name!r} _full_doc_ too short "
            f"({len(full_doc)} chars); expected substantive documentation"
        )


def test_full_doc_documents_exit_code_none(manager_stub: SimpleNamespace) -> None:
    """``_full_doc_`` for ``service_start`` documents ``exit_code: int | None``.

    Approver-note rider (A4 / F10): deaths NOT observed via
    ``service_stop`` yield ``None``. Documented in the start tool's
    long doc.
    """
    from daemon.tools.service_tools import create_service_tools

    tools = create_service_tools(
        manager=manager_stub,
        current_instance_id="inst-test",
        agent_id="worker",
    )
    start_tool = next(t for t in tools if getattr(t, "name", None) == "service_start")
    full_doc = getattr(start_tool, "_full_doc_", "")
    # The phrase "exit_code: int | None" or "exit_code" + "None" must
    # appear. Look for the explicit phrasing first; if absent, fall
    # back to a softer "None" + "exit_code" co-occurrence.
    assert ("exit_code: int | None" in full_doc) or (
        "exit_code" in full_doc and "None" in full_doc
    ), (
        "service_start._full_doc_ must document exit_code=None for "
        "deaths not observed via service_stop (A4 / F10 approver gate)"
    )


@pytest.mark.parametrize("tool_name", ["service_status", "service_stop"])
def test_full_doc_documents_exit_code_none_for_status_and_stop(
    manager_stub: SimpleNamespace, tool_name: str
) -> None:
    """F10 / A4 pin — ``_full_doc_`` for ``service_status`` AND
    ``service_stop`` documents affirmative ``exit_code: int | None``
    semantics.

    Extension of the existing ``test_full_doc_documents_exit_code_none``
    pin (which covers ``service_start`` only). Per F10/A4, the LLMs
    that READ the docs for these tools must see the exit_code-equals-
    None semantics where they actually observe EXITED rows:

    * ``service_status`` reconciles PID liveness inline and returns
      EXITED rows on dead/recycled PIDs.
    * ``service_stop`` is the canonical producer of EXITED rows on
      graceful / forced termination.

    The soft assertion mirrors the existing pin style: prefer the
    explicit ``exit_code: int | None`` phrasing; fall back to
    ``exit_code`` + ``None`` co-occurrence (so a doc that names the
    field and explains the sentinel still passes; a doc that only
    names the field without explaining the None semantics fails).

    Note for the docs lane: as of this pin's authoring, neither
    ``service_status._full_doc_`` nor ``service_stop._full_doc_``
    carries the explicit ``exit_code: int | None`` wording — the
    existing ``service_start._full_doc_`` is the only doc that does.
    This test therefore acts as a tripwire until the docs catch up.
    """
    from daemon.tools.service_tools import create_service_tools

    tools = create_service_tools(
        manager=manager_stub,
        current_instance_id="inst-test",
        agent_id="worker",
    )
    target_tool = next(t for t in tools if getattr(t, "name", None) == tool_name)
    full_doc = getattr(target_tool, "_full_doc_", "")
    assert ("exit_code: int | None" in full_doc) or (
        "exit_code" in full_doc and "None" in full_doc
    ), (
        f"{tool_name}._full_doc_ must document exit_code=None for "
        f"deaths not observed via service_stop (F10 / A4 approver gate). "
        f"Mirror the service_start._full_doc_ wording: '``exit_code: int | None`` "
        f"on EXITED rows — ``None`` is the canonical value for deaths NOT "
        f"observed via service_stop'."
    )


def test_full_doc_documents_cross_instance_stop_semantics(
    manager_stub: SimpleNamespace,
) -> None:
    """``_full_doc_`` for ``service_stop`` documents daemon-global / name-keyed."""
    from daemon.tools.service_tools import create_service_tools

    tools = create_service_tools(
        manager=manager_stub,
        current_instance_id="inst-test",
        agent_id="worker",
    )
    stop_tool = next(t for t in tools if getattr(t, "name", None) == "service_stop")
    full_doc = getattr(stop_tool, "_full_doc_", "")
    text = full_doc.lower()
    # F10: name-keyed + daemon-global.
    assert "name-keyed" in text or "name keyed" in text, (
        "service_stop._full_doc_ must document name-keyed semantics (F10)"
    )
    # OQ#4 resolution: no started_by gate. The phrase may be phrased as
    # "no ``started_by`` gate" or "no started_by gate" — accept either.
    has_no_started_by = (
        "no ``started_by``" in text
        or "no started_by" in text
        or "no 'started_by'" in text
    )
    assert has_no_started_by, (
        "service_stop._full_doc_ must document the absence of a "
        "started_by gate (OQ#4 resolution)"
    )


def test_full_doc_documents_grandchild_setsid_escape(
    manager_stub: SimpleNamespace,
) -> None:
    """``_full_doc_`` for ``service_stop`` documents the grandchild-setsid escape (F15)."""
    from daemon.tools.service_tools import create_service_tools

    tools = create_service_tools(
        manager=manager_stub,
        current_instance_id="inst-test",
        agent_id="worker",
    )
    stop_tool = next(t for t in tools if getattr(t, "name", None) == "service_stop")
    full_doc = getattr(stop_tool, "_full_doc_", "")
    # F15: a grandchild that calls setsid itself escapes killpg.
    text = full_doc.lower()
    assert "setsid" in text and ("escape" in text or "escapes" in text), (
        "service_stop._full_doc_ must document the grandchild-setsid "
        "killpg escape limitation (F15)"
    )


def test_stop_docs_never_promise_running_return(
    manager_stub: SimpleNamespace,
) -> None:
    """Council F3 lockstep pin: the ``service_stop`` docs must NOT
    advertise a ``{"status": "running"}`` return.

    ``stop`` blocks through the grace window and always resolves to a
    terminal shape (exited / not_found / starting+pid_not_yet_assigned
    / exited+pid_dead / exited+pid_recycled family / disabled). An
    earlier revision of the tool docstring advertised a ``running``
    return that NO code path ever produced — this pin keeps the
    corrected docs honest in both doc surfaces (the tool docstring
    and its ``_full_doc_``).

    The pin matches the EXACT old advertised return bullets (so the
    correction's own "there is no ``{\"status\": \"running\"}``
    return" negation text does not false-trip it).
    """
    import inspect

    import daemon.services.service_tool_manager as _stm
    from daemon.tools.service_tools import create_service_tools

    tools = create_service_tools(
        manager=manager_stub,
        current_instance_id="inst-test",
        agent_id="worker",
    )
    stop_tool = next(t for t in tools if getattr(t, "name", None) == "service_stop")
    full_doc = getattr(stop_tool, "_full_doc_", "")
    tool_doc = inspect.getdoc(stop_tool) or ""
    manager_doc = inspect.getdoc(_stm.ServiceToolManager.stop) or ""

    old_bullets = (
        '{"name": ..., "pid": ..., "status": "running"}',
        '{"name": ..., "status": "running"}',
    )
    for label, doc in (
        ("service_stop tool docstring", tool_doc),
        ("service_stop._full_doc_", full_doc),
        ("ServiceToolManager.stop docstring", manager_doc),
    ):
        for bullet in old_bullets:
            assert bullet not in doc, (
                f"council F3: {label} still advertises the 'running' "
                f"stop return bullet {bullet!r} — no code path produces "
                f"one (stop blocks through grace and always resolves "
                f"to a terminal shape)"
            )


# ── decorator order ────────────────────────────────────────────────


def test_decorator_order_register_above_tool_in_source() -> None:
    """Decorator order: ``@register_tool_category("service")`` OUTER,
    ``@tool`` INNER — verified by source-grepping.

    The langchain ``@tool`` wrapper preserves inner-function
    attributes (incl. ``_tool_category`` set by the
    ``register_tool_category`` decorator) via ``functools.wraps`` —
    but ONLY if the register decorator ran FIRST on the raw function.
    The canonical pin is ``test_attestation_registration.py:128-140``
    (A14 amendment).
    """
    source = (
        REPO_ROOT / "daemon" / "tools" / "service_tools.py"
    ).read_text(encoding="utf-8")

    # Find every ``@register_tool_category("service")`` decorator.
    # Each one MUST be followed by ``@tool`` before the next ``def``.
    register_lines = [
        i for i, line in enumerate(source.splitlines())
        # The decorator itself starts with ``@`` (no leading whitespace
        # beyond standard 4-space indent). Docstring mentions (e.g.
        # the module docstring) use ``@register_tool_category`` in
        # backticks and are NOT prefixed with ``@`` directly on the
        # line.
        if line.lstrip().startswith('@register_tool_category("service")')
    ]
    tool_lines = [
        i for i, line in enumerate(source.splitlines())
        if line.strip() == "@tool"
    ]
    def_lines = [
        i for i, line in enumerate(source.splitlines())
        if "def service_" in line
    ]

    assert register_lines, "@register_tool_category(\"service\") decorator missing"
    assert tool_lines, "@tool decorator missing"
    assert def_lines, "def service_*(...) missing"

    # Every register decorator must have a corresponding @tool BEFORE
    # the next ``def`` line.
    # Each ``@register_tool_category("service")`` should be one line
    # before ``@tool`` which is one line before ``def service_*``.
    # We assert the simple invariant: register < tool < def per
    # service_*.function.
    assert len(register_lines) == 5, (
        f"expected 5 @register_tool_category decorators; got {len(register_lines)}"
    )
    assert len(tool_lines) >= 5, (
        f"expected at least 5 @tool decorators; got {len(tool_lines)}"
    )

    # Pair-wise: each register should be IMMEDIATELY followed by @tool
    # then def. We pair by file-order.
    for i, reg in enumerate(register_lines):
        # Find the next @tool and next def after ``reg``.
        next_tool = next((t for t in tool_lines if t > reg), None)
        next_def = next((d for d in def_lines if d > reg), None)
        assert next_tool is not None and next_def is not None, (
            f"register@{reg} has no following @tool or def"
        )
        assert reg < next_tool < next_def, (
            f"decorator order wrong for register@{i}: "
            f"register@{reg}, tool@{next_tool}, def@{next_def}"
        )


# ── category attrs ──────────────────────────────────────────────────


def test_module_has_category_name_and_doc() -> None:
    """``service_tools`` exposes ``CATEGORY_NAME`` and ``CATEGORY_DOC`` (precedent proc_tools.py:66-77)."""
    from daemon.tools import service_tools

    assert hasattr(service_tools, "CATEGORY_NAME")
    assert service_tools.CATEGORY_NAME == "Service"
    assert hasattr(service_tools, "CATEGORY_DOC")
    assert isinstance(service_tools.CATEGORY_DOC, str)
    assert len(service_tools.CATEGORY_DOC) > 50


# ── live tool behavior (smoke) ──────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_service_start_then_stop_smoke(manager_stub: SimpleNamespace, tmp_path) -> None:
    """End-to-end smoke: ``service_start`` then ``service_stop`` round-trip."""
    import asyncio
    import os

    from daemon.tools.service_tools import create_service_tools

    # Redirect the log root to tmp_path so the test does not pollute
    # the repo's data/ dir.
    os.environ["ENSEMBLE_SERVICE_LOG_DIR"] = str(tmp_path / "logs")
    try:
        tools = create_service_tools(
            manager=manager_stub,
            current_instance_id="inst-test",
            agent_id="worker",
        )
        start_tool = next(
            t for t in tools if getattr(t, "name", None) == "service_start"
        )
        stop_tool = next(
            t for t in tools if getattr(t, "name", None) == "service_stop"
        )

        # Start a short-lived ``sleep`` service.
        start_result = asyncio.run(
            start_tool.ainvoke(
                {
                    "name": "smoke-test-1",
                    "command": ["sleep", "30"],
                    "cwd": None,
                }
            )
        )
        assert isinstance(start_result, dict)
        assert start_result.get("status") == "running", (
            f"start failed: {start_result!r}"
        )
        pid = start_result.get("pid")
        assert pid is not None and pid > 0

        # Stop the service (force=True skips the 5s grace).
        stop_result = asyncio.run(
            stop_tool.ainvoke({"name": "smoke-test-1", "force": True})
        )
        assert isinstance(stop_result, dict)
        # force=True always returns "exited" (stop() marks the row
        # EXITED on both the killpg-success and pid_dead branches).
        assert stop_result.get("status") == "exited", (
            f"stop result unexpected: {stop_result!r}"
        )
    finally:
        os.environ.pop("ENSEMBLE_SERVICE_LOG_DIR", None)