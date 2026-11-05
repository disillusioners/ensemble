"""Registration + wiring proof for the ``nest-report`` write category.

The nest-anomaly-report lane (2026-11-05, board ``wi-2d691ed2``) opens
ONE write surface in its OWN NON-PRIVILEGED category: ``report_anomaly``
(an agent reports something wrong; the daemon normalizes, scrubs,
harvests the bounded evidence and persists ONE row).

This pack pins:

* (a) ``CATEGORY_MODULES`` carries the entry and it is a
  ``daemon.tools.*`` path (a ``daemon.services.*`` value is NEVER
  discovered by the AST scanner and the exact-equality drift pin fails at
  boot);
* (b) the category is **NOT** in ``PRIVILEGED_TOOL_CATEGORIES`` (R4) and
  the privileged set is untouched by this lane;
* (c) both tool-name universes (``DYNAMIC_TOOL_NAMES`` +
  ``KNOWN_TOOL_NAMES``) carry the tool name, and the frozen-name drift
  test's source-only universe agrees (bidirectional);
* (d) the AST scanner finds the ``@tool`` function (decorator-only
  registration is SILENTLY INVISIBLE);
* (e) the construction extend in ``daemon/tools/instance.py`` PRECEDES
  ``create_help_tool`` / ``scan_tools_for_full_docs`` /
  ``_apply_tool_filter`` (a later extend is invisible to the help tool or
  BYPASSES the privileged strip);
* (f) the DECORATOR ORDER is ``register_tool_category`` ABOVE ``tool``;
* (g) the COLD-BOOT wiring — ``daemon/loader.py`` carries the warm
  import, the metadata-scan stub AND the flatten entry (without them the
  category is missing from the rendered prompt until some instance build
  repopulates the metadata — the W1-FIX-NOW class);
* (h) the committed tool-category ARTIFACT carries the category (the
  drift check itself lives in ``tests/unit/tools/test_tool_category_artifact.py``
  — the F-1 trap: a stale committed artifact regenerates as net-new reds
  in every later gate);
* (i) **AM-10** — the budget key is the agent TYPE, never the instance
  id: the factory captures the type, the extend threads the type, and the
  persisted row carries the TYPE in ``agent_id`` and the instance id in
  ``instance_id``;
* (j) the REFUSAL LADDER is typed exactly — ``tool-disabled`` (config
  absent / kill-switch off / AM-16 deny list / unmounted substrate) and
  ``invalid-input`` (empty description / out-of-set severity / non-string
  argument) — in the house envelope shape, and the ``invalid-input`` legs
  persist NOTHING;
* (k) the SUCCESS ENVELOPE parses field-per-line with the FIXED field
  set — the ``category_fold_notice`` line appears iff a fold happened and
  the ``truncated=true`` note appears iff a field was capped;
* (l) the EMPTY-ALLOW universe inherits the tool (R4) while an EXPLICIT
  allow list that does not name the category does not (until the phase-5
  grant ceremony);
* (m) the CATEGORY_DOC carries every disclosure the lane's checklist
  demands (the refusal ladder, the AM-11 split + the sole-defence
  statement, the AM-10 per-TYPE budget, the AM-14 offload pressure, the
  AM-16 conditional default-ON, the N2 bound hierarchy, the N3
  concurrency caveat, the C3 shed note, the closed vocabulary and the
  severity tiers).

The scratch-POSTGRESQL leg builds its OWN uniquely-named scratch database
over TCP with the superuser role and DROPS it at teardown.  It never
touches a live database: the name is random and the guard refuses any
target whose host is not loopback.  The ``POSTGRES_*`` / ``DATABASE_URL``
variables are NEVER read — the coordinates come from ``PG_TEST_*`` only.

SAFETY FENCE: no HOME-derived or live-shaped path is ever resolved by
this file — every read is a repo-relative source read.
"""

from __future__ import annotations

import ast
import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel, select

from daemon.config import NestReportConfig
from daemon.repositories.anomaly.models import AnomalyCluster, AnomalyReport
from daemon.services.anomaly_service import AnomalyService
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)
from daemon.tools.instance import _apply_tool_filter, resolve_tool_filter

REPO_ROOT = Path(__file__).resolve().parents[2]

CATEGORY = "nest-report"
MODULE = "daemon.tools.nest_report"
TOOL_NAME = "report_anomaly"

#: The house refusal envelope (the shape every category uses).
REFUSAL_TEMPLATE = "Error: {label} REFUSED — reason={token}: {message}"


def _module_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "nest_report.py").read_text(
        encoding="utf-8"
    )


def _instance_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
        encoding="utf-8"
    )


def _loader_source() -> str:
    return (REPO_ROOT / "daemon" / "loader.py").read_text(encoding="utf-8")


# ── the stub manager + the scratch engine ──────────────────────────────────


class _StubInstanceRepo:
    def get_ancestor_ids(self, instance_id: str) -> list[str]:
        return []

    def get_agent_ids_for(self, instance_ids: list[str]) -> dict[str, str | None]:
        return {iid: "" for iid in instance_ids}


class _StubTaskRepo:
    def find_running_by_instance(self, instance_id: str):
        return None


class _StubJobRepo:
    def get_active_by_instance(self, instance_id: str, job_id: str | None = None):
        return None


class _StubMissionRepo:
    def find_outstanding_commission_for_worker(self, instance_id: str, **kwargs):
        return None


class _StubWedgeRepo:
    def list_recent(self, limit: int = 50):
        return []


class _StubManager:
    """A manager stub carrying the two handles the tool reads PER CALL.

    ``config.nest_report`` is the operator's knob set; ``_anomaly_service``
    is the substrate.  A degraded boot omits one or both — the tool then
    refuses ``tool-disabled`` and NEVER vanishes.
    """

    def __init__(
        self,
        config: Any = None,
        service: Any = None,
        *,
        with_config: bool = True,
        with_service: bool = True,
    ) -> None:
        if with_config:
            self.config = type(
                "_ConfigStub",
                (),
                {"nest_report": config if config is not None else NestReportConfig()},
            )()
        if with_service:
            self._anomaly_service = service
        self.checkpointer = None
        self.metrics_query_provider = None
        self._instance_repository = _StubInstanceRepo()
        self._task_repo = _StubTaskRepo()
        self._job_queue_service = type(
            "_Jqs", (), {"repository": _StubJobRepo()}
        )()
        self._mission_record_service = type(
            "_Mrs", (), {"commission_repository": lambda self=None: _StubMissionRepo()}
        )()
        self._wedge_capture_repository = _StubWedgeRepo()


async def _passthrough(fn, *args, **kwargs):
    """The offload seam replaced by a direct call (no executor)."""
    return fn(*args, **kwargs)


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'anomaly_registration.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
    )
    return engine


def _service(engine, manager: Any) -> AnomalyService:
    return AnomalyService(
        engine,
        config=NestReportConfig(),
        manager=manager,
        offload=_passthrough,
    )


def _tools(manager: Any, *, agent_id: str = "worker", instance_id: str = "inst-1"):
    from daemon.tools.nest_report import create_nest_report_tools

    return create_nest_report_tools(
        manager,
        current_instance_id=instance_id,
        agent_id=agent_id,
        agent_tag=None,
    )


def _by_name(tools: list) -> dict[str, Any]:
    return {getattr(t, "name", ""): t for t in tools}


def _row(engine, report_id: str) -> AnomalyReport:
    with Session(engine) as session:
        row = session.get(AnomalyReport, report_id)
        assert row is not None
        session.expunge(row)
        return row


def _parse_envelope(envelope: str) -> dict[str, str]:
    """Parse the field-per-line envelope into a dict."""
    lines = envelope.splitlines()
    assert lines[0] == "OK: anomaly accepted", envelope
    parsed: dict[str, str] = {}
    for line in lines[1:]:
        key, _, value = line.partition("=")
        parsed[key] = value
    return parsed


# ── the static registration points ─────────────────────────────────────────


class TestStaticRegistration:
    def test_category_modules_entry(self) -> None:
        """``CATEGORY_MODULES`` maps the category to its module, and the
        value is a ``daemon.tools.*`` path (the AST scanner resolves a
        value as ``daemon/tools/<parts[2:]>.py``)."""
        assert CATEGORY_MODULES.get(CATEGORY) == MODULE
        value = CATEGORY_MODULES.get(CATEGORY)
        assert value.startswith("daemon.tools."), (
            f"{value} is not a daemon.tools.* path — a daemon.services.* "
            "value is never discovered by the AST scanner and the "
            "drift pin fails at boot"
        )

    def test_ast_discovery_finds_the_tool(self) -> None:
        """The AST walker finds the factory-created ``@tool`` function.

        Decorator-only registration is SILENTLY INVISIBLE — a missing
        module or a renamed function shrinks the discovered universe
        without any error.
        """
        discovered = discover_source_only_tool_names()
        assert TOOL_NAME in discovered, (
            f"AST discovery missed {TOOL_NAME!r} — the tool would be "
            "invisible to the frozen-name drift pin"
        )

    def test_the_name_universes_carry_the_tool(self) -> None:
        assert TOOL_NAME in set(DYNAMIC_TOOL_NAMES)
        assert TOOL_NAME in set(KNOWN_TOOL_NAMES)

    def test_the_frozen_name_universe_has_no_drift(self) -> None:
        """The bidirectional drift detector — the same equality the
        ``test_frozen_tool_name_discovery`` pack asserts."""
        assert set(KNOWN_TOOL_NAMES) == discover_source_only_tool_names()

    def test_the_decorator_order_is_register_then_tool(self) -> None:
        """``@register_tool_category`` MUST sit ABOVE ``@tool``.

        Reversed, the category attribute lands on a different object than
        the tool and the category resolution FAILS OPEN.
        """
        tree = ast.parse(_module_source())
        found: dict[str, list[str]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != TOOL_NAME:
                continue
            found[node.name] = [
                (
                    d.id
                    if isinstance(d, ast.Name)
                    else (
                        d.func.id
                        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                        else "?"
                    )
                )
                for d in node.decorator_list
            ]
        assert set(found) == {TOOL_NAME}, f"missing tool def: {found}"
        assert found[TOOL_NAME] == ["register_tool_category", "tool"], (
            f"{TOOL_NAME} carries the decorators {found[TOOL_NAME]} — the "
            "order must be ('register_tool_category', 'tool')"
        )

    def test_the_construction_extend_is_present(self) -> None:
        source = _instance_source()
        assert "from .nest_report import create_nest_report_tools" in source
        assert "create_nest_report_tools(" in source

    def test_the_extend_precedes_the_filter_and_the_doc_scan(self) -> None:
        """The extend MUST precede ``create_help_tool`` /
        ``scan_tools_for_full_docs`` / ``_apply_tool_filter`` — a later
        extend either bypasses the privileged strip or is invisible to the
        help tool."""
        source = _instance_source()
        extend_idx = source.index("create_nest_report_tools(")
        help_idx = source.index("create_help_tool(tools")
        scan_idx = source.index("scan_tools_for_full_docs(tools)")
        filter_idx = source.index("_apply_tool_filter(tools, agent_id")
        assert extend_idx < help_idx, "the extend must precede create_help_tool"
        assert extend_idx < scan_idx, (
            "the extend must precede scan_tools_for_full_docs"
        )
        assert extend_idx < filter_idx, (
            "the extend must precede _apply_tool_filter"
        )


class TestColdBootWiring:
    """The loader's three legs — without them the category is missing
    from the rendered prompt until some instance build repopulates the
    metadata (the W1-FIX-NOW class)."""

    def test_the_warm_import_is_present(self) -> None:
        assert (
            "from .tools.nest_report import create_nest_report_tools"
            in _loader_source()
        )

    def test_the_metadata_scan_stub_is_present(self) -> None:
        assert "create_nest_report_tools(None)" in _loader_source()

    def test_the_flatten_entry_is_present(self) -> None:
        source = _loader_source()
        assert "nest_report_tools," in source

    def test_the_cold_boot_scan_registers_the_category(self) -> None:
        """The REAL cold-boot scan — the category lands in the metadata."""
        from daemon.loader import _ensure_tool_metadata_populated
        from daemon.tools import _tool_registry as registry

        _ensure_tool_metadata_populated()
        metadata = registry._tool_metadata.get(TOOL_NAME)
        assert metadata is not None, (
            f"{TOOL_NAME} is absent from the registry metadata — the "
            "cold-boot scan did not register the category"
        )
        assert metadata.get("category") == CATEGORY


class TestTheNonPrivilegedCategory:
    """R4 — the category is NOT privileged; the privileged set and its
    pin sites stay UNTOUCHED."""

    def test_the_category_is_not_privileged(self) -> None:
        assert CATEGORY not in PRIVILEGED_TOOL_CATEGORIES

    def test_the_privileged_set_is_unchanged_by_this_lane(self) -> None:
        """The seventeen-member privileged set — the nest-anomaly lane
        adds nothing to it (R4: the tool is a write surface, not an ops
        surface)."""
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 17
        assert "nest-report" not in PRIVILEGED_TOOL_CATEGORIES
        assert "nest-report-read" not in PRIVILEGED_TOOL_CATEGORIES


class TestTheArtifact:
    """The committed tool-category artifact carries the category.

    The DRIFT CHECK itself lives in
    ``tests/unit/tools/test_tool_category_artifact.py`` — this leg is the
    local proof that the committed artifact (regenerated by the house
    generator, never hand-edited) names the new category.
    """

    def test_the_committed_artifact_carries_the_category(self) -> None:
        from daemon.checkpoint_metrics import tool_category_artifact_path

        payload = json.loads(
            tool_category_artifact_path().read_text(encoding="utf-8")
        )
        assert CATEGORY in payload["categories"], (
            "the committed tool-category artifact does not carry "
            f"{CATEGORY!r} — regenerate it with the house generator"
        )
        assert payload["count"] == len(payload["categories"])

    def test_the_artifact_vocabulary_version_matches_the_marker(self) -> None:
        from daemon.checkpoint_metrics import (
            VOCABULARY_VERSION,
            tool_category_artifact_path,
        )

        payload = json.loads(
            tool_category_artifact_path().read_text(encoding="utf-8")
        )
        assert payload["vocabulary_version"] == VOCABULARY_VERSION


# ── AM-10: the budget key is the agent TYPE ───────────────────────────────


class TestTheBudgetKey:
    """AM-10 — the budget key is the agent TYPE, never the instance id."""

    def test_the_extend_threads_the_agent_type(self) -> None:
        """The construction call threads ``agent_id=agent_id`` — the
        caller's agent TYPE — and NEVER the instance id."""
        source = _instance_source()
        idx = source.index("create_nest_report_tools(")
        window = source[idx : idx + 400]
        assert "agent_id=agent_id," in window, (
            "the factory call must thread the caller's agent_id (the TYPE) "
            "— without it the budget key would be empty"
        )
        assert "agent_id=current_instance_id" not in window, (
            "the budget key must NEVER be the instance id (AM-10)"
        )

    async def test_the_persisted_row_carries_the_type_and_the_instance(
        self, tmp_path
    ) -> None:
        """The behavioural proof: the row's ``agent_id`` is the TYPE and
        the row's ``instance_id`` is the instance."""
        engine = _engine(tmp_path)
        manager = _StubManager()
        service = _service(engine, manager)
        manager._anomaly_service = service
        tools = _by_name(
            _tools(manager, agent_id="developer", instance_id="inst-42")
        )
        result = await tools[TOOL_NAME].ainvoke(
            {"category": "tool failure", "description": "the write path died"}
        )
        assert result.startswith("OK: anomaly accepted"), result
        report_id = _parse_envelope(result)["report_id"]
        row = _row(engine, report_id)
        assert row.agent_id == "developer", (
            "the budget key must be the agent TYPE — the row's agent_id "
            f"is {row.agent_id!r}"
        )
        assert row.instance_id == "inst-42"

    async def test_two_instances_of_one_type_share_the_budget_key(
        self, tmp_path
    ) -> None:
        """Two instances of ONE type write the SAME budget key — spawning
        does not multiply the budget."""
        engine = _engine(tmp_path)
        manager = _StubManager()
        manager._anomaly_service = _service(engine, manager)
        keys: list[str] = []
        for instance_id in ("inst-a", "inst-b"):
            tools = _by_name(
                _tools(manager, agent_id="worker", instance_id=instance_id)
            )
            result = await tools[TOOL_NAME].ainvoke(
                {"category": "performance", "description": "slow"}
            )
            row = _row(engine, _parse_envelope(result)["report_id"])
            keys.append(row.agent_id)
        assert keys == ["worker", "worker"]


# ── the refusal ladder ────────────────────────────────────────────────────


class TestTheRefusalLadder:
    async def test_an_absent_config_is_tool_disabled(self) -> None:
        tools = _by_name(_tools(_StubManager(with_config=False)))
        result = await tools[TOOL_NAME].ainvoke(
            {"category": "performance", "description": "slow"}
        )
        assert result.startswith(
            f"Error: {TOOL_NAME} REFUSED — reason=tool-disabled: "
        ), result
        assert REFUSAL_TEMPLATE.startswith("Error: "), REFUSAL_TEMPLATE

    async def test_the_kill_switch_is_tool_disabled(self) -> None:
        manager = _StubManager(config=NestReportConfig(enabled=False))
        tools = _by_name(_tools(manager))
        result = await tools[TOOL_NAME].ainvoke(
            {"category": "performance", "description": "slow"}
        )
        assert "reason=tool-disabled" in result, result
        assert "ENSEMBLE_NEST_REPORT_ENABLED" in result

    async def test_the_deny_list_refuses_that_writer_only(self) -> None:
        """AM-16 — a listed writer is refused; a NON-listed writer in the
        same boot is unaffected."""
        config = NestReportConfig(disabled_agents="worker")
        manager = _StubManager(config=config, with_service=False)

        listed = _by_name(_tools(manager, agent_id="worker"))
        refused = await listed[TOOL_NAME].ainvoke(
            {"category": "performance", "description": "slow"}
        )
        assert "reason=tool-disabled" in refused, refused
        assert "ENSEMBLE_NEST_REPORT_DISABLED_AGENTS" in refused

        # A writer that is NOT on the list reaches the next rung (here:
        # the unmounted substrate) — the refusal reason MOVES, proving the
        # deny list is per-writer and not a global kill.
        other = _by_name(_tools(manager, agent_id="coder"))
        moved = await other[TOOL_NAME].ainvoke(
            {"category": "performance", "description": "slow"}
        )
        assert "reason=tool-disabled" in moved
        assert "ENSEMBLE_NEST_REPORT_DISABLED_AGENTS" not in moved, (
            "a non-listed writer must not be refused by the deny list"
        )

    async def test_an_unmounted_substrate_is_tool_disabled(self) -> None:
        """A degraded boot leaves the tool PRESENT and refusing LOUDLY
        (the metrics-read RULING-1 shape) — never absent, never an
        exception."""
        manager = _StubManager(with_service=False)
        tools = _by_name(_tools(manager))
        assert len(tools) == 1, "the tool must stay PRESENT on a degraded boot"
        result = await tools[TOOL_NAME].ainvoke(
            {"category": "performance", "description": "slow"}
        )
        assert "reason=tool-disabled" in result, result
        assert "Traceback" not in result, "a refusal is never an exception"

    async def test_an_empty_description_is_invalid_input(self, tmp_path) -> None:
        engine = _engine(tmp_path)
        manager = _StubManager()
        manager._anomaly_service = _service(engine, manager)
        tools = _by_name(_tools(manager))
        result = await tools[TOOL_NAME].ainvoke(
            {"category": "performance", "description": "   "}
        )
        assert "reason=invalid-input" in result, result
        with Session(engine) as session:
            rows = session.exec(select(AnomalyReport)).all()
        assert rows == [], "an invalid-input refusal persists NOTHING"

    async def test_an_out_of_set_severity_is_invalid_input(self, tmp_path) -> None:
        engine = _engine(tmp_path)
        manager = _StubManager()
        manager._anomaly_service = _service(engine, manager)
        tools = _by_name(_tools(manager))
        result = await tools[TOOL_NAME].ainvoke(
            {
                "category": "performance",
                "description": "slow",
                "severity": "catastrophic",
            }
        )
        assert "reason=invalid-input" in result, result
        assert "minor|major|critical" in result, result
        with Session(engine) as session:
            rows = session.exec(select(AnomalyReport)).all()
        assert rows == [], "an invalid-input refusal persists NOTHING"

    async def test_a_non_string_severity_is_invalid_input(self, tmp_path) -> None:
        """The BODY's own non-string guard.

        The tool's pydantic schema rejects a non-string severity BEFORE
        the body runs (the framework is the first line of defence), so
        this leg calls the underlying coroutine directly to prove the
        body refuses rather than raising.
        """
        engine = _engine(tmp_path)
        manager = _StubManager()
        manager._anomaly_service = _service(engine, manager)
        tool = _by_name(_tools(manager))[TOOL_NAME]
        body = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
        assert body is not None, "the tool carries no underlying function"
        result = await body(
            category="performance",
            description="slow",
            severity=3,
        )
        assert "reason=invalid-input" in result, result

    async def test_the_ladder_order_is_tool_disabled_then_invalid_input(
        self, tmp_path
    ) -> None:
        """The kill-switch is checked BEFORE the input validation: a
        disabled tool refuses a bad input with ``tool-disabled`` (the
        ladder order is the contract)."""
        manager = _StubManager(config=NestReportConfig(enabled=False))
        tools = _by_name(_tools(manager))
        result = await tools[TOOL_NAME].ainvoke(
            {"category": "performance", "description": ""}
        )
        assert "reason=tool-disabled" in result, result


class TestTheForwardRefusalTokens:
    """The rungs C3 will enforce — the tool must already refuse them
    correctly-shaped if the SERVICE raises them.

    The service signals a refusal by raising an exception that carries a
    ``token`` attribute in ``SERVICE_TOKENS``; an exception WITHOUT a
    recognised token is a persistence failure (``persist-failed``).
    """

    class _Refusal(Exception):
        def __init__(self, token: str) -> None:
            super().__init__(f"service refused: {token}")
            self.token = token

    class _RefusingService:
        def __init__(self, exc: BaseException | None) -> None:
            self._exc = exc

        async def accept_report(self, **kwargs):
            if self._exc is not None:
                raise self._exc
            raise AssertionError("the refusing stub never accepts")

    async def _invoke(self, exc: BaseException | None) -> str:
        manager = _StubManager()
        manager._anomaly_service = self._RefusingService(exc)
        tools = _by_name(_tools(manager))
        return await tools[TOOL_NAME].ainvoke(
            {"category": "performance", "description": "slow"}
        )

    async def test_a_rate_limited_service_refusal_is_typed(self) -> None:
        result = await self._invoke(self._Refusal("rate-limited"))
        assert result.startswith(
            "Error: report_anomaly REFUSED — reason=rate-limited: "
        ), result

    async def test_a_capacity_exhausted_service_refusal_is_typed(self) -> None:
        result = await self._invoke(self._Refusal("capacity-exhausted"))
        assert result.startswith(
            "Error: report_anomaly REFUSED — reason=capacity-exhausted: "
        ), result

    async def test_an_unknown_exception_is_a_persist_failure(self) -> None:
        """An exception WITHOUT a recognised token is a persistence
        failure — never a crash out of the tool boundary."""
        result = await self._invoke(RuntimeError("the INSERT died"))
        assert result.startswith(
            "Error: report_anomaly REFUSED — reason=persist-failed: "
        ), result
        assert "Traceback" not in result

    async def test_an_unrecognised_token_is_a_persist_failure(self) -> None:
        """A token outside ``SERVICE_TOKENS`` does not leak into the
        envelope vocabulary."""
        result = await self._invoke(self._Refusal("not-a-token"))
        assert result.startswith(
            "Error: report_anomaly REFUSED — reason=persist-failed: "
        ), result


# ── the success envelope ──────────────────────────────────────────────────


class TestTheSuccessEnvelope:
    async def test_the_envelope_carries_the_fixed_field_set(self, tmp_path) -> None:
        engine = _engine(tmp_path)
        manager = _StubManager()
        manager._anomaly_service = _service(engine, manager)
        tools = _by_name(_tools(manager))
        result = await tools[TOOL_NAME].ainvoke(
            {"category": "performance", "description": "slow"}
        )
        assert result.startswith("OK: anomaly accepted"), result
        parsed = _parse_envelope(result)
        assert set(parsed) == {
            "report_id",
            "cluster_id",
            "cluster_report_count",
            "cluster_first_report_at",
        }, parsed
        assert parsed["report_id"]
        # C3 — the envelope carries the REAL cluster identity (the
        # phase-2 flip; the degenerate ``pending-phase-2`` tail is gone
        # and the tool file itself is UNTOUCHED).
        assert parsed["cluster_id"] != "pending-phase-2", (
            "phase 2 replaced the degenerate cluster tail"
        )
        assert len(parsed["cluster_id"]) == 36, (
            "the cluster id is a uuid"
        )
        assert parsed["cluster_report_count"] == "1"
        assert parsed["cluster_first_report_at"].endswith("+00:00")
        assert parsed["cluster_first_report_at"] == _row(
            engine, parsed["report_id"]
        ).created_at

    async def test_the_fold_notice_appears_iff_a_fold_happened(
        self, tmp_path
    ) -> None:
        engine = _engine(tmp_path)
        manager = _StubManager()
        manager._anomaly_service = _service(engine, manager)
        tools = _by_name(_tools(manager))

        folded = await tools[TOOL_NAME].ainvoke(
            {"category": "tool failure", "description": "the write path died"}
        )
        parsed = _parse_envelope(folded)
        assert parsed["category_fold_notice"] == (
            'category "tool failure" folded to "tool-failure"'
        ), folded

        canonical = await tools[TOOL_NAME].ainvoke(
            {"category": "performance", "description": "slow"}
        )
        assert "category_fold_notice" not in _parse_envelope(canonical), (
            "an already-canonical category carries NO fold notice"
        )

    async def test_an_over_length_description_truncates_and_notes(
        self, tmp_path
    ) -> None:
        """AC1 — an over-length input TRUNCATES (never refuses) and the
        envelope says so."""
        engine = _engine(tmp_path)
        manager = _StubManager()
        manager._anomaly_service = _service(engine, manager)
        tools = _by_name(_tools(manager))
        result = await tools[TOOL_NAME].ainvoke(
            {"category": "performance", "description": "x" * 5000}
        )
        parsed = _parse_envelope(result)
        assert parsed.get("truncated") == "true", result
        row = _row(engine, parsed["report_id"])
        assert len(row.description) <= 1000

    async def test_an_over_length_category_is_capped_at_64(self, tmp_path) -> None:
        """AC1 — the raw category is capped at 64 chars and the envelope
        says the field was capped."""
        engine = _engine(tmp_path)
        manager = _StubManager()
        manager._anomaly_service = _service(engine, manager)
        tools = _by_name(_tools(manager))
        long_category = "y" * 200
        result = await tools[TOOL_NAME].ainvoke(
            {"category": long_category, "description": "odd category"}
        )
        parsed = _parse_envelope(result)
        assert parsed.get("truncated") == "true", result
        row = _row(engine, parsed["report_id"])
        assert len(row.category_raw) == 64
        assert row.category_raw == long_category[:64]


# ── the allow-list posture ────────────────────────────────────────────────


class TestTheAllowListPosture:
    def test_the_empty_allow_universe_inherits_the_tool(self) -> None:
        """R4 — the category is NOT privileged, so an agent with an EMPTY
        allow list inherits the tool through the default universe."""
        categories = {
            CATEGORY: [TOOL_NAME],
            "metrics-read": ["metrics_families"],
        }
        allowed = resolve_tool_filter(
            allow=[],
            deny=["bash"],
            tool_categories=categories,
            all_tool_names={TOOL_NAME, "metrics_families"},
        )
        assert allowed is not None
        assert TOOL_NAME in allowed, (
            "an empty-allow agent must inherit the nest-report tool"
        )
        assert "metrics_families" not in allowed, (
            "a privileged category must NOT join the empty-allow universe"
        )

    def test_an_explicit_allow_without_the_category_excludes_the_tool(
        self,
    ) -> None:
        """Until the phase-5 grant ceremony, an agent with an EXPLICIT
        allow list does not receive the tool."""
        categories = {
            CATEGORY: [TOOL_NAME],
            "bash": ["bash"],
        }
        allowed = resolve_tool_filter(
            allow=["bash"],
            deny=[],
            tool_categories=categories,
            all_tool_names={TOOL_NAME, "bash"},
        )
        assert allowed is not None
        assert TOOL_NAME not in allowed

    def test_the_real_filter_keeps_the_non_privileged_tool(self) -> None:
        """Through the REAL filter, an agent with no meta keeps the
        non-privileged tool (the privileged strip does not touch it)."""

        class _FakeTool:
            def __init__(self, name: str) -> None:
                self.name = name
                self._tool_category = CATEGORY

        filtered = _apply_tool_filter(
            [_FakeTool(TOOL_NAME)], "synthetic-empty-allow-agent"
        )
        assert [getattr(t, "name", "") for t in filtered] == [TOOL_NAME]

    def test_no_agent_holds_the_category_yet(self) -> None:
        """The grant ceremony is phase 5 — NO in-repo ``meta.json``
        names the category yet (C2 touches no ``agents/**`` file)."""
        holders = []
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = meta.get("tools", {}).get("allow", [])
            if CATEGORY in allow:
                holders.append(meta_path.parent.name)
        assert holders == [], (
            f"the grant ceremony is phase 5 — {holders} already hold the "
            "category"
        )


# ── the category documentation ────────────────────────────────────────────


class TestTheCategoryDoc:
    """The checklist's step 1 demands a CATEGORY_DOC that carries every
    disclosure — an agent reads it, so a dropped disclosure is a silent
    loss."""

    @pytest.fixture(scope="class")
    def doc(self) -> str:
        from daemon.tools.nest_report import CATEGORY_DOC

        return " ".join(CATEGORY_DOC.split())

    def test_the_purpose_is_stated(self, doc: str) -> None:
        assert "report_anomaly" in doc
        assert "one row" in doc.lower()

    def test_the_refusal_ladder_is_stated(self, doc: str) -> None:
        for token in (
            "tool-disabled",
            "invalid-input",
            "rate-limited",
            "capacity-exhausted",
            "persist-failed",
        ):
            assert token in doc, token

    def test_the_am11_split_disclosure_is_stated(self, doc: str) -> None:
        """The excerpt is FAIL-CLOSED on a scrubber-absent boot; the
        description / suspected_cause stay FAIL-OPEN."""
        assert "FAIL-CLOSED" in doc
        assert "FAIL-OPEN" in doc
        assert "excerpt" in doc
        assert "description" in doc
        assert "suspected_cause" in doc

    def test_the_sole_defence_statement_is_stated(self, doc: str) -> None:
        """C1c — for a NON-STORED secret the pattern masker is the ONLY
        defence; scrubber presence is irrelevant to that class."""
        assert "PATTERN MASKER IS THE ONLY DEFENCE" in doc
        assert "NON-STORED" in doc

    def test_the_am10_budget_semantics_are_stated(self, doc: str) -> None:
        assert "SPAWNING DOES NOT MULTIPLY THE BUDGET" in doc
        assert "INSTANCES SHARE ONE BUDGET" in doc
        assert "KEY SPACE IS NOT AGENT-EXPANDABLE" in doc
        assert "TYPE" in doc

    def test_the_am14_offload_pressure_is_stated(self, doc: str) -> None:
        assert "SEVEN off-loop database round trips" in doc
        assert "25-slot strict-admission executor" in doc
        assert "NULL" in doc

    def test_the_am16_conditional_default_on_is_stated(self, doc: str) -> None:
        assert "DEFAULT-ON POSTURE IS CONDITIONAL" in doc
        assert "default-OFF" in doc
        assert "ENSEMBLE_NEST_REPORT_DISABLED_AGENTS" in doc

    def test_the_n2_bound_hierarchy_is_stated(self, doc: str) -> None:
        assert "SHEDS" in doc
        assert "nest-wide wall" in doc
        assert "OPERATOR BRAKE, not a security fence" in doc
        assert "INNER brake" in doc

    def test_the_n3_concurrency_caveat_is_stated(self, doc: str) -> None:
        assert "BEST-EFFORT under concurrency" in doc
        assert "read-then-insert" in doc
        assert "NOT a hard invariant" in doc

    def test_the_c3_shed_note_is_stated(self, doc: str) -> None:
        assert "never refuses" in doc.lower()
        assert "one tree can never go dark" in doc
        assert "board item" in doc

    def test_the_closed_vocabulary_is_stated(self, doc: str) -> None:
        assert "CLOSED canonical vocabulary" in doc
        for category in (
            "tool-failure",
            "performance",
            "resource-exhaustion",
            "loop-stall",
            "wedge-suspected",
            "data-corruption",
            "auth-permission",
            "external-dependency",
            "mcp-failure",
            "llm-provider",
            "queue-scheduling",
            "environment-config",
        ):
            assert category in doc, category

    def test_the_severity_tiers_are_stated(self, doc: str) -> None:
        assert "minor" in doc
        assert "major" in doc
        assert "critical" in doc
        assert "never" in doc.lower()

    def test_the_shipped_strings_carry_no_defect_attribution(self) -> None:
        """The shipped strings assert the runtime-verifiable facts and
        never name a defect (a shipped string that names one goes stale
        the moment the owning lane lands)."""
        source = _module_source()
        for token in ("R-i", "R-ii", "capture_active", "census inverted"):
            assert token not in source, (
                f"the shipped strings must not attribute a cause — {token!r} "
                "must be absent from nest_report.py"
            )


# ── the scratch-POSTGRESQL leg ────────────────────────────────────────────


class TestTheScratchPostgresLeg:
    """AC3 — the accept path on a REAL PostgreSQL scratch database.

    The house PG recipe (``tests/postgres/conftest.py``): the coordinates
    come from the ``PG_TEST_*`` env vars ONLY (``POSTGRES_*`` /
    ``DATABASE_URL`` are NEVER read), the target must be a SANCTIONED
    SCRATCH database, and the live databases (``ensemble_prod`` /
    ``ensemble_dev`` / ``ensemble_prod_test``) are forbidden outright.
    """

    async def test_the_accept_path_works_on_postgresql(self) -> None:
        import os

        host = os.environ.get("PG_TEST_HOST", "localhost")
        port = os.environ.get("PG_TEST_PORT", "5432")
        user = os.environ.get("PG_TEST_USER", "postgres")
        password = os.environ.get("PG_TEST_PASSWORD", "postgres")

        if host not in {"localhost", "127.0.0.1", "::1"}:
            pytest.skip(
                f"the scratch-PG leg only runs against a loopback server "
                f"(PG_TEST_HOST={host!r} is not loopback — it may be live)"
            )

        admin_url = (
            f"postgresql+psycopg://{user}:{password}@{host}:{port}/postgres"
        )
        scratch_name = f"ensemble_anomaly_c2_{uuid.uuid4().hex[:12]}"
        assert scratch_name not in {
            "ensemble_prod",
            "ensemble_dev",
            "ensemble_prod_test",
        }
        admin = create_engine(admin_url, pool_pre_ping=True, future=True)
        try:
            with admin.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(text(f'CREATE DATABASE "{scratch_name}"'))
        except Exception as exc:  # noqa: BLE001 — skip-cleanly
            pytest.skip(f"no scratch database could be created: {exc}")
        scratch_url = (
            f"postgresql+psycopg://{user}:{password}@{host}:{port}/{scratch_name}"
        )
        engine = create_engine(scratch_url, pool_pre_ping=True, future=True)
        try:
            SQLModel.metadata.create_all(
                engine,
                tables=[AnomalyReport.__table__, AnomalyCluster.__table__],
            )
            manager = _StubManager()
            manager._anomaly_service = _service(engine, manager)
            tools = _by_name(_tools(manager, agent_id="worker"))
            result = await tools[TOOL_NAME].ainvoke(
                {"category": "tool failure", "description": "the write died"}
            )
            assert result.startswith("OK: anomaly accepted"), result
            parsed = _parse_envelope(result)
            row = _row(engine, parsed["report_id"])
            assert row.agent_id == "worker"
            assert row.category == "tool-failure"
        finally:
            engine.dispose()
            with admin.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}"'))
            admin.dispose()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
