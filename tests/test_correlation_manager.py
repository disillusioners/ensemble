"""Comprehensive unit tests for CorrelationManager.

Covers all 5 functional areas:
  1. Register / Resolve / Callback flow
  2. had_error flag and terminal status (Fix N2)
  3. Per-parent lock serialization
  4. rebuild_from_db with real UUIDs (Fix N1)
  5. Shadow-mode comparison and rate-limited logging

All external dependencies (instance_repository, message_queue_repository,
EventBus) are mocked — no daemon is started.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.services.correlation_manager import (
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_RESPONDED,
    CorrelationManager,
    ParentCorrelation,
    PendingResponse,
)


# =============================================================================
# Mock helpers
# =============================================================================


def make_instance(
    instance_id: str,
    waiting_for: int = 0,
    status: str = "running",
    parent_id: str | None = None,
) -> MagicMock:
    """Build a minimal mock Instance with the attributes CM reads."""
    inst = MagicMock(name=f"Instance({instance_id})")
    inst.instance_id = instance_id
    inst.waiting_for = waiting_for
    inst.status = status
    inst.parent_id = parent_id
    return inst


def make_message(message_id: str, instance_id: str) -> MagicMock:
    """Build a minimal mock MessageQueue row."""
    msg = MagicMock(name=f"Msg({message_id})")
    msg.message_id = message_id
    msg.instance_id = instance_id
    return msg


def make_instance_repo(
    *,
    instances: list[Any] | None = None,
    children_by_parent: dict[str, list[Any]] | None = None,
    instance_by_id: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock SQLModelInstanceRepository.

    - ``list(...)`` returns ``(instances, len(instances))``.
    - ``get_all_with_waiting_for()`` returns the subset of ``instances`` whose
      ``waiting_for`` is > 0 (mirrors the production repository method).
    - ``get_children(parent_id)`` returns the children map (or []).
    - ``get(instance_id)`` returns the instance or None.
    """
    repo = MagicMock(name="InstanceRepo")
    instances = instances or []
    repo.list = MagicMock(return_value=(list(instances), len(instances)))
    repo.get_all_with_waiting_for = MagicMock(
        return_value=[i for i in instances if getattr(i, "waiting_for", 0) > 0]
    )
    repo.get_children = MagicMock(
        side_effect=lambda pid: list((children_by_parent or {}).get(pid, []))
    )
    by_id = instance_by_id or {}
    repo.get = MagicMock(side_effect=lambda iid: by_id.get(iid))
    return repo


def make_msg_repo(
    *,
    msgs_by_status_and_instance: dict[tuple[str, str], list[Any]] | None = None,
) -> MagicMock:
    """Build a mock SQLModelMessageQueueRepository.

    - ``list(status=..., instance_id=...)`` returns the configured messages.
    - ``get_pending_for_instances(child_ids)`` returns
      ``[(msg.instance_id, msg.message_id), ...]`` for messages in
      READY/PROCESSING/RETRYING (any of the configured status buckets)
      whose ``instance_id`` is in ``child_ids``.
    """
    repo = MagicMock(name="MsgRepo")
    mapping = msgs_by_status_and_instance or {}

    def _list(status=None, instance_id=None, limit=100, offset=0):
        return list(mapping.get((status, instance_id), []))

    def _get_pending_for_instances(child_ids):
        if not child_ids:
            return []
        child_set = set(child_ids)
        # Production repo considers READY/PROCESSING/RETRYING pending. We
        # accept any of those buckets the test has configured.
        pending_statuses = {"ready", "processing", "retrying"}
        pairs: list[tuple[str, str]] = []
        for (status, instance_id), msgs in mapping.items():
            if status in pending_statuses and instance_id in child_set:
                for m in msgs:
                    pairs.append((m.instance_id, m.message_id))
        return pairs

    repo.list = MagicMock(side_effect=_list)
    repo.get_pending_for_instances = MagicMock(side_effect=_get_pending_for_instances)
    return repo


def make_callback() -> tuple[AsyncRecorder, Any]:
    """Build a completion_callback that records every invocation."""
    recorder: list[tuple[str, str]] = []

    async def _cb(parent_id: str, terminal_status: str) -> None:
        recorder.append((parent_id, terminal_status))

    # Attach the recorder list so tests can inspect it
    _cb.calls = recorder  # type: ignore[attr-defined]
    return recorder, _cb


def make_cm(
    *,
    instance_repo: MagicMock | None = None,
    msg_repo: MagicMock | None = None,
    callback: Any = None,
    event_bus: Any = None,
) -> CorrelationManager:
    """Instantiate a CorrelationManager with sensible defaults."""
    return CorrelationManager(
        instance_repository=instance_repo or make_instance_repo(),
        message_queue_repository=msg_repo or make_msg_repo(),
        completion_callback=callback,
        event_bus=event_bus,
    )


# =============================================================================
# Group 1 — Register / Resolve / Callback Flow
# =============================================================================


class TestRegisterResolveCallback:
    """Tests for register_message_send, resolve_response, and callback firing."""

    @pytest.mark.asyncio
    async def test_register_single_message_send(self):
        """Register 1 message send → pending_count=1, is_complete=False."""
        cm = make_cm()
        parent, child, msg = "parent-1", "child-1", str(uuid.uuid4())

        await cm.register_message_send(parent, child, msg)

        assert cm.get_pending_count(parent) == 1
        assert cm.is_complete(parent) is False

    @pytest.mark.asyncio
    async def test_register_multiple_message_sends_same_parent(self):
        """Register 3 sends for parent P → pending_count=3."""
        cm = make_cm()
        parent = "parent-1"

        for i in range(3):
            await cm.register_message_send(parent, f"child-{i}", str(uuid.uuid4()))

        assert cm.get_pending_count(parent) == 3
        assert cm.is_complete(parent) is False

    @pytest.mark.asyncio
    async def test_resolve_partial(self):
        """Register 3, resolve 2 → pending_count=1, callback NOT fired."""
        recorder, cb = make_callback()
        # Shadow validation calls repo.get on partial resolve; return None to skip.
        instance_repo = make_instance_repo(instance_by_id={"parent-1": make_instance("parent-1", waiting_for=1)})
        cm = make_cm(instance_repo=instance_repo, callback=cb)
        parent = "parent-1"
        keys = [(f"child-{i}", str(uuid.uuid4())) for i in range(3)]

        for child, msg in keys:
            await cm.register_message_send(parent, child, msg)

        # Resolve 2 of 3
        r1 = await cm.resolve_response(parent, keys[0][0], keys[0][1])
        r2 = await cm.resolve_response(parent, keys[1][0], keys[1][1])

        assert r1 is False  # not the last
        assert r2 is False
        assert cm.get_pending_count(parent) == 1
        assert cm.is_complete(parent) is False
        assert recorder == [], "Callback must not fire on partial resolve"

    @pytest.mark.asyncio
    async def test_resolve_all_triggers_callback(self):
        """Register 3, resolve all 3 → callback fired with terminal_status=completed."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        keys = [(f"child-{i}", str(uuid.uuid4())) for i in range(3)]

        for child, msg in keys:
            await cm.register_message_send(parent, child, msg)

        # Resolve first 2 (should not fire callback)
        await cm.resolve_response(parent, keys[0][0], keys[0][1])
        await cm.resolve_response(parent, keys[1][0], keys[1][1])

        # Resolve the last one — should fire callback
        last = await cm.resolve_response(parent, keys[2][0], keys[2][1])

        assert last is True  # this was the last pending correlation
        assert len(recorder) == 1
        assert recorder[0] == (parent, "completed")
        # After completion the in-memory state is cleaned up
        assert cm.get_pending_count(parent) == 0

    @pytest.mark.asyncio
    async def test_resolve_unknown_parent(self):
        """resolve_response for untracked parent → returns False, no exception."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)

        result = await cm.resolve_response("ghost-parent", "child-1", "msg-1")

        assert result is False
        assert recorder == []

    @pytest.mark.asyncio
    async def test_resolve_unknown_key(self):
        """resolve_response with wrong (child_id, message_id) → returns False."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        await cm.register_message_send(parent, "child-1", "msg-1")

        result = await cm.resolve_response(parent, "child-1", "wrong-msg-id")

        assert result is False
        assert recorder == []
        # The real entry is still pending
        assert cm.get_pending_count(parent) == 1

    @pytest.mark.asyncio
    async def test_resolve_multiple_messages_to_same_child(self):
        """Parent sends 2 messages to same child → 2 entries; callback fires only after both."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent, child = "parent-1", "child-1"
        msg_a, msg_b = str(uuid.uuid4()), str(uuid.uuid4())

        await cm.register_message_send(parent, child, msg_a)
        await cm.register_message_send(parent, child, msg_b)

        assert cm.get_pending_count(parent) == 2

        # Resolve first — no callback yet
        r1 = await cm.resolve_response(parent, child, msg_a)
        assert r1 is False
        assert recorder == []

        # Resolve second — callback fires
        r2 = await cm.resolve_response(parent, child, msg_b)
        assert r2 is True
        assert recorder == [(parent, "completed")]


