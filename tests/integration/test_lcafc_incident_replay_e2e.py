"""7d4a3bd9 incident-replay acceptance (Job 2) — INDEPENDENTLY CONSTRUCTED
through the REAL gate node.

Merge-gate acceptance evidence for the 2026-09-26 ``correct-judge-override``
fix cycle (commit ``d5c50994`` on top of the LCA note-removal gate).

The 7d4a3bd9 incident (leader 7d4a3bd9, 2026-09-25 17:40:30Z→19:13:27Z,
mode=enforce deny_bound=3, Episode B): the tester child self-completed
19:10:55Z while its own report said "Awaiting the root-level core tests
report…" → ``pending_children=0`` → deny band (c_quiet+a_suspicion). The
fused judge verdict ``not_complete`` was CORRECT (the reason named the
mid-work state) and consumed deny slot 1; then TWO judge double-timeouts
(50s each, model=quick, TimeoutError at the 25s default) consumed slots
2+3; the next evaluation fired ``decision=terminal_after_bound`` at
19:13:26Z and wrote ``status=COMPLETED`` with
``completion_gate_escalated=true`` — INVISIBLE at the user surface.

The fix (commit ``d5c50994``, 2026-09-26, user rulings v1→v4 — final v3):
**EXHAUSTION COMPOSITION GATE.** Bound counting UNCHANGED ("boundedness
by simplicity" — every deny counts, timeouts included). At bound
exhaustion the node branches on the epoch's deny-event composition
(channel ``attestation_any_substantive_deny``; substantive := fused-judge
verdict ``not_complete``; timeout/error/unparsable/disabled = never-
spoke; reset with the ledger on attested allow / terminal):

* ≥1 substantive ⇒ judge SPOKE and was overridden ⇒
  ``terminal_after_bound`` stands as today (the loud
  ``completed (gate escalated — unverified)`` surface).
* ZERO substantive ⇒ NOT COMPLETE, NO terminal write from timeouts
  alone — the deny+nudge cycle CONTINUES (the committed counter may
  rise past the bound). Exits: ``attest_completion`` / finish the
  work / ask the user.

Plus the three sibling fixes shipped this cycle:

* **Fix 3 — directive nudge on no-progress repeat denies** (≥ 2nd
  consecutive deny ∧ zero new tool calls since the prior deny snapshot).
* **Fix 4 — unverified-completion surface.** ``completion_gate_escalated=True``
  ⇒ every read surface renders ``completed (gate escalated — unverified)``
  via the canonical ``COMPLETION_GATE_ESCALATED_DISPLAY`` constant.
* **Fix 5a — scanner fields on every evaluated path** (the
  ``final_word_count`` / ``length_trigger`` measurements are real on
  all paths; ``final_word_count`` flips from default 0 to the actual
  count even on non-denied paths).

THREE cells, all driven end-to-end through the REAL compiled graph
(``build_instance_graph`` + ``graph.ainvoke`` + scripted chat model +
judge patched at the module-attribute seam — the ONE judge call site
imports its callable at call time, so patching intercepts the REAL
invocation with the REAL bundle text in hand):

(a) **Ep-B arc** — 1 substantive deny (judge SPOKE, named a mid-work
    state) + 2 timeout denies (judge NEVER SPOKE — both attempts
    timeout; retry-once then timeout) with ZERO tool calls between
    denies ⇒ the 4th eval trips the bound with substantive=True ⇒
    loud ``terminal_after_bound`` is written
    (``completion_gate_escalated=True``); terminal label
    ``completed (gate escalated — unverified)`` (the canonical
    ``COMPLETION_GATE_ESCALATED_DISPLAY`` constant).

(b) **All-timeout variant** — 4+ deny episodes ALL judge-timeout,
    ZERO substantive. The exhaustion composition gate RE-REPLACES the
    terminal_after_bound decision back to DENIED (the counter rises
    past the bound, which is now correct under v3 ruling); the
    deny+nudge cycle CONTINUES past the bound — NO terminal write.
    Then leader exits via ``attest_completion`` (the meta_bypass /
    attested-allow path; the deterministic trust fallback) ⇒ assert
    the ``Decision.ALLOWED with attestation_present=True`` outcome
    (the reset-trigger-1 path that clears ALL the 7d4a3bd9 channels
    including ``attestation_any_substantive_deny``).

(c) **Directive nudge** — 2 consecutive deny episodes with ZERO new
    tool calls since the prior deny snapshot ⇒ the directive nudge
    body fires on the 2nd deny ONLY (byte-pin the verbatim
    ``ATTESTATION_DIRECTIVE_NUDGE_TEXT`` constant from
    ``daemon/graph.py``); the 1st deny gets the STANDARD nudge.

Process discipline:

  * Drift-pin first (per dispatch constraint); pack wrapper re-pins.
  * Resolver cache reset between cells via the established pattern
    (autouse ``_reset_resolvers`` fixture; both
    ``reset_attestation_resolver_for_tests`` AND
    ``reset_llm_judge_resolver_for_tests``).
  * Judge patched at the module-attribute seam — the ONE judge call
    site imports its callable at call time, so the patch intercepts
    the REAL invocation.
  * The ``build_instance_llms`` factory is replaced so the graph
    NEVER POSTs to a real LLM endpoint.
  * Mode pinned to ``enforce`` (D2 — only enforce writes the ledger;
    dry/off modes do not).
  * ``WATCHOVER_ENABLED=false`` (no second-attention side effect).
  * Judge wall-clock cap OVERRIDDEN via
    ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=5.0`` (the
    documented min-clamp floor) so the timeout cells fit the
    5-minute pack hard cap. Per-attempt cost = 5.0s × 2 retries =
    10s per timeout deny episode.

NO production-code changes. Defects found → evidence + report; do NOT
fix production code.

INDEPENDENCE CLAIM:
  This file is authored from scratch against ``d5c50994`` independently
  of ``tests/unit/test_lca_false_complete_fixes.py``. Different harness
  (real-graph via ``ainvoke`` + scripted chat model + judge patched at
  module seam, NOT the dev's direct ``create_attestation_gate_node`` +
  ``monkeypatch`` unit path), different scenarios, different Source-A
  evidence shape (independent cell content).
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from daemon.constants import COMPLETION_GATE_ESCALATED_DISPLAY
from daemon.graph import (
    ATTESTATION_ANY_SUBSTANTIVE_KEY,
    ATTESTATION_DIRECTIVE_NUDGE_TEXT,
    ATTESTATION_NUDGE_TEXT,
)
from daemon.services.attestation_report_judge import FusedJudgeResult
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from tests.support.scripted_chat_model import ScriptedChatModel

pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────────────
# Constants — three independent cells, each driven through the REAL gate node
# ─────────────────────────────────────────────────────────────────────────────

# Must match the instance id the ``attestation_repository`` fixture
# creates in ``tests/support/conftest.py`` (the fixture hard-codes
# ``instance_id="attestation-leader-e2e"``). If the two diverge the
# gate's Phase-3 ``increment_attestation_denied_count`` finds no row
# and returns the missing-instance sentinel ``-1`` — the gate's C3
# fail-open guard (``safe_increment`` returning ``< 0``) then degrades
# the deny to allow END, and the graph ends after a single eval.
# That fail-open path is the bug class this constant pins against.
LEADER_INSTANCE_ID = "attestation-leader-e2e"

# Override the judge wall-clock cap to the documented 5.0s min-clamp floor so
# timeout cells fit the 5-min pack hard cap. With 5.0s × 2 retries
# (retry-once-on-timeout, incident bc145c7e R1, 2026-09-19), each timeout
# deny episode costs at most 10s wall-clock. Cell (b) runs ~4 timeout
# episodes ⇒ ~40s of judge time; cell (a) runs 2 ⇒ ~20s; cell (c) runs 0.
# The pack's outer `timeout 300` + inner per-pytest cap stays the hard
# guarantee; this env override is the only knob that lets the timeout
# cells fit.
JUDGE_TIMEOUT_ENV = "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S"
JUDGE_TIMEOUT_OVERRIDE_VALUE = "5.0"  # MIN_JUDGE_TIMEOUT_S floor

# D5 default deny bound (matches the lcancheck harness — the production
# default the 7d4a3bd9 incident ran against).
DENY_BOUND = 3

# A child instance the leader delegates to (the canonical "I dispatched
# the worker" turn-1 shape — flips attestation_required=True).
CHILD_INSTANCE_ID = "7d4a3bd9-child-001"

# The user's mission ask — single sentence, no marker; the FIRST and ONLY
# real user message the gate scans back to when locating the
# ``delegation_since_last_user`` anchor.
USER_MISSION = (
    "Run the calibration sweep and report back when the worker finishes."
)

# The canonical mid-work ACK — under SHORT_REPORT_WORD_THRESHOLD (150) so
# the gate evaluates the resolve tree (NOT the attested-allow short-
# circuit). The same shape across all three cells (the independent
# "leader held without action" prose the 7d4a3bd9 leader used).
HOLDING_PROSE = (
    "Holding exactly there. The tester is still working; I will "
    "report again when it lands. Ending turn now."
)

# A long, standalone text report (≥ SHORT_REPORT_WORD_THRESHOLD words).
# Used by cell (b) to satisfy the attested-allow path
# (``Decision.ALLOWED with attestation_present=True``) AFTER the
# leader has issued ``attest_completion`` and the bound cycle has
# continued past the deny bound. Built deterministic and ≥150 words
# so the gate's ``classify_final_ai_shape`` returns
# ``final_ai_is_text_report=True`` (the 2026-09-19 attest-first
# contract). Single-line source so the collection-time sanity
# assertion's ``len(...split())`` matches the runtime concat (no
# multi-line-split ambiguity).
_TEXT_REPORT_PROSE_BODY = (
    "Calibration sweep complete. The tester child delivered its core tests report at "
    "19:13:27Z; the changelog records one entry under review by the docs team, but "
    "that is the only outstanding item on my side. The mission is finished. The "
    "worker reconciled its ledger, the gates passed, and the upstream probe verified "
    "the fixtures. I have confirmed that no background tasks remain in the dependency-"
    "watchers table; the message-queue is drained; the job queue carries no unsettled "
    "rows; the instance tree is clean. Summary: every delegated child terminated with "
    "a clean verdict, the leader prompt has been honored, and the final report below "
    "documents the mission's outcomes. The deliverables are: the calibration sweep "
    "results, the tester core-tests report, the upstream probe verification, the "
    "dependency-watchers clean state, the message-queue drain confirmation, the job-"
    "queue settlement confirmation, and the instance-tree cleanliness audit. The "
    "leader prompt section 'Final Report Format' is satisfied; the mission is closed. "
    "End of mission."
)
TEXT_REPORT_PROSE = _TEXT_REPORT_PROSE_BODY

# Sanity: TEXT_REPORT_PROSE MUST cross the 150-word threshold so the
# attested-allow path engages (cell (b) ALLOW exit).
assert len(TEXT_REPORT_PROSE.split()) >= 150, (
    "TEXT_REPORT_PROSE MUST be ≥ SHORT_REPORT_WORD_THRESHOLD (150) "
    "so classify_final_ai_shape returns final_ai_is_text_report=True "
    "and the attested-allow path engages (cell b ALLOW exit); "
    f"got {len(TEXT_REPORT_PROSE.split())} words"
)
# Sanity: HOLDING_PROSE MUST stay under the 150-word threshold so the
# gate evaluates the resolve tree (NOT the attested-allow short-
# circuit) on the mid-work ACK turns.
assert len(HOLDING_PROSE.split()) < 150, (
    "HOLDING_PROSE MUST be < SHORT_REPORT_WORD_THRESHOLD (150) so the "
    "gate evaluates the resolve tree (NOT the attested-allow short-"
    f"circuit); got {len(HOLDING_PROSE.split())} words"
)


# ─────────────────────────────────────────────────────────────────────────────
# Harness mechanics (real-graph integration test, established lcancheck pattern)
# ─────────────────────────────────────────────────────────────────────────────


@tool
def attest_completion() -> dict:
    """Real leader attestation tool (the graph's bound tool).

    The dummy body — cell (a) NEVER calls it (the loud unverified path
    is the system's `terminal_after_bound` machinery, NOT a leader
    attest). Cell (b) drives the leader to issue ``attest_completion``
    AFTER the deny cycle has continued past the bound; the tool must
    be bound so the LLM's tool_call returns a tool message (not a
    graph crash) and so the graph can route back to the agent for the
    final text-report turn.
    """
    return {"attested": True, "timestamp": "2026-09-26T00:00:00+00:00"}


@tool
def send_message(target: str, content: str) -> dict:
    """Dummy send_message target — the lcancheck harness shape.

    Cell (a)/(b)/(c) all start with LLM1 delegating via ``send_message``
    to flip ``attestation_required=True``. The tool is bound but does
    NOT actually deliver (we don't need real delivery for these cells
    — the gate's resolver reads ``delegation_since_last_user`` from the
    AIMessage tool_call, not from a delivered message). The dummy body
    keeps the ToolNode happy and the graph routes back to agent.
    """
    return {"dispatched": True, "target": target, "echoed": content}


@pytest.fixture(autouse=True)
def _override_judge_timeout(monkeypatch):
    """Pin the judge wall-clock cap to the 5.0s min-clamp floor.

    The default (180.0s since the 2026-09-26 7d4a3bd9 amendment) is too
    long for the timeout cells to fit the 5-min pack hard cap. Each
    timeout deny episode costs at most 5.0s × 2 retries (retry-once on
    TimeoutError, incident bc145c7e R1, 2026-09-19) = 10s wall-clock.
    Cell (b) runs ~4 timeout episodes ⇒ ~40s of judge time; the env
    override keeps every timeout cell bounded under the pack cap.

    Documented in the pack script header — see
    ``test/packs/lcafc_incident_replay_e2e_test.sh``.
    """
    monkeypatch.setenv(JUDGE_TIMEOUT_ENV, JUDGE_TIMEOUT_OVERRIDE_VALUE)


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    """Pin enforce mode + reset resolver caches between cells.

    Mirrors the established pattern in
    ``tests/integration/test_lcancheck_b2f4dae9_regression.py::
    _reset_resolvers``: mode = enforce (D2 — only enforce writes the
    ledger); WATCHOVER off (no second-attention side effect); resolver
    caches reset before AND after each test so a sibling test that
    flips ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`` does not
    leak into this file's evaluation.
    """
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


def _settings():
    """Match the lcancheck harness settings — mode=enforce, bound=3."""
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=DENY_BOUND)


def _build_graph(graph_module, model, manager, checkpointer):
    """Build the REAL compiled graph with the scripted model installed.

    Mirrors ``test_lcancheck_b2f4dae9_regression.py::_build_graph``
    byte-for-byte on the graph-construction seam — ``build_instance_llms``
    is replaced so the graph NEVER POSTs to a real LLM endpoint, and
    ``resolve_gate_settings`` is patched to the enforce settings so
    the resolver cache reset + env pinning are the only sources of
    truth for mode/window/bound.
    """
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion, send_message],
            checkpointer=checkpointer,
            llm_config={
                "model": "scripted-lcafc-7d4a3bd9",
                "api_key": "test",
            },
            system_prompt="scripted 7d4a3bd9 incident-replay leader",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": LEADER_INSTANCE_ID}},
            attestation_enabled=True,
        )


def _delegate_ai() -> AIMessage:
    """LLM 1 — delegate via ``send_message`` (the canonical attestation
    ON-switch; ``delegation_scan.delegation_since_last_user`` flips to
    True on this call so the gate evaluates at end_candidate).
    """
    return AIMessage(
        content="Dispatching the worker.",
        tool_calls=[
            {
                "name": "send_message",
                "args": {
                    "target": CHILD_INSTANCE_ID,
                    "content": "Run the calibration sweep",
                },
                "id": "lcafc-dispatch-1",
            }
        ],
    )


def _holding_ack() -> AIMessage:
    """The canonical 7d4a3bd9 mid-work ACK — under
    SHORT_REPORT_WORD_THRESHOLD (150) so the gate evaluates the
    resolve tree (NOT the attested-allow short-circuit). Same prose
    across all three cells (the independent "leader held without
    action" shape the 7d4a3bd9 leader used).
    """
    return AIMessage(content=HOLDING_PROSE)


def _attest_ai() -> AIMessage:
    """Clean-call ``attest_completion`` tool_call (no content).

    Used by cell (b) — after the deny cycle has continued past the
    bound, the leader issues attest_completion with empty content.
    The graph routes through ToolNode → tool message → routes back to
    agent. On the next eval the gate sees attested=True (the prior
    AIMessage carries the tool_call); if the next AIMessage is a
    standalone text report (≥150 words), the gate ALLOWs via the
    attested-allow path (the deterministic trust fallback the D-entry
    pins).
    """
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "attest_completion",
                "args": {},
                "id": "lcafc-attest-1",
            }
        ],
    )


def _text_report_ai() -> AIMessage:
    """Standalone text report — no tool calls, ≥150 words (the
    SHORT_REPORT_WORD_THRESHOLD the attested-allow path requires).
    """
    return AIMessage(content=TEXT_REPORT_PROSE)


class _SequencedJudge:
    """Graph-seam judge probe — sequenced verdicts across invocations.

    Mirrors ``_RecordingJudge`` from
    ``tests/integration/test_lcancheck_b2f4dae9_regression.py`` — the
    ONE judge call site imports its callable at call time, so patching
    ``daemon.services.attestation_report_judge.judge_fused_bundle_async``
    intercepts the REAL invocation with the REAL bundle text in hand.

    Distinct from the lcancheck harness: this probe takes a SEQUENCE
    of verdicts (one per invocation) so a single test can drive
    multiple evaluations through different judge outcomes (substantive
    on call 1, timeout on call 2, timeout on call 3, etc.). The probe
    records EVERY bundle it sees so the test can assert "judge was
    called N times" + "the Nth call carried the expected verdict".

    Supported verdicts (str):
      * ``"not_complete"`` — substantive. The judge SPOKE. Stamps the
        ``attestation_any_substantive_deny`` channel.
      * ``"timeout"`` — never-spoke (TimeoutError after retry-once).
        No channel stamp. The exhaustion composition gate will withhold
        the terminal if no substantive verdict preceded.
      * ``"complete"`` — judge yes. Plain ALLOW (rescue path).
      * ``"error"`` — never-spoke (HTTP/API fault, no retry).
      * ``"unparsable"`` — never-spoke (model responded but verdict
        JSON did not parse).
    """

    def __init__(self, verdicts: list[str]):
        if not verdicts:
            raise ValueError(
                "_SequencedJudge requires ≥1 verdict; an empty script "
                "would silently skip every gate evaluation"
            )
        self.verdicts = list(verdicts)
        self.bundles: list[str] = []
        self.calls: int = 0

    async def __call__(self, bundle_text: str, *, config, timeout_s=None):
        if self.calls >= len(self.verdicts):
            raise IndexError(
                f"_SequencedJudge exhausted: the gate requested "
                f"{self.calls + 1} judge calls but the script only "
                f"has {len(self.verdicts)} verdicts; "
                f"add the next verdict to the test script"
            )
        verdict = self.verdicts[self.calls]
        self.bundles.append(bundle_text)
        self.calls += 1
        return FusedJudgeResult(
            invoked=True,
            is_complete=(verdict == "complete"),
            verdict=verdict,
            evidence_cited=(
                ("calibration sweep incomplete",)
                if verdict == "not_complete"
                else ()
            ),
            advisory_note_text=(
                "the tester child has not yet reported"
                if verdict == "not_complete"
                else ""
            ),
            rationale=(
                "mid-work — child has not reported"
                if verdict == "not_complete"
                else f"stub adjudication: verdict={verdict}"
            ),
            model="stub-judge-7d4a3bd9",
            latency_ms=(
                int((timeout_s or 5.0) * 1000) if verdict == "timeout" else 1
            ),
            error_class="TimeoutError" if verdict == "timeout" else None,
            attempt=2 if verdict == "timeout" else 1,
            first_unparsable_excerpt=None,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Per-cell log-row + nudge helpers
# ─────────────────────────────────────────────────────────────────────────────


def _gate_eval_rows(caplog) -> list[str]:
    """ALL canonical ``[AttestationGate]`` decision rows (the canonical
    log surface operators grep for to reconstruct the arc)."""
    return [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_gate decision=" in r.getMessage()
    ]


def _resolver_eval_rows(caplog) -> list[str]:
    """ALL ``event=leader_completion_resolver_eval`` rows (Stage-3 unified
    path; carries the band + judge_invoked + judge_verdict +
    resolver_outcome fields)."""
    return [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_resolver_eval " in r.getMessage()
    ]


def _terminal_after_bound_rows(caplog) -> list[str]:
    """Operator-event rows for ``terminal_after_bound`` — the FIX-1
    invariant: ZERO such rows in the pre-7d4a3bd9 fleet log; ≥1 on
    every fixed-behavior path that trips the bound with substantive
    present."""
    return [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_gate_terminal_after_bound" in r.getMessage()
    ]


def _bound_exhausted_never_spoke_rows(caplog) -> list[str]:
    """The 7d4a3bd9 Fix-A exhaustion-composition rows — the
    ``decision=terminal_withheld_deny_continues`` operator event that
    fires when the bound trips WITHOUT any substantive verdict."""
    return [
        r.getMessage()
        for r in caplog.records
        if "event=leader_completion_gate_bound_exhausted_never_spoke"
        in r.getMessage()
    ]


def _nudge_messages(messages) -> list[HumanMessage]:
    """All HumanMessages carrying the ``attestation_nudge`` marker
    (the in-graph deny nudge the leader sees). Stable id contract:
    consecutive denies SUPERSEDE the prior nudge block in place via
    LangGraph's ``add_messages`` reducer upsert, so the final list
    carries AT MOST ONE nudge per id-class — but the body of the
    surviving message reflects the LAST nudge injection."""
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and (m.additional_kwargs or {}).get("attestation_nudge")
    ]


def _attestation_route_hints(state) -> list[str | None]:
    """All ``attestation_route`` channel values across the run — used
    to assert that no individual eval flipped to the END route when
    we expected a deny+nudge cycle continuation."""
    route = state.get("attestation_route") if isinstance(state, dict) else None
    return [route]


# ─────────────────────────────────────────────────────────────────────────────
# Cell (a) — Ep-B arc: 1 substantive deny + 2 timeout denies ⇒ loud terminal
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_epb_arc_loud_terminal_after_bound_with_substantive(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """Cell (a) — Ep-B arc (7d4a3bd9, judge was RIGHT).

    Scripted arc (5 LLM turns):
      LLM1 — delegate via send_message (flip attestation_required=True)
      LLM2 — short mid-work ACK → eval1: judge=not_complete (substantive)
              ⇒ DENIED, counter 0→1, standard nudge injected
      LLM3 — short mid-work ACK → eval2: judge=timeout (never spoke)
              ⇒ DENIED, counter 1→2, directive nudge (≥2nd deny ∧ zero
                 progress)
      LLM4 — short mid-work ACK → eval3: judge=timeout (never spoke)
              ⇒ DENIED, counter 2→3, directive nudge
      LLM5 — short mid-work ACK → eval4: bound trips
              (`denied_count(3) + 1 > bound(3)` = True), substantive=True
              ⇒ TERMINAL_AFTER_BOUND, loud surface
              (`completed (gate escalated — unverified)`) + counter
              reset to 0 + completion_gate_escalated=True.

    Code-derived expected semantics:
      * decide() at step (4) trips the bound at counter=3 (the
        ``deny_bound_exceeded(3, 3)`` predicate — `3 + 1 > 3` = True).
      * Graph node sees ``decision.decision is
        Decision.TERMINAL_AFTER_BOUND`` AND
        ``any_substantive_deny=True`` (stamped on eval1) — the
        exhaustion composition gate's NOT-WITHHELD branch ⇒ terminal
        machinery runs.
      * Phase-3 ledger: ``set_escalated_and_reset`` writes
        ``completion_gate_escalated=true`` and resets the counter.
      * The terminal label is the canonical
        ``COMPLETION_GATE_ESCALATED_DISPLAY``
        (``"completed (gate escalated — unverified)"``) per Fix 4.
      * The operator event ``event=leader_completion_gate_terminal_
        after_bound`` is emitted.
      * ZERO ``event=leader_completion_gate_bound_exhausted_never_spoke``
        rows (substantive=True means the never-spoke branch NEVER
        fires — the user's ruling v3 takes the loud-surface path).

    Pre-fix (pre-2026-09-26): the loud surface was MISSING — the
    terminal wrote ``status=COMPLETED`` plain; the user saw a green
    completion even though the leader was in the middle of the work.
    Post-fix: the loud surface renders
    ``completed (gate escalated — unverified)`` at every read point
    (work_notifier, jobs_crud, missions) and ties the escalated
    terminal to the episode's REAL job (already-finalized witness).

    Independence from the dev witness:
      * Real graph via ainvoke (NOT the dev's direct
        ``create_attestation_gate_node`` + monkeypatch unit path).
      * Different Source-A evidence shape (independent cell content).
      * Independent scenario wording (independent mid-work ACK prose
        + an independent child id + an independent mission ask).
    """
    repo, _instance = attestation_repository

    # The leader is alone in the tree (no descendants) so R2 inputs are
    # all zero — the deny band fires (c_quiet=True, band=BAND_DENY).
    # No child instances created in the repo (the manager facade reads
    # the real instance tree; live_descendants=0 by construction).
    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=0,
        queued_wakeups=0,
        live_descendants=0,  # empty tree → real facade also returns 0
    )

    # Judge sequence: substantive, timeout, timeout, (no call on
    # eval4 — budget parity on TERMINAL_AFTER_BOUND).
    judge = _SequencedJudge(verdicts=["not_complete", "timeout", "timeout"])
    model = ScriptedChatModel(
        responses=[
            _delegate_ai(),  # LLM1 — delegate (attestation_required=True)
            _holding_ack(),  # LLM2 — eval1 (substantive)
            _holding_ack(),  # LLM3 — eval2 (timeout)
            _holding_ack(),  # LLM4 — eval3 (timeout)
            _holding_ack(),  # LLM5 — eval4 (bound trips, no judge)
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    caplog.set_level(logging.INFO)
    with patch(
        "daemon.services.attestation_report_judge.judge_fused_bundle_async",
        new=judge,
    ):
        state = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content=USER_MISSION),
                ]
            },
            config={
                "configurable": {"thread_id": LEADER_INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model.assert_all_responses_consumed()

    # ── Judge budget: EXACTLY 3 calls (eval1 substantive + eval2/3
    # timeouts; eval4 bound-trips → budget parity → NO judge call).
    assert judge.calls == 3, (
        f"cell (a): judge MUST be invoked exactly 3 times "
        f"(eval1 substantive + eval2/3 timeouts; eval4 trips the "
        f"bound → budget parity → NO judge call); got {judge.calls} "
        f"calls"
    )

    # ── The eval1 bundle MUST carry the mid-work evidence the judge
    # adjudicated. The fused bundle is what the judge SAW (Source U
    # user mission + Source A child reports + Source B leader signals +
    # Source C R2 inputs); the "judge was RIGHT" invariant the 7d4a3bd9
    # incident pins is that this evidence is a mid-work state — the
    # user's mission says "report back when the worker finishes" and the
    # leader's ACK is "Holding exactly there... tester is still
    # working... ending turn now" — clearly mid-work. The judge
    # correctly returned ``not_complete`` and consumed deny slot 1.
    bundle = judge.bundles[0]
    assert (
        "Run the calibration sweep and report back when the worker finishes"
        in bundle
    ), (
        f"cell (a): eval1 bundle MUST carry the user's mission (the "
        f"Source-U evidence the judge adjudicated); got bundle "
        f"excerpt: {bundle[:300]!r}"
    )
    assert "Holding exactly there" in bundle, (
        f"cell (a): eval1 bundle MUST carry the leader's mid-work "
        f"ACK (the Source-B evidence the judge adjudicated — the "
        f"7d4a3bd9 'judge was RIGHT' invariant); got bundle "
        f"excerpt: {bundle[:300]!r}"
    )

    # ── Terminal machinery MUST have fired (eval4 trips the bound
    # with substantive=True → loud surface stands).
    terminal_rows = _terminal_after_bound_rows(caplog)
    assert len(terminal_rows) == 1, (
        f"cell (a): EXACTLY ONE terminal_after_bound operator event "
        f"MUST fire (the loud-surface path the 7d4a3bd9 fix delivers); "
        f"got {len(terminal_rows)} rows: {terminal_rows}"
    )
    assert "completion_gate_escalated=true" in terminal_rows[0], (
        f"cell (a): the terminal_after_bound row MUST carry "
        f"completion_gate_escalated=true (the canonical surface the "
        f"Fix 4 threads to the record); got: {terminal_rows[0]}"
    )

    # ── ZERO never-spoke rows (substantive=True → exhaustion gate
    # does NOT withhold the terminal).
    never_spoke_rows = _bound_exhausted_never_spoke_rows(caplog)
    assert never_spoke_rows == [], (
        f"cell (a): ZERO never-spoke operator events MUST fire — the "
        f"substantive=True stamp on eval1 closes the exhaustion-"
        f"composition withholding branch; got "
        f"{len(never_spoke_rows)} rows: {never_spoke_rows}"
    )

    # ── The completion display MUST be the canonical loud surface.
    assert COMPLETION_GATE_ESCALATED_DISPLAY == (
        "completed (gate escalated — unverified)"
    ), (
        f"cell (a): COMPLETION_GATE_ESCALATED_DISPLAY drifted (the "
        f"canonical constant the unverified surface renders); "
        f"got {COMPLETION_GATE_ESCALATED_DISPLAY!r}"
    )

    # ── Counter reset on the terminal (Phase-3 ledger
    # set_escalated_and_reset path). The ledger repository's
    # get_attestation_denied_count returns 0 after the terminal.
    counter_after = repo.get_attestation_denied_count(LEADER_INSTANCE_ID)
    assert counter_after == 0, (
        f"cell (a): counter MUST be reset to 0 after the terminal "
        f"(the Phase-3 ledger's set_escalated_and_reset path — "
        f"reset trigger 2 mirrors trigger 1); got counter="
        f"{counter_after}"
    )

    # ── Graph ended (no route hint on the terminal return).
    route = (
        state.get("attestation_route")
        if isinstance(state, dict)
        else getattr(state, "attestation_route", None)
    )
    assert route is None, (
        f"cell (a): terminal_after_bound MUST END the graph "
        f"(attestation_route=None); got route={route!r}"
    )

    # ── Channel reset semantics — the substantive channel is cleared
    # on the terminal path (the fix-A reviewer-flagged A1 stale-flag
    # leak guard runs on every return).
    channel = (
        state.get(ATTESTATION_ANY_SUBSTANTIVE_KEY)
        if isinstance(state, dict)
        else getattr(state, ATTESTATION_ANY_SUBSTANTIVE_KEY, None)
    )
    assert channel is False, (
        f"cell (a): the substantive channel MUST be cleared on the "
        f"terminal path (the next episode starts clean; the stale-"
        f"flag leak guard); got {channel!r}"
    )

    # ── The completion_gate_escalated flag is stamped on the
    # instance row (Phase-3 ledger write; surfaced as the canonical
    # 'completed (gate escalated — unverified)' label at every read).
    instance = repo.get(LEADER_INSTANCE_ID)
    assert getattr(instance, "completion_gate_escalated", False) is True, (
        f"cell (a): the instance row MUST carry "
        f"completion_gate_escalated=True (Phase-3 ledger write — "
        f"the unverified surface reads this flag and renders the "
        f"canonical 'completed (gate escalated — unverified)' label); "
        f"got completion_gate_escalated="
        f"{getattr(instance, 'completion_gate_escalated', None)!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cell (b) — All-timeout: deny+nudge cycle CONTINUES past bound, attest exit
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_b_all_timeout_deny_cycle_continues_then_attest_exit(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """Cell (b) — All-timeout variant (7d4a3bd9 never-spoke arc).

    Scripted arc (8 LLM turns):
      LLM1 — delegate via send_message (attestation_required=True)
      LLM2 — short mid-work ACK → eval1: judge=timeout → DENIED,
              counter 0→1, standard nudge
      LLM3 — short mid-work ACK → eval2: judge=timeout → DENIED,
              counter 1→2, directive nudge
      LLM4 — short mid-work ACK → eval3: judge=timeout → DENIED,
              counter 2→3, directive nudge
      LLM5 — short mid-work ACK → eval4: BOUND TRIPS
              (counter(3)+1>bound(3) = True), judge NOT invoked
              (budget parity), but ZERO substantive → exhaustion
              composition gate RE-REPLACES back to DENIED, counter
              3→4, directive nudge (the cycle CONTINUES — no
              terminal_after_bound write)
      LLM6 — short mid-work ACK → eval5: bound STILL trips (counter=4),
              judge NOT invoked, ZERO substantive → DENIED, counter
              4→5, directive nudge
      LLM7 — AIMessage with ``attest_completion`` tool_call (clean
              call, empty content) → tool execution → tool message →
              routes back to agent
      LLM8 — standalone text report (≥150 words) → eval6: gate sees
              attested=True + final_ai_is_text_report=True →
              Decision.ALLOWED with attestation_present=True
              (reset trigger 1; the meta_bypass / attested-allow
              fallback). Channels reset. Graph ends.

    Code-derived expected semantics:
      * decide() at step (4) returns TERMINAL_AFTER_BOUND at counter≥3.
      * Graph node sees TERMINAL_AFTER_BOUND AND substantive=False
        (no not_complete verdicts stamped) → exhaustion composition
        gate RE-REPLACES back to DENIED with counter +1
        (``next_denied_count = decision.denied_count + 1``).
      * Phase-3 ledger writes ``increment`` (NOT
        ``set_escalated_and_reset``) on every never-spoke exhausted
        eval — the counter rises past the bound (now correct under
        v3 ruling).
      * Each eval re-injects a checkpoint-durable nudge (the
        standard nudge on eval1, directive nudges on evals2..N —
        directive-vs-standard selection runs on every DENIED).
      * The directive body fires on the 2nd-and-onward denies (every
        eval ≥2 while ``_is_repeat_deny=True`` and ``_zero_progress=
        True`` since the prior deny snapshot).
      * ZERO ``event=leader_completion_gate_terminal_after_bound``
        operator events (the never-spoke branch never produces one).
      * ≥1 ``event=leader_completion_gate_bound_exhausted_never_spoke``
        operator event (the never-spoke branch logs its decision).
      * The final eval (LLM8's text report with prior attest tool_call)
        → Decision.ALLOWED with attestation_present=True → graph ends.
        The substantive channel is reset to False (reset trigger 1).

    Independence from the dev witness: same harness pattern (real-
    graph ainvoke + scripted chat model), distinct scenario (4+
    timeout denies + attest exit, NOT the 1-substantive+2-timeout
    arc cell (a) drives).
    """
    repo, _instance = attestation_repository

    # The leader is alone in the tree — R2 inputs all zero → deny band
    # fires (c_quiet=True).
    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=0,
        queued_wakeups=0,
        live_descendants=0,
    )

    # Judge sequence: all 3 substantive evals are timeout (eval4/5/6
    # trip the bound → NO judge call; eval7 has attested=True → NO
    # judge call either). So judge is called exactly 3 times (all
    # timeout).
    judge = _SequencedJudge(verdicts=["timeout", "timeout", "timeout"])
    model = ScriptedChatModel(
        responses=[
            _delegate_ai(),    # LLM1 — delegate
            _holding_ack(),    # LLM2 — eval1 (timeout, counter 0→1,
                               #          STANDARD nudge on the 1st deny)
            _holding_ack(),    # LLM3 — eval2 (timeout, counter 1→2,
                               #          DIRECTIVE nudge on the 2nd deny)
            _holding_ack(),    # LLM4 — eval3 (timeout, counter 2→3,
                               #          directive nudge)
            _holding_ack(),    # LLM5 — eval4 (BOUND trips 3+1>3, NO
                               #          judge, exhaustion composition
                               #          gate re-replaces to DENIED,
                               #          counter 3→4, directive nudge)
            _holding_ack(),    # LLM6 — eval5 (bound still trips 4+1>3,
                               #          NO judge, counter 4→5)
            _holding_ack(),    # LLM7 — eval6 (bound still trips 5+1>3,
                               #          NO judge, counter 5→6 — counter
                               #          rose PAST the bound, which is
                               #          the 7d4a3bd9 v3 ruling)
            _attest_ai(),      # LLM8 — attest_completion tool_call
                               #          (clean call, empty content)
            _text_report_ai(), # LLM9 — text report ≥150 words → end
                               #          _candidate → gate eval
                               #          attested=True + text_report=
                               #          True → Decision.ALLOWED (the
                               #          meta_bypass / attested-allow
                               #          fallback the D-entry pins).
                               #          Graph ends.
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    caplog.set_level(logging.INFO)
    with patch(
        "daemon.services.attestation_report_judge.judge_fused_bundle_async",
        new=judge,
    ):
        state = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content=USER_MISSION),
                ]
            },
            config={
                "configurable": {"thread_id": LEADER_INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model.assert_all_responses_consumed()

    # ── Judge budget: EXACTLY 3 calls (eval1/2/3 timeouts; eval4/5
    # bound-tripping → budget parity → NO judge call; eval6 has
    # attested=True → ALLOW before judge plan fires).
    assert judge.calls == 3, (
        f"cell (b): judge MUST be invoked exactly 3 times "
        f"(eval1/2/3 timeouts; eval4/5 trip the bound → NO judge "
        f"call; eval6 has attested=True → ALLOW before judge plan "
        f"fires); got {judge.calls} calls"
    )

    # ── ZERO terminal_after_bound operator events (the never-spoke
    # arc never produces one — the user's v3 ruling takes the
    # continuation path, NOT the loud-surface path).
    terminal_rows = _terminal_after_bound_rows(caplog)
    assert terminal_rows == [], (
        f"cell (b): ZERO terminal_after_bound operator events MUST "
        f"fire (the never-spoke branch withholds the terminal; the "
        f"deny+nudge cycle CONTINUES — the 7d4a3bd9 v3 ruling); "
        f"got {len(terminal_rows)} rows: {terminal_rows}"
    )

    # ── ≥1 never-spoke operator event (the exhaustion composition
    # gate logs its decision on every bound-tripping eval with
    # substantive=False).
    never_spoke_rows = _bound_exhausted_never_spoke_rows(caplog)
    assert len(never_spoke_rows) >= 2, (
        f"cell (b): ≥2 never-spoke operator events MUST fire "
        f"(eval4 + eval5 both trip the bound with substantive=False "
        f"— each logs the never-spoke branch's decision); "
        f"got {len(never_spoke_rows)} rows: {never_spoke_rows}"
    )
    # Each row MUST carry the substantive=False witness.
    for row in never_spoke_rows:
        assert "any_substantive_deny=false" in row, (
            f"cell (b): every never-spoke row MUST carry "
            f"any_substantive_deny=false (the witness the branch "
            f"checks); got: {row}"
        )
        assert "decision=terminal_withheld_deny_continues" in row, (
            f"cell (b): every never-spoke row MUST carry "
            f"decision=terminal_withheld_deny_continues (the "
            f"canonical decision label); got: {row}"
        )

    # ── Counter rose PAST the bound (the 7d4a3bd9 v3 ruling: the
    # counter may rise past the bound on the never-spoke path —
    # "every deny counts" is UNCHANGED). The counter is captured
    # BEFORE the attest-allow reset (the attested-allow path resets
    # to 0 per reset trigger 1, which would otherwise mask the
    # never-spoke counter's peak). Scan BOTH the gate decision log
    # rows AND the never-spoke operator events for the peak counter
    # value:
    #   * Gate decision rows carry the counter-at-eval-time (the
    #     value the bound check consulted); the row format is
    #     ``decision=<v> ... denied_count=N next_denied_count=M ...``.
    #   * Never-spoke operator events carry the POST-re-replace
    #     counter (after the exhaustion composition gate replaces
    #     TERMINAL_AFTER_BOUND back to DENIED + counter+1).
    # The peak across both surfaces is the highest counter value the
    # epoch ever reached.
    def _parse_next_denied_count(row: str) -> int | None:
        idx = row.find("next_denied_count=")
        if idx == -1:
            return None
        tail = row[idx + len("next_denied_count="):]
        try:
            return int(tail.split()[0])
        except (ValueError, IndexError):
            return None

    peak_counter = 0
    for row in _gate_eval_rows(caplog):
        value = _parse_next_denied_count(row)
        if value is not None:
            peak_counter = max(peak_counter, value)
    for row in _bound_exhausted_never_spoke_rows(caplog):
        value = _parse_next_denied_count(row)
        if value is not None:
            peak_counter = max(peak_counter, value)
    assert peak_counter > DENY_BOUND, (
        f"cell (b): peak deny counter MUST exceed the bound on the "
        f"never-spoke path (the 7d4a3bd9 v3 ruling — 'every deny "
        f"counts'; the counter may rise past the bound); got "
        f"peak_counter={peak_counter}, deny_bound={DENY_BOUND}; "
        f"the counter is captured from the gate decision log rows "
        f"+ the never-spoke operator events BEFORE the attested-"
        f"allow reset (the attested-allow path resets to 0 per "
        f"reset trigger 1)"
    )

    # ── The graph ended (route hint None on the attested-allow path).
    route = (
        state.get("attestation_route")
        if isinstance(state, dict)
        else getattr(state, "attestation_route", None)
    )
    assert route is None, (
        f"cell (b): attested-allow MUST END the graph "
        f"(attestation_route=None); got route={route!r}"
    )

    # ── Channel reset on the attested-allow path (the fix-A reviewer-
    # flagged A1 stale-flag leak guard). Even if no substantive was
    # ever stamped, the channels MUST be cleared to False so the next
    # episode starts clean.
    channel = (
        state.get(ATTESTATION_ANY_SUBSTANTIVE_KEY)
        if isinstance(state, dict)
        else getattr(state, ATTESTATION_ANY_SUBSTANTIVE_KEY, None)
    )
    assert channel is False, (
        f"cell (b): the substantive channel MUST be cleared on the "
        f"attested-allow path (reset trigger 1 + the stale-flag "
        f"leak guard); got {channel!r}"
    )

    # ── Counter reset on the attested-allow path (reset trigger 1:
    # the attested-allow reset clears the per-mission counter).
    counter_after_attest = repo.get_attestation_denied_count(LEADER_INSTANCE_ID)
    assert counter_after_attest == 0, (
        f"cell (b): counter MUST be reset to 0 on the attested-allow "
        f"path (reset trigger 1); got counter={counter_after_attest}"
    )

    # ── completion_gate_escalated is FALSE on the attested-allow
    # path (only set_escalated_and_reset on terminal_after_bound
    # writes True; the attested-allow path is a plain allow).
    instance = repo.get(LEADER_INSTANCE_ID)
    assert getattr(instance, "completion_gate_escalated", False) is False, (
        f"cell (b): completion_gate_escalated MUST stay False on the "
        f"attested-allow path (only terminal_after_bound sets it to "
        f"True — the never-spoke arc never produced a terminal); "
        f"got completion_gate_escalated="
        f"{getattr(instance, 'completion_gate_escalated', None)!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cell (c) — Directive nudge fires on 2nd deny ONLY when zero new tool calls
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_c_directive_nudge_fires_on_second_deny_with_zero_progress(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """Cell (c) — directive nudge on no-progress repeat denies (Fix 3).

    Scripted arc (4 LLM turns):
      LLM1 — delegate via send_message (attestation_required=True)
      LLM2 — short mid-work ACK → eval1: judge=not_complete (substantive)
              ⇒ DENIED, counter 0→1, STANDARD nudge (1st deny, NOT
                 directive — ``counted_denied_count=1 < 2`` fails the
                 ``_is_repeat_deny`` predicate)
      LLM3 — short mid-work ACK → eval2: judge=not_complete (substantive)
              ⇒ DENIED, counter 1→2, DIRECTIVE nudge (2nd deny AND
                 zero progress since prior deny snapshot — the same
                 short-ACK shape ⇒ zero new tool calls ⇒
                 ``_zero_progress=True``)

    Code-derived expected semantics:
      * Eval1: decide() returns DENIED. Counter increments to 1.
        ``_is_repeat_deny = (counted_denied_count >= 2) = (1 >= 2) =
        False`` ⇒ STANDARD nudge body
        (``ATTESTATION_NUDGE_TEXT``, NOT the directive).
      * Eval2: decide() returns DENIED. Counter increments to 2.
        ``_is_repeat_deny = (2 >= 2) = True``. The progress snapshot
        recorded at eval1's deny = current tool-call count (0, the
        leader had no tool calls). Eval2's current tool-call count
        (0, same short-ACK shape) <= prior snapshot (0) ⇒
        ``_zero_progress=True``. ⇒ DIRECTIVE nudge body
        (``ATTESTATION_DIRECTIVE_NUDGE_TEXT``).
      * The directive body MUST byte-pin to the canonical constant
        ``daemon.graph.ATTESTATION_DIRECTIVE_NUDGE_TEXT`` (the
        verbatim directive the 7d4a3bd9 fix cycle ships).
      * The standard body MUST NOT match the directive body (the
        directive-vs-standard selection is mutually exclusive).

    Independence from the dev witness: same harness pattern (real-
    graph ainvoke + scripted chat model), distinct scenario
    (consecutive substantive denies with zero progress, NOT the
    Ep-B or all-timeout arcs cells (a)/(b) drive).
    """
    repo, _instance = attestation_repository

    # The leader is alone in the tree — R2 inputs all zero → deny band
    # fires.
    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=0,
        queued_wakeups=0,
        live_descendants=0,
    )

    # Judge sequence: all 3 substantive evals substantive (not_complete).
    # The 7d4a3bd9 fix pairs directive-nudge with the all-timeout
    # continuation in cell (b); for THIS cell we use substantive
    # verdicts so we can observe the directive-vs-standard selection
    # in isolation (NOT entangled with the all-timeout continuation).
    # Scripted arc drives the gate to bound exhaustion (eval4 trips
    # the bound with substantive=True) so the graph reaches a clean
    # END and the assertion phase can read the surviving
    # attestation_nudge HumanMessage:
    #   * eval1 (counter 0→1): STANDARD nudge (``_is_repeat_deny=False``)
    #   * eval2 (counter 1→2): DIRECTIVE nudge (``_is_repeat_deny=True``
    #     ∧ ``_zero_progress=True``)
    #   * eval3 (counter 2→3): DIRECTIVE nudge (continues)
    #   * eval4 (counter 3→4): BOUND trips with substantive=True →
    #     loud ``terminal_after_bound`` written (NO judge call —
    #     budget parity); graph ends.
    judge = _SequencedJudge(
        verdicts=["not_complete", "not_complete", "not_complete"]
    )
    model = ScriptedChatModel(
        responses=[
            _delegate_ai(),    # LLM1 — delegate
            _holding_ack(),    # LLM2 — eval1 (substantive, STANDARD nudge,
                               #          counter 0→1)
            _holding_ack(),    # LLM3 — eval2 (substantive, DIRECTIVE
                               #          nudge fires, counter 1→2)
            _holding_ack(),    # LLM4 — eval3 (substantive, directive
                               #          nudge continues, counter 2→3)
            _holding_ack(),    # LLM5 — eval4 (bound trips 3+1>3, NO
                               #          judge, substantive=True ⇒
                               #          loud terminal written, counter
                               #          reset to 0, completion_gate_
                               #          escalated=True, graph ends)
        ],
        i=0,
    )
    graph = _build_graph(real_graph_module, model, manager, memory_saver)

    caplog.set_level(logging.INFO)
    with patch(
        "daemon.services.attestation_report_judge.judge_fused_bundle_async",
        new=judge,
    ):
        state = await graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content=USER_MISSION),
                ]
            },
            config={
                "configurable": {"thread_id": LEADER_INSTANCE_ID},
                "recursion_limit": 30,
            },
        )
    model.assert_all_responses_consumed()

    # ── Judge budget: EXACTLY 3 calls (eval1/2/3 substantive; eval4
    # trips the bound → budget parity → NO judge call).
    assert judge.calls == 3, (
        f"cell (c): judge MUST be invoked exactly 3 times "
        f"(eval1/2/3 substantive; eval4 trips the bound → budget "
        f"parity → NO judge call); got {judge.calls} calls"
    )

    # ── Counter: reset to 0 on the bound terminal (the eval4
    # ``terminal_after_bound`` triggers ``set_escalated_and_reset``
    # which writes ``completion_gate_escalated=True`` AND resets the
    # counter to 0 per reset trigger 2 — the same semantics as
    # reset trigger 1 on the attested-allow path).
    counter = repo.get_attestation_denied_count(LEADER_INSTANCE_ID)
    assert counter == 0, (
        f"cell (c): counter MUST be reset to 0 after the bound "
        f"terminal (Phase-3 ledger's set_escalated_and_reset on "
        f"eval4); got counter={counter}"
    )

    # ── completion_gate_escalated IS True on the bound terminal
    # (the loud unverified surface the Fix 4 threads to the record).
    instance = repo.get(LEADER_INSTANCE_ID)
    assert getattr(instance, "completion_gate_escalated", False) is True, (
        f"cell (c): completion_gate_escalated MUST be True after "
        f"the bound terminal (the loud unverified surface); got "
        f"completion_gate_escalated="
        f"{getattr(instance, 'completion_gate_escalated', None)!r}"
    )

    # ── The latest surviving nudge body MUST byte-pin to the
    # canonical directive constant (LangGraph's add_messages reducer
    # upserts consecutive denies in place via the stable
    # ``attestation_nudge:{instance_id}`` id — the surviving message
    # carries the LAST nudge body).
    messages = state["messages"]
    nudges = _nudge_messages(messages)
    assert len(nudges) >= 1, (
        f"cell (c): ≥1 attestation_nudge HumanMessage MUST survive "
        f"the reducer upsert (the directive body rides the same "
        f"stable id as the standard nudge); got {len(nudges)} nudges"
    )
    directive_body = ATTESTATION_DIRECTIVE_NUDGE_TEXT
    standard_body = ATTESTATION_NUDGE_TEXT
    # The directive and standard bodies MUST be distinct (the
    # selection is mutually exclusive — if they're equal the gate
    # cannot tell them apart).
    assert directive_body != standard_body, (
        "cell (c): ATTESTATION_DIRECTIVE_NUDGE_TEXT and "
        "ATTESTATION_NUDGE_TEXT MUST be distinct — the gate's "
        "directive-vs-standard selection is mutually exclusive"
    )
    surviving = nudges[-1].content
    assert surviving == directive_body, (
        f"cell (c): the surviving nudge body MUST byte-pin to "
        f"ATTESTATION_DIRECTIVE_NUDGE_TEXT (the canonical directive "
        f"constant from daemon/graph.py); the directive fires on "
        f"the 2nd deny ONLY when zero progress since the prior "
        f"deny snapshot; got surviving body excerpt: "
        f"{surviving[:200]!r}; expected body excerpt: "
        f"{directive_body[:200]!r}"
    )
    # The directive body MUST NOT match the standard body (the
    # directive-vs-standard selection is observable).
    assert surviving != standard_body, (
        f"cell (c): the surviving nudge MUST NOT match "
        f"ATTESTATION_NUDGE_TEXT (the 2nd deny fires the directive, "
        f"NOT the standard); got surviving body excerpt: "
        f"{surviving[:200]!r}"
    )

    # ── The [AttestationGate] deny log line MUST stamp
    # ``nudge_kind=directive`` on the 2nd deny (the canonical log
    # surface operators grep for to confirm the directive fired).
    deny_log_lines = [
        r.getMessage()
        for r in caplog.records
        if "[AttestationGate] deny instance=" in r.getMessage()
    ]
    directive_log_lines = [
        line for line in deny_log_lines if "nudge_kind=directive" in line
    ]
    standard_log_lines = [
        line for line in deny_log_lines if "nudge_kind=standard" in line
    ]
    assert len(directive_log_lines) >= 1, (
        f"cell (c): ≥1 [AttestationGate] deny log line MUST stamp "
        f"nudge_kind=directive (the canonical log surface for the "
        f"directive nudge firing on the 2nd deny); got "
        f"{len(directive_log_lines)} directive lines and "
        f"{len(standard_log_lines)} standard lines; all deny lines: "
        f"{deny_log_lines}"
    )
    assert len(standard_log_lines) >= 1, (
        f"cell (c): ≥1 [AttestationGate] deny log line MUST stamp "
        f"nudge_kind=standard (the canonical log surface for the "
        f"standard nudge firing on the 1st deny); got "
        f"{len(standard_log_lines)} standard lines and "
        f"{len(directive_log_lines)} directive lines; all deny lines: "
        f"{deny_log_lines}"
    )

    # ── The directive log line MUST carry the zero-progress witness.
    # The directive is conditional on both ``_is_repeat_deny`` AND
    # ``_zero_progress``; the log line stamps BOTH so operators can
    # confirm the selection at a glance.
    directive_line = directive_log_lines[-1]
    assert "prior_progress=" in directive_line, (
        f"cell (c): the directive log line MUST carry prior_progress= "
        f"(the zero-progress witness the directive-vs-standard gate "
        f"reads); got: {directive_line}"
    )
    assert "progress_tools=" in directive_line, (
        f"cell (c): the directive log line MUST carry progress_tools= "
        f"(the current tool-call snapshot the gate reads); got: "
        f"{directive_line}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Auxiliary ToolMessage helper — kept for future cells that drive the
# tool-call path (cell (b) uses it implicitly through the ScriptedChatModel
# flow but does not assert on the tool message body).
# ─────────────────────────────────────────────────────────────────────────────


def _attest_tool_message(call_id: str = "lcafc-attest-1") -> ToolMessage:
    """The tool message the ToolNode emits after executing
    ``attest_completion``. Not directly asserted by any cell in this
    file but exported for symmetry with the lcancheck harness."""
    return ToolMessage(
        content='{"attested": true, "timestamp": "2026-09-26T00:00:00+00:00"}',
        tool_call_id=call_id,
        name="attest_completion",
    )