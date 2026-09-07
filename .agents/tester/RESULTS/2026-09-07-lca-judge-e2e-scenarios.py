"""INDEPENDENT E2E verification — LCA inline-LLM completion-report judge.

Jobs 2 (merge gate) of 6 — this module proves the judge semantics end-to-end
through the REAL ``build_instance_graph`` + the REAL gate node + the REAL
attestation_judge_resolver, with ONLY the LLM seam scripted:

* the leader / child instance rows are real ``SQLModelInstanceRepository``
  rows (file-backed SQLite + WAL + NullPool — the prior-gate harness recipe);
* ``daemon.services.attestation_report_judge._invoke_judge_llm`` is the
  seam — it is the function the gate's async entry point
  ``judge_completion_report_async`` delegates to; we stub THAT seam and
  attach a call recorder so per-scenario counts and inputs are observable;
* ``daemon.graph.build_instance_llms`` is patched to install a
  ``ScriptedChatModel`` (the prior-gate pattern — no real LLM sockets).

Five scenarios (per the Job 2 deliverable):

* (a) Judge-YES → ``decision=allowed``, instance COMPLETES with NO further
  toolcall; nudge list empty; denied-counter NOT incremented (read before
  AND after); no marker writes; judge log row carries model + latency_ms
  fields (``llm_judge_model=``, ``llm_judge_latency_ms=``).
* (b) Judge-NO → ``decision=denied`` + nudge delivered. Delivered nudge
  content equals the imported ``ATTESTATION_NUDGE_TEXT`` constant, the
  header AND the mermaid ``ReportJudge`` node are present. Continue →
  leader attests → ``decision=allowed`` with ``attestation_present=True``.
* (c) Judge failure triad — EACH falls back to deny+nudge:
  (i) timeout (raises ``asyncio.TimeoutError`` — the class the real 3-layer
  timeout path produces), (ii) HTTP error (raises ``openai.APIStatusError``
  — the class the judge client actually raises), (iii) unparsable JSON
  (returns non-JSON garbage). For each: deny+nudge occurred AND the
  judge-error / judge-failure log row carries ``llm_judge_model``.
* (d) Kill-switch OFF via REAL env (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_
  ENABLED=0``) + cache reset → would-be-DENIED turn → judge stub call
  count == 0 (never invoked) AND straight deny+nudge (nudge delivered,
  counter incremented).
* (e) Judge NEVER called on the FOUR non-would-be-DENIED paths:
  (i) attested path, (ii) not-required path (no delegation → allowed,
  ``attestation_required=False``), (iii) pending-wakeup path
  (``allowed_legitimate_pending_wakeup``), (iv) terminal-after-bound path.

Sources grounding every expectation (read before writing this file):
``daemon/services/attestation_report_judge.py`` (judge invocation seam
``_invoke_judge_llm``; ``judge_completion_report_async``; ``JudgeResult``),
``daemon/services/attestation_judge_resolver.py`` (Pattern C cached-global
resolver + ``reset_llm_judge_resolver_for_tests``),
``daemon/graph.py`` (gate wiring, ``ATTESTATION_NUDGE_TEXT``,
``event=leader_completion_gate_judge`` log schema, ``judge-yes override``
log), ``daemon/services/attestation_gate.py`` (``evaluate``,
``build_gate_config``, ``Decision`` enum).

Pre-run invariant (per the sibling-evidence amendment): this file
must be the ONLY staged change in the worktree — only
``.agents/tester/RESULTS/2026-09-07-lca-judge-e2e-scenarios.py`` may be
created; daemon/ + tests/ are byte-identical to ``d6e30d9d``. The sibling
probe artifact ``.agents/tester/RESULTS/2026-09-07-lca-judge-live-probe.py``
is preserved untouched.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, func, select

# ─────────────────────────────────────────────────────────────────────────────
# Self-contained harnesses — the file is the ONLY artifact this turn creates,
# so all fixtures live inline (no ``tests/support/conftest.py`` dependency;
# ``pytest tests/.../X.py`` is auto-discovered as a standalone collection
# root, which would otherwise skip the support fixtures because the file
# sits in ``.agents/tester/RESULTS/``).
# ─────────────────────────────────────────────────────────────────────────────

# The conftest in tests/ installs lightweight langgraph stubs for unit
# tests. The gate-graph tests need the REAL langgraph modules — the
# prior-gate pattern evicts the stubs at fixture setup and restores
# them at teardown.
_LANGGRAPH_MOCK_KEYS = [
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.memory",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
]


def _evict_langgraph_mocks() -> dict:
    saved = {}
    for key in _LANGGRAPH_MOCK_KEYS:
        if key in sys.modules:
            saved[key] = sys.modules[key]
            del sys.modules[key]
    return saved


def _restore_langgraph_mocks(saved: dict) -> None:
    for key in _LANGGRAPH_MOCK_KEYS:
        if key in saved:
            sys.modules[key] = saved[key]


# ─────────────────────────────────────────────────────────────────────────────
# Imports that need the REAL langgraph modules — defer until after the
# fixture evicts the mocks. Exposed as module-level lazy imports via
# the local helpers below.
# ─────────────────────────────────────────────────────────────────────────────


def _real_graph_module():
    """Return the freshly-imported ``daemon.graph`` (real langgraph)."""
    return importlib.import_module("daemon.graph")


def _judge_module():
    return importlib.import_module("daemon.services.attestation_report_judge")


def _judge_resolver_module():
    return importlib.import_module("daemon.services.attestation_judge_resolver")


def _scripted_chat_model_cls():
    from tests.support.scripted_chat_model import ScriptedChatModel

    return ScriptedChatModel


# ─────────────────────────────────────────────────────────────────────────────
# Test-local tools (mirrors the prior-gate pattern — the conditional scanner
# matches the ``send_message`` tool NAME; this stub exists so the ToolNode
# can execute the scripted call without any messaging stack).
# ─────────────────────────────────────────────────────────────────────────────

LEADER_ID = "judge-e2e-leader"
CHILD_ID = "judge-e2e-child"


@tool
def attest_completion() -> dict:
    """Leader attestation tool wired into the ToolNode."""
    return {"attested": True, "timestamp": "2026-09-07T00:00:00+00:00"}


@tool
def send_message(target_instance_id: str, message: str) -> dict:
    """Test-local delegation stub — scanner matches the tool NAME."""
    return {"delivered": True, "target": target_instance_id}


# ─────────────────────────────────────────────────────────────────────────────
# Judge stub recorder — wraps ``_invoke_judge_llm`` and captures every call
# so per-scenario counts + inputs are observable.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _JudgeCall:
    """One captured invocation of the judge LLM seam."""

    config: Any
    user_payload: str
    timeout_s: float


class _JudgeStub:
    """Pluggable judge LLM stub.

    Replaces ``daemon.services.attestation_report_judge._invoke_judge_llm``
    via ``monkeypatch.setattr``. The judge call recorder is shared across
    all five scenarios — call count + inputs are checked at the end.

    Modes (set per scenario):

    * ``"yes"`` — returns ``{"is_complete_report": true, ...}`` (LLM-confirmed
      genuine report → judge-yes override).
    * ``"no"`` — returns ``{"is_complete_report": false, ...}`` (LLM-confirmed
      short/mid-work → deny+nudge fall-through).
    * ``"timeout"`` — raises ``asyncio.TimeoutError`` (the class the real
      3-layer timeout path produces; judge_co­mpletion_report_async
      catches it as the timeout verdict).
    * ``"http_error"`` — raises ``openai.APIStatusError`` (the class the
      judge client actually raises; converted to verdict="error").
    * ``"unparsable"`` — returns a non-JSON string (no fenced object); the
      parser returns None → verdict="unparsable".
    """

    def __init__(self) -> None:
        self.calls: list[_JudgeCall] = []
        self.mode: str = "yes"

    def install(self, monkeypatch: pytest.MonkeyPatch, judge_mod: Any) -> None:
        """Wire the stub onto ``judge_mod._invoke_judge_llm``."""
        self.calls = []
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", self._invoke)

    async def _invoke(self, config, user_payload, *, timeout_s):
        self.calls.append(
            _JudgeCall(
                config=config,
                user_payload=user_payload,
                timeout_s=timeout_s,
            )
        )
        if self.mode == "timeout":
            raise asyncio.TimeoutError()
        if self.mode == "http_error":
            import openai

            # The real exception constructor takes ``response=`` (a stub
            # request-response shaped object). We don't have an
            # httpx.Response here, so the keyword-only ``message`` form
            # raises a non-network-shape subclass that still inherits
            # ``openai.APIStatusError`` — exactly the class the judge
            # client produces on a 5xx.
            try:
                err = openai.APIStatusError(
                    "simulated 502 from upstream",
                    response=MagicMock(status_code=502),
                    body=None,
                )
                raise err
            except TypeError:
                # Older openai SDK — fall back to a bare subclass.
                class _HTTPError(openai.APIError):
                    pass

                raise _HTTPError("simulated 502 from upstream")
        if self.mode == "unparsable":
            # Non-JSON garbage. The ``_parse_judge_response`` strict-mode
            # regex fails (no ``{...}`` object) → ``None`` → verdict
            # "unparsable".
            return (
                "Sorry, I cannot help with that.",
                "fake-judge-unparsable",
            )
        if self.mode == "yes":
            return (
                '{"is_complete_report": true, "reason": "detailed '
                'outcomes, evidence, follow-ups"}',
                "fake-judge-yes",
            )
        if self.mode == "no":
            return (
                '{"is_complete_report": false, "reason": "short status '
                'update only"}',
                "fake-judge-no",
            )
        raise AssertionError(f"unknown judge stub mode: {self.mode!r}")

    @property
    def call_count(self) -> int:
        return len(self.calls)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — module-local; recreate the ``tests/support/conftest.py`` shape
# inline because the test file lives outside ``tests/``.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def real_graph_module():
    """Yield ``daemon.graph`` with REAL langgraph installed."""
    saved = _evict_langgraph_mocks()
    saved_daemon_graph = sys.modules.pop("daemon.graph", None)
    try:
        module = _real_graph_module()
        yield module
    finally:
        sys.modules.pop("daemon.graph", None)
        if saved_daemon_graph is not None:
            sys.modules["daemon.graph"] = saved_daemon_graph
        _restore_langgraph_mocks(saved)


@pytest.fixture
def file_sqlite_engine(tmp_path: Path):
    """File-backed SQLite at ``tmp_path`` — narrow prior-gate recipe.

    WAL + busy_timeout + NullPool. Schema: Instance, DependencyWatcher,
    MessageQueue, Task, Event (the prior-gate minimum).
    """
    from daemon.repositories.dependency_bus.models import DependencyWatcher
    from daemon.repositories.event.models import Event
    from daemon.repositories.instance.models import Instance
    from daemon.repositories.message_queue.models import MessageQueue
    from daemon.repositories.task.models import Task

    db_path = tmp_path / "judge_e2e.sqlite"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(
        engine,
        tables=[
            Instance.__table__,
            DependencyWatcher.__table__,
            MessageQueue.__table__,
            Task.__table__,
            Event.__table__,
        ],
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def memory_saver():
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


@pytest.fixture(autouse=True)
def _isolate_judge_kill_switch(monkeypatch):
    """Hermetic kill-switch isolation per the W2 punch-list.

    Clears ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`` so an outer
    ``.env`` / CI mutation cannot leak in and silently disable the judge
    for the assertions below. Tests that want the OFF posture explicitly
    set the env var after this fixture runs.
    """
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    # The Pattern C resolver caches on first read — wipe it on entry so
    # any env mutation in the test body re-resolves under the new env.
    from daemon.services import attestation_judge_resolver as _jrr  # noqa: WPS433

    _jrr.reset_llm_judge_resolver_for_tests()
    yield
    _jrr.reset_llm_judge_resolver_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — module-local
# ─────────────────────────────────────────────────────────────────────────────


def _repo(engine):
    from daemon.repositories.instance.repository import SQLModelInstanceRepository

    return SQLModelInstanceRepository(engine)


def _seed_leader_and_child(repo, *, child_status: str) -> None:
    """Leader + child instance rows (hierarchy junction created locally)."""
    from sqlmodel import SQLModel

    from daemon.repositories.instance.models import InstanceHierarchy

    SQLModel.metadata.create_all(repo.engine, tables=[InstanceHierarchy.__table__])
    repo.create(
        instance_id=LEADER_ID,
        agent_id="leader",
        agent_dir="./agents/leader",
    )
    repo.create(
        instance_id=CHILD_ID,
        agent_id="worker",
        agent_dir="./agents/worker",
        parent_id=LEADER_ID,
        status=child_status,
    )


def _make_manager(engine, repo):
    """Test-local manager exposing the production gate facades.

    The stub MUST expose ``count_live_descendants`` (third R2 input
    upstream added 2026-09-06 — without it the gate's DB-seam guard
    fails open and every deny assertion is unsatisfiable) and ``config``
    (the gate's judge wiring reads ``manager.config`` to resolve the
    judge model — exposing a minimal stub here avoids the real
    ``load_config()`` path).
    """

    class JudgeE2EManager:
        _instance_repository = repo

        def __init__(self) -> None:
            self.count_pending_children = MagicMock(
                side_effect=self._pending_children
            )
            self.get_queued_or_expected_wakeups = MagicMock(return_value=0)
            self.count_live_descendants = MagicMock(
                side_effect=self._live_descendants
            )
            self.enqueue_message = MagicMock(name="enqueue_message")
            # The gate's judge wiring does ``manager_config = getattr(manager,
            # "config", None)``. A MagicMock without a ``config`` attr would
            # auto-create one and short-circuit ``load_config()`` — the
            # stub here provides the minimum surface the judge needs
            # (``llm.model`` + ``llm.model_keywords``).
            self.config = _stub_judge_config()

        @staticmethod
        def _pending_children(target_instance_id: str) -> int:
            from daemon.repositories.dependency_bus.models import (
                DependencyWatcher,
                DependencyWatcherState,
            )

            with Session(engine) as session:
                return int(
                    session.scalar(
                        select(func.count())
                        .select_from(DependencyWatcher)
                        .where(
                            DependencyWatcher.target_instance_id
                            == target_instance_id,
                            DependencyWatcher.state
                            == DependencyWatcherState.PENDING.value,
                        )
                    )
                    or 0
                )

        @staticmethod
        def _live_descendants(target_instance_id: str) -> int:
            from daemon.repositories.instance.models import InstanceStatus

            terminal = {
                InstanceStatus.COMPLETED.value,
                InstanceStatus.TERMINATED.value,
                InstanceStatus.ERROR.value,
                InstanceStatus.FAILED.value,
            }
            count = 0
            for iid in repo.get_tree_ids_permanent(target_instance_id):
                if iid == target_instance_id:
                    continue
                row = repo.get(iid)
                if row is not None and row.status not in terminal:
                    count += 1
            return count

        @staticmethod
        def is_watchover_enabled(_iid: str) -> bool:
            return False

        @staticmethod
        def is_question_pause_requested(_iid: str) -> bool:
            return False

        @staticmethod
        def set_deferred_watchover_terminate(_iid: str) -> None:
            return None

    return JudgeE2EManager()


def _stub_judge_config():
    """Minimal ``Config``-shape stub for the judge wiring.

    The judge reads only ``llm.model_keywords`` (then ``llm.model`` via
    :func:`resolve_judge_model`). It NEVER touches request_timeout /
    base_url / api_key because we stub ``_invoke_judge_llm`` upstream of
    the LLM-client construction. Keep the surface tight to avoid
    accidentally exercising the real ``load_config()`` machinery.
    """
    cfg = MagicMock()
    llm = MagicMock()
    llm.model_keywords = ""
    llm.model = "scripted-judge-e2e"
    cfg.llm = llm
    return cfg


def _pin_enforce_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``mode=enforce`` + ``WATCHOVER_ENABLED=false`` via real env→resolver path."""
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")
    from daemon.services.attestation_gate import _reset_gate_settings_for_tests

    _reset_gate_settings_for_tests()


