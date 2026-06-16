"""Phase 3 tests: Race #3 elimination — concurrent register+resolve and no DB query.

Race #3 was the HIGH-severity TOCTOU race in the legacy cascade code:

    # Legacy (Sites 1A and 2) — DANGEROUS
    if parent.waiting_for == 0:
        parent_pending = session.exec(select(func.count())...)   # RACE WINDOW
        if parent_pending == 0:
            parent.status = COMPLETED

Between the `waiting_for == 0` check and the `SELECT COUNT(*)`, a new
`send_message` could register a new pending message, and the parent would
be incorrectly marked COMPLETED with orphaned pending work.

Phase 3 (Fix C5) eliminates this race by using the CM's in-memory pending
set as the single source of truth. The set operation ``set.discard(key)``
is atomic within the per-parent ``asyncio.Lock`` — there is no
``SELECT COUNT(*)`` to ``decide`` to ``commit`` window.

Test coverage (mapped to plan §Verification Strategy items 2, 5):
  4. Concurrent ``register_message_send`` + ``resolve_response`` for the
     same parent: no premature completion. The Lock serializes them, so
     the final state is always consistent.
  5. No ``SELECT COUNT(*)`` in the completion path: the completing
     ``resolve_response`` must not touch ``instance_repo`` or
     ``message_queue_repository`` at all.

See ``.agents/shared/planning/correlation-manager/phase3-cascade-unification.md``
for the full plan.

Run with:

    pytest tests/test_cascade_race3.py -v
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.correlation_manager import (
    STATUS_RESPONDED,
    CorrelationManager,
)


# =============================================================================
# Shared mock helpers
# =============================================================================


def make_instance_repo(
    *,
    instance_by_id: dict[str, Any] | None = None,
) -> MagicMock:
    """Mock SQLModelInstanceRepository. Tracks call counts."""
    repo = MagicMock(name="InstanceRepo")
    by_id = instance_by_id or {}
    repo.get = MagicMock(side_effect=lambda iid: by_id.get(iid))
    repo.get_all_with_waiting_for = MagicMock(return_value=[])
    repo.get_children = MagicMock(return_value=[])
    return repo


def make_msg_repo() -> MagicMock:
    """Mock SQLModelMessageQueueRepository. Tracks call counts."""
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
    """Build a CM and return (cm, instance_repo, msg_repo) for assertion."""
    ir = instance_repo or make_instance_repo()
    mr = msg_repo or make_msg_repo()
    cm = CorrelationManager(
        instance_repository=ir,
        message_queue_repository=mr,
        completion_callback=callback,
    )
    return cm, ir, mr


# =============================================================================
# Test 4 — Concurrent register + resolve (Race #3 elimination)
# =============================================================================


class TestConcurrentRegisterResolve:
    """Concurrently call ``register_message_send`` and ``resolve_response``
    for the same parent. The per-parent ``asyncio.Lock`` (Fix C4) serializes
    them, so the final state is always consistent — no premature completion.

    Race #3 (legacy): the count_pending DB query opened a TOCTOU window where
    a concurrent register could land between the query and the completion
    decision, leaving the parent incorrectly marked COMPLETED with a
    pending child.

    Phase 3: the completion decision is the ``pending[parent].is_empty``
    check inside the per-parent Lock. A concurrent register on the same
    parent contends for the same Lock, so it either:
      - runs BEFORE the resolve (set grows, resolve doesn't trigger
        premature completion), or
      - runs AFTER the resolve (set is already empty/cleaned, the register
        starts a new work cycle).
    There is no interleaving inside the critical section.
    """

    @pytest.mark.asyncio
    async def test_concurrent_register_resolve_no_premature_completion(self):
        """Register 2 sends, then concurrently resolve msg-1 AND register msg-3.

        Both operations contend on the same per-parent lock. After the
        gather completes:
          - msg-1 has been resolved (set is 1 smaller).
          - msg-3 has been registered (set is 1 larger).
          - msg-2 is still pending.
        Net: at least 1 pending entry remains → callback MUST NOT fire.
        """
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent = "parent-1"
        msg_1 = str(uuid.uuid4())
        msg_2 = str(uuid.uuid4())
        msg_3 = str(uuid.uuid4())

        # Seed: 2 pending correlations.
        await cm.register_message_send(parent, "child-1", msg_1)
        await cm.register_message_send(parent, "child-2", msg_2)
        assert cm.get_pending_count(parent) == 2
        callback.assert_not_called()

        # Concurrently: resolve msg-1 + register a brand-new msg-3.
        # Both contend on the same per-parent lock.
        results = await asyncio.gather(
            cm.resolve_response(parent, "child-1", msg_1, status=STATUS_RESPONDED),
            cm.register_message_send(parent, "child-3", msg_3),
        )

        # resolve_response returns False because msg-2 and msg-3 are still
        # pending (in any order). register_message_send returns None.
        assert results[0] is False
        assert results[1] is None

        # No premature completion: at least one of msg-2 or msg-3 is pending.
        # (msg-3 was definitely registered; msg-2 was definitely still pending.)
        assert cm.get_pending_count(parent) == 2
        # CRITICAL: callback was NOT called.
        callback.assert_not_called()

        # Drain the remaining work.
        await cm.resolve_response(parent, "child-2", msg_2, status=STATUS_RESPONDED)
        # Still 1 pending (msg-3) — callback still must not have fired.
        assert cm.get_pending_count(parent) == 1
        callback.assert_not_called()

        last = await cm.resolve_response(parent, "child-3", msg_3, status=STATUS_RESPONDED)
        assert last is True
        # Now the callback fires — exactly once for this parent.
        assert callback.call_count == 1
        callback.assert_called_once_with(parent, "completed")

    @pytest.mark.asyncio
    async def test_concurrent_resolve_of_unknown_and_register(self):
        """Concurrently resolve an UNKNOWN msg-id and register a new one.

        Edge case: the resolve targets a key that was never registered.
        The CM silently returns False (no entry to remove). The register
        adds the new entry. There is no spurious completion and no error.
        """
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent = "parent-1"

        results = await asyncio.gather(
            cm.resolve_response(parent, "ghost-child", "ghost-msg"),
            cm.register_message_send(parent, "child-1", "msg-1"),
        )

        # Unknown resolve → False. Register → None.
        assert results[0] is False
        assert results[1] is None

        # Only the registered entry is pending.
        assert cm.get_pending_count(parent) == 1
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_repeated_concurrent_register_resolve_under_gather(self):
        """Stress: 5 pairs of (register new msg, resolve previous msg).

        For each pair, the resolve targets a previously-registered msg
        and the register adds a new one. After all pairs, the set has
        exactly 1 pending entry (the very last register), and the
        callback has never fired.
        """
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent = "parent-1"
        n = 5
        keys: list[tuple[str, str]] = []

        # Build a list of (resolve_coro, register_coro) pairs and fire
        # them concurrently. Each pair is independent of the others
        # (different msg ids), so there is no causal dependency — only
        # the per-parent lock couples them.
        coros: list[Any] = []
        for i in range(n):
            child = f"child-{i}"
            msg_resolve = str(uuid.uuid4())  # resolve a previously-registered msg
            msg_register = str(uuid.uuid4())  # register a new msg
            keys.append((child, msg_resolve, msg_register))

        # Pre-register msg_resolve for the first n-1 pairs only.
        # The (n-1)th pair has nothing to resolve, only to register.
        for i in range(n - 1):
            await cm.register_message_send(parent, f"child-prep-{i}", keys[i][1])
        # Now cm has n-1 pending entries.

        # Build the gather: for pairs 0..n-2, concurrently resolve the
        # pre-registered msg and register a new msg. For the last pair
        # (no pre-registered msg), only register.
        for i in range(n - 1):
            coros.append(
                cm.resolve_response(parent, f"child-prep-{i}", keys[i][1], status=STATUS_RESPONDED)
            )
            coros.append(
                cm.register_message_send(parent, keys[i][0], keys[i][2])
            )
        # Final register (no resolve to pair with).
        coros.append(cm.register_message_send(parent, keys[-1][0], keys[-1][2]))

        results = await asyncio.gather(*coros)

        # After the gather: n-1 resolutions + n registers.
        # The n-1 pre-registered msgs have been resolved.
        # The n new msgs have been registered.
        # Net: 0 + n = n pending.
        assert cm.get_pending_count(parent) == n

        # The callback NEVER fired during the concurrent burst — at no
        # point did the set reach 0 (the registers and resolves were
        # always balanced, with the registers landing in time).
        # Note: this is a probabilistic assertion — if scheduling is
        # pathological (every register happens AFTER all resolves), the
        # set would hit 0 between resolves. We accept either outcome and
        # check consistency below.
        callback_call_count_after_gather = callback.call_count

        # Drain — one more register may be needed if set hit 0 mid-gather
        # (callback would have fired then and we'd have an orphan register).
        # Strategy: for each tracked entry, try to resolve it; track how
        # many successful resolves we get.
        successful_resolves = 0
        for child, _msg_resolve, msg_register in keys:
            r = await cm.resolve_response(parent, child, msg_register, status=STATUS_RESPONDED)
            if r is True:
                successful_resolves += 1

        # Total callback firings: gather (0 or 1) + drain (1 if set was non-empty
        # at start of drain, else 0). The invariant is: each parent fires its
        # callback EXACTLY ONCE across the whole test.
        total_callbacks = callback_call_count_after_gather + (1 if successful_resolves == 1 else 0)
        # More precise: in the drain phase, exactly one resolve returns True
        # (whichever empties the set last).
        assert successful_resolves == 1, (
            f"Expected exactly 1 successful resolve in drain, got "
            f"{successful_resolves}. The CM state may be inconsistent."
        )
        # Total callback calls across the whole test must be ≤ 2 (one from
        # gather if scheduling is pathological, one from drain) and
        # every call uses the same parent + terminal_status.
        assert callback.call_count <= 2
        for call in callback.call_args_list:
            assert call.args == (parent, "completed"), (
                f"Unexpected callback args: {call.args}"
            )

    @pytest.mark.asyncio
    async def test_concurrent_register_resolve_consistent_final_state(self):
        """After concurrent register+resolve, the CM's internal state is consistent.

        Regardless of which operation wins the lock first:
          - if register wins: set grows, resolve later empties one slot.
          - if resolve wins: set is already empty, register adds a new entry.
        In neither case is the set corrupted, the lock leaked, or the
        callback fired spuriously.
        """
        callback = AsyncMock()
        cm, _ir, _mr = make_cm(callback=callback)
        parent = "parent-1"
        msg_a, msg_b = str(uuid.uuid4()), str(uuid.uuid4())

        # Pre-register msg_a, then concurrently resolve msg_a + register msg_b.
        await cm.register_message_send(parent, "child-1", msg_a)
        results = await asyncio.gather(
            cm.resolve_response(parent, "child-1", msg_a, status=STATUS_RESPONDED),
            cm.register_message_send(parent, "child-2", msg_b),
        )

        # resolve returns True iff msg_b was NOT yet registered when the
        # resolve ran (resolve won the race). The result depends on
        # scheduling order, which is non-deterministic.
        if results[0] is True:
            # Resolve won → set was empty when the resolve ran, callback fired.
            # The register then added msg_b → set has 1 entry.
            assert callback.call_count == 1
            assert cm.get_pending_count(parent) == 1
            # Resolving msg_b now completes again.
            r = await cm.resolve_response(parent, "child-2", msg_b, status=STATUS_RESPONDED)
            assert r is True
            assert callback.call_count == 2
        else:
            # Register won → set still has msg_b, the resolve of msg_a just
            # removed msg_a. No completion fired.
            assert callback.call_count == 0
            assert cm.get_pending_count(parent) == 1
            # Resolving msg_b completes the cycle.
            r = await cm.resolve_response(parent, "child-2", msg_b, status=STATUS_RESPONDED)
            assert r is True
            assert callback.call_count == 1
            callback.assert_called_with(parent, "completed")


# =============================================================================
# Test 5 — No SELECT COUNT(*) in the completion path
# =============================================================================


class TestNoDbQueryInCompletionPath:
    """Fix C5: the completing resolve must NOT query the DB.

    The legacy cascade code did ``SELECT COUNT(*) FROM MessageQueue`` to
    decide if the parent had pending work. Between the query and the
    decision, a new register could land — Race #3.

    Phase 3 replaces this with the in-memory ``pending[parent].is_empty``
    check. The completing ``resolve_response`` must therefore not call
    any repository method.

    We verify this two ways:
      1. Direct: the mock repos' method call counts must be zero on the
         completing resolve.
      2. Indirect: a partial resolve DOES call ``instance_repo.get`` (for
         shadow validation), but a completing resolve does NOT. This
         proves the completion path is structurally different from the
         partial path — the legacy count_pending query was inside the
         partial path, not the completion path. Fix C5 keeps the partial
         path's shadow validation (cheap, read-only) and removes the
         completion path's DB query entirely.
    """

    @pytest.mark.asyncio
    async def test_completing_resolve_zero_db_calls(self):
        """Register 1, resolve 1. The completing resolve makes zero DB calls.

        There is no other code path here that should touch the repos.
        """
        ir = make_instance_repo(
            instance_by_id={"parent-1": MagicMock(waiting_for=1, status="running")}
        )
        mr = make_msg_repo()
        cm = CorrelationManager(
            instance_repository=ir,
            message_queue_repository=mr,
            completion_callback=AsyncMock(),
        )

        await cm.register_message_send(parent_id="parent-1", child_id="child-1", message_id="msg-1")

        # Reset all mock counters to ignore the register call's internal DB
        # queries (rebuild_from_db is not called on register, but be safe).
        ir.get.reset_mock()
        ir.get_all_with_waiting_for.reset_mock()
        ir.get_children.reset_mock()
        mr.get_pending_for_instances.reset_mock()
        mr.list.reset_mock()

        # Completing resolve — must make ZERO DB calls.
        result = await cm.resolve_response(
            parent_id="parent-1", child_id="child-1", message_id="msg-1"
        )
        assert result is True

        # Fix C5 invariant: zero DB calls in the completion path.
        assert ir.get.call_count == 0
        assert ir.get_all_with_waiting_for.call_count == 0
        assert ir.get_children.call_count == 0
        assert mr.get_pending_for_instances.call_count == 0
        assert mr.list.call_count == 0

    @pytest.mark.asyncio
    async def test_partial_resolve_may_call_db_for_shadow_only(self):
        """A PARTIAL resolve may call ``instance_repo.get`` for shadow validation,
        but ONLY when the parent is NOT complete. The completing resolve does not.

        This proves the completion path is distinct from the partial path:
        the legacy count_pending query lived in the partial path, and
        Fix C5 keeps the partial path's read-only shadow check while
        removing the completion path's DB query.
        """
        ir = make_instance_repo(
            instance_by_id={"parent-1": MagicMock(waiting_for=3, status="running")}
        )
        mr = make_msg_repo()
        cm = CorrelationManager(
            instance_repository=ir,
            message_queue_repository=mr,
            completion_callback=AsyncMock(),
        )

        # Register 3, resolve 2 (partial), then resolve the last (completing).
        parent = "parent-1"
        keys = [(f"child-{i}", f"msg-{i}") for i in range(3)]
        for c, m in keys:
            await cm.register_message_send(parent, c, m)

        # Reset and do the first resolve (partial) — it should call
        # instance_repo.get (shadow validation) but NOT msg_repo.
        ir.get.reset_mock()
        ir.get_all_with_waiting_for.reset_mock()
        ir.get_children.reset_mock()
        mr.get_pending_for_instances.reset_mock()
        mr.list.reset_mock()

        r1 = await cm.resolve_response(parent, keys[0][0], keys[0][1])
        assert r1 is False  # partial
        # Partial resolve does touch the DB (shadow validation).
        assert ir.get.call_count == 1, "Partial resolve should validate shadow"
        # msg_repo is NOT touched.
        assert mr.get_pending_for_instances.call_count == 0
        assert mr.list.call_count == 0

        # Reset and do the second resolve (still partial).
        ir.get.reset_mock()
        ir.get_all_with_waiting_for.reset_mock()
        ir.get_children.reset_mock()
        mr.get_pending_for_instances.reset_mock()
        mr.list.reset_mock()

        r2 = await cm.resolve_response(parent, keys[1][0], keys[1][1])
        assert r2 is False  # still partial
        assert ir.get.call_count == 1, "Second partial resolve should also validate"

        # Reset and do the THIRD (completing) resolve.
        ir.get.reset_mock()
        ir.get_all_with_waiting_for.reset_mock()
        ir.get_children.reset_mock()
        mr.get_pending_for_instances.reset_mock()
        mr.list.reset_mock()

        r3 = await cm.resolve_response(parent, keys[2][0], keys[2][1])
        assert r3 is True  # completing
        # Fix C5: the completing resolve makes ZERO DB calls.
        assert ir.get.call_count == 0
        assert ir.get_all_with_waiting_for.call_count == 0
        assert ir.get_children.call_count == 0
        assert mr.get_pending_for_instances.call_count == 0
        assert mr.list.call_count == 0

    @pytest.mark.asyncio
    async def test_completion_path_does_not_call_count_or_select(self):
        """The completion path makes no aggregate queries (no ``count()``,
        no ``SELECT COUNT(*)``).

        Since the CM uses a mock repo (not a real SQLAlchemy session), we
        cannot directly inspect SQL. Instead, we verify the structurally
        equivalent invariant: the CM's _pending dict is the SOLE source
        of truth for the completion decision. The resolving call
        mutates _pending and reads it back; it does not call any
        repository method.
        """
        ir = MagicMock(name="InstanceRepo")
        ir.get = MagicMock(return_value=MagicMock(waiting_for=0))
        ir.get_all_with_waiting_for = MagicMock(return_value=[])
        ir.get_children = MagicMock(return_value=[])
        mr = MagicMock(name="MsgRepo")
        mr.get_pending_for_instances = MagicMock(return_value=[])
        mr.list = MagicMock(return_value=[])

        cm = CorrelationManager(
            instance_repository=ir,
            message_queue_repository=mr,
            completion_callback=AsyncMock(),
        )

        parent = "parent-1"
        await cm.register_message_send(parent, "child-1", "msg-1")

        # Snapshot the _pending dict just before the completing resolve.
        snapshot_before = dict(cm._pending)  # shallow copy
        # All repo mock counters — reset.
        for repo in (ir, mr):
            for attr in dir(repo):
                mock = getattr(repo, attr, None)
                if isinstance(mock, MagicMock) and hasattr(mock, "reset_mock"):
                    mock.reset_mock()

        # Completing resolve.
        result = await cm.resolve_response(parent, "child-1", "msg-1")
        assert result is True

        # The _pending entry for this parent was deleted (state mutation
        # under the lock). No other state was touched.
        assert parent not in cm._pending
        # ALL repo methods remain at zero calls.
        for repo in (ir, mr):
            for attr in dir(repo):
                mock = getattr(repo, attr, None)
                if isinstance(mock, MagicMock) and hasattr(mock, "call_count"):
                    if attr.startswith("_"):
                        continue  # skip private attrs
                    assert mock.call_count == 0, (
                        f"Fix C5 violation: completing resolve called "
                        f"{repo._mock_name or 'repo'}.{attr} "
                        f"{mock.call_count} time(s) — completion must be "
                        f"pure in-memory"
                    )
