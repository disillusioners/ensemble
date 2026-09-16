"""LCA Stage-2 INDEPENDENT E2E — incident shapes (a)(b)(c).

This file is the JOB 2a-c independent E2E acceptance for the LCA Stage-2
flip merge gate (Job 2 of 9). The existing unit file
``tests/unit/test_attestation_resolver_stage2.py`` covers shape per-class
THIN at these seams:

* Shape (a) b08f40fe:
    - bound escalation through the FUSED deny path (3 sequential deny
      cycles → ``terminal_after_bound``; the existing unit test only
      covers a single deny cycle at denied_count=0).
    - retry-on-unparsable-first-response (HTTP attempts=2 within ONE
      logical invocation; the §5 budget guard; this is the
      preserved 98b59dd7 retry contract).
    - legacy ``judge_completion_report_async`` entry-point silence
      specifically under the b08f40fe shape (Δ1 fusion carry).
* Shape (b) 98b59dd7:
    - counter non-movement on the rescue path with a prior denied_count
      pre-seeded (the rescue path bypasses both Phase-3 ledger writes —
      no increment, no reset).
    - ``judge_invoked`` event-row correctness on the allow-via-rescue path
      (the field MUST be ``True`` AND the spy MUST record exactly one
      HTTP attempt).
* Shape (c) 6a0d60c9:
    - ``judge_invoked`` event-row correctness on the meta-bypass path
      (``fired=False``, ``bypass_reason=meta_bypass``, ``judge_invoked=
      False`` — three sentinel fields together).
    - ZERO ``event=leader_completion_gate_fused_judge`` operator rows
      emitted on the answer-pending path (the judge does NOT fire, so
      the row is NOT emitted; this is distinct from the
      judge-fired-but-not-complete shape where the row DOES fire with
      ``verdict=not_complete``).

Every scenario embeds the cross-shape budget guards (R-RES2-7 / R-RES2-8 /
R-RES2-10 acceptance):

* Judge logical-invocation count ≤ 1 per evaluation (assert
  ``len(fused_rows) <= 1`` per scenario).
* HTTP attempts ≤ 2 (assert ``len(spy.attempts) <= 2`` per scenario;
  the upper-bound 2 is REACHED only on a deliberately-unparsable-
  first-response case).
* ``judge_invoked`` event-row field correctness (the field is the
  DERIVED ``FusedJudgeResult.invoked``, never a literal — Stage-1
  review hazard pin).
* Legacy judge sites silent: ``judge_completion_report_async`` zero
  calls across the scenario (R-RES2-8).

Harness mirrors ``tests/integration/test_attestation_marker_routing_lca.py``
and ``tests/unit/test_attestation_resolver_stage2.py``: real
``create_attestation_gate_node`` closure + stubbed manager + stubbed
ledger + scripted ``_invoke_judge_llm`` stub + legacy spy. NO real
LLM calls, NO real DB.

Production code is FROZEN — this file is test-only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services import attestation_report_judge as judge_mod
from daemon.services.attestation_gate import (
    Decision,
    GateSettings,
    build_gate_config,
)
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)
from daemon.graph import create_attestation_gate_node


# ─────────────────────────────────────────────────────────────────────────────
# Drift-pin guard — confirm we are on the LCA Stage-2 branch family
# ─────────────────────────────────────────────────────────────────────────────
_EXPECTED_HEAD_PREFIX = "feature/lca-resolver-stage2"


def _git_head() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:  # noqa: BLE001 — drift-pin is best-effort
        return "<unknown>"


@pytest.fixture(autouse=True)
def _branch_drift_pin():
    """Soft-pin: must be on the Stage-2 branch family."""
    head = _git_head()
    if not head.startswith(_EXPECTED_HEAD_PREFIX):
        pytest.skip(
            f"drift-pin: branch={head!r}, expected prefix "
            f"{_EXPECTED_HEAD_PREFIX!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Hermetic resolver caches + kill-switch env per test
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# Verdict-JSON helpers (mirror test_attestation_resolver_stage2.py shape)
# ─────────────────────────────────────────────────────────────────────────────


def _complete_json(evidence=("report enumerates outcomes",)) -> str:
    return json.dumps(
        {
            "verdict": "complete",
            "evidence_cited": list(evidence),
            "advisory_note_text": "",
            "rationale": "genuine",
        }
    )


def _not_complete_json(
    evidence=("SOURCE A: child-terminal contradiction evidence",),
    advisory="The child's report promised future work",
) -> str:
    return json.dumps(
        {
            "verdict": "not_complete",
            "evidence_cited": list(evidence),
            "advisory_note_text": advisory,
            "rationale": "mid-work",
        }
    )


def _unparsable_text() -> str:
    """A body that fails :func:`_parse_fused_judge_response` — triggers
    the retry-on-unparsable second attempt inside the fused judge."""
    return "Sorry, I cannot help with that."


# ─────────────────────────────────────────────────────────────────────────────
# Judge-call spy: records every HTTP attempt + the payload the judge SAW
# ─────────────────────────────────────────────────────────────────────────────


class _ScriptedJudge:
    """Scripted LLM-judge stub.

    ``responses`` is a list of bodies consumed in order across HTTP
    attempts; a single-element list is reused for every attempt. Every
    attempt appends to ``self.attempts`` (HTTP-attempt count) and to
    ``self.payloads`` (the verbatim user_payload the fused judge fed the
    LLM).
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.attempts: list[str] = []
        self.payloads: list[str] = []

    async def __call__(self, config, user_payload, *, timeout_s, system_prompt=None):
        self.attempts.append(user_payload)
        self.payloads.append(user_payload)
        if len(self.responses) == 1:
            return (self.responses[0], "fake-quick")
        idx = min(len(self.attempts) - 1, len(self.responses) - 1)
        return (self.responses[idx], "fake-quick")


