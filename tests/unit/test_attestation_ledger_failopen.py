"""Unit tests for the C3 fail-open wrapper at the attestation ledger DB seam.

Covers :mod:`daemon.services.attestation_ledger` — the four ``safe_*``
wrappers added in Phase 3 task 3.6 to extend the Phase-2 fail-open
layer (the scanner/gate ``except Exception`` + ``leader_completion_gate_
error``) to the new DB seam (the four ledger methods). The wrappers:

* widen the W4 precedent's narrow exception set to ``except Exception``
  (so SQLAlchemy ``OperationalError`` IS caught);
* on failure, return ``None`` to the caller and emit the canonical
  ``leader_completion_gate_db_error`` structured log;
* keep ``KeyboardInterrupt`` / ``SystemExit`` fail-CLOSED (BaseException
  propagates — every handler here is ``except Exception``).

The ``except Exception`` choice is the AC-6.6 contract: DB errors at
the ledger seam degrade deny → allow + structured log; the leader
mission NEVER errors (D2 outage class — bounded by the log volume).
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from daemon.services.attestation_ledger import (
    AttestationLedger,
    safe_increment,
    safe_reset,
    safe_set_escalated,
    safe_set_escalated_and_reset,
)


# ─────────────────────────────────────────────────────────────────────────────
# Protocol-shape sanity (the gate node's consumer contract)
# ─────────────────────────────────────────────────────────────────────────────


class TestAttestationLedgerProtocol:
    def test_protocol_lists_four_methods(self):
        # The Protocol declares the four methods the gate node consumes;
        # assert the names exist (so a repo rename would break this test).
        method_names = {
            "increment",
            "reset",
            "set_escalated",
            "set_escalated_and_reset",
        }
        assert hasattr(AttestationLedger, "__annotations__") or True
        # The Protocol's body declares them as ...; runtime check via
        # __protocol_attrs__ (PEP 544 runtime protocol checks).
        for name in method_names:
            assert hasattr(AttestationLedger, name), (
                f"Protocol missing method: {name}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Happy-path delegation (the wrapper is a thin try/except)
# ─────────────────────────────────────────────────────────────────────────────


class TestSafeDelegation:
    """The safe_* wrappers pass through the underlying repo return value."""

    def test_safe_increment_returns_underlying_value(self):
        ledger = MagicMock()
        ledger.increment.return_value = 5
        result = safe_increment(ledger, "inst-1", "ep-1")
        assert result == 5
        ledger.increment.assert_called_once_with("inst-1", "ep-1")

    def test_safe_reset_returns_true(self):
        ledger = MagicMock()
        ledger.reset.return_value = True
        result = safe_reset(ledger, "inst-1")
        assert result is True

    def test_safe_set_escalated_returns_true(self):
        ledger = MagicMock()
        ledger.set_escalated.return_value = True
        result = safe_set_escalated(ledger, "inst-1")
        assert result is True

    def test_safe_set_escalated_and_reset_returns_true(self):
        ledger = MagicMock()
        ledger.set_escalated_and_reset.return_value = True
        result = safe_set_escalated_and_reset(ledger, "inst-1")
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# C3 fail-open — OperationalError degrades to None + structured log
# ─────────────────────────────────────────────────────────────────────────────


class TestFailOpenOnOperationalError:
    """AC-6.6: SQLAlchemy OperationalError does NOT crash the gate."""

    @pytest.fixture
    def failing_ledger(self):
        from sqlalchemy.exc import OperationalError

        ledger = MagicMock()
        ledger.increment.side_effect = OperationalError(
            "statement", {}, Exception("db down")
        )
        ledger.reset.side_effect = OperationalError(
            "statement", {}, Exception("db down")
        )
        ledger.set_escalated.side_effect = OperationalError(
            "statement", {}, Exception("db down")
        )
        ledger.set_escalated_and_reset.side_effect = OperationalError(
            "statement", {}, Exception("db down")
        )
        return ledger

    def test_safe_increment_swallows_operational_error(self, failing_ledger, caplog):
        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_ledger"):
            result = safe_increment(failing_ledger, "inst-1", "ep-1")
        assert result is None  # fail-open return sentinel
        assert any(
            "event=leader_completion_gate_db_error" in r.message
            and "method=increment" in r.message
            and "instance_id=inst-1" in r.message
            and "error_class=OperationalError" in r.message
            for r in caplog.records
        )

    def test_safe_reset_swallows_operational_error(self, failing_ledger, caplog):
        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_ledger"):
            result = safe_reset(failing_ledger, "inst-1")
        assert result is None
        assert any(
            "event=leader_completion_gate_db_error" in r.message
            and "method=reset" in r.message
            for r in caplog.records
        )

    def test_safe_set_escalated_swallows_operational_error(self, failing_ledger, caplog):
        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_ledger"):
            result = safe_set_escalated(failing_ledger, "inst-1")
        assert result is None
        assert any(
            "event=leader_completion_gate_db_error" in r.message
            and "method=set_escalated" in r.message
            for r in caplog.records
        )

    def test_safe_set_escalated_and_reset_swallows_operational_error(
        self, failing_ledger, caplog
    ):
        with caplog.at_level(
            logging.ERROR, logger="daemon.services.attestation_ledger"
        ):
            result = safe_set_escalated_and_reset(failing_ledger, "inst-1")
        assert result is None
        assert any(
            "event=leader_completion_gate_db_error" in r.message
            and "method=set_escalated_and_reset" in r.message
            for r in caplog.records
        )


class TestFailOpenOnGenericException:
    """Every Exception (not just OperationalError) fails open — the
    widening from the W4 precedent's narrow set is the whole point of
    Phase 3 task 3.6.
    """

    def test_safe_increment_swallows_runtime_error(self, caplog):
        ledger = MagicMock()
        ledger.increment.side_effect = RuntimeError("something else")
        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_ledger"):
            result = safe_increment(ledger, "inst-1", "ep-1")
        assert result is None
        assert any(
            "error_class=RuntimeError" in r.message for r in caplog.records
        )

    def test_safe_increment_swallows_value_error(self, caplog):
        ledger = MagicMock()
        ledger.increment.side_effect = ValueError("bad data")
        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_ledger"):
            result = safe_increment(ledger, "inst-1", "ep-1")
        assert result is None


class TestKeyboardInterruptPropagates:
    """``KeyboardInterrupt`` / ``SystemExit`` are BaseException — the
    wrappers must NOT swallow them (fail-closed on interpreter shutdown).
    """

    def test_safe_increment_propagates_keyboard_interrupt(self):
        ledger = MagicMock()
        ledger.increment.side_effect = KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            safe_increment(ledger, "inst-1", "ep-1")

    def test_safe_reset_propagates_system_exit(self):
        ledger = MagicMock()
        ledger.reset.side_effect = SystemExit(1)
        with pytest.raises(SystemExit):
            safe_reset(ledger, "inst-1")


# ─────────────────────────────────────────────────────────────────────────────
# Context plumbing (error_class_context is logged verbatim)
# ─────────────────────────────────────────────────────────────────────────────


class TestContextPropagatesToLog:
    def test_context_dict_is_logged(self, caplog):
        ledger = MagicMock()
        ledger.increment.side_effect = RuntimeError("boom")
        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_ledger"):
            safe_increment(
                ledger,
                "inst-1",
                "ep-1",
                error_class_context={"denial_epoch": "ep-1", "extra": "ctx"},
            )
        assert any(
            "denial_epoch" in r.message and "ep-1" in r.message
            for r in caplog.records
        )

    def test_instance_id_falls_back_to_none(self, caplog):
        ledger = MagicMock()
        ledger.reset.side_effect = RuntimeError("boom")
        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_ledger"):
            safe_reset(ledger, None)
        # Log line still emitted (fail-open contract).
        assert any(
            "event=leader_completion_gate_db_error" in r.message
            for r in caplog.records
        )
