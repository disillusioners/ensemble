"""LCA Stage-2 BUDGET PARITY MATRIX — judge-call counting parity (flip ON vs OFF × scenario).

LCA resolver Stage 2 final merge gate — Job 3 of 9 (the BUDGET BEHAVIOR
LIVE counting matrix). Drives the REAL gate node
(``daemon.graph.create_attestation_gate_node``) through nine scenarios ×
two flip states (fused authoritative / legacy sites live) and pins the
exact judge-call counts on each cell:

* flip ON (``_LCA_STAGE2_RESOLVER_FLIP=True``):
    - ``judge_fused_bundle_async`` entries ≤1 per evaluation EVERYWHERE;
      HTTP attempts ≤2 within one invocation.
    - ``judge_completion_report_async`` (legacy) ZERO calls across all
      scenarios (R-RES2-8).
* flip OFF (monkeypatched ``_LCA_STAGE2_RESOLVER_FLIP`` to ``False``):
    - ``judge_fused_bundle_async`` ZERO calls everywhere.
    - ``judge_completion_report_async`` per-scenario counts reflect the
      legacy marker-path + would-be-deny sites.

**Parity contract (the test asserts this LITERALLY):**
the ONLY scenario where flip ON adds a judge call vs flip OFF is
``(3) A-band alone`` (fused 1 vs legacy 0). Every other scenario:
identical counts (0→0 or 1→1). Any other delta = FAIL with evidence.

Scenario-specific contract pins:
* **(8) unparsable-first-attempt retry** — ``entries==1 ∧ attempts==2``
  pinned on the path(s) that fire (fused on flip ON, legacy on flip OFF).
* **(9) bound-terminal 4th deny cycle** — across the 4-cycle arc:
  cycles 1-3 invoke the judge (1 entry each, 1 attempt with parsable
  responses); cycle 4 (``denied_count=3`` ⇒ ``3+1>3`` ⇒
  ``TERMINAL_AFTER_BOUND``) is ``judge_invoked=False`` (0 entries,
  0 attempts). Totals: 3 fused entries on flip ON, 3 legacy entries on
  flip OFF.

Spies:
* HTTP-attempt counter replaces ``daemon.services.attestation_report_
  judge._invoke_judge_llm`` (the LLM HTTP call site inside both judges).
* Entry counters wrap ``judge_fused_bundle_async`` and
  ``judge_completion_report_async`` with delegates to the REAL
  implementations (so the retry-on-unparsable inner loop fires for
  real inside the wrapped entry, preserving the 98b59dd7 contract).
  Both entry-point attributes are monkeypatched at the module level —
  the graph node imports them inside the block at call time, so the
  monkeypatch intercepts the real call sites.
* Attempt-count partitioning: ``_invoke_judge_llm`` is shared by both
  fused and legacy judges. With the flip invariant
  (``flip ON ⇒ legacy entries == 0``; ``flip OFF ⇒ fused entries == 0``)
  the HTTP-attempt count IS the per-flip-side attempt count (entries
  partition attempts by construction).

KNOWN DEFECT (canned verdict JSONs must stay ≤ :data:`JUDGE_MAX_OUTPUT_
CHARS` = 400 chars in BOTH paths so all stubs parse cleanly; per
``attestation_report_judge.py:120/897/1316/1354`` truncation fires
BEFORE parsing). All canned builders below enforce ``len(body) ≤ 400``;
any breach raises ``AssertionError`` at construction time.

Production code is FROZEN — this file is test-only. Mirrors the harness
patterns in ``tests/integration/test_attestation_stage2_killswitch.py``
and ``tests/integration/test_attestation_stage2_incident_abc.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Sequence
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import daemon.graph as daemon_graph_mod
from daemon.graph import create_attestation_gate_node
from daemon.services import attestation_report_judge as judge_mod
from daemon.services.attestation_gate import (
    Decision,
    GateSettings,
    build_gate_config,
)
from daemon.services.attestation_judge_resolver import (
    is_llm_judge_enabled,
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_judge_timeout_resolver import (
    reset_judge_timeout_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)

# ─────────────────────────────────────────────────────────────────────────────
# Drift-pin guards — fail loud on branch drift or a flip that landed
# as the opposite of the assumed state.
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
# Hermetic resolver caches + kill-switch env per test (mirror killswitch
# fixture). Three cached-global resolvers must be reset per test for
# hermetic isolation; otherwise a previous cell's mode/kill-switch state
# leaks into the next.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolvers_between_cells(monkeypatch):
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_MODE", raising=False
    )
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    reset_judge_timeout_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    reset_judge_timeout_resolver_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# Canned verdict JSON helpers — ≤400 chars (JUDGE_MAX_OUTPUT_CHARS).
# Two verdict shapes (parses of the two judge paths):
#   * fused:      {"verdict": "complete"|"not_complete", ...}
#   * legacy:     {"is_complete_report": bool, "reason": str}
# ─────────────────────────────────────────────────────────────────────────────


def _assert_within_output_cap(body: str, label: str) -> str:
    """ENFORCE the 400-char cap (KNOWN DEFECT: truncation-before-parse).

    Both judges truncate the LLM response to JUDGE_MAX_OUTPUT_CHARS
    BEFORE parsing; an over-length canned verdict would silently be
    truncated and produce an unparsable row (hiding a real count
    parity violation behind a parse-shape regression). Fail loud here.
    """
    cap = judge_mod.JUDGE_MAX_OUTPUT_CHARS
    assert len(body) <= cap, (
        f"{label}: canned verdict length {len(body)} exceeds "
        f"JUDGE_MAX_OUTPUT_CHARS={cap} — would be truncated "
        f"BEFORE parse and corrupt the count matrix."
    )
    return body


def _fused_complete_json(
    evidence=("report enumerates outcomes",),
) -> str:
    return _assert_within_output_cap(
        json.dumps(
            {
                "verdict": "complete",
                "evidence_cited": list(evidence),
                "advisory_note_text": "",
                "rationale": "genuine",
            }
        ),
        "_fused_complete_json",
    )


def _fused_not_complete_json(
    evidence=("SOURCE A: child-terminal contradiction evidence",),
    advisory="The child's report promised future work",
) -> str:
    return _assert_within_output_cap(
        json.dumps(
            {
                "verdict": "not_complete",
                "evidence_cited": list(evidence),
                "advisory_note_text": advisory,
                "rationale": "mid-work",
            }
        ),
        "_fused_not_complete_json",
    )


def _legacy_complete_json(reason: str = "report enumerates outcomes") -> str:
    return _assert_within_output_cap(
        json.dumps(
            {"is_complete_report": True, "reason": reason}
        ),
        "_legacy_complete_json",
    )


def _legacy_not_complete_json(reason: str = "mid-work") -> str:
    return _assert_within_output_cap(
        json.dumps(
            {"is_complete_report": False, "reason": reason}
        ),
        "_legacy_not_complete_json",
    )


def _unparsable_text() -> str:
    """A body that fails both ``_parse_fused_judge_response`` and
    ``_parse_judge_response`` — triggers the retry-on-unparsable
    second attempt inside both judges."""
    return _assert_within_output_cap(
        "Sorry, I cannot help with that.",
        "_unparsable_text",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Judge-call spies — HTTP-attempt level (replaces _invoke_judge_llm) and
# entry-point level (wraps both judge functions, delegating to REAL impls
# so the inner retry-on-unparsable fires for real).
# ─────────────────────────────────────────────────────────────────────────────


class _HttpAttemptSpy:
    """Records every HTTP attempt to ``_invoke_judge_llm``.

    ``responses`` is a list of LLM bodies consumed in order across
    attempts; a single-element list is reused for every attempt (so a
    deterministic response can be replayed). Every attempt appends the
    payload to ``self.attempts``.
    """

    def __init__(self, responses: Sequence[str]):
        self.responses = list(responses)
        self.attempts: list[str] = []

    async def __call__(
        self,
        config,
        user_payload,
        *,
        timeout_s,
        system_prompt=None,
    ):
        self.attempts.append(user_payload)
        if len(self.responses) == 1:
            return (self.responses[0], "fake-quick")
        idx = min(len(self.attempts) - 1, len(self.responses) - 1)
        return (self.responses[idx], "fake-quick")


@dataclass
class _Counters:
    """Entry counters at the two judge entry points.

    ``attempts`` is the TOTAL HTTP-attempt count (shared across both
    entry points because both funnel through ``_invoke_judge_llm``).
    ``as_tuple`` partitions it per side by the entries invariant: in a
    single cell only ONE side can fire (flip ON structurally disables
    both legacy entry conditions; flip OFF removes the fused block), so
    ``entries == 0`` on a side ⇒ ``0`` attempts on that side.
    """

    fused_entries: int = 0
    legacy_entries: int = 0
    attempts: int = 0

    def as_tuple(self) -> tuple[int, int, int, int]:
        fused_att = self.attempts if self.fused_entries > 0 else 0
        legacy_att = self.attempts if self.legacy_entries > 0 else 0
        return (
            self.fused_entries,
            fused_att,
            self.legacy_entries,
            legacy_att,
        )


def _install_spies(
    monkeypatch, *, flip: bool, responses: Sequence[str]
) -> tuple[_HttpAttemptSpy, _Counters]:
    """Install entry counters + HTTP-attempt spy.

    * flips ``daemon.graph._LCA_STAGE2_RESOLVER_FLIP`` to ``flip``
    * monkeypatches ``judge_mod._invoke_judge_llm`` with the HTTP spy
    * wraps ``judge_fused_bundle_async`` and
      ``judge_completion_report_async`` with counting delegates to the
      REAL impls (preserves retry-on-unparsable contract; records
      entries).
    """
    monkeypatch.setattr(
        daemon_graph_mod, "_LCA_STAGE2_RESOLVER_FLIP", flip, raising=True
    )
    http_spy = _HttpAttemptSpy(responses)
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", http_spy)
    counters = _Counters()

    real_fused = judge_mod.judge_fused_bundle_async

    async def _counting_fused(bundle_text, *, config, timeout_s=None):
        counters.fused_entries += 1
        return await real_fused(
            bundle_text, config=config, timeout_s=timeout_s
        )

    monkeypatch.setattr(judge_mod, "judge_fused_bundle_async", _counting_fused)

    real_legacy = judge_mod.judge_completion_report_async

    async def _counting_legacy(
        messages,
        *,
        config,
        window=judge_mod.JUDGE_DEFAULT_WINDOW,
        timeout_s=None,
    ):
        counters.legacy_entries += 1
        return await real_legacy(
            messages,
            config=config,
            window=window,
            timeout_s=timeout_s,
        )

    monkeypatch.setattr(
        judge_mod, "judge_completion_report_async", _counting_legacy
    )

    # Sync the attempt count via a tiny post-call hook: a fresh
    # ``_HttpAttemptSpy`` instance is created locally in this fn;
    # reading ``len(http_spy.attempts)`` post-cell gives the canonical
    # total. We expose it via the spy for clarity.
    counters.attempts = len(http_spy.attempts)

    return http_spy, counters


def _sync_attempts(counters: _Counters, http_spy: _HttpAttemptSpy) -> None:
    """Pull the post-run HTTP-attempt count from the spy into counters."""
    counters.attempts = len(http_spy.attempts)


# ─────────────────────────────────────────────────────────────────────────────
# Node builders + state helpers — full-knob mirror of the killswitch
# fixture, extended with user_answer_pending and denied_count_getter.
# ─────────────────────────────────────────────────────────────────────────────


def _make_node(
    *,
    instance_id: str,
    pending_children: int = 0,
    queued_wakeups: int = 0,
    live_descendants: int = 0,
    busy_descendants: int = 0,
    denied_count: int = 0,
    user_answer_pending: bool = False,
    mode: str = "enforce",
    settings: GateSettings | None = None,
    llm_judge_enabled: bool = True,
    denied_count_getter=None,
    manager: MagicMock | None = None,
    ledger: MagicMock | None = None,
):
    if manager is None:
        manager = MagicMock()
        manager.count_pending_children.return_value = pending_children
        manager.get_queued_or_expected_wakeups.return_value = queued_wakeups
        manager.count_live_descendants.return_value = live_descendants
        manager.count_busy_descendants.return_value = busy_descendants
        manager.get_tree_ids_permanent.return_value = []
    manager.has_open_user_answer = MagicMock(return_value=user_answer_pending)
    manager.is_watchover_enabled = MagicMock(return_value=False)
    manager.is_question_pause_requested = MagicMock(return_value=False)
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    if ledger is None:
        ledger = MagicMock()
        ledger.increment.return_value = 1
        ledger.reset.return_value = True
        ledger.set_escalated_and_reset.return_value = True
        ledger.get.return_value = denied_count

    settings = settings or GateSettings(mode, 3, 3)
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


def _delegated_state(final_text: str, extra_messages=()) -> dict:
    """Delegated mission — send_message tool_call anchors the conditional
    gate as ON (the 2026-09-06 amendment; the marker/length trigger
    scan runs only on a delegated mission)."""
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


def _attested_state(final_text: str = "I am done with attestation.") -> dict:
    """Delegated mission WITH attestation in window — attestation
    present short-circuits the marker scan AND triggers meta-bypass
    (attested=True ⇒ predicate fired=False, bypass_reason=meta_bypass).
    The mission text is short (markers CANNOT fire on this path —
    the marker scan is SKIPPED for attested allows)."""
    delegation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": "child-id"}, "id": "c1"}
        ],
    )
    attestation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "attest_completion", "args": {}, "id": "a1"}
        ],
    )
    return {
        "messages": [
            HumanMessage(content="please do it"),
            delegation_ai,
            attestation_ai,
            AIMessage(content=final_text),
        ]
    }


def _child_report_check_note(child_id: str | None = None) -> HumanMessage:
    """System-injected child-report contradiction note (Δ1 source A)."""
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


# Marker-free long mission (≥150 words; avoids ALL MID_WORK_MARKERS
# substrings AND CHILD_TERMINAL_PROMISE_MARKERS substrings so neither
# marker_hit nor length_trigger fires). This is the answer-pending
# scenario mission (scenario 5) — on flip OFF the legacy marker-path
# judge entry requires marker_hit OR length_trigger; both must stay
# False on flip OFF to preserve the parity contract.
_LONG_MARKER_FREE_MISSION: str = (
    "The work for this engagement concluded successfully across all of "
    "the agreed-upon deliverables. The cache TTL fix ships in commit "
    "f8b2c4d1 with the elapsed-second and wall-clock-second boundaries "
    "both exercised by the unit suite; the worker-pool dead-import "
    "cleanup landed in commit a3e9d772 with the legacy compatibility "
    "shim retired and the migration runner now idempotent across "
    "SQLite and Postgres targets. The integration log shows a clean "
    "restart-to-v0.12.9 boot window with both readiness probes "
    "returning two hundred, and the proactive-compaction ninety-five "
    "percent pre-call hook rides the return-carried channel so the "
    "checkpoint commit lands atomically alongside the post-channel "
    "writes. No further work pending for this run; the report stands "
    "as written. End of turn."
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
# Per-scenario cell functions — each returns the counters after one or
# four evaluations. Behavioral asserts (nudge / hint / route / increment
# / set_escalated) live inside the cells so a wrong fixture fails fast
# with a meaningful message (counts-only asserts would mask a fixture
# that silently produced the wrong band).
# ─────────────────────────────────────────────────────────────────────────────


def _cell_deny_band_quiet(flip, monkeypatch, caplog) -> _Counters:
    """(1) deny-band: un-attested ∧ quiet. band=deny, decision=DENIED.

    On flip ON: fused judge fires (1 entry, 1 attempt) ⇒ not_complete →
    deny+nudge (Q1 parity: judge is a RESCUER, the rescue fails →
    deny+nudge).
    On flip OFF: legacy would-be-deny judge fires (decision=DENIED,
    marker_path not in {"a","d"}) (1 entry, 1 attempt) ⇒ not_complete
    → deny+nudge. Legacy marker-path judge does NOT fire
    (decision=DENIED, not in legacy marker entry set).
    """
    caplog.clear()
    _capture(caplog)
    responses = [_fused_not_complete_json() if flip else _legacy_not_complete_json()]
    _http, counters = _install_spies(monkeypatch, flip=flip, responses=responses)
    node, _m, ledger = _make_node(instance_id="s1-deny")
    result = _run(node, _delegated_state("All done, shipped. Ending turn."), "s1-deny")

    # Both flips deny+nudge behaviorally (different decision paths but
    # identical operator-visible result on the deny path).
    assert "messages" in result, "s1 deny-band: nudge MUST be injected"
    assert result["attestation_route"] == "agent"
    ledger.increment.assert_called_once()
    # Band row on flip ON (resolver_eval row only fires when the fused
    # block runs).
    if flip:
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows and "band=deny" in eval_rows[0]
        assert "resolver_outcome=deny_nudge" in eval_rows[0]
        assert "judge_invoked=True" in eval_rows[0]
    _sync_attempts(counters, _http)
    return counters


def _cell_marker_band(flip, monkeypatch, caplog) -> _Counters:
    """(2) marker-band: marker_hit (real marker phrase, not length
    trigger) ∧ ¬quiet. decision=ALLOWED with marker_hit=True.

    On flip ON: fused judge fires (1 entry, 1 attempt) ⇒ complete →
    route-(c) plain allow, no hint, no nudge, no counter.
    On flip OFF: legacy marker-path judge fires
    (decision=ALLOWED ∧ marker_hit=True ∧ not suppressed ∧ kill-switch
    ON) (1 entry, 1 attempt) ⇒ complete → plain allow.
    Both flips: 0 legacy on flip ON, 0 fused on flip OFF.
    """
    caplog.clear()
    _capture(caplog)
    responses = [_fused_complete_json() if flip else _legacy_complete_json()]
    _http, counters = _install_spies(monkeypatch, flip=flip, responses=responses)
    # pending=1 ⇒ NOT quiet (deny band suppressed); busy=0 ⇒ b_fires;
    # "will write" in mission ⇒ marker_hit=True (real marker term,
    # not length trigger).
    node, _m, ledger = _make_node(
        instance_id="s2-marker",
        pending_children=1,
    )
    result = _run(
        node,
        _delegated_state(
            "Awaiting child report. I will write the final summary next."
        ),
        "s2-marker",
    )

    # Plain allow — NO nudge, NO hint, no counter.
    assert "messages" not in result, (
        "s2 marker-band: plain allow — NO nudge/hint"
    )
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    # Canonical row carries marker_hit=True on both flips.
    gate_rows = _rows(caplog, "event=leader_completion_gate ")
    assert gate_rows and "marker_hit=True" in gate_rows[0]
    if flip:
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows and "band=marker" in eval_rows[0]
        assert "judge_invoked=True" in eval_rows[0]
    _sync_attempts(counters, _http)
    return counters


def _cell_a_band_alone(flip, monkeypatch, caplog) -> _Counters:
    """(3) A-band ALONE: a_suspicion ∧ ¬quiet ∧ ¬b_fires
    (busy=2 mutes the marker/length trigger; the child-report
    check note drives Source A alone; Δ2 row — Source A is NOT
    busy-suppressed). decision=ALLOWED with marker_hit=False,
    length_trigger=True (short final) but trigger_suppressed_by=busy.

    On flip ON: fused judge fires (1 entry, 1 attempt) ⇒ complete →
    plain allow. THIS IS THE ONLY CELL WHERE FLIP ADDS A CALL vs
    legacy.
    On flip OFF: BOTH legacy judge sites are silent —
    * marker-path: blocked by `not decision.trigger_suppressed_by` →
      False (busy suppresses).
    * would-be-deny: decision=ALLOWED → condition false.
    So legacy=0. The parity delta: fused 1 vs legacy 0.
    """
    caplog.clear()
    _capture(caplog)
    responses = [_fused_complete_json() if flip else _legacy_complete_json()]
    _http, counters = _install_spies(monkeypatch, flip=flip, responses=responses)
    node, _m, ledger = _make_node(
        instance_id="s3-a-band",
        pending_children=1,
        live_descendants=2,
        busy_descendants=2,
    )
    state = _delegated_state(
        "Everything is done here.",
        extra_messages=[_child_report_check_note()],
    )
    result = _run(node, state, "s3-a-band")

    # Plain allow (the marker/A signal alone is too weak to deny without
    # the LLM verdict — fused complete ⇒ route-(c) plain allow).
    assert "messages" not in result, "s3 A-band: plain allow — NO nudge/hint"
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    if flip:
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows and "band=a_suspicion" in eval_rows[0]
        assert "judge_invoked=True" in eval_rows[0]
    # On flip OFF, the trigger is busy-suppressed; the canonical row
    # still records the state.
    gate_rows = _rows(caplog, "event=leader_completion_gate ")
    assert gate_rows
    assert "marker_hit=False" in gate_rows[0]
    _sync_attempts(counters, _http)
    return counters


def _cell_attested_metabypass(flip, monkeypatch, caplog) -> _Counters:
    """(4) attested meta-bypass: attested=True → predicate
    fired=False, bypass_reason=meta_bypass.

    Both flips: 0 judge entries.
    """
    caplog.clear()
    _capture(caplog)
    responses = [_fused_complete_json() if flip else _legacy_complete_json()]
    _http, counters = _install_spies(monkeypatch, flip=flip, responses=responses)
    node, _m, ledger = _make_node(instance_id="s4-attested")
    result = _run(node, _attested_state(), "s4-attested")

    assert "messages" not in result, "s4 attested: plain allow — NO nudge/hint"
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    if flip:
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows and "bypass_reason=meta_bypass" in eval_rows[0]
        assert "fired=False" in eval_rows[0] or "bypass_reason=meta_bypass" in eval_rows[0]
        assert "judge_invoked=False" in eval_rows[0]
    _sync_attempts(counters, _http)
    return counters


def _cell_answer_pending(flip, monkeypatch, caplog) -> _Counters:
    """(5) user-answer-pending: has_open_user_answer=True →
    ALLOWED_LEGITIMATE_PENDING_WAKEUP.

    Resolver: meta-bypass (user_answer_pending) ⇒ fired=False.
    On flip OFF: decision=ALLOWED_LEGITIMATE_PENDING_WAKEUP IS in the
    legacy marker-path entry set, but the long marker-free mission
    text keeps marker_hit=False AND length_trigger=False ⇒ legacy
    marker-path judge does NOT fire. No deny ⇒ no legacy would-be-deny.
    Both flips: 0.
    """
    caplog.clear()
    _capture(caplog)
    responses = [_fused_complete_json() if flip else _legacy_complete_json()]
    _http, counters = _install_spies(monkeypatch, flip=flip, responses=responses)
    node, _m, ledger = _make_node(
        instance_id="s5-answer-pending",
        user_answer_pending=True,
        pending_children=1,  # not quiet — wakeup arm natural
    )
    result = _run(
        node,
        _delegated_state(_LONG_MARKER_FREE_MISSION),
        "s5-answer-pending",
    )

    # ALLOWED_LEGITIMATE_PENDING_WAKEUP — plain allow, no nudge/hint,
    # no counter increment (the pending counter is consumed via the
    # wakeup arm, not the deny arm).
    assert "messages" not in result, "s5 answer-pending: plain allow — NO nudge/hint"
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    if flip:
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows and "judge_invoked=False" in eval_rows[0]
    # Canonical row carries decision=allowed_legitimate_pending_wakeup.
    gate_rows = _rows(caplog, "event=leader_completion_gate ")
    assert gate_rows
    assert "decision=allowed_legitimate_pending_wakeup" in gate_rows[0]
    _sync_attempts(counters, _http)
    return counters


def _cell_mode_dry(flip, monkeypatch, caplog) -> _Counters:
    """(6) MODE=dry: GateSettings("dry", 3, 3). decision=DRY_LOG.

    On flip ON: judge_plan_on requires mode=="enforce" → False ⇒
    no judge call (R3: dry computes + logs, node skipped).
    On flip OFF: dry-mode tuple check EXCLUDES DRY_LOG from the
    legacy marker-path entry; no deny ⇒ no would-be-deny. Both flips:
    0.
    """
    caplog.clear()
    _capture(caplog)
    responses = [_fused_complete_json() if flip else _legacy_complete_json()]
    _http, counters = _install_spies(monkeypatch, flip=flip, responses=responses)
    node, _m, ledger = _make_node(
        instance_id="s6-mode-dry",
        mode="dry",
    )
    result = _run(node, _delegated_state("All done, shipped."), "s6-mode-dry")

    # DRY_LOG row stamped; no nudge, no counter.
    assert "messages" not in result, "s6 dry: NO nudge"
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    ledger.reset.assert_not_called()
    ledger.set_escalated_and_reset.assert_not_called()
    gate_rows = _rows(caplog, "event=leader_completion_gate ")
    assert gate_rows
    assert "decision=dry_log" in gate_rows[0]
    if flip:
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows
        assert "mode=dry" in eval_rows[0]
        assert "judge_invoked=False" in eval_rows[0]
    _sync_attempts(counters, _http)
    return counters


def _cell_killswitch_off(flip, monkeypatch, caplog) -> _Counters:
    """(7) judge kill-switch OFF: env=0 + reset + gate_config
    llm_judge_enabled=False.

    On flip ON deny-band: fused_judge_on=False ⇒ disabled row, no call.
    On flip OFF deny-band: judge_on=False / marker_judge_on=False ⇒
    no calls. Both flips: 0.
    """
    caplog.clear()
    _capture(caplog)
    responses = [_fused_not_complete_json() if flip else _legacy_not_complete_json()]
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", "0")
    reset_llm_judge_resolver_for_tests()
    assert is_llm_judge_enabled() is False, (
        "s7 pre-condition: kill-switch OFF must flip the real resolver "
        "to False (resolver-cache leak guard)."
    )
    _http, counters = _install_spies(monkeypatch, flip=flip, responses=responses)
    try:
        node, _m, ledger = _make_node(
            instance_id="s7-killswitch-off",
            llm_judge_enabled=False,  # gate_config must agree with env
        )
        result = _run(
            node, _delegated_state("All done, shipped."), "s7-killswitch-off"
        )

        # deny-band behavior preserved (kill-switch off does not change
        # the deny decision itself on flip OFF; on flip ON the deny is
        # also unchanged — the deny-path judge is a RESCUER, kill-
        # switch off denies WITHOUT a rescue).
        assert "messages" in result, "s7 deny-band: deny+nudge retained"
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()
        if flip:
            # Disabled-row carries the explicit sentinel.
            disabled_rows = _rows(
                caplog, "event=leader_completion_gate_fused_judge_disabled"
            )
            assert disabled_rows and "verdict=<skipped>" in disabled_rows[0]
            assert "band=deny" in disabled_rows[0]
            eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
            assert eval_rows and "judge_invoked=False" in eval_rows[0]
    finally:
        # Hermetic cleanup so subsequent cells in a cross-flip test
        # see a fresh resolver cache.
        monkeypatch.delenv(
            "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
        )
        reset_llm_judge_resolver_for_tests()
    _sync_attempts(counters, _http)
    return counters


def _cell_unparsable_retry(flip, monkeypatch, caplog) -> _Counters:
    """(8) unparsable-first-attempt retry: deny-band fixture with
    responses=[unparsable, parsable].

    Both flips: entries==1 ∧ attempts==2 on the path(s) that fire
    (fused on flip ON, legacy on flip OFF). The OTHER path stays
    silent (entries==0).
    """
    caplog.clear()
    _capture(caplog)
    responses = [_unparsable_text(), _fused_complete_json() if flip else _legacy_complete_json()]
    _http, counters = _install_spies(monkeypatch, flip=flip, responses=responses)
    node, _m, ledger = _make_node(instance_id="s8-unparsable")
    # Parsable 2nd response = "complete" / "yes" → fused rescue → plain
    # allow; legacy yes → rescue → no counter increment.
    result = _run(node, _delegated_state("All done, shipped."), "s8-unparsable")

    # Rescue succeeded → plain allow, no nudge, no counter.
    assert "messages" not in result, (
        "s8 unparsable-retry: rescue succeeded → plain allow"
    )
    assert result["attestation_route"] is None
    ledger.increment.assert_not_called()
    if flip:
        eval_rows = _rows(caplog, "event=leader_completion_resolver_eval ")
        assert eval_rows and "judge_invoked=True" in eval_rows[0]
        # attempt=2 sentinel on the canonical row (incident 98b59dd7
        # forensic contract).
        fused_judge_rows = _rows(caplog, "event=leader_completion_gate_fused_judge ")
        assert fused_judge_rows and "attempt=2" in fused_judge_rows[0]
    _sync_attempts(counters, _http)
    return counters


def _cell_bound_terminal_arc(flip, monkeypatch, caplog) -> _Counters:
    """(9) bound-terminal 4th deny cycle: drive 4 sequential deny
    cycles with denied_count escalating 0→1→2→3. Cycles 1-3 are DENIED
    (judge fires on the path(s) that fire); cycle 4 hits
    ``deny_bound_exceeded(denied_count=3, bound=3) = 3+1>3 = True`` →
    TERMINAL_AFTER_BOUND WITHOUT a judge call.

    Totals: 3 fused entries on flip ON, 3 legacy entries on flip OFF.
    Cycle 4: judge_invoked=False (terminal).
    """
    caplog.clear()
    _capture(caplog)
    responses = [_fused_not_complete_json() if flip else _legacy_not_complete_json()]
    _http, counters = _install_spies(monkeypatch, flip=flip, responses=responses)
    denied_state = {"count": 0}
    ledger = MagicMock()
    ledger.increment.return_value = denied_state["count"] + 1
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = 0
    node, _m, _l = _make_node(
        instance_id="s9-bound",
        denied_count_getter=lambda: denied_state["count"],
        ledger=ledger,
    )

    cycle_texts = [
        "Awaiting final four. Then aggregate and write RESULTS. Ending turn.",
        "Still working on it. Ending turn.",
        "Continuing. Ending turn.",
        "Continuing. Ending turn.",
    ]
    per_cycle = []
    for cycle_idx, text in enumerate(cycle_texts):
        ledger.reset_mock()
        _http.attempts.clear()
        counters.fused_entries = 0
        counters.legacy_entries = 0
        result = _run(node, _delegated_state(text), "s9-bound")
        per_cycle.append(
            (
                counters.fused_entries,
                counters.legacy_entries,
                len(_http.attempts),
            )
        )
        if cycle_idx < 3:
            # Deny+nudge on cycles 1-3.
            assert "messages" in result, (
                f"s9 cycle {cycle_idx + 1}: deny+nudge required"
            )
            assert result["attestation_route"] == "agent"
            ledger.increment.assert_called_once()
            denied_state["count"] = cycle_idx + 1
        else:
            # Cycle 4: TERMINAL_AFTER_BOUND, no judge, no nudge.
            assert "messages" not in result, (
                "s9 cycle 4: terminal — NO nudge"
            )
            assert result["attestation_route"] is None
            ledger.set_escalated_and_reset.assert_called_once()
            ledger.increment.assert_not_called()
            denied_state["count"] = 0  # reset
            # Operator terminal row.
            terminal_rows = _rows(
                caplog, "event=leader_completion_gate_terminal_after_bound"
            )
            assert terminal_rows
    # Aggregate per the contract: cycles 1-3 fired one judge each on
    # the relevant path; cycle 4 fired zero.
    total_fused = sum(c[0] for c in per_cycle)
    total_legacy = sum(c[1] for c in per_cycle)
    total_attempts = sum(c[2] for c in per_cycle)
    counters.fused_entries = total_fused
    counters.legacy_entries = total_legacy
    counters.attempts = total_attempts
    return counters


# ─────────────────────────────────────────────────────────────────────────────
# Per-cell expected counts (the parity table the test pins LITERALLY):
#   scenario → {flip ON: (fused_ent, attempts, legacy_ent, attempts),
#               flip OFF: (...)}.
# Attempts is the shared HTTP-attempt counter; partition by entries
# (entries==0 on the silent side ⇒ 0 attempts on that side).
# ─────────────────────────────────────────────────────────────────────────────


_SCENARIOS = [
    # (id, runner, expected ON, expected OFF)
    # Tuple shape: (fused_entries, fused_attempts, legacy_entries, legacy_attempts).
    # Attempts are partitioned per side by the entries invariant (a side
    # with 0 entries has 0 attempts by construction).
    (
        "s1-deny-band-quiet",
        _cell_deny_band_quiet,
        (1, 1, 0, 0),
        (0, 0, 1, 1),
    ),
    (
        "s2-marker-band",
        _cell_marker_band,
        (1, 1, 0, 0),
        (0, 0, 1, 1),
    ),
    (
        "s3-a-band-alone",
        _cell_a_band_alone,
        (1, 1, 0, 0),
        (0, 0, 0, 0),
    ),
    (
        "s4-attested-metabypass",
        _cell_attested_metabypass,
        (0, 0, 0, 0),
        (0, 0, 0, 0),
    ),
    (
        "s5-answer-pending",
        _cell_answer_pending,
        (0, 0, 0, 0),
        (0, 0, 0, 0),
    ),
    (
        "s6-mode-dry",
        _cell_mode_dry,
        (0, 0, 0, 0),
        (0, 0, 0, 0),
    ),
    (
        "s7-killswitch-off",
        _cell_killswitch_off,
        (0, 0, 0, 0),
        (0, 0, 0, 0),
    ),
    (
        "s8-unparsable-retry",
        _cell_unparsable_retry,
        (1, 2, 0, 0),
        (0, 0, 1, 2),
    ),
    (
        "s9-bound-terminal-arc",
        _cell_bound_terminal_arc,
        # Across the 4-cycle arc: 3 fused entries (cycles 1-3),
        # 3 attempts with parsable responses; cycle 4 = 0.
        (3, 3, 0, 0),
        (0, 0, 3, 3),
    ),
]


def _expected(scenario: str, flip: bool):
    for sid, _runner, on, off in _SCENARIOS:
        if sid == scenario:
            return on if flip else off
    raise KeyError(scenario)


# ─────────────────────────────────────────────────────────────────────────────
# Per-cell parametrized matrix test.
# ─────────────────────────────────────────────────────────────────────────────


_SCENARIO_IDS = [s[0] for s in _SCENARIOS]


class TestBudgetParityMatrix:
    """Each (scenario × flip) cell — exact count pins + flip invariants.

    Run order is implicit (pytest parametrize). Within a single cell,
    the flip-ON/flip-OFF invariants are asserted (legacy silence on
    flip ON; fused silence on flip OFF; ≤1 fused entry per evaluation).
    """

    @pytest.mark.parametrize(
        "flip", [True, False], ids=["flip-ON-fused", "flip-OFF-legacy"]
    )
    @pytest.mark.parametrize("scenario", _SCENARIO_IDS)
    def test_cell_counts(self, monkeypatch, caplog, scenario, flip):
        runner = dict((s, r) for s, r, _on, _off in _SCENARIOS)[scenario]
        actual = runner(flip, monkeypatch, caplog).as_tuple()
        expected = _expected(scenario, flip)
        fused_ent, fused_att, legacy_ent, legacy_att = actual
        exp_fused_ent, exp_fused_att, exp_legacy_ent, exp_legacy_att = expected
        assert actual == expected, (
            f"COUNT MISMATCH scenario={scenario} flip={flip}: "
            f"actual=(fused_ent={fused_ent}, fused_att={fused_att}, "
            f"legacy_ent={legacy_ent}, legacy_att={legacy_att}) "
            f"expected=(fused_ent={exp_fused_ent}, fused_att={exp_fused_att}, "
            f"legacy_ent={exp_legacy_ent}, legacy_att={exp_legacy_att})"
        )

        # Flip-specific global invariants.
        if flip:
            assert legacy_ent == 0 and legacy_att == 0, (
                f"R-RES2-8 violated on flip ON scenario={scenario}: "
                f"legacy_entries={legacy_ent} legacy_attempts={legacy_att}"
            )
            # ≤1 fused entry per single evaluation; for the multi-cycle
            # s9 cell we already verified totals == 3 across 4 cycles.
            if scenario != "s9-bound-terminal-arc":
                assert fused_ent <= 1, (
                    f"§5 budget guard violated: scenario={scenario} flip ON "
                    f"had {fused_ent} fused entries (>1 in one evaluation)"
                )
            assert fused_att <= 2 * max(1, fused_ent), (
                "§5 budget guard: >2 attempts per invocation"
            )
        else:
            assert fused_ent == 0 and fused_att == 0, (
                f"Fused block must stay silent on flip OFF; scenario="
                f"{scenario} got fused_entries={fused_ent} attempts={fused_att}"
            )
            if scenario != "s9-bound-terminal-arc":
                assert legacy_ent <= 1, (
                    f"Legacy budget guard: >1 legacy entry per evaluation "
                    f"on scenario={scenario}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# Cross-flip parity delta test — the literal "only scenario 3 differs"
# claim.
# ─────────────────────────────────────────────────────────────────────────────


class TestCrossFlipParityDelta:
    """One test running the FULL 9 × 2 matrix; asserts the parity delta.

    For every scenario, flip ON's fused-entries count MUST equal flip
    OFF's legacy-entries count, EXCEPT scenario (3) A-band alone where
    fused ON = 1 and legacy OFF = 0 (the documented, intentional parity
    delta). Any other delta = FAIL with the actual numbers.

    Attempts parity: across both flips, the HTTP-attempt counts are
    identical per scenario (the retry contract is preserved on both
    paths).
    """

    def test_only_scenario_3_gains_a_call_across_flip(
        self, monkeypatch, caplog
    ):
        results: dict[str, dict[bool, tuple[int, int, int, int]]] = {}
        # Run scenarios in a fixed order; clear caplog between cells.
        for sid in _SCENARIO_IDS:
            results[sid] = {}
            for flip in (True, False):
                caplog.clear()
                _capture(caplog)
                runner = dict((s, r) for s, r, _on, _off in _SCENARIOS)[sid]
                actual = runner(flip, monkeypatch, caplog).as_tuple()
                results[sid][flip] = actual

        # Build the parity delta table.
        deltas: list[tuple[str, int, int, int, int]] = []
        for sid in _SCENARIO_IDS:
            on = results[sid][True]
            off = results[sid][False]
            on_fused, on_att, _on_legacy, _on_legacy_att = on
            _off_fused, _off_fused_att, off_legacy, off_legacy_att = off
            delta = on_fused - off_legacy
            deltas.append((sid, on_fused, off_legacy, on_att, off_legacy_att))

        # The ONLY scenario where delta != 0 is s3-a-band-alone.
        violations = [
            (sid, on, off, att_on, att_off)
            for (sid, on, off, att_on, att_off) in deltas
            if on != off and sid != "s3-a-band-alone"
        ]
        assert not violations, (
            "PARITY DELTA VIOLATION — the ONLY scenario where flip adds "
            "a judge call vs old path is s3-a-band-alone. Unexpected "
            "deltas found:\n"
            + "\n".join(
                f"  {sid}: ON_fused={on} OFF_legacy={off} "
                f"attempts ON={att_on} OFF={att_off}"
                for (sid, on, off, att_on, att_off) in violations
            )
        )

        # Sanity: s3-a-band-alone is the only delta (1 vs 0).
        s3 = next(d for d in deltas if d[0] == "s3-a-band-alone")
        assert s3[1] == 1 and s3[2] == 0, (
            f"s3-a-band-alone delta regressed: ON_fused={s3[1]} "
            f"OFF_legacy={s3[2]} (expected 1 vs 0)"
        )

        # Attempts parity: ON fused attempts == OFF legacy attempts per
        # scenario (the retry contract is preserved on both paths) —
        # EXCEPT s3-a-band-alone, which is the documented entries delta
        # (fused 1/1 vs legacy 0/0; attempts follow entries by
        # construction).
        att_violations = [
            (sid, on_att, off_att)
            for (sid, _on, _off, on_att, off_att) in deltas
            if on_att != off_att and sid != "s3-a-band-alone"
        ]
        assert not att_violations, (
            "ATTEMPTS PARITY VIOLATION — ON fused attempts MUST equal "
            "OFF legacy attempts per scenario (except the s3 delta):\n"
            + "\n".join(
                f"  {sid}: ON_attempts={on_att} OFF_attempts={off_att}"
                for (sid, on_att, off_att) in att_violations
            )
        )
        # Sanity: the s3 attempts delta mirrors the entries delta
        # exactly (1 vs 0).
        assert s3[3] == 1 and s3[4] == 0, (
            f"s3-a-band-alone attempts delta regressed: "
            f"ON_attempts={s3[3]} OFF_attempts={s3[4]} (expected 1 vs 0)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Canned JSON length pre-flight (module-level guard against future
# edits that would silently breach the truncation-before-parse defect).
# ─────────────────────────────────────────────────────────────────────────────


def test_canned_verdicts_within_output_cap():
    """KNOWN DEFECT guard: every canned verdict body MUST be ≤ 400
    chars (JUDGE_MAX_OUTPUT_CHARS) so all stubs parse cleanly under the
    truncation-before-parse hazard. Fails loud at collection time."""
    cap = judge_mod.JUDGE_MAX_OUTPUT_CHARS
    bodies = [
        _fused_complete_json(),
        _fused_not_complete_json(),
        _legacy_complete_json(),
        _legacy_not_complete_json(),
        _unparsable_text(),
    ]
    for body in bodies:
        assert len(body) <= cap, (
            f"canned body length {len(body)} > JUDGE_MAX_OUTPUT_CHARS={cap}: "
            f"{body!r}"
        )