class _LegacyJudgeSpy:
    """Spy on the legacy ``judge_completion_report_async``.

    R-RES2-8 pin: this entry-point MUST stay at zero calls under the
    Stage-2 flip. Any call recorded by the spy means the flip
    regressed.
    """

    def __init__(self):
        self.calls: list = []

    async def __call__(self, messages, *, config, window=3, timeout_s=None):
        self.calls.append(messages)
        from daemon.services.attestation_report_judge import JudgeResult

        return JudgeResult(
            is_complete_report=True,
            verdict="yes",
            reason="legacy-never-runs",
            model="<legacy-stub>",
            latency_ms=0,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Node builders + state helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_node(
    *,
    instance_id: str,
    llm_judge_enabled: bool = True,
    denied_count: int = 0,
    pending_children: int = 0,
    queued_wakeups: int = 0,
    live_descendants: int = 0,
    busy_descendants: int = 0,
    user_answer_pending: bool = False,
    settings: GateSettings | None = None,
    manager: MagicMock | None = None,
    ledger: MagicMock | None = None,
    denied_count_getter=None,
):
    if manager is None:
        manager = MagicMock()
        manager.count_pending_children.return_value = pending_children
        manager.get_queued_or_expected_wakeups.return_value = queued_wakeups
        manager.count_live_descendants.return_value = live_descendants
        manager.count_busy_descendants.return_value = busy_descendants
        manager.get_tree_ids_permanent.return_value = []
        manager.revive = MagicMock()
        manager.send_message = MagicMock()
    manager.has_open_user_answer = MagicMock(return_value=user_answer_pending)

    if ledger is None:
        ledger = MagicMock()
        ledger.increment.return_value = 1
        ledger.reset.return_value = True
        ledger.set_escalated_and_reset.return_value = True
        ledger.get.return_value = denied_count

    settings = settings or GateSettings("enforce", 3, 3)
    config = build_gate_config(
        instance_id, settings, llm_judge_enabled=llm_judge_enabled
    )
    node = create_attestation_gate_node(
        config,
        settings,
        manager,
        instance_id,
        denied_count_getter=denied_count_getter or (lambda: denied_count),
        ledger=ledger,
    )
    return node, manager, ledger


def _delegated_mission(final_text: str, extra_messages=()) -> dict:
    """Delegated mission — send_message tool_call anchors the conditional
    gate as ON (the 2026-09-06 amendment)."""
    delegation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": "child-id"}, "id": "c1"}
        ],
    )
    return {
        "messages": [
            HumanMessage(content="please do it"),
            delegation_ai,
            *extra_messages,
            AIMessage(content=final_text),
        ]
    }


def _child_report_check_note(child_id: str | None = None) -> HumanMessage:
    """System-injected child-report contradiction note (Δ1 source)."""
    child = child_id or str(uuid.uuid4())
    body = (
        f"Child {child} completed while its final report promised future "
        "work. Matched terms: will write the report. This completion is "
        "likely premature — the child promised work it did not deliver."
    )
    return HumanMessage(
        content=f"[SYSTEM CONTEXT: Child Report Check]\n\n{body}",
        additional_kwargs={
            "context_kind": "child_report_check",
            "child_report_check": True,
            "child_instance_id": child,
            "child_report_check_terms": ["will write the report"],
        },
        id=f"child_report_check:lead:{child}",
    )


def _run(node, state, instance_id):
    return asyncio.run(
        node(state, config={"configurable": {"thread_id": instance_id}})
    )


def _rows(caplog, token: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if token in r.getMessage()
    ]


def _capture(caplog):
    for logger in (
        "daemon.graph",
        "daemon.services.attestation_gate",
        "daemon.services.attestation_resolver_activation",
    ):
        caplog.set_level(logging.INFO, logger=logger)
    return caplog


# ─────────────────────────────────────────────────────────────────────────────
# Generic cross-shape budget guards (reused per scenario)
# ─────────────────────────────────────────────────────────────────────────────


def _assert_budget_guards(spy: _ScriptedJudge, legacy: _LegacyJudgeSpy, caplog):
    """Embed the §5 / §R-RES2 budget + legacy-site guards in every
    scenario:

    * at most ONE fused judge invocation per evaluation (the row-count
      sentinel — the logical-invocation count ≤ 1 invariant)
    * at most TWO HTTP attempts within that single invocation (the
      retry-on-unparsable cap; reached only on a deliberately-unparsable-
      first-response case)
    * ``judge_invoked`` event-row field correctness is asserted
      scenario-locally (the field is DERIVED from
      ``FusedJudgeResult.invoked``)
    * legacy judge sites silent (``judge_completion_report_async`` zero
      calls under the flip)
    """
    fused_rows = _rows(caplog, "event=leader_completion_gate_fused_judge ")
    assert len(fused_rows) <= 1, (
        f"§5 budget guard violated: {len(fused_rows)} fused judge rows in "
        f"one evaluation; expected ≤ 1 logical invocation. Rows: {fused_rows}"
    )
    assert len(spy.attempts) <= 2, (
        f"§5 budget guard violated: {len(spy.attempts)} HTTP attempts in "
        f"one evaluation; expected ≤ 2 within one invocation (98b59dd7 "
        f"retry contract)."
    )
    assert legacy.calls == [], (
        f"R-RES2-8 pin violated: legacy judge_completion_report_async "
        f"called {len(legacy.calls)} time(s) under the flip; expected 0. "
        f"Call signatures: {legacy.calls}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Shape (a) — b08f40fe idle-orphan → fused deny+nudge → bound escalation
# ─────────────────────────────────────────────────────────────────────────────
#
# (a1) The judge SAW the child-report evidence (Δ1) — payload contains the
#      SOURCE A markers from the child-report contradiction note.
# (a2) Bound escalation through the FUSED path: 3 sequential deny cycles
#      → ``terminal_after_bound`` on the 3rd. NOT covered by the existing
#      unit test (which only does ONE deny cycle at denied_count=0).
# (a3) Retry-on-unparsable: 2 HTTP attempts within ONE invocation; the
#      second response parses to a verdict. Single fused row, judge_invoked
#      stays True (the HTTP attempt DID happen).
# (a4) Legacy sites silent on the b08f40fe shape (R-RES2-8) with Δ1 fused
#      carry — the old deny-path judge's signature is NOT observed.


class TestShapeAB08f40feIdleOrphanBoundEscalation:
    """Independent E2E for shape (a) — idle-orphan → deny+nudge →
    bound escalation through the FUSED path."""

    def test_a1_judge_payload_carries_child_report_evidence(
        self, monkeypatch, caplog
    ):
        """Δ1: the fused judge (called on the deny band) SAW the
        SOURCE A child-terminal-contradiction evidence in its payload.
        This is the load-bearing assertion: pre-Stage-2 the deny-path
        judge never saw the child's evidence."""
        spy = _ScriptedJudge(
            [_not_complete_json(
                evidence=["SOURCE A: child promised future work"],
                advisory="The child's terminal report promised work",
            )]
        )
        legacy = _LegacyJudgeSpy()
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            judge_mod, "judge_completion_report_async", legacy
        )

        node, _m, ledger = _make_node(instance_id="a1-payload")
        state = _delegated_mission(
            "Awaiting final four: C12a/b/c + blame-worker. "
            "Then I aggregate and write RESULTS. Ending turn.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, state, "a1-payload")

        # The judge SAW the SOURCE A markers from the child-report note.
        assert spy.payloads, "deny-band fused judge must have fired"
        assert "SOURCE A: child-terminal contradiction evidence" in spy.payloads[0]
        assert "promised future work" in spy.payloads[0]

        # deny+nudge via the existing machinery.
        assert "messages" in result
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()

        # judge_invoked event-row correctness: True (the fused judge DID
        # invoke; FusedJudgeResult.invoked=True carries over).
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert eval_rows and "judge_invoked=True" in eval_rows[0]
        assert "resolver_outcome=deny_nudge" in eval_rows[0]
        assert "band=deny" in eval_rows[0]

        _assert_budget_guards(spy, legacy, caplog)

    def test_a2_four_sequential_denies_then_terminal_after_bound(
        self, monkeypatch, caplog
    ):
        """Bound escalation through the FUSED deny path.

        Drive 4 sequential deny cycles (denied_count 0→1→2→3). On the 4th
        cycle ``deny_bound_exceeded(denied_count=3, bound=3) → 3+1>3 →
        True`` ⇒ ``TERMINAL_AFTER_BOUND`` WITHOUT a judge call (budget
        parity), the counter resets to 0 via
        ``set_escalated_and_reset``, and the
        ``event=leader_completion_gate_terminal_after_bound`` operator
        row fires.

        NOT covered by the existing unit test (which only does ONE deny
        cycle at denied_count=0). The unit file's ``TestR7PinBoundEsca
        lationFromFusedPath`` covers the bound cycle but with an
        INDEPENDENT node per cycle (closed-bound-state reconstruction);
        this test drives the bound through a SHARED mutable counter to
        exercise the production ``denied_count_getter`` wiring end-to-end.
        ``deny_bound_exceeded(denied_count, bound) := denied_count + 1 >
        bound`` — bound=3 needs denied_count=3 to fire
        (``3+1>3 = True``).
        """
        spy = _ScriptedJudge([_not_complete_json()])
        legacy = _LegacyJudgeSpy()
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            judge_mod, "judge_completion_report_async", legacy
        )

        # Mutable denied-count state — drives the production
        # denied_count_getter wiring across 3 cycles.
        denied_state = {"count": 0}

        # Ledger records what the gate node writes (increment is called
        # on each deny; the 3rd cycle hits TERMINAL_AFTER_BOUND and the
        # counter resets via set_escalated_and_reset instead).
        ledger = MagicMock()
        ledger.increment.return_value = denied_state["count"] + 1
        ledger.reset.return_value = True
        ledger.set_escalated_and_reset.return_value = True
        ledger.get.return_value = 0

        node, _m, _l = _make_node(
            instance_id="a2-bound",
            denied_count_getter=lambda: denied_state["count"],
            ledger=ledger,
        )

        def _drive_one_cycle(final_text: str):
            with _capture(caplog).at_level(logging.INFO):
                result = _run(
                    node,
                    _delegated_mission(
                        final_text,
                        extra_messages=[_child_report_check_note()],
                    ),
                    "a2-bound",
                )
            return result

        # Cycle 1: denied_count=0 → deny+nudge, counter → 1.
        result1 = _drive_one_cycle(
            "Awaiting final four. Then aggregate and write RESULTS. "
            "Ending turn."
        )
        denied_state["count"] = 1
        ledger.increment.assert_called_once()
        assert "messages" in result1
        assert result1["attestation_route"] == "agent"
        eval1 = _rows(caplog, "event=leader_completion_resolver_eval")[-1]
        assert "resolver_outcome=deny_nudge" in eval1
        assert "judge_invoked=True" in eval1

        # Cycle 2: denied_count=1 → deny+nudge, counter → 2.
        ledger.reset_mock()
        result2 = _drive_one_cycle(
            "Still working on it. Will update. Ending turn."
        )
        denied_state["count"] = 2
        ledger.increment.assert_called_once()
        assert "messages" in result2
        assert result2["attestation_route"] == "agent"

        # Cycle 3: denied_count=2 → deny+nudge, next_denied_count=3 (not
        # > bound yet; the bound predicate is `denied_count + 1 > bound`).
        ledger.reset_mock()
        result3 = _drive_one_cycle(
            "Continuing. Ending turn."
        )
        denied_state["count"] = 3
        ledger.increment.assert_called_once()
        assert "messages" in result3
        assert result3["attestation_route"] == "agent"

        # Cycle 4: denied_count=3 → 3+1>3=True → TERMINAL_AFTER_BOUND.
        # The 4th cycle hits the at-bound deny-band arm WITHOUT a judge
        # call (budget parity), counter resets via set_escalated_and_reset
        # to 0.
        ledger.reset_mock()
        result4 = _drive_one_cycle(
            "Continuing. Ending turn."
        )
        # No nudge on the terminal path.
        assert "messages" not in result4
        assert result4["attestation_route"] is None
        # The terminal machinery ran set_escalated_and_reset exactly once
        # (and no increment on this cycle).
        ledger.set_escalated_and_reset.assert_called_once()
        ledger.increment.assert_not_called()
        # Operator event fired.
        terminal_rows = _rows(
            caplog, "event=leader_completion_gate_terminal_after_bound"
        )
        assert len(terminal_rows) == 1, (
            f"expected exactly one terminal_after_bound row; got "
            f"{len(terminal_rows)}: {terminal_rows}"
        )
        # The 4th-cycle eval row carries the terminal outcome.
        eval4 = _rows(caplog, "event=leader_completion_resolver_eval")[-1]
        assert "resolver_outcome=terminal_after_bound" in eval4
        # The 4th cycle's deny band hit the bound — NO judge fired
        # (budget parity). judge_invoked is False on this cycle.
        assert "judge_invoked=False" in eval4

        # 3 of 4 cycles invoked the judge (cycles 1, 2, 3 — the deny
        # band with judge_plan_on). Cycle 4 hit the bound before the
        # judge (budget parity) — 0 attempts on that cycle. Total
        # attempts across the 4 cycles ≤ 6 (3 × ≤2).
        assert len(spy.attempts) <= 6, (
            f"§5 budget guard: ≤3 judge-fired cycles × ≤2 attempts ≤ 6 "
            f"total; got {len(spy.attempts)}"
        )
        # Legacy site silent across all 4 cycles.
        assert legacy.calls == [], (
            f"R-RES2-8 pin violated under bound cycle: legacy judge "
            f"called {len(legacy.calls)} time(s)"
        )

    def test_a3_unparsable_first_attempt_within_one_invocation(
        self, monkeypatch, caplog
    ):
        """The retry-on-unparsable contract (incident 98b59dd7 carried
        forward to Stage 2): the fused judge fires the LLM ONCE for
        attempt 1, parses fail, fires ONCE for attempt 2, parses succeed.

        One logical invocation (1 fused row), 2 HTTP attempts in the
        spy. judge_invoked stays True (the retry is part of the same
        invocation).
        """
        spy = _ScriptedJudge(
            [
                _unparsable_text(),    # attempt 1 — unparsable → retry
                _not_complete_json(    # attempt 2 — parses → not_complete
                    evidence=[
                        "SOURCE A: child-terminal contradiction evidence"
                    ],
                ),
            ]
        )
        legacy = _LegacyJudgeSpy()
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            judge_mod, "judge_completion_report_async", legacy
        )

        node, _m, ledger = _make_node(instance_id="a3-unparsable")
        state = _delegated_mission(
            "Awaiting. Ending turn.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, state, "a3-unparsable")

        # Two HTTP attempts within ONE invocation (the retry contract).
        assert len(spy.attempts) == 2, (
            f"retry-on-unparsable expected exactly 2 HTTP attempts; "
            f"got {len(spy.attempts)}"
        )
        # The fused row fires exactly once (one logical invocation).
        fused_rows = _rows(caplog, "event=leader_completion_gate_fused_judge ")
        assert len(fused_rows) == 1, (
            f"retry-on-unparsable expected exactly 1 fused judge row; "
            f"got {len(fused_rows)}"
        )
        # judge_invoked=True (the HTTP attempt DID happen).
        assert "judge_invoked=True" in fused_rows[0]
        # The retry's attempt=2 surfaces on the fused row.
        assert "llm_judge_attempt=2" in fused_rows[0]

        # deny+nudge — the retry resolved to not_complete → deny band
        # deny+nudge.
        assert "messages" in result
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()

        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert "resolver_outcome=deny_nudge" in eval_rows[0]

        # Budget + legacy guards re-asserted (the second attempt's
        # payload is identical to the first — same invocation).
        _assert_budget_guards(spy, legacy, caplog)

    def test_a4_legacy_sites_silent_on_b08f40fe_shape(
        self, monkeypatch, caplog
    ):
        """R-RES2-8 pin reiterated for the b08f40fe shape specifically:
        with the child-report contradiction note in state (Δ1 carry) and
        the deny band firing, the legacy ``judge_completion_report_async``
        entry-point MUST stay at zero calls.

        The existing ``TestOldSitesDeadButPresent`` covers the deny +
        marker bands on a bare mission; this test exercises the fused
        deny band with the b08f40fe evidence (child-report check note),
        which is the post-Stage-2 realistic path.
        """
        spy = _ScriptedJudge([_not_complete_json()])
        legacy = _LegacyJudgeSpy()
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            judge_mod, "judge_completion_report_async", legacy
        )

        node, _m, _l = _make_node(instance_id="a4-legacy-silent")
        state = _delegated_mission(
            "Awaiting final four. Then aggregate and write RESULTS. "
            "Ending turn.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, state, "a4-legacy-silent")

        # The fused judge fired (the new path).
        assert spy.attempts, "fused judge must have fired on the deny band"
        assert len(spy.attempts) == 1

        # Legacy silent — the only judge call was via the new path.
        assert legacy.calls == [], (
            f"R-RES2-8 violated on b08f40fe shape: legacy entry-point "
            f"called {len(legacy.calls)} time(s); the fused judge is "
            f"the SOLE call site."
        )

        # Legacy log rows silent.
        assert _rows(caplog, "event=leader_completion_gate_judge ") == []
        assert _rows(
            caplog, "event=leader_completion_gate_marker_judge "
        ) == []
        # The fused row fires (the new path).
        assert len(
            _rows(caplog, "event=leader_completion_gate_fused_judge ")
        ) == 1

        # Outcome contract.
        assert result["attestation_route"] == "agent"
        assert "messages" in result


# ─────────────────────────────────────────────────────────────────────────────
# Shape (b) — 98b59dd7 genuine long report → fused rescue → ALLOW
# ─────────────────────────────────────────────────────────────────────────────
#
# (b1) Counter non-movement on the rescue path with a pre-seeded counter:
#      the rescue path bypasses both Phase-3 ledger writes (no increment,
#      no reset). The counter stays at the pre-seeded value.
# (b2) ``judge_invoked`` event-row correctness on the allow-via-rescue
#      path: judge_invoked=True (the fused judge DID invoke) AND exactly
#      one HTTP attempt was recorded.


class TestShapeB98b59dd7GenuineReportRescue:
    """Independent E2E for shape (b) — genuine long report → fused rescue
    → ALLOW without attestation. The rescue path bypasses both Phase-3
    ledger writes — the counter does NOT increment AND does NOT reset."""

    LONG_REPORT = (
        "All four workstreams completed and verified. Workstream 1: "
        "the parser rewrite landed with 34 new unit tests covering the "
        "edge cases found in the incident; all green on the first CI "
        "run. Workstream 2: the cache TTL off-by-one is fixed and "
        "pinned by a regression test at both boundaries. Workstream "
        "3: the operator runbook now documents the new boot probe "
        "lines with grep recipes. Workstream 4: the migration was "
        "re-run against the disposable database and the checksums "
        "match the shipped files. Evidence: CI run links in the task "
        "comments; the diff stat is 12 files, +900/-140. Follow-ups: "
        "none — the acceptance criteria are met in full."
    )

    def test_b1_rescue_does_not_move_pre_seeded_counter(
        self, monkeypatch, caplog
    ):
        """Rescue path contract: the fused judge said complete, the
        gate ALLOWS without attestation, AND the counter stays at its
        pre-seeded value (no increment AND no reset).

        The pre-existing unit test (TestIncident98b59dd7GenuineReport)
        uses denied_count=0 (the default); this test pins that a
        pre-seeded denied_count=2 (a would-be-stuck leader) ALSO gets
        the rescue allow without counter movement. This is the
        reset-on-allow semantics gap: the rescue path does NOT consume
        a reset trigger (those fire only on attested=True → ALLOWED
        or TERMINAL_AFTER_BOUND; the rescue allow is neither).
        """
        spy = _ScriptedJudge([_complete_json()])
        legacy = _LegacyJudgeSpy()
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            judge_mod, "judge_completion_report_async", legacy
        )

        PRIOR_COUNT = 2
        node, _m, ledger = _make_node(
            instance_id="b1-rescue", denied_count=PRIOR_COUNT
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_mission(self.LONG_REPORT),
                "b1-rescue",
            )

        # Rescue ALLOW: route=None (END without attestation).
        assert result["attestation_route"] is None
        # No nudge injected.
        assert "messages" not in result

        # Counter non-movement: neither increment NOR reset fired.
        ledger.increment.assert_not_called()
        ledger.reset.assert_not_called()
        ledger.set_escalated_and_reset.assert_not_called()

        # Eval row correctness: judge_invoked=True, resolver_outcome=allow.
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert eval_rows
        assert "judge_invoked=True" in eval_rows[0]
        assert "resolver_outcome=allow" in eval_rows[0]
        assert "band=deny" in eval_rows[0]

        # The fused judge fired exactly once with exactly one HTTP
        # attempt (single parsable response — no retry needed).
        assert len(spy.attempts) == 1
        # Legacy silent.
        assert legacy.calls == []
        _assert_budget_guards(spy, legacy, caplog)

    def test_b2_judge_invoked_event_field_correctness(
        self, monkeypatch, caplog
    ):
        """``judge_invoked`` event-row field is DERIVED from
        ``FusedJudgeResult.invoked`` (Stage-1 review hazard pin). For
        shape (b) the judge fires AND parses, so ``judge_invoked=True``
        on the eval row AND on the operator row.

        Pins the field-source invariant against an accidental literal
        substitution.
        """
        spy = _ScriptedJudge([_complete_json()])
        legacy = _LegacyJudgeSpy()
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            judge_mod, "judge_completion_report_async", legacy
        )

        node, _m, _l = _make_node(instance_id="b2-derived")
        with _capture(caplog).at_level(logging.INFO):
            _run(
                node,
                _delegated_mission(self.LONG_REPORT),
                "b2-derived",
            )

        # Eval row: judge_invoked=True (DERIVED from
        # FusedJudgeResult.invoked — never a literal).
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert eval_rows and "judge_invoked=True" in eval_rows[0]
        # Operator row: judge_invoked=True mirrors the same source.
        fused_rows = _rows(caplog, "event=leader_completion_gate_fused_judge ")
        assert len(fused_rows) == 1
        assert "judge_invoked=True" in fused_rows[0]
        # Attempt count surface.
        assert "llm_judge_attempt=1" in fused_rows[0]

        _assert_budget_guards(spy, legacy, caplog)


