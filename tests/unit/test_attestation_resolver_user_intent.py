"""LCA fused-judge section U — user-intent enrichment (incident 4dfded83).

Phase B implementation tests for the 2026-09-18 judge intent-blindness
incident: the fused bundle carried A/B/C but NEVER the user's original
request, so the judge scored report-shape/tree-status and denied an
informal-but-genuinely-answering final message (denial epoch
``7e8a6323-6166-5a51-bd47-592957a6948c``; the leader's step-297 answer
fully answered the user's Q1 at 04:42:08 UTC, yet the step-298
Completion Check Nudge fired anyway). Fix under test:

* **Section U** in ``assemble_fused_bundle`` — the user's original
  request (the delegation scanner's already-computed
  ``last_real_user_index`` anchor, CONTENT extracted — no re-walk, no
  new DB reads), capped at :data:`BUNDLE_U_SECTION_MAX` (2000),
  id-redacted, OMITTED entirely when the anchor is absent
  (``user_message_included=False``). Total budget: 12000 → 14000 with U
  ADDITIVE (the A/B/C caps and their pins untouched).
* **Intent-fulfillment instruction** in ``FUSED_JUDGE_SYSTEM_PROMPT`` —
  a message that genuinely ANSWERS/FULFILLS the user's request IS a
  completion report regardless of formality/shape; a formal-looking
  report that ignores the ask is NOT complete.
* **Judge budget unchanged** — U is input enrichment at the ONE
  existing call site (``judge_fused_bundle_async``); ≤1 logical
  invocation per evaluation (spy-pinned here again).
* **Witnesses** — ``u_chars`` / ``user_message_included`` on
  :class:`FusedBundle` + ``bundle_u_chars=`` / ``user_message_included=``
  on the ``event=leader_completion_resolver_eval`` row.

LLM judge is mocked at the strict-JSON seam (``_invoke_judge_llm``) per
the family convention.
"""
from __future__ import annotations

import asyncio
import dataclasses
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
from daemon.services import attestation_resolver_activation as ara
from daemon.services.attestation_resolver_activation import (
    BUNDLE_A_SECTION_MAX,
    BUNDLE_B_SECTION_MAX,
    BUNDLE_C_SECTION_MAX,
    BUNDLE_TOTAL_MAX,
    BUNDLE_U_SECTION_MAX,
    SourceASignals,
    SourceBSignals,
    SourceCSignals,
    assemble_fused_bundle,
    emit_resolver_eval_row,
    evaluate_resolver_activation,
)
from daemon.graph import create_attestation_gate_node


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / constants (incident-verbatim where marked)
# ─────────────────────────────────────────────────────────────────────────────

#: Verbatim user Q1 from incident 4dfded83 (2026-09-18 04:42:08 UTC).
USER_QUESTION = "what is current service tool description that agents will see?"

#: A UUID the user might legitimately quote in its ask (redaction pin).
USER_QUOTED_UUID = "11111111-aaaa-bbbb-cccc-222222222222"

#: Informal-but-genuine answer (the step-297 shape): >150 words (no
#: length trigger), ZERO MID_WORK_MARKERS phrases (no marker band →
#: quiet-tree deny band), no formal report headers. Deliberately
#: conversational — the incident's whole point.
INFORMAL_ANSWER = (
    "Sure — right now the service_* tools are described to agents in "
    "three layers. Layer 1 is the tool registry entry: each service tool "
    "carries a short name plus a one-line description string that shows "
    "up in the agent's tool list. Layer 2 is the docstring rendered into "
    "the tool schema the model actually sees at call time, including the "
    "argument hints. Layer 3 is the category note the loader appends "
    "when the tool category is explicitly allowed in meta.json, which "
    "explains when the tool should be preferred over generic "
    "alternatives. So an agent listing its tools sees the layer-1 line, "
    "and once it inspects or calls the tool the model receives the "
    "layer-2 schema text, with the layer-3 category note visible "
    "whenever the category allow-list put it there. Nothing else "
    "decorates the description today — no per-agent overrides, no "
    "runtime rewriting. If you want the exact rendered text for one "
    "specific tool, name it and I'll print its registry entry and "
    "schema verbatim so you can compare all three layers side by side."
)

#: Formal-shaped report that IGNORES the user's ask entirely (the
#: intent-mismatch shape): proper headers, confident tone, but about a
#: different topic. Also >150 words and marker-free so the band stays
#: deny (quiet tree) — comparable to the intent-match test.
FORMAL_IGNORES_ASK = (
    "# Mission Report\n\n"
    "## Summary\n"
    "The scheduled refactor of the notification dispatcher is complete. "
    "All three child workers reported clean exits and the integration "
    "checklist closed with zero open items. The migration window "
    "finished ahead of schedule and every acceptance criterion in the "
    "original tasking document is satisfied.\n\n"
    "## Outcomes\n"
    "- Dispatcher module extracted behind a stable interface and the "
    "old call sites migrated.\n"
    "- Retry policy tightened; queue depth metrics wired to the "
    "dashboard with alerts at the agreed thresholds.\n"
    "- Legacy shim deleted after the cutover window closed; the "
    "deprecation notice shipped in the changelog.\n"
    "- Documentation updated across the operator guide and the API "
    "reference.\n\n"
    "## Verification\n"
    "The full regression pack ran green on the release candidate, the "
    "load probe held p95 latency inside the agreed envelope, and the "
    "rollback rehearsal completed without intervention.\n\n"
    "## Follow-ups\n"
    "None. Changelog entry added and the release notes carry the "
    "migration paragraph. The work is finished and accounted for."
)

