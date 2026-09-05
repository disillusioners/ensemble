"""Integration test — docs/setup.md runbook drift (Phase 4 task 4.6).

The operator runbook in ``docs/setup.md`` documents the three env vars
(``ENSEMBLE_LEADER_ATTESTATION_MODE``, ``ENSEMBLE_LEADER_ATTESTATION_WINDOW``,
``ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND``) and the dry→enforce
promotion SOP. This test pins the env-var names, default values, and
the canonical boot-log prefix in the doc against the canonical
resolver module so an accidental rename or default-flip in either
place fails CI before it ships.

The test is INTENTIONALLY lightweight (text-level regex) — the runbook
is human-written prose; the resolver module is the canonical code. A
drift is a sign one was edited without the other.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from daemon.services.attestation_resolver import (
    DEFAULT_DENY_BOUND,
    DEFAULT_MODE,
    DEFAULT_WINDOW,
    ENSEMBLE_ATTESTATION_DENY_BOUND_ENV,
    ENSEMBLE_ATTESTATION_MODE_ENV,
    ENSEMBLE_ATTESTATION_WINDOW_ENV,
    METRIC_DRY_LOG_DENY_PREDICATE_TOTAL,
    METRIC_DRY_LOG_TOTAL,
    METRIC_ENFORCE_DENIED_TOTAL,
)


# Repo root — tests/integration → ../.. → repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_MD = REPO_ROOT / "docs" / "setup.md"


# =============================================================================
# Doc presence — the runbook + env table exist
# =============================================================================


class TestRunbookPresence:
    @pytest.fixture(autouse=True)
    def _doc(self):
        assert SETUP_MD.is_file(), f"missing runbook doc: {SETUP_MD}"
        self.text = SETUP_MD.read_text(encoding="utf-8")
        return self.text


# =============================================================================
# Env-var name drift — names match between doc and resolver
# =============================================================================


class TestEnvVarNameDrift:
    @pytest.fixture(autouse=True)
    def _doc(self):
        assert SETUP_MD.is_file(), f"missing runbook doc: {SETUP_MD}"
        self.text = SETUP_MD.read_text(encoding="utf-8")
        return self.text

    def test_attestation_mode_env_named(self):
        assert ENSEMBLE_ATTESTATION_MODE_ENV in self.text, (
            f"runbook missing env var {ENSEMBLE_ATTESTATION_MODE_ENV!r}"
        )

    def test_attestation_window_env_named(self):
        assert ENSEMBLE_ATTESTATION_WINDOW_ENV in self.text, (
            f"runbook missing env var {ENSEMBLE_ATTESTATION_WINDOW_ENV!r}"
        )

    def test_attestation_deny_bound_env_named(self):
        assert ENSEMBLE_ATTESTATION_DENY_BOUND_ENV in self.text, (
            f"runbook missing env var {ENSEMBLE_ATTESTATION_DENY_BOUND_ENV!r}"
        )


# =============================================================================
# Default-value drift — defaults match between doc and resolver
# =============================================================================


class TestDefaultValueDrift:
    @pytest.fixture(autouse=True)
    def _doc(self):
        assert SETUP_MD.is_file(), f"missing runbook doc: {SETUP_MD}"
        self.text = SETUP_MD.read_text(encoding="utf-8")
        return self.text

    def test_default_mode_dry_documented(self):
        # The runbook env-table cell + the boot-log section both name
        # the default. We assert the env-table cell pinned to ``dry``
        # (the env table format: a row containing the env name + the
        # default value in the third column).
        # Look for the row that names the env var; the default lives
        # in the same row.
        pattern = re.compile(
            re.escape(ENSEMBLE_ATTESTATION_MODE_ENV)
            + r".*?`"
            + re.escape(DEFAULT_MODE)
            + r"`",
            re.DOTALL,
        )
        assert pattern.search(self.text), (
            f"runbook does not document the default {DEFAULT_MODE!r} "
            f"for {ENSEMBLE_ATTESTATION_MODE_ENV}"
        )

    def test_default_window_3_documented(self):
        pattern = re.compile(
            re.escape(ENSEMBLE_ATTESTATION_WINDOW_ENV)
            + r".*?`"
            + str(DEFAULT_WINDOW)
            + r"`",
            re.DOTALL,
        )
        assert pattern.search(self.text), (
            f"runbook does not document the default {DEFAULT_WINDOW} "
            f"for {ENSEMBLE_ATTESTATION_WINDOW_ENV}"
        )

    def test_default_deny_bound_3_documented(self):
        pattern = re.compile(
            re.escape(ENSEMBLE_ATTESTATION_DENY_BOUND_ENV)
            + r".*?`"
            + str(DEFAULT_DENY_BOUND)
            + r"`",
            re.DOTALL,
        )
        assert pattern.search(self.text), (
            f"runbook does not document the default {DEFAULT_DENY_BOUND} "
            f"for {ENSEMBLE_ATTESTATION_DENY_BOUND_ENV}"
        )


# =============================================================================
# Boot-log prefix drift — the operator grep target is verbatim
# =============================================================================


class TestBootLogPrefixDrift:
    @pytest.fixture(autouse=True)
    def _doc(self):
        assert SETUP_MD.is_file(), f"missing runbook doc: {SETUP_MD}"
        self.text = SETUP_MD.read_text(encoding="utf-8")
        return self.text

    def test_boot_log_prefix_documented(self):
        # Operators grep for this exact prefix; the doc must name it
        # verbatim so an accidental rename triggers CI.
        assert "Leader completion attestation resolved" in self.text

    def test_o1_marker_documented(self):
        # The O1 WARN marker (``N_le_min_recent_window=WARN``) is
        # documented as part of the runbook — operators must be able
        # to grep for the operator-visible failure mode.
        assert "N_le_min_recent_window=WARN" in self.text

    def test_min_recent_window_documented(self):
        # The compaction floor is named in the runbook so the O1 WARN
        # makes sense to operators reading the runbook.
        assert "MIN_RECENT_WINDOW" in self.text or "min_recent_window" in self.text


# =============================================================================
# Promotion-metric name drift — canonical counter names match
# =============================================================================


class TestMetricNameDrift:
    @pytest.fixture(autouse=True)
    def _doc(self):
        assert SETUP_MD.is_file(), f"missing runbook doc: {SETUP_MD}"
        self.text = SETUP_MD.read_text(encoding="utf-8")
        return self.text

    def test_dry_log_total_named(self):
        assert METRIC_DRY_LOG_TOTAL in self.text, (
            f"runbook missing canonical metric name {METRIC_DRY_LOG_TOTAL!r}"
        )

    def test_dry_log_deny_predicate_total_named(self):
        assert METRIC_DRY_LOG_DENY_PREDICATE_TOTAL in self.text, (
            f"runbook missing canonical metric name "
            f"{METRIC_DRY_LOG_DENY_PREDICATE_TOTAL!r}"
        )

    def test_enforce_denied_total_named(self):
        assert METRIC_ENFORCE_DENIED_TOTAL in self.text, (
            f"runbook missing canonical metric name "
            f"{METRIC_ENFORCE_DENIED_TOTAL!r}"
        )


# =============================================================================
# Promotion SOP — the dry→enforce decision rule is documented
# =============================================================================


class TestPromotionSOP:
    @pytest.fixture(autouse=True)
    def _doc(self):
        assert SETUP_MD.is_file(), f"missing runbook doc: {SETUP_MD}"
        self.text = SETUP_MD.read_text(encoding="utf-8")
        return self.text

    def test_dry_to_enforce_promotion_named(self):
        # The runbook must document the dry→enforce promotion SOP so
        # operators know the standard procedure (≤2-week soak,
        # adjudicated false-positive rate, then flip).
        assert "dry" in self.text.lower() and "enforce" in self.text.lower()

    def test_soak_duration_documented(self):
        # The ≤2-week soak is the canonical promotion metric baseline.
        assert "≤2-week" in self.text or "2-week" in self.text

    def test_postmortem_query_documented(self):
        # The PG query for ``completion_gate_escalated=true`` is part of
        # the operator postmortem checklist.
        assert "completion_gate_escalated" in self.text

    def test_revert_path_documented(self):
        # The instant-revert path (set mode=off or dry + restart) is
        # documented in the runbook.
        assert "restart" in self.text.lower()


# =============================================================================
# Drift direction — flagging when the doc mentions a value the resolver
# does NOT export (defense-in-depth against typos in the doc).
# =============================================================================


class TestNoPhantomEnvVar:
    @pytest.fixture(autouse=True)
    def _doc(self):
        assert SETUP_MD.is_file(), f"missing runbook doc: {SETUP_MD}"
        self.text = SETUP_MD.read_text(encoding="utf-8")
        return self.text

    def test_no_legacy_single_bool_env_in_doc(self):
        """Per C-5 / AC-7.9 corrected: the legacy single-bool env is
        FORBIDDEN. The runbook must NOT document any legacy env var
        that would re-introduce the dual-state class of bugs."""
        # The WC-wake precedent has its own env vars; those are
        # allowed. The attestation feature has exactly three env vars
        # (the canonical resolver exports only those).
        for phantom in (
            "ENSEMBLE_ATTESTATION_ENABLED",
            "ENSEMBLE_LEADER_ATTESTATION_DRY",
            "ENSEMBLE_LEADER_ATTESTATION_DRY_RUN",
            "ENSEMBLE_ATTESTATION_ENABLED",
            "ENSEMBLE_LEADER_ATTESTATION_OFF",
        ):
            assert phantom not in self.text, (
                f"runbook documents phantom legacy env var {phantom!r} "
                "(AC-7.9 forbids the legacy single-bool surface)"
            )

# =============================================================================
# Escalation-path doc truth (review must-fix / same-pass fix 3 — branch
# feature/leader-completion-attestation). The postmortem section's log
# event name, field claims, and SQL are pinned against the EMITTED code
# (daemon/graph.py gate node) so the doc cannot drift from production
# truth again. Cross-checked bidirectionally: doc ↔ graph source.
# =============================================================================

GRAPH_PY = REPO_ROOT / "daemon" / "graph.py"

#: The literal event name emitted by the gate node's escalation branch.
EMITTED_TERMINAL_EVENT = "event=gate_terminal_after_bound"


class TestEscalationPathDocTruth:
    @pytest.fixture(autouse=True)
    def _sources(self):
        assert SETUP_MD.is_file(), f"missing runbook doc: {SETUP_MD}"
        assert GRAPH_PY.is_file(), f"missing graph source: {GRAPH_PY}"
        self.text = SETUP_MD.read_text(encoding="utf-8")
        self.graph_src = GRAPH_PY.read_text(encoding="utf-8")
        return self.text

    def test_emitted_event_name_is_canonical_in_graph(self):
        """Sanity anchor: the graph DOES emit event=gate_terminal_after_bound.

        If this fails, the emitter was renamed — update the runbook AND
        EMITTED_TERMINAL_EVENT together.
        """
        assert EMITTED_TERMINAL_EVENT in self.graph_src

    def test_doc_names_the_emitted_event(self):
        # (a) event-name truth: the doc must name the event the gate
        # node ACTUALLY emits (not the phantom
        # leader_completion_gate_terminal_after_bound).
        assert EMITTED_TERMINAL_EVENT in self.text, (
            "runbook escalation section must name the emitted event "
            f"{EMITTED_TERMINAL_EVENT!r}"
        )
        assert "leader_completion_gate_terminal_after_bound" not in self.text, (
            "runbook documents the phantom event "
            "'leader_completion_gate_terminal_after_bound' — the gate "
            "node emits 'gate_terminal_after_bound'"
        )

    def test_doc_does_not_claim_last_denial_reason_field(self):
        # (b) field truth: the escalation log line carries instance_id,
        # attestation_denied_count, completion_gate_escalated — there is
        # NO last_denial_reason field anywhere in the emitter.
        assert "last_denial_reason" not in self.text, (
            "runbook claims a last_denial_reason field that the "
            "gate_terminal_after_bound emitter never produces"
        )
        assert "last_denial_reason" not in self.graph_src

    def test_postmortem_sql_selects_the_primary_key(self):
        # (c) SQL truth: the instances table PK is instance_id; the
        # postmortem query must not select a nonexistent "id" column.
        assert "SELECT instance_id, attestation_denied_count" in self.text
        assert "SELECT id," not in self.text, (
            "runbook postmortem SQL selects a nonexistent 'id' column — "
            "the instances PK is instance_id"
        )
