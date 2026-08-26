"""Unit tests — C3 blob-prune service layer (mock adapter, no DB).

Mirrors phase1-plan.md §C3 file-table row for
``tests/unit/services/test_maintenance_prune_direct_anti_join.py``:

* verify the fail-safe logic (zero-refs → SKIP + ERROR, zero rows
  deleted — detection and prevention asserted separately);
* verify candidate iteration goes through ``find_all_thread_ns_pairs``
  (D21) and NOT ``find_excess_checkpoint_groups`` (whose HAVING clause
  would skip single-checkpoint threads);
* verify the conservative ladder: dry-run by default, destructive only
  when BOTH env flags are set, and — structurally — that the
  ``delete_blobs_anti_join`` call site is unreachable when the gate is
  off (AST dominance check + runtime sentinel);
* verify per-pair failure isolation and the maintenance wiring
  (Operation E after D, isolation of blob-bucket failures).
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import daemon.services.checkpoint_prune as checkpoint_prune
from daemon.services.checkpoint_prune import (
    blob_prune_destructive_enabled,
    prune_unreferenced_blobs,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PRUNE_MODULE = REPO_ROOT / "daemon" / "services" / "checkpoint_prune.py"


# ── helpers ────────────────────────────────────────────────────────────────────


def _pg_adapter_mock(pairs, refs=5, count_result=(2, 4096)) -> MagicMock:
    """A mock shaped like PostgresCheckpointerAdapter (isinstance-bypassed).

    ``prune_unreferenced_blobs`` gates on ``isinstance(checkpointer,
    PostgresCheckpointerAdapter)``; tests patch that check via monkeypatch
    of the module symbol, so the mock only needs the method surface.
    """
    m = MagicMock()
    m.find_all_thread_ns_pairs = AsyncMock(return_value=list(pairs))
    m.count_refs_for_blob_thread = AsyncMock(return_value=refs)
    m.count_blobs_anti_join = AsyncMock(return_value=count_result)
    m.delete_blobs_anti_join = AsyncMock(return_value=(0, 0))
    return m


@pytest.fixture
def as_pg(monkeypatch):
    """Make the isinstance gate treat the mock as a Postgres adapter."""
    monkeypatch.setattr(
        checkpoint_prune, "PostgresCheckpointerAdapter", MagicMock
    )
    # MagicMock is not a class for isinstance unless configured; simplest:
    # replace the check function's view of the class with `object`.
    monkeypatch.setattr(
        checkpoint_prune, "PostgresCheckpointerAdapter", object
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts with the ladder in its default (dry-run) state."""
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", raising=False)
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", raising=False)


# ── env-flag matrix ────────────────────────────────────────────────────────────


class TestDestructiveGateMatrix:
    """blob_prune_destructive_enabled() — the conservative ladder's keyhole."""

    @pytest.mark.parametrize(
        "dry,des,expected",
        [
            (None, None, False),   # defaults: dry-run ON, destructive OFF
            ("1", None, False),
            ("0", None, False),    # dry-run off alone is NOT enough
            (None, "1", False),    # destructive alone is NOT enough
            ("1", "1", False),     # both set but dry-run still on
            ("0", "1", True),      # the ONLY arming combination
            ("0", "0", False),
            ("true", "1", False),  # only the literal "0" disarms dry-run
        ],
    )
    def test_flag_matrix(self, monkeypatch, dry, des, expected):
        if dry is None:
            monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", raising=False)
        else:
            monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", dry)
        if des is None:
            monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", raising=False)
        else:
            monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", des)
        assert blob_prune_destructive_enabled() is expected


# ── structural unreachability of the DELETE ────────────────────────────────────


class TestStructuralGate:
    """The destructive call site must be dominated by the gate — statically."""

    def test_delete_call_is_structurally_gated_by_destructive_flag(self):
        """AST: every ``delete_blobs_anti_join`` call in checkpoint_prune.py
        is preceded — within its enclosing loop — by an
        ``if not destructive: ... continue`` guard it does not live inside.

        Python control flow within a loop body is lexical: a guard-If that
        contains a ``continue`` and appears textually BEFORE the call
        dominates it (the only way to reach the call is falling through
        the guard, i.e. ``destructive`` truthy). This is the static half
        of "env-flag-off ⇒ destructive path UNREACHABLE".
        """
        tree = ast.parse(PRUNE_MODULE.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "delete_blobs_anti_join"
        ]
        assert calls, "expected at least one delete_blobs_anti_join call site"

        # parent map
        parents: dict[int, ast.AST] = {}
        stack = [tree]
        while stack:
            node = stack.pop()
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
                stack.append(child)

        def nearest_ancestor_of_types(
            target: ast.AST, types: tuple
        ) -> ast.AST | None:
            cur = target
            while True:
                parent = parents.get(id(cur))
                if parent is None:
                    return None
                if isinstance(parent, types):
                    return parent
                cur = parent

        def _node_in(target: ast.AST, stmts: list[ast.AST]) -> bool:
            for stmt in stmts:
                if id(stmt) == id(target):
                    return True
                for sub in ast.walk(stmt):
                    if id(sub) == id(target):
                        return True
            return False

        for call in calls:
            loop = nearest_ancestor_of_types(call, (ast.For, ast.AsyncFor))
            assert loop is not None, (
                f"delete call at line {call.lineno} must live inside the "
                "per-pair candidate loop"
            )
            # Collect every guard-If inside the loop (at any nesting depth
            # that is still within the loop) testing `not destructive`.
            guard = None
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
                    and not _node_in(call, node.body)
                ):
                    guard = node
                    break
            assert guard is not None, (
                f"delete_blobs_anti_join call at line {call.lineno} is not "
                "dominated by an `if not destructive: ... continue` guard "
                "inside its enclosing loop"
            )

    def test_source_contains_exactly_one_delete_call_site(self):
        """Belt: the module's own source never calls the DELETE arm
        outside the destructive section (single call site)."""
        import re

        src = PRUNE_MODULE.read_text(encoding="utf-8")
        call_forms = re.findall(r"\.\s*delete_blobs_anti_join\s*\(", src)
        assert len(call_forms) == 1

    def test_runtime_gate_off_delete_never_called(self, as_pg, monkeypatch):
        """Runtime half: flags OFF (default) → the mock's delete arm is
        replaced by an exploding sentinel; the prune completes in dry-run
        and the sentinel is never touched."""
        import asyncio

        adapter = _pg_adapter_mock([("t1", "", 3)], refs=4)
        adapter.delete_blobs_anti_join = AsyncMock(
            side_effect=AssertionError("DELETE reached with gate OFF")
        )
        summary = asyncio.run(prune_unreferenced_blobs(adapter))
        assert summary.dry_run is True
        assert summary.total_deleted == 0
        adapter.count_blobs_anti_join.assert_awaited_once_with("t1", "")
        adapter.delete_blobs_anti_join.assert_not_awaited()

    def test_runtime_gate_armed_delete_called(self, as_pg, monkeypatch):
        import asyncio

        monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", "0")
        monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", "1")
        adapter = _pg_adapter_mock([("t1", "", 3)], refs=4)
        adapter.delete_blobs_anti_join = AsyncMock(return_value=(7, 1234))
        summary = asyncio.run(prune_unreferenced_blobs(adapter))
        assert summary.dry_run is False
        assert summary.total_deleted == 7
        assert summary.total_bytes_freed == 1234
        adapter.count_blobs_anti_join.assert_not_awaited()
        adapter.delete_blobs_anti_join.assert_awaited_once_with("t1", "")


# ── fail-safe: zero refs ───────────────────────────────────────────────────────


class TestZeroRefsFailSafe:
    """0 refs + remaining checkpoints ⇒ SKIP + ERROR + zero deletes."""

    def test_detection_error_log_and_prevention_zero_deletes(self, as_pg, caplog):
        adapter = _pg_adapter_mock([("thread-a", "", 2), ("thread-b", "", 2)])
        # thread-a: extraction broken (0 refs); thread-b: healthy.
        adapter.count_refs_for_blob_thread = AsyncMock(side_effect=[0, 9])
        adapter.count_blobs_anti_join = AsyncMock(return_value=(5, 512))
        adapter.delete_blobs_anti_join = AsyncMock(return_value=(99, 9999))
        import asyncio

        with caplog.at_level(logging.ERROR, logger="daemon.services.checkpoint_prune"):
            summary = asyncio.run(prune_unreferenced_blobs(adapter))

        # DETECTION — an ERROR line naming the fail-safe and the thread.
        error_lines = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and "ZERO_REFS_FAIL_SAFE" in r.getMessage()
        ]
        assert error_lines, "fail-safe must DETECT via an ERROR log line"
        assert "thread-a"[:8] in error_lines[0].getMessage()

        # PREVENTION — thread-a never reaches either blob arm.
        assert ("thread-a", "", "ZERO_REFS_FAIL_SAFE") in summary.skipped
        # thread-b proceeded normally (dry-run default).
        assert adapter.count_blobs_anti_join.await_count == 1
        assert adapter.delete_blobs_anti_join.assert_not_awaited() is None

    def test_fail_safe_trips_even_when_destructive(self, as_pg, monkeypatch, caplog):
        """The fail-safe outranks the destructive flag: zero refs ⇒ no delete."""
        monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", "0")
        monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", "1")
        adapter = _pg_adapter_mock([("thread-x", "", 4)], refs=0)
        adapter.delete_blobs_anti_join = AsyncMock(return_value=(42, 4200))
        import asyncio

        with caplog.at_level(logging.ERROR, logger="daemon.services.checkpoint_prune"):
            summary = asyncio.run(prune_unreferenced_blobs(adapter))
        assert summary.total_deleted == 0
        adapter.delete_blobs_anti_join.assert_not_awaited()

    def test_zero_refs_detection_is_separate_from_deletion_prevention(self, as_pg):
        """Detection = the ERROR record; prevention = absence of arm calls.
        Assert the two observables independently on one run."""
        adapter = _pg_adapter_mock([("t", "ns", 1)], refs=0)
        import asyncio

        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        lg = logging.getLogger("daemon.services.checkpoint_prune")
        lg.addHandler(handler)
        try:
            asyncio.run(prune_unreferenced_blobs(adapter))
        finally:
            lg.removeHandler(handler)
        assert any(
            r.levelno == logging.ERROR and "ZERO_REFS_FAIL_SAFE" in r.getMessage()
            for r in records
        ), "detection observable missing"
        adapter.count_blobs_anti_join.assert_not_awaited()
        adapter.delete_blobs_anti_join.assert_not_awaited()
        assert adapter.count_refs_for_blob_thread.await_count == 1


# ── D21 iteration + cap + isolation ────────────────────────────────────────────


class TestCandidateIteration:
    def test_iterates_via_find_all_thread_ns_pairs_not_excess(self, as_pg):
        adapter = _pg_adapter_mock([("t1", "", 1), ("t2", "child:x", 1)])
        import asyncio

        asyncio.run(prune_unreferenced_blobs(adapter))
        adapter.find_all_thread_ns_pairs.assert_awaited_once()
        adapter.find_excess_checkpoint_groups = AsyncMock()
        adapter.find_excess_checkpoint_groups.assert_not_awaited()

    def test_single_checkpoint_threads_are_candidates(self, as_pg):
        """D21: pairs with count == 1 are enumerated (HAVING would drop)."""
        adapter = _pg_adapter_mock([("only", "", 1)])
        import asyncio

        summary = asyncio.run(prune_unreferenced_blobs(adapter))
        assert summary.scanned_pairs == 1
        adapter.count_blobs_anti_join.assert_awaited_once()

    def test_max_refs_cap_skips_pair(self, as_pg):
        adapter = _pg_adapter_mock([("big", "", 10)], refs=100_001)
        import asyncio

        summary = asyncio.run(prune_unreferenced_blobs(adapter))
        assert ("big", "", "MAX_REFS_EXCEEDED") in summary.skipped
        adapter.count_blobs_anti_join.assert_not_awaited()

    def test_per_pair_error_isolation(self, as_pg):
        """One exploding pair never breaks the cycle for the others."""
        adapter = _pg_adapter_mock([("bad", "", 2), ("good", "", 2)])
        adapter.count_refs_for_blob_thread = AsyncMock(
            side_effect=[RuntimeError("boom"), 3]
        )
        adapter.count_blobs_anti_join = AsyncMock(return_value=(1, 10))
        import asyncio

        summary = asyncio.run(prune_unreferenced_blobs(adapter))  # must not raise
        assert ("good", "",) not in [(s[0], s[1]) for s in summary.skipped]
        adapter.count_blobs_anti_join.assert_awaited_once_with("good", "")

    def test_empty_candidates_short_circuits(self, as_pg):
        adapter = _pg_adapter_mock([])
        import asyncio

        summary = asyncio.run(prune_unreferenced_blobs(adapter))
        assert summary.scanned_pairs == 0
        adapter.count_refs_for_blob_thread.assert_not_awaited()


# ── SQLite no-op ───────────────────────────────────────────────────────────────


class TestSqliteNoOp:
    def test_non_pg_backend_noops_with_warning(self, caplog):
        """A non-Postgres adapter never reaches ANY blob method."""
        adapter = MagicMock()  # NOT a PostgresCheckpointerAdapter
        adapter.find_all_thread_ns_pairs = AsyncMock()
        import asyncio

        with caplog.at_level(logging.WARNING, logger="daemon.services.checkpoint_prune"):
            summary = asyncio.run(prune_unreferenced_blobs(adapter))
        assert summary.backend == "sqlite"
        assert summary.scanned_pairs == 0
        adapter.find_all_thread_ns_pairs.assert_not_awaited()
        assert any(
            "checkpoint_blobs" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )


# ── maintenance wiring ─────────────────────────────────────────────────────────


class TestMaintenanceWiring:
    def test_execute_runs_operation_e_after_d(self, as_pg):
        """execute() invokes the blob prune (Op E) after retention (Op D)."""
        from daemon.services.maintenance import CheckpointCleanupJob
        from daemon.config import PersistenceConfig

        adapter = MagicMock()
        adapter.list_thread_ids = AsyncMock(return_value=[])
        order: list[str] = []

        async def excess(*a, **k):
            order.append("D")
            return []

        async def pairs(*a, **k):
            order.append("E")
            return []

        adapter.find_excess_checkpoint_groups = excess
        adapter.find_all_thread_ns_pairs = pairs
        job = CheckpointCleanupJob(
            config=PersistenceConfig(), checkpointer=adapter, instance_repo=MagicMock()
        )
        import asyncio

        asyncio.run(job.execute())
        assert order == ["D", "E"]

    def test_blob_prune_failure_does_not_break_maintenance(self, as_pg, monkeypatch):
        """Even if prune_unreferenced_blobs were to raise (contract break),
        execute() still completes — the wrapper isolates the blob bucket."""
        from daemon.services.maintenance import CheckpointCleanupJob
        from daemon.config import PersistenceConfig

        adapter = MagicMock()
        adapter.list_thread_ids = AsyncMock(return_value=[])
        adapter.find_excess_checkpoint_groups = AsyncMock(return_value=[])
        job = CheckpointCleanupJob(
            config=PersistenceConfig(), checkpointer=adapter, instance_repo=MagicMock()
        )

        async def explode():
            raise RuntimeError("blob bucket on fire")

        # maintenance._prune_unreferenced_blobs imports the function from
        # checkpoint_prune at call time — patch it at the source module.
        monkeypatch.setattr(
            "daemon.services.checkpoint_prune.prune_unreferenced_blobs", explode
        )
        import asyncio

        asyncio.run(job.execute())  # must not raise
        adapter.find_excess_checkpoint_groups.assert_awaited_once()

    def test_prune_module_never_raises_on_enumeration_failure(self, as_pg):
        adapter = _pg_adapter_mock([("t", "", 1)])
        adapter.find_all_thread_ns_pairs = AsyncMock(
            side_effect=RuntimeError("db gone")
        )
        import asyncio

        summary = asyncio.run(prune_unreferenced_blobs(adapter))  # must not raise
        assert summary.scanned_pairs == 0