def _build_graph(real_graph_module, monkeypatch, model, manager):
    """Patch ONLY the LLM seam; resolver + gate + ledger all real."""
    monkeypatch.setattr(
        real_graph_module,
        "build_instance_llms",
        lambda **_: (model, model),
    )
    return real_graph_module.build_instance_graph(
        tools=[attest_completion, send_message],
        checkpointer=_memory_saver(),
        llm_config={"model": "scripted-judge-e2e", "api_key": "test"},
        system_prompt="judge independent e2e",
        user_language="Auto",
        language_check_enabled=False,
        manager=manager,
        graph_config={"configurable": {"thread_id": LEADER_ID}},
        attestation_enabled=True,
    )


def _memory_saver():
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


async def _run(graph, *input_messages):
    return await graph.ainvoke(
        {"messages": list(input_messages)},
        config={
            "configurable": {"thread_id": LEADER_ID},
            "recursion_limit": 40,
        },
    )


def _nudges(messages) -> list[HumanMessage]:
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and m.additional_kwargs.get("attestation_nudge")
    ]


def _gate_rows(caplog) -> list[str]:
    """Canonical gate decision log lines (one per evaluation)."""
    return [
        rec.getMessage()
        for rec in caplog.records
        if "event=leader_completion_gate decision=" in rec.getMessage()
    ]


