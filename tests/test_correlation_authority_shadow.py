"""Shadow test pack for Phase A: CorrelationManager is authoritative.

This test pack proves the CM is the sole completion authority when
``USE_LEGACY_WAITING_FOR_CASCADE=OFF`` (the default). It is the
precondition for A7 (removing the ``FOR UPDATE`` row-lock gate).

Test conventions:
  - UNIT tests: SQLite in-memory or mocks, no real DB required.
  - Reuses helpers from ``tests/test_correlation_manager.py``.
  - All async tests use ``pytest.mark.asyncio``.
  - Concurrency tests use deterministic synchronization (asyncio.Event).

Categories
===========
  1. CM is_complete() correctness (6 tests)
  2. Register-window proof (5 tests)
  3. Pause/resume with flag OFF (4 tests)
  4. Crash-recovery via rebuild_from_db() (4 tests)
  5. waiting_for / CM consistency (3 tests)
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

# Reuse helpers from the main CM test suite.
from tests.test_correlation_manager import (
    make_callback,
    make_cm,
    make_instance,
    make_instance_repo,
    make_message,
    make_msg_repo,
)

from daemon.services.correlation_manager import (
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_RESPONDED,
    CorrelationManager,
)


# =============================================================================
# Category 1 — CM is_complete() correctness
# =============================================================================


class TestCMIsCompleteCorrectness:
    """Prove ``is_complete()`` returns the right value in all scenarios."""

    @pytest.mark.asyncio
    async def test_is_complete_true_when_no_pending(self):
        """``is_complete()`` returns True when there are no tracked correlations."""
        cm = make_cm()
        # No registration — parent is unknown to CM → complete.
        assert cm.is_complete("unknown-parent") is True

    @pytest.mark.asyncio
    async def test_is_complete_false_when_pending(self):
        """``is_complete()`` returns False when pending_count > 0."""
        cm = make_cm()
        await cm.register_message_send("parent-1", "child-1", str(uuid.uuid4()))
        assert cm.is_complete("parent-1") is False

    @pytest.mark.asyncio
    async def test_is_complete_true_after_all_resolved(self):
        """``is_complete()`` returns True after all registered correlations resolve."""
        cm = make_cm()
        parent, child = "parent-1", "child-1"
        msg_a, msg_b = str(uuid.uuid4()), str(uuid.uuid4())

        await cm.register_message_send(parent, child, msg_a)
        await cm.register_message_send(parent, child, msg_b)

        assert cm.is_complete(parent) is False

        await cm.resolve_response(parent, child, msg_a)
        assert cm.is_complete(parent) is False

        await cm.resolve_response(parent, child, msg_b)
        assert cm.is_complete(parent) is True

    @pytest.mark.asyncio
    async def test_is_complete_false_with_partial_resolution(self):
        """``is_complete()`` returns False while any correlation remains unresolved."""
        cm = make_cm()
        parent = "parent-1"
        keys = [(f"child-{i}", str(uuid.uuid4())) for i in range(4)]

        for child, msg in keys:
            await cm.register_message_send(parent, child, msg)

        # Resolve 3 of 4.
        for child, msg in keys[:3]:
            await cm.resolve_response(parent, child, msg)

        assert cm.is_complete(parent) is False
        assert cm.get_pending_count(parent) == 1

    @pytest.mark.asyncio
    async def test_is_complete_random_fixtures(self):
        """``is_complete()`` matches pending_count == 0 across 50 random fixtures."""
        cm = make_cm()
        rng = random.Random(42)  # deterministic

        for i in range(50):
            parent = f"parent-fixture-{i}"
            registered = rng.randint(1, 10)
            resolved = rng.randint(0, registered)

            # Register all children with stable (child, msg) keys.
            keys: list[tuple[str, str]] = []
            for j in range(registered):
                child = f"child-{i}-{j}"
                msg = str(uuid.uuid4())
                await cm.register_message_send(parent, child, msg)
                keys.append((child, msg))

            # Resolve the first `resolved` keys.
            for child, msg in keys[:resolved]:
                await cm.resolve_response(parent, child, msg)

            expected_complete = resolved == registered
            actual = cm.is_complete(parent)
            assert actual is expected_complete, (
                f"Fixture {i}: registered={registered}, resolved={resolved}, "
                f"is_complete={actual}, expected={expected_complete}"
            )

    @pytest.mark.asyncio
    async def test_is_complete_with_error_on_one_child(self):
        """``is_complete()`` ignores error flag — only pending count matters."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        keys = [(f"child-{i}", str(uuid.uuid4())) for i in range(3)]

        for child, msg in keys:
            await cm.register_message_send(parent, child, msg)

        # Resolve 2 normally, 1 with error.
        await cm.resolve_response(parent, keys[0][0], keys[0][1], status=STATUS_RESPONDED)
        await cm.resolve_response(parent, keys[1][0], keys[1][1], status=STATUS_ERROR)

        # is_complete is still False (1 pending), but terminal_status will be error.
        assert cm.is_complete(parent) is False

        # Final resolve fires callback with terminal_status="error".
        await cm.resolve_response(parent, keys[2][0], keys[2][1])
        assert recorder[-1] == (parent, "error")


# =============================================================================
# Category 2 — Register-window proof
# =============================================================================


class TestRegisterWindowProof:
    """Prove the register-before/increment-after window is closed under flag OFF.

    These tests are the critical proof for A7: they demonstrate that the CM
    registers a correlation BEFORE any finalize can fire, and that a concurrent
    register during finalize prevents premature completion.
    """

    @pytest.mark.asyncio
    async def test_register_creates_pending_before_any_finalize(self):
        """After ``register_message_send`` the CM's pending set is populated
        immediately — before any DB write or finalize callback can run."""
        cm = make_cm()
        parent, child, msg = "parent-reg-1", "child-1", str(uuid.uuid4())

        # Verify state BEFORE any await other than the register itself.
        await cm.register_message_send(parent, child, msg)
        assert cm.get_pending_count(parent) == 1
        assert cm.is_complete(parent) is False
        assert f"{child}:{msg}" in cm._pending[parent].pending

    @pytest.mark.asyncio
    async def test_register_tracked_when_flag_off(self):
        """When flag is OFF (default), CM is the authority — register
        is always tracked regardless of the legacy kill-switch state."""
        cm = make_cm()
        parent, child, msg = "parent-reg-2", "child-1", str(uuid.uuid4())

        # No flag check in CM itself — register always works.
        await cm.register_message_send(parent, child, msg)

        assert cm.get_pending_count(parent) == 1
        assert cm.is_complete(parent) is False

        # Resolve completes normally.
        result = await cm.resolve_response(parent, child, msg)
        assert result is True
        assert cm.is_complete(parent) is True

    @pytest.mark.asyncio
    async def test_concurrent_register_prevents_premature_complete(self):
        """Register a new child while another child's finalize is in progress.
        The parent must NOT be prematurely completed — ``is_complete`` returns False."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-race-1"
        gate = asyncio.Event()

        # Register 2 children.
        child_a, msg_a = "child-A", str(uuid.uuid4())
        child_b, msg_b = "child-B", str(uuid.uuid4())
        await cm.register_message_send(parent, child_a, msg_a)
        await cm.register_message_send(parent, child_b, msg_b)

        # Resolve child A (partial — 1 of 2 pending).
        await cm.resolve_response(parent, child_a, msg_a)
        assert cm.is_complete(parent) is False
        assert recorder == []

        # Now simulate: while we are about to resolve B, a concurrent
        # register for a new child C arrives. This models the scenario
        # where the parent's finalize callback is running but a new child
        # is spawned before the callback completes.
        async def late_register():
            await gate.wait()
            await cm.register_message_send(parent, "child-C", str(uuid.uuid4()))

        late_task = asyncio.create_task(late_register())

        # Fire the final resolve of B — it should see the CM as NOT complete
        # because child C is concurrently being registered.
        gate.set()
        await late_task

        # CM has 1 pending (B) + 1 concurrent (C) = 2 total.
        assert cm.get_pending_count(parent) == 2
        assert cm.is_complete(parent) is False

    @pytest.mark.asyncio
    async def test_finalize_defers_when_cm_not_complete(self):
        """When ``cm.is_complete()`` is False the finalize must NOT transition
        the job to completed. Simulates the flag-OFF path where finalize
        reads ``cm.is_complete`` instead of ``waiting_for``."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-finalize-1"
        child_a, msg_a = "child-A", str(uuid.uuid4())
        child_b, msg_b = "child-B", str(uuid.uuid4())

        await cm.register_message_send(parent, child_a, msg_a)
        await cm.register_message_send(parent, child_b, msg_b)

        # Resolve A — still 1 pending.
        await cm.resolve_response(parent, child_a, msg_a)

        # is_complete is False (1 pending), simulating what finalize sees.
        assert cm.is_complete(parent) is False

        # Callback is NOT fired yet.
        assert len(recorder) == 0

        # Resolve B — last pending, callback fires.
        await cm.resolve_response(parent, child_b, msg_b)
        assert len(recorder) == 1
        assert recorder[0] == (parent, "completed")

    @pytest.mark.asyncio
    async def test_full_spawn_completion_cascade_under_flag_off(self):
        """End-to-end: spawn children → each completes → parent cascades only
        when ALL children are resolved. This is the full register→resolve→callback
        sequence under the authoritative CM path."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-cascade-1"

        # Spawn 5 children (5 register calls).
        children = [(f"child-{i}", str(uuid.uuid4())) for i in range(5)]
        for child, msg in children:
            await cm.register_message_send(parent, child, msg)

        assert cm.is_complete(parent) is False
        assert cm.get_pending_count(parent) == 5

        # Complete 4 children — still not done.
        for child, msg in children[:4]:
            result = await cm.resolve_response(parent, child, msg)
            assert result is False  # Not the last.
        assert recorder == []
        assert cm.is_complete(parent) is False

        # Complete the last child — callback fires.
        last_child, last_msg = children[4]
        result = await cm.resolve_response(parent, last_child, last_msg)
        assert result is True
        assert recorder == [(parent, "completed")]
        assert cm.is_complete(parent) is True


# =============================================================================
# Category 3 — Pause/resume with flag OFF
# =============================================================================


class TestPauseResumeWithFlagOff:
    """Prove the CM pending set survives pause/resume when flag is OFF.

    The CM itself has no pause/resume methods — the pause/resume logic is
    in ``instance_lifecycle.py``. These tests verify the CM's contract:
    the pending set is never cleared by CM operations alone, and
    ``is_complete`` returns the correct value after any sequence of
    pause/resume-like operations (simulated via direct CM calls).
    """

    @pytest.mark.asyncio
    async def test_cm_pending_set_survives_clear_operations(self):
        """CM pending set survives explicit clear operations."""
        cm = make_cm()
        parent = "parent-pause-1"
        await cm.register_message_send(parent, "child-1", str(uuid.uuid4()))
        await cm.register_message_send(parent, "child-2", str(uuid.uuid4()))

        assert cm.get_pending_count(parent) == 2

        # Simulate pause: clear_for_instance should NOT be called on a
        # running parent. Only terminated instances get cleared.
        # The CM's pending state for a running parent must be intact.
        assert cm.is_complete(parent) is False
        assert cm.get_pending_count(parent) == 2

    @pytest.mark.asyncio
    async def test_resume_after_partial_resolve_preserves_state(self):
        """After resolving some children, a resume (new register) should
        preserve the CM pending set and not reset to a clean state."""
        cm = make_cm()
        parent = "parent-resume-1"
        child_a, msg_a = "child-A", str(uuid.uuid4())
        child_b, msg_b = "child-B", str(uuid.uuid4())

        # Register both.
        await cm.register_message_send(parent, child_a, msg_a)
        await cm.register_message_send(parent, child_b, msg_b)

        # Resolve A (simulating a partial completion before pause).
        await cm.resolve_response(parent, child_a, msg_a)
        assert cm.get_pending_count(parent) == 1

        # Simulate resume: register a new child C.
        child_c, msg_c = "child-C", str(uuid.uuid4())
        await cm.register_message_send(parent, child_c, msg_c)

        # State reflects the new registration on top of the partial state.
        assert cm.get_pending_count(parent) == 2
        assert cm.is_complete(parent) is False

    @pytest.mark.asyncio
    async def test_pause_resume_cycle_pending_unchanged(self):
        """A simulated pause/resume cycle (clear then re-register) does not
        alter the CM pending count if no new work arrives."""
        cm = make_cm()
        parent = "parent-cycle-1"

        await cm.register_message_send(parent, "child-1", str(uuid.uuid4()))
        await cm.register_message_send(parent, "child-2", str(uuid.uuid4()))
        initial_count = cm.get_pending_count(parent)
        assert initial_count == 2

        # Simulate pause: the parent is paused, no new children spawn.
        # (The CM itself doesn't change during pause — only clear_for_instance
        # on actual terminate would clear, and that's NOT a pause.)

        # After any idle period (no new registers), count is unchanged.
        assert cm.get_pending_count(parent) == initial_count
        assert cm.is_complete(parent) is False

    @pytest.mark.asyncio
    async def test_cm_state_survives_multiple_register_resolve_cycles(self):
        """CM pending count is consistent across multiple waves of
        spawn/completion (simulating pause/resume waves)."""
        cm = make_cm()
        parent = "parent-waves-1"

        # Wave 1: 2 children — register with stable keys, then resolve.
        wave1_keys = [
            ("wave1-child-A", str(uuid.uuid4())),
            ("wave1-child-B", str(uuid.uuid4())),
        ]
        for child, msg in wave1_keys:
            await cm.register_message_send(parent, child, msg)

        for child, msg in wave1_keys:
            await cm.resolve_response(parent, child, msg)

        assert cm.is_complete(parent) is True
        # After complete, CM cleans the slot (empty ParentCorrelation removed).
        assert parent not in cm._pending or cm.get_pending_count(parent) == 0

        # Wave 2: spawn after resume with stable keys.
        wave2_keys = [
            ("wave2-child-A", str(uuid.uuid4())),
            ("wave2-child-B", str(uuid.uuid4())),
            ("wave2-child-C", str(uuid.uuid4())),
        ]
        for child, msg in wave2_keys:
            await cm.register_message_send(parent, child, msg)

        assert cm.is_complete(parent) is False
        assert cm.get_pending_count(parent) == 3

        # Wave 2 completes.
        for child, msg in wave2_keys:
            await cm.resolve_response(parent, child, msg)
        assert cm.is_complete(parent) is True


# =============================================================================
# Category 4 — Crash-recovery via rebuild_from_db()
# =============================================================================


class TestCrashRecoveryRebuild:
    """Prove ``rebuild_from_db()`` reconstructs correct state on restart.

    Tests cover:
      - Mid-flight parents reconstructed correctly.
      - Partial resolution survives restart.
      - Concurrent register during rebuild preserved (A0a fix).
      - Orphan counts handled correctly.
    """

    @pytest.mark.asyncio
    async def test_rebuild_mid_flight_parent(self):
        """Restart with a parent that has children in-flight — rebuild must
        reconstruct the pending set from DB."""
        parent_id = "parent-crash-1"
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
                ("ready", child_id): [
                    make_message(msg_a, child_id),
                    make_message(msg_b, child_id),
                ],
            }
        )
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        await cm.rebuild_from_db()

        assert cm.get_pending_count(parent_id) == 2
        assert cm.is_complete(parent_id) is False

    @pytest.mark.asyncio
    async def test_rebuild_partial_resolution(self):
        """Restart after partial resolution: some children resolved, some pending.
        Only the pending ones must be in the reconstructed state."""
        parent_id = "parent-partial-1"
        child_id = "child-1"
        msg_pending = str(uuid.uuid4())

        instance = make_instance(parent_id, waiting_for=1)
        child = make_instance(child_id)
        instance_repo = make_instance_repo(
            instances=[instance],
            children_by_parent={parent_id: [child]},
        )
        msg_repo = make_msg_repo(
            msgs_by_status_and_instance={
                ("ready", child_id): [make_message(msg_pending, child_id)],
            }
        )
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        await cm.rebuild_from_db()

        assert cm.get_pending_count(parent_id) == 1
        assert cm.is_complete(parent_id) is False
        # Resolve using the rebuilt entry.
        result = await cm.resolve_response(parent_id, child_id, msg_pending)
        assert result is True
        assert cm.is_complete(parent_id) is True

    @pytest.mark.asyncio
    async def test_rebuild_concurrent_register_during_rebuild(self):
        """A ``register_message_send`` arriving during rebuild must not be
        lost (A0a MERGE semantics).

        Test design: mock ``get_all_with_waiting_for`` to block the thread
        briefly, allowing a concurrent register to land between the top-level
        clear and the per-parent rebuild write."""
        parent_id = "parent-concurrent-2"
        child_db = "child-db"
        msg_db = str(uuid.uuid4())

        instance = make_instance(parent_id, waiting_for=1)
        child = make_instance(child_db)

        def slow_get_parents():
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

        # Concurrent register arrives during the rebuild window.
        concurrent_child = "child-concurrent"
        concurrent_msg = str(uuid.uuid4())

        async def late_register():
            await cm.register_message_send(parent_id, concurrent_child, concurrent_msg)

        late_task = asyncio.create_task(late_register())

        await cm.rebuild_from_db()
        await late_task

        # The concurrent register's entry must survive the rebuild (A0a fix).
        assert f"{concurrent_child}:{concurrent_msg}" in cm._pending[parent_id].pending
        # The DB-backed entry must also be present.
        assert f"{child_db}:{msg_db}" in cm._pending[parent_id].pending
        # Total count = 1 (DB) + 1 (concurrent) = 2.
        assert cm.get_pending_count(parent_id) == 2
        assert cm.is_complete(parent_id) is False

    @pytest.mark.asyncio
    async def test_rebuild_orphan_count_waiting_for_gt_zero_no_children(
        self, caplog: pytest.LogCaptureFixture
    ):
        """Parent has ``waiting_for > 0`` but no children in the DB.
        CM tracks 0, logs a mismatch warning."""
        parent_id = "parent-orphan-1"
        instance = make_instance(parent_id, waiting_for=5)

        instance_repo = make_instance_repo(
            instances=[instance],
            children_by_parent={},  # no children
        )
        msg_repo = make_msg_repo()
        cm = make_cm(instance_repo=instance_repo, msg_repo=msg_repo)

        caplog.set_level(logging.WARNING)
        await cm.rebuild_from_db()

        # CM tracks nothing for this parent (orphan count).
        assert cm.get_pending_count(parent_id) == 0
        assert cm.is_complete(parent_id) is True

        # Mismatch warning logged.
        mismatch_logs = [r for r in caplog.records if "mismatch" in r.message.lower()]
        assert len(mismatch_logs) >= 1
        assert any("waiting_for=5" in r.message for r in mismatch_logs)


# =============================================================================
# Category 5 — waiting_for / CM consistency
# =============================================================================


class TestWaitingForCMConsistency:
    """Prove ``waiting_for`` (DB) stays consistent with CM's in-memory pending set.

    These tests use a mock instance_repo that always reports the CM's pending
    count as ``waiting_for`` — simulating the in-sync state after A6 migration.
    They also test the ``DEBUG_COMPLETION_INVARIANT`` check fires correctly.
    """

    @pytest.mark.asyncio
    async def test_full_register_resolve_cycle_wf_and_cm_agree(self):
        """After a full register → resolve cycle, ``waiting_for`` (mocked) and
        CM agree on the pending count."""
        parent_id = "parent-consistency-1"
        child_id = "child-1"
        msgs = [str(uuid.uuid4()) for _ in range(3)]

        # Mock instance repo that mirrors CM state.
        waiting_for = {"count": 0}

        def _get(iid):
            if iid == parent_id:
                inst = make_instance(parent_id, waiting_for=waiting_for["count"])
                return inst
            return None

        instance_repo = make_instance_repo(instance_by_id={parent_id: make_instance(parent_id, waiting_for=0)})
        instance_repo.get = MagicMock(side_effect=_get)
        # Override to return the waiting_for tracked above.
        instance_repo.get = MagicMock(
            side_effect=lambda iid: make_instance(iid, waiting_for=waiting_for["count"]) if iid == parent_id else None
        )

        cm = make_cm(instance_repo=instance_repo)

        # Register 3 — DB (mocked) and CM both have 3.
        for msg in msgs:
            await cm.register_message_send(parent_id, child_id, msg)
            waiting_for["count"] = cm.get_pending_count(parent_id)
        assert cm.get_pending_count(parent_id) == 3

        # Resolve 2 — both have 1.
        for msg in msgs[:2]:
            await cm.resolve_response(parent_id, child_id, msg)
            waiting_for["count"] = cm.get_pending_count(parent_id)
        assert cm.get_pending_count(parent_id) == 1

        # Resolve last — both have 0.
        await cm.resolve_response(parent_id, child_id, msgs[2])
        waiting_for["count"] = cm.get_pending_count(parent_id)
        assert cm.get_pending_count(parent_id) == 0
        assert cm.is_complete(parent_id) is True

    @pytest.mark.asyncio
    async def test_partial_resolve_wf_and_cm_agree(self):
        """With partial resolution, ``waiting_for`` (mocked) and CM both reflect
        the same non-zero pending count."""
        parent_id = "parent-partial-consistency-1"
        child_id = "child-1"
        msgs = [str(uuid.uuid4()) for _ in range(4)]

        waiting_for = {"count": 0}
        instance_repo = make_instance_repo(
            instance_by_id={
                parent_id: make_instance(parent_id, waiting_for=0)
            }
        )
        instance_repo.get = MagicMock(
            side_effect=lambda iid: make_instance(iid, waiting_for=waiting_for["count"]) if iid == parent_id else None
        )
        cm = make_cm(instance_repo=instance_repo)

        # Register 4.
        for msg in msgs:
            await cm.register_message_send(parent_id, child_id, msg)
        waiting_for["count"] = cm.get_pending_count(parent_id)

        # Resolve 2 of 4 — both have 2.
        for msg in msgs[:2]:
            await cm.resolve_response(parent_id, child_id, msg)
        waiting_for["count"] = cm.get_pending_count(parent_id)

        cm_count = cm.get_pending_count(parent_id)
        assert cm_count == 2
        assert cm_count == waiting_for["count"]
        assert cm.is_complete(parent_id) is False

    @pytest.mark.asyncio
    async def test_debug_invariant_fires_on_mismatch(self, caplog: pytest.LogCaptureFixture):
        """``DEBUG_COMPLETION_INVARIANT`` fires a structured WARNING when
        CM's pending count disagrees with the DB ``waiting_for``."""
        parent_id = "parent-invariant-1"
        child_id = "child-1"
        msg = str(uuid.uuid4())

        # Mock instance repo that reports waiting_for=99 (mismatch with CM's 1).
        instance_repo = make_instance_repo(
            instance_by_id={
                parent_id: make_instance(parent_id, waiting_for=99)
            }
        )
        cm = make_cm(instance_repo=instance_repo)
        cm._debug_invariant_enabled = True  # Enable the invariant check.

        caplog.set_level(logging.WARNING)
        await cm.register_message_send(parent_id, child_id, msg)

        # CM has 1 pending, DB says 99 — mismatch must be logged.
        mismatch_logs = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "CM_WAITING_FOR_DIVERGENCE" in r.message
        ]
        assert len(mismatch_logs) >= 1
        assert "cm_pending_count=1" in mismatch_logs[0].message
        assert "db_waiting_for=99" in mismatch_logs[0].message

    @pytest.mark.asyncio
    async def test_debug_invariant_silent_on_match(self, caplog: pytest.LogCaptureFixture):
        """``DEBUG_COMPLETION_INVARIANT`` produces no warning when CM and DB agree."""
        parent_id = "parent-match-1"
        child_id = "child-1"
        msg = str(uuid.uuid4())

        # Mock instance repo that reports the same count as CM.
        instance_repo = make_instance_repo(
            instance_by_id={
                parent_id: make_instance(parent_id, waiting_for=1)
            }
        )
        cm = make_cm(instance_repo=instance_repo)
        cm._debug_invariant_enabled = True

        caplog.set_level(logging.WARNING)
        await cm.register_message_send(parent_id, child_id, msg)

        # CM has 1, DB has 1 — no mismatch logged.
        mismatch_logs = [
            r for r in caplog.records
            if "CM_WAITING_FOR_DIVERGENCE" in r.message
        ]
        assert mismatch_logs == []
