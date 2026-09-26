"""LCAFC SEMANTICS verification pack (Jobs 3 + 4).

Merge gate for incident 7d4a3bd9 (correct-judge-override class). INDEPENDENT
asserts — every value here is read from the daemon source under
``daemon/services/``; this module does NOT import the dev's
``tests/unit/test_lca_false_complete_fixes.py``.

§1 JUDGE TIMEOUT (Job 3) — ``daemon/services/attestation_judge_timeout_resolver.py``
  * Default 180.0s with env UNSET (Pattern C cached-global, fail-OPEN).
  * Env override honored (set 12.5 ⇒ resolved 12.5).
  * Min clamp: sub-floor (1.0) clamps UP to 5.0.
  * Retry-once unchanged — max 2 attempts per evaluation. Pinned via the
    resolver's source-text + the ``DEFAULT_JUDGE_TIMEOUT_S`` /
    ``MIN_JUDGE_TIMEOUT_S`` constants the resolver reads.

§2 SCANNER FIX (Job 4) — ``daemon/services/attestation_scanner.py`` +
    ``daemon/services/attestation_marker_scanner.py`` (the length + marker
    scanners the gate consumes).
  * Length scan now runs on EVERY evaluated path (FIX-5a,
    ``daemon/services/attestation_gate.py:1474``): final AIMessage word
    count is the REAL split-count for the fixture (computed in-test);
    ``length_trigger`` is present + measured (not the dataclass default
    ``False/0`` shape that the prior routing-gated stamp produced).
  * Marker scan remains ROUTING-GATED by ``attestation_required`` (the
    D10/R4 fold): when ``attestation_required=False`` the marker scan is
    SKIPPED — ``marker_hit`` stays at the dataclass default ``False`` and
    ``marker_terms`` stays ``()``. When ``attestation_required=True`` AND
    a marker substring is present in the last AIMessage, the marker scan
    fires and the result carries the firing terms.

Notes on R3 route: ``R3`` appears in this worktree as the Stage-3 R3 fold
(``attestation_gate.py`` docstring ``Stage 3 (2026-09-17,
resolver-unification R2/R3/R4)`` — the dry-mode mapping). The marker
scanner itself does NOT name an ``R3`` route; the marker scan's routing
predicate is the 4-way ``if`` at ``attestation_gate.py:1531-1550``. The
spec asked "if R3 is a named route in the scanner/resolver, pin it" —
since no R3-named route exists in the scanner/resolver surface, this
test pack documents that absence explicitly via a sentinel marker in
the module docstring above (rather than fabricating a pin).
"""
from __future__ import annotations

import inspect
import os

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from unittest.mock import MagicMock

from daemon.services.attestation_gate import (
    Decision,
    GateSettings,
    evaluate,
)
from daemon.services.attestation_judge_timeout_resolver import (
    DEFAULT_JUDGE_TIMEOUT_S,
    ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV,
    MIN_JUDGE_TIMEOUT_S,
    _parse_judge_timeout_s,
    get_judge_timeout_s,
    reset_judge_timeout_resolver_for_tests,
)
from daemon.services.attestation_marker_scanner import (
    MID_WORK_MARKERS,
    SHORT_REPORT_WORD_THRESHOLD,
    scan_for_mid_work_markers,
    scan_for_short_final_ai,
)
from daemon.services.attestation_report_judge import (
    FusedJudgeResult,
    judge_fused_bundle_async,
)
from daemon.services.attestation_scanner import DEFAULT_ATTESTATION_TOOL_NAME


# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures + helpers
# ─────────────────────────────────────────────────────────────────────────────


# 7d4a3bd9 amendment: DEFAULT bumped 25.0s → 180.0s on 2026-09-26 (incident
# 7d4a3bd9 Episode B). Pin this so a future amendment moves the test.
_EXPECTED_DEFAULT_JUDGE_TIMEOUT_S: float = 180.0
_EXPECTED_MIN_JUDGE_TIMEOUT_S: float = 5.0


@pytest.fixture(autouse=True)
def _isolate_judge_timeout_resolver(monkeypatch):
    """Hermetic timeout-resolver isolation per the resolver's Pattern C contract.

    Mirrors the autouse fixture in
    ``tests/unit/test_attestation_judge_resolver.py`` — clears the env
    var + resets the cached-global resolver + the two one-shot WARN
    flags so each test re-resolves from a clean slate. Without this,
    the Pattern C cached-global + a side-effect from another test
    module would mask a regression here.
    """
    monkeypatch.delenv(
        ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV, raising=False
    )
    reset_judge_timeout_resolver_for_tests()
    yield
    reset_judge_timeout_resolver_for_tests()


def _real_ai(content: str) -> AIMessage:
    """Plain text AIMessage with no tool_calls (a Q&A final message)."""
    return AIMessage(content=content, tool_calls=[])


def _make_manager() -> MagicMock:
    """Manager stub matching the four facade surface the gate reads.

    Mirrors ``tests/unit/test_attestation_gate.py::make_manager`` —
    all four reads (pending_children, queued_or_expected_wakeups,
    live_descendants, busy_descendants) defaulted to 0; tests that
    need a non-zero value override per-test. ``has_open_user_answer``
    is intentionally NOT stubbed — the duck-typed fallback in
    ``attestation_gate.py`` reads it as False when missing.
    """
    mgr = MagicMock()
    mgr.count_pending_children = MagicMock(return_value=0)
    mgr.get_queued_or_expected_wakeups = MagicMock(return_value=0)
    mgr.count_live_descendants = MagicMock(return_value=0)
    mgr.count_busy_descendants = MagicMock(return_value=0)
    return mgr


# ─────────────────────────────────────────────────────────────────────────────
# §1 JUDGE TIMEOUT (Job 3) — daemon/services/attestation_judge_timeout_resolver.py
# ─────────────────────────────────────────────────────────────────────────────


class TestJudgeTimeoutResolver:
    """§1 — judge wall-clock cap resolver, 7d4a3bd9 amendment 180.0s."""

    def test_default_resolves_to_180_with_env_unset(self):
        """Job 3 default: env UNSET ⇒ 180.0s (7d4a3bd9 amendment).

        Pins ``DEFAULT_JUDGE_TIMEOUT_S == 180.0`` AND verifies the
        Pattern C resolver returns it when the env var is absent.
        The autouse ``_isolate_judge_timeout_resolver`` fixture
        deletes the env var BEFORE ``get_judge_timeout_s`` is
        called — proves the resolver does not read a leaked env from
        a sibling test.
        """
        # CONSTANT pin (single source of truth — the resolver reads it).
        assert DEFAULT_JUDGE_TIMEOUT_S == _EXPECTED_DEFAULT_JUDGE_TIMEOUT_S, (
            f"DEFAULT_JUDGE_TIMEOUT_S regressed: "
            f"got {DEFAULT_JUDGE_TIMEOUT_S}, "
            f"expected {_EXPECTED_DEFAULT_JUDGE_TIMEOUT_S} "
            f"(7d4a3bd9 amendment, 2026-09-26)"
        )
        assert DEFAULT_JUDGE_TIMEOUT_S == 180.0

        # Resolver behavior pin: unset env ⇒ default.
        resolved = get_judge_timeout_s()
        assert resolved == 180.0, (
            f"get_judge_timeout_s() with env unset resolved to "
            f"{resolved}; expected 180.0"
        )

        # Direct parser pin (bypasses the cached-global so a regression
        # in the parser vs the cached-global is caught independently).
        parsed = _parse_judge_timeout_s({})
        assert parsed == 180.0, (
            f"_parse_judge_timeout_s({{}}) returned {parsed}; "
            f"expected 180.0 (DEFAULT_JUDGE_TIMEOUT_S)"
        )

    def test_env_override_honored(self):
        """Job 3 env override: ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=12.5`` ⇒ 12.5.

        Pins the env var name AND that values above the floor pass
        through verbatim (no rounding, no min-clamp).
        """
        # ENV VAR NAME pin — single source of truth.
        assert (
            ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV
            == "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S"
        )

        # Parser-level pin (independent of the cached-global).
        parsed = _parse_judge_timeout_s(
            {ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV: "12.5"}
        )
        assert parsed == 12.5, (
            f"env override 12.5 did not pass through; got {parsed}"
        )

        # Cached-global pin (resolves once, returns cached).
        os.environ[ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV] = "12.5"
        try:
            reset_judge_timeout_resolver_for_tests()
            resolved = get_judge_timeout_s()
            assert resolved == 12.5, (
                f"get_judge_timeout_s() with env=12.5 resolved to "
                f"{resolved}; expected 12.5"
            )
        finally:
            del os.environ[ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV]

    def test_min_clamp_below_floor(self):
        """Job 3 min clamp: 1.0 ⇒ 5.0 (clamp fires UP, not DOWN).

        Pins ``MIN_JUDGE_TIMEOUT_S == 5.0`` AND that values below the
        floor clamp UP to the floor (not silently accepted, not
        fail-OPENed to the default — both are explicit divergence
        from the spec).
        """
        # MIN pin — single source of truth.
        assert MIN_JUDGE_TIMEOUT_S == _EXPECTED_MIN_JUDGE_TIMEOUT_S
        assert MIN_JUDGE_TIMEOUT_S == 5.0

        # Below-floor pin (1.0 ⇒ 5.0).
        parsed = _parse_judge_timeout_s(
            {ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV: "1.0"}
        )
        assert parsed == 5.0, (
            f"below-floor 1.0 did not clamp to MIN_JUDGE_TIMEOUT_S=5.0; "
            f"got {parsed}"
        )

        # Negative pin (fail-OPEN to default, NOT clamp) — proves the
        # clamp fires only on 0 < value < MIN; non-positive values take
        # the invalid-value fail-OPEN branch (the spec is explicit).
        parsed_negative = _parse_judge_timeout_s(
            {ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV: "-3"}
        )
        assert parsed_negative == 180.0, (
            f"negative value -3 should fail-OPEN to default 180.0 "
            f"(NOT clamp to 5.0); got {parsed_negative}"
        )

    def test_retry_once_unchanged_max_two_attempts(self):
        """Job 3 retry-once unchanged: max 2 attempts per evaluation.

        Mechanism pinned:
        * ``FusedJudgeResult.attempt`` is the canonical attempt
          counter (dataclass field, default 1) — the retry-once path
          stamps ``attempt=2``.
        * The retry fires in
          ``daemon/services/attestation_report_judge.py`` after the
          FIRST attempt times out (incident bc145c7e R1, 2026-09-19)
          — single retry, no third attempt on the same logical
          invocation.
        * The wall-clock cap the retry fires under is the SAME
          per-attempt timeout (``min(resolved_timeout,
          config.llm.request_timeout or resolved_timeout)``).

        Concretely pinned:
        (a) The default of ``FusedJudgeResult.attempt`` is ``1`` — a
            single-call success path stays at 1 (the dataclass default).
        (b) The resolver source has the single-retry semantic
            (``_attempt_once`` is invoked twice on the timeout branch,
            and ONLY on the timeout branch — HTTP/API errors keep no
            retry).
        (c) ``judge_fused_bundle_async`` is async — the retry loop
            is inside ONE async call (no third invocation).
        """
        # (a) Dataclass default pin — single-call success ⇒ attempt=1.
        result_single = FusedJudgeResult(
            invoked=True,
            is_complete=True,
            verdict="complete",
        )
        assert result_single.attempt == 1, (
            "FusedJudgeResult.attempt must default to 1 (single-call "
            "success path)"
        )

        # (b) Source-text pin on the single-retry semantic.
        judge_source = inspect.getsource(judge_fused_bundle_async)
        # The retry fires ONLY on the first-attempt timeout branch.
        assert "first.kind == \"timeout\"" in judge_source, (
            "judge_fused_bundle_async must branch on first-attempt "
            "timeout to fire the retry (incident bc145c7e R1, 2026-09-19)"
        )
        # The retry invokes _attempt_once EXACTLY ONCE (not in a loop).
        # The "second = await _attempt_once()" line is the single retry.
        assert "second = await _attempt_once()" in judge_source, (
            "judge_fused_bundle_async must invoke _attempt_once exactly "
            "twice (attempt 1 + single retry) — not in a loop"
        )
        # NO loop construct around the retry — single retry, not N retries.
        assert "for attempt in" not in judge_source, (
            "judge_fused_bundle_async must NOT loop over attempts "
            "(retry-once is the contract)"
        )
        # attempt=2 stamped on the retry-returned result.
        assert "attempt=2" in judge_source, (
            "judge_fused_bundle_async must stamp attempt=2 on the "
            "retry-returned FusedJudgeResult"
        )

        # (c) Async-function pin — retry is a sequential await chain,
        # not a concurrent fan-out.
        assert inspect.iscoroutinefunction(judge_fused_bundle_async), (
            "judge_fused_bundle_async must be async — the retry "
            "loop is a sequential await chain inside ONE invocation"
        )


# ─────────────────────────────────────────────────────────────────────────────
# §2 SCANNER FIX (Job 4) — daemon/services/attestation_scanner.py +
#     daemon/services/attestation_marker_scanner.py
# ─────────────────────────────────────────────────────────────────────────────


class TestScannerFixLengthOnEveryPath:
    """§2 length scan (FIX-5a) — runs on EVERY evaluated path."""

    def test_length_scan_returns_real_word_count_for_qa_turn(self):
        """FIX-5a: Q&A turn (no delegation, long final AI) ⇒
        ``final_word_count > 0`` AND equals the real split-count.

        Builds a fixture:
        * HumanMessage (real user prompt).
        * AIMessage with real prose (~30+ words, well over the
          SHORT_REPORT_WORD_THRESHOLD=150 the brevity class needs
          to clear).

        Pins:
        * ``scan_for_short_final_ai(...).final_word_count > 0`` —
          the length scan returns a REAL measurement, not the
          dataclass default 0 (the FIX-5a defect shape).
        * ``final_word_count == expected_split_count`` — the
          measurement matches ``text.split()`` on the AIMessage
          content (the contract — same shape the marker scanner
          substring-matches against).
        * ``length_trigger == False`` — the prose is long enough
          to clear the brevity class (i.e. NOT brevity). The
          measurement is meaningful, not always-False.
        """
        # Compose the final-AI content (≥ 30 words, well above the
        # 150-word brevity threshold — proves length_trigger=False
        # when the prose is genuinely long, the OLD routing-gated
        # stamp would have stamped False/0 by DEFAULT for this
        # non-delegated path, hiding the real measurement).
        final_ai_content = (
            "LangGraph state reducers are functions that merge update keys into "
            "the existing state dict at runtime. This project wires reducers in "
            "the StateGraph constructor at daemon/graph.py for the messages key, "
            "the user_answer_pending flag, and the wait queue. They run AFTER "
            "each node returns its partial update, and they are the canonical "
            "way to compose concurrent updates to the same key without losing "
            "intermediate writes — the messages reducer specifically appends "
            "newly produced messages without overwriting older ones, the "
            "wait-queue reducer merges entries by id and tracks the head/tail "
            "of the queue so wake-up dispatch stays deterministic, and the "
            "user_answer_pending flag reducer is a last-write-wins identity "
            "for the boolean state with no merge step. The reducers are "
            "configured once at graph-build time in the daemon module that "
            "constructs the StateGraph and they fire automatically inside "
            "LangGraph's apply_node_updates step, so individual node "
            "implementations never call them by hand. I hope this helps."
        )
        # Compute expected word count IN-TEST (per spec — "compute
        # expected in-test"). The split-count on the flattened string
        # is the contract: ``text.split()`` with no args collapses
        # any whitespace run.
        expected_word_count = len(final_ai_content.split())
        # Self-check the fixture itself: must be >= 30 words (spec) AND
        # >= SHORT_REPORT_WORD_THRESHOLD so length_trigger=False (the
        # NOT-brevity class — proves the measurement is real, not the
        # always-False default).
        assert expected_word_count >= 30, (
            f"fixture too short: {expected_word_count} words; "
            f"spec requires ~30+ words"
        )
        assert expected_word_count >= SHORT_REPORT_WORD_THRESHOLD, (
            f"fixture must clear the brevity threshold "
            f"({SHORT_REPORT_WORD_THRESHOLD}); got {expected_word_count} "
            f"words — increase the prose to test length_trigger=False"
        )

        messages = [
            HumanMessage(content="Hi, can you explain how LangGraph state "
                                 "reducers work in this project?"),
            _real_ai(final_ai_content),
        ]

        # Pin via the public scanner (FIX-5a's primary entry point).
        length_result = scan_for_short_final_ai(messages, window=3)

        # PRIMARY FIX-5a pin: final_word_count > 0 (NOT the old default-0
        # shape that the routing-gated stamp produced for non-delegated
        # rows).
        assert length_result.final_word_count > 0, (
            "FIX-5a defect shape: length scan returned final_word_count=0 "
            "for a non-empty final AIMessage — the OLD routing-gated stamp "
            "produced defaults for non-delegated paths"
        )
        # The measurement equals the real split-count (the contract).
        assert length_result.final_word_count == expected_word_count, (
            f"final_word_count={length_result.final_word_count} but the "
            f"split-count on the flattened AIMessage content is "
            f"{expected_word_count} — measurement does not match contract"
        )
        # Length trigger is MEASURED False (NOT the default-False shape —
        # the prose clears the brevity threshold, so a correctly-running
        # scanner returns False because the word count IS >= 150).
        assert length_result.length_trigger is False, (
            f"length_trigger should be False (prose has "
            f"{expected_word_count} words, >= "
            f"SHORT_REPORT_WORD_THRESHOLD={SHORT_REPORT_WORD_THRESHOLD}); "
            f"got length_trigger={length_result.length_trigger}"
        )

        # Cross-pin via the gate composition path — confirms the
        # length scan runs on the evaluate() path (FIX-5a
        # hoisted the scan above the routing-gated block at
        # attestation_gate.py:1474).
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)
        result = evaluate("test-iid-qa", 0, messages, settings, _make_manager())

        # Non-delegated Q&A ⇒ Term-1 plain ALLOW (the gate is OFF).
        assert result.decision is Decision.ALLOWED, (
            f"non-delegated Q&A turn should ALLOW via the Term-1 "
            f"fold; got decision={result.decision.value}"
        )
        assert result.attestation_required is False, (
            "non-delegated Q&A turn must have attestation_required=False "
            "(the R4 fold — no send_message since the last real user "
            "message ⇒ gate is OFF)"
        )
        # FIX-5a effect on the gate composition path: length-signal
        # fields are stamped with the REAL measurement, not the
        # dataclass defaults (final_word_count=0 / length_trigger=False).
        assert result.final_word_count == expected_word_count, (
            f"gate composition did not stamp the real length signal: "
            f"final_word_count={result.final_word_count}, expected "
            f"{expected_word_count}"
        )
        assert result.length_trigger is False, (
            f"gate composition did not stamp the real length signal: "
            f"length_trigger={result.length_trigger}, expected False"
        )


class TestScannerFixMarkerStillRoutingGated:
    """§2 marker scan remains ROUTING-GATED by ``attestation_required``."""

    def test_marker_scan_skipped_when_attestation_required_false(self):
        """Marker scan gating: non-delegated Q&A ⇒ marker fields stay
        at the DATACLASS DEFAULTS (``False`` / ``()``).

        Builds a Q&A fixture (no delegation) and asserts the marker
        signal fields on the gate's GateDecision remain at the
        default shape — the routing predicate at
        ``attestation_gate.py:1531-1550`` skips the marker scan
        when ``attestation_required`` is False (the D10/R4 fold).
        """
        # Final AI has a mid-work marker in plain prose, but the
        # mission is a non-delegated Q&A — the marker scan MUST
        # NOT consume it (the routing gate fires first).
        final_ai_content = (
            "I am ending my turn but the answer is interim, will report "
            "back with the full breakdown once the data has been fully "
            "compiled and verified against the original requirements "
            "document. Sorry for the holdup — the analysis took longer "
            "than expected because the dataset has more edge cases than "
            "anticipated."
        )
        messages = [
            HumanMessage(content="Quick question — what's the difference "
                                 "between asyncio.gather and asyncio.TaskGroup?"),
            _real_ai(final_ai_content),
        ]
        # Sanity-check the fixture has a firing marker (proves the
        # marker scan WOULD fire if it were not gated — otherwise the
        # "skipped" assertion below would be vacuous).
        direct_marker_result = scan_for_mid_work_markers(messages, window=3)
        assert direct_marker_result.marker_hit is True, (
            "fixture invariant broken: scan_for_mid_work_markers must "
            "fire on the marker-bearing AIMessage (otherwise the "
            "routing-gate test below is vacuous)"
        )
        assert len(direct_marker_result.marker_terms) > 0, (
            "fixture invariant broken: marker_terms must be non-empty "
            "for the marker-bearing AIMessage"
        )

        # Gate composition path — non-delegated ⇒ Term-1 plain ALLOW,
        # marker scan SKIPPED (the routing predicate blocks it).
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)
        result = evaluate("test-iid-marker-off", 0, messages, settings, _make_manager())

        # Routing predicate on: ``attestation_required=False``.
        assert result.attestation_required is False, (
            "non-delegated Q&A turn must have attestation_required=False"
        )
        # Gate decision: Term-1 plain ALLOW.
        assert result.decision is Decision.ALLOWED, (
            f"non-delegated Q&A turn should ALLOW; got "
            f"decision={result.decision.value}"
        )
        # THE GATING PIN — marker fields stay at the dataclass
        # defaults because the §(iii.b) routing-gated block at
        # ``attestation_gate.py:1531-1550`` was SKIPPED.
        assert result.marker_hit is False, (
            f"marker scan must be skipped when attestation_required=False; "
            f"got marker_hit={result.marker_hit} (the routing-gated block "
            f"should not have stamped this field at all)"
        )
        assert result.marker_terms == (), (
            f"marker_terms must stay at the dataclass default () when "
            f"attestation_required=False; got {result.marker_terms}"
        )

    def test_marker_scan_fires_when_attestation_required_true(self):
        """Marker scan gating: delegated mission + marker ⇒ marker
        scan FIRES (the §(iii.b) block runs).

        Builds a delegated mission where the LAST AIMessage carries
        a mid-work marker substring and the mission has a LIVE
        descendant (``count_live_descendants=1``) — the
        ``ALLOWED_LEGITIMATE_PENDING_WAKEUP`` decision keeps the
        marker scan in scope, and the marker substring must fire.
        """
        # Final AI: long enough to clear brevity AND has a marker
        # substring. Includes "ending my turn" (a known
        # MID_WORK_MARKERS entry — pinned below).
        assert "ending my turn" in MID_WORK_MARKERS, (
            "fixture invariant broken: 'ending my turn' must be in "
            "MID_WORK_MARKERS catalog (pinned in daemon/services/"
            "attestation_marker_scanner.py)"
        )
        final_ai_content = (
            "I am ending my turn because the child coder is still working "
            "on the implementation and the verifier is in the middle of "
            "running the test suite. The final results are interim — "
            "I will report back once the full test suite has finished "
            "running and the build is verified green, which should take "
            "a few more minutes given the size of the codebase."
        )
        messages = [
            HumanMessage(content="Build a hello-world example for me."),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "send_message",
                     "args": {"target": "coder"},
                     "id": "d1"}
                ],
            ),
            ToolMessage(content="ok", tool_call_id="d1"),
            _real_ai(final_ai_content),
        ]

        # Direct scanner pin (proves the marker substring fires when
        # the scan runs — sanity for the gate composition test below).
        direct = scan_for_mid_work_markers(messages, window=3)
        assert direct.marker_hit is True
        assert "ending my turn" in direct.marker_terms, (
            f"'ending my turn' must appear in marker_terms; got "
            f"{direct.marker_terms}"
        )

        # Gate composition path — delegated mission with a LIVE
        # descendant. The decision becomes
        # ``ALLOWED_LEGITIMATE_PENDING_WAKEUP`` (live_descendants=1
        # ⇒ pending wakeup > 0 ⇒ R2 allow, not deny). The marker
        # scan runs because the §(iii.b) routing gate evaluates to
        # True (decision ∈ allow family, not attested, no answer
        # pending, attestation_required=True).
        mgr = _make_manager()
        mgr.count_live_descendants = MagicMock(return_value=1)
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)
        result = evaluate("test-iid-marker-on", 0, messages, settings, mgr)

        # Routing predicate ON.
        assert result.attestation_required is True, (
            "delegated mission must have attestation_required=True "
            "(send_message since the last real user message)"
        )
        # R2 allow because of live descendant.
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP, (
            f"live-descendant delegated mission should ALLOW via R2; "
            f"got decision={result.decision.value}"
        )
        # THE GATING-ON PIN — marker scan fires, marker terms populated.
        assert result.marker_hit is True, (
            f"marker scan must fire when attestation_required=True "
            f"and the §(iii.b) routing gate evaluates True; got "
            f"marker_hit={result.marker_hit}"
        )
        assert "ending my turn" in result.marker_terms, (
            f"'ending my turn' must appear in marker_terms; got "
            f"{result.marker_terms}"
        )
        # And the length signal is also stamped (FIX-5a hoisted it
        # above the routing gate — so it stamps regardless).
        assert result.final_word_count > 0, (
            "length scan must stamp the real word count on every "
            "evaluated path (FIX-5a — final_word_count should not be 0)"
        )