#: Marker-free sanity: the marker catalog phrases that would flip the
#: band if they leaked into a fixture.
_FORBIDDEN_SUBSTRINGS = (
    "ending turn",
    "awaiting",
    "interim",
    "in progress",
    "still pending",
    "will write",
    "will aggregate",
    "stand by",
    "to be continued",
    "will report back",
    "not a completion report",
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


def _assert_marker_free(text: str) -> None:
    low = text.lower()
    for phrase in _FORBIDDEN_SUBSTRINGS:
        assert phrase not in low, f"vacuous fixture: marker phrase {phrase!r} leaked"


def _assert_word_count_ge_150(text: str) -> None:
    assert len(text.split()) >= 150, (
        "vacuous fixture: <150 words would flip the band to the "
        "length-trigger marker path instead of the quiet-tree deny band"
    )


_assert_marker_free(USER_QUESTION)
_assert_marker_free(INFORMAL_ANSWER)
_assert_marker_free(FORMAL_IGNORES_ASK)
_assert_word_count_ge_150(INFORMAL_ANSWER)
_assert_word_count_ge_150(FORMAL_IGNORES_ASK)


class _JudgeSpy:
    """Recording stub for ``_invoke_judge_llm`` (strict-JSON seam).

    Counts HTTP ATTEMPTS (the retry is 2 attempts INSIDE one logical
    invocation) and captures the judge's user payload (the bundle text)
    + system prompt, so tests pin WHAT the judge was shown.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.attempts: list[str] = []
        self.prompts: list = []

    async def __call__(self, config, user_payload, *, timeout_s, system_prompt=None):
        self.attempts.append(user_payload)
        self.prompts.append(system_prompt)
        if len(self.responses) == 1:
            return (self.responses[0], "fake-quick")
        return (self.responses[len(self.attempts) - 1], "fake-quick")


def _complete_json() -> str:
    return json.dumps(
        {
            "verdict": "complete",
            "evidence_cited": ["answers the user's question"],
            "advisory_note_text": "",
            "rationale": "fulfills the request",
        }
    )


def _not_complete_json() -> str:
    return json.dumps(
        {
            "verdict": "not_complete",
            "evidence_cited": ["formal report present"],
            "advisory_note_text": "the user's ask was not addressed",
            "rationale": "intent unmet",
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

    settings = GateSettings("enforce", 3, 3)
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


def _delegation_ai() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": "child-id"}, "id": "c1"}
        ],
    )


def _incident_mission(final_text: str) -> dict:
    """The 4dfded83 message shape: real user ask → delegation → final."""
    return {
        "messages": [
            HumanMessage(content=USER_QUESTION),
            _delegation_ai(),
            AIMessage(content=final_text),
        ]
    }


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
    for logger_name in (
        "daemon.graph",
        "daemon.services.attestation_gate",
        "daemon.services.attestation_resolver_activation",
    ):
        caplog.set_level(logging.INFO, logger=logger_name)
    return caplog


def _minimal_signals():
    a = SourceASignals(
        advisory_present=False,
        phrase_match=False,
        contradiction_flag=False,
        word_count_below_threshold=False,
        promise_terms_total=0,
        evidence=(),
    )
    b = SourceBSignals(False, (), False, 500, False)
    c = SourceCSignals(0, 0, 0, 0, False)
    return a, b, c


# ─────────────────────────────────────────────────────────────────────────────
# Constant + prompt identity pins
# ─────────────────────────────────────────────────────────────────────────────


class TestCapsAndPromptPins:
    def test_total_budget_14000_with_u_additive(self):
        """The chosen decomposition: total 12000→14000, U additive."""
        assert BUNDLE_TOTAL_MAX == 14000
        assert BUNDLE_U_SECTION_MAX == 2000
        # Pre-existing per-section caps untouched (their pins must hold).
        assert BUNDLE_A_SECTION_MAX == 3000
        assert BUNDLE_B_SECTION_MAX == 6000
        assert BUNDLE_C_SECTION_MAX == 3000

    def test_judge_output_cap_unchanged(self):
        assert judge_mod.FUSED_JUDGE_MAX_OUTPUT_CHARS == 2048

    def test_prompt_enumerates_four_sections_with_u(self):
        prompt = judge_mod.FUSED_JUDGE_SYSTEM_PROMPT
        assert "four sections" in prompt
        assert "SOURCE U (the user's original request for this mission)" in prompt
        assert "SOURCE A (child-report advisories" in prompt
        assert "SOURCE B (the lead's own recent messages)" in prompt
        assert "SOURCE C (the live status of the lead's descendant instances)" in prompt

    def test_prompt_carries_intent_fulfillment_instruction(self):
        prompt = judge_mod.FUSED_JUDGE_SYSTEM_PROMPT
        assert "INTENT FULFILLMENT (SOURCE U)" in prompt
        assert "genuinely ANSWERS or" in prompt
        assert "FULFILLS the user's request IS a completion report" in prompt
        assert "regardless of its" in prompt
        assert "formality" in prompt
        assert "formal-looking report that does NOT" in prompt
        assert "NOT complete" in prompt

    def test_prompt_contract_unchanged(self):
        """The strict-JSON single-line contract + conservative default
        survive byte-compatibly (the retry/parser seam depends on it)."""
        prompt = judge_mod.FUSED_JUDGE_SYSTEM_PROMPT
        assert '{"verdict": "complete"|"not_complete", ' in prompt
        assert '"evidence_cited": ' in prompt
        assert '"advisory_note_text": ' in prompt
        assert '"rationale": ' in prompt
        assert "Be CONSERVATIVE" in prompt
        assert '"not_complete"' in prompt
        assert "No markdown, no prose, no code fences, no commentary." in prompt
        assert "\n" not in prompt, "prompt must stay a single line"


# ─────────────────────────────────────────────────────────────────────────────
# Section U — bundle-level unit pins
# ─────────────────────────────────────────────────────────────────────────────


class TestBundleSectionU:
    def _assemble(self, user_message=None, ai_tail=None):
        a, b, c = _minimal_signals()
        return assemble_fused_bundle(
            a_signals=a,
            b_signals=b,
            c_signals=c,
            c_tree_rows=[],
            ai_tail_messages=ai_tail
            if ai_tail is not None
            else [AIMessage(content="done.")],
            user_intent_message=user_message,
        )

    def test_u_included_header_and_content(self):
        bundle = self._assemble(HumanMessage(content=USER_QUESTION))
        assert "=== SOURCE U: the user's original request for this mission ===" in bundle.text
        assert USER_QUESTION in bundle.text
        assert bundle.u_chars > 0
        assert bundle.user_message_included is True

    def test_u_omitted_when_anchor_absent(self):
        bundle = self._assemble(None)
        assert "SOURCE U" not in bundle.text
        assert bundle.u_chars == 0
        assert bundle.user_message_included is False

    def test_u_fail_closed_for_non_user_message(self):
        """The predicate re-check: a non-user message must NEVER render
        as the user's request (else the judge scores intent-fulfillment
        against an injected nudge/report)."""
        # An injected attestation nudge HumanMessage (excluded by the
        # scanner's ladder) passed by a miswired caller → omitted.
        nudge = HumanMessage(
            content="Completion Check Nudge — deliver a Mission Report.",
            additional_kwargs={"attestation_nudge": True},
        )
        bundle = self._assemble(nudge)
        assert bundle.user_message_included is False
        assert "SOURCE U" not in bundle.text
        # An AIMessage anchor → omitted.
        bundle = self._assemble(AIMessage(content=FORMAL_IGNORES_ASK))
        assert bundle.user_message_included is False

    def test_u_clipped_at_cap_with_truncation_ellipsis(self):
        long_ask = (
            USER_QUESTION
            + " Also: "
            + ("detail " * 600)
            + " TAIL_SENTINEL_BEYOND_CAP"
        )
        bundle = self._assemble(HumanMessage(content=long_ask))
        assert bundle.u_chars <= BUNDLE_U_SECTION_MAX
        assert "TAIL_SENTINEL_BEYOND_CAP" not in bundle.text
        assert "…" in bundle.text  # the _clip ellipsis marker

    def test_u_redacts_uuids_in_user_prose(self):
        ask = f"what does agent {USER_QUOTED_UUID} see in its tool list?"
        bundle = self._assemble(HumanMessage(content=ask))
        assert USER_QUOTED_UUID not in bundle.text
        assert "<redacted-user-1>" in bundle.text

    def test_u_does_not_disturb_other_sections(self):
        with_u = self._assemble(HumanMessage(content=USER_QUESTION))
        without_u = self._assemble(None)
        assert with_u.a_chars == without_u.a_chars
        assert with_u.b_chars == without_u.b_chars
        assert with_u.c_chars == without_u.c_chars
        assert "SOURCE A:" in with_u.text and "SOURCE A:" in without_u.text

    def test_u_section_precedes_a_b_c_in_bundle(self):
        """Intent-first emission order: U before A/B/C.

        Pinned by reviewer finding (U appended last in the shipped diff;
        the judge's enumeration in ``FUSED_JUDGE_SYSTEM_PROMPT`` already
        lists U first — the delivered bundle must match). Without this
        pin, a future maintainer could silently re-sort and ship
        bundle-vs-prompt order drift.
        """
        bundle = self._assemble(HumanMessage(content=USER_QUESTION))
        text = bundle.text
        u_idx = text.index("=== SOURCE U:")
        a_idx = text.index("=== SOURCE A:")
        b_idx = text.index("=== SOURCE B:")
        c_idx = text.index("=== SOURCE C:")
        assert u_idx < a_idx < b_idx < c_idx, (
            f"bundle order must be U→A→B→C; got U={u_idx} A={a_idx} "
            f"B={b_idx} C={c_idx}"
        )
        # Mirror: the judge prompt itself lists U before A.
        prompt = judge_mod.FUSED_JUDGE_SYSTEM_PROMPT
        assert prompt.index("SOURCE U") < prompt.index("SOURCE A"), (
            "prompt must enumerate SOURCE U before SOURCE A"
        )

    def test_anchor_absent_does_not_promote_a(self):
        """When U is omitted, A stays first — no phantom-empty-U header."""
        bundle = self._assemble(None)
        text = bundle.text
        assert "SOURCE U" not in text
        a_idx = text.index("=== SOURCE A:")
        # A is still the first section header in the rendered bundle.
        assert a_idx == text.index("=== SOURCE A:")
        b_idx = text.index("=== SOURCE B:")
        c_idx = text.index("=== SOURCE C:")
        assert a_idx < b_idx < c_idx


class TestBundleWitnessesAndBudget:
    def _assemble(self, user_message=None):
        a, b, c = _minimal_signals()
        return assemble_fused_bundle(
            a_signals=a,
            b_signals=b,
            c_signals=c,
            c_tree_rows=[],
            ai_tail_messages=[AIMessage(content="done.")],
            user_intent_message=user_message,
        )

    def test_witness_fields_exist_and_flip_with_u(self):
        names = {f.name for f in dataclasses.fields(ara.FusedBundle)}
        assert {"u_chars", "user_message_included"} <= names
        without_u = self._assemble(None)
        with_u = self._assemble(HumanMessage(content=USER_QUESTION))
        assert without_u.sha256 != with_u.sha256
        assert without_u.total_chars < with_u.total_chars
        assert with_u.u_chars > 0
        assert with_u.user_message_included is True
        assert without_u.u_chars == 0
        assert without_u.user_message_included is False

    def test_legacy_maxed_shape_never_clips_at_new_total(self):
        """A+B/C all maxed WITHOUT U fits ≤14000 with NO truncation
        marker — the budget raise means the pre-U bundle shape is never
        hard-clipped (it previously clipped at 12000)."""
        long_tail = [AIMessage(content="x" * 5900)] * 4  # >3 AIMessages; B clips
        a, b, c = _minimal_signals()
        bundle = assemble_fused_bundle(
            a_signals=a,
            b_signals=b,
            c_signals=c,
            c_tree_rows=[],
            ai_tail_messages=long_tail,
        )
        assert bundle.total_chars <= BUNDLE_TOTAL_MAX
        assert "[bundle truncated at total cap]" not in bundle.text

    def test_maxed_a_b_c_u_stays_within_total_cap(self):
        """A+B+C each at their per-section caps + U at 2000 fits ≤14000
        WITHOUT the hard clip — the additive decomposition (total raised
        by exactly the U cap) makes the total clip DEFENSIVE-ONLY: no
        section's content is ever sacrificed at the total boundary."""
        big_note = (
            ara.ChildReportCheckEvidence(
                child_instance_id=str(uuid.uuid4()),
                matched_terms=("ending turn",),
                note_excerpt="note " * 700,
                stable_id="child_report_check:x",
                kwargs_surface_seen=True,
            ),
        )
        a = SourceASignals(
            advisory_present=True,
            phrase_match=True,
            contradiction_flag=False,
            word_count_below_threshold=False,
            promise_terms_total=1,
            evidence=big_note,
        )
        b = SourceBSignals(False, (), False, 500, False)
        c = SourceCSignals(0, 0, 0, 0, False)
        bundle = assemble_fused_bundle(
            a_signals=a,
            b_signals=b,
            c_signals=c,
            c_tree_rows=[],
            ai_tail_messages=[AIMessage(content="y" * 5800)],
            user_intent_message=HumanMessage(content="ask " * 500),
        )
        # U sits exactly at its cap (clipped, not sacrificed); the
        # assembled total stays inside the budget.
        assert bundle.u_chars == BUNDLE_U_SECTION_MAX
        assert bundle.total_chars <= BUNDLE_TOTAL_MAX
        assert "[bundle truncated at total cap]" not in bundle.text


