"""Mode matrix: off/dry/enforce decision entry counts and promotion metrics.

The loop deliberately evaluates the pure gate 1,000 times, rather than relying
on a mock log call, so the count is an operator-observable corpus signal.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from daemon.services.attestation_gate import GateSettings, evaluate
from daemon.services.attestation_resolver import (
    METRIC_DRY_LOG_DENY_PREDICATE_TOTAL,
    METRIC_DRY_LOG_TOTAL,
    METRIC_ENFORCE_DENIED_TOTAL,
    emit_attestation_boot_log,
    get_promotion_metrics,
    reset_attestation_resolver_for_tests,
)


def _manager():
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    # Third R2 input (2026-09-06) — defaulted to 0; the observability
    # matrix exercises the "no live descendants" R2 deny predicate.
    manager.count_live_descendants.return_value = 0
    return manager


@pytest.mark.parametrize(
    "mode,expected_entries,expected_dry_deny,expected_enforce_deny",
    [
        ("off", 0, 0, 0),
        ("dry", 1000, 1000, 0),
        ("enforce", 1000, 0, 1000),
    ],
    ids=["off", "dry", "enforce"],
)
def test_decision_log_counts_and_metrics_per_mode(
    mode,
    expected_entries,
    expected_dry_deny,
    expected_enforce_deny,
    caplog,
    monkeypatch,
):
    reset_attestation_resolver_for_tests()
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", mode)
    manager = _manager()
    messages = [AIMessage(content="no attestation")]
    settings = GateSettings(mode=mode, window=3, deny_bound=3)
    with caplog.at_level(logging.INFO):
        for _ in range(1000):
            evaluate(f"observability-{mode}", 0, messages, settings, manager)
        emit_attestation_boot_log()

    lines = [
        record.message
        for record in caplog.records
        if "event=leader_completion_gate" in record.message
    ]
    assert len(lines) == expected_entries
    if expected_entries:
        assert all("messages_scanned=" in line and "mode=" + mode in line for line in lines)
        assert all("pending_children=0" in line for line in lines)
        assert all("queued_or_expected_wakeups=0" in line for line in lines)
    metrics = get_promotion_metrics()
    assert metrics[METRIC_DRY_LOG_TOTAL] == expected_dry_deny
    assert metrics[METRIC_DRY_LOG_DENY_PREDICATE_TOTAL] == expected_dry_deny
    assert metrics[METRIC_ENFORCE_DENIED_TOTAL] == expected_enforce_deny

    boot_lines = [
        record.message
        for record in caplog.records
        if "Leader completion attestation resolved" in record.message
    ]
    assert len(boot_lines) == 1
    assert f"mode={mode}" in boot_lines[0]
