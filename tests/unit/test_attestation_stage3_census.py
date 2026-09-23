"""Stage-3 retirement census — negative pins over the RETIRED surfaces.

LCA resolver Stage 3 (2026-09-17, resolver-unification §7 R1–R8 +
user-approved Appendix A): the two legacy judge call sites, the flip
constant, the marker-path route plumbing, the R1 log-only diagnostic
field, the R2 gate-config threading, the R3 decide()-level dry
branch, the R4 delegation arm, and the R5 busy-suppression block are
DELETED. This census follows the project's WC_WAKE_ENQUEUE-removal
negative-pin style: any resurrection of a retired symbol/site is
LOUD (a failed assert), not silent.

Each pin greps the PRODUCTION source (daemon/) — historical mentions
in decisions.md / docs / this file's own docstrings are out of scope
by design (decision-record mentions are fine; live claims are not).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import daemon.graph as graph_mod
import daemon.services.attestation_gate as gate_mod
import daemon.services.attestation_report_judge as judge_mod
import daemon.services.attestation_scanner as scanner_mod
from daemon.services.attestation_gate import (
    CANONICAL_LOG_SCHEMA_FIELDS,
    GATE_CONFIG_KEYS,
    decide,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DAEMON_DIR = _REPO_ROOT / "daemon"


def _daemon_sources() -> dict[str, str]:
    """Read the daemon Python sources the census sweeps.

    Roots: ``daemon/`` top level plus the ``services/``, ``tools/``,
    ``routers/``, ``repositories/``, ``clients/`` and ``sources/``
    subpackages (one glob level each — the daemon tree's layout).
    """
    sources: dict[str, str] = {}
    for pattern in (
        "*.py",
        "services/*.py",
        "tools/*.py",
        "routers/*.py",
        "repositories/*.py",
        "clients/*.py",
        "sources/*.py",
    ):
        for path in _DAEMON_DIR.glob(pattern):
            if path.is_file():
                sources[str(path)] = path.read_text(encoding="utf-8")
    return sources


def _assert_symbol_absent(symbol: str) -> None:
    """The symbol appears in NO daemon source file's CODE."""
    hits: list[str] = []
    for path, src in _daemon_sources().items():
        if symbol in src:
            hits.append(path)
    assert not hits, f"retired symbol {symbol!r} resurrected in: {hits}"


# ─────────────────────────────────────────────────────────────────────────────
# R7 — the flip constant + both legacy judge call sites
# ─────────────────────────────────────────────────────────────────────────────


class TestR7CensusFlipConstantDeleted:
    def test_flip_constant_absent_from_daemon(self) -> None:
        _assert_symbol_absent("_LCA_STAGE2_RESOLVER_FLIP")

    def test_gate_node_source_has_single_unconditional_fused_guard(self) -> None:
        src = inspect.getsource(graph_mod.create_attestation_gate_node)
        assert "if resolver_snapshot is not None:" in src
        # The fused block is NOT conditionally guarded by any module
        # constant — exactly one completion path.
        assert "RESOLVER_FLIP" not in src


class TestR7CensusLegacyJudgeDeleted:
    def test_legacy_judge_entry_points_absent(self) -> None:
        assert not hasattr(judge_mod, "judge_completion_report_async")
        assert not hasattr(judge_mod, "judge_completion_report_sync")
        assert not hasattr(judge_mod, "JudgeResult")
        _assert_symbol_absent("judge_completion_report_async")
        _assert_symbol_absent("judge_completion_report_sync")

    def test_gate_node_never_references_legacy_judge(self) -> None:
        src = inspect.getsource(graph_mod.create_attestation_gate_node)
        assert "judge_completion_report" not in src
        # Exactly ONE judge INVOCATION remains: the fused judge await.
        assert src.count("await judge_fused_bundle_async(") == 1

    def test_legacy_judge_only_helpers_absent(self) -> None:
        for helper in (
            "_slice_judge_window",
            "_format_window_for_judge",
            "_parse_judge_response",
            "JUDGE_SYSTEM_PROMPT",
            "JUDGE_MAX_INPUT_CHARS",
            "JUDGE_MAX_OUTPUT_CHARS",
            "JUDGE_MAX_WINDOW",
            "JUDGE_DEFAULT_WINDOW",
        ):
            assert not hasattr(judge_mod, helper), helper


# ─────────────────────────────────────────────────────────────────────────────
# R8 — the legacy judge log-event family
# ─────────────────────────────────────────────────────────────────────────────


class TestR8CensusLegacyJudgeEventNamesDeleted:
    def test_marker_judge_event_family_absent_from_daemon(self) -> None:
        _assert_symbol_absent("leader_completion_gate_marker_judge")

    def test_bare_would_be_deny_judge_event_absent_from_daemon(self) -> None:
        # The legacy would-be-deny judge VERDICT row. Precise
        # trailing-space pattern — the bare token is banned in every
        # shape; the _error sibling is pinned separately below.
        _assert_symbol_absent("event=leader_completion_gate_judge ")
        _assert_symbol_absent('event=leader_completion_gate_judge"')
        _assert_symbol_absent("event=leader_completion_gate_judge'")

    def test_bare_would_be_deny_judge_error_event_absent_from_daemon(self) -> None:
        # The legacy would-be-deny judge ERROR row — a distinct event
        # name the trailing-space verdict-row pattern would miss
        # (adversarial-review precision fix 2026-09-17).
        _assert_symbol_absent("event=leader_completion_gate_judge_error")

    def test_single_fused_event_family_present(self) -> None:
        src = inspect.getsource(graph_mod.create_attestation_gate_node)
        for event in (
            "leader_completion_gate_fused_judge ",
            "leader_completion_gate_fused_judge_disabled ",
            "leader_completion_gate_fused_judge_error ",
        ):
            assert event in src, event


# ─────────────────────────────────────────────────────────────────────────────
# R1 — the outside-window diagnostic field
# ─────────────────────────────────────────────────────────────────────────────


class TestR1CensusOutsideWindowFieldDeleted:
    def test_scanner_function_absent(self) -> None:
        assert not hasattr(scanner_mod, "attestation_seen_outside_window")
        _assert_symbol_absent("attestation_seen_outside_window")

    def test_gate_field_and_log_key_absent(self) -> None:
        _assert_symbol_absent("attest_seen_outside_window")
        assert "attest_seen_outside_window" not in CANONICAL_LOG_SCHEMA_FIELDS
        # 17 canonical fields post-retirement (was 18).
        assert len(CANONICAL_LOG_SCHEMA_FIELDS) == 17


# ─────────────────────────────────────────────────────────────────────────────
# R2 — the gate-config meta-flag threading
# ─────────────────────────────────────────────────────────────────────────────


class TestR2CensusGateConfigThreadingDeleted:
    def test_gate_config_keys_do_not_carry_meta_flags(self) -> None:
        assert "attestation_enabled" not in GATE_CONFIG_KEYS
        assert "scope_applicable" not in GATE_CONFIG_KEYS

    def test_gate_node_does_not_thread_meta_flags(self) -> None:
        src = inspect.getsource(graph_mod.create_attestation_gate_node)
        assert 'gate_config.get("attestation_enabled"' not in src
        assert 'gate_config.get("scope_applicable"' not in src

    def test_decide_signature_has_no_meta_params(self) -> None:
        params = inspect.signature(decide).parameters
        for retired in ("scope_applicable", "mode", "attestation_enabled"):
            assert retired not in params, retired
        for kept in (
            "attested",
            "pending_children",
            "queued_or_expected_wakeups",
            "live_descendants",
            "denied_count",
            "bound",
            "attestation_required",
            "user_answer_pending",
        ):
            assert kept in params, kept


# ─────────────────────────────────────────────────────────────────────────────
# R3 — the decide()-level dry branch
# ─────────────────────────────────────────────────────────────────────────────


class TestR3CensusDecideDryBranchDeleted:
    def test_decide_source_has_no_mode_branches(self) -> None:
        src = inspect.getsource(decide)
        assert '"dry"' not in src
        assert '"off"' not in src
        assert '"enforce"' not in src
        # The dry mapping lives in evaluate()'s mode layer.
        eval_src = inspect.getsource(gate_mod.evaluate)
        assert 'Decision.DRY_LOG' in eval_src


# ─────────────────────────────────────────────────────────────────────────────
# R4 — the decide()-level delegation arm
# ─────────────────────────────────────────────────────────────────────────────


class TestR4CensusDelegationArmDeleted:
    def test_decide_source_has_no_delegation_arm(self) -> None:
        src = inspect.getsource(decide)
        assert "not attestation_required" not in src.replace(
            "attestation_required=attestation_required", ""
        )

    def test_evaluate_composition_mirrors_term1(self) -> None:
        # The composition layer owns the meta-bypass mirror (the
        # predicate's Term 1 lives in attestation_resolver_activation).
        eval_src = inspect.getsource(gate_mod.evaluate)
        assert "elif not attestation_required:" in eval_src
        # D10 mirror: the Source-B scan block is gated on the
        # delegation flag — suspicion signals are not even evaluated
        # on non-delegated missions.
        assert "and result.attestation_required" in eval_src


# ─────────────────────────────────────────────────────────────────────────────
# R5/R6 — the trigger plumbing + busy-suppression block
# ─────────────────────────────────────────────────────────────────────────────


class TestR5R6CensusTriggerPlumbingDeleted:
    def test_retired_fields_absent_from_gate_decision(self) -> None:
        import dataclasses

        fields = {f.name for f in dataclasses.fields(gate_mod.GateDecision)}
        for retired in (
            "marker_path",
            "marker_judge_verdict",
            "marker_judge_latency_ms",
            "marker_judge_error_class",
            "trigger_source",
            "trigger_suppressed_by",
        ):
            assert retired not in fields, retired
        for kept in (
            "marker_hit",
            "marker_terms",
            "length_trigger",
            "final_word_count",
            "busy_descendants",
        ):
            assert kept in fields, kept
        # 2026-09-23 (b2f4dae9): marker_hint_message RETIRED along with
        # the entire (b)/(d)-with-pending hint injection surface. The
        # field is gone from GateDecision; the (b) route resolves to
        # allow log-only on the resolver row.
        assert "marker_hint_message" not in fields, (
            "marker_hint_message MUST stay retired (incident b2f4dae9); "
            "if this assertion fails, the field was re-introduced "
            "without re-anchoring the contract — see decisions.md "
            "D-entry 2026-09-23"
        )

    def test_marker_path_constants_absent(self) -> None:
        for const in (
            "MARKER_PATH_NONE",
            "MARKER_PATH_A",
            "MARKER_PATH_B",
            "MARKER_PATH_C",
            "MARKER_PATH_D",
        ):
            assert not hasattr(gate_mod, const), const
        _assert_symbol_absent("MARKER_PATH_")

    def test_scanner_catalog_survives(self) -> None:
        # R6 KEEPS the scanners + the 16-pattern catalog + the length
        # threshold as activation-signal producers.
        from daemon.services.attestation_marker_scanner import (  # noqa: F401
            MID_WORK_MARKERS,
            SHORT_REPORT_WORD_THRESHOLD,
            scan_for_mid_work_markers,
            scan_for_short_final_ai,
        )

        assert 12 <= len(MID_WORK_MARKERS) <= 18
        assert SHORT_REPORT_WORD_THRESHOLD == 150

    def test_noqa_ble001_comments_justified_in_fused_region(self) -> None:
        # Ledger item (e): every BLE001 suppression in the fused
        # region carries an explanatory note (an em-dash suffix).
        src = inspect.getsource(graph_mod.create_attestation_gate_node)
        fused = src[src.index("if resolver_snapshot is not None:"):]
        for line in fused.splitlines():
            if "noqa: BLE001" in line:
                assert "# noqa: BLE001 — " in line, (
                    f"unjustified BLE001 suppression: {line.strip()!r}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# Ledger (c) — the child-report marker-write seam (RETIRED D-CTD-7)
# ─────────────────────────────────────────────────────────────────────────────
#
# 2026-09-18 (D-CTD-7, user decision): the LCA ``Child Report Check``
# advisory note mint was removed entirely. The two pin tests that
# survived the Stage-3 retirement of the mint block
# (``test_scanner_import_hoisted_out_of_hot_path`` /
# ``test_marker_write_catch_splits_import_error``) are now obsolete:
# the scanner is no longer imported into ``child_reports`` (no callers
# after the mint deletion), and the SAVEPOINT-catch-import-error seam
# does not exist (the mint block is gone). The catalog lives in
# ``daemon/services/attestation_marker_scanner.py`` and is pinned
# positively by ``tests/unit/test_attestation_marker_scanner.py`` +
# ``tests/unit/test_child_terminal_contradiction.py::TestSourcePins
# ::test_catalog_lives_in_marker_scanner``; the note-mint removal is
# pinned negatively by ``TestSourcePins
# ::test_note_mint_site_is_gone_from_child_reports``. The ledger (c)
# row in the Stage-3 retirement record references D-CTD-7.

# (Ledger (c) tests removed 2026-09-18 — see comment above.)