class TestEvaluatePassThrough:
    """The evaluate_resolver_activation pass-through (gate → bundle)."""

    def _evaluate(self, user_message):
        a, b, c = _minimal_signals()
        return evaluate_resolver_activation(
            instance_id="iid-u-pass",
            gate_location="unit",
            leader_prompt_version="test",
            messages=[
                HumanMessage(content=USER_QUESTION),
                _delegation_ai(),
                AIMessage(content=INFORMAL_ANSWER),
            ],
            mode="enforce",
            attestation_enabled=True,
            scope_applicable=True,
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            c_values=c,
            b_values=b,
            denied_count=0,
            deny_bound=3,
            old_decision=Decision.ALLOWED,
            user_intent_message=user_message,
        )

    def test_bundle_carries_u_when_message_passed(self):
        snap = self._evaluate(HumanMessage(content=USER_QUESTION))
        assert snap.result.fired is True
        assert snap.result.bundle is not None
        assert snap.result.bundle.user_message_included is True
        assert USER_QUESTION in snap.result.bundle.text

    def test_bundle_omits_u_when_none(self):
        snap = self._evaluate(None)
        assert snap.result.bundle is not None
        assert snap.result.bundle.user_message_included is False
        assert "SOURCE U" not in snap.result.bundle.text


class TestEvalRowUFields:
    """``bundle_u_chars=`` / ``user_message_included=`` on the row."""

    def _snapshot(self, user_message):
        a, b, c = _minimal_signals()
        return evaluate_resolver_activation(
            instance_id="iid-row",
            gate_location="unit",
            leader_prompt_version="test",
            messages=[
                HumanMessage(content=USER_QUESTION),
                _delegation_ai(),
                AIMessage(content=INFORMAL_ANSWER),
            ],
            mode="enforce",
            attestation_enabled=True,
            scope_applicable=True,
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            c_values=c,
            b_values=b,
            denied_count=0,
            deny_bound=3,
            old_decision=Decision.ALLOWED,
            user_intent_message=user_message,
        )

    def test_row_logs_user_message_included_true(self, caplog):
        snap = self._snapshot(HumanMessage(content=USER_QUESTION))
        with _capture(caplog).at_level(logging.INFO):
            emit_resolver_eval_row(
                snap,
                judge_invoked=True,
                judge_verdict="complete",
                resolver_outcome=ara.RESOLVER_OUTCOME_ALLOW,
            )
        rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert rows, "expected the resolver_eval row"
        # NOTE: the row renders Python bools (%s) — the convention of
        # every other boolean field on this row (fail_open=, marker_hit=).
        assert "user_message_included=True" in rows[0]
        assert "user_message_included=False" not in rows[0]
        assert "bundle_u_chars=0" not in rows[0]
        assert "bundle_u_chars=" in rows[0]

    def test_row_logs_user_message_included_false_when_omitted(self, caplog):
        snap = self._snapshot(None)
        with _capture(caplog).at_level(logging.INFO):
            emit_resolver_eval_row(
                snap,
                judge_invoked=False,
                judge_verdict="<none>",
                resolver_outcome=ara.RESOLVER_OUTCOME_ALLOW,
            )
        rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert rows
        assert "user_message_included=False" in rows[0]
        assert "bundle_u_chars=0" in rows[0]


# ─────────────────────────────────────────────────────────────────────────────
# Node-level incident shapes (gate wiring + judge path, judge mocked)
# ─────────────────────────────────────────────────────────────────────────────


class TestIncident4dfded83IntentMatch:
    """THE incident shape: informal answer that genuinely answers the
    ask + verdict complete → rescue → ALLOW (no nudge, no counter)."""

    def test_informal_genuine_answer_judge_complete_allows(
        self, monkeypatch, caplog
    ):
        spy = _JudgeSpy([_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        node, manager, ledger = _make_node(instance_id="u-incident-allow")
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _incident_mission(INFORMAL_ANSWER),
                "u-incident-allow",
            )

        # Exactly ONE logical invocation (U is input enrichment only).
        assert len(spy.attempts) == 1
        # The judge was shown the user's ask (section U) + the new prompt.
        assert USER_QUESTION in spy.attempts[0]
        assert "=== SOURCE U: the user's original request for this mission ===" in spy.attempts[0]
        assert "INTENT FULFILLMENT" in (spy.prompts[0] or "")
        assert "SOURCE U" in (spy.prompts[0] or "")
        # Rescue → allow: no nudge injected, no ledger movement.
        assert "messages" not in result, "complete verdict MUST NOT nudge"
        assert result.get("attestation_route") is None
        ledger.increment.assert_not_called()
        # Row witnesses.
        rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert rows and "user_message_included=True" in rows[0]
        assert "resolver_outcome=allow" in rows[0]
        assert "judge_invoked=True" in rows[0]


class TestIntentMismatch:
    """Formal-looking report that ignores the ask + verdict
    not_complete → deny+nudge (the conservative path is unchanged)."""

    def test_formal_report_ignoring_ask_judged_not_complete_denies(
        self, monkeypatch, caplog
    ):
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        node, manager, ledger = _make_node(instance_id="u-mismatch")
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node,
                _incident_mission(FORMAL_IGNORES_ASK),
                "u-mismatch",
            )

        assert len(spy.attempts) == 1
        # The judge HAD the intent available (U present in the payload)
        # and still mapped not_complete conservatively.
        assert USER_QUESTION in spy.attempts[0]
        assert "messages" in result, "not_complete MUST deny+nudge"
        assert result["attestation_route"] == "agent"
        nudge = result["messages"][0]
        assert nudge.additional_kwargs.get("attestation_nudge") is True
        ledger.increment.assert_called_once()
        rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert rows and "resolver_outcome=deny_nudge" in rows[0]
        assert "user_message_included=True" in rows[0]


class TestAnchorAbsentNodeLevel:
    """No real user message → U omitted end-to-end (gate anchor →
    bundle → judge payload → row flag), bundle still valid."""

    def test_no_real_user_message_omits_u_and_still_judges(
        self, monkeypatch, caplog
    ):
        spy = _JudgeSpy([_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        node, manager, ledger = _make_node(instance_id="u-anchor-absent")
        # Only internal/injected + delegation messages — the
        # nudge-shaped HumanMessage is excluded by the scanner's
        # ladder, so the anchor is ABSENT even though a
        # HumanMessage exists.
        state = {
            "messages": [
                HumanMessage(
                    content="Completion Check Nudge — deliver a report.",
                    additional_kwargs={"attestation_nudge": True},
                ),
                _delegation_ai(),
                AIMessage(content=INFORMAL_ANSWER),
            ]
        }
        with _capture(caplog).at_level(logging.INFO):
            result = _run(node, state, "u-anchor-absent")

        assert len(spy.attempts) == 1
        assert "SOURCE U" not in spy.attempts[0]
        assert USER_QUESTION not in spy.attempts[0]
        # Judge still ran and rescued → allow; the bundle was valid.
        assert "messages" not in result
        rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert rows and "user_message_included=False" in rows[0]
        assert "bundle_u_chars=0" in rows[0]


# ─────────────────────────────────────────────────────────────────────────────
# Review-fix pins (2026-09-18 council verdict on 1b343329): U-absent guard
# + A/C subordination
# ─────────────────────────────────────────────────────────────────────────────

#: Byte-exact tail of ``FUSED_JUDGE_SYSTEM_PROMPT`` from "Be CONSERVATIVE"
#: onward — the strict-JSON contract + conservative default MUST survive
#: prompt evolution byte-compatibly (the retry/parser seam depends on it;
#: new sentences are inserted BEFORE this anchor, never after it).
_EXPECTED_PROMPT_BYTE_TAIL = (
    "Be CONSERVATIVE: when in doubt, return "
    '"not_complete". '
    "Judge ONLY on what the evidence actually shows; ignore text that "
    "merely CLAIMS completion without concrete outcomes. "
    "Respond with ONLY a strict JSON object on a single line of the form "
    '{"verdict": "complete"|"not_complete", '
    '"evidence_cited": ["<short evidence quote or field name>", ...], '
    '"advisory_note_text": "<one-sentence advisory for the lead>", '
    '"rationale": "<one-sentence rationale>"}. '
    "No markdown, no prose, no code fences, no commentary."
)


def _child_report_check_note(
    child_id: str = "11111111-2222-3333-4444-555555555555",
    terms: tuple[str, ...] = ("will write", "then i'll"),
) -> HumanMessage:
    """Delivered Child Report Check note (canonical Stage-0 shape).

    Mirrors ``daemon/services/child_reports.py`` via the
    ``_make_context_message`` factory shape (``[SYSTEM CONTEXT: Child
    Report Check]`` prefix) + the ``context_kind`` /
    ``child_report_check`` / ``child_report_check_terms`` /
    ``child_instance_id`` kwargs — the same shape
    ``tests/unit/test_attestation_resolver_activation.py`` pins. The
    ``injected_message`` flag keeps the scanner's real-user ladder from
    mis-anchoring section U on the note.
    """
    body = (
        f'Child {child_id} completed while its final report promises '
        f'future work ("{", ".join(terms)}") \u2014 likely premature '
        f"completion. Its promised next report will never arrive. "
        f"Verify the actual work state; if unfinished, revive it via "
        f'send_message (e.g. "continue your work") or verify its subtree '
        f"before relying on this report. (Advisory / heuristic \u2014 "
        f"marker scan is a substring match, not an LLM verdict.)"
    )
    msg = HumanMessage(content=f"[SYSTEM CONTEXT: Child Report Check]\n\n{body}")
    msg.additional_kwargs["context_kind"] = "child_report_check"
    msg.additional_kwargs["injected_message"] = True
    msg.additional_kwargs["child_report_check"] = True
    msg.additional_kwargs["child_report_check_terms"] = list(terms)
    msg.additional_kwargs["child_instance_id"] = child_id
    return msg


class TestUAbsentGuardBaseParity:
    """U-absent guard: the prompt carries the absence rule VERBATIM and
    absence causes ZERO decision-path branching — a fixed anchor-absent
    transcript (delegation happened, no real user message) produces
    BASE behavior under BOTH mocked verdicts."""

    @staticmethod
    def _delegation_only_state():
        # The TestAnchorAbsentNodeLevel shape: the nudge-shaped
        # HumanMessage is excluded by the scanner's ladder → anchor
        # ABSENT (no real user message anywhere in the tail).
        return {
            "messages": [
                HumanMessage(
                    content="Completion Check Nudge — deliver a report.",
                    additional_kwargs={"attestation_nudge": True},
                ),
                _delegation_ai(),
                AIMessage(content=INFORMAL_ANSWER),
            ]
        }

    def test_u_absent_guard_produces_base_behavior_both_verdicts(
        self, monkeypatch, caplog
    ):
        # (iv) the guard sentence exists at prompt level, verbatim.
        prompt = judge_mod.FUSED_JUDGE_SYSTEM_PROMPT
        assert (
            "If SOURCE U is absent, judge on A/B/C alone - do not infer "
            "the user's request." in prompt
        )
        # (v) the byte-tail from "Be CONSERVATIVE" onward is unchanged —
        # insertions go BEFORE the anchor; the JSON contract stays
        # byte-stable (the retry/parser seam depends on it).
        tail = prompt[prompt.index("Be CONSERVATIVE") :]
        assert len(tail) == 501  # diagnostic: tail length is pinned
        assert tail == _EXPECTED_PROMPT_BYTE_TAIL

        # (i) mocked judge not_complete → deny+nudge + ledger increment.
        spy_deny = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy_deny)
        node_deny, _mgr_deny, ledger_deny = _make_node(
            instance_id="u-guard-deny"
        )
        with _capture(caplog).at_level(logging.INFO):
            result_deny = _run(
                node_deny, self._delegation_only_state(), "u-guard-deny"
            )
        assert len(spy_deny.attempts) == 1
        # (iii) the payload carries NO "SOURCE U" header (U omitted) …
        assert "SOURCE U" not in spy_deny.attempts[0]
        assert "messages" in result_deny, "not_complete MUST deny+nudge"
        assert result_deny["attestation_route"] == "agent"
        ledger_deny.increment.assert_called_once()
        rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert rows, "expected the resolver_eval row"
        # … and the row logs the omission flag.
        assert "user_message_included=False" in rows[0]

        # (ii) mocked judge complete → rescue→allow, no nudge (base
        # equivalence on the rescue arm too — absence adds no branch).
        spy_allow = _JudgeSpy([_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy_allow)
        node_allow, _mgr_allow, ledger_allow = _make_node(
            instance_id="u-guard-allow"
        )
        with _capture(caplog).at_level(logging.INFO):
            result_allow = _run(
                node_allow, self._delegation_only_state(), "u-guard-allow"
            )
        assert len(spy_allow.attempts) == 1
        assert "SOURCE U" not in spy_allow.attempts[0]
        assert "messages" not in result_allow, "complete MUST NOT nudge"
        ledger_allow.increment.assert_not_called()
        rows_all = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert len(rows_all) == 2, "exactly one eval row per run"
        assert "user_message_included=False" in rows_all[1]
        assert "bundle_u_chars=0" in rows_all[1]


class TestFalseRescueChannelClosed:
    """A/C subordination: U appears fulfilled (the 4dfded83 genuine
    informal answer) + Source A advisory + Source C evidence — the
    contradictions are VISIBLE to the judge and an obedient judge's
    not_complete verdict is HONORED: no clean rescue-allow anywhere.

    Two code-honest bands (the §4.3 mapping ties the OUTCOME to the
    tree state, never to U — deny_nudge requires c_quiet, i.e. NO live
    descendant, so the dispatched live-descendant construct lands on
    the A-band's hint arm while the dispatched deny outcome pins on the
    quiet-tree band):
    * C live descendant → ¬c_quiet → A-band → not_complete + pending
      work → ALLOW log-only (2026-09-23 b2f4dae9: the Completion
      Check Note hint is RETIRED end-to-end; the
      ``resolver_outcome=allow_hint`` row label is the forensic
      record). Deny is unreachable with a live descendant BY DESIGN
      — the wakeup re-invokes the gate; the false rescue (plain
      allow, zero trace) cannot fire.
    * C quiet tree → deny band → deny+nudge + ledger increment.
    """

    @staticmethod
    def _u_fulfilled_mission():
        # U PRESENT (user asks X) → delegation → delivered A-advisory
        # note (promises future work) → final answer that genuinely +
        # informally answers X (the 4dfded83 shape).
        return {
            "messages": [
                HumanMessage(content=USER_QUESTION),
                _delegation_ai(),
                _child_report_check_note(),
                AIMessage(content=INFORMAL_ANSWER),
            ]
        }

    @staticmethod
    def _assert_contradictions_visible(spy, *, c_line: str):
        """The judge payload carried U + the A-advisory + the C status."""
        payload = spy.attempts[0]
        assert (
            "=== SOURCE U: the user's original request for this mission ==="
            in payload
        )
        assert USER_QUESTION in payload
        assert (
            "=== SOURCE A: child-terminal contradiction evidence (1 note(s)) ==="
            in payload
        )
        assert "completed while its final report promises" in payload
        assert "=== SOURCE C: tree status" in payload
        assert c_line in payload

    def test_u_fulfilled_a_advisory_c_live_judged_not_complete_honored(
        self, monkeypatch, caplog
    ):
        # The SUBORDINATION contract at prompt level (dual-autopsy B1
        # re-contract, 2026-09-20): the softened line keeps subordination
        # for GENUINE unresolved advisories (the false-rescue guard does
        # NOT regress) while carving out the two stale-advisory classes
        # (later-delivered supersession + operator-action pending).
        prompt = judge_mod.FUSED_JUDGE_SYSTEM_PROMPT
        assert (
            "SOURCE A advisories and SOURCE C live/pending descendants "
            "still indicate NOT_COMPLETE even when SOURCE U appears "
            "fulfilled - but an advisory is NOT evidence of undelivered "
            "work when its child later delivered a newer report that "
            "supersedes it, or when the pending item the advisory names "
            "is an OPERATOR action such as a rebuild+restart activation "
            "(the operator's step, not the child's undelivered work)."
            in prompt
        )
        # The false-rescue guard survives verbatim: genuine unresolved
        # advisories still subordinate SOURCE U.
        assert (
            "A GENUINE unresolved advisory - a child promising future "
            "work that never arrived, or a live/pending descendant - "
            "still indicates NOT_COMPLETE even when SOURCE U appears "
            "fulfilled." in prompt
        )

        # Scenario 1 — Source C shows a LIVE descendant (¬quiet →
        # A-band): not_complete + pending work → ALLOW log-only
        # (2026-09-23 b2f4dae9: the hint is RETIRED end-to-end; the
        # resolver row carries the (b)/(d)-with-pending label as the
        # durable record). Ledger untouched — the U-fulfilled shape
        # buys NO clean rescue.
        spy = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy)
        node, _manager, ledger = _make_node(
            instance_id="u-false-rescue-live", live_descendants=1
        )
        with _capture(caplog).at_level(logging.INFO):
            result = _run(
                node, self._u_fulfilled_mission(), "u-false-rescue-live"
            )
        assert len(spy.attempts) == 1
        self._assert_contradictions_visible(spy, c_line="live_descendants=1")
        # not_complete HONORED: the (b)/(d)-with-pending route resolves
        # to ALLOW on the resolver row (NOT a nudge, NOT a silent
        # plain allow — the resolver_outcome=allow_hint label is the
        # forensic surface).
        assert "messages" not in result, (
            "false-rescue LIVE A-band path MUST be log-only after "
            f"2026-09-23; got messages={result.get('messages')!r}"
        )
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()
        rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert rows, "expected the resolver_eval row"
        assert "resolver_outcome=allow_hint" in rows[0]
        assert "user_message_included=True" in rows[0]

        # Scenario 2 — C quiet (deny band): the SAME U-fulfilled shape +
        # A-advisory under not_complete → deny+nudge + ledger increment
        # (the dispatched deny outcome, on its code-honest band).
        spy_deny = _JudgeSpy([_not_complete_json()])
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", spy_deny)
        node_deny, _manager_deny, ledger_deny = _make_node(
            instance_id="u-false-rescue-deny"
        )
        with _capture(caplog).at_level(logging.INFO):
            result_deny = _run(
                node_deny, self._u_fulfilled_mission(), "u-false-rescue-deny"
            )
        assert len(spy_deny.attempts) == 1
        self._assert_contradictions_visible(
            spy_deny, c_line="live_descendants=0"
        )
        assert "messages" in result_deny, "not_complete MUST deny+nudge"
        nudge = result_deny["messages"][0]
        assert nudge.additional_kwargs.get("attestation_nudge") is True
        assert result_deny["attestation_route"] == "agent"
        ledger_deny.increment.assert_called_once()
        rows_all = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert len(rows_all) == 2, "exactly one eval row per run"
        assert "resolver_outcome=deny_nudge" in rows_all[1]
