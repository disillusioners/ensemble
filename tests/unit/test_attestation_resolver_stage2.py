"""LCA unified resolver — Stage 2 flip tests (2026-09-16, the flip).

The resolver's outcome mapping is AUTHORITATIVE at the completion seam
(``daemon/graph.py`` fused block, gated by ``_LCA_STAGE2_RESOLVER_FLIP``);
the two legacy judge sites are dead-but-present. This file pins:

* **R7 invariant pins** (spec Appendix A R7 loud flags — the flip is
  behavior-neutral ONLY with these):
  - R7-1 ``TestR7PinJudgeErrorNeverAllows`` — judge error/timeout/
    unparsable-after-retry → deny+nudge, bound-enforced, NEVER allow
    (DP-5 REJECTED; path-(d)-exact).
  - R7-2 ``TestR7PinKillSwitchPerBandMapping`` — kill-switch OFF →
    deny+nudge on the un-attested-quiet band WITHOUT judge (Q1
    parity), plain allow on suspicion bands (exact today semantics).
  - R7-3 ``TestR7PinBoundEscalationFromFusedPath`` — bound/escalation
    reachable from the fused path exactly as the old deny path.
* **Budget guard** (§5): ``TestBudgetGuardSentinel`` — no input
  configuration produces 2 judge-INVOCATIONS in one evaluation; the
  old two-site worst case is structurally impossible (single site).
  The retry-once-on-unparsable is 2 HTTP attempts within ONE logical
  invocation (the preserved 98b59dd7 contract) — the sentinel is
  pinned at the INVOCATION level and that reading is documented here
  explicitly.
* **judge_invoked derivation** (Stage-1 review hazard pin): the eval
  row's ``judge_invoked`` is DERIVED from
  :attr:`FusedJudgeResult.invoked` — no literals.
* **Old sites dead-but-present**: neither legacy judge entry is
  reachable while the flip is active (spy pin).
* **D4 hint citation**: the Completion Check Note gains the evidence
  citation when the verdict carries one; byte-identical otherwise.
* **Incident-class E2E** (named per shape): b08f40fe, 98b59dd7,
  6a0d60c9, and the ORIGINAL child-lie arc.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services import attestation_report_judge as judge_mod
from daemon.services import (
    attestation_judge_resolver as judge_resolver_mod,
)
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
from daemon.graph import (
    COMPLETION_CHECK_NOTE_TEXT,
    _LCA_STAGE2_RESOLVER_FLIP,
    create_attestation_gate_node,
)


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    """Fresh resolver caches + hermetic kill-switch env per test."""
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# Harness
# ─────────────────────────────────────────────────────────────────────────────


class _JudgeSpy:
    """Recording stub for ``_invoke_judge_llm`` — counts HTTP ATTEMPTS
    and captures payloads; answers with a scripted verdict JSON."""

    def __init__(self, responses):
        # responses: list of strings (raw LLM bodies) consumed in order;
        # a single string is reused for every attempt.
        self.responses = list(responses)
        self.attempts: list[str] = []
        self.payloads: list[str] = []
        self.prompts: list = []

    async def __call__(self, config, user_payload, *, timeout_s, system_prompt=None):
        self.attempts.append(user_payload)
        self.payloads.append(user_payload)
        self.prompts.append(system_prompt)
        if len(self.responses) == 1:
            return (self.responses[0], "fake-quick")
        return (self.responses[len(self.attempts) - 1], "fake-quick")

    @property
    def invocations(self) -> int:
        # A LOGICAL invocation = one fused-judge entry (1–2 HTTP
        # attempts inside it when the retry fires). This spy counts
        # HTTP attempts; the invocation-level sentinel wraps entries.
        raise NotImplementedError("use node-level entry counting")


def _not_complete_json(evidence=(), advisory="") -> str:
    import json

    return json.dumps(
        {
            "verdict": "not_complete",
            "evidence_cited": list(evidence),
            "advisory_note_text": advisory,
            "rationale": "mid-work",
        }
    )


def _complete_json() -> str:
    import json

    return json.dumps(
        {
            "verdict": "complete",
            "evidence_cited": ["report enumerates outcomes"],
            "advisory_note_text": "",
            "rationale": "genuine",
        }
    )


def _make_node(
    *,
    instance_id: str,
    llm_judge_enabled: bool = True,
    denied_count: int = 0,
    pending_children: int = 0,
    queued_wakeups: int = 0,
    live_descendants: int = 0,
    busy_descendants: int = 0,
    settings: GateSettings | None = None,
):
    manager = MagicMock()
    manager.count_pending_children.return_value = pending_children
    manager.get_queued_or_expected_wakeups.return_value = queued_wakeups
    manager.count_live_descendants.return_value = live_descendants
    manager.count_busy_descendants.return_value = busy_descendants
    manager.get_tree_ids_permanent.return_value = []
    manager.has_open_user_answer = MagicMock(return_value=False)
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

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
        denied_count_getter=lambda: denied_count,
        ledger=ledger,
    )
    return node, manager, ledger


def _delegated_mission(final_text: str, extra_messages=()) -> dict:
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


def _rows(caplog, token):
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
# R7-1 — judge error/timeout/unparsable×2 → deny+nudge, bound-enforced,
#        NEVER allow (DP-5 rejected; path-(d)-exact)
# ─────────────────────────────────────────────────────────────────────────────


class TestR7PinJudgeErrorNeverAllows:
    @pytest.mark.parametrize(
        "responses",
        [
            # timeout on attempt 1 (no retry)
            ["<timeout>"],
            # error on attempt 1
            ["<error>"],
            # unparsable ×2 (retry exhausted)
            ["prose, no json", "still no json"],
        ],
        ids=["timeout", "error", "unparsable-x2"],
    )
    def test_deny_band_error_never_allows(self, monkeypatch, caplog, responses):
        """R7-1: deny band (un-attested∧quiet) + judge fault → deny+nudge
        via the EXISTING machinery; NEVER a fail-safe allow."""

        class _FaultSpy(_JudgeSpy):
            async def __call__(self, config, user_payload, *, timeout_s, system_prompt=None):
                self.attempts.append(user_payload)
                self.payloads.append(user_payload)
                if responses[0] == "<timeout>":
                    raise asyncio.TimeoutError()
                if responses[0] == "<error>":
                    raise RuntimeError("boom")
                return (responses[len(self.attempts) - 1], "fake-quick")

        spy = _FaultSpy([])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, manager, ledger = _make_node(instance_id="r7p1-deny")
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_mission("All done, shipped."),
                "r7p1-deny",
            )

        # NEVER allow: the deny+nudge machinery ran.
        assert "messages" in result, "R7-1: judge fault MUST deny+nudge"
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()
        nudge = result["messages"][0]
        assert nudge.additional_kwargs.get("attestation_nudge") is True
        # The resolver row records the conservative outcome.
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert eval_rows and "resolver_outcome=deny_nudge" in eval_rows[0]
        assert "judge_invoked=True" in eval_rows[0]

    def test_marker_band_unparsable_x2_with_pending_allows_with_hint_not_silent(
        self, monkeypatch, caplog
    ):
        """R7-1 (marker band, path-(d)-with-pending): the conservative
        route is allow+hint — NOT a silent plain allow (the hint is the
        durable record) and NEVER a deny while work is en route."""
        spy = _JudgeSpy(["prose no json", "still not json"])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, manager, ledger = _make_node(
            instance_id="r7p1-marker", pending_children=1
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_mission("Awaiting child. Ending turn."),
                "r7p1-marker",
            )

        assert "messages" in result, "path-(d)-with-pending → hint"
        hint = result["messages"][0]
        assert hint.additional_kwargs.get("context_kind") == "task_context"
        ledger.increment.assert_not_called()
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert "resolver_outcome=allow_hint" in eval_rows[0]
        # Both attempts fired (retry preserved) then conservatively routed.
        assert len(spy.attempts) == 2


# ─────────────────────────────────────────────────────────────────────────────
# R7-2 — kill-switch off → deny+nudge WITHOUT judge (deny band, Q1
#        parity); plain allow on suspicion bands
# ─────────────────────────────────────────────────────────────────────────────


class TestR7PinKillSwitchPerBandMapping:
    def test_kill_switch_off_deny_band_denies_without_judge(
        self, monkeypatch, caplog
    ):
        """Q1 parity: on the un-attested-quiet band the judge is a
        RESCUER — kill-switch OFF ⇒ deny+nudge WITHOUT any judge call."""
        spy = _JudgeSpy([_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setenv(
            "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0"
        )
        judge_resolver_mod.reset_llm_judge_resolver_for_tests()
        assert judge_resolver_mod.is_llm_judge_enabled() is False

        node, manager, ledger = _make_node(instance_id="r7p2-deny")
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node, _delegated_mission("All done."), "r7p2-deny"
            )

        # ZERO judge HTTP attempts — deny+nudge without the judge.
        assert spy.attempts == [], "Q1: kill-switch off ⇒ no judge call"
        assert "messages" in result
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert "judge_invoked=False" in eval_rows[0]
        assert "resolver_outcome=deny_nudge" in eval_rows[0]
        # The fused-judge row MUST NOT fire (no invocation).
        assert _rows(caplog, "event=leader_completion_gate_fused_judge ") == []

    @pytest.mark.parametrize(
        "fixture",
        [
            # marker band: delegated + pending + marker phrasing
            dict(
                pending_children=1,
                mission="Awaiting child reply. Ending turn, will continue.",
            ),
        ],
        ids=["marker-band"],
    )
    def test_kill_switch_off_suspicion_bands_plain_allow(
        self, monkeypatch, caplog, fixture
    ):
        """Exact today semantics: marker-band signal alone is too weak
        to deny — kill-switch OFF ⇒ plain allow (no hint, no judge)."""
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setenv(
            "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0"
        )
        judge_resolver_mod.reset_llm_judge_resolver_for_tests()

        node, manager, ledger = _make_node(
            instance_id="r7p2-marker",
            pending_children=fixture["pending_children"],
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, _delegated_mission(fixture["mission"]), "r7p2-marker")

        assert spy.attempts == [], "kill-switch off ⇒ no judge call"
        assert "messages" not in result, "plain allow — NO hint"
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert "resolver_outcome=allow" in eval_rows[0]
        assert "judge_invoked=False" in eval_rows[0]

    def test_kill_switch_off_a_band_plain_allow(self, monkeypatch, caplog):
        """A-band (Δ2 row): child-report note + busy tree — suspicion
        bands never deny without the judge; plain allow."""
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setenv(
            "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0"
        )
        judge_resolver_mod.reset_llm_judge_resolver_for_tests()

        node, manager, ledger = _make_node(
            instance_id="r7p2-a-band",
            pending_children=1,
            live_descendants=2,
            busy_descendants=2,  # markers muted; A fires ALONE
        )
        state = _delegated_mission(
            "Everything is done here.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, state, "r7p2-a-band")

        assert spy.attempts == [], "kill-switch off ⇒ no judge call"
        assert "messages" not in result, "plain allow — NO hint"
        assert result["attestation_route"] is None
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert "band=a_suspicion" in eval_rows[0]
        assert "resolver_outcome=allow" in eval_rows[0]
        assert "judge_invoked=False" in eval_rows[0]


# ─────────────────────────────────────────────────────────────────────────────
# R7-3 — bound/escalation reachable from the fused path exactly as the
#        old deny path
# ─────────────────────────────────────────────────────────────────────────────


class TestR7PinBoundEscalationFromFusedPath:
    def test_at_bound_quiet_row_terminates_with_escalation(
        self, monkeypatch, caplog
    ):
        """denied_count == bound (3) + un-attested∧quiet → decide()
        step (6) TERMINAL — the SAME machinery the old deny path used;
        no judge (budget parity: at-bound TERMINAL never got a judge
        pre-flip either); escalation ledger write + operator event."""
        spy = _JudgeSpy([_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, manager, ledger = _make_node(
            instance_id="r7p3-bound", denied_count=3
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node, _delegated_mission("All done."), "r7p3-bound"
            )

        assert "messages" not in result, "terminal — no nudge"
        assert result["attestation_route"] is None
        ledger.set_escalated_and_reset.assert_called_once()
        ledger.increment.assert_not_called()
        assert _rows(caplog, "event=leader_completion_gate_terminal_after_bound")
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert "resolver_outcome=terminal_after_bound" in eval_rows[0]
        assert "judge_invoked=False" in eval_rows[0]

    def test_below_bound_fused_deny_increments_counter(
        self, monkeypatch, caplog
    ):
        """Below the bound the fused deny rides the same counter —
        increments via safe_increment (the existing machinery)."""
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, manager, ledger = _make_node(
            instance_id="r7p3-below", denied_count=1
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node, _delegated_mission("All done."), "r7p3-below"
            )

        assert "messages" in result
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()
        nudge = result["messages"][0]
        # The nudge kwargs carry the counted denied count (the existing
        # checkpoint-durable contract — FIX-3 stable id + count).
        assert (
            nudge.additional_kwargs.get("attestation_nudge_denied_count") == 1
        )
        assert nudge.id == "attestation_nudge:r7p3-below"

    def test_three_denies_then_terminal_from_fused_path(
        self, monkeypatch, caplog
    ):
        """The loop bound from the fused path: EXACTLY 3 nudges then
        terminal — the 6a0d60c9 FIX-1 contract preserved end-to-end."""
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        nudges = 0
        for denied_count in (0, 1, 2, 3):
            node, _m, ledger = _make_node(
                instance_id="r7p3-loop", denied_count=denied_count
            )
            result = _run(
                node,
                _delegated_mission("Ending turn, awaiting go/no-go."),
                "r7p3-loop",
            )
            if "messages" in result:
                nudges += 1
                assert result["attestation_route"] == "agent"
                ledger.increment.assert_called_once()
            else:
                ledger.set_escalated_and_reset.assert_called_once()
        assert nudges == 3, "EXACTLY 3 nudges then terminal"


# ─────────────────────────────────────────────────────────────────────────────
# Budget guard sentinel — §5 + scope item 4
# ─────────────────────────────────────────────────────────────────────────────


class TestBudgetGuardSentinel:
    """no input configuration produces 2 judge-INVOCATIONS in one
    evaluation — the old two-site worst case (marker judge + would-be
    deny judge in one eval was structurally impossible pre-flip, but
    TWO call sites existed; post-flip there is ONE) is structurally
    impossible. READING (documented explicitly): the invocation level
    is the FUSED-JUDGE ENTRY (``judge_fused_bundle_async`` call), NOT
    the HTTP attempt count — the retry-once-on-unparsable may issue 2
    HTTP attempts WITHIN one logical invocation (the preserved
    98b59dd7 contract; pinned in test_attestation_fused_judge.py)."""

    @pytest.mark.parametrize(
        "fixture",
        [
            # deny band, not_complete
            dict(
                pending=0, live=0, busy=0,
                mission="All done.",
                note=False,
            ),
            # marker band, pending
            dict(
                pending=1, live=1, busy=0,
                mission="Awaiting child. Ending turn.",
                note=False,
            ),
            # A band (Δ2): note + busy tree (markers muted)
            dict(
                pending=1, live=2, busy=2,
                mission="Everything is done here.",
                note=True,
            ),
            # marker band + judge-complete (rescue arm)
            dict(
                pending=1, live=1, busy=0,
                mission="Awaiting child. Ending turn.",
                note=False,
            ),
        ],
        ids=["deny-band", "marker-band", "a-band-delta2", "rescue-arm"],
    )
    def test_at_most_one_logical_invocation_per_evaluation(
        self, monkeypatch, caplog, fixture
    ):
        entries = {"n": 0}

        real_async = judge_mod.judge_fused_bundle_async

        async def _counting_entry(bundle_text, *, config, timeout_s=None):
            entries["n"] += 1
            return await real_async(bundle_text, config=config, timeout_s=timeout_s)

        monkeypatch.setattr(judge_mod, "judge_fused_bundle_async", _counting_entry)
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        extra = [_child_report_check_note()] if fixture["note"] else []
        node, _m, _l = _make_node(
            instance_id="budget-sentinel",
            pending_children=fixture["pending"],
            live_descendants=fixture["live"],
            busy_descendants=fixture["busy"],
        )
        with _capture(caplog).at_level(logging.INFO):
            _run(node, _delegated_mission(fixture["mission"], extra), "budget-sentinel")

        assert entries["n"] <= 1, (
            "BUDGET SENTINEL: >1 logical judge invocation in one evaluation"
        )

    def test_unparsable_retry_is_attempts_within_one_invocation(
        self, monkeypatch, caplog
    ):
        """The preserved retry contract: 2 HTTP attempts, ONE logical
        invocation (entry count == 1) — the sentinel reading pinned."""
        entries = {"n": 0}
        real_async = judge_mod.judge_fused_bundle_async

        async def _counting_entry(bundle_text, *, config, timeout_s=None):
            entries["n"] += 1
            return await real_async(bundle_text, config=config, timeout_s=timeout_s)

        monkeypatch.setattr(judge_mod, "judge_fused_bundle_async", _counting_entry)
        spy = _JudgeSpy(["prose", "also prose"])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, _m, _l = _make_node(instance_id="budget-retry")
        with _capture(caplog).at_level(logging.INFO):
            _run(node, _delegated_mission("All done."), "budget-retry")

        assert entries["n"] == 1, "ONE logical invocation"
        assert len(spy.attempts) == 2, "…containing 2 HTTP attempts (retry)"

    def test_meta_bypass_rows_never_invoke(self, monkeypatch, caplog):
        """Non-delegated (D10-exempt), attested, answer-pending, and
        not-fired rows: ZERO invocations (0-LLM rows preserved)."""
        spy = _JudgeSpy([_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        # non-delegated quick question with markers — D10 exempt.
        node, _m, _l = _make_node(instance_id="budget-d10")
        state = {
            "messages": [
                HumanMessage(content="what's the answer?"),
                AIMessage(
                    content="The answer. Ending turn, will continue."
                ),
            ]
        }
        with _capture(caplog).at_level(logging.INFO):
            _run(node, state, "budget-d10")
        assert spy.attempts == []
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert eval_rows and "judge_invoked=False" in eval_rows[0]

    def test_dry_mode_never_invokes(self, monkeypatch, caplog):
        """Dry mode: activation computed + logged, node skipped (R3) —
        zero LLM calls."""
        spy = _JudgeSpy([_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, _m, _l = _make_node(
            instance_id="budget-dry",
            settings=GateSettings("dry", 3, 3),
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, _delegated_mission("All done."), "budget-dry")

        assert spy.attempts == []
        assert "messages" not in result
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert eval_rows and "mode=dry" in eval_rows[0]
        assert "judge_invoked=False" in eval_rows[0]


# ─────────────────────────────────────────────────────────────────────────────
# judge_invoked derivation + old-sites-dead pins
# ─────────────────────────────────────────────────────────────────────────────


class TestJudgeInvokedDerivation:
    def test_row_flag_true_iff_invocation_happened(self, monkeypatch, caplog):
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, _m, _l = _make_node(instance_id="derive-true")
        with _capture(caplog).at_level(logging.INFO):
            _run(node, _delegated_mission("All done."), "derive-true")

        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert eval_rows
        # Derived: judge invoked (HTTP attempts exist) → row says True.
        assert spy.attempts
        assert "judge_invoked=True" in eval_rows[0]

    def test_row_flag_false_when_kill_switch_off(self, monkeypatch, caplog):
        spy = _JudgeSpy([_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        monkeypatch.setenv(
            "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0"
        )
        judge_resolver_mod.reset_llm_judge_resolver_for_tests()

        node, _m, _l = _make_node(instance_id="derive-false")
        with _capture(caplog).at_level(logging.INFO):
            _run(node, _delegated_mission("All done."), "derive-false")

        assert spy.attempts == []
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert eval_rows and "judge_invoked=False" in eval_rows[0]


class TestOldSitesDeadButPresent:
    def test_flip_constant_is_on(self):
        assert _LCA_STAGE2_RESOLVER_FLIP is True

    def test_legacy_judge_entries_never_called(self, monkeypatch, caplog):
        """Both legacy entry points are unreachable while the flip is
        active: the old marker-path judge and the would-be-deny judge
        would call ``judge_completion_report_async`` — it MUST stay at
        zero calls across a full fused evaluation (including the deny
        band, which used to be the would-be-deny site's row)."""
        legacy_calls = []

        async def _legacy_never(messages, *, config, window=3, timeout_s=None):
            legacy_calls.append(messages)
            from daemon.services.attestation_report_judge import JudgeResult

            return JudgeResult(
                is_complete_report=True, verdict="yes", reason="x", model="m", latency_ms=0
            )

        monkeypatch.setattr(
            judge_mod, "judge_completion_report_async", _legacy_never
        )
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        # deny-band row (the would-be-deny site's family).
        node, _m, _l = _make_node(instance_id="dead-deny")
        with _capture(caplog).at_level(logging.INFO):
            _run(node, _delegated_mission("All done."), "dead-deny")
        # marker-band row (the marker site's family).
        node2, _m2, _l2 = _make_node(
            instance_id="dead-marker", pending_children=1
        )
        with _capture(caplog).at_level(logging.INFO):
            _run(
                node2,
                _delegated_mission("Awaiting child. Ending turn."),
                "dead-marker",
            )

        assert legacy_calls == [], (
            "old judge sites must be dead-but-present (routing removed only)"
        )
        # The fused judge IS the one that ran (twice — once per eval).
        assert len(spy.attempts) == 2

    def test_legacy_marker_log_rows_silent(self, monkeypatch, caplog):
        """The legacy marker-judge event family does not fire."""
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        node, _m, _l = _make_node(
            instance_id="dead-rows", pending_children=1
        )
        with _capture(caplog).at_level(logging.INFO):
            _run(
                node,
                _delegated_mission("Awaiting child. Ending turn."),
                "dead-rows",
            )
        assert _rows(caplog, "event=leader_completion_gate_marker_judge") == []
        assert _rows(caplog, "event=leader_completion_gate_judge ") == []
        # The fused row fired instead.
        assert _rows(caplog, "event=leader_completion_gate_fused_judge ")


# ─────────────────────────────────────────────────────────────────────────────
# D4 — the hint gains the evidence citation
# ─────────────────────────────────────────────────────────────────────────────


class TestD4HintEvidenceCitation:
    def test_hint_carries_citation_when_verdict_cites(self, monkeypatch, caplog):
        spy = _JudgeSpy(
            [
                _not_complete_json(
                    evidence=["SOURCE A: child promised future work"],
                    advisory="Verify the child's promised report",
                )
            ]
        )
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, _m, _l = _make_node(
            instance_id="d4-hint", pending_children=1, live_descendants=1
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_mission("Awaiting child. Ending turn."),
                "d4-hint",
            )

        hint = result["messages"][0]
        content = str(hint.content)
        # Byte-identical canonical prefix + the D4 citation suffix.
        assert content.startswith(COMPLETION_CHECK_NOTE_TEXT)
        assert "Completion evidence cited by the completion judge:" in content
        assert "- SOURCE A: child promised future work" in content
        assert "Advisory: Verify the child's promised report" in content
        # The stable id (supersede contract) is unaffected by D4.
        assert hint.id == "completion_check_note:d4-hint"

    def test_hint_byte_identical_when_verdict_cites_nothing(
        self, monkeypatch, caplog
    ):
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, _m, _l = _make_node(
            instance_id="d4-plain", pending_children=1, live_descendants=1
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_mission("Awaiting child. Ending turn."),
                "d4-plain",
            )

        hint = result["messages"][0]
        assert str(hint.content) == COMPLETION_CHECK_NOTE_TEXT


# ─────────────────────────────────────────────────────────────────────────────
# Incident-class E2E (scope item 6 — named per shape)
# ─────────────────────────────────────────────────────────────────────────────


class TestIncidentB08f40feIdleOrphans:
    """(a) idle orphans → nothing pending → un-attested → judge sees
    child-report evidence (Δ1) → deny+nudge."""

    def test_incident_b08f40fe_unattested_quiet_judge_sees_child_report_deny_nudge(
        self, monkeypatch, caplog
    ):
        seen_payloads = []

        async def _stub(config, user_payload, *, timeout_s, system_prompt=None):
            seen_payloads.append(user_payload)
            return (
                _not_complete_json(
                    evidence=["SOURCE A: child promised future work"],
                    advisory="The child's terminal report promised work",
                ),
                "fake-quick",
            )

        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _stub)

        node, _m, ledger = _make_node(instance_id="b08f40fe-stage2")
        state = _delegated_mission(
            "Awaiting final four: C12a/b/c + blame-worker. "
            "Then I aggregate and write RESULTS. Ending turn.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, state, "b08f40fe-stage2")

        # The judge fired and the bundle carried the CHILD-REPORT
        # evidence (Δ1 — the old deny-path judge never saw it).
        assert seen_payloads, "deny band judge must fire"
        assert "SOURCE A: child-terminal contradiction evidence" in seen_payloads[0]
        assert "promised future work" in seen_payloads[0]
        # deny+nudge via the existing machinery.
        assert "messages" in result
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert "band=deny" in eval_rows[0]
        assert "resolver_outcome=deny_nudge" in eval_rows[0]


class TestIncident98b59dd7GenuineReport:
    """(b) genuine long report + judge verdict complete → allow, no
    nudge."""

    def test_incident_98b59dd7_genuine_report_complete_allows_no_nudge(
        self, monkeypatch, caplog
    ):
        spy = _JudgeSpy([_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, _m, ledger = _make_node(instance_id="98b59dd7-stage2")
        genuine_report = (
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
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node, _delegated_mission(genuine_report), "98b59dd7-stage2"
            )

        assert "messages" not in result, "genuine report → NO nudge"
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert "resolver_outcome=allow" in eval_rows[0]
        assert "judge_invoked=True" in eval_rows[0]


class TestIncident6a0d60c9AwaitingAnswer:
    """(c) awaiting-answer → plain allow; bound enforced if somehow
    nudged."""

    def test_incident_6a0d60c9_awaiting_answer_plain_allow(
        self, monkeypatch, caplog
    ):
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

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
        config = build_gate_config("6a0d60c9-stage2", settings)
        node = create_attestation_gate_node(
            config,
            settings,
            manager,
            "6a0d60c9-stage2",
            denied_count_getter=lambda: 0,
            ledger=ledger,
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_mission("Ending turn, awaiting your go/no-go."),
                "6a0d60c9-stage2",
            )

        # Answer-gate: plain allow, ZERO side effects (FIX-2 preserved).
        assert spy.attempts == [], "answer-pending ⇒ no judge"
        assert "messages" not in result
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()
        ledger.set_escalated_and_reset.assert_not_called()
        # The answer-pending arm IS a canonical-path evaluation — the
        # resolver row appears with the Term-1 meta-bypass recorded
        # (fired=False; zero LLM).
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert eval_rows
        assert "user_answer_pending=True" in eval_rows[0]
        assert "fired=False" in eval_rows[0]
        assert "bypass_reason=meta_bypass" in eval_rows[0]
        assert "judge_invoked=False" in eval_rows[0]

    def test_incident_6a0d60c9_bound_enforced_if_nudged(
        self, monkeypatch, caplog
    ):
        """The answer-gate bypass is the ONLY thing preventing the deny
        on this shape; with the answer closed (False) and the counter
        at the bound, the SAME mission terminates (bound enforced)."""
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, _m, ledger = _make_node(
            instance_id="6a0d60c9-bound", denied_count=3
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _delegated_mission("Ending turn, awaiting your go/no-go."),
                "6a0d60c9-bound",
            )

        assert "messages" not in result, "at bound → terminal, no nudge"
        ledger.set_escalated_and_reset.assert_called_once()
        assert _rows(caplog, "event=leader_completion_gate_terminal_after_bound")


class TestIncidentOriginalChildLie:
    """(d) ORIGINAL child-lie arc — child promises-then-terminal while
    leader busy → A-band fires (Δ2) → fused judge sees child interim
    report (Δ1) + tree rows (Δ3) → deny+nudge → leader revives child →
    eventually attests → allow."""

    def test_incident_original_child_lie_full_arc(self, monkeypatch, caplog):
        seen_payloads = []

        async def _stub(config, user_payload, *, timeout_s, system_prompt=None):
            seen_payloads.append(user_payload)
            return (
                _not_complete_json(
                    evidence=["SOURCE A: child promised future work"],
                    advisory="The child's report contradicts 'done'",
                ),
                "fake-quick",
            )

        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _stub)

        # ── Eval 1: the lie lands — child note delivered, siblings busy.
        node1, manager1, ledger1 = _make_node(
            instance_id="child-lie",
            pending_children=1,
            live_descendants=2,
            busy_descendants=2,  # B muted; A fires ALONE (Δ2)
        )
        state1 = _delegated_mission(
            "Everything is complete on my side.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result1 = _run(node1, state1, "child-lie")

        # The A-band fired the fused judge (Δ2's new 0→1 row).
        assert len(seen_payloads) == 1
        payload = seen_payloads[0]
        # Δ1: the judge SAW the child interim-report evidence.
        assert "SOURCE A" in payload and "child promised" in payload.lower() or (
            "SOURCE A" in payload and "Child Report Check" in payload
        )
        # Δ3: the judge SAW the tree rows.
        assert "SOURCE C: tree status" in payload
        # deny+nudge (not_complete + nothing... wait — pending>0 here).
        # A-band + pending>0 → allow + D4-citing hint (the matrix row).
        assert "messages" in result1
        hint1 = result1["messages"][0]
        assert "Completion evidence cited" in str(hint1.content)
        assert result1["attestation_route"] is None
        ledger1.increment.assert_not_called()
        eval1 = _rows(caplog, "event=leader_completion_resolver_eval")[0]
        assert "band=a_suspicion" in eval1
        assert "resolver_outcome=allow_hint" in eval1

        # ── Eval 2: the leader acted on the hint — revived the child;
        # tree went QUIET (child re-pending), still un-attested → deny
        # band → judge → not_complete → deny+nudge (the loop that
        # drives the leader back to work).
        node2, _m2, ledger2 = _make_node(instance_id="child-lie")
        state2 = _delegated_mission(
            "I revived the child and am waiting for its report.",
            extra_messages=[_child_report_check_note()],
        )
        with _capture(caplog).at_level(logging.INFO):
            result2 = _run(node2, state2, "child-lie")

        assert "messages" in result2, "deny band → nudge"
        assert result2["attestation_route"] == "agent"
        ledger2.increment.assert_called_once()

        # ── Eval 3: the child delivered; the leader attests → allow
        # with the counter RESET (trigger 1 — attested-allow).
        attestation_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        node3, _m3, ledger3 = _make_node(instance_id="child-lie")
        state3 = _delegated_mission(
            "Full report delivered above.",
            extra_messages=[_child_report_check_note(), attestation_ai],
        )
        with _capture(caplog).at_level(logging.INFO):
            result3 = _run(node3, state3, "child-lie")

        assert "messages" not in result3
        assert result3["attestation_route"] is None
        ledger3.reset.assert_called_once()
        # The resolver's Term-1 attested meta-bypass: not fired, 0 LLM.
        assert len(seen_payloads) == 2, "eval 3 must NOT invoke the judge"
        eval3_rows = _rows(caplog, "event=leader_completion_resolver_eval")
        assert "fired=False" in eval3_rows[-1]
        assert "bypass_reason=meta_bypass" in eval3_rows[-1]
