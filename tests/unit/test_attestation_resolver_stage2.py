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
* **D4 hint citation RETIRED 2026-09-23 (b2f4dae9)**: the
  Completion Check Note + the hint-citation suffix are RETIRED
  end-to-end; the verdict's ``evidence_cited`` +
  ``advisory_note_text`` still flow into the resolver_eval row
  via the ``llm_judge_reason`` field (the canonical forensic
  home). The ``TestD4HintEvidenceCitationRetired`` class is the
  WITNESS that the D4 hint class was retired.
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

    def test_marker_band_unparsable_x2_with_pending_allows_log_only_not_silent(
        self, monkeypatch, caplog
    ):
        """R7-1 (marker band, path-(d)-with-pending): the conservative
        route is allow log-only (2026-09-23, b2f4dae9: the hint is
        RETIRED end-to-end) — NOT a silent plain allow (the
        resolver_eval row + the would-be-route label are the
        durable records) and NEVER a deny while work is en route."""
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

        # (b)/(d)-with-pending path is log-only — NO message, NO
        # context_kind, NO counter movement.
        assert "messages" not in result, (
            "(d)-with-pending MUST be log-only after 2026-09-23; "
            f"got messages={result.get('messages')!r}"
        )
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
    the HTTP attempt count — the retry-once-on-unparsable (incident
    98b59dd7) AND the retry-once-on-timeout (incident bc145c7e R1,
    2026-09-19) may each issue 2 HTTP attempts WITHIN one logical
    invocation; both retries fit the same ``entries == 1`` sentinel
    (no per-evaluation expansion). The unparsable retry is pinned in
    ``test_unparsable_retry_is_attempts_within_one_invocation``; the
    timeout retry is pinned in
    ``test_timeout_retry_is_attempts_within_one_invocation``."""

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

    def test_timeout_retry_is_attempts_within_one_invocation(
        self, monkeypatch, caplog
    ):
        """Incident bc145c7e R1 (2026-09-19): timeout retry mirrors the
        unparsable retry at the budget-sentinel level — 2 HTTP attempts
        within ONE logical invocation (``entries == 1`` AND
        ``len(spy.attempts) == 2``). Pinning this here CLOSES the spec
        spec-item-2 sentinel-level gap: the timeout-retry path MUST fit
        the existing budget sentinel exactly like the unparsable retry
        (no per-evaluation expansion, only a re-distribution across
        attempt-1 and attempt-2 on the rescuer path)."""

        class _TimeoutThenOkSpy(_JudgeSpy):
            async def __call__(
                self, config, user_payload, *, timeout_s, system_prompt=None
            ):
                self.attempts.append(user_payload)
                self.payloads.append(user_payload)
                self.prompts.append(system_prompt)
                if len(self.attempts) == 1:
                    raise asyncio.TimeoutError()
                return (_complete_json(), "fake-quick")

        entries = {"n": 0}
        real_async = judge_mod.judge_fused_bundle_async

        async def _counting_entry(bundle_text, *, config, timeout_s=None):
            entries["n"] += 1
            return await real_async(bundle_text, config=config, timeout_s=timeout_s)

        monkeypatch.setattr(judge_mod, "judge_fused_bundle_async", _counting_entry)
        spy = _TimeoutThenOkSpy([])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)

        node, _m, _l = _make_node(instance_id="budget-timeout-retry")
        with _capture(caplog).at_level(logging.INFO):
            _run(
                node,
                _delegated_mission("All done."),
                "budget-timeout-retry",
            )

        assert entries["n"] == 1, (
            "BUDGET SENTINEL: timeout retry MUST stay within ONE "
            "logical invocation"
        )
        assert len(spy.attempts) == 2, (
            "BUDGET SENTINEL: timeout retry MUST issue exactly 2 HTTP "
            "attempts within the single invocation"
        )

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


class TestLegacySitesDeleted:
    """Stage 3 (2026-09-17, R7): the two legacy judge sites are not
    dead-but-present anymore — they are DELETED. The flip constant is
    gone (no runtime toggle by design; revert = redeploy an earlier
    build), the legacy judge service entry points no longer exist, and
    the legacy event family can never fire because no code path can
    reach it. Complementary structural pins live in
    ``tests/unit/test_attestation_stage3_census.py``; these behavioral
    pins exercise the live node."""

    def test_flip_constant_deleted_from_graph_module(self):
        import daemon.graph as graph_module

        assert not hasattr(graph_module, "_LCA_STAGE2_RESOLVER_FLIP")

    def test_legacy_judge_symbols_deleted(self):
        assert not hasattr(judge_mod, "judge_completion_report_async")
        assert not hasattr(judge_mod, "judge_completion_report_sync")
        assert not hasattr(judge_mod, "JudgeResult")

    def test_fused_judge_serves_both_legacy_families(self, monkeypatch, caplog):
        """The deny band (the would-be-deny site's family) and the
        marker band (the marker site's family) both route through the
        ONE fused judge — two evaluations, two fused invocations, zero
        legacy rows."""
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

        # The fused judge IS the one that ran (twice — once per eval).
        assert len(spy.attempts) == 2
        assert _rows(caplog, "event=leader_completion_gate_fused_judge ")

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
# D4 — the hint citation class is RETIRED 2026-09-23 (b2f4dae9)
# ─────────────────────────────────────────────────────────────────────────────
#
# The D4 evidence-citation contract (hint carries the fused judge's
# verdict's evidence_cited + advisory_note_text) is RETIRED along with
# the hint itself — the (b)/(d)-with-pending route resolves to allow
# log-only, no message is injected, no citation suffix is minted.
# The verdict's evidence_cited + advisory_note_text still flow into
# the resolver_eval row log (the b2f4dae9 evidence chain remains
# log-reconstructible) — that's the new home for the D4 forensic
# surface. The class below is preserved as a stub so the surrounding
# R7-pin classes stay organized, with the actual D4 tests removed.


class TestD4HintEvidenceCitationRetired:
    def test_d4_hint_class_retired_after_b2f4dae9(self):
        """2026-09-23 (b2f4dae9): D4 hint-citation RETIRED end-to-end.

        The fused judge's verdict JSON still carries ``evidence_cited``
        + ``advisory_note_text`` fields; the
        ``leader_completion_gate_fused_judge`` log row still carries
        the ``llm_judge_reason`` field (the canonical forensic home
        for the D4 surface). The hint-mint path that built the
        byte-identical canonical prefix + D4 citation suffix is
        RETIRED; the surface that lives is the log row. This test
        is the WITNESS that the class was retired — the live
        forensic-surface tests live in
        ``tests/unit/test_attestation_fused_judge.py`` (the
        ``test_*_log_discrimination`` family covers the
        attempt-1 / retry / post-retry log shapes).
        """
        # The hint factory is gone — assert it cannot be imported.
        import daemon.graph

        assert not hasattr(daemon.graph, "_make_completion_check_note_message"), (
            "D4 hint factory MUST stay retired (incident b2f4dae9); "
            "if this assertion fails, the factory was re-introduced "
            "without re-anchoring the contract — open a follow-up."
        )
        # The constant is gone too.
        assert not hasattr(daemon.graph, "COMPLETION_CHECK_NOTE_TEXT"), (
            "COMPLETION_CHECK_NOTE_TEXT MUST stay retired (incident "
            "b2f4dae9); if this assertion fails, the constant was "
            "re-introduced without re-anchoring the contract — open a "
            "follow-up."
        )
        # The canonical id-format table row is gone.
        from daemon.services.context_messages import _stable_id_for

        import pytest as _pytest

        with _pytest.raises(ValueError) as exc_info:
            _stable_id_for("completion_check_note", instance_id="any-iid")
        assert "completion_check_note" not in str(exc_info.value) or (
            "unknown kind" in str(exc_info.value)
        ), (
            "the kind sentinel was removed from the table; if this "
            "assertion fails, the kind branch was re-introduced"
        )


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
        # A-band + pending>0 → ALLOW log-only (2026-09-23 b2f4dae9:
        # the hint is RETIRED end-to-end; the resolver row carries
        # the (b)/(d)-with-pending label as the durable record).
        assert "messages" not in result1, (
            "child-lie Eval 1 (A-band + pending) MUST be log-only "
            "after 2026-09-23; "
            f"got messages={result1.get('messages')!r}"
        )
        assert result1["attestation_route"] is None
        ledger1.increment.assert_not_called()
        eval1 = _rows(caplog, "event=leader_completion_resolver_eval")[0]
        assert "band=a_suspicion" in eval1
        assert "resolver_outcome=allow_hint" in eval1
        # would_be_route is on the [AttestationGate] log line, not the
        # resolver_eval row — pin it separately.
        gate_rows = _rows(caplog, "[AttestationGate] fused-judge")
        assert any("would_be_route=allow_hint" in r for r in gate_rows), (
            f"would_be_route=allow_hint MUST appear on the "
            f"[AttestationGate] log line; got rows={gate_rows!r}"
        )

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
        # 2026-09-19 attest-first contract: the FINAL AIMessage
        # MUST be a standalone text report (no tool calls, >=
        # SHORT_REPORT_WORD_THRESHOLD = 150 words) for the gate
        # to allow END via Decision.ALLOWED. The clean attest_call
        # is the SECOND-TO-LAST AIMessage (empty content +
        # attest tool_call); the FULL REPORT is the LAST
        # AIMessage (long, no tool calls).
        attestation_ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "a1"}
            ],
        )
        # Long standalone text report — same shape as the unit
        # test fixture ``report_ai()`` but kept inline so the
        # incident-arc test stays self-contained.
        full_report_text = (
            "The work is finished. All four patches shipped; the "
            "test matrix is green; the integration tests pass on "
            "every environment we maintain. Patch 1 fixed the "
            "off-by-one in the cache TTL calculator; the unit "
            "tests now exercise both the elapsed-second and "
            "wall-clock-second boundaries at the second and "
            "minute granularity. Patch 2 cleaned up the dead "
            "imports in the worker pool module after the "
            "migration, removing the legacy compatibility shim "
            "and the related test scaffolding. Patch 3 refactored "
            "the error-reporting decorator so the stack-frame "
            "metadata is consistent across all four call sites in "
            "the graph node and the manager facade. Patch 4 added "
            "the missing operator-boot log line for the new "
            "resolver module so operators can grep the boot "
            "summary for the resolved effective values. All four "
            "patches passed their respective suites on the first "
            "run with no flake; the integration matrix is green "
            "end-to-end across all environments we maintain. No "
            "follow-ups outstanding; the mission is complete and "
            "ready for review by the next teammate in the chain."
        )
        node3, _m3, ledger3 = _make_node(instance_id="child-lie")
        state3 = _delegated_mission(
            full_report_text,
            extra_messages=[
                _child_report_check_note(),
                attestation_ai,
            ],
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
