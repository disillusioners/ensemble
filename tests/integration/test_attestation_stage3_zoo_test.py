"""LCA Stage-3 single-path E2E ZOO — Job 2 of the Stage-3 gate.

Stage-3 retired the legacy judge routes; the ONLY path is
``predicate -> conditional fused judge -> outcomes`` (resolver in
``daemon/services/attestation_resolver_activation.py``; fused block
in ``daemon/graph.py`` ~line 5225; ``judge_fused_bundle_async`` is
the one judge call site).

Stage-2 E2E artifacts (``incident_abc`` + ``_de`` + ``_budget_parity``)
were deleted in the adversarial-review round; their semantics were
partially absorbed by ``census`` (R1-R8 negative-pin suite) +
``TestLegacySitesDeleted``. Job 2 re-establishes the GAP: the full
child-lie lifecycle arc + the genuine-report rescue through the REAL
compiled graph under the Stage-3 single-path API.

Coverage map (8 scenarios):
  1. CHILD-LIE ARC — this file ``test_zoo_scenario_1_*`` (gap; no
     test currently drives 3 evals through the REAL compiled graph).
  2. GENUINE REPORT — this file ``test_zoo_scenario_2_*`` (gap;
     unit-level TestIncident98b59dd7GenuineReport drives the gate
     node directly, NOT through real graph).
  3-8. Covered by existing tests (cited in Stage-3 census docstring):
     awaiting-answer → ``test_attestation_user_answer_pending_lca.py``;
     idle-orphans → ``test_attestation_idle_orphan_incident.py``;
     non-delegated → ``test_attestation_marker_routing_lca.py``;
     dry/off/default → ``test_attestation_stage2_killswitch.py``
     TestCellC/B/D.

Production code is FROZEN — this file is test-only.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from daemon.services import attestation_report_judge as judge_mod
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)
from tests.support.scripted_chat_model import ScriptedChatModel

INSTANCE_ID = "attestation-leader-e2e"
_EXPECTED_HEAD_PREFIX = "feature/lca-resolver-stage3"


def _git_head() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:  # noqa: BLE001
        return "<unknown>"


@pytest.fixture(autouse=True)
def _branch_drift_pin():
    head = _git_head()
    if not head.startswith(_EXPECTED_HEAD_PREFIX):
        pytest.skip(
            f"drift-pin: branch={head!r}, expected "
            f"prefix {_EXPECTED_HEAD_PREFIX!r}"
        )


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


@tool
def attest_completion() -> dict:
    """Real leader attestation tool used by the graph's ToolNode."""
    return {"attested": True, "timestamp": "2026-09-17T00:00:00+00:00"}


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


def _build(graph_module, model, manager, checkpointer):
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-zoo", "api_key": "test"},
            system_prompt="scripted stage3 zoo leader",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


def _not_complete_json() -> str:
    body = json.dumps(
        {
            "verdict": "not_complete",
            "evidence_cited": ["SOURCE A: child promised future work"],
            "advisory_note_text": "child contradicts done",
            "rationale": "mid-work",
        }
    )
    assert len(body) <= judge_mod.FUSED_JUDGE_MAX_OUTPUT_CHARS
    return body


def _complete_json() -> str:
    body = json.dumps(
        {
            "verdict": "complete",
            "evidence_cited": ["report enumerates outcomes"],
            "advisory_note_text": "",
            "rationale": "genuine",
        }
    )
    assert len(body) <= judge_mod.FUSED_JUDGE_MAX_OUTPUT_CHARS
    return body


@dataclass
class _ScriptedJudge:
    """Judge-invocation recorder at the ONE shared LLM seam
    ``_invoke_judge_llm`` (consumed by ``judge_fused_bundle_async``).
    """

    responses: list[str]
    attempts: list[str] = field(default_factory=list)
    payloads: list[str] = field(default_factory=list)

    async def __call__(
        self, config, user_payload, *, timeout_s, system_prompt=None
    ):
        self.attempts.append(user_payload)
        self.payloads.append(user_payload)
        idx = min(len(self.attempts) - 1, len(self.responses) - 1)
        return (self.responses[idx], "fake-quick")


