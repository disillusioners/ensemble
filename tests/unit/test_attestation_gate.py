"""Unit tests for the attestation gate decision + composition (Phase 2).

Covers ``daemon/services/attestation_gate.py`` (tasks 2.2 + 2.3) and the
graph-side gate config contract (task 2.5 / O8):

* parameterized ``decide()`` matrix (plan 2.2 test notes) with the
  leader-ruling-1 counter semantics — R2-allow does NOT reset;
* canonical Decision enum shape (Phase 4 task 4.5 — single definition);
* ``evaluate()`` composition with the two NEW manager facades mocked;
* C3 fail-open at the DB seam (``leader_completion_gate_db_error``)
  and around the scanner (``leader_completion_gate_error``);
* canonical log-schema field population (Phase 4 task 4.5 reference);
* O8 unit assertion — the gate config shape carries NO ``checkpoint_ns``;
* resolver fail-open on invalid env values (leader ruling 4).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services import attestation_gate as gate_module
from daemon.services.attestation_gate import (
    DEFAULT_GATE_SETTINGS,
    Decision,
    GateSettings,
    build_gate_config,
    decide as _decide_impl,
    evaluate,
    resolve_gate_settings,
)
from daemon.services.attestation_scanner import DEFAULT_ATTESTATION_TOOL_NAME


# Helper for the decide() matrix: 2026-09-06 amendment made
# ``attestation_required`` a keyword-only required parameter (the
# user-spec'd conditional gate). All legacy decide()-matrix tests in
# this file exercise the delegated-mission branch (gate ON), so the
# helper defaults ``attestation_required=True``. Tests that exercise
# the new conditional-gate-OFF branch import the bare ``decide`` and
# pass ``attestation_required=False`` explicitly.
#
# Stage 3 (2026-09-17, resolver-unification R2/R3): decide() lost its
# meta params (scope_applicable / mode / attestation_enabled) — those
# branches moved to evaluate()'s composition layer (predicate Term 0 /
# Term 1 mirrors + the mode-layer dry mapping). The matrix tests below
# still PASS those kwargs (historical shape); the helper drops them so
# the assertions pin the pure enforce tree unchanged.
def decide(*args, **kwargs):
    kwargs.setdefault("attestation_required", True)
    kwargs.pop("scope_applicable", None)
    kwargs.pop("mode", None)
    kwargs.pop("attestation_enabled", None)
    return _decide_impl(*args, **kwargs)


def ai(content="working", tool_calls=None):
    return AIMessage(content=content, tool_calls=tool_calls or [])


def attest_messages():
    """A message tail containing a fresh attestation inside the window."""
    return [
        HumanMessage(content="go"),
        ai(
            "attesting",
            tool_calls=[{"name": DEFAULT_ATTESTATION_TOOL_NAME, "args": {}, "id": "t1"}],
        ),
        ai("All done."),
    ]


def plain_messages():
    """A message tail with NO attestation but WITH a dispatched
    child (delegated mission — the 2026-09-06 conditional-attestation
    gate amendment requires a real delegation for the deny branch to
    fire; without a ``send_message`` tool call the gate correctly
    ALLOWS without demanding the toolcall — see the user-spec'd
    chart-request / quick-follow-up cases).
    """
    return [
        HumanMessage(content="go"),
        # Delegated mission: the AI dispatched a child via send_message.
        ai(
            "",
            tool_calls=[
                {"name": "send_message", "args": {"target": "child"}, "id": "dispatch"}
            ],
        ),
        ai("Working on it."),
        ai("Everything is complete."),
    ]


def make_manager(pending_children=0, wakeups=0, live_descendants=0):
    manager = MagicMock()
    manager.count_pending_children = MagicMock(return_value=pending_children)
    manager.get_queued_or_expected_wakeups = MagicMock(return_value=wakeups)
    # Third R2 input (2026-09-06) — defaulted to 0 for the unit suite.
    # Tests that need a non-zero live-descendant count override via
    # this kwarg (or use MagicMock side_effect on a per-test basis).
    manager.count_live_descendants = MagicMock(return_value=live_descendants)
    # Fourth LCA input (2026-09-12) — busy descendants trigger
    # suppression (defaulted to 0; tests that exercise the suppression
    # branch override per-test). See
    # ``tests/unit/test_attestation_marker_wiring.py`` for the
    # suppression test family.
    manager.count_busy_descendants = MagicMock(return_value=0)
    return manager


# =============================================================================
# decide() — the parameterized R2 matrix (plan 2.2 test notes)
# =============================================================================


class TestMetaConditionsAtCompositionLayer:
    """Stage 3 (R2): decide() lost its meta-condition branch — the
    master-flag / leader-scope / off-mode checks live at evaluate()'s
    composition layer (mirroring the unified predicate's Term 0).
    Production enforces them at GRAPH-BUILD time (the gate node is
    only wired for in-scope leaders with the master flag on); these
    pins hold the evaluate()-level defensive mirror."""

    def _deny_row_messages(self):
        return plain_messages()

    def test_disabled_gate_allows_regardless(self):
        result = evaluate(
            "meta-disabled", 2, self._deny_row_messages(), DEFAULT_GATE_SETTINGS,
            make_manager(), attestation_enabled=False,
        )
        assert result.decision is Decision.ALLOWED
        assert result.should_inject_nudge is False
        # gate didn't run → counter untouched (NOT one of the 4 resets)
        assert result.next_denied_count == 2

    def test_scope_inapplicable_allows(self):
        result = evaluate(
            "meta-scope", 1, self._deny_row_messages(), DEFAULT_GATE_SETTINGS,
            make_manager(), scope_applicable=False,
        )
        assert result.decision is Decision.ALLOWED
        assert result.next_denied_count == 1

    def test_mode_off_allows_regardless(self):
        settings = GateSettings(mode="off", window=3, deny_bound=3)
        result = evaluate(
            "meta-off", 3, self._deny_row_messages(), settings, make_manager(),
        )
        assert result.decision is Decision.ALLOWED
        assert result.next_denied_count == 3


class TestDryModeAtCompositionLayer:
    """Stage 3 (R3): the DRY_LOG mapping moved from decide()'s branch
    to evaluate()'s mode layer — the enforce-tree decision is computed
    with full diagnostics, then mapped to DRY_LOG with the counter
    frozen at its input value. Same observable contract as the retired
    branch: decision=dry_log, zero side effects, counter unchanged."""

    def test_dry_is_dry_log_with_zero_side_effects(self):
        # Dry + missing attestation + R2-deny-predicate satisfied:
        # evaluation recorded (dry_log) but allow + no counter change.
        settings = GateSettings(mode="dry", window=3, deny_bound=3)
        result = evaluate(
            "dry-mapping", 1, plain_messages(), settings, make_manager(),
        )
        assert result.decision is Decision.DRY_LOG
        assert result.should_inject_nudge is False
        assert result.next_denied_count == 1  # zero side effects (frozen)

    def test_dry_with_attestation_still_dry_log(self):
        # The mode-layer mapping disarms the reset: no counter movement
        # in dry even when the enforce tree would have reset (attested).
        settings = GateSettings(mode="dry", window=3, deny_bound=3)
        result = evaluate(
            "dry-attested", 2, attest_messages(), settings, make_manager(),
        )
        assert result.decision is Decision.DRY_LOG
        assert result.next_denied_count == 2


class TestDecideEnforce:
    def test_attested_allow_resets_counter(self):
        # Architect addition — attested allow is reset trigger (1).
        # 2026-09-19 attest-first contract: attested alone is no
        # longer sufficient for ALLOWED — the FINAL AIMessage must
        # ALSO be a standalone text report (no tool calls,
        # >= SHORT_REPORT_WORD_THRESHOLD words). When attested=True
        # WITHOUT a final text report, decide() returns HOLD (not
        # ALLOWED) and the gate injects the clean-call Final
        # Report Reminder.
        result = decide(
            attested=True, pending_children=0, queued_or_expected_wakeups=0, live_descendants=0,
            denied_count=3, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
            final_ai_is_text_report=True,  # 2026-09-19: required for ALLOWED
        )
        assert result.decision is Decision.ALLOWED
        assert result.next_denied_count == 0
        assert result.should_inject_nudge is False

    def test_attested_without_text_report_returns_hold(self):
        """2026-09-19 attest-first contract: attested=True but the
        final AIMessage is NOT a standalone text report (it's the
        attest-call message itself, OR a short/bundled shape) ⇒
        HOLD with reminder injection. Counter is UNCHANGED (no
        bound/escalation interaction). This is the new
        attestation_present-but-not-text-report branch."""
        result = decide(
            attested=True, pending_children=0, queued_or_expected_wakeups=0, live_descendants=0,
            denied_count=3, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
            final_ai_is_text_report=False,  # 2026-09-19: HOLD triggers
            final_ai_is_attest_call=True,  # last AI IS the attest-call
            is_bundled_call=False,  # clean-call HOLD (not bundled)
            reminder_text_clean="[SYSTEM CONTEXT: Final Report Reminder]",
        )
        assert result.decision is Decision.HOLD
        # Counter UNCHANGED — HOLD is NOT a denial. No
        # bound/escalation interaction.
        assert result.next_denied_count == 3
        assert result.should_inject_nudge is False
        assert result.should_inject_reminder is True
        assert result.reminder_text == "[SYSTEM CONTEXT: Final Report Reminder]"

    def test_attested_bundled_returns_hold_with_bundled_reminder(self):
        """2026-09-19 attest-first contract: attested=True AND the
        last AIMessage bundled text + attest tool_call (the
        c5d9a38a shape) ⇒ HOLD with the bundled-call reminder
        text. Counter UNCHANGED. The bundled reminder is
        distinct from the clean-call reminder — the gate node
        passes both texts via ``reminder_text_clean`` /
        ``reminder_text_bundled`` and ``decide()`` picks based on
        ``is_bundled_call``."""
        result = decide(
            attested=True, pending_children=0, queued_or_expected_wakeups=0, live_descendants=0,
            denied_count=2, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
            final_ai_is_text_report=False,
            final_ai_is_attest_call=True,
            is_bundled_call=True,  # c5d9a38a shape
            reminder_text_clean="[CLEAN]",
            reminder_text_bundled="[BUNDLED]",
        )
        assert result.decision is Decision.HOLD
        assert result.next_denied_count == 2
        assert result.should_inject_reminder is True
        assert result.reminder_text == "[BUNDLED]"  # bundled text wins

    def test_r2_allow_pending_children(self):
        result = decide(
            attested=False, pending_children=2, queued_or_expected_wakeups=0, live_descendants=0,
            denied_count=0, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        # RULING 1: the R2 non-reset IS the loop protection.
        assert result.next_denied_count == 0
        assert result.should_inject_nudge is False

    def test_r2_allow_queued_wakeups(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=1, live_descendants=0,
            denied_count=2, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert result.next_denied_count == 2  # unchanged — loop protection
        assert result.should_inject_nudge is False

    def test_first_deny(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0, live_descendants=0,
            denied_count=0, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.DENIED
        assert result.next_denied_count == 1
        assert result.should_inject_nudge is True

    def test_last_deny_before_bound(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0, live_descendants=0,
            denied_count=2, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.DENIED
        assert result.next_denied_count == 3
        assert result.should_inject_nudge is True

    def test_denied_count_at_bound_is_terminal_after_bound(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0, live_descendants=0,
            denied_count=3, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.TERMINAL_AFTER_BOUND
        # Reset trigger (2) — same single reset op clears the escalated
        # flag (ruling 2; persistence is Phase 3).
        assert result.next_denied_count == 0
        # Never a nudge on the escalation path.
        assert result.should_inject_nudge is False

    def test_boundary_bound_minus_one_denies(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0, live_descendants=0,
            denied_count=2, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.DENIED

    @pytest.mark.parametrize("denied_count,bound,expected", [
        (0, 3, Decision.DENIED),
        (1, 3, Decision.DENIED),
        (2, 3, Decision.DENIED),
        (3, 3, Decision.TERMINAL_AFTER_BOUND),
        (4, 3, Decision.TERMINAL_AFTER_BOUND),
        (0, 1, Decision.DENIED),
        (1, 1, Decision.TERMINAL_AFTER_BOUND),
    ])
    def test_bound_boundary_matrix(self, denied_count, bound, expected):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0, live_descendants=0,
            denied_count=denied_count, bound=bound, scope_applicable=True,
            mode="enforce", attestation_enabled=True,
        )
        assert result.decision is expected


class TestDecisionEnumCanonical:
    """The canonical enum (Phase 4 task 4.5 — verbatim values).

    2026-09-19 (attest-first contract, c5d9a38a remediation): the
    enum gained a sixth value, ``Decision.HOLD = "hold"``. The HOLD
    branch fires when attestation_present=True but the FINAL
    AIMessage is NOT a standalone text report (either the
    attest-call message itself with no subsequent text, or the
    bundled c5d9a38a shape). The branch injects a checkpoint-
    durable Final Report Reminder (counter-INDEPENDENT, capped at
    :data:`daemon.graph.ATTESTATION_REMINDER_CAP` per mission) and
    routes back to ``agent``. Completion (meta_bypass allow) fires
    ONLY when attestation_present AND the final AIMessage is a
    standalone text report. The 5-value invariant retired.
    """

    def test_exactly_six_values(self):
        assert {d.value for d in Decision} == {
            "allowed",
            "denied",
            "hold",
            "terminal_after_bound",
            "dry_log",
            "allowed_legitimate_pending_wakeup",
        }

    def test_nudge_only_on_denied(self):
        for value in Decision:
            result = GateDecisionFactory(value)
            expected = value is Decision.DENIED
            assert result.should_inject_nudge is expected

    def test_reminder_only_on_hold(self):
        """2026-09-19 — the reminder guard mirrors the nudge guard:
        ``should_inject_reminder`` is True ONLY for ``Decision.HOLD``.
        Every other enum value (allowed / denied / terminal_after_bound
        / dry_log / allowed_legitimate_pending_wakeup) is structurally
        forbidden from injecting the Final Report Reminder. The
        reminder path is counter-INDEPENDENT — no bound/escalation
        interaction — and the structural exclusivity to HOLD is the
        invariant that keeps the new branch from leaking into the
        existing deny / allow / bound machinery."""
        for value in Decision:
            result = GateDecisionFactory(value)
            expected = value is Decision.HOLD
            assert result.should_inject_reminder is expected


def GateDecisionFactory(value: Decision):
    """Build a GateDecision carrying ``value`` with the nudge + reminder guards
    applied by construction (mirrors decide()'s invariant for every enum
    member). 2026-09-19: ``should_inject_reminder`` joins the guard set;
    the reminder path is structurally exclusive to ``Decision.HOLD``."""
    from daemon.services.attestation_gate import GateDecision

    return GateDecision(
        decision=value,
        next_denied_count=0,
        should_inject_nudge=(value is Decision.DENIED),
        should_inject_reminder=(value is Decision.HOLD),
    )


# =============================================================================
# Gate config — O8 unit assertion
# =============================================================================


class TestGateConfigO8:
    def test_no_checkpoint_ns_in_config(self):
        cfg = build_gate_config("inst-1", DEFAULT_GATE_SETTINGS)
        assert "checkpoint_ns" not in cfg
        assert all(isinstance(k, str) for k in cfg)

    def test_config_keys_are_canonical(self):
        cfg = build_gate_config("inst-1", GateSettings("enforce", 3, 3))
        assert cfg["instance_id"] == "inst-1"
        assert cfg["window"] == 3
        assert cfg["deny_bound"] == 3
        assert cfg["mode"] == "enforce"
        assert cfg["gate_location"] == "graph_end_candidate"

    def test_node_factory_attaches_clean_config(self):
        # The graph-side node factory attaches the config it will run
        # with — assert THAT object too carries no checkpoint_ns.
        from daemon.graph import create_attestation_gate_node

        cfg = build_gate_config("inst-1", GateSettings("enforce", 3, 3))
        manager = make_manager()
        node = create_attestation_gate_node(cfg, GateSettings("enforce", 3, 3), manager, "inst-1")
        attached = getattr(node, "attestation_config")
        assert "checkpoint_ns" not in attached
        assert attached["mode"] == "enforce"


# =============================================================================
# evaluate() — composition with the manager facades
# =============================================================================


class TestEvaluateComposition:
    def test_attested_allow_via_facades(self, caplog):
        manager = make_manager(pending_children=0, wakeups=0)
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)
        with caplog.at_level(logging.INFO, logger="daemon.services.attestation_gate"):
            result = evaluate(
                "inst-1", 0, attest_messages(), settings, manager,
            )
        assert result.decision is Decision.ALLOWED
        assert result.attestation_present is True
        assert result.next_denied_count == 0
        manager.count_pending_children.assert_called_once_with("inst-1")
        manager.get_queued_or_expected_wakeups.assert_called_once_with("inst-1")
        # canonical log entry emitted with the schema keys
        assert any("event=leader_completion_gate" in r.message for r in caplog.records)

    def test_deny_reads_facades_and_logs_inputs(self, caplog):
        manager = make_manager(pending_children=0, wakeups=0)
        with caplog.at_level(logging.INFO, logger="daemon.services.attestation_gate"):
            result = evaluate(
                "inst-1", 0, plain_messages(), DEFAULT_GATE_SETTINGS, manager,
            )
        # DEFAULT_GATE_SETTINGS.mode is now "enforce" (operator override
        # 2026-09-06; ship default flipped from "dry") → R2-deny predicate
        # satisfied (pending_children=0, wakeups=0, no attestation) →
        # DENIED + nudge injected (passive observer is no longer the default).
        assert result.decision is Decision.DENIED
        assert result.messages_scanned > 0
        assert result.should_inject_nudge is True
        assert "event=leader_completion_gate" in caplog.text
        assert "decision=denied" in caplog.text
        assert "pending_children=0" in caplog.text
        assert "queued_or_expected_wakeups=0" in caplog.text
        assert "messages_scanned=" in caplog.text
        assert "scanned_window_size=3" in caplog.text

    def test_r2_inputs_surface_in_result(self):
        manager = make_manager(pending_children=3, wakeups=1)
        result = evaluate(
            "inst-1", 0, plain_messages(), DEFAULT_GATE_SETTINGS, manager,
        )
        assert result.pending_children == 3
        assert result.queued_or_expected_wakeups == 1
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP  # R2-allow (DEFAULT=enforce; pending=3, wakeups=1)

    def test_stale_outside_window_attestation_still_denies(self, caplog):
        """Stage 3 (R1) re-contract: the outside-window diagnostic
        FIELD is retired (log-only, no decision weight); the behavior
        it observed — a STALE attestation aged out of the window does
        NOT satisfy the gate — is pinned here on the decision itself."""
        import logging as _logging

        messages = [
            HumanMessage(content="go"),
            ai("dispatching child", tool_calls=[
                {"name": "send_message", "args": {"target": "child"}, "id": "dispatch"}
            ]),
            ai("stale", tool_calls=[{"name": DEFAULT_ATTESTATION_TOOL_NAME, "args": {}, "id": "old"}]),
        ]
        # 4 newer AIMessages push the attestation OUTSIDE the N=3 window.
        for i in range(4):
            messages.append(ai(f"filler {i}"))
        manager = make_manager()
        with caplog.at_level(_logging.INFO, logger="daemon.services.attestation_gate"):
            result = evaluate(
                "inst-1", 0, messages, DEFAULT_GATE_SETTINGS, manager,
            )
        assert result.attestation_present is False
        # DEFAULT=enforce (operator override 2026-09-06); no in-window
        # attestation + R2-deny predicate satisfied (0/0) → DENIED + nudge.
        assert result.decision is Decision.DENIED
        # The retired diagnostic key is absent from the canonical row.
        log_line = next(
            record.message
            for record in caplog.records
            if "event=leader_completion_gate" in record.message
        )
        assert "attest_seen_outside_window=" not in log_line

    def test_enforce_deny_full_flow(self):
        manager = make_manager(0, 0)
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)
        result = evaluate("inst-1", 0, plain_messages(), settings, manager)
        assert result.decision is Decision.DENIED
        assert result.next_denied_count == 1
        assert result.should_inject_nudge is True

    def test_none_manager_fails_open(self, caplog):
        with caplog.at_level(logging.WARNING, logger="daemon.services.attestation_gate"):
            result = evaluate(
                "inst-1", 0, plain_messages(), DEFAULT_GATE_SETTINGS, None,
            )
        assert result.decision is Decision.ALLOWED
        assert "ManagerUnavailable" in caplog.text


class TestEvaluateFailOpen:
    def test_db_seam_failure_fails_open_with_db_error_event(self, caplog):
        manager = make_manager()
        manager.count_pending_children.side_effect = RuntimeError("db down")
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)
        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_gate"):
            result = evaluate("inst-1", 2, plain_messages(), settings, manager)
        assert result.decision is Decision.ALLOWED
        assert result.should_inject_nudge is False
        # unknown R2 inputs are surfaced as -1 (read failed), never as 0
        assert result.pending_children == -1
        assert result.queued_or_expected_wakeups == -1
        assert "event=leader_completion_gate_db_error" in caplog.text
        assert "error_class=RuntimeError" in caplog.text
        # counter untouched on the fail-open path
        assert result.next_denied_count == 2

    def test_second_facade_failure_also_fails_open(self, caplog):
        manager = make_manager()
        manager.get_queued_or_expected_wakeups.side_effect = RuntimeError("boom")
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)
        result = evaluate("inst-1", 0, plain_messages(), settings, manager)
        assert result.decision is Decision.ALLOWED
        assert "leader_completion_gate_db_error" in caplog.text

    def test_scanner_exception_fails_open_with_error_event(self, caplog, monkeypatch):
        manager = make_manager(0, 0)
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)

        def boom(*args, **kwargs):
            raise ValueError("scanner exploded")

        monkeypatch.setattr(gate_module, "scan_for_attestation_detailed", boom)
        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_gate"):
            result = evaluate("inst-1", 1, plain_messages(), settings, manager)
        assert result.decision is Decision.ALLOWED
        assert "event=leader_completion_gate_error" in caplog.text
        assert "error_class=ValueError" in caplog.text


# =============================================================================
# Resolver — fail-open on invalid env (leader ruling 4)
# =============================================================================


class TestResolverFailOpen:
    def setup_method(self):
        gate_module._reset_gate_settings_for_tests()

    def teardown_method(self):
        gate_module._reset_gate_settings_for_tests()

    def test_defaults_when_unset(self):
        """env unset → resolved mode is ``DEFAULT_MODE`` (currently
        ``"enforce"`` per operator override 2026-09-06)."""
        settings = resolve_gate_settings()
        assert settings == GateSettings(mode="enforce", window=3, deny_bound=3)

    def test_valid_env_parsed(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_WINDOW", "2")
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND", "5")
        settings = resolve_gate_settings()
        assert settings.mode == "enforce"
        assert settings.window == 2
        assert settings.deny_bound == 5

    def test_invalid_mode_fails_open_to_enforce_with_warn(self, caplog, monkeypatch):
        """Invalid/typo'd mode fails OPEN to ``DEFAULT_MODE``
        (currently ``"enforce"`` per operator override 2026-09-06)."""
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforse")
        with caplog.at_level(logging.WARNING, logger="daemon.services.attestation_gate"):
            settings = resolve_gate_settings()
        assert settings.mode == "enforce"
        assert "not a recognized mode" in caplog.text

    def test_invalid_window_fails_open_to_default(self, caplog, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_WINDOW", "three")
        with caplog.at_level(logging.WARNING, logger="daemon.services.attestation_gate"):
            settings = resolve_gate_settings()
        assert settings.window == 3
        assert "not a valid integer" in caplog.text

    def test_invalid_bound_fails_open_to_default(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND", "0")
        settings = resolve_gate_settings()
        assert settings.deny_bound == 3

    def test_cached_across_calls(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
        first = resolve_gate_settings()
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "off")
        second = resolve_gate_settings()
        assert first is second  # restart-read semantics: cache wins


# =============================================================================
# Scanner re-export sanity (single tool-name source)
# =============================================================================


def test_default_tool_name_matches_scanner():
    assert gate_module.DEFAULT_ATTESTATION_TOOL_NAME == (
        __import__(
            "daemon.services.attestation_scanner",
            fromlist=["DEFAULT_ATTESTATION_TOOL_NAME"],
        ).DEFAULT_ATTESTATION_TOOL_NAME
    )


# =============================================================================
# Review fix 4b — the gate-exception marker persists in DRY mode (pinned)
# =============================================================================


class TestGateExceptionMarkerDry:
    """Pins the dry-mode marker choice made in ``daemon/graph.py``.

    The marker records an operational FAULT (the gate failed open), not
    a decision side effect: dry's zero-side-effects contract covers
    decision OUTPUTS (nudge / counter / escalation / terminal), not
    failure diagnostics. This test locks that choice — if someone gates
    the marker on enforce, this fails and the docstring in
    ``_persist_gate_exception_marker`` must change with it.
    """

    def test_dry_mode_facade_failure_still_persists_marker(self):
        from daemon.graph import create_attestation_gate_node

        manager = make_manager()
        manager.count_pending_children.side_effect = RuntimeError("db down")
        ledger = MagicMock()

        settings = GateSettings(mode="dry", window=3, deny_bound=3)
        config = build_gate_config("inst-dry-marker", settings)
        node = create_attestation_gate_node(
            config,
            settings,
            manager,
            "inst-dry-marker",
            denied_count_getter=lambda: 0,
            ledger=ledger,
        )

        result = asyncio.run(
            node(
                {"messages": plain_messages()},
                config={"configurable": {"thread_id": "inst-dry-marker"}},
            )
        )

        # fail-open shape: allow-END with the transient marker raised
        assert result["gate_exception_seen"] is True
        assert result["attestation_route"] is None
        # THE pinned choice: the marker write happens EVEN IN DRY.
        ledger.set_metadata.assert_called_once_with(
            "inst-dry-marker",
            "attestation_gate_exception_seen",
            True,
        )
        # ...and nothing else was written (dry has no ledger side effects
        # beyond the fault marker).
        ledger.increment.assert_not_called()
        ledger.reset.assert_not_called()
        ledger.set_escalated_and_reset.assert_not_called()


# =============================================================================
# KB-trap pin — incident 98b59dd7 follow-up (brief §4)
#
# On Decision.DENIED rows the marker/length scanner NEVER runs (the
# scan is gated on ``result.decision in (ALLOWED,
# ALLOWED_LEGITIMATE_PENDING_WAKEUP, DRY_LOG) AND not
# attestation_present`` — see ``daemon/services/attestation_gate.py:
# 1009-1017``). The ``marker_hit`` / ``length_trigger`` /
# ``final_word_count`` fields on :class:`GateDecision` therefore
# stay at their dataclass defaults (False / False / 0) on a DENIED
# row. Operators / future readers MUST NOT interpret those fields as
# measurements of the final AIMessage content on a DENIED row —
# they are noise on the deny path.
# =============================================================================


class TestDeniedRowsHaveRealSignalFields:
    """Pin the marker/length fields on DENIED rows.

    7d4a3bd9 FIX-5a re-contract (2026-09-26, supersedes the former
    "defaults on denied rows" pin): the LENGTH scan now runs on EVERY
    evaluated path, so ``length_trigger`` / ``final_word_count`` on a
    DENIED row are REAL measurements of the final AIMessage — the
    former dataclass-default pins encoded the exact defect that made
    Episode A of leader 7d4a3bd9 log ``final_word_count=0
    length_trigger=False`` against a real 2313-char final AIMessage
    (a non-delegated allow printed defaults, forensics misread them
    as scanner zeros). The MARKER scan stays routing-gated: on DENIED
    rows ``marker_hit`` is still the dataclass default (the deny path
    consumes ``should_inject_nudge`` + the ledger writes; the deny
    band resolves on the C term alone and leader-prose markers are
    moot there — the marker field on a DENIED row remains
    "scanner did not run", not a measurement).
    """

    def test_denied_rows_have_marker_hit_default_false(self):
        manager = make_manager(pending_children=0, wakeups=0)
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)
        result = evaluate(
            "inst-denied-marker", 0, plain_messages(), settings, manager,
        )
        # DENIED is the canonical deny path — the MARKER scan still
        # does not run (routing-gated; the deny band needs no prose
        # signal).
        assert result.decision is Decision.DENIED
        assert result.marker_hit is False, (
            "marker_hit on a DENIED row MUST be the dataclass default "
            "(False) — the marker scan does not run on the deny path. "
            "Reading marker_hit=True on a DENIED row as 'the marker "
            "saw a marker on the final message' would be wrong."
        )

    def test_denied_rows_carry_real_length_trigger(self):
        # 7d4a3bd9 FIX-5a re-contract: REAL measurement, not the
        # default. ``plain_messages()`` ends with the 3-word final
        # AIMessage "Everything is complete." → brevity-class
        # (3 < SHORT_REPORT_WORD_THRESHOLD=150) → length_trigger True.
        manager = make_manager(pending_children=0, wakeups=0)
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)
        result = evaluate(
            "inst-denied-length", 0, plain_messages(), settings, manager,
        )
        assert result.decision is Decision.DENIED
        assert result.length_trigger is True, (
            "length_trigger on a DENIED row is now a REAL measurement "
            "of the final AIMessage (7d4a3bd9 FIX-5a) — the 3-word "
            "final in plain_messages() is brevity-class, so True is "
            "the honest value. False here would mean the length scan "
            "stopped running on deny paths (the Episode-A defect "
            "shape)."
        )

    def test_denied_rows_carry_real_final_word_count(self):
        # 7d4a3bd9 FIX-5a re-contract: final_word_count reflects the
        # actual final AIMessage text ("Everything is complete." → 3
        # words) on the DENIED path too.
        manager = make_manager(pending_children=0, wakeups=0)
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)
        result = evaluate(
            "inst-denied-wordcount", 0, plain_messages(), settings, manager,
        )
        assert result.decision is Decision.DENIED
        assert result.final_word_count == 3, (
            "final_word_count on a DENIED row is now the REAL word "
            "count of the final AIMessage (7d4a3bd9 FIX-5a). A 0 here "
            "would mean the default-zeros defect (Episode-A shape) "
            "regressed back."
        )

    def test_denied_rows_have_no_marker_path_field(self):
        """Stage 3 (R6/R7) re-contract: the marker route enum
        (``marker_path`` / a-b-c-d) retired with the legacy judge
        plumbing — the field no longer EXISTS on the decision."""
        import dataclasses as _dc

        manager = make_manager(pending_children=0, wakeups=0)
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)
        result = evaluate(
            "inst-denied-path", 0, plain_messages(), settings, manager,
        )
        assert result.decision is Decision.DENIED
        field_names = {f.name for f in _dc.fields(result)}
        assert "marker_path" not in field_names
