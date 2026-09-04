"""Unit tests for ``daemon.services.command_dispatcher`` (Phase 1 / WS-1).

Covers the WS-1 acceptance surface from ``phase1-plan.md``:
  - Parse layer: ``/compact`` w/ args, ``//``-BEFORE-``/`` passthrough,
    case-insensitivity, unknown → ``None``, no-slash → ``None``.
  - Registry: duplicate registration raises, case-insensitive resolve.
  - O10 state registry: active slot per instance, terminal ring LRU caps
    at ``max_state_per_instance`` (default 20), TTL expiry → not found,
    instance-delete eviction.
  - Rate-limit state: busy in-flight, rate-limited within min-interval,
    ordering — rate-limited NEVER acquires the gate (test-visible seam).

Each test exercises the dispatcher in isolation — no manager, no router.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from daemon.services.command_dispatcher import (
    ActiveCommand,
    CommandContext,
    CommandDispatcher,
    CommandPhase,
    CommandRegistry,
    CommandSpec,
    CommandStateRegistry,
    CompactedType,
    DispatchOutcome,
    NoopReason,
    ParseResult,
    ParsedCommand,
    RejectionReason,
    parse_slash_command,
)


# ─────────────────────────────────────────────────────────────────────────
# Helpers — dummy handler for tests that need an active slot
# ─────────────────────────────────────────────────────────────────────────


async def _noop_handler(*, instance_id, args, command_id, context):
    """Handler that just terminalizes success — used by happy-path tests."""
    await context.terminalize(
        CommandPhase.SUCCESS.value,
        detail={"reason": "noop_test"},
    )


async def _hanging_handler(*, instance_id, args, command_id, context):
    """Handler that stays in-flight until the test cancels the task."""
    await asyncio.Event().wait()


async def _crashing_handler(*, instance_id, args, command_id, context):
    raise RuntimeError("boom")


def _make_spec(
    name="compact",
    *,
    rate_limit=0,
    handler=None,
    availability=None,
    description="",
) -> CommandSpec:
    return CommandSpec(
        name=name,
        description=description or f"Test command {name}",
        availability=availability,
        rate_limit_per_instance=rate_limit,
        handler=handler or _noop_handler,
    )


def _make_dispatcher(
    *,
    enabled=True,
    escape_prefix="//",
    min_interval_s=10,
    state_ttl_s=600,
    max_state_per_instance=20,
) -> CommandDispatcher:
    return CommandDispatcher(
        enabled=enabled,
        escape_prefix=escape_prefix,
        min_interval_s=min_interval_s,
        state_ttl_s=state_ttl_s,
        max_state_per_instance=max_state_per_instance,
    )


# ─────────────────────────────────────────────────────────────────────────
# Parse layer
# ─────────────────────────────────────────────────────────────────────────


class TestParseSlashCommand:
    """WS-1 acceptance — parse layer."""

    def test_leading_slash_with_name_no_args(self):
        result = parse_slash_command("/compact")
        assert result.command == ParsedCommand(name="compact", args="")

    def test_leading_slash_with_name_and_args(self):
        result = parse_slash_command("/compact foo bar")
        assert result.command == ParsedCommand(name="compact", args="foo bar")

    def test_case_insensitive_name(self):
        # Uppercase input → canonical lowercase name.
        result = parse_slash_command("/COMPACT")
        assert result.command == ParsedCommand(name="compact", args="")

    def test_mixed_case_with_args(self):
        result = parse_slash_command("/Compact --force")
        assert result.command == ParsedCommand(name="compact", args="--force")

    def test_double_slash_escape_returns_sanitized_text(self):
        # O-B1: `//etc/hosts` → sanitize to `/etc/hosts`, no command.
        result = parse_slash_command("//etc/hosts")
        assert result.command is None
        assert result.sanitized_text == "/etc/hosts"

    def test_double_slash_with_space(self):
        result = parse_slash_command("//hello world")
        assert result.command is None
        assert result.sanitized_text == "/hello world"

    def test_triple_slash_is_command_with_extra_arg(self):
        # Only the configured escape prefix is treated as an escape;
        # `///` (prefix-of-prefix + slash) is `/` + `/compact` (args).
        result = parse_slash_command("///etc/hosts")
        # `///` starts with `//` → escape path → strip one `/` → `//etc/hosts`
        assert result.command is None
        assert result.sanitized_text == "//etc/hosts"

    def test_no_leading_slash_returns_none(self):
        result = parse_slash_command("hello")
        assert result.command is None
        assert result.sanitized_text is None

    def test_empty_string_returns_none(self):
        # S4 upstream handles the 400; parse is permissive.
        result = parse_slash_command("")
        assert result.command is None
        assert result.sanitized_text is None

    def test_slash_only_no_name_returns_none(self):
        result = parse_slash_command("/")
        assert result.command is None

    def test_slash_only_whitespace_returns_none(self):
        result = parse_slash_command("/   ")
        assert result.command is None

    def test_custom_escape_prefix(self):
        # Operator could configure a different escape prefix; the parser
        # must strip one char (len(prefix) - 1) to keep the rest verbatim.
        result = parse_slash_command("!!cmd", escape_prefix="!!")
        assert result.command is None
        assert result.sanitized_text == "!cmd"

    def test_custom_escape_prefix_with_slash_command(self):
        # Custom escape + the actual command flow still parses
        # normally when text doesn't start with the escape.
        result = parse_slash_command("/compact", escape_prefix="!!")
        assert result.command == ParsedCommand(name="compact", args="")

    def test_dispatcher_parse_uses_configured_prefix(self):
        d = _make_dispatcher(escape_prefix="!!")
        result = d.parse("!!etc/passwd")
        assert result.command is None
        # `!!` (2 chars) → strip 1 char → "!etc/passwd".
        assert result.sanitized_text == "!etc/passwd"


# ─────────────────────────────────────────────────────────────────────────
# CommandRegistry
# ─────────────────────────────────────────────────────────────────────────


class TestCommandRegistry:
    """WS-1 acceptance — registry shape."""

    def test_register_and_resolve(self):
        r = CommandRegistry()
        r.register(_make_spec())
        assert r.get("compact") is not None

    def test_resolve_is_case_insensitive(self):
        r = CommandRegistry()
        r.register(_make_spec())
        assert r.get("COMPACT") is not None
        assert r.get("Compact") is not None

    def test_unknown_returns_none(self):
        r = CommandRegistry()
        assert r.get("nope") is None

    def test_duplicate_registration_raises(self):
        # mirrors ``daemon.sources.registry.SourceRegistry.register`` —
        # duplicate-raise keeps startup misconfiguration loud.
        r = CommandRegistry()
        r.register(_make_spec("compact"))
        with pytest.raises(ValueError, match="already registered"):
            r.register(_make_spec("compact"))

    def test_duplicate_case_insensitive_raises(self):
        r = CommandRegistry()
        r.register(_make_spec("compact"))
        with pytest.raises(ValueError, match="already registered"):
            r.register(_make_spec("COMPACT"))

    def test_list_sorted(self):
        r = CommandRegistry()
        r.register(_make_spec("zeta"))
        r.register(_make_spec("alpha"))
        r.register(_make_spec("mu"))
        assert r.list() == ["alpha", "mu", "zeta"]

    def test_list_empty(self):
        r = CommandRegistry()
        assert r.list() == []

    def test_clear_drops_all(self):
        r = CommandRegistry()
        r.register(_make_spec("compact"))
        r.clear()
        assert r.get("compact") is None
        assert r.list() == []


# ─────────────────────────────────────────────────────────────────────────
# O10 state registry — active slot + terminal ring LRU + TTL
# ─────────────────────────────────────────────────────────────────────────


class TestCommandStateRegistry:
    """O10 registry — active slot per instance, ring LRU, TTL."""

    def _make(self, *, ttl=600, max_per=20) -> CommandStateRegistry:
        return CommandStateRegistry(state_ttl_s=ttl, max_state_per_instance=max_per)

    def test_record_start_creates_active(self):
        s = self._make()
        ac = s.record_start(
            instance_id="inst-A",
            command_id="cmd-1",
            command="compact",
            ttl_seconds=600,
        )
        assert s.get_active("inst-A") is ac
        assert ac.phase == "waiting"
        assert ac.phase_seq == 1

    def test_record_start_twice_for_same_instance_raises(self):
        s = self._make()
        s.record_start(
            instance_id="inst-A",
            command_id="cmd-1",
            command="compact",
            ttl_seconds=600,
        )
        with pytest.raises(RuntimeError, match="already has an active command"):
            s.record_start(
                instance_id="inst-A",
                command_id="cmd-2",
                command="compact",
                ttl_seconds=600,
            )

    def test_update_phase_increments_seq(self):
        s = self._make()
        s.record_start(
            instance_id="inst-A",
            command_id="cmd-1",
            command="compact",
            ttl_seconds=600,
        )
        updated = s.update_phase("inst-A", "cmd-1", "in_progress")
        assert updated is not None
        assert updated.phase == "in_progress"
        assert updated.phase_seq == 2

    def test_update_phase_with_no_active_returns_none(self):
        s = self._make()
        assert s.update_phase("inst-A", "cmd-1", "in_progress") is None

    def test_update_phase_stale_command_id_returns_none(self):
        s = self._make()
        s.record_start(
            instance_id="inst-A",
            command_id="cmd-1",
            command="compact",
            ttl_seconds=600,
        )
        assert s.update_phase("inst-A", "cmd-other", "in_progress") is None

    def test_terminalize_moves_to_ring(self):
        s = self._make()
        s.record_start(
            instance_id="inst-A",
            command_id="cmd-1",
            command="compact",
            ttl_seconds=600,
        )
        s.terminalize("inst-A", "cmd-1", phase="success")
        # Active slot cleared.
        assert s.get_active("inst-A") is None
        # GET endpoint still returns the terminal entry (within TTL).
        assert s.get_for_endpoint("inst-A") is not None
        assert s.get_for_endpoint("inst-A").phase == "success"

    def test_terminalize_twice_returns_none_second_time(self):
        s = self._make()
        s.record_start(
            instance_id="inst-A",
            command_id="cmd-1",
            command="compact",
            ttl_seconds=600,
        )
        assert s.terminalize("inst-A", "cmd-1", phase="success") is not None
        assert s.terminalize("inst-A", "cmd-1", phase="failed") is None

    def test_ring_lru_caps_at_max_state_per_instance(self):
        # WS-1 spec: daemon-wide ring LRU ≤ max_state_per_instance (20).
        s = self._make(max_per=3)
        for i in range(5):
            cmd_id = f"cmd-{i}"
            s.record_start(
                instance_id="inst-A",
                command_id=cmd_id,
                command="compact",
                ttl_seconds=600,
            )
            s.terminalize("inst-A", cmd_id, phase="success")
        # Ring holds the last 3.
        ring = s._ring["inst-A"]
        assert len(ring) == 3
        assert list(ring.keys()) == ["cmd-2", "cmd-3", "cmd-4"]
        # get_for_endpoint returns the newest (cmd-4).
        assert s.get_for_endpoint("inst-A").command_id == "cmd-4"

    def test_ttl_expiry_evicts_ring_entry(self):
        s = self._make(ttl=1)
        s.record_start(
            instance_id="inst-A",
            command_id="cmd-1",
            command="compact",
            ttl_seconds=1,
        )
        s.terminalize("inst-A", "cmd-1", phase="success")
        # Within TTL — present.
        assert s.get_for_endpoint("inst-A") is not None
        # After TTL — gone (lazy eviction).
        time.sleep(1.1)
        assert s.get_for_endpoint("inst-A") is None
        # The per-instance ring slice is emptied by the lazy-evict pass.
        assert s._ring.get("inst-A") in (None, {})  # lazy-evicted

    def test_ttl_expiry_multiple_expired_entries_does_not_mutate_during_iteration(self):
        """Defect #3 (2026-08-31 live gate) — GET /commands/active raised
        HTTP 500 ``RuntimeError: OrderedDict mutated during iteration``
        when the per-instance ring held multiple expired entries.

        Root cause: ``get_for_endpoint`` iterated
        ``reversed(ring.values())`` and the lazy-eviction
        ``ring.pop(...)`` mutated the same OrderedDict mid-iteration.
        CPython detects the size change and raises.

        Deterministic repro: terminalize multiple commands to populate
        the per-instance ring, then mark every entry expired by setting
        ``last_event_at`` backwards (no ``time.sleep`` so the test is
        reliable and finishes in <10ms). Old code raised
        ``RuntimeError`` here; new code returns ``None`` and the ring
        slice is cleaned.
        """
        s = self._make(ttl=1)
        # Need >=2 ring entries so the for-loop has at least one
        # iteration step AFTER the first pop() — the error fires on the
        # NEXT iteration when CPython detects the size change.
        for cmd_id in ("cmd-1", "cmd-2", "cmd-3"):
            s.record_start(
                instance_id="inst-A",
                command_id=cmd_id,
                command="compact",
                ttl_seconds=1,
            )
            s.terminalize("inst-A", cmd_id, phase="success")
        ring = s._ring["inst-A"]
        assert len(ring) == 3
        # Force-expire every entry deterministically — wall-clock pin
        # makes the test independent of system scheduling jitter.
        past = time.monotonic() - 100.0  # 100s ago → way past TTL=1
        for entry in ring.values():
            entry.last_event_at = past
        # The call that crashed on the old code. Must NOT raise.
        result = s.get_for_endpoint("inst-A")
        # All expired → endpoint reports "no command".
        assert result is None
        # Lazy-evict pass emptied the per-instance ring slice.
        assert s._ring.get("inst-A") in (None, {})

    def test_get_for_endpoint_prefers_newest_unexpired_with_expired_older(self):
        """Companion to the defect-#3 fix — confirms the snapshot-iterate
        pattern preserves the "newest wins" semantic when an older
        expired entry sits beside a fresh one in the ring.

        Iterates ``reversed(values)`` → newest first → returns on the
        first FRESH hit, never touches the older expired entry on this
        call (a future call will evict it lazily once it also expires).
        The point is: no exception + correct ordering.
        """
        s = self._make(ttl=10)
        # cmd-1 — old, marked expired below.
        s.record_start(
            instance_id="inst-A",
            command_id="cmd-1",
            command="compact",
            ttl_seconds=10,
        )
        s.terminalize("inst-A", "cmd-1", phase="success")
        # cmd-2 — newest, stays fresh.
        s.record_start(
            instance_id="inst-A",
            command_id="cmd-2",
            command="compact",
            ttl_seconds=10,
        )
        s.terminalize("inst-A", "cmd-2", phase="success")
        # Force-expire ONLY cmd-1.
        s._ring["inst-A"]["cmd-1"].last_event_at = time.monotonic() - 100.0
        # Newest entry (cmd-2) wins — no exception.
        ac = s.get_for_endpoint("inst-A")
        assert ac is not None
        assert ac.command_id == "cmd-2"

    def test_get_for_endpoint_prefers_active_over_ring(self):
        s = self._make()
        s.record_start(
            instance_id="inst-A",
            command_id="cmd-1",
            command="compact",
            ttl_seconds=600,
        )
        s.terminalize("inst-A", "cmd-1", phase="success")
        # Start a NEW command — active wins over the terminal ring.
        s.record_start(
            instance_id="inst-A",
            command_id="cmd-2",
            command="compact",
            ttl_seconds=600,
        )
        ac = s.get_for_endpoint("inst-A")
        assert ac is not None
        assert ac.command_id == "cmd-2"

    def test_evict_instance_drops_active_and_ring(self):
        s = self._make()
        s.record_start(
            instance_id="inst-A",
            command_id="cmd-1",
            command="compact",
            ttl_seconds=600,
        )
        s.terminalize("inst-A", "cmd-1", phase="success")
        s.record_start(
            instance_id="inst-B",
            command_id="cmd-2",
            command="compact",
            ttl_seconds=600,
        )
        s.evict_instance("inst-A")
        # inst-A is fully gone.
        assert s.get_for_endpoint("inst-A") is None
        assert "inst-A" not in s._ring
        assert "inst-A" not in s._active
        # inst-B unaffected.
        assert s.get_active("inst-B") is not None

    def test_evict_unknown_instance_is_noop(self):
        s = self._make()
        s.evict_instance("ghost")
        # No exception; nothing inserted.


# ─────────────────────────────────────────────────────────────────────────
# Rate-limit state + dispatch ordering
# ─────────────────────────────────────────────────────────────────────────


class TestRateLimitAndOrdering:
    """WS-1.4 + WS-6 — rate-limit BEFORE ExecutionGate acquisition."""

    async def test_inflight_command_returns_busy(self):
        d = _make_dispatcher(min_interval_s=0)
        d.registry.register(_make_spec(handler=_hanging_handler))
        # First dispatch — accepted, handler hangs.
        first = await d.dispatch(
            "inst-A", "/compact", pending_injections=0
        )
        assert first.kind == "ack"
        assert first.ack["state"] == "accepted"
        # Second dispatch while in-flight — busy.
        second = await d.dispatch(
            "inst-A", "/compact", pending_injections=0
        )
        assert second.kind == "ack"
        assert second.ack["state"] == "rejected"
        assert second.ack["reason"] == RejectionReason.BUSY.value
        # Cleanup — let the hanging handler finish.
        for task in list(d._tasks):
            task.cancel()
        for task in list(d._tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_min_interval_returns_rate_limited(self):
        # min_interval_s=10 → second POST within window → rate_limited.
        d = _make_dispatcher(min_interval_s=10)
        d.registry.register(_make_spec())
        first = await d.dispatch("inst-A", "/compact")
        assert first.ack["state"] == "accepted"
        # Drain the bg task so the in-flight slot clears; only the
        # min-interval guard should fire on the second dispatch.
        for task in list(d._tasks):
            await task
        assert d._tasks == set()
        second = await d.dispatch("inst-A", "/compact")
        assert second.ack["state"] == "rejected"
        assert second.ack["reason"] == RejectionReason.RATE_LIMITED.value

    async def test_min_interval_resets_after_window(self):
        # min_interval_s=0 → no interval guard → second dispatch is
        # accepted once the in-flight slot clears.
        d = _make_dispatcher(min_interval_s=0)
        d.registry.register(_make_spec())
        first = await d.dispatch("inst-A", "/compact")
        assert first.ack["state"] == "accepted"
        # Drain the bg task so the in-flight slot clears.
        for task in list(d._tasks):
            await task
        assert d._tasks == set()
        second = await d.dispatch("inst-A", "/compact")
        assert second.ack["state"] == "accepted"

    async def test_rate_limited_never_acquires_gate(self):
        """Architect §1 / plan 1.4 LOAD-BEARING ordering test.

        We instrument the dispatcher so a rate-limited request cannot
        reach the handler — if it ever did, the handler would mark the
        command active in the registry. We assert the registry stays
        empty after a rate-limited request, AND the bg task set stays
        empty.
        """
        d = _make_dispatcher(min_interval_s=10)
        handler_started = asyncio.Event()

        async def _spy_handler(*, instance_id, args, command_id, context):
            handler_started.set()
            await _hanging_handler(
                instance_id=instance_id,
                args=args,
                command_id=command_id,
                context=context,
            )

        d.registry.register(_make_spec(handler=_spy_handler))

        first = await d.dispatch("inst-A", "/compact")
        assert first.ack["state"] == "accepted"
        # Wait for the handler to actually start before sending the
        # second request — ensures the in-flight guard sees the slot
        # taken AND the min-interval guard fires.
        await asyncio.wait_for(handler_started.wait(), timeout=1.0)

        # Snapshot task count BEFORE second dispatch.
        tasks_before = len(d._tasks)

        # Second dispatch — must be rejected before any handler spawn.
        second = await d.dispatch("inst-A", "/compact")
        assert second.ack["state"] == "rejected"
        # The reason could be BUSY (in-flight) or RATE_LIMITED
        # (min-interval); both prove the gate-acquisition path was
        # never reached.
        assert second.ack["reason"] in (
            RejectionReason.BUSY.value,
            RejectionReason.RATE_LIMITED.value,
        )
        # Crucially, NO new bg task was spawned.
        assert len(d._tasks) == tasks_before, (
            f"Rate-limited dispatch spawned a handler task; "
            f"tasks before={tasks_before} after={len(d._tasks)}. "
            "Plan 1.4 ordering violated."
        )

        # Cleanup.
        for task in list(d._tasks):
            task.cancel()
        for task in list(d._tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_min_interval_alone_returns_rate_limited(self):
        """If no command is in-flight but window hasn't elapsed, still
        rate_limited — and still no handler task spawned."""
        d = _make_dispatcher(min_interval_s=10)
        d.registry.register(_make_spec())
        first = await d.dispatch("inst-A", "/compact")
        assert first.ack["state"] == "accepted"
        # Drain the bg task so the in-flight slot clears; only the
        # min-interval guard should fire next.
        for task in list(d._tasks):
            await task
        assert d._tasks == set()
        # Min-interval is still active.
        second = await d.dispatch("inst-A", "/compact")
        assert second.ack["state"] == "rejected"
        assert second.ack["reason"] == RejectionReason.RATE_LIMITED.value
        # No new task — gate-acquisition was bypassed.
        assert d._tasks == set()


# ─────────────────────────────────────────────────────────────────────────
# Full dispatch flow + executor-coordination seams
# ─────────────────────────────────────────────────────────────────────────


class TestDispatchFlow:
    """WS-1 end-to-end dispatch flow."""

    async def test_passthrough_when_disabled(self):
        d = _make_dispatcher(enabled=False)
        d.registry.register(_make_spec())
        outcome = await d.dispatch("inst-A", "/compact")
        assert outcome.kind == "passthrough"
        assert outcome.sanitized_text is None

    async def test_passthrough_when_no_leading_slash(self):
        d = _make_dispatcher()
        d.registry.register(_make_spec())
        outcome = await d.dispatch("inst-A", "hello")
        assert outcome.kind == "passthrough"

    async def test_passthrough_with_sanitized_text_for_escape(self):
        d = _make_dispatcher(escape_prefix="//")
        outcome = await d.dispatch("inst-A", "//etc/hosts")
        assert outcome.kind == "passthrough"
        assert outcome.sanitized_text == "/etc/hosts"

    async def test_unknown_command_returns_unknown_outcome(self):
        d = _make_dispatcher()
        # Registry is empty.
        outcome = await d.dispatch("inst-A", "/foo")
        assert outcome.kind == "unknown_command"
        assert outcome.available == []

    async def test_unknown_command_lists_available(self):
        d = _make_dispatcher()
        d.registry.register(_make_spec("compact"))
        d.registry.register(_make_spec("status"))
        outcome = await d.dispatch("inst-A", "/foo")
        assert outcome.kind == "unknown_command"
        assert outcome.available == ["compact", "status"]

    async def test_accepted_ack_shape(self):
        d = _make_dispatcher(state_ttl_s=300)
        d.registry.register(_make_spec())
        outcome = await d.dispatch("inst-A", "/compact")
        assert outcome.kind == "ack"
        ack = outcome.ack
        assert ack["status"] == "command"
        assert ack["command"] == "compact"
        assert ack["state"] == "accepted"
        assert ack["reason"] is None
        assert ack["detail"] is None
        assert ack["ttl_seconds"] == 300
        assert isinstance(ack["command_id"], str) and len(ack["command_id"]) > 0
        assert isinstance(ack["timestamp"], str)

    async def test_pending_injections_guard(self):
        d = _make_dispatcher()
        d.registry.register(_make_spec())
        outcome = await d.dispatch(
            "inst-A", "/compact", pending_injections=2
        )
        assert outcome.kind == "ack"
        assert outcome.ack["state"] == "rejected"
        assert outcome.ack["reason"] == RejectionReason.PENDING_INJECTIONS.value

    async def test_handler_runs_and_terminalizes(self):
        """End-to-end: dispatch → handler runs → terminal phase."""
        d = _make_dispatcher()
        d.registry.register(_make_spec())
        outcome = await d.dispatch("inst-A", "/compact")
        assert outcome.kind == "ack"
        command_id = outcome.ack["command_id"]
        # Wait for the bg task to finish.
        for task in list(d._tasks):
            await task
        # Active slot is gone (handler terminalized).
        assert d.state._active.get("inst-A") is None
        # Ring holds the terminal entry — GET endpoint sees it.
        terminal = d.state._ring["inst-A"][command_id]
        assert terminal.phase == CommandPhase.SUCCESS.value
        assert terminal.detail == {"reason": "noop_test"}
        # get_active (which falls back to ring within TTL) sees it too.
        ac = d.get_for_endpoint("inst-A")
        assert ac is not None
        assert ac.command_id == command_id

    async def test_handler_crash_forces_failed_terminal(self):
        """O9 / WS-6: handler crashes → forced FAILED phase."""
        d = _make_dispatcher()
        d.registry.register(_make_spec(handler=_crashing_handler))
        outcome = await d.dispatch("inst-A", "/compact")
        assert outcome.kind == "ack"
        command_id = outcome.ack["command_id"]
        for task in list(d._tasks):
            try:
                await task
            except Exception:
                pass
        # GET endpoint sees FAILED terminal (within TTL).
        ac = d.get_for_endpoint("inst-A")
        assert ac is not None
        assert ac.command_id == command_id
        assert ac.phase == CommandPhase.FAILED.value
        assert ac.detail == {"failure_kind": "error", "reason": "RuntimeError"}

    async def test_handler_return_without_terminalize_forces_failed(self):
        """Handler bug safety net — handler returns without terminalize."""
        async def _no_terminal(*, instance_id, args, command_id, context):
            # Do nothing — pretend we forgot.
            return None

        d = _make_dispatcher()
        d.registry.register(_make_spec(handler=_no_terminal))
        await d.dispatch("inst-A", "/compact")
        for task in list(d._tasks):
            await task
        # Forced FAILED so the GET endpoint doesn't hang on "waiting".
        ac = d.get_for_endpoint("inst-A")
        assert ac is not None
        assert ac.phase == CommandPhase.FAILED.value
        assert ac.detail == {"reason": "handler_returned_without_terminalize"}

    async def test_handler_can_update_phase_then_terminalize(self):
        """WS-2 contract: handler drives phase updates via context."""
        phases = []

        async def _phase_handler(*, instance_id, args, command_id, context):
            await context.update_phase(CommandPhase.IN_PROGRESS.value)
            phases.append("in_progress")
            await context.update_phase(
                CommandPhase.IN_PROGRESS.value,
                detail={"tokens_before": 1000},
                bump_seq=True,
            )
            phases.append("in_progress_2")
            await context.terminalize(
                CommandPhase.SUCCESS.value,
                detail={"tokens_after": 200},
            )

        d = _make_dispatcher()
        d.registry.register(_make_spec(handler=_phase_handler))
        outcome = await d.dispatch("inst-A", "/compact")
        command_id = outcome.ack["command_id"]
        for task in list(d._tasks):
            await task
        terminal = d.state._ring["inst-A"][command_id]
        # phase_seq started at 1 (record_start), +1 (in_progress),
        # +1 (in_progress with detail), +1 (terminalize). The final
        # phase_seq captured at GET is the post-terminalize value.
        assert terminal.phase == CommandPhase.SUCCESS.value
        assert terminal.detail == {"tokens_after": 200}
        assert phases == ["in_progress", "in_progress_2"]


# ─────────────────────────────────────────────────────────────────────────
# Eviction + lifecycle integration
# ─────────────────────────────────────────────────────────────────────────


class TestDispatcherLifecycle:
    """evict_instance + state cleanup mirrors manager._cleanup_instance_state."""

    async def test_evict_instance_clears_inflight_and_state(self):
        d = _make_dispatcher()
        d.registry.register(_make_spec(handler=_hanging_handler))
        outcome = await d.dispatch("inst-A", "/compact")
        assert outcome.kind == "ack"
        assert d._is_inflight("inst-A")
        # Eviction clears every per-instance state row.
        d.evict_instance("inst-A")
        assert not d._is_inflight("inst-A")
        assert d.get_active("inst-A") is None

    async def test_get_active_after_dispatch_and_completion(self):
        d = _make_dispatcher()
        d.registry.register(_make_spec())
        await d.dispatch("inst-A", "/compact")
        for task in list(d._tasks):
            await task
        # Active command terminalized — still visible via
        # get_for_endpoint (within TTL) for SSE-loss recovery.
        ac = d.get_for_endpoint("inst-A")
        assert ac is not None
        assert ac.phase == CommandPhase.SUCCESS.value

    def test_get_active_when_empty(self):
        d = _make_dispatcher()
        # No active command — get_active returns None. The TTL-aware
        # fallback ``get_for_endpoint`` also returns None for an
        # instance with no history.
        assert d.get_active("ghost") is None
        assert d.get_for_endpoint("ghost") is None


# ─────────────────────────────────────────────────────────────────────────
# W-3.4 — bg-task cancellation drops BOTH slots (no phantom busy)
# ─────────────────────────────────────────────────────────────────────────


class TestHandlerCancellationDropsSlots:
    """W-3.4 — cancelling an in-flight command task drops BOTH the
    ``_inflight`` slot AND the state registry's active entry
    (``command_dispatcher.py`` ``_run_handler`` CancelledError branch),
    so the GET endpoint returns ``exists:false`` instead of a phantom
    "stuck busy" command.

    Drives the REAL ``dispatch`` → ``asyncio.create_task(_run_handler)``
    background-task path: the hanging handler is cancelled mid-flight;
    ``_run_handler`` catches ``asyncio.CancelledError``, pops both
    rows, and re-raises. The cancel path deliberately does NOT move
    the entry to the terminal ring (transient state; restart resets),
    so even the TTL-aware ``get_for_endpoint`` fallback finds nothing.
    """

    async def test_cancel_drops_inflight_and_active(self):
        handler_started = asyncio.Event()

        async def _spy_started_handler(*, instance_id, args, command_id, context):
            handler_started.set()
            await _hanging_handler(
                instance_id=instance_id,
                args=args,
                command_id=command_id,
                context=context,
            )

        d = _make_dispatcher(min_interval_s=0)
        d.registry.register(_make_spec(handler=_spy_started_handler))

        outcome = await d.dispatch("inst-A", "/compact")
        assert outcome.kind == "ack"
        assert outcome.ack["state"] == "accepted"
        command_id = outcome.ack["command_id"]

        # Wait until the handler is GENUINELY mid-flight before
        # cancelling — cancelling before the task's first step would
        # skip ``_run_handler``'s body entirely (the coroutine is
        # never entered) and the W-3.4 branch would not be driven.
        await asyncio.wait_for(handler_started.wait(), timeout=1.0)

        # Pre-condition: while the handler hangs, BOTH rows exist.
        assert d._is_inflight("inst-A")
        active = d._state._active.get("inst-A")
        assert active is not None
        assert active.command_id == command_id
        assert d.get_active("inst-A") is active

        # Cancel the bg task and let ``_run_handler``'s
        # CancelledError branch run (it re-raises after cleanup).
        tasks = list(d._tasks)
        assert tasks, "dispatch must have spawned a bg task"
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass  # expected — _run_handler re-raises after cleanup

        # BOTH rows dropped (W-3.4 binding).
        assert not d._inflight.get("inst-A"), (
            "cancelled command must drop the _inflight slot"
        )
        assert d._state._active.get("inst-A") is None, (
            "cancelled command must drop the state registry's active entry"
        )

        # GET-equivalent: exists:false afterward. ``get_active`` is
        # the active-only accessor; ``get_for_endpoint`` is what the
        # GET route serializes — its None → ``{exists: false}``.
        assert d.get_active("inst-A") is None
        assert d.get_for_endpoint("inst-A") is None


# ─────────────────────────────────────────────────────────────────────────
# WS-5 ack envelope + WS-6 ordering test (more granular)
# ─────────────────────────────────────────────────────────────────────────


class TestAckEnvelope:
    """WS-5 normative CommandAck shape (architect §7)."""

    async def test_accepted_envelope_keys(self):
        d = _make_dispatcher(state_ttl_s=600)
        d.registry.register(_make_spec())
        outcome = await d.dispatch("inst-A", "/compact")
        ack = outcome.ack
        # Exact key set per WS-5 spec.
        assert set(ack.keys()) == {
            "status",
            "command",
            "command_id",
            "state",
            "reason",
            "detail",
            "timestamp",
            "ttl_seconds",
        }
        assert ack["status"] == "command"
        assert ack["state"] == "accepted"
        assert ack["ttl_seconds"] == 600

    async def test_rejected_envelope_keys(self):
        d = _make_dispatcher()
        d.registry.register(_make_spec())
        outcome = await d.dispatch("inst-A", "/compact", pending_injections=3)
        ack = outcome.ack
        assert set(ack.keys()) == {
            "status",
            "command",
            "command_id",
            "state",
            "reason",
            "detail",
            "timestamp",
            "ttl_seconds",
        }
        assert ack["state"] == "rejected"
        assert ack["command_id"] is None  # never minted
        assert ack["reason"] == RejectionReason.PENDING_INJECTIONS.value
        assert ack["detail"]  # human guidance populated

    async def test_command_id_is_uuidv4_format(self):
        d = _make_dispatcher()
        d.registry.register(_make_spec())
        outcome = await d.dispatch("inst-A", "/compact")
        # UUIDv4 = 36 chars with dashes, version digit 4.
        cid = outcome.ack["command_id"]
        assert len(cid) == 36
        assert cid[14] == "4"


# ─────────────────────────────────────────────────────────────────────────
# Enums — pin the closed six-value rejection-reason + phase set
# ─────────────────────────────────────────────────────────────────────────


class TestEnums:
    def test_rejection_reasons_pinned_at_seven(self):
        # W-2.5 (leader-approved 2026-08-31) — the enum gained the
        # 7th value ``unavailable`` for the per-agent availability
        # predicate path (O-B6). The 6-value pin is OBSOLETE; we
        # now pin at 7.
        assert {r.value for r in RejectionReason} == {
            "terminal_instance",
            "busy",
            "rate_limited",
            "pending_injections",
            "unavailable",  # W-2.5
            "compaction_disabled",
            "quiescence_timeout",
        }

    def test_rejection_reasons_unique(self):
        # Belt-and-braces — every enum value is unique (a regression
        # that duplicated a value would silently pass the set-equality
        # check above).
        values = [r.value for r in RejectionReason]
        assert len(values) == len(set(values)), (
            f"RejectionReason values must be unique; got {values!r}"
        )

    def test_availability_predicate_emits_unavailable(self):
        """W-2.5 — when ``spec.availability`` returns False, the
        dispatch surfaces ``reason=unavailable`` (NOT the historical
        ``pending_injections`` placeholder). The pending_injections
        guard (step 5) remains its own independent check."""
        async def _deny_availability(instance_context):
            return False

        d = _make_dispatcher()
        d.registry.register(
            _make_spec(availability=_deny_availability)
        )

        outcome = asyncio.run(d.dispatch("inst-A", "/compact"))
        assert outcome.kind == "ack"
        assert outcome.ack["state"] == "rejected"
        assert outcome.ack["reason"] == RejectionReason.UNAVAILABLE.value, (
            f"W-2.5: availability predicate False must surface as "
            f"'unavailable'; got {outcome.ack['reason']!r} (the previous "
            "implementation used pending_injections as a placeholder)."
        )
        assert "not available" in outcome.ack["detail"].lower()

    def test_pending_injections_guard_independent(self):
        """W-2.5 — the pending_injections guard (step 5) is its own
        check, not the placeholder for unavailable. A non-empty
        pending_injections queue still rejects with
        reason=pending_injections (the original WS-6 row), distinct
        from the new ``unavailable`` path.
        """
        d = _make_dispatcher()
        d.registry.register(_make_spec())

        outcome = asyncio.run(
            d.dispatch("inst-A", "/compact", pending_injections=2)
        )
        assert outcome.kind == "ack"
        assert outcome.ack["state"] == "rejected"
        assert outcome.ack["reason"] == RejectionReason.PENDING_INJECTIONS.value, (
            "pending_injections guard must surface as "
            "'pending_injections' (NOT 'unavailable'); the two are "
            "distinct paths after W-2.5"
        )

    def test_phases_include_six_core(self):
        # WS-5 phase machine — 6 phases.
        assert {p.value for p in CommandPhase} == {
            "waiting",
            "in_progress",
            "success",
            "timed_out",
            "fallback_applied",
            "failed",
        }

    def test_compacted_type_includes_partial_summary(self):
        # C1 amendment — enum gained `partial_summary`.
        assert {c.value for c in CompactedType} == {
            "summary",
            "partial_summary",
            "truncation",
            "noop",
        }

    def test_noop_reason_values(self):
        # Cycle 2 (proactive-compaction-fix review W-4) —
        # ``injections_dominate`` is added to ``NoopReason`` so
        # the engine ``skipped_injections_dominate`` value can be
        # mapped to a FE-enum-compatible noop reason. The
        # ``CompactedType`` enum stays
        # ``{summary, partial_summary, truncation, noop}`` —
        # the W-4 fix is on the noop REASON side, not the
        # noop TYPE side.
        #
        # Cycle 3 (proactive-compaction-fix residual W-4.5) —
        # ``preserved_within_threshold`` is added to mirror the
        # engine's emergency-bail emitter
        # (``skipped_preserved_within_threshold``,
        # ``daemon/compaction.py:2129-2138``). Without this enum
        # member, the executor's wire mapping has no FE-enum-
        # compatible value to stamp on the user-facing /compact
        # no-op, and the raw engine string leaks through the wire
        # (the pre-cycle-3 W-4 enum-contract residual). The
        # ``CompactedType`` enum stays unchanged — the W-4.5
        # fix is on the noop REASON side, same shape as cycle 2.
        assert {n.value for n in NoopReason} == {
            "below_floor",
            "recently_compacted",
            "too_few_messages",
            "injections_dominate",
            "preserved_within_threshold",
        }


# ─────────────────────────────────────────────────────────────────────────
# Defect #2 (2026-08-31 e2e gate) — terminal-instance rejection AT ACK TIME
# ─────────────────────────────────────────────────────────────────────────


class TestTerminalStatusAckRejection:
    """Defect #2 — the instance-status gate belongs at ACK time.

    Live evidence (tester gate 2026-08-31, command d34c1026): a
    terminal instance acked ``accepted`` → the FE seeded its waiting
    card → the bg executor terminalized ``failed(terminal_instance)``
    ~3ms later — a terminal event the client could not observe
    (emitted before the post-ack subscription). Architect §5/§6/§7:
    rejected statuses answer with ``reason=terminal_instance`` + the
    W-1.2 pinned guidance copy, as a SYNC 200 ack envelope.

    compact-on-COMPLETED (2026-08-31): the gate rejects ONLY the
    ``COMPACT_REJECT_STATUSES`` triple (terminated / error / failed).
    ``completed`` is compact-eligible — it proceeds past the gate to
    acceptance (C1 Variant A persist keeps ``next=()``; revive-on-send
    runs the agent) — while remaining terminal for every other
    consumer of ``daemon.constants.TERMINAL_INSTANCE_STATUSES``.

    The gate is a DISPATCH-TIME guard (cheap, synchronous): it fires
    BEFORE ``record_start`` so no ``command_id`` is minted, no
    in-flight slot is taken, and NO background task is spawned. The
    executor keeps its own terminal guard as defense-in-depth for
    the TOCTOU window (instance terminalizes between the router's
    status read and the bg task's start).
    """

    COMPACT_REJECT_STATUSES = ("terminated", "error", "failed")
    GUIDANCE = "Send a message to start a new turn, then /compact."

    def _spy_handler_calls(self):
        calls = []

        async def _spy(*, instance_id, args, command_id, context):
            calls.append(command_id)

        return _spy, calls

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", COMPACT_REJECT_STATUSES)
    async def test_each_terminal_status_rejects_at_dispatch(
        self, status
    ):
        """Every COMPACT_REJECT_STATUSES status → 200 ack envelope,
        state=rejected."""
        handler, calls = self._spy_handler_calls()
        d = _make_dispatcher()
        d.registry.register(_make_spec(handler=handler))

        outcome = await d.dispatch(
            "inst-A", "/compact", instance_status=status
        )

        assert outcome.kind == "ack"
        ack = outcome.ack
        assert ack["status"] == "command"
        assert ack["command"] == "compact"
        assert ack["state"] == "rejected"
        assert ack["reason"] == RejectionReason.TERMINAL_INSTANCE.value
        assert ack["detail"] == self.GUIDANCE, (
            "the guidance copy must be the W-1.2 pinned string (§5), "
            "reused verbatim from the executor's defense-in-depth guard"
        )
        # §7 schema: a rejected ack carries NO command_id — none was
        # minted because record_start never ran.
        assert ack["command_id"] is None
        assert ack["timestamp"]
        assert ack["ttl_seconds"] == 600

    @pytest.mark.asyncio
    async def test_completed_passes_the_terminal_gate(self):
        """compact-on-COMPLETED (2026-08-31): ``completed`` is
        compact-eligible — it proceeds PAST the instance-status gate
        to a normal accepted dispatch (record_start runs, a
        command_id is minted, the handler task is spawned). The
        instance row remains terminal for every other consumer of
        ``daemon.constants.TERMINAL_INSTANCE_STATUSES``; only /compact
        falls through."""
        handler, calls = self._spy_handler_calls()
        d = _make_dispatcher()
        d.registry.register(_make_spec(handler=handler))

        outcome = await d.dispatch(
            "inst-A", "/compact", instance_status="completed"
        )

        assert outcome.kind == "ack"
        ack = outcome.ack
        assert ack["state"] == "accepted", (
            "completed must be compact-eligible — the gate rejects "
            f"only {self.COMPACT_REJECT_STATUSES}"
        )
        assert ack["command"] == "compact"
        assert ack["command_id"], "accepted ack mints a command_id"
        assert ack["ttl_seconds"] == 600
        assert ack["timestamp"]
        # record_start ran → the active slot is taken and the bg
        # handler task exists.
        assert d._state.get_active("inst-A") is not None
        assert len(d._tasks) == 1
        for task in list(d._tasks):
            task.cancel()

    @pytest.mark.asyncio
    async def test_no_background_task_spawned_for_terminal(self):
        """No bg task, no handler invocation, no in-flight slot, no
        active registry entry — the rejection is pure dispatch-time."""
        handler, calls = self._spy_handler_calls()
        d = _make_dispatcher()
        d.registry.register(_make_spec(handler=handler))

        outcome = await d.dispatch(
            "inst-A", "/compact", instance_status="terminated"
        )

        assert outcome.ack["state"] == "rejected"
        # Give the loop a chance to run anything mistakenly spawned.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert d._tasks == set(), "no background task may be spawned"
        assert calls == [], "handler must never run"
        assert "inst-A" not in d._inflight
        assert d._state.get_active("inst-A") is None
        assert d.get_for_endpoint("inst-A") is None, (
            "nothing was recorded — GET /commands/active returns "
            "exists:false (no phantom card)"
        )

    @pytest.mark.asyncio
    async def test_non_terminal_statuses_still_accept(self):
        """IDLE / RUNNING / PAUSED / WAITING_CHILDREN are NOT terminal —
        the gate must not over-reject. compact-on-COMPLETED (2026-08-31):
        ``completed`` joins the accepted set — compact-eligible, the
        gate rejects only terminated / error / failed."""
        handler, calls = self._spy_handler_calls()
        for status in (
            "idle", "running", "paused", "waiting_children", "completed"
        ):
            d = _make_dispatcher()
            d.registry.register(_make_spec(handler=handler))
            outcome = await d.dispatch(
                "inst-A", "/compact", instance_status=status
            )
            assert outcome.kind == "ack", status
            assert outcome.ack["state"] == "accepted", (
                f"status={status!r} is not terminal; must accept"
            )
            for task in list(d._tasks):
                task.cancel()

    @pytest.mark.asyncio
    async def test_status_gate_is_case_and_space_tolerant(self):
        """Status strings arrive from the router's instance_info —
        normalize defensively (strip + lower)."""
        handler, calls = self._spy_handler_calls()
        d = _make_dispatcher()
        d.registry.register(_make_spec(handler=handler))
        outcome = await d.dispatch(
            "inst-A", "/compact", instance_status="  TERMINATED "
        )
        assert outcome.ack["state"] == "rejected"
        assert outcome.ack["reason"] == "terminal_instance"

    @pytest.mark.asyncio
    async def test_instance_status_none_keeps_legacy_behavior(self):
        """Back-compat: callers that don't pass ``instance_status``
        (older tests, future commands without a status source) get the
        pre-defect-#2 behavior — the gate is silent on None."""
        handler, calls = self._spy_handler_calls()
        d = _make_dispatcher()
        d.registry.register(_make_spec(handler=handler))
        outcome = await d.dispatch("inst-A", "/compact")
        assert outcome.ack["state"] == "accepted"
        for task in list(d._tasks):
            task.cancel()

    @pytest.mark.asyncio
    async def test_terminal_rejection_precedes_other_guards(self):
        """A terminal instance answers terminal_instance even when a
        command is ALSO in-flight-busy — the terminal state is the
        more fundamental fact about the instance.

        (Reviewer note: this test leaks ``_inflight["inst-A"]`` from
        the first accepted dispatch; it is cleared by the cancelled
        hanging handler's cancel-path cleanup when the tasks are
        cancelled below — fixture behavior, not asserted.)
        """
        d = _make_dispatcher()
        d.registry.register(_make_spec(handler=_hanging_handler))

        # Put the instance into in-flight state via a normal dispatch
        # on a RUNNING instance (hanging handler keeps it active).
        busy_setup = await d.dispatch(
            "inst-A", "/compact", instance_status="running"
        )
        assert busy_setup.ack["state"] == "accepted"

        # A second dispatch on the busy instance (still RUNNING) is
        # BUSY — baseline.
        busy = await d.dispatch(
            "inst-A", "/compact", instance_status="running"
        )
        assert busy.ack["state"] == "rejected"
        assert busy.ack["reason"] == RejectionReason.BUSY.value

        # Same dispatcher state, but the instance is now terminal:
        # terminal wins over busy — the instance cannot compact,
        # period. (compact-on-COMPLETED: ``terminated`` carries the
        # terminal-beats-busy invariant; ``completed`` no longer
        # rejects at all.)
        terminal = await d.dispatch(
            "inst-A", "/compact", instance_status="terminated"
        )
        assert terminal.ack["state"] == "rejected"
        assert terminal.ack["reason"] == (
            RejectionReason.TERMINAL_INSTANCE.value
        ), "terminal beats busy: the instance cannot compact, period"
        for task in list(d._tasks):
            task.cancel()

    @pytest.mark.asyncio
    async def test_terminal_rejection_does_not_burn_rate_limit(self):
        """A rejected dispatch never stamps ``_last_dispatch`` — a user
        retrying /compact on a terminal instance must not trip the
        min-interval guard."""
        handler, calls = self._spy_handler_calls()
        d = _make_dispatcher(min_interval_s=10)
        d.registry.register(_make_spec(handler=handler))

        first = await d.dispatch(
            "inst-A", "/compact", instance_status="terminated"
        )
        second = await d.dispatch(
            "inst-A", "/compact", instance_status="terminated"
        )
        assert first.ack["reason"] == "terminal_instance"
        assert second.ack["reason"] == "terminal_instance", (
            "retry after a terminal rejection must still surface the "
            "terminal reason (rejections don't consume the min-interval)"
        )
        assert "inst-A" not in d._last_dispatch