# =============================================================================
# Group 2 — had_error Flag and Terminal Status (Fix N2)
# =============================================================================


class TestHadErrorAndTerminalStatus:
    """Tests for the conservative error → terminal_status="error" behaviour."""

    @pytest.mark.asyncio
    async def test_terminal_status_completed(self):
        """Register 2, resolve both with status=responded → callback gets 'completed'."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        keys = [("child-1", str(uuid.uuid4())), ("child-2", str(uuid.uuid4()))]

        for c, m in keys:
            await cm.register_message_send(parent, c, m)

        await cm.resolve_response(parent, keys[0][0], keys[0][1], status=STATUS_RESPONDED)
        await cm.resolve_response(parent, keys[1][0], keys[1][1], status=STATUS_RESPONDED)

        assert recorder == [(parent, "completed")]

    @pytest.mark.asyncio
    async def test_terminal_status_error(self):
        """Register 2, resolve 1st with responded, 2nd with error → 'error'."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        keys = [("child-1", str(uuid.uuid4())), ("child-2", str(uuid.uuid4()))]

        for c, m in keys:
            await cm.register_message_send(parent, c, m)

        await cm.resolve_response(parent, keys[0][0], keys[0][1], status=STATUS_RESPONDED)
        await cm.resolve_response(parent, keys[1][0], keys[1][1], status=STATUS_ERROR)

        assert recorder == [(parent, "error")]

    @pytest.mark.asyncio
    async def test_terminal_status_error_first(self):
        """Register 2, resolve 1st with error, 2nd with responded → 'error' (any error → error)."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        keys = [("child-1", str(uuid.uuid4())), ("child-2", str(uuid.uuid4()))]

        for c, m in keys:
            await cm.register_message_send(parent, c, m)

        await cm.resolve_response(parent, keys[0][0], keys[0][1], status=STATUS_ERROR)
        await cm.resolve_response(parent, keys[1][0], keys[1][1], status=STATUS_RESPONDED)

        assert recorder == [(parent, "error")]

    @pytest.mark.asyncio
    async def test_had_error_set_before_pop(self):
        """had_error is set BEFORE the entry is popped (Fix N2).

        We verify indirectly: when the LAST entry resolves with error, the
        terminal_status must be 'error', proving had_error was set before the
        is_complete check ran (which reads _determine_terminal_status).
        """
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent, child, msg = "parent-1", "child-1", str(uuid.uuid4())

        await cm.register_message_send(parent, child, msg)

        # Single entry, resolved with error → must be the last AND report error
        result = await cm.resolve_response(parent, child, msg, status=STATUS_ERROR)

        assert result is True
        assert recorder == [(parent, "error")]

    @pytest.mark.asyncio
    async def test_failed_status_also_sets_error(self):
        """status='failed' must be treated the same as 'error' (per source code)."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent, child, msg = "parent-1", "child-1", str(uuid.uuid4())

        await cm.register_message_send(parent, child, msg)
        await cm.resolve_response(parent, child, msg, status="failed")

        assert recorder == [(parent, "error")]


# =============================================================================
# Group 3 — Per-Parent Lock Serialization
# =============================================================================


class TestPerParentLockSerialization:
    """Tests verifying per-parent asyncio.Lock serializes resolves correctly."""

    @pytest.mark.asyncio
    async def test_per_parent_lock_serialization(self):
        """Concurrent resolve_response calls for same parent serialize correctly.

        We register 5 correlations, then fire 5 concurrent resolves.
        Exactly one must return True (the last), and the callback must fire
        exactly once. This proves the per-parent lock serializes the
        terminal check — without it, multiple concurrent resolves could
        all see is_complete and fire the callback multiple times.
        """
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        keys = [(f"child-{i}", str(uuid.uuid4())) for i in range(5)]

        for c, m in keys:
            await cm.register_message_send(parent, c, m)

        results = await asyncio.gather(
            *[cm.resolve_response(parent, c, m) for c, m in keys]
        )

        # Exactly one True (the resolving last entry)
        true_count = sum(1 for r in results if r)
        assert true_count == 1, f"Expected exactly 1 True, got {true_count}"
        # Callback fired exactly once
        assert len(recorder) == 1

    @pytest.mark.asyncio
    async def test_different_parents_parallel(self):
        """Operations on different parents don't interfere — state isolation."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)

        # Parent 1: 2 correlations
        p1_keys = [("c1a", str(uuid.uuid4())), ("c1b", str(uuid.uuid4()))]
        for c, m in p1_keys:
            await cm.register_message_send("parent-1", c, m)

        # Parent 2: 3 correlations
        p2_keys = [(f"c2{i}", str(uuid.uuid4())) for i in range(3)]
        for c, m in p2_keys:
            await cm.register_message_send("parent-2", c, m)

        # Resolve all of parent-1 and parent-2 concurrently
        all_tasks = (
            [cm.resolve_response("parent-1", c, m) for c, m in p1_keys]
            + [cm.resolve_response("parent-2", c, m) for c, m in p2_keys]
        )
        await asyncio.gather(*all_tasks)

        # Both parents should have fired their callbacks
        p1_calls = [r for r in recorder if r[0] == "parent-1"]
        p2_calls = [r for r in recorder if r[0] == "parent-2"]
        assert len(p1_calls) == 1
        assert len(p2_calls) == 1

    @pytest.mark.asyncio
    async def test_lock_isolation(self):
        """Resolving P1 doesn't affect P2's pending count."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)

        # Both parents have 2 pending each
        p1_keys = [("c1a", str(uuid.uuid4())), ("c1b", str(uuid.uuid4()))]
        p2_keys = [("c2a", str(uuid.uuid4())), ("c2b", str(uuid.uuid4()))]
        for c, m in p1_keys:
            await cm.register_message_send("P1", c, m)
        for c, m in p2_keys:
            await cm.register_message_send("P2", c, m)

        # Resolve ALL of P1
        for c, m in p1_keys:
            await cm.resolve_response("P1", c, m)

        # P1 fired, P2 untouched
        assert recorder == [("P1", "completed")]
        assert cm.get_pending_count("P2") == 2
        assert cm.is_complete("P2") is False


# =============================================================================
# Group 4 — rebuild_from_db with Real UUIDs (Fix N1)
# =============================================================================


class TestRebuildFromDb:
    """Tests for rebuild_from_db reconstructing pending state from DB."""

    @pytest.mark.asyncio
    async def test_rebuild_with_matching_counts(self):
        """Seed DB with waiting_for=2 for parent P, 2 messages in queue → pending_count=2."""
        parent_id = "parent-1"
        child_id = "child-1"
        msg_a, msg_b = str(uuid.uuid4()), str(uuid.uuid4())

        instance = make_instance(parent_id, waiting_for=2)
        child = make_instance(child_id)
        instance_repo = make_instance_repo(
            instances=[instance],
            children_by_parent={parent_id: [child]},
        )
        msg_repo = make_msg_repo(
            msgs_by_status_and_instance={
                ("ready", child_id): [make_message(msg_a, child_id), make_message(msg_b, child_id)],
            }
        )
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        await cm.rebuild_from_db()

        assert cm.get_pending_count(parent_id) == 2
        assert cm.is_complete(parent_id) is False

    @pytest.mark.asyncio
    async def test_rebuild_resolvable_with_real_uuids(self):
        """After rebuild, resolve_response with a real message_id from the queue resolves."""
        parent_id = "parent-1"
        child_id = "child-1"
        msg_id = str(uuid.uuid4())  # real UUID, not placeholder

        instance = make_instance(parent_id, waiting_for=1)
        child = make_instance(child_id)
        instance_repo = make_instance_repo(
            instances=[instance],
            children_by_parent={parent_id: [child]},
        )
        msg_repo = make_msg_repo(
            msgs_by_status_and_instance={
                ("ready", child_id): [make_message(msg_id, child_id)],
            }
        )
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        await cm.rebuild_from_db()

        # The correlation_key in rebuild uses f"{child_id}:{msg.message_id}"
        # which must EXACTLY match what resolve_response builds.
        result = await cm.resolve_response(parent_id, child_id, msg_id)

        assert result is True, "resolve_response must find the rebuilt entry by real UUID"

    @pytest.mark.asyncio
    async def test_rebuild_no_children(self, caplog: pytest.LogCaptureFixture):
        """Parent has waiting_for > 0 but no children → logs warning, skips."""
        parent_id = "parent-1"
        instance = make_instance(parent_id, waiting_for=2)

        instance_repo = make_instance_repo(
            instances=[instance],
            children_by_parent={},  # no children
        )
        msg_repo = make_msg_repo()
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        caplog.set_level(logging.WARNING)
        await cm.rebuild_from_db()

        # pending count stays 0 (nothing to track)
        assert cm.get_pending_count(parent_id) == 0
        # A mismatch warning should be logged (DB says 2, CM found 0)
        mismatch_logs = [r for r in caplog.records if "mismatch" in r.message.lower()]
        assert len(mismatch_logs) >= 1

    @pytest.mark.asyncio
    async def test_rebuild_count_mismatch(self, caplog: pytest.LogCaptureFixture):
        """Parent waiting_for=3 but only 2 messages in queue → logs warning, tracks 2."""
        parent_id = "parent-1"
        child_id = "child-1"
        msg_a, msg_b = str(uuid.uuid4()), str(uuid.uuid4())

        instance = make_instance(parent_id, waiting_for=3)  # DB says 3
        child = make_instance(child_id)
        instance_repo = make_instance_repo(
            instances=[instance],
            children_by_parent={parent_id: [child]},
        )
        msg_repo = make_msg_repo(
            msgs_by_status_and_instance={
                ("ready", child_id): [
                    make_message(msg_a, child_id),
                    make_message(msg_b, child_id),
                ],  # only 2 messages
            }
        )
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        caplog.set_level(logging.WARNING)
        await cm.rebuild_from_db()

        # CM tracks what it found (2)
        assert cm.get_pending_count(parent_id) == 2
        # Mismatch warning logged
        mismatch_logs = [r for r in caplog.records if "mismatch" in r.message.lower()]
        assert len(mismatch_logs) >= 1

    @pytest.mark.asyncio
    async def test_rebuild_empty(self):
        """No parents with waiting_for > 0 → empty state."""
        # All instances have waiting_for=0 → skipped
        instances = [make_instance("a", waiting_for=0), make_instance("b", waiting_for=0)]
        instance_repo = make_instance_repo(instances=instances)
        msg_repo = make_msg_repo()
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        await cm.rebuild_from_db()

        assert cm.get_pending_count("a") == 0
        assert cm.get_pending_count("b") == 0

    @pytest.mark.asyncio
    async def test_rebuild_multiple_status_filters(self):
        """rebuild_from_db queries ready, processing, retrying — all should be tracked."""
        parent_id = "parent-1"
        child_id = "child-1"
        msg_ready = str(uuid.uuid4())
        msg_proc = str(uuid.uuid4())
        msg_retry = str(uuid.uuid4())

        instance = make_instance(parent_id, waiting_for=3)
        child = make_instance(child_id)
        instance_repo = make_instance_repo(
            instances=[instance],
            children_by_parent={parent_id: [child]},
        )
        msg_repo = make_msg_repo(
            msgs_by_status_and_instance={
                ("ready", child_id): [make_message(msg_ready, child_id)],
                ("processing", child_id): [make_message(msg_proc, child_id)],
                ("retrying", child_id): [make_message(msg_retry, child_id)],
            }
        )
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        await cm.rebuild_from_db()

        assert cm.get_pending_count(parent_id) == 3


# =============================================================================
# Group 5 — Shadow Mode Comparison
# =============================================================================


class TestShadowModeComparison:
    """Tests for shadow validation and rate-limited logging."""

    @pytest.mark.asyncio
    async def test_shadow_match(self, caplog: pytest.LogCaptureFixture):
        """CM pending_count matches DB waiting_for → no mismatch logged."""
        parent_id = "parent-1"
        child_id = "child-1"
        msg_id = str(uuid.uuid4())

        # Register one correlation; DB also says waiting_for=1 → match
        instance_repo = make_instance_repo(
            instance_by_id={parent_id: make_instance(parent_id, waiting_for=1)}
        )
        cm = make_cm(instance_repo=instance_repo)

        await cm.register_message_send(parent_id, child_id, msg_id)

        caplog.set_level(logging.WARNING)
        caplog.clear()
        await cm._validate_shadow_mode(parent_id)

        mismatch_logs = [r for r in caplog.records if "mismatch" in r.message.lower()]
        assert mismatch_logs == []

    @pytest.mark.asyncio
    async def test_shadow_mismatch(self, caplog: pytest.LogCaptureFixture):
        """CM pending_count differs from DB waiting_for → mismatch logged."""
        parent_id = "parent-1"
        child_id = "child-1"
        msg_id = str(uuid.uuid4())

        # CM has 1 pending, but DB says waiting_for=5 → mismatch
        instance_repo = make_instance_repo(
            instance_by_id={parent_id: make_instance(parent_id, waiting_for=5)}
        )
        cm = make_cm(instance_repo=instance_repo)

        await cm.register_message_send(parent_id, child_id, msg_id)

        caplog.set_level(logging.WARNING)
        caplog.clear()
        await cm._validate_shadow_mode(parent_id)

        mismatch_logs = [r for r in caplog.records if "mismatch" in r.message.lower()]
        assert len(mismatch_logs) == 1
        assert "waiting_for=5" in mismatch_logs[0].message
        assert "CM pending=1" in mismatch_logs[0].message

    @pytest.mark.asyncio
    async def test_shadow_no_instance_in_db(self, caplog: pytest.LogCaptureFixture):
        """If the parent is not in DB, _validate_shadow_mode returns silently."""
        cm = make_cm(instance_repo=make_instance_repo(instance_by_id={}))  # nothing in DB

        await cm.register_message_send("ghost-parent", "child-1", "msg-1")

        caplog.set_level(logging.WARNING)
        caplog.clear()
        # Should not raise — just logs debug and returns
        await cm._validate_shadow_mode("ghost-parent")

        mismatch_logs = [r for r in caplog.records if "mismatch" in r.message.lower()]
        assert mismatch_logs == []

    def test_should_log_mismatch_under_cap(self):
        """First 100 mismatches in a window return True."""
        cm = make_cm()
        cm._mismatch_window_start = 0.0  # avoid time.monotonic drift

        results = [cm._should_log_mismatch() for _ in range(100)]
        assert all(results), "First 100 mismatches in a window should log"

    def test_should_log_mismatch_over_cap_returns_false(self):
        """After 100 mismatches in a window, subsequent calls return False
        (provided the 5-min summary interval hasn't elapsed).
        """
        import time as _time

        cm = make_cm()
        now = _time.monotonic()
        cm._mismatch_window_start = now  # fresh window
        cm._last_mismatch_summary = now  # summary fired "just now" → not yet 5 min

        # Burn through the 100 cap
        for _ in range(100):
            cm._should_log_mismatch()

        # 101st call — should be rate-limited (False) since summary just fired
        result = cm._should_log_mismatch()
        assert result is False, "101st mismatch within window should be rate-limited"

    def test_should_log_mismatch_summary_after_interval(self, caplog: pytest.LogCaptureFixture):
        """After 5-min interval, a summary mismatch log fires (returns True)."""
        cm = make_cm()
        # Force window to be "old" so we've burned the cap, but summary interval passed
        import time as _time

        now = _time.monotonic()
        cm._mismatch_window_start = now  # fresh window
        cm._last_mismatch_summary = now - 400.0  # 400s ago > 300s interval

        # Burn cap
        for _ in range(100):
            cm._should_log_mismatch()

        caplog.set_level(logging.WARNING)
        caplog.clear()
        # 101st call — should hit the summary branch and return True
        result = cm._should_log_mismatch()
        assert result is True
        summary_logs = [r for r in caplog.records if "rate limit active" in r.message.lower()]
        assert len(summary_logs) == 1

    def test_should_log_match_under_cap(self):
        """First 100 matches in a window return True."""
        cm = make_cm()
        cm._match_window_start = 0.0

        results = [cm._should_log_match() for _ in range(100)]
        assert all(results)

    def test_rate_limited_logging_window_reset(self):
        """After the 60s window resets, the cap refreshes."""
        import time as _time

        cm = make_cm()
        now = _time.monotonic()
        # Simulate an old window so the next call resets it
        cm._mismatch_window_start = now - 120.0  # 2 minutes ago
        cm._mismatch_count = 100  # previously capped

        result = cm._should_log_mismatch()
        # Window should have reset → returns True and resets count
        assert result is True
        assert cm._mismatch_count == 1  # reset to 0, then incremented to 1

    def test_match_and_mismatch_windows_are_independent(self):
        """A burst of mismatches must not reset the match window (and vice versa)."""
        import time as _time

        cm = make_cm()
        now = _time.monotonic()
        # Mismatch window is "old" (will reset) but match window is "fresh".
        cm._mismatch_window_start = now - 120.0  # 2 min ago → will reset
        cm._match_window_start = now  # fresh
        cm._mismatch_count = 0
        cm._match_count = 50  # match was at 50; window should NOT reset

        # Trigger a mismatch; this resets the mismatch window but must
        # leave the match window/count untouched.
        cm._should_log_mismatch()

        # Match count should be unchanged (no reset of match window).
        assert cm._match_count == 50
        # Match window start should be unchanged.
        assert cm._match_window_start == now


# =============================================================================
# Bonus — Dataclass sanity tests
# =============================================================================


class TestDataclasses:
    """Sanity tests for PendingResponse and ParentCorrelation dataclasses."""

    def test_pending_response_defaults(self):
        entry = PendingResponse(
            parent_id="p1", child_id="c1", message_id="m1", created_at=0.0
        )
        assert entry.status == STATUS_PENDING
        assert entry.parent_id == "p1"

    def test_parent_correlation_defaults(self):
        pc = ParentCorrelation(parent_id="p1")
        assert pc.pending == {}
        assert pc.had_error is False
        assert pc.is_complete is True  # empty → complete
        assert pc.pending_count == 0

    def test_parent_correlation_with_entries(self):
        pc = ParentCorrelation(parent_id="p1")
        pc.pending["c1:m1"] = PendingResponse("p1", "c1", "m1", 0.0)
        pc.pending["c1:m2"] = PendingResponse("p1", "c1", "m2", 0.0)
        assert pc.is_complete is False
        assert pc.pending_count == 2

    def test_parent_correlation_had_error_flag(self):
        pc = ParentCorrelation(parent_id="p1")
        assert pc.had_error is False
        pc.had_error = True
        assert pc.had_error is True


# =============================================================================
# Group 6 — clear_for_instance (Fix 3 part A: terminate cleanup)
# =============================================================================


class TestClearForInstance:
    """Tests for ``CorrelationManager.clear_for_instance``.

    Called from ``instance_lifecycle.terminate_instance()`` to evict stale
    in-memory state when an instance is terminated. Without this, a
    terminated-and-revived instance would inherit its previous
    ``_pending[parent_id]`` entry — ``is_complete()`` would never return True
    again until daemon restart, wedging the parent permanently.
    """

    async def test_clear_removes_pending_and_locks_after_register(self):
        """After register_message_send populates _pending and _locks,
        clear_for_instance must remove BOTH entries so a revived instance
        starts with a clean slate.
        """
        cm = make_cm()
        parent = "parent-cleanup-001"
        child = "child-001"

        # Populate _pending and _locks via a real registration.
        await cm.register_message_send(parent, child, "msg-001")

        # Sanity check: state is populated.
        assert parent in cm._pending
        assert parent in cm._locks
        assert cm.get_pending_count(parent) == 1
        assert cm.is_complete(parent) is False

        # Clear.
        await cm.clear_for_instance(parent)

        # Both _pending and _locks entries are gone.
        assert parent not in cm._pending, (
            f"_pending should be cleared, found keys: {list(cm._pending)}"
        )
        assert parent not in cm._locks, (
            f"_locks should be cleared, found keys: {list(cm._locks)}"
        )
        # Public-state confirmation.
        assert cm.get_pending_count(parent) == 0
        assert cm.is_complete(parent) is True  # no entry → complete

    async def test_clear_unknown_parent_is_safe(self):
        """clear_for_instance on a parent with no entries must NOT raise.

        Both ``.pop(key, None)`` calls are no-ops on missing keys, so this
        must complete silently. Critical for terminate cleanup when an
        instance never had any children.
        """
        cm = make_cm()

        # Must not raise.
        await cm.clear_for_instance("never-registered-parent")

        # Public state confirms nothing was tracked.
        assert cm.get_pending_count("never-registered-parent") == 0
        assert cm.is_complete("never-registered-parent") is True

    async def test_clear_does_not_affect_other_parents(self):
        """clear_for_instance(P1) must NOT touch P2's state.

        Parents are isolated — clearing one must leave siblings untouched.
        """
        cm = make_cm()

        # Two parents with pending entries.
        await cm.register_message_send("parent-A", "child-1", "msg-a1")
        await cm.register_message_send("parent-A", "child-2", "msg-a2")
        await cm.register_message_send("parent-B", "child-1", "msg-b1")

        # Clear parent-A.
        await cm.clear_for_instance("parent-A")

        # parent-A is gone.
        assert "parent-A" not in cm._pending
        assert "parent-A" not in cm._locks

        # parent-B is untouched.
        assert "parent-B" in cm._pending
        assert "parent-B" in cm._locks
        assert cm.get_pending_count("parent-B") == 1
        assert cm.is_complete("parent-B") is False

    async def test_clear_after_full_resolve_leaves_no_state(self):
        """clear_for_instance after resolve_response is also safe.

        resolve_response cleans up _pending on completion; clear_for_instance
        is a defensive no-op when called after that.
        """
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-resolved-001"
        child = "child-001"
        msg = "msg-001"

        await cm.register_message_send(parent, child, msg)
        # Resolve to completion → callback fires → _pending is cleaned.
        result = await cm.resolve_response(parent, child, msg)
        assert result is True
        assert parent not in cm._pending  # already cleared by resolve

        # Now call clear — must not raise and must leave _locks clean too.
        await cm.clear_for_instance(parent)

        # _locks is also cleaned (resolve_response already popped it, but
        # clear must be idempotent).
        assert parent not in cm._locks

    async def test_clear_then_register_starts_fresh(self):
        """After clear, a new register on the same parent starts with count=0.

        This is the exact behavior needed for terminate→revive: the revived
        instance must NOT inherit the terminated instance's pending state.
        """
        cm = make_cm()
        parent = "parent-revived-001"

        # Pre-populate.
        await cm.register_message_send(parent, "child-1", "msg-1")
        await cm.register_message_send(parent, "child-2", "msg-2")
        assert cm.get_pending_count(parent) == 2

        # Terminate → clear.
        await cm.clear_for_instance(parent)
        assert cm.get_pending_count(parent) == 0

        # Revive → fresh registration starts at count=1, NOT count=3.
        await cm.register_message_send(parent, "child-3", "msg-3")
        assert cm.get_pending_count(parent) == 1, (
            "Revived instance must start with fresh pending count"
        )

    async def test_clear_uses_per_parent_lock(self):
        """clear_for_instance must take the per-parent lock for serialized access.

        Verifies by registering entries concurrently with a clear — neither
        operation should hang or leave inconsistent state.
        """
        cm = make_cm()
        parent = "parent-concurrent-001"

        # Register many entries.
        for i in range(10):
            await cm.register_message_send(parent, f"child-{i}", f"msg-{i}")

        # Race a clear against a final register. The clear must complete
        # without hanging (no N3 lock-across-event-loop violation).
        await asyncio.wait_for(
            asyncio.gather(
                cm.clear_for_instance(parent),
                cm.register_message_send(parent, "child-late", "msg-late"),
            ),
            timeout=2.0,
        )

        # Final state: either the clear happened after the late register
        # (no state at all) or before it (just the late entry). In either
        # case the count is at most 1.
        assert cm.get_pending_count(parent) <= 1


# =============================================================================
# Group 7 — Callback Exception Restoration (Fix H7)
# =============================================================================
#
# H7 fix: when the completion_callback raises, restore _pending[parent_id]
# so external retry (or a subsequent register_message_send) can recover.
# Without this, the parent job is permanently wedged in PROCESSING (orphan
# job) because state was cleared under the lock before the callback fired.
# All tests in this group use a deliberately-failing callback to exercise
# the H7 restoration path.


def _make_failing_callback(
    exc: Exception | None = None,
    *,
    on_call: Any | None = None,
) -> Any:
    """Build a callback that raises ``exc`` (default: ``ValueError``).

    If ``on_call`` is provided, it is awaited (or called) BEFORE the
    exception is raised. Used to interleave work between lock release and
    exception (the realistic race window for a concurrent register).
    """
    if exc is None:
        exc = ValueError("simulated callback failure")

    async def _cb(parent_id: str, terminal_status: str) -> None:
        if on_call is not None:
            result = on_call(parent_id, terminal_status)
            if asyncio.iscoroutine(result):
                await result
        raise exc

    return _cb


class TestCallbackExceptionRestoration:
    """H7 fix tests: completion_callback exception must not orphan the parent.

    Coverage:
      * State restoration on callback exception
      * Logging on failure (exception + restoration message)
      * Recovery via subsequent resolve (re-register → re-resolve → callback)
      * Concurrent-register overwrite protection
      * No-restore on callback success
      * Same-object identity preservation across restoration
      * No-callback path is unaffected
    """

    @pytest.mark.asyncio
    async def test_callback_exception_restores_pending_state(self):
        """Callback raises → ``_pending[parent_id]`` is restored so the
        parent is no longer permanently untracked.
        """
        cm = make_cm(callback=_make_failing_callback())
        parent, child, msg = "parent-h7-001", "child-1", "msg-1"

        await cm.register_message_send(parent, child, msg)

        # Resolve triggers the (failing) callback. The CM must catch the
        # exception internally — resolve_response itself returns True.
        result = await cm.resolve_response(parent, child, msg)
        assert result is True, "resolve_response still returns True on last-resolve"

        # CRITICAL: _pending[parent_id] must be re-populated by H7 fix.
        assert parent in cm._pending, (
            "H7 fix failed: _pending[parent_id] not restored after callback "
            f"exception. _pending keys: {list(cm._pending)}"
        )
        # The restored entry has no pending entries (all were resolved) but
        # is_complete() must still reflect reality — empty pending = complete.
        assert cm.get_pending_count(parent) == 0
        assert cm.is_complete(parent) is True

    @pytest.mark.asyncio
    async def test_callback_exception_logs_failure_and_restoration(
        self, caplog: pytest.LogCaptureFixture
    ):
        """Both the exception traceback AND the restoration message must be
        logged at WARNING/ERROR level so operators see the failure.
        """
        caplog.set_level(logging.WARNING, logger="daemon.services.correlation_manager")
        cm = make_cm(callback=_make_failing_callback(RuntimeError("boom")))
        parent, child, msg = "parent-h7-002", "child-1", "msg-1"

        await cm.register_message_send(parent, child, msg)
        await cm.resolve_response(parent, child, msg)

        # The exception log ("attempting to restore _pending for retry")
        # fires via logger.exception → level ERROR + traceback attached.
        exc_logs = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR
            and "completion_callback failed" in r.message
        ]
        assert len(exc_logs) >= 1, (
            f"Expected ERROR log for callback failure, got: "
            f"{[(r.levelname, r.message) for r in caplog.records]}"
        )

        # The restoration log fires via logger.warning.
        restore_logs = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "restored _pending" in r.message
        ]
        assert len(restore_logs) == 1, (
            f"Expected exactly 1 WARNING log for restoration, got: "
            f"{[(r.levelname, r.message) for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_recovery_via_subsequent_resolve_cycle(self):
        """After restoration, a new register+resolve cycle fires the callback
        normally (recovery path). Proves the CM is fully usable after a
        callback failure — the orphan-job scenario is resolved.
        """
        recorder: list[tuple[str, str, str]] = []
        call_count = {"n": 0}

        async def _flaky_cb(parent_id: str, terminal_status: str) -> None:
            call_count["n"] += 1
            recorder.append((parent_id, terminal_status, f"call-{call_count['n']}"))
            if call_count["n"] == 1:
                raise ValueError("first call fails; second must succeed")

        cm = make_cm(callback=_flaky_cb)
        parent, child = "parent-h7-003", "child-1"
        msg_a, msg_b = "msg-a", "msg-b"

        # First cycle: register one, resolve → callback fails → state restored.
        await cm.register_message_send(parent, child, msg_a)
        result_1 = await cm.resolve_response(parent, child, msg_a)
        assert result_1 is True
        assert call_count["n"] == 1
        # H7: _pending is restored.
        assert parent in cm._pending
        assert cm.get_pending_count(parent) == 0

        # Second cycle: register a NEW message + resolve → callback succeeds.
        await cm.register_message_send(parent, child, msg_b)
        assert cm.get_pending_count(parent) == 1
        result_2 = await cm.resolve_response(parent, child, msg_b)
        assert result_2 is True
        assert call_count["n"] == 2, "Callback must fire again on next completion"

        # After successful second callback, _pending is cleaned (success path).
        assert parent not in cm._pending, (
            "After successful callback, _pending should be cleaned (no restore)"
        )

    @pytest.mark.asyncio
    async def test_concurrent_register_protects_overwrite(self):
        """If ``_pending[parent_id]`` is populated between the callback
        throwing and the restoration acquiring the lock, restoration must
        NOT overwrite that concurrent state. Clobbering would lose newly-
        registered correlations and leave the parent in a worse state
        than the original orphan.

        We simulate the race by having the failing callback populate
        ``_pending[parent_id]`` BEFORE throwing — this models what would
        happen if a concurrent ``register_message_send`` acquired the
        per-parent lock between the callback yield and the restoration
        lock acquisition. The callback runs OUTSIDE the original lock and
        the restoration acquires a fresh lock, so this race is real.
        """
        cm = make_cm()
        parent, child, msg = "parent-h7-004", "child-1", "msg-1"

        # Pre-build the state that a concurrent register_message_send would
        # have populated by the time our restoration runs.
        concurrent_state = ParentCorrelation(parent_id=parent)
        concurrent_state.pending["late-child:late-msg"] = PendingResponse(
            parent_id=parent,
            child_id="late-child",
            message_id="late-msg",
            created_at=0.0,
            status=STATUS_PENDING,
        )

        async def _concurrent_then_fail_cb(
            parent_id: str, terminal_status: str
        ) -> None:
            # Simulate a concurrent register that landed between original
            # lock release and restoration lock acquisition. In production
            # this would be a separate coroutine racing for the per-parent
            # lock; here we just mutate the dict directly (no lock held by
            # the callback) which has the same observable effect for the
            # conditional restore check.
            cm._pending[parent_id] = concurrent_state
            raise ValueError("simulated failure after concurrent register")

        cm._completion_callback = _concurrent_then_fail_cb

        await cm.register_message_send(parent, child, msg)
        await cm.resolve_response(parent, child, msg)

        # The concurrent register's ParentCorrelation must NOT have been
        # clobbered — restoration must have skipped because _pending was
        # already populated.
        assert parent in cm._pending
        assert cm._pending[parent] is concurrent_state, (
            "H7 conditional restore must NOT overwrite a concurrent "
            "register_message_send's ParentCorrelation"
        )
        # The concurrent register's pending entry must still be there.
        assert "late-child:late-msg" in cm._pending[parent].pending
        assert cm.get_pending_count(parent) == 1

    @pytest.mark.asyncio
    async def test_concurrent_register_protects_overwrite_async_race(
        self, caplog: pytest.LogCaptureFixture
    ):
        """Async variant of the concurrent-register test: uses asyncio.Event
        to genuinely yield control between callback start and exception,
        proving the race is handled even when the callback awaits.
        """
        caplog.set_level(logging.INFO, logger="daemon.services.correlation_manager")
        gate = asyncio.Event()
        parent, child, msg = "parent-h7-004b", "child-1", "msg-1"

        async def _gated_failing_cb(parent_id: str, terminal_status: str) -> None:
            await gate.wait()  # yield to event loop
            raise ValueError("failure after yield")

        cm = make_cm(callback=_gated_failing_cb)
        await cm.register_message_send(parent, child, msg)

        # Kick off the resolve — it will block inside the callback on gate.
        resolve_task = asyncio.create_task(
            cm.resolve_response(parent, child, msg)
        )
        # Give the task a chance to enter the callback and hit gate.wait().
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # While the resolve is suspended, a concurrent register lands.
        await cm.register_message_send(parent, "late-child", "late-msg")
        # Sanity: the late register populated _pending[parent_id].
        assert cm.get_pending_count(parent) == 1

        # Now release the gate — callback resumes and throws.
        gate.set()
        result = await resolve_task
        assert result is True

        # The concurrent register's entry must survive restoration.
        assert parent in cm._pending
        assert cm.get_pending_count(parent) == 1
        late_entry = cm._pending[parent].pending.get("late-child:late-msg")
        assert late_entry is not None, (
            "Async race: concurrent register entry must survive restoration"
        )

        # The "restore skipped" log should be present.
        skip_logs = [
            r for r in caplog.records if "restore skipped" in r.message
        ]
        assert len(skip_logs) == 1, (
            f"Expected 'restore skipped' log when concurrent register "
            f"populated _pending, got: {[(r.levelname, r.message) for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_no_restore_on_success(self):
        """Callback succeeds → no restoration. After a successful callback
        the in-memory state stays cleaned (current pre-H7 behaviour)."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent, child, msg = "parent-h7-005", "child-1", "msg-1"

        await cm.register_message_send(parent, child, msg)
        result = await cm.resolve_response(parent, child, msg)

        assert result is True
        assert recorder == [(parent, "completed")]
        # On success, _pending must stay cleaned (no restore).
        assert parent not in cm._pending
        assert cm.get_pending_count(parent) == 0

    @pytest.mark.asyncio
    async def test_same_object_identity_preserved_on_restoration(self):
        """The restored ``ParentCorrelation`` is the SAME Python object as
        the one that was deleted (same id()). Preserves ``had_error``,
        any other attributes, and avoids surprising downstream code that
        relies on object identity.
        """
        cm = make_cm(callback=_make_failing_callback())
        parent, child, msg = "parent-h7-006", "child-1", "msg-1"

        await cm.register_message_send(parent, child, msg)

        # Resolve with ERROR status so had_error is set on the ParentCorrelation.
        await cm.resolve_response(parent, child, msg, status=STATUS_ERROR)

        # _pending[parent_id] should be the SAME object as the original
        # (had_error=True preserved).
        assert parent in cm._pending
        restored = cm._pending[parent]
        assert restored.had_error is True, (
            "had_error must be preserved across restoration (same object)"
        )
        # Empty pending dict (all entries resolved before callback fired).
        assert restored.pending == {}
        # The restored object is a ParentCorrelation — type preserved.
        assert isinstance(restored, ParentCorrelation)

    @pytest.mark.asyncio
    async def test_had_error_preserved_across_restoration(self):
        """When the LAST resolve has status=error and the callback then
        throws, the restored ``ParentCorrelation`` must retain
        ``had_error=True`` so a retry path can re-derive
        ``terminal_status='error'`` from state.
        """
        cm = make_cm(callback=_make_failing_callback())
        parent, child, msg = "parent-h7-007", "child-1", "msg-1"

        await cm.register_message_send(parent, child, msg)
        await cm.resolve_response(parent, child, msg, status=STATUS_ERROR)

        assert parent in cm._pending
        assert cm._pending[parent].had_error is True

    @pytest.mark.asyncio
    async def test_no_callback_registered_skips_restoration_logic(self):
        """When ``_completion_callback`` is None, the exception-handling
        block is unreachable (no callback is invoked). The behaviour must
        match the pre-H7 implementation: state is cleared under the lock
        and nothing else happens.
        """
        cm = make_cm(callback=None)
        parent, child, msg = "parent-h7-008", "child-1", "msg-1"

        await cm.register_message_send(parent, child, msg)
        result = await cm.resolve_response(parent, child, msg)

        assert result is True
        assert parent not in cm._pending
        assert cm.get_pending_count(parent) == 0

    @pytest.mark.asyncio
    async def test_multiple_correlations_partial_then_fail(self):
        """Register 3, resolve 2 (no callback), resolve 3rd (callback fires,
        fails). State must be restored exactly as if all 3 were resolved
        in a clean run, with no leftover pending entries from the first 2.
        """
        recorder: list[str] = []
        call_count = {"n": 0}

        async def _cb(parent_id: str, terminal_status: str) -> None:
            call_count["n"] += 1
            recorder.append(f"{parent_id}:{terminal_status}")
            raise ValueError("always fail")

        cm = make_cm(callback=_cb)
        parent = "parent-h7-009"
        keys = [(f"child-{i}", f"msg-{i}") for i in range(3)]

        for c, m in keys:
            await cm.register_message_send(parent, c, m)

        # Resolve first two — no callback, just decrements.
        r1 = await cm.resolve_response(parent, *keys[0])
        r2 = await cm.resolve_response(parent, *keys[1])
        assert r1 is False
        assert r2 is False
        assert call_count["n"] == 0

        # Resolve the third — callback fires (and fails).
        r3 = await cm.resolve_response(parent, *keys[2])
        assert r3 is True
        assert call_count["n"] == 1

        # _pending must be restored with no leftover entries.
        assert parent in cm._pending
        assert cm._pending[parent].pending == {}
        assert cm._pending[parent].had_error is False  # all responded
        assert cm.get_pending_count(parent) == 0

    @pytest.mark.asyncio
    async def test_restoration_uses_lock_no_deadlock(self):
        """The restoration path acquires ``_get_lock(parent_id)`` after
        the original lock was popped from ``_locks`` (S3 fix). The lock
        must be lazily recreated and the whole flow must complete without
        deadlock or asyncio cancellation. We bound the test with
        ``asyncio.wait_for`` to detect hangs.
        """
        cm = make_cm(callback=_make_failing_callback())
        parent, child, msg = "parent-h7-010", "child-1", "msg-1"

        await cm.register_message_send(parent, child, msg)

        # The original lock was popped; restoration must lazily recreate.
        await asyncio.wait_for(
            cm.resolve_response(parent, child, msg),
            timeout=2.0,
        )

        # After completion, _locks[parent_id] should be lazily present
        # again (from the restoration's _get_lock call).
        assert parent in cm._locks, (
            "Restoration should have lazily re-created the per-parent lock "
            f"via _get_lock. _locks keys: {list(cm._locks)}"
        )
        # And _pending[parent_id] should be restored.
        assert parent in cm._pending


# =============================================================================
# Group 8 — rebuild_from_db() crash-safety (A0a)
# =============================================================================
#
# A0a fix: rebuild_from_db() must OVERWRITE stale pre-clear state but MERGE
# concurrent register_message_send entries that arrive between the top-level
# clear and the per-parent rebuild loop. The per-parent write is the
# additive complement to the W2 OVERWRITE-at-the-top: stale entries are wiped
# by the clear, but a register that landed after the clear is preserved.
#
# Tests in this group cover three crash-recovery scenarios from the
# crash-recovery contract documented in the rebuild_from_db() docstring:
#   * Restart with stale entries (old _pending entries must be cleared)
#   * Restart with zero children but waiting_for > 0 (orphan count)
#   * Restart with concurrent register (must not be lost)


class TestRebuildFromDbCrashSafety:
    """A0a crash-safety tests for ``rebuild_from_db()``.

    Each test asserts a specific clause of the crash-recovery contract:
      * ``test_rebuild_overwrites_stale_entries`` — top-level clear wipes
        pre-crash state.
      * ``test_rebuild_orphan_count_zero_children`` — orphan count
        (waiting_for > 0 but no children) is logged as a mismatch and
        leaves _pending untouched for that parent.
      * ``test_rebuild_concurrent_register_not_lost`` — a concurrent
        ``register_message_send`` that lands between the clear and the
        per-parent rebuild loop is preserved by the merge semantics.
    """

    @pytest.mark.asyncio
    async def test_rebuild_overwrites_stale_entries(self):
        """Pre-existing stale entries in ``_pending`` must be wiped by the
        clear at the start of rebuild (W2 fix: OVERWRITE, not MERGE).

        Scenario: daemon crashed while ``_pending`` contained entries for
        ``parent-stale`` and ``parent-live``. After restart, only
        ``parent-live`` has DB-backed children/messages. ``parent-stale``
        must be gone entirely, and the stale entry in ``parent-live`` must
        be replaced by the DB-backed entry.
        """
        parent_stale = "parent-stale"
        parent_live = "parent-live"
        child_live = "child-live"
        msg_live = str(uuid.uuid4())

        instance = make_instance(parent_live, waiting_for=1)
        child = make_instance(child_live)
        instance_repo = make_instance_repo(
            instances=[instance],
            children_by_parent={parent_live: [child]},
        )
        msg_repo = make_msg_repo(
            msgs_by_status_and_instance={
                ("ready", child_live): [make_message(msg_live, child_live)],
            }
        )
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        # Pre-populate _pending with stale entries (simulating pre-crash state).
        await cm.register_message_send(parent_stale, "stale-child", "stale-msg")
        await cm.register_message_send(parent_live, "stale-child-2", "stale-msg-2")
        assert cm.get_pending_count(parent_stale) == 1
        assert cm.get_pending_count(parent_live) == 1
        assert cm.is_complete(parent_stale) is False

        # Rebuild.
        await cm.rebuild_from_db()

        # Stale parent (no DB row) must be gone entirely.
        assert parent_stale not in cm._pending, (
            f"Stale parent must be wiped by rebuild clear. "
            f"_pending keys: {list(cm._pending)}"
        )
        assert cm.get_pending_count(parent_stale) == 0
        assert cm.is_complete(parent_stale) is True  # no entry → complete

        # Live parent must have the DB-backed entry, not the stale one.
        assert cm.get_pending_count(parent_live) == 1
        assert "stale-child-2:stale-msg-2" not in cm._pending[parent_live].pending, (
            "Stale entry in live parent must be replaced by DB-backed entry"
        )
        assert f"{child_live}:{msg_live}" in cm._pending[parent_live].pending

    @pytest.mark.asyncio
    async def test_rebuild_orphan_count_zero_children(self, caplog: pytest.LogCaptureFixture):
        """Parent has ``waiting_for > 0`` but zero children and zero pending
        messages. CM tracks nothing for this parent (orphan count), and
        logs a mismatch warning.

        This documents the orphan-count clause of the crash-recovery
        contract: the CM cannot fabricate pending entries that aren't in
        the DB, so a parent whose ``waiting_for`` counter is non-zero but
        whose children/messages are missing is left with no CM state.
        External recovery code is responsible for reconciling the
        inconsistency.
        """
        parent_id = "parent-orphan"
        instance = make_instance(parent_id, waiting_for=3)  # DB says 3

        instance_repo = make_instance_repo(
            instances=[instance],
            children_by_parent={},  # no children
        )
        msg_repo = make_msg_repo()  # no pending messages
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        caplog.set_level(logging.WARNING)
        await cm.rebuild_from_db()

        # CM tracks nothing for this parent — the orphan count is a DB
        # inconsistency that CM cannot fix on its own.
        assert cm.get_pending_count(parent_id) == 0
        assert parent_id not in cm._pending, (
            "Orphan-count parent must not get a CM entry — there is no "
            "pending state to track"
        )

        # Mismatch warning logged (DB waiting_for=3, CM found=0).
        mismatch_logs = [
            r for r in caplog.records if "mismatch" in r.message.lower()
        ]
        assert len(mismatch_logs) == 1, (
            f"Expected exactly 1 mismatch warning for orphan count, "
            f"got {len(mismatch_logs)}: "
            f"{[(r.levelname, r.message) for r in mismatch_logs]}"
        )
        assert "waiting_for=3" in mismatch_logs[0].message
        assert "CM found=0" in mismatch_logs[0].message

    @pytest.mark.asyncio
    async def test_rebuild_concurrent_register_not_lost(self):
        """A ``register_message_send`` arriving between the top-level clear
        and the per-parent rebuild loop must NOT be lost.

        Regression test for the per-parent OVERWRITE hazard: previously,
        the rebuild loop created a fresh ``ParentCorrelation`` and
        assigned it to ``self._pending[parent_id]``, clobbering any
        entry a concurrent register had just written. The fix is to
        MERGE into the existing slot instead of replacing it.

        Test design: the mock's ``get_all_with_waiting_for`` blocks the
        calling thread (via ``time.sleep``) so the event loop is free to
        run a concurrent ``register_message_send`` during the suspension.
        The register lands AFTER the top-level clear (because we sleep
        briefly before calling it) and BEFORE the rebuild loop reaches
        the parent (because the thread is still blocked).
        """
        parent_id = "parent-concurrent"
        child_db = "child-db"
        msg_db = str(uuid.uuid4())

        instance = make_instance(parent_id, waiting_for=1)
        child = make_instance(child_db)

        def slow_get_parents():
            # Block the thread (not the event loop) to create a window for
            # the concurrent register to land between the clear and the
            # rebuild loop. 50ms is generous for the concurrent task to
            # complete its register_message_send.
            import time
            time.sleep(0.05)
            return [instance]

        instance_repo = make_instance_repo(
            instances=[instance],
            children_by_parent={parent_id: [child]},
        )
        instance_repo.get_all_with_waiting_for = MagicMock(
            side_effect=slow_get_parents
        )
        msg_repo = make_msg_repo(
            msgs_by_status_and_instance={
                ("ready", child_db): [make_message(msg_db, child_db)],
            }
        )
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        new_child = "child-concurrent"
        new_msg = str(uuid.uuid4())

        async def concurrent_register():
            # Let rebuild start and clear _pending first (the clear is
            # synchronous and happens before the to_thread call).
            await asyncio.sleep(0.01)
            await cm.register_message_send(parent_id, new_child, new_msg)

        register_task = asyncio.create_task(concurrent_register())
        await cm.rebuild_from_db()
        await register_task

        # Both the DB-backed entry AND the concurrent register's entry
        # must be tracked. If the per-parent OVERWRITE bug were still
        # present, only the DB-backed entry would survive (count == 1).
        assert cm.get_pending_count(parent_id) == 2, (
            f"Concurrent register lost during rebuild: expected 2 pending "
            f"(1 DB-backed + 1 concurrent), got {cm.get_pending_count(parent_id)}. "
            f"Pending keys: "
            f"{list(cm._pending.get(parent_id, ParentCorrelation(parent_id='')).pending) if parent_id in cm._pending else 'PARENT NOT TRACKED'}"
        )
        assert cm._pending[parent_id].pending.get(f"{child_db}:{msg_db}") is not None, (
            "DB-backed entry must be tracked after rebuild"
        )
        assert cm._pending[parent_id].pending.get(f"{new_child}:{new_msg}") is not None, (
            "Concurrent register's entry must NOT be clobbered by rebuild"
        )
