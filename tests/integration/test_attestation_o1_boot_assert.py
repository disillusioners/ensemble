"""Integration test — O1 boot assert (Phase 4 task 4.2).

Per FR-7 / AC-7.8: when ``ENSEMBLE_LEADER_ATTESTATION_WINDOW`` exceeds
``MIN_RECENT_WINDOW`` (the compaction floor — ``daemon/constants.py``),
the resolver's one-time boot log carries a ``N_le_min_recent_window=WARN``
marker AND emits a SEPARATE WARN-level line naming the operator-visible
risk. The assert is WARN-only — the gate continues running with the
configured WINDOW (no hard-fail, per architecture-recommendation.md
§G14 RESOLVED → FR-7).

This test is a focused integration test (no full daemon boot — the
resolver + boot-log are the unit of test). It DOES NOT spin up the
HTTP server; it imports the resolver module directly and exercises
:func:`emit_attestation_boot_log` under env-mutation conditions,
verifying the O1 contract surfaces correctly to operators.

Layered with :file:`tests/unit/test_attestation_resolver.py` (which
covers the same surface in finer granularity), this test pins the
operator-facing boot-log contract end-to-end (INFO + optional WARN
pair, restart-read idempotence, one-shot emission).
"""
from __future__ import annotations

import logging
import re

import pytest

from daemon.services.attestation_resolver import (
    DEFAULT_WINDOW,
    ENSEMBLE_ATTESTATION_WINDOW_ENV,
    emit_attestation_boot_log,
    get_config,
    reset_attestation_resolver_for_tests,
)


@pytest.fixture(autouse=True)
def _resolver_reset():
    reset_attestation_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()


# =============================================================================
# O1 boot assert — WARN fires on WINDOW > MIN_RECENT_WINDOW
# =============================================================================


class TestO1BootAssertWARN:
    def test_window_above_floor_emits_warn_marker(
        self, monkeypatch, caplog
    ):
        """WINDOW=5 (MIN_RECENT_WINDOW=3) → ``N_le_min_recent_window=WARN``."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "5")
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        # The main INFO line carries the WARN marker.
        info_messages = [
            r.message
            for r in caplog.records
            if r.levelno == logging.INFO
        ]
        assert any(
            "N_le_min_recent_window=WARN" in m for m in info_messages
        )
        assert any("window=5" in m for m in info_messages)

    def test_window_above_floor_emits_separate_warn_line(
        self, monkeypatch, caplog
    ):
        """The WARN detail is a SEPARATE WARN-level log line — operators
        filter WARN separately from INFO."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "7")
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        warn_records = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        # Operator-visible risk named in the WARN body.
        assert any(
            "attestation_denied_count risk" in r.message
            for r in warn_records
        )
        assert any(
            "WINDOW=7" in r.message for r in warn_records
        )

    def test_window_above_floor_does_not_fail_closed(
        self, monkeypatch, caplog
    ):
        """The WARN is operator-visible but the gate continues running —
        ``get_config()`` still returns the configured WINDOW."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "10")
        config = get_config()
        # No raise — boot succeeds.
        assert config.window == 10
        emit_attestation_boot_log()
        # The resolver returns the configured WINDOW; the O1 assert
        # does not clamp it down.
        assert get_config().window == 10


class TestO1BootAssertPASS:
    def test_window_equals_floor_passes(self, monkeypatch, caplog):
        """Default ``WINDOW=3`` matches ``MIN_RECENT_WINDOW=3`` — PASS,
        no WARN. This is the ship posture."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "3")
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        info_messages = [
            r.message
            for r in caplog.records
            if r.levelno == logging.INFO
        ]
        assert any("N_le_min_recent_window=PASS" in m for m in info_messages)
        assert not any(
            r.levelno == logging.WARNING for r in caplog.records
        )

    def test_window_below_floor_passes(self, monkeypatch, caplog):
        """``WINDOW=2 < MIN_RECENT_WINDOW=3`` — PASS (assert is one-sided;
        floor is a soft target, not a hard minimum)."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "2")
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        info_messages = [
            r.message
            for r in caplog.records
            if r.levelno == logging.INFO
        ]
        assert any("N_le_min_recent_window=PASS" in m for m in info_messages)

    def test_default_config_passes(self, monkeypatch, caplog):
        """No env set → ``WINDOW=DEFAULT_WINDOW=3`` → PASS."""
        monkeypatch.delenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, raising=False)
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        info_messages = [
            r.message
            for r in caplog.records
            if r.levelno == logging.INFO
        ]
        assert any("N_le_min_recent_window=PASS" in m for m in info_messages)
        assert any(
            f"window={DEFAULT_WINDOW}" in m for m in info_messages
        )


# =============================================================================
# Boot log structural contract — INFO line carries the resolved values
# =============================================================================


class TestBootLogStructure:
    def test_boot_log_carries_resolved_mode_window_bound(
        self, monkeypatch, caplog
    ):
        """Single INFO line carries ``mode=``, ``window=``, ``deny_bound=``,
        ``attestation_enabled=``, ``N_le_min_recent_window=`` — operators
        grep for these tokens to confirm the resolved config at startup.
        """
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "5")
        monkeypatch.delenv("ENSEMBLE_LEADER_ATTESTATION_MODE", raising=False)
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()

        info_text = "\n".join(
            r.message
            for r in caplog.records
            if r.levelno == logging.INFO
        )
        # Resolved values named in the line.
        assert re.search(r"mode=\w+", info_text)
        assert re.search(r"window=\d+", info_text)
        assert re.search(r"deny_bound=\d+", info_text)
        assert re.search(r"attestation_enabled=(true|false)", info_text)
        assert re.search(r"N_le_min_recent_window=(PASS|WARN)", info_text)
        # The env vars are echoed verbatim for operator visibility
        # (``<unset>`` for the mode that wasn't set).
        assert "<unset>" in info_text

    def test_boot_log_emitted_exactly_once_per_process(
        self, monkeypatch, caplog
    ):
        """Idempotent emission — the boot log fires EXACTLY ONCE per
        process even if the manager-init path accidentally calls
        ``emit_attestation_boot_log`` multiple times (defense-in-depth
        against duplicate-init bugs)."""
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            for _ in range(3):
                emit_attestation_boot_log()
        info_count = sum(
            1
            for r in caplog.records
            if "Leader completion attestation resolved" in r.message
        )
        assert info_count == 1

    def test_boot_log_named_for_grep(self, monkeypatch, caplog):
        """Operators grep for the literal prefix ``Leader completion
        attestation resolved`` — pinned verbatim so an accidental
        rewrite of the prefix triggers the drift assertion."""
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        assert any(
            r.message.startswith("Leader completion attestation resolved")
            for r in caplog.records
        )