"""Mode: enforce — gate decision overhead performance gate (NFR-1).

The measurement isolates synchronous ``evaluate`` (scanner, R2 facades, decision,
and canonical log emission) and reports the measured P95 for the run.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from daemon.services.attestation_gate import GateSettings, evaluate


def _settings():
    return GateSettings(mode="enforce", window=3, deny_bound=3)


def test_gate_decision_p95_is_at_most_20ms():
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    messages = [
        HumanMessage(content="work"),
        AIMessage(content="plain final"),
    ]

    # Warm the one-time resolver import before collecting samples.
    evaluate("perf", 0, messages, _settings(), manager)
    durations_ns = []
    for _ in range(200):
        started = time.perf_counter_ns()
        evaluate("perf", 0, messages, _settings(), manager)
        durations_ns.append(time.perf_counter_ns() - started)

    durations_ns.sort()
    p95_ns = durations_ns[int(0.95 * (len(durations_ns) - 1))]
    p95_ms = p95_ns / 1_000_000
    assert p95_ms <= 20.0, f"NFR-1 measured P95={p95_ms:.3f} ms"
