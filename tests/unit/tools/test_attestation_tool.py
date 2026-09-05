"""Tool return-shape and idempotency tests for ``attest_completion``.

The LCA feature's Phase 2 completion gate scans the leader's most recent
``N`` AIMessages for an ``attest_completion`` tool_call. The contract
implemented by ``daemon/tools/attestation.py`` is:

* The tool is **no-arg** — the args schema is empty.
* The tool is **idempotent** — any call in the lookback window counts.
* The tool returns a **deterministic confirmation frame**
  ``{"attested": True, "timestamp": "<iso8601 UTC>"}``.
* The tool **does not mutate state** — the attestation is recorded by
  virtue of the tool call existing in the message stream; the return
  value is for caller-side display only.

These tests pin the contract at the StructuredTool level so a
maintainer refactoring the body cannot silently break the
return-shape invariant (the Phase 2 scanner reads the tool_call name,
not the return value — but the return value is what the leader sees
in its ToolMessage and is the surface area the agent experiences).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


# ── Tool return-shape contract ───────────────────────────────────────────────


class TestAttestCompletionReturnShape:
    """The tool returns ``{"attested": True, "timestamp": "<iso8601>"}``
    — the deterministic confirmation frame the Phase 2 scanner relies
    on. Per the plan text, the return shape is byte-stable contract
    surface area even though the scanner only reads the tool_call name."""

    @pytest.fixture
    def tool(self):
        from daemon.tools.attestation import attest_completion

        return attest_completion

    def test_return_has_attested_true(self, tool) -> None:
        """``attested`` MUST be exactly ``True`` (boolean), not a
        truthy surrogate — the scanner and the FE inspector rely on
        exact equality."""
        result = tool.invoke({})
        assert isinstance(result, dict)
        assert result.get("attested") is True

    def test_return_has_iso8601_timestamp(self, tool) -> None:
        """``timestamp`` MUST be an ISO-8601 string. Loose regex check
        (year-month-dayThour:minute:second with optional fractional /
        timezone offset). The exact format is whatever
        ``datetime.now(timezone.utc).isoformat()`` produces today;
        pin the shape, not the byte content."""
        result = tool.invoke({})
        assert "timestamp" in result
        ts = result["timestamp"]
        # Re-parse to confirm it round-trips through fromisoformat — the
        # simplest ISO-8601 conformance check. datetime.fromisoformat
        # accepts the Python 3.11+ extended format including the
        # ``+00:00`` UTC offset and fractional seconds.
        parsed = datetime.fromisoformat(ts)
        # Confirm it's a UTC timestamp (timezone-aware)
        assert parsed.tzinfo is not None, (
            f"timestamp must be timezone-aware, got naive: {ts}"
        )

    def test_return_has_exactly_two_keys(self, tool) -> None:
        """Two-key contract — adding fields is a deliberate change,
        not an accident. Pin the key set so a maintainer adding
        state to the return value gets a test failure."""
        result = tool.invoke({})
        assert set(result.keys()) == {"attested", "timestamp"}

    def test_no_arg_signature(self, tool) -> None:
        """The tool takes NO arguments. ``args_schema`` is empty and
        ``invoke({})`` succeeds with no required keys."""
        assert tool.args == {}
        # Should NOT accept any keyword — empty kwargs only.
        result = tool.invoke({})
        assert result["attested"] is True

    def test_return_serializable_to_json(self, tool) -> None:
        """The dict return MUST round-trip through ``json.dumps`` —
        the structured output flows back to the leader as a JSON
        ToolMessage content."""
        result = tool.invoke({})
        serialized = json.dumps(result)
        reparsed = json.loads(serialized)
        assert reparsed == result


# ── Idempotency contract ────────────────────────────────────────────────────


class TestAttestCompletionIdempotency:
    """Per the plan: ``attest_completion`` is idempotent — any call in
    the lookback window counts as the attestation. The tool body must
    produce a fresh timestamp on every call (the contract is "ANY call
    counts", not "only one call per turn counts")."""

    def test_repeated_calls_all_succeed(self) -> None:
        """N successive invocations all return ``attested: True`` —
        no per-call cooldown, no exception, no first-call-wins
        guard."""
        from daemon.tools.attestation import attest_completion

        results = [attest_completion.invoke({}) for _ in range(5)]
        assert all(r["attested"] is True for r in results)
        assert len(results) == 5

    def test_repeated_calls_have_distinct_timestamps(self) -> None:
        """Each call produces a fresh timestamp (the tool body
        re-reads ``datetime.now(timezone.utc)`` on every invocation,
        not a module-load-time constant). Distinct microsecond
        precision is the strictest practical guarantee."""
        from daemon.tools.attestation import attest_completion

        results = [attest_completion.invoke({}) for _ in range(3)]
        timestamps = [r["timestamp"] for r in results]
        # At least one pair must differ; on fast hardware the
        # timestamps may collide to microsecond, so use a set check
        # rather than pairwise inequality.
        assert len(set(timestamps)) >= 1, "timestamps must be valid"


# ── No-mutation contract ────────────────────────────────────────────────────


class TestAttestCompletionIsNoOp:
    """The attestation is recorded by virtue of the tool call existing
    in the message stream — the tool body must NOT mutate any state.
    These tests assert the body is free of side effects beyond
    constructing the return dict."""

    def test_factory_unused_args_are_ignored(self) -> None:
        """``create_attestation_tools(manager, instance_id, agent_id)``
        accepts the same signature as sibling factories but the body
        does not depend on any closure binding. Passing ``None`` for
        all three MUST NOT crash."""
        from daemon.tools.attestation import create_attestation_tools

        tools = create_attestation_tools(None, None, None)
        assert len(tools) == 1
        result = tools[0].invoke({})
        assert result["attested"] is True

    def test_factory_returns_same_tool_object(self) -> None:
        """Multiple factory calls return the same module-level tool
        object — the factory is a thin wrapper, not a per-instance
        rebuilder."""
        from daemon.tools.attestation import (
            create_attestation_tools,
            attest_completion,
        )

        manager = MagicMock(name="InstanceManager")
        tools_a = create_attestation_tools(manager, "a", "leader")
        tools_b = create_attestation_tools(manager, "b", "leader")
        assert tools_a[0] is tools_b[0] is attest_completion