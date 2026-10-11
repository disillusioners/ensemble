"""Unit tests — Section 1 (Maintenance Console) T2 destructive override kwarg.

``phase1-backend.md`` §4.1 — the ``destructive`` override kwarg on
:func:`prune_unreferenced_blobs` plus the T1 dry-run accumulation
fields and the AM-2 ordering pin. The historical dual-arm env-flag
gate is GONE (feature/blob-prune-default-on); the kwarg is the only
arbiter — explicit ``True`` matches the auto-cycle default,
explicit ``False`` is the only opt-out (manual preview / dry-run).

* INV-1 — the default (``destructive`` kwarg omitted) reaches the
  DELETE arm (the auto cycle path lands here unconditionally).
* INV-2 — ``destructive=True`` reaches the DELETE arm (manual execute
  matching the default).
* INV-3 — the zero-refs fail-safe and MAX_REFS cap hold under both
  ``True`` (default and explicit) and the historical ``False`` kwarg
  (the structural gate + fail-safes are untouched).
* AM-11 — the dry-run arm accumulates the canonical
  ``would_delete_count`` / ``would_free_bytes`` fields.
* AM-2 (case 9a) — the MANUAL entry point
  (``CheckpointCleanupJob.run_checkpoint_prunes``) awaits the blob arm
  (Op E) BEFORE the row arm (Op D); the auto ``execute()`` keeps its
  inline D→E order (pinned separately by
  ``test_maintenance_prune_direct_anti_join.py``).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import daemon.services.checkpoint_prune as checkpoint_prune
from daemon.services.checkpoint_prune import (
    BlobPruneSummary,
    prune_unreferenced_blobs,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PRUNE_MODULE = REPO_ROOT / "daemon" / "services" / "checkpoint_prune.py"
MAINTENANCE_MODULE = REPO_ROOT / "daemon" / "services" / "maintenance.py"


# ── helpers ────────────────────────────────────────────────────────────────────


def _pg_adapter_mock(pairs, refs=5, count_result=(2, 4096)) -> MagicMock:
    """Mock shaped like PostgresCheckpointerAdapter (same as the
    anti-join suite's ``_pg_adapter_mock`` — the isinstance gate is
    monkeypatched to ``object`` by the ``as_pg`` fixture)."""
    m = MagicMock()
    m.find_all_thread_ns_pairs = AsyncMock(return_value=list(pairs))
    m.count_refs_for_blob_thread = AsyncMock(return_value=refs)
    m.count_blobs_anti_join = AsyncMock(return_value=count_result)
    m.delete_blobs_anti_join = AsyncMock(return_value=(2, 4096))
    return m


@pytest.fixture
def as_pg(monkeypatch):
    monkeypatch.setattr(
        checkpoint_prune, "PostgresCheckpointerAdapter", object
    )


@pytest.fixture(autouse=True)
def _default_ladder(monkeypatch):
    """Sentinel — no env vars named for the destructive ladder exist
    anymore (feature/blob-prune-default-on retired
    ``CHECKPOINT_BLOB_PRUNE_DRY_RUN`` and ``CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE``).
    The fixture stays here as a tripwire so any future re-introduction
    of env-state dependency in the prune module surfaces as a stale
    ``delenv`` against an undefined name — which is what we want.
    """
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", raising=False)
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", raising=False)


def _boom_delete() -> AsyncMock:
    """A sentinel DELETE arm that explodes if reached."""
    sentinel = AsyncMock(side_effect=AssertionError("DELETE arm reached"))
    return sentinel


# ── §4.1 cases 1–6 — the override kwarg vs the destructive default ────────────


class TestDestructiveOverrideKwarg:
    async def test_default_reaches_delete_unconditionally(self, as_pg):
        """Case 1 — no kwarg, default = destructive=True → DELETE arm
        is reached (the gate is gone; the auto cycle path lands here)."""
        adapter = _pg_adapter_mock([("t-1", "", 3)])
        summary = await prune_unreferenced_blobs(adapter)
        assert summary.dry_run is False
        assert summary.destructive is True
        adapter.count_blobs_anti_join.assert_not_awaited()
        adapter.delete_blobs_anti_join.assert_awaited_once_with("t-1", "")

    async def test_explicit_destructive_true_matches_default(self, as_pg):
        """Case 2 — explicit ``destructive=True`` produces the same
        observable result as the kwarg-less default. Defends against
        a refactor that flips the default without leaving the
        explicit-True path behind."""
        adapter = _pg_adapter_mock([("t-1", "", 3)])
        summary = await prune_unreferenced_blobs(adapter, destructive=True)
        assert summary.destructive is True
        adapter.delete_blobs_anti_join.assert_awaited_once_with("t-1", "")

    async def test_explicit_destructive_false_overrides_default(self, as_pg):
        """Case 3 — ``destructive=False`` is the SOLE way to opt out;
        the explicit kwarg reaches the SELECT-only count arm and the
        DELETE sentinel is untouched."""
        adapter = _pg_adapter_mock([("t-1", "", 3)])
        adapter.delete_blobs_anti_join = _boom_delete()
        summary = await prune_unreferenced_blobs(adapter, destructive=False)
        assert summary.dry_run is True
        assert summary.destructive is False
        adapter.count_blobs_anti_join.assert_awaited_once_with("t-1", "")
        adapter.delete_blobs_anti_join.assert_not_awaited()

    async def test_destructive_false_preserves_zero_refs_fail_safe(
        self, as_pg, caplog
    ):
        """Case 4 — INV-3: kwarg False + refs=0 staged → ERROR log + skip,
        DELETE untouched (fail-safe fires BEFORE the dry-run guard)."""
        import logging

        adapter = _pg_adapter_mock([("t-1", "", 3)], refs=0)
        adapter.delete_blobs_anti_join = _boom_delete()
        with caplog.at_level(logging.ERROR, logger="daemon.services.checkpoint_prune"):
            summary = await prune_unreferenced_blobs(adapter, destructive=False)
        assert summary.skipped == [("t-1", "", "ZERO_REFS_FAIL_SAFE")]
        assert summary.total_deleted == 0
        adapter.delete_blobs_anti_join.assert_not_awaited()
        assert any("ZERO_REFS_FAIL_SAFE" in r.message for r in caplog.records) or any(
            "zero rows deleted" in r.message for r in caplog.records
        )

    async def test_destructive_default_preserves_zero_refs_fail_safe(
        self, as_pg, caplog
    ):
        """Case 5 — INV-3: default path (destructive) + refs=0 staged →
        same fail-safe fires (the destructive default never short-circuits
        the zero-refs pre-check)."""
        import logging

        adapter = _pg_adapter_mock([("t-1", "", 3)], refs=0)
        adapter.delete_blobs_anti_join = _boom_delete()
        with caplog.at_level(logging.ERROR, logger="daemon.services.checkpoint_prune"):
            summary = await prune_unreferenced_blobs(adapter)
        assert summary.skipped == [("t-1", "", "ZERO_REFS_FAIL_SAFE")]
        assert summary.total_deleted == 0
        adapter.delete_blobs_anti_join.assert_not_awaited()

    async def test_destructive_default_preserves_max_refs_skip(self, as_pg):
        """Case 6 — INV-3: default path + refs over the cap →
        MAX_REFS_EXCEEDED skip, DELETE untouched."""
        adapter = _pg_adapter_mock(
            [("t-1", "", 3)],
            refs=checkpoint_prune.CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD + 1,
        )
        adapter.delete_blobs_anti_join = _boom_delete()
        summary = await prune_unreferenced_blobs(adapter)
        assert summary.skipped == [("t-1", "", "MAX_REFS_EXCEEDED")]
        adapter.delete_blobs_anti_join.assert_not_awaited()

    def test_ast_dominance_pin_still_green(self):
        """Case 8 — thin re-run guard: the AST dominance predicate from
        ``test_maintenance_prune_direct_anti_join.py::
        test_delete_call_is_structurally_gated_by_destructive_flag``
        holds against the modified module (the authoritative assertion
        lives in that file; this witness documents that the override
        kwarg did not break the structural gate — INV-3 / R-9)."""
        src = PRUNE_MODULE.read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "delete_blobs_anti_join"
        ]
        assert calls, "expected at least one delete call site"
        # Single call site pin (companion of the dominance pin).
        call_forms = re.findall(r"\.\s*delete_blobs_anti_join\s*\(", src)
        assert len(call_forms) == 1
        # The local feeding the guard is still named ``destructive``
        # (the pin keys on ``ast.Name(id="destructive")`` — R-9).
        parents: dict[int, ast.AST] = {}
        stack = [tree]
        while stack:
            node = stack.pop()
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
                stack.append(child)

        def nearest_ancestor_of_types(target, types):
            cur = target
            while True:
                parent = parents.get(id(cur))
                if parent is None:
                    return None
                if isinstance(parent, types):
                    return parent
                cur = parent

        for call in calls:
            loop = nearest_ancestor_of_types(call, (ast.For, ast.AsyncFor))
            assert loop is not None
            found_guard = False
            for node in ast.walk(loop):
                if not isinstance(node, ast.If):
                    continue
                test = node.test
                is_not_destructive = (
                    isinstance(test, ast.UnaryOp)
                    and isinstance(test.op, ast.Not)
                    and isinstance(test.operand, ast.Name)
                    and test.operand.id == "destructive"
                )
                if (
                    is_not_destructive
                    and any(isinstance(s, ast.Continue) for s in node.body)
                    and node.lineno < call.lineno
                ):
                    found_guard = True
                    break
            assert found_guard, (
                f"delete call at line {call.lineno} lost its "
                "`if not destructive: ... continue` guard"
            )


# ── §4.1 case 9 — dry-run accumulation fields (AM-11) ─────────────────────────


class TestDryRunAccumulation:
    async def test_dry_run_accumulation_fields_populated(self, as_pg):
        """Case 9 — dry-run sweep (manual preview path with
        ``destructive=False``) fills ``would_delete_count`` /
        ``would_free_bytes`` (canonical AM-11 names); destructive
        sweep (the new default) fills ``total_deleted`` /
        ``total_bytes_freed`` and leaves the would-fields at 0."""
        adapter = _pg_adapter_mock(
            [("t-1", "", 3), ("t-2", "", 3)], count_result=(4, 2048)
        )
        dry = await prune_unreferenced_blobs(adapter, destructive=False)
        assert dry.would_delete_count == 8  # 2 pairs × 4
        assert dry.would_free_bytes == 4096  # 2 pairs × 2048
        assert dry.total_deleted == 0
        assert dry.total_bytes_freed == 0

        adapter2 = _pg_adapter_mock(
            [("t-1", "", 3), ("t-2", "", 3)],
            count_result=(4, 2048),
        )
        adapter2.delete_blobs_anti_join = AsyncMock(return_value=(4, 2048))
        destructive = await prune_unreferenced_blobs(adapter2)  # default
        assert destructive.total_deleted == 8
        assert destructive.total_bytes_freed == 4096
        assert destructive.would_delete_count == 0
        assert destructive.would_free_bytes == 0

        # to_summary_blobs_dict dual-flavor keys (AM-11) — the wire
        # shape contract for the ``blobs`` block (§2 / §3).
        dry_dict = dry.to_summary_blobs_dict()
        assert dry_dict["would_delete_count"] == 8
        assert dry_dict["would_free_bytes"] == 4096
        assert dry_dict["would_delete"] == 8
        assert dry_dict["bytes"] == 4096
        assert dry_dict["destructive"] is False
        assert dry_dict["skipped"] == []
        assert dry_dict["skipped_truncated"] is False
        d_dict = destructive.to_summary_blobs_dict()
        assert d_dict["deleted"] == 8
        assert d_dict["bytes_freed"] == 4096
        assert d_dict["destructive"] is True


# ── §4.1 case 9a — AM-2 ordering pin (MANUAL entry point) ─────────────────────


class TestManualEntryPointOrdering:
    def test_manual_entry_point_orders_blobs_before_rows(self):
        """Case 9a — [AM-2, BLOCKING] call-order pin.

        ``run_checkpoint_prunes(destructive=True)`` awaits the BLOB arm
        (Op E) before the ROW arm (Op D). The auto ``execute()`` is
        unaffected (its inline D→E order is pinned by the existing
        anti-join suite + the perf suite).

        NOTE (reviewer carry-note, approver fold-in (d)): the
        mock-call-sequence assertion below is FRAGILE against a future
        ``asyncio.gather`` refactor of the manual entry point — if the
        two arms are ever gathered concurrently, entry order no longer
        implies completion order and this pin needs a different
        mechanism (e.g. dependency injection or a shared event).
        """
        import asyncio

        from daemon.config import PersistenceConfig
        from daemon.services.maintenance import CheckpointCleanupJob

        adapter = MagicMock()
        adapter.list_thread_ids = AsyncMock(return_value=[])
        adapter.find_excess_checkpoint_groups = AsyncMock(return_value=[])
        adapter.find_all_thread_ns_pairs = AsyncMock(return_value=[])
        adapter.get_checkpoint_ids = AsyncMock(return_value=["ck-1"])

        job = CheckpointCleanupJob(
            config=PersistenceConfig(),
            checkpointer=adapter,
            instance_repo=MagicMock(),
        )
        order: list[str] = []
        real_e = job._prune_unreferenced_blobs
        real_d = job._prune_per_thread_checkpoints

        async def wrap_e(*a, **k):
            order.append("E")
            return await real_e(*a, **k)

        async def wrap_d(*a, **k):
            order.append("D")
            return await real_d(*a, **k)

        job._prune_unreferenced_blobs = wrap_e  # type: ignore[method-assign]
        job._prune_per_thread_checkpoints = wrap_d  # type: ignore[method-assign]

        result = asyncio.run(job.run_checkpoint_prunes(destructive=True))
        assert order == ["E", "D"], (
            "AM-2 violation: the MANUAL entry point must compose Op E "
            "(blobs) BEFORE Op D (rows); auto keeps D→E (INV-9)"
        )
        assert isinstance(result.rows, object)
        assert result.duration_ms >= 0

    def test_manual_none_kwarg_lands_destructive_true(self):
        """FIX C (fix cycle 1) pin — ``destructive=None`` must land on
        ``destructive=True`` end-to-end through the manual entry point.

        The docblock documents ``None`` → the auto-cycle (destructive)
        semantics, but the old ``bool(destructive)`` coercion made
        ``None`` → ``False`` (dry-run) — silently inverting the
        contract. The fixed coercion
        (``destructive if destructive is not None else True``) must
        drive the blob arm (asserted here through the real
        ``_prune_unreferenced_blobs`` call) AND the row-arm branch.
        """
        import asyncio

        from daemon.config import PersistenceConfig
        from daemon.services.maintenance import CheckpointCleanupJob

        adapter = MagicMock()
        adapter.list_thread_ids = AsyncMock(return_value=[])
        adapter.find_excess_checkpoint_groups = AsyncMock(return_value=[])
        adapter.find_all_thread_ns_pairs = AsyncMock(return_value=[])
        adapter.get_checkpoint_ids = AsyncMock(return_value=["ck-1"])

        job = CheckpointCleanupJob(
            config=PersistenceConfig(),
            checkpointer=adapter,
            instance_repo=MagicMock(),
        )
        seen_kwargs: list[dict] = []
        real_e = job._prune_unreferenced_blobs

        async def wrap_e(*a, **k):
            seen_kwargs.append(k)
            return await real_e(*a, **k)

        job._prune_unreferenced_blobs = wrap_e  # type: ignore[method-assign]

        row_calls: list[bool] = []
        real_d = job._prune_per_thread_checkpoints

        async def wrap_d(*a, **k):
            row_calls.append(True)
            return await real_d(*a, **k)

        job._prune_per_thread_checkpoints = wrap_d  # type: ignore[method-assign]

        result = asyncio.run(job.run_checkpoint_prunes(destructive=None))

        # The blob arm received destructive=True (the documented None
        # semantics) — not the old bool(None) → False.
        assert seen_kwargs and seen_kwargs[0].get("destructive") is True, (
            "None must land on destructive=True (fix C); "
            f"got {seen_kwargs[0] if seen_kwargs else 'no call'}"
        )
        # The row-arm branch follows the SAME coerced value — None
        # must not compose a destructive blob arm with a dry-run row
        # projection: the DESTRUCTIVE compose (not
        # _compute_row_prune_dry_run) is the branch taken.
        assert row_calls == [True], (
            "None must compose the destructive row arm "
            "(coerced value drives BOTH arms)"
        )
        assert result.duration_ms >= 0

    def test_manual_dry_run_entry_point_is_read_only(self):
        """Companion to 9a (§4.2 case 17 lives in the lock/capture
        suite; this pins the entry-point composition cheaply here too):
        ``destructive=False`` passes ``destructive=False`` to the blob
        arm so the manual preview stays SELECT-only. With the
        destructive env-flag gate gone, the explicit ``False`` kwarg
        is the only opt-out path — the entry point must never collapse
        to ``None`` for the dry-run compose."""
        import asyncio

        from daemon.config import PersistenceConfig
        from daemon.services.maintenance import CheckpointCleanupJob

        adapter = MagicMock()
        adapter.find_all_thread_ns_pairs = AsyncMock(return_value=[])
        adapter.find_excess_checkpoint_groups = AsyncMock(return_value=[])
        job = CheckpointCleanupJob(
            config=PersistenceConfig(),
            checkpointer=adapter,
            instance_repo=MagicMock(),
        )
        seen_kwargs: list[dict] = []
        real_e = job._prune_unreferenced_blobs

        async def wrap_e(*a, **k):
            seen_kwargs.append(k)
            return await real_e(*a, **k)

        job._prune_unreferenced_blobs = wrap_e  # type: ignore[method-assign]
        asyncio.run(job.run_checkpoint_prunes(destructive=False))
        assert seen_kwargs and seen_kwargs[0].get("destructive") is False, (
            "the manual dry-run blob arm must pass destructive=False "
            "explicitly"
        )
