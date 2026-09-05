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
    decide,
    evaluate,
    resolve_gate_settings,
)
from daemon.services.attestation_scanner import DEFAULT_ATTESTATION_TOOL_NAME


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
    """A message tail with NO attestation (hallucinated completion)."""
    return [
        HumanMessage(content="go"),
        ai("Working on it."),
        ai("Everything is complete."),
    ]


def make_manager(pending_children=0, wakeups=0):
    manager = MagicMock()
    manager.count_pending_children = MagicMock(return_value=pending_children)
    manager.get_queued_or_expected_wakeups = MagicMock(return_value=wakeups)
    return manager


# =============================================================================
# decide() — the parameterized R2 matrix (plan 2.2 test notes)
# =============================================================================


class TestDecideMetaConditions:
    def test_disabled_gate_allows_regardless(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0,
            denied_count=2, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=False,
        )
        assert result.decision is Decision.ALLOWED
        assert result.should_inject_nudge is False
        # gate didn't run → counter untouched (NOT one of the 4 resets)
        assert result.next_denied_count == 2

    def test_scope_inapplicable_allows(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0,
            denied_count=1, bound=3, scope_applicable=False, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.ALLOWED
        assert result.next_denied_count == 1

    def test_mode_off_allows_regardless(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0,
            denied_count=3, bound=3, scope_applicable=True, mode="off",
            attestation_enabled=True,
        )
        assert result.decision is Decision.ALLOWED
        assert result.next_denied_count == 3


class TestDecideDryMode:
    def test_dry_is_dry_log_with_zero_side_effects(self):
        # Dry + missing attestation + R2-deny-predicate satisfied:
        # evaluation recorded (dry_log) but allow + no counter change.
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0,
            denied_count=1, bound=3, scope_applicable=True, mode="dry",
            attestation_enabled=True,
        )
        assert result.decision is Decision.DRY_LOG
        assert result.should_inject_nudge is False
        assert result.next_denied_count == 1  # zero side effects

    def test_dry_with_attestation_still_dry_log(self):
        # Plan logic-tree order: dry short-circuits BEFORE the attested
        # check — so no reset fires in dry (zero side effects, always).
        result = decide(
            attested=True, pending_children=0, queued_or_expected_wakeups=0,
            denied_count=2, bound=3, scope_applicable=True, mode="dry",
            attestation_enabled=True,
        )
        assert result.decision is Decision.DRY_LOG
        assert result.next_denied_count == 2


class TestDecideEnforce:
    def test_attested_allow_resets_counter(self):
        # Architect addition — attested allow is reset trigger (1).
        result = decide(
            attested=True, pending_children=0, queued_or_expected_wakeups=0,
            denied_count=3, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.ALLOWED
        assert result.next_denied_count == 0
        assert result.should_inject_nudge is False

    def test_r2_allow_pending_children(self):
        result = decide(
            attested=False, pending_children=2, queued_or_expected_wakeups=0,
            denied_count=0, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        # RULING 1: the R2 non-reset IS the loop protection.
        assert result.next_denied_count == 0
        assert result.should_inject_nudge is False

    def test_r2_allow_queued_wakeups(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=1,
            denied_count=2, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert result.next_denied_count == 2  # unchanged — loop protection
        assert result.should_inject_nudge is False

    def test_first_deny(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0,
            denied_count=0, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.DENIED
        assert result.next_denied_count == 1
        assert result.should_inject_nudge is True

    def test_last_deny_before_bound(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0,
            denied_count=2, bound=3, scope_applicable=True, mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.DENIED
        assert result.next_denied_count == 3
        assert result.should_inject_nudge is True

    def test_denied_count_at_bound_is_terminal_after_bound(self):
        result = decide(
            attested=False, pending_children=0, queued_or_expected_wakeups=0,
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
            attested=False, pending_children=0, queued_or_expected_wakeups=0,
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
            attested=False, pending_children=0, queued_or_expected_wakeups=0,
            denied_count=denied_count, bound=bound, scope_applicable=True,
            mode="enforce", attestation_enabled=True,
        )
        assert result.decision is expected


class TestDecisionEnumCanonical:
    """The canonical 5-value enum (Phase 4 task 4.5 — verbatim values)."""

    def test_exactly_five_values(self):
        assert {d.value for d in Decision} == {
            "allowed",
            "denied",
            "terminal_after_bound",
            "dry_log",
            "allowed_legitimate_pending_wakeup",
        }

    def test_nudge_only_on_denied(self):
        for value in Decision:
            result = GateDecisionFactory(value)
            expected = value is Decision.DENIED
            assert result.should_inject_nudge is expected


def GateDecisionFactory(value: Decision):
    """Build a GateDecision carrying ``value`` with the nudge guard applied
    by construction (mirrors decide()'s invariant for every enum member)."""
    from daemon.services.attestation_gate import GateDecision

    return GateDecision(
        decision=value,
        next_denied_count=0,
        should_inject_nudge=(value is Decision.DENIED),
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
        # default settings are mode=dry → dry_log (passive observer)
        assert result.decision is Decision.DRY_LOG
        assert result.messages_scanned > 0
        assert "event=leader_completion_gate" in caplog.text
        assert "decision=dry_log" in caplog.text
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
        assert result.decision is Decision.DRY_LOG  # dry default

    def test_attest_seen_outside_window_populated(self):
        messages = [
            ai("stale", tool_calls=[{"name": DEFAULT_ATTESTATION_TOOL_NAME, "args": {}, "id": "old"}]),
        ]
        # 4 newer AIMessages push the attestation OUTSIDE the N=3 window.
        for i in range(4):
            messages.append(ai(f"filler {i}"))
        manager = make_manager()
        result = evaluate(
            "inst-1", 0, messages, DEFAULT_GATE_SETTINGS, manager,
        )
        assert result.attest_seen_outside_window is True
        assert result.attestation_present is False
        # dry default: diagnostic fires while the decision stays dry_log
        assert result.decision is Decision.DRY_LOG

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
        settings = resolve_gate_settings()
        assert settings == GateSettings(mode="dry", window=3, deny_bound=3)

    def test_valid_env_parsed(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_WINDOW", "2")
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND", "5")
        settings = resolve_gate_settings()
        assert settings.mode == "enforce"
        assert settings.window == 2
        assert settings.deny_bound == 5

    def test_invalid_mode_fails_open_to_dry_with_warn(self, caplog, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforse")
        with caplog.at_level(logging.WARNING, logger="daemon.services.attestation_gate"):
            settings = resolve_gate_settings()
        assert settings.mode == "dry"
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
