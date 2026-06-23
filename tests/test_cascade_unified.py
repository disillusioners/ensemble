"""Phase 3 tests: Cascade Unification — pure set-based completion and error symmetry.

Tests the CorrelationManager (CM) contracts that Phase 3 (Cascade Unification)
relies on. Phase 3 replaces 3 divergent cascade decision sites (Sites 1A, 1B, 2)
with a single delegation to the CM. The CM is the authoritative source of truth
for "is the parent ready to complete?" — the decision is made purely from the
in-memory pending set, not from a DB `SELECT COUNT(*)` query.

Test coverage (mapped to plan §Verification Strategy items 1, 2, 4, 6):
  1. Pure set-based completion (Fix C5): register N, resolve N one by one,
     verify the completion_callback fires ONLY on the Nth resolve.
  2. No DB query in the completion path (Fix C5): the completing resolve
     must NOT touch the instance_repo or msg_repo.
  3. Error+success path symmetry: terminal_status is "error" if ANY child
     errored, regardless of resolution order.
  4. Site 1B root completion (Fix A2): `cm.is_complete()` is the read-only
     check for "all children done?" — the root's own queue check is a
     separate concern (the existing `SELECT COUNT(*) FROM MessageQueue`).

See ``.agents/shared/planning/correlation-manager/phase3-cascade-unification.md``
for the full plan.

Run with:

    pytest tests/test_cascade_unified.py -v
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.skip(reason="Phase 5: CorrelationManager removed; tests CM contracts")

# CM-era imports removed in Phase 5 (CorrelationManager → DependencyBus).
# Tests in this module are skipped via ``pytestmark`` above.


# =============================================================================
# Shared mock helpers (mirrors tests/test_correlation_manager.py patterns)
# =============================================================================


def make_instance(
    instance_id: str,
    waiting_for: int = 0,
    status: str = "running",
) -> MagicMock:
    """Build a minimal mock Instance."""
    inst = MagicMock(name=f"Instance({instance_id})")
    inst.instance_id = instance_id
    inst.waiting_for = waiting_for
    inst.status = status
    return inst


def make_instance_repo(
    *,
    instance_by_id: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock SQLModelInstanceRepository.

    We track call counts so tests can assert that the DB is NOT touched
    during the completion path (Fix C5).
    """
    repo = MagicMock(name="InstanceRepo")
    by_id = instance_by_id or {}
    repo.get = MagicMock(side_effect=lambda iid: by_id.get(iid))
    repo.get_all_with_waiting_for = MagicMock(return_value=[])
    repo.get_children = MagicMock(return_value=[])
    return repo


def make_msg_repo() -> MagicMock:
    """Build a mock SQLModelMessageQueueRepository. Tracks call counts."""
    repo = MagicMock(name="MsgRepo")
    repo.get_pending_for_instances = MagicMock(return_value=[])
    repo.list = MagicMock(return_value=[])
    return repo


def make_cm(
    *,
    callback: Any = None,
    instance_repo: MagicMock | None = None,
    msg_repo: MagicMock | None = None,
) -> tuple[CorrelationManager, MagicMock, MagicMock]:
    """Instantiate a CorrelationManager and return (cm, instance_repo, msg_repo).

    The returned repos are the same mocks the CM is wired to — tests can
    inspect ``repo.method_name.call_count`` to verify DB-touch invariants.
    """
    ir = instance_repo or make_instance_repo()
    mr = msg_repo or make_msg_repo()
    cm = CorrelationManager(
        instance_repository=ir,
        message_queue_repository=mr,
        completion_callback=callback,
    )
    return cm, ir, mr


# =============================================================================
# Test 1 — Pure set-based completion (Fix C5)
# =============================================================================


class TestPureSetCompletion:
    """Register N sends, resolve N one by one. Callback fires ONLY on the Nth.

    This proves the completion decision uses the in-memory set (Fix C5),
    not a DB query. The callback is registered with ``asyncio.wait_for``
    timeout-equivalent semantics by checking call count after each resolve.
    """

    @pytest.mark.asyncio
    async def test_callback_fires_only_on_last_resolve(self):
        """Register 3, resolve 3 sequentially. Callback fires exactly once
        on the 3rd resolve; the first two resolves must not fire it.
        """
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent = "parent-1"
        keys = [(f"child-{i}", str(uuid.uuid4())) for i in range(3)]

        # Register 3 sends.
        for child, msg in keys:
            await cm.register_message_send(parent, child, msg)
        assert cm.get_pending_count(parent) == 3
        assert callback.call_count == 0

        # Resolve the 1st — not the last, callback must NOT fire.
        r1 = await cm.resolve_response(parent, keys[0][0], keys[0][1])
        assert r1 is False
        assert callback.call_count == 0
        assert cm.get_pending_count(parent) == 2

        # Resolve the 2nd — still not the last, callback must NOT fire.
        r2 = await cm.resolve_response(parent, keys[1][0], keys[1][1])
        assert r2 is False
        assert callback.call_count == 0
        assert cm.get_pending_count(parent) == 1

        # Resolve the 3rd — the last one, callback fires exactly once.
        r3 = await cm.resolve_response(parent, keys[2][0], keys[2][1])
        assert r3 is True
        assert callback.call_count == 1
        # Callback args: (parent_id, terminal_status)
        callback.assert_called_once_with(parent, "completed")
        # After completion the in-memory state is cleaned up.
        assert cm.get_pending_count(parent) == 0

    @pytest.mark.asyncio
    async def test_no_db_query_on_completing_resolve(self):
        """Fix C5 invariant: the completing resolve must NOT query the DB.

        Setup: register 3, resolve 2 (these are partial — shadow validation
        WILL touch ``instance_repo.get`` to compare with ``waiting_for``).
        Then reset mock counters and resolve the 3rd (completing resolve).
        The completing resolve must NOT call ``instance_repo.get`` or any
        ``msg_repo`` method — the in-memory set IS the source of truth.
        """
        # Wire the instance_repo to return a matching waiting_for=2 for the
        # first two (partial) resolves — so shadow validation runs cleanly.
        ir = make_instance_repo(
            instance_by_id={"parent-1": make_instance("parent-1", waiting_for=2)}
        )
        cm, _ir, mr = make_cm(callback=AsyncMock(), instance_repo=ir)
        parent = "parent-1"
        keys = [(f"child-{i}", str(uuid.uuid4())) for i in range(3)]

        for child, msg in keys:
            await cm.register_message_send(parent, child, msg)

        # Partial resolves — shadow validation calls instance_repo.get.
        await cm.resolve_response(parent, keys[0][0], keys[0][1])
        await cm.resolve_response(parent, keys[1][0], keys[1][1])

        # Sanity: the partial resolves DID touch the DB (shadow validation).
        assert ir.get.call_count >= 1
        assert cm.get_pending_count(parent) == 1

        # Reset counters — we want to assert the completing resolve is clean.
        ir.get.reset_mock()
        ir.get_all_with_waiting_for.reset_mock()
        ir.get_children.reset_mock()
        mr.get_pending_for_instances.reset_mock()
        mr.list.reset_mock()

        # Completing resolve — must NOT touch the DB at all.
        last = await cm.resolve_response(parent, keys[2][0], keys[2][1])
        assert last is True

        # Fix C5: zero DB queries in the completion path.
        assert ir.get.call_count == 0, (
            f"Fix C5 violation: completing resolve called instance_repo.get "
            f"{ir.get.call_count} time(s) — completion must be pure in-memory"
        )
        assert ir.get_all_with_waiting_for.call_count == 0
        assert ir.get_children.call_count == 0
        assert mr.get_pending_for_instances.call_count == 0
        assert mr.list.call_count == 0

    @pytest.mark.asyncio
    async def test_single_register_single_resolve_callback_fires(self):
        """Edge case: 1 send, 1 resolve. Callback fires on the only resolve."""
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent, child, msg = "parent-1", "child-1", str(uuid.uuid4())

        await cm.register_message_send(parent, child, msg)
        result = await cm.resolve_response(parent, child, msg)

        assert result is True
        assert callback.call_count == 1
        callback.assert_called_once_with(parent, "completed")

    @pytest.mark.asyncio
    async def test_resolve_then_register_creates_new_cycle(self):
        """Resolve an unknown key, then register → new cycle starts.

        This is the "Message arrives after child completes" scenario from
        the plan. It's NOT a race — it's a new work cycle.
        """
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent, child = "parent-1", "child-1"

        # First cycle: register, resolve — callback fires.
        msg_a = str(uuid.uuid4())
        await cm.register_message_send(parent, child, msg_a)
        result = await cm.resolve_response(parent, child, msg_a)
        assert result is True
        assert callback.call_count == 1
        # State cleaned up.
        assert cm.get_pending_count(parent) == 0

        # New cycle: register a new message.
        msg_b = str(uuid.uuid4())
        await cm.register_message_send(parent, child, msg_b)
        assert cm.get_pending_count(parent) == 1
        # Callback must NOT have fired again — we registered, not completed.
        assert callback.call_count == 1

        # Resolve the new message → second callback fires.
        result = await cm.resolve_response(parent, child, msg_b)
        assert result is True
        assert callback.call_count == 2
        # Both callbacks fired with the same (parent, "completed") signature
        # (the CM doesn't pass msg_id to the callback — only parent + terminal_status).
        for call in callback.call_args_list:
            assert call.args == (parent, "completed")


# =============================================================================
# Test 2 — Error+success path symmetry
# =============================================================================


class TestErrorSuccessSymmetry:
    """Conservative error propagation: ANY error → terminal_status='error'.

    This unifies the previously divergent behaviour:
      - Site 1A (child_reports.py) preserved ERROR status.
      - Site 2 (error_reporting.py) overwrote ERROR to COMPLETED.
    Phase 3 adopts the conservative rule: any errored child → parent error.
    """

    @pytest.mark.asyncio
    async def test_error_last_yields_error(self):
        """Register 2, resolve 1st as 'responded', 2nd as 'error' → 'error'."""
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent = "parent-1"
        keys = [("child-1", str(uuid.uuid4())), ("child-2", str(uuid.uuid4()))]

        for c, m in keys:
            await cm.register_message_send(parent, c, m)

        await cm.resolve_response(parent, keys[0][0], keys[0][1], status=STATUS_RESPONDED)
        await cm.resolve_response(parent, keys[1][0], keys[1][1], status=STATUS_ERROR)

        assert callback.call_count == 1
        callback.assert_called_once_with(parent, "error")

    @pytest.mark.asyncio
    async def test_error_first_yields_error(self):
        """Register 2, resolve 1st as 'error', 2nd as 'responded' → 'error'.

        Order does not matter — the conservative rule fires either way.
        """
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent = "parent-1"
        keys = [("child-1", str(uuid.uuid4())), ("child-2", str(uuid.uuid4()))]

        for c, m in keys:
            await cm.register_message_send(parent, c, m)

        await cm.resolve_response(parent, keys[0][0], keys[0][1], status=STATUS_ERROR)
        await cm.resolve_response(parent, keys[1][0], keys[1][1], status=STATUS_RESPONDED)

        assert callback.call_count == 1
        callback.assert_called_once_with(parent, "error")

    @pytest.mark.asyncio
    async def test_all_responded_yields_completed(self):
        """Register 2, resolve both as 'responded' → 'completed' (no errors)."""
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent = "parent-1"
        keys = [("child-1", str(uuid.uuid4())), ("child-2", str(uuid.uuid4()))]

        for c, m in keys:
            await cm.register_message_send(parent, c, m)

        await cm.resolve_response(parent, keys[0][0], keys[0][1], status=STATUS_RESPONDED)
        await cm.resolve_response(parent, keys[1][0], keys[1][1], status=STATUS_RESPONDED)

        assert callback.call_count == 1
        callback.assert_called_once_with(parent, "completed")

    @pytest.mark.asyncio
    async def test_three_children_one_error_yields_error(self):
        """Register 3, 2 responded + 1 error in any position → 'error'."""
        for error_position in (0, 1, 2):
            callback = AsyncMock()
            cm, _ir, _mr = make_cm(callback=callback)
            parent = f"parent-{error_position}"
            keys = [("child-1", str(uuid.uuid4())),
                    ("child-2", str(uuid.uuid4())),
                    ("child-3", str(uuid.uuid4()))]

            for c, m in keys:
                await cm.register_message_send(parent, c, m)

            for i, (c, m) in enumerate(keys):
                status = STATUS_ERROR if i == error_position else STATUS_RESPONDED
                await cm.resolve_response(parent, c, m, status=status)

            assert callback.call_count == 1, (
                f"error_position={error_position}: expected 1 callback, "
                f"got {callback.call_count}"
            )
            callback.assert_called_once_with(parent, "error")

    @pytest.mark.asyncio
    async def test_had_error_set_before_terminal_check(self):
        """Fix N2: ``had_error`` is set BEFORE the entry is popped.

        Indirect proof: resolve the LAST remaining entry with status='error'.
        The terminal_status must be 'error', proving had_error was set
        before the is_complete check (which reads _determine_terminal_status).
        """
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent, child, msg = "parent-1", "child-1", str(uuid.uuid4())

        await cm.register_message_send(parent, child, msg)
        result = await cm.resolve_response(parent, child, msg, status=STATUS_ERROR)

        assert result is True
        # Fix N2: terminal_status is 'error' even though the entry is gone
        # at the time of the is_complete check.
        assert callback.call_count == 1
        callback.assert_called_once_with(parent, "error")


# =============================================================================
# Test 3 — Site 1B root completion (Fix A2)
# =============================================================================


class TestSite1BRootCompletion:
    """Fix A2: root completion is a READ-ONLY CM check + a SEPARATE queue check.

    The CM tracks child→parent RESPONSE correlations. A root instance checking
    its own pending messages is NOT a child response — it must NOT call
    ``resolve_response`` (the self-referential key would never match any
    registered correlation). Instead, ``cm.is_complete()`` is a read-only
    "are all child responses received?" check.

    These tests verify the CM contract that Site 1B relies on:
      - Root with no children tracked → ``is_complete()`` is True
        (because _pending has no entry → vacuously complete).
      - Root with pending CM children → ``is_complete()`` is False.
      - Root whose children all resolved → ``is_complete()`` is True
        (the entry is deleted from _pending on completion).
    """

    @pytest.mark.asyncio
    async def test_root_with_no_children_is_complete(self):
        """Root instance that the CM has never seen → is_complete() True.

        Site 1B condition 1 (all children done) is satisfied: there are
        no children in the CM. If the queue is also empty, the root
        completes. This test only verifies the CM half of that decision.
        """
        cm, _ir, _mr = make_cm()

        # CM has never seen this parent.
        assert "parent-1" not in cm._pending
        assert cm.is_complete("parent-1") is True
        assert cm.get_pending_count("parent-1") == 0

    @pytest.mark.asyncio
    async def test_root_with_pending_children_not_complete(self):
        """Root with at least one pending child in CM → is_complete() False.

        Site 1B condition 1 (all children done) is NOT satisfied → root
        stays in current status. The CM half of the decision reports False.
        """
        cm, _ir, _mr = make_cm()
        parent = "root-1"

        await cm.register_message_send(parent, "child-1", "msg-1")
        assert cm.is_complete(parent) is False
        assert cm.get_pending_count(parent) == 1

    @pytest.mark.asyncio
    async def test_root_after_all_children_resolved_is_complete(self):
        """Root whose children all resolved → entry is cleaned from _pending
        → is_complete() returns True again (vacuously).

        This models the post-completion state: the CM has done its job
        and forgotten about the parent. Site 1B's next read of
        ``is_complete()`` returns True.
        """
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent = "root-1"

        await cm.register_message_send(parent, "child-1", "msg-1")
        assert cm.is_complete(parent) is False

        # Resolve the only child — callback fires, _pending entry is deleted.
        result = await cm.resolve_response(parent, "child-1", "msg-1")
        assert result is True
        assert callback.call_count == 1

        # After completion, is_complete() is True again (vacuously).
        assert parent not in cm._pending
        assert cm.is_complete(parent) is True
        assert cm.get_pending_count(parent) == 0

    @pytest.mark.asyncio
    async def test_site_1b_two_condition_decision(self):
        """Simulate the Site 1B two-condition decision.

        The full Site 1B logic is:
          1. ``cm.is_complete(instance_id)`` — all child responses received?
          2. ``SELECT COUNT(*) FROM MessageQueue WHERE instance_id = ...``
             — root's own queue empty?

        If both True → root COMPLETED. Otherwise → stays.

        This test models the combined decision function with a queue stub
        to verify the CM half integrates cleanly with the queue check.
        """
        cm, _ir, _mr = make_cm()
        parent = "root-1"

        async def site_1b_decision(
            instance_id: str, queue_pending: bool
        ) -> str:
            """Returns the Site 1B action: 'complete', 'wait_children', or 'wait_queue'."""
            condition_1 = cm.is_complete(instance_id)
            condition_2 = not queue_pending
            if condition_1 and condition_2:
                return "complete"
            if not condition_1:
                return "wait_children"  # CM has pending children
            return "wait_queue"  # CM done, queue still has messages

        # Case A: no children tracked, no queue messages → complete.
        action = await site_1b_decision(parent, queue_pending=False)
        assert action == "complete"

        # Case B: children still pending in CM → wait_children (regardless of queue).
        await cm.register_message_send(parent, "child-1", "msg-1")
        action = await site_1b_decision(parent, queue_pending=False)
        assert action == "wait_children"
        action = await site_1b_decision(parent, queue_pending=True)
        assert action == "wait_children"

        # Case C: children done (CM complete), queue has messages → wait_queue.
        await cm.resolve_response(parent, "child-1", "msg-1")
        action = await site_1b_decision(parent, queue_pending=True)
        assert action == "wait_queue"

        # Case D: children done + queue empty → complete.
        action = await site_1b_decision(parent, queue_pending=False)
        assert action == "complete"