# ─────────────────────────────────────────────────────────────────────────────
# Shape (c) — 6a0d60c9 awaiting-answer → PLAIN ALLOW (meta-bypass)
# ─────────────────────────────────────────────────────────────────────────────
#
# (c1) ZERO ``event=leader_completion_gate_fused_judge`` operator rows
#      emitted on the answer-pending path (the judge does NOT fire; the
#      row MUST NOT emit either).
# (c2) Three sentinel fields pinned together on the eval row:
#      ``fired=False``, ``bypass_reason=meta_bypass``, ``judge_invoked=
#      False``. The judge_invoked derivation surfaces a literal False
#      here (NOT a fallback / not "not invoked" — the field is the
#      DERIVED real-invocation flag, which is False because no Fused
#      JudgeResult exists).


class TestShapeC6a0d60c9AwaitingAnswerMetaBypass:
    """Independent E2E for shape (c) — answer-pending → PLAIN ALLOW with
    meta-bypass. The judge does NOT fire; the counter does NOT move."""

    def test_c1_no_fused_judge_row_on_answer_pending(
        self, monkeypatch, caplog
    ):
        """The answer-pending arm is a CANONICAL-PATH evaluation but the
        fused judge does NOT fire. Consequently the operator-level
        ``event=leader_completion_gate_fused_judge`` row MUST NOT be
        emitted (it is emitted ONLY when the fused judge runs).

        The pre-existing unit test pins the eval-row ssendtinel fields
        (``fired=False``, ``bypass_reason=meta_bypass``,
        ``judge_invoked=False``) but does NOT pin the operator-row
        ABSENCE. This is the budget-guard's matching inverse: "no
        invocation ⇒ no operator row".
        """
        spy = _ScriptedJudge([_complete_json()])
        legacy = _LegacyJudgeSpy()
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            judge_mod, "judge_completion_report_async", legacy
        )

        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0
        manager.count_busy_descendants.return_value = 0
        manager.get_tree_ids_permanent.return_value = []
        manager.has_open_user_answer = MagicMock(return_value=True)

        ledger = MagicMock()
        ledger.increment.return_value = 1
        ledger.reset.return_value = True
        ledger.set_escalated_and_reset.return_value = True
        ledger.get.return_value = 0

        settings = GateSettings("enforce", 3, 3)
        config = build_gate_config("c1-answer-pending", settings)
        node = create_attestation_gate_node(
            config,
            settings,
            manager,
            "c1-answer-pending",
            denied_count_getter=lambda: 0,
            ledger=ledger,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_mission("Ending turn, awaiting your go/no-go."),
                "c1-answer-pending",
            )

        # Plain ALLOW — no nudge, no route.
        assert "messages" not in result
        assert result["attestation_route"] is None
        # Counter non-movement (FIX-2 contract).
        ledger.increment.assert_not_called()
        ledger.set_escalated_and_reset.assert_not_called()
        # The judge NEVER fired (spy has zero attempts).
        assert spy.attempts == [], (
            f"FIX-2 violated: the fused judge was called "
            f"{len(spy.attempts)} time(s) while an answer is pending; "
            f"expected 0. The awaiting answer is the whole turn's "
            f"purpose — judge work is forbidden on this branch."
        )
        # No operator-level fused-judge row emitted.
        fused_rows = _rows(caplog, "event=leader_completion_gate_fused_judge ")
        assert fused_rows == [], (
            f"FIX-2 / §R-RES2-7 violated: operator fused-judge row "
            f"emitted {len(fused_rows)} time(s) on the meta-bypass "
            f"path; expected 0 (no judge fired)."
        )

        _assert_budget_guards(spy, legacy, caplog)

    def test_c2_three_sentinel_fields_pinned_together(
        self, monkeypatch, caplog
    ):
        """The three sentinel fields on the eval row must travel
        together on the answer-pending path:

        * ``fired=False`` (the activation predicate did NOT fire)
        * ``bypass_reason=meta_bypass`` (Term 1 — the meta-bypass arm)
        * ``judge_invoked=False`` (DERIVED — no FusedJudgeResult exists)

        The existing unit test pins each in isolation via substring
        assertions; this test pins the JOINT contract (all three on the
        SAME row) and adds the ``attestation_required=False`` companion
        field which the meta-bypass carries through the same arm.
        """
        spy = _ScriptedJudge([_complete_json()])
        legacy = _LegacyJudgeSpy()
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setattr(
            judge_mod, "judge_completion_report_async", legacy
        )

        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0
        manager.count_busy_descendants.return_value = 0
        manager.get_tree_ids_permanent.return_value = []
        manager.has_open_user_answer = MagicMock(return_value=True)

        ledger = MagicMock()
        ledger.increment.return_value = 1
        ledger.reset.return_value = True
        ledger.set_escalated_and_reset.return_value = True
        ledger.get.return_value = 0

        settings = GateSettings("enforce", 3, 3)
        config = build_gate_config("c2-sentinels", settings)
        node = create_attestation_gate_node(
            config,
            settings,
            manager,
            "c2-sentinels",
            denied_count_getter=lambda: 0,
            ledger=ledger,
        )
        with _capture(caplog).at_level(logging.INFO):
            _run(
                node,
                _delegated_mission("Ending turn, awaiting your go/no-go."),
                "c2-sentinels",
            )

        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert eval_rows, "canonical-path eval row missing"
        row = eval_rows[0]

        # Three sentinel fields together on the SAME row.
        assert "fired=False" in row, (
            f"expected fired=False on the meta-bypass row; got: {row}"
        )
        assert "bypass_reason=meta_bypass" in row, (
            f"expected bypass_reason=meta_bypass; got: {row}"
        )
        assert "judge_invoked=False" in row, (
            f"expected judge_invoked=False (DERIVED — no invocation "
            f"record exists); got: {row}"
        )
        # Companion: user_answer_pending=True on the same row. Note
        # `attestation_required=True` is also expected here because the
        # mission IS delegated (a ``send_message`` tool_call is in the
        # tape) — the meta-bypass fires via ``user_answer_pending`` not
        # via ``¬attestation_required``. The two arms of the Term-1
        # bypass are independent: delegation-conditional + answer-pending.
        assert "user_answer_pending=True" in row
        assert "attestation_required=True" in row, (
            f"delegated mission carries attestation_required=True even "
            f"on the answer-pending meta-bypass; the bypass is via "
            f"user_answer_pending=True, NOT via attestation_required=False. "
            f"Got row: {row}"
        )

        # No operator-level fused-judge row.
        fused_rows = _rows(caplog, "event=leader_completion_gate_fused_judge ")
        assert fused_rows == []
        # Legacy silent.
        assert legacy.calls == []
        # Spy untouched.
        assert spy.attempts == []
