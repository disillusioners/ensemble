"""Mode: enforce — fail-open exceptions at the scanner and ledger seams."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.exc import OperationalError

from daemon.services.attestation_gate import GateSettings, evaluate


def _settings():
    return GateSettings(mode="enforce", window=3, deny_bound=3)


def _manager():
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    return manager


def test_scanner_operational_error_allows_and_logs_db_error(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise OperationalError("statement", {}, Exception("scanner db down"))

    monkeypatch.setattr(
        "daemon.services.attestation_gate.scan_for_attestation_detailed",
        boom,
    )
    with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_gate"):
        result = evaluate(
            "fail-open",
            0,
            [AIMessage(content="not attested")],
            _settings(),
            _manager(),
        )
    assert result.decision.value == "allowed"
    assert result.should_inject_nudge is False
    assert "event=leader_completion_gate_error" in caplog.text
    assert "error_class=OperationalError" in caplog.text


def test_keyboard_interrupt_is_not_caught(monkeypatch):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("pause/cancel")

    monkeypatch.setattr(
        "daemon.services.attestation_gate.scan_for_attestation_detailed",
        interrupt,
    )
    with pytest.raises(KeyboardInterrupt):
        evaluate(
            "cancel",
            0,
            [AIMessage(content="not attested")],
            _settings(),
            _manager(),
        )