def _judge_rows(caplog) -> list[str]:
    """Canonical judge log lines (one per judge invocation)."""
    return [
        rec.getMessage()
        for rec in caplog.records
        if "event=leader_completion_gate_judge" in rec.getMessage()
    ]


def _send_call(call_id: str) -> dict:
    return {
        "name": "send_message",
        "args": {
            "target_instance_id": CHILD_ID,
            "message": "handle the delegated subtask",
        },
        "id": call_id,
    }


def _attest_call(call_id: str) -> dict:
    return {"name": "attest_completion", "args": {}, "id": call_id}


# ─────────────────────────────────────────────────────────────────────────────
# (a) judge-yes → ALLOWED, no nudge, no counter increment; judge log row
#     carries model + latency.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_judge_yes_allows_no_nudge_no_counter_increment_log_carries_model_latency(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _make_manager(file_sqlite_engine, repo)

    # Pre-condition: counter starts at 0; record the value before the run.
    before_count = repo.get_attestation_denied_count(LEADER_ID)
    assert before_count == 0

    # Wire the judge stub — YES verdict for a genuine detailed final report.
    judge_stub = _JudgeStub()
    judge_stub.mode = "yes"
    judge_mod = _judge_module()
    judge_stub.install(monkeypatch, judge_mod)

    # Sanity: the judge kill-switch is ON under default env.
    judge_resolver = _judge_resolver_module()
    assert judge_resolver.is_llm_judge_enabled() is True

    # Mission: open with a real delegation (arms the conditional gate),
    # then the leader delivers a GENUINE DETAILED final report as its
    # last assistant message (the judge's YES verdict target).
    detailed_report = (
        "Final report — Q3 launch verification complete.\n"
        "Outcomes:\n"
        "  - Migration rolled forward; 0 regressions.\n"
        "  - Latency p95 dropped from 412ms to 287ms.\n"
        "  - All 12 acceptance criteria pass.\n"
        "Evidence:\n"
        "  - Run id #9821 (green), test report attached.\n"
        "  - Dashboard: https://internal/q3\n"
        "Follow-ups:\n"
        "  - File the post-mortem by Friday.\n"
        "  - Schedule a retro with infra team."
    )

    ScriptedChatModel = _scripted_chat_model_cls()
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Dispatching the verification child.",
                tool_calls=[_send_call("judge-a-send")],
            ),
            AIMessage(content=detailed_report),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    # Critical import — the gate text is injected verbatim. Pulled here
    # so the assertion is bound to the live daemon.graph constant.
    from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT

    with caplog.at_level(logging.INFO):
        state = await _run(
            graph,
            HumanMessage(
                content="Run the Q3 launch verification end-to-end."
            ),
        )

    # (i) Gate-decision row: DENIED — the gate's ``evaluate()`` logs the
    # decision BEFORE the judge runs. The judge-YES override flips the
    # actual routing to ALLOWED via early return; the canonical
    # decision=allowed row is NOT emitted (the override skips the rest
    # of the gate body). The denial was REJECTED at the judge step, not
    # by the deny fall-through. The judge-yes override log line IS the
    # operator signal that the END routing flipped on the judge path.
    rows = _gate_rows(caplog)
    assert len(rows) == 1
    assert "decision=denied" in rows[0]
    assert "attestation_required=True" in rows[0]
    assert "should_inject_nudge=True" in rows[0]

    # (ii) Judge row carries model + latency_ms fields.
    jrows = _judge_rows(caplog)
    assert len(jrows) == 1
    judge_row = jrows[0]
    assert "event=leader_completion_gate_judge" in judge_row
    assert "verdict=yes" in judge_row
    assert "llm_judge_model=fake-judge-yes" in judge_row
    assert "llm_judge_latency_ms=" in judge_row
    # Latency must be a non-negative integer.
    import re

    m = re.search(r"llm_judge_latency_ms=(\d+)", judge_row)
    assert m is not None
    assert int(m.group(1)) >= 0
    # The judge-yes override log line is the operator signal that the
    # END routing flipped on the judge path (not the deny path).
    assert "[AttestationGate] judge-yes override" in caplog.text
    assert "instance=judge-e2e-leader" in caplog.text

    # (iii) Stub invocation: ONE call captured; the user_payload is the
    # formatted AIMessage slice (the last AI message is the detailed
    # report we just emitted).
    assert judge_stub.call_count == 1
    captured = judge_stub.calls[0]
    assert "Final report" in captured.user_payload
    assert "Outcomes:" in captured.user_payload

    # (iv) NO nudge delivered — judge-yes skips the in-graph nudge
    # entirely. ZERO nudges in the message list.
    messages = state["messages"]
    assert _nudges(messages) == []
    # The state channel ``attestation_nudge_denied_count`` is written
    # ONLY on the deny path (it carries the post-increment count); the
    # judge-yes override returns ``{"attestation_route": None}`` so the
    # channel is absent from the state.
    assert "attestation_nudge_denied_count" not in state or (
        state.get("attestation_nudge_denied_count") in (None, 0)
    )

    # (v) Counter NOT incremented — judge-yes is not an attested-allow,
    # but it IS not a deny either; ruling 1's reset triggers do not
    # include judge-yes, and the increment path is gated on DENIED only.
    after_count = repo.get_attestation_denied_count(LEADER_ID)
    assert after_count == before_count == 0
    # No escalation flag flipped — judge-yes does not touch the gate's
    # escalation side.
    assert repo.get(LEADER_ID).completion_gate_escalated is False

    # (vi) No marker writes — judge-yes never enters the deny path, so
    # ``safe_increment`` and ``safe_set_escalated_and_reset`` are never
    # called. The leader instance row's lifecycle status is untouched.
    assert repo.get(LEADER_ID).status == "idle"

    # (vii) No further toolcall — the graph END'd on the judge-yes
    # override (the leader delivered its detailed final report as its
    # last AI turn; no more scripted turns are required). The
    # ScriptedChatModel's exhausted-state error would fire if the graph
    # requested one more LLM turn — the calls_made count pins the
    # expected end-state.
    assert model.calls_made == 2
    assert messages[-1].content == detailed_report
    manager.enqueue_message.assert_not_called()

    # (viii) Constant-parity sanity — the nudge text constant was NOT
    # injected (nudge list is empty); the daemon.graph constant is the
    # same string the deny path would inject in scenario (b).
    assert isinstance(NUDGE_TEXT, str)
    assert NUDGE_TEXT.startswith(
        "[SYSTEM CONTEXT: Completion Check Nudge]"
    )


# ─────────────────────────────────────────────────────────────────────────────
# (b) judge-no → deny + nudge; nudge content == ATTESTATION_NUDGE_TEXT
#     constant (header + mermaid flowchart including the ``ReportJudge``
#     node); continue → leader attests → ALLOWED.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_b_judge_no_denies_with_nudge_then_attest_allows(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _make_manager(file_sqlite_engine, repo)

    judge_stub = _JudgeStub()
    judge_stub.mode = "no"
    judge_mod = _judge_module()
    judge_stub.install(monkeypatch, judge_mod)

    # Mission: open with a real delegation; the leader's last message
    # is a short/mid-work text — the judge-NO target.
    short_text = (
        "All work complete — nothing pending."
    )

    ScriptedChatModel = _scripted_chat_model_cls()
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Dispatching the verification child.",
                tool_calls=[_send_call("judge-b-send")],
            ),
            AIMessage(content=short_text),
            # Post-nudge, SAME execution: attest + final prose.
            AIMessage(
                content="Report delivered above; attesting completion.",
                tool_calls=[_attest_call("judge-b-attest")],
            ),
            AIMessage(content="Mission complete and attested."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT

    with caplog.at_level(logging.INFO):
        state = await _run(
            graph,
            HumanMessage(
                content="Run the Q3 launch verification end-to-end."
            ),
        )

    # (i) Exactly ONE nudge in the message list — content matches the
    # imported server constant verbatim (constant-parity discipline).
    messages = state["messages"]
    nudges = _nudges(messages)
    assert len(nudges) == 1
    assert nudges[0].content == NUDGE_TEXT

    # (ii) Nudge carries the header AND a mermaid flowchart that
    # includes the ``ReportJudge`` node — pinned on BOTH the imported
    # constant AND the delivered copy (the gate injects the constant
    # verbatim so they must be byte-identical).
    for source in (NUDGE_TEXT, nudges[0].content):
        assert source.startswith(
            "[SYSTEM CONTEXT: Completion Check Nudge]"
        )
        assert "```mermaid" in source
        assert "flowchart TD" in source
        assert "ReportJudge" in source

    # The nudge kwargs carry the checkpoint-durable denied-count stamp.
    assert nudges[0].additional_kwargs == {
        "attestation_nudge": True,
        "attestation_nudge_denied_count": 1,
    }
    assert state["attestation_nudge_denied_count"] == 1

    # (iii) Gate decision rows: deny (required=True) THEN attested allow.
    rows = _gate_rows(caplog)
    assert len(rows) == 2
    assert "decision=denied" in rows[0]
    assert "attestation_required=True" in rows[0]
    assert "delegation_tool_call_total=1" in rows[0]
    assert "first_delegation_after_last_user_index=1" in rows[0]
    assert "decision=allowed" in rows[1]
    assert "attestation_present=True" in rows[1]

    # (iv) Judge row carries verdict=no + model + latency.
    jrows = _judge_rows(caplog)
    assert len(jrows) == 1
    judge_row = jrows[0]
    assert "verdict=no" in judge_row
    assert "llm_judge_model=fake-judge-no" in judge_row
    assert "llm_judge_latency_ms=" in judge_row

    # (v) Stub invocation captured the short text the judge rejected.
    assert judge_stub.call_count == 1
    assert short_text in judge_stub.calls[0].user_payload

    # (vi) SAME ainvoke: 4 scripted turns consumed; final prose is last.
    assert model.calls_made == 4
    assert messages[-1].content == "Mission complete and attested."

    # (vii) Counter RESET to 0 by the attested allow (ruling 1 trigger 1).
    assert repo.get_attestation_denied_count(LEADER_ID) == 0
    assert repo.get(LEADER_ID).completion_gate_escalated is False


# ─────────────────────────────────────────────────────────────────────────────
# (c) judge failure triad — (i) timeout, (ii) HTTP error, (iii) unparsable.
#     EACH falls back to deny+nudge; the judge-error log row carries
#     ``llm_judge_model``.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_c1_judge_timeout_falls_through_to_deny_nudge(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _make_manager(file_sqlite_engine, repo)

    judge_stub = _JudgeStub()
    judge_stub.mode = "timeout"
    judge_mod = _judge_module()
    judge_stub.install(monkeypatch, judge_mod)

    ScriptedChatModel = _scripted_chat_model_cls()
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Delegating now.",
                tool_calls=[_send_call("judge-c1-send")],
            ),
            AIMessage(content="All done; nothing pending."),
            AIMessage(
                content="Report delivered; attesting.",
                tool_calls=[_attest_call("judge-c1-attest")],
            ),
            AIMessage(content="Mission complete and attested."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT

    with caplog.at_level(logging.INFO):
        state = await _run(graph, HumanMessage(content="do the work"))

    messages = state["messages"]
    nudges = _nudges(messages)
    assert len(nudges) == 1
    assert nudges[0].content == NUDGE_TEXT
    assert nudges[0].additional_kwargs["attestation_nudge_denied_count"] == 1

    # Judge row: verdict=timeout, error_class=TimeoutError, model present.
    jrows = _judge_rows(caplog)
    assert len(jrows) == 1
    judge_row = jrows[0]
    assert "verdict=timeout" in judge_row
    assert "llm_judge_error_class=TimeoutError" in judge_row
    assert "llm_judge_model=" in judge_row
    # The judge row carries the resolved model — for the timeout path
    # the judge_completion_report_async handler still calls
    # ``resolve_judge_model(config)`` to stamp the row.
    assert "llm_judge_model=scripted-judge-e2e" in judge_row
    assert "llm_judge_latency_ms=" in judge_row

    assert judge_stub.call_count == 1
    assert repo.get_attestation_denied_count(LEADER_ID) == 0


@pytest.mark.asyncio
async def test_c2_judge_http_error_falls_through_to_deny_nudge(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _make_manager(file_sqlite_engine, repo)

    judge_stub = _JudgeStub()
    judge_stub.mode = "http_error"
    judge_mod = _judge_module()
    judge_stub.install(monkeypatch, judge_mod)

    ScriptedChatModel = _scripted_chat_model_cls()
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Delegating now.",
                tool_calls=[_send_call("judge-c2-send")],
            ),
            AIMessage(content="All done; nothing pending."),
            AIMessage(
                content="Report delivered; attesting.",
                tool_calls=[_attest_call("judge-c2-attest")],
            ),
            AIMessage(content="Mission complete and attested."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT

    with caplog.at_level(logging.INFO):
        state = await _run(graph, HumanMessage(content="do the work"))

    messages = state["messages"]
    nudges = _nudges(messages)
    assert len(nudges) == 1
    assert nudges[0].content == NUDGE_TEXT

    jrows = _judge_rows(caplog)
    assert len(jrows) == 1
    judge_row = jrows[0]
    assert "verdict=error" in judge_row
    # The judge-error class is the openai class the stub raised — the
    # gate's judge wraps ``Exception as exc`` and stamps ``error_class=
    # type(exc).__name__``. The exact class name depends on the openai
    # SDK version (``APIStatusError`` on modern SDKs; ``APIError`` on
    # older ones — the stub falls back to a subclass). Both are valid
    # as the judge-error log row.
    assert (
        "llm_judge_error_class=APIStatusError" in judge_row
        or "llm_judge_error_class=APIError" in judge_row
    )
    assert "llm_judge_model=" in judge_row
    assert "llm_judge_model=scripted-judge-e2e" in judge_row

    assert judge_stub.call_count == 1
    assert repo.get_attestation_denied_count(LEADER_ID) == 0


@pytest.mark.asyncio
async def test_c3_judge_unparsable_falls_through_to_deny_nudge(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _make_manager(file_sqlite_engine, repo)

    judge_stub = _JudgeStub()
    judge_stub.mode = "unparsable"
    judge_mod = _judge_module()
    judge_stub.install(monkeypatch, judge_mod)

    ScriptedChatModel = _scripted_chat_model_cls()
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Delegating now.",
                tool_calls=[_send_call("judge-c3-send")],
            ),
            AIMessage(content="All done; nothing pending."),
            AIMessage(
                content="Report delivered; attesting.",
                tool_calls=[_attest_call("judge-c3-attest")],
            ),
            AIMessage(content="Mission complete and attested."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT

    with caplog.at_level(logging.INFO):
        state = await _run(graph, HumanMessage(content="do the work"))

    messages = state["messages"]
    nudges = _nudges(messages)
    assert len(nudges) == 1
    assert nudges[0].content == NUDGE_TEXT

    jrows = _judge_rows(caplog)
    assert len(jrows) == 1
    judge_row = jrows[0]
    assert "verdict=unparsable" in judge_row
    assert "llm_judge_model=" in judge_row
    # On the unparsable path, the judge model is the one the LLM
    # actually served (the stub returns ``"fake-judge-unparsable"`` as
    # the second tuple element of ``_invoke_judge_llm``).
    assert "llm_judge_model=fake-judge-unparsable" in judge_row
    assert "llm_judge_latency_ms=" in judge_row
    assert "llm_judge_error_class=<none>" in judge_row

    assert judge_stub.call_count == 1
    assert repo.get_attestation_denied_count(LEADER_ID) == 0


# ─────────────────────────────────────────────────────────────────────────────
# (d) kill-switch OFF via REAL env + resolver cache reset → judge never
#     invoked AND straight deny+nudge on a would-be-DENIED turn.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_d_kill_switch_off_real_env_judge_never_invoked_straight_deny(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _make_manager(file_sqlite_engine, repo)

    judge_stub = _JudgeStub()
    judge_stub.mode = "yes"  # would return YES if invoked — proves
    # the kill-switch OFF path bypasses the call entirely.
    judge_mod = _judge_module()
    judge_stub.install(monkeypatch, judge_mod)

    # Set the kill-switch to OFF via the REAL env var + REAL resolver
    # cache reset (the flag is restart-read — flipping requires a reset
    # to bypass the cached-global). Mirrors the prior gate's
    # monkeypatched parser variant exactly: the env var mutation +
    # reset helper IS the "restart" semantics the resolver honors.
    monkeypatch.setenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0"
    )
    judge_resolver = _judge_resolver_module()
    judge_resolver.reset_llm_judge_resolver_for_tests()
    # Sanity: the real resolver returns False under the mutated env.
    assert judge_resolver.is_llm_judge_enabled() is False

    ScriptedChatModel = _scripted_chat_model_cls()
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Delegating now.",
                tool_calls=[_send_call("judge-d-send")],
            ),
            AIMessage(content="All done; nothing pending."),
            AIMessage(
                content="Report delivered; attesting.",
                tool_calls=[_attest_call("judge-d-attest")],
            ),
            AIMessage(content="Mission complete and attested."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT

    with caplog.at_level(logging.INFO):
        state = await _run(graph, HumanMessage(content="do the work"))

    messages = state["messages"]
    nudges = _nudges(messages)
    # Judge never invoked → straight deny+nudge path. ONE nudge.
    assert len(nudges) == 1
    assert nudges[0].content == NUDGE_TEXT
    assert nudges[0].additional_kwargs["attestation_nudge_denied_count"] == 1

    # Judge stub call count == 0 — the kill-switch short-circuits the
    # call before ``_invoke_judge_llm`` is reached.
    assert judge_stub.call_count == 0
    # NO judge log row — the canonical judge log fires only after a
    # call returns (the gate logs the row inside the ``if judge_result
    # is not None:`` branch).
    assert _judge_rows(caplog) == []

    # Gate decision rows: deny THEN attested allow (same shape as (b),
    # minus the judge log line).
    rows = _gate_rows(caplog)
    assert len(rows) == 2
    assert "decision=denied" in rows[0]
    assert "decision=allowed" in rows[1]
    assert "attestation_present=True" in rows[1]

    # Counter incremented (deny path) then reset (attested allow).
    assert repo.get_attestation_denied_count(LEADER_ID) == 0


# ─────────────────────────────────────────────────────────────────────────────
# (e) judge NEVER called on the FOUR non-would-be-DENIED paths:
#     (i) attested, (ii) not-required, (iii) pending-wakeup,
#     (iv) terminal-after-bound.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e1_judge_not_called_on_attested_path(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _make_manager(file_sqlite_engine, repo)

    judge_stub = _JudgeStub()
    judge_stub.mode = "yes"
    judge_mod = _judge_module()
    judge_stub.install(monkeypatch, judge_mod)

    ScriptedChatModel = _scripted_chat_model_cls()
    # Open with delegation; the SECOND AI turn ALREADY carries the
    # ``attest_completion`` tool call → attested allow → judge never
    # called.
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Delegating now.",
                tool_calls=[_send_call("judge-e1-send")],
            ),
            AIMessage(
                content="Report delivered above; attesting completion.",
                tool_calls=[_attest_call("judge-e1-attest")],
            ),
            AIMessage(content="Mission complete and attested."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(graph, HumanMessage(content="do the work"))

    # Judge never invoked — attested allow is not a would-be-DENIED path.
    assert judge_stub.call_count == 0
    assert _judge_rows(caplog) == []
    assert _nudges(state["messages"]) == []

    # Gate row: ALLOWED with attestation_present=True.
    rows = _gate_rows(caplog)
    assert len(rows) == 1
    assert "decision=allowed" in rows[0]
    assert "attestation_present=True" in rows[0]

    assert repo.get_attestation_denied_count(LEADER_ID) == 0


@pytest.mark.asyncio
async def test_e2_judge_not_called_on_not_required_path(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _make_manager(file_sqlite_engine, repo)

    judge_stub = _JudgeStub()
    judge_stub.mode = "yes"
    judge_mod = _judge_module()
    judge_stub.install(monkeypatch, judge_mod)

    ScriptedChatModel = _scripted_chat_model_cls()
    # No delegation tool call after the last real user message — the
    # conditional gate is OFF (``attestation_required=False``).
    model = ScriptedChatModel(
        responses=[
            AIMessage(content="The answer is 42."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(
            graph, HumanMessage(content="What is the meaning of life?")
        )

    # Judge never invoked — the conditional gate stays off.
    assert judge_stub.call_count == 0
    assert _judge_rows(caplog) == []
    assert _nudges(state["messages"]) == []

    rows = _gate_rows(caplog)
    assert len(rows) == 1
    assert "decision=allowed" in rows[0]
    assert "attestation_required=False" in rows[0]

    assert repo.get_attestation_denied_count(LEADER_ID) == 0


@pytest.mark.asyncio
async def test_e3_judge_not_called_on_pending_wakeup_path(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    # Child ALIVE — the parked-leader state.
    _seed_leader_and_child(repo, child_status="running")
    manager = _make_manager(file_sqlite_engine, repo)
    # Plant a real PENDING dependency_watchers row so the R2 read
    # returns 1 (the parked-leader signature).
    from daemon.repositories.dependency_bus.models import (
        DependencyWatcher,
        DependencyWatcherState,
    )

    with Session(file_sqlite_engine) as session:
        session.add(
            DependencyWatcher(
                source_task_id="judge-e3-source",
                target_instance_id=LEADER_ID,
                state=DependencyWatcherState.PENDING.value,
            )
        )
        session.commit()
    # Sanity: the R2 read sees 1 pending child.
    assert manager.count_pending_children(LEADER_ID) == 1

    judge_stub = _JudgeStub()
    judge_stub.mode = "yes"
    judge_mod = _judge_module()
    judge_stub.install(monkeypatch, judge_mod)

    ScriptedChatModel = _scripted_chat_model_cls()
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Dispatching to the child; will wait.",
                tool_calls=[_send_call("judge-e3-send")],
            ),
            AIMessage(content="Child is working; ending my turn."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(
            graph, HumanMessage(content="Delegate and wait for the child.")
        )

    # Judge never invoked — the R2 allow path bypasses the gate's
    # would-be-deny block entirely.
    assert judge_stub.call_count == 0
    assert _judge_rows(caplog) == []
    assert _nudges(state["messages"]) == []

    rows = _gate_rows(caplog)
    assert len(rows) == 1
    assert "decision=allowed_legitimate_pending_wakeup" in rows[0]
    assert "attestation_required=True" in rows[0]
    # The PENDING watcher row is untouched on this path.
    with Session(file_sqlite_engine) as session:
        from sqlmodel import select as _select  # noqa: WPS433

        watcher = session.exec(
            _select(DependencyWatcher).where(
                DependencyWatcher.target_instance_id == LEADER_ID
            )
        ).one()
        assert watcher.state == DependencyWatcherState.PENDING.value

    # Counter unchanged — allowed_legitimate_pending_wakeup is the
    # ruling-1 non-reset path.
    assert repo.get_attestation_denied_count(LEADER_ID) == 0


@pytest.mark.asyncio
async def test_e4_judge_not_called_on_terminal_after_bound_path(
    real_graph_module, file_sqlite_engine, caplog, monkeypatch
):
    _pin_enforce_mode(monkeypatch)
    repo = _repo(file_sqlite_engine)
    _seed_leader_and_child(repo, child_status="completed")
    manager = _make_manager(file_sqlite_engine, repo)

    # Pre-seed the ledger at ``denied_count = bound = 3`` so the FIRST
    # deny attempt lands on the bound-exceeded branch immediately
    # (counter + 1 = 4 > 3 ⇒ TERMINAL_AFTER_BOUND). Each increment uses
    # a unique epoch — the O4 seen-epochs idempotency rejects duplicate
    # epochs with a no-op return.
    repo.increment_attestation_denied_count(LEADER_ID, "judge-e4-epoch-1")
    repo.increment_attestation_denied_count(LEADER_ID, "judge-e4-epoch-2")
    repo.increment_attestation_denied_count(LEADER_ID, "judge-e4-epoch-3")
    assert repo.get_attestation_denied_count(LEADER_ID) == 3

    judge_stub = _JudgeStub()
    judge_stub.mode = "yes"
    judge_mod = _judge_module()
    judge_stub.install(monkeypatch, judge_mod)

    ScriptedChatModel = _scripted_chat_model_cls()
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="Delegating now.",
                tool_calls=[_send_call("judge-e4-send")],
            ),
            AIMessage(content="All done; nothing pending."),
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, monkeypatch, model, manager)

    with caplog.at_level(logging.INFO):
        state = await _run(graph, HumanMessage(content="do the work"))

    # Judge never invoked — the deny path is the terminal_after_bound
    # branch, which does not enter the judge block (the judge sits on
    # ``if decision.decision is Decision.DENIED`` only; the bound-
    # exceeded branch produces TERMINAL_AFTER_BOUND).
    assert judge_stub.call_count == 0
    assert _judge_rows(caplog) == []

    rows = _gate_rows(caplog)
    assert len(rows) == 1
    # The bound-exceeded branch is distinct from DENIED — no nudge.
    assert "decision=terminal_after_bound" in rows[0]
    # The terminal escalation event is the operator signal.
    assert (
        "event=leader_completion_gate_terminal_after_bound" in caplog.text
    )
    # NO nudge was injected — the bound-exceeded branch is END-routing,
    # not deny-routing.
    assert _nudges(state["messages"]) == []

    # Ruling 2: the SAME reset op zeroed the counter AND set the flag.
    row = repo.get(LEADER_ID)
    assert row.attestation_denied_count == 0
    assert row.completion_gate_escalated is True
