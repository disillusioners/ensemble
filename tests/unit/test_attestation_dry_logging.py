"""Mode: dry — canonical decision logging and zero side effects."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from daemon.services.attestation_gate import CANONICAL_LOG_SCHEMA_FIELDS, evaluate
from daemon.services.attestation_resolver import reset_attestation_resolver_for_tests


def test_dry_log_has_complete_schema_and_stale_attestation_diagnostic(caplog):
    reset_attestation_resolver_for_tests()
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.enqueue_message = MagicMock()
    messages = [
        AIMessage(
            content="old attested",
            tool_calls=[{"name": "attest_completion", "args": {}, "id": "old"}],
        )
    ]
    messages.extend(AIMessage(content=f"filler {i}") for i in range(4))

    with caplog.at_level(logging.INFO, logger="daemon.services.attestation_gate"):
        result = evaluate(
            "dry-schema",
            2,
            messages,
            __import__(
                "daemon.services.attestation_gate",
                fromlist=["GateSettings"],
            ).GateSettings(mode="dry", window=3, deny_bound=3),
            manager,
        )

    assert result.decision.value == "dry_log"
    assert result.next_denied_count == 2
    assert result.should_inject_nudge is False
    assert result.attest_seen_outside_window is True
    assert result.messages_scanned == 3
    assert result.pending_children == 0
    assert result.queued_or_expected_wakeups == 0

    log_line = next(
        record.message
        for record in caplog.records
        if "event=leader_completion_gate" in record.message
    )
    for field in CANONICAL_LOG_SCHEMA_FIELDS:
        assert f"{field}=" in log_line, f"missing canonical field {field}"
    assert "messages_scanned=3" in log_line
    assert "attest_seen_outside_window=True" in log_line
    manager.count_pending_children.assert_called_once_with("dry-schema")
    manager.get_queued_or_expected_wakeups.assert_called_once_with("dry-schema")
    manager.enqueue_message.assert_not_called()


def test_dry_mode_does_not_touch_ledger_or_increment_counter(caplog):
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.enqueue_message = MagicMock()
    with caplog.at_level(logging.INFO, logger="daemon.services.attestation_gate"):
        result = evaluate(
            "dry-zero",
            1,
            [AIMessage(content="no attestation")],
            __import__(
                "daemon.services.attestation_gate",
                fromlist=["GateSettings"],
            ).GateSettings(mode="dry", window=3, deny_bound=3),
            manager,
        )
    assert result.decision.value == "dry_log"
    assert result.next_denied_count == 1
    # The pure gate has no ledger argument by design; the graph node is the
    # seam that owns the real row write.  The no-side-effect contract here is
    # enforced by the decision value and unchanged counter.
    manager.enqueue_message.assert_not_called()
    assert not result.should_inject_nudge