def _install_judge_stub(monkeypatch, judge):
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", judge)


def _delegate_ai() -> AIMessage:
    return AIMessage(
        content="Delegating to child.",
        tool_calls=[
            {"name": "send_message", "args": {"target": "child"}, "id": "d1"}
        ],
    )


def _child_report_check_note(child_id: str | None = None) -> HumanMessage:
    child = child_id or str(uuid.uuid4())
    return HumanMessage(
        content=(
            f"[SYSTEM CONTEXT: Child Report Check]\n\nChild {child} "
            "completed while its final report promised future work. "
            "Matched terms: will write the report."
        ),
        additional_kwargs={
            "context_kind": "child_report_check",
            "child_report_check": True,
            "child_instance_id": child,
            "child_report_check_terms": ["will write the report"],
        },
        id=f"child_report_check:lead:{child}",
    )


def _rows(caplog, token: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if token in r.getMessage()]


def _nudge_messages(messages):
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and (m.additional_kwargs or {}).get("attestation_nudge")
    ]


def _hint_messages(messages):
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and (m.additional_kwargs or {}).get("context_kind") == "task_context"
    ]


# SCENARIO 1 — full CHILD-LIE ARC through REAL graph (3 evals, 2 ainvoke)
@pytest.mark.asyncio
async def test_zoo_scenario_1_child_lie_full_lifecycle_real_graph(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    monkeypatch,
    caplog,
):
    """3 evals across 2 ainvoke calls: deny+nudge → revive → attested
    allow + counter RESET. Counter transitions 0→1→2→0.

    Eval 1: Source-A child-report evidence + quiet tree → c_quiet=true
    → BAND_DENY → fused judge fires → not_complete → DENY+NUDGE (0→1).
    Eval 2: leader revives (no tool_call) → quiet tree → BAND_DENY →
    judge fires → not_complete → DENY+NUDGE (1→2; FIX-3 stable-id
    supersede keeps surviving nudge).
    Eval 3: leader attests via ``attest_completion`` → meta-bypass →
    ALLOW + counter RESET (ALLOWED + attestation_present=True).

    Anchors: judge payloads contain SOURCE A (Δ1); 2 fused-judge
    operator rows; 0 legacy ``*_marker_judge*`` rows; legacy
    ``event=leader_completion_gate_judge`` row absent.
    """
    judge = _ScriptedJudge(responses=[_not_complete_json()] * 2)
    _install_judge_stub(monkeypatch, judge)

    repo, _instance = attestation_repository
    manager = attestation_manager_factory(
        file_sqlite_engine, repo, pending_children=0, queued_wakeups=0
    )

    # ainvoke #1 — drives eval 1 (deny+nudge) AND eval 2 (attested
    # allow + counter reset, 0→1→0). The deny+nudge routes back to
    # the agent; we therefore script enough responses to reach the
    # attested allow AIMessage before the gate ENDS the graph.
    #
    #   LLM 1: delegate (send_message) → tools (unbound, error) →
    #     routes back to agent
    #   LLM 2: hallucinated → end_candidate → gate eval1 (BAND_DENY +
    #     judge=not_complete → DENY+NUDGE, counter 0→1; routes back)
    #   LLM 3: attest_ai (attest_completion) → tools (succeeds) →
    #     routes back to agent
    #   LLM 4: final_ai → end_candidate → gate eval2 (attested
    #     meta-bypass → ALLOW, counter reset to 0; END)
    model1 = ScriptedChatModel(
        responses=[
            _delegate_ai(),
            AIMessage(content="hallucinated-1"),
            AIMessage(
                content="Attesting now.",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "a1-zoo-eval2",
                    }
                ],
            ),
            AIMessage(content="full report delivered"),
        ],
        i=0,
    )
    graph1 = _build(real_graph_module, model1, manager, memory_saver)
    with caplog.at_level(logging.INFO):
        await graph1.ainvoke(
            {
                "messages": [
                    HumanMessage(content="please complete the mission"),
                    _child_report_check_note(),
                ]
            },
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model1.assert_all_responses_consumed()

    # 2 resolver rows: eval1 deny+nudge + eval2 attested allow.
    resolver1 = _rows(caplog, "event=leader_completion_resolver_eval")
    assert len(resolver1) == 2, (
        f"ainvoke #1 ⇒ 2 evals (eval1 deny+nudge + eval2 attested "
        f"allow); got {len(resolver1)}: {resolver1}"
    )
    assert "judge_invoked=True" in resolver1[0]
    assert "resolver_outcome=deny_nudge" in resolver1[0]
    assert "fired=False" in resolver1[1]
    assert "bypass_reason=meta_bypass" in resolver1[1]
    assert "SOURCE A" in judge.payloads[0], judge.payloads[0][:200]
    # 1 fused judge invocation (eval 1); eval 2 is meta-bypass (0).
    assert len(judge.attempts) == 1, (
        f"eval 1 (1 invocation) + eval 2 (0 invocation) ⇒ 1 attempt; "
        f"got {len(judge.attempts)}"
    )
    # Counter: 0 → 1 (eval 1) → 0 (eval 2 attested-allow reset).
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0, (
        f"eval 2 attested-allow MUST reset counter to 0; "
        f"got {repo.get_attestation_denied_count(INSTANCE_ID)}"
    )

    # ainvoke #2 — drives eval 3 (revive, deny+nudge on quiet tree,
    # counter 0→1) AND eval 4 (attested allow, counter reset 1→0).
    # The deny+nudge in eval 3 routes back; we again script enough
    # responses for the attested allow AIMessage to fire.
    #
    #   LLM 1: send_message_ai (revive send_message) → tools (unbound) →
    #     routes back
    #   LLM 2: revive_ai → end_candidate → gate eval3 (BAND_DENY +
    #     judge=not_complete → DENY+NUDGE, counter 0→1; routes back)
    #   LLM 3: attest_ai (attest_completion) → tools (succeeds) →
    #     routes back
    #   LLM 4: final_ai → end_candidate → gate eval4 (attested
    #     meta-bypass → ALLOW, counter reset to 0; END)
    model2 = ScriptedChatModel(
        responses=[
            AIMessage(
                content="reviving child",
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"target": "child-id"},
                        "id": "c2-revive",
                    }
                ],
            ),
            AIMessage(content="I revived the child and am waiting."),
            AIMessage(
                content="Attesting completion.",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "a2-zoo-eval4",
                    }
                ],
            ),
            AIMessage(content="final report"),
        ],
        i=0,
    )
    graph2 = _build(real_graph_module, model2, manager, memory_saver)
    with caplog.at_level(logging.INFO):
        state2 = await graph2.ainvoke(
            {"messages": [HumanMessage(content="verify the child's report")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model2.assert_all_responses_consumed()

    # 2 more resolver rows (eval 3 + eval 4) ⇒ 4 total across the arc.
    rows_all = _rows(caplog, "event=leader_completion_resolver_eval")
    assert len(rows_all) == 4, (
        f"4 evals across 2 ainvoke calls ⇒ 4 resolver rows; "
        f"got {len(rows_all)}: {rows_all}"
    )
    assert "resolver_outcome=deny_nudge" in rows_all[2], rows_all[2]
    assert "judge_invoked=True" in rows_all[2]
    assert "fired=False" in rows_all[3]
    assert "bypass_reason=meta_bypass" in rows_all[3]

    # Judge invocation count: 2 (eval 1 + eval 3). Evals 2, 4 = 0.
    assert len(judge.attempts) == 2, (
        f"4 evals × (1, 0, 1, 0) judge invocations ⇒ 2 HTTP attempts; "
        f"got {len(judge.attempts)}"
    )
    fused_total = _rows(caplog, "event=leader_completion_gate_fused_judge ")
    assert len(fused_total) == 2, (
        f"2 fused operator rows (eval 1 + eval 3); got {len(fused_total)}"
    )

    # Counter transitions: 0 → 1 → 0 → 1 → 0.
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0, (
        f"eval 4 attested-allow MUST reset counter; final = "
        f"{repo.get_attestation_denied_count(INSTANCE_ID)}"
    )

    # FIX-3 stable-id supersede: surviving nudge carries the
    # per-instance id; only one nudge block in the final tape.
    nudges = _nudge_messages(state2["messages"])
    assert len(nudges) >= 1, nudges
    assert all(
        n.id == f"attestation_nudge:{INSTANCE_ID}" for n in nudges
    ), f"nudge id drift: {[n.id for n in nudges]}"

    # Stage-3 R8 census: legacy judge event rows MUST NOT appear.
    log_text = caplog.text
    assert "event=leader_completion_gate_judge " not in log_text
    assert "leader_completion_gate_marker_judge" not in log_text


# SCENARIO 2 — GENUINE REPORT rescue through real graph
# ≥150 words, no catalog marker substring (must pass the
# SHORT_REPORT_WORD_THRESHOLD guard and avoid MID_WORK_MARKERS).
LONG_BENIGN = (
    "Comprehensive completion report covering the planned scope of this "
    "dispatch cycle. All twelve planned fixes have shipped to latest, "
    "with green gates on every test pack we own and the four cross-repo "
    "tests we touched also passing. The post-merge restart is on the "
    "operator's next step. Soak shows zero post-merge regressions on any "
    "of the four areas we re-ran. I am reporting completion of the planned "
    "scope for this dispatch cycle. Out of scope: the upgrade live rung and "
    "the unrelated cleanups the user flagged as out-of-scope. Recommend the "
    "operator run the standard pause-first restart runbook to activate the "
    "restart-pending fixes, then re-verify live before resolving the "
    "corresponding critical notes. The full migration landed cleanly and the "
    "checksums match the shipped files; the operator runbook now documents "
    "the new boot probe lines with grep recipes. The incident report covers "
    "all twelve workstreams with linked CI evidence. Follow-ups are limited "
    "to the deferred upgrade rung and the four unrelated cleanups the user "
    "flagged as out-of-scope for this dispatch."
)
assert len(LONG_BENIGN.split()) >= 150, "long benign ≥ 150 words"


@pytest.mark.asyncio
async def test_zoo_scenario_2_genuine_report_rescue_real_graph(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    monkeypatch,
    caplog,
):
    """Long benign + quiet tree + delegated → BAND_DENY → fused judge
    fires → verdict=complete → RESCUE ALLOW (no nudge, no counter
    movement, no hint).

    Anchors: 1 judge invocation (1 HTTP attempt); fused operator row
    with verdict=complete + judge_invoked=True; resolver row
    resolver_outcome=allow + band=deny; counter stays at 0; zero
    nudges / hints in the final tape.
    """
    judge = _ScriptedJudge(responses=[_complete_json()])
    _install_judge_stub(monkeypatch, judge)

    repo, _instance = attestation_repository
    manager = attestation_manager_factory(
        file_sqlite_engine, repo, pending_children=0, queued_wakeups=0
    )

    model = ScriptedChatModel(
        responses=[_delegate_ai(), AIMessage(content=LONG_BENIGN)],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, memory_saver)

    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="finish the mission")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model.assert_all_responses_consumed()

    final_messages = state["messages"]

    assert len(judge.attempts) == 1, judge.attempts
    fused_rows = _rows(caplog, "event=leader_completion_gate_fused_judge ")
    assert len(fused_rows) == 1, fused_rows
    assert "verdict=complete" in fused_rows[0], fused_rows[0]
    assert "judge_invoked=True" in fused_rows[0], fused_rows[0]

    resolver_rows = _rows(caplog, "event=leader_completion_resolver_eval")
    assert len(resolver_rows) == 1, resolver_rows
    assert "resolver_outcome=allow" in resolver_rows[0]
    assert "judge_invoked=True" in resolver_rows[0]
    assert "band=deny" in resolver_rows[0]

    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0, (
        f"rescue allow MUST NOT move the counter; got "
        f"{repo.get_attestation_denied_count(INSTANCE_ID)}"
    )

    assert _nudge_messages(final_messages) == [], _nudge_messages(final_messages)
    assert _hint_messages(final_messages) == [], _hint_messages(final_messages)