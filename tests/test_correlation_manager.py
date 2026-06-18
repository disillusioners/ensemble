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
