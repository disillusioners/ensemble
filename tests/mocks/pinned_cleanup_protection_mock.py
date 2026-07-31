"""Independent mock test for Pinned Instance Cleanup Protection.

Drives the REAL ``CheckpointCleanupJob`` (in-process, no HTTP, no ports) against
a real in-memory SQLite database + real ``SQLModelInstanceRepository`` and
``InstanceUiPrefsRepository``. Does NOT import or call the dev's pytest tests.

The script builds 8 isolated scenarios in fresh DBs and runs them in sequence
under a hard 120s self-timeout. Each scenario sets up its own state, runs the
relevant operation, and asserts the expected outcome (the pinned subtree
must be excluded from TTL-based and history-cap cleanup).

The script NEVER modifies production source code. If a scenario FAILS, that
is reported — fixing the bug is the developer's job, not this test's.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Ensure repo root is on the path so the daemon package is importable when
# the script is invoked from any CWD.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

# Importing the repositories package registers every SQLModel table on
# ``SQLModel.metadata`` BEFORE ``create_all`` is called.
import daemon.repositories  # noqa: E402,F401

# We also import the model modules directly so the import side-effects are
# guaranteed even if the package re-exports change later.
from daemon.repositories.instance.models import (  # noqa: E402
    Instance,
    InstanceHierarchy,
    InstanceStatus,
)
from daemon.repositories.instance.repository import (  # noqa: E402
    SQLModelInstanceRepository,
)
from daemon.repositories.instance_ui_prefs.models import InstanceUiPrefs  # noqa: E402
from daemon.repositories.instance_ui_prefs.repository import (  # noqa: E402
    InstanceUiPrefsRepository,
)
from daemon.services.maintenance import (  # noqa: E402
    CheckpointCleanupJob,
    utcnow,
)

# Cap log noise during the test run.
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("pinned_cleanup_mock_test")


# ---------------------------------------------------------------------------
# Self-timeout (script-level guard)
# ---------------------------------------------------------------------------
HARD_TIMEOUT_SECONDS = 120


def _timeout_handler(_signum, _frame):
    print("RESULT: TIMEOUT (script exceeded 120s hard cap)", flush=True)
    sys.exit(124)


signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(HARD_TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# PersistenceConfig substitute
# ---------------------------------------------------------------------------
# We do NOT use the real ``PersistenceConfig`` because it inherits
# ``BaseSettings`` and reads environment variables. Building it cleanly is
# fragile in-process. The job only reads three attributes:
#   - checkpoint_ttl_hours
#   - max_instance_history
#   - checkpoint_cleanup_interval
# A small ``dataclass`` with those attributes is enough. (If the job
# evolves to read more, this dataclass will need to grow.)


@dataclass
class _PersistenceConfig:
    checkpoint_ttl_hours: int = 1
    max_instance_history: int = 2
    checkpoint_cleanup_interval: int = 1


# ---------------------------------------------------------------------------
# In-process DB / repo factory
# ---------------------------------------------------------------------------


@contextmanager
def fresh_db():
    """Yield a fresh in-memory SQLite engine with FK enforcement + tables.

    Disposes the engine on exit so no SQLite file leaks. Every scenario
    gets a brand-new isolated DB, so scenario state cannot leak across
    boundaries.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def make_repos(engine):
    instance_repo = SQLModelInstanceRepository(engine)
    ui_prefs_repo = InstanceUiPrefsRepository(engine)
    return instance_repo, ui_prefs_repo


def make_job(config, checkpointer, instance_repo, ui_prefs_repo=None):
    return CheckpointCleanupJob(
        config=config,
        checkpointer=checkpointer,
        instance_repo=instance_repo,
        ui_prefs_repo=ui_prefs_repo,
    )


def make_checkpointer() -> MagicMock:
    """A MagicMock that absorbs every checkpointer call. ``adelete_thread``
    is awaited by the job, so it must be an ``AsyncMock``."""
    cp = MagicMock()
    cp.adelete_thread = AsyncMock(return_value=None)
    cp.list_thread_ids = AsyncMock(return_value=[])
    cp.find_excess_checkpoint_groups = AsyncMock(return_value=[])
    cp.get_checkpoint_ids = AsyncMock(return_value=[])
    cp.delete_checkpoints_excluding = AsyncMock(return_value=0)
    cp.delete_writes_excluding = AsyncMock(return_value=0)
    return cp


# ---------------------------------------------------------------------------
# Domain helpers — drive the REAL production repositories
# ---------------------------------------------------------------------------


def _insert_instance(
    engine,
    instance_id: str,
    *,
    parent_id: str | None = None,
    status: str = InstanceStatus.COMPLETED.value,
    updated_at: datetime | None = None,
    agent_id: str = "developer",
    agent_dir: str = "agents/developer",
) -> Instance:
    """Insert a real ``Instance`` row using the production repository so
    the ``InstanceHierarchy`` junction is populated correctly when
    ``parent_id`` is set (the dev's tests rely on this). Then
    back-date ``updated_at`` via a raw SQL UPDATE because the job's
    candidate query filters on ``updated_at < cutoff``."""
    repo = SQLModelInstanceRepository(engine)
    instance = repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        parent_id=parent_id,
        status=status,
    )
    if updated_at is not None:
        # Direct Core UPDATE — the ORM ``before_update`` listener is
        # irrelevant because the job compares ISO strings, and the
        # listener is a no-op for Core updates anyway. This is the
        # same back-dating pattern the dev's tests use to make
        # instances appear "expired" without sleeping for hours.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE instances SET updated_at = :ts WHERE instance_id = :iid"),
                {"ts": updated_at.isoformat(), "iid": instance_id},
            )
    return instance


def _delete_instance_row(engine, instance_id: str) -> None:
    """Bypass the cascade and hard-delete just the ``Instance`` row.

    Used by the W1 broken-ancestor-chain scenario to simulate the
    middle instance having been cleaned up by an earlier cycle while
    leaving the leaf's ``parent_id`` pointing at the now-gone middle.
    """
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM instances WHERE instance_id = :iid"),
            {"iid": instance_id},
        )


def _row_count(engine, table: str) -> int:
    with engine.begin() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def _all_instance_ids(engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT instance_id FROM instances")).all()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Scenario infrastructure
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    note: str
    details: dict[str, Any]


def _ok(name: str, note: str, **details) -> ScenarioResult:
    return ScenarioResult(name=name, passed=True, note=note, details=details)


def _fail(name: str, note: str, **details) -> ScenarioResult:
    return ScenarioResult(name=name, passed=False, note=note, details=details)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def scenario_1_ttl_protects_pinned() -> ScenarioResult:
    """TTL protects pinned terminal instance; non-pinned twin is deleted."""
    name = "TTL protects pinned"
    with fresh_db() as engine:
        inst_repo, ui_repo = make_repos(engine)
        config = _PersistenceConfig(checkpoint_ttl_hours=1)
        checkpointer = make_checkpointer()
        job = make_job(config, checkpointer, inst_repo, ui_prefs_repo=ui_repo)

        long_ago = utcnow() - timedelta(hours=10)
        _insert_instance(
            engine, "A_pinned",
            status=InstanceStatus.COMPLETED.value,
            updated_at=long_ago,
        )
        _insert_instance(
            engine, "B_unpinned",
            status=InstanceStatus.COMPLETED.value,
            updated_at=long_ago,
        )
        ui_repo.upsert("A_pinned", pinned=True)

        await job._cleanup_expired_terminal()

        survivors = _all_instance_ids(engine)
        try:
            assert "A_pinned" in survivors, f"pinned A was deleted: survivors={sorted(survivors)}"
            assert "B_unpinned" not in survivors, (
                f"unpinned B should have been deleted: survivors={sorted(survivors)}"
            )
            # adelete_thread is awaited only for instances that survived
            # the DB delete — for B the delete happens, then adelete_thread
            # is called; for A no delete, no adelete_thread.
            b_delete = checkpointer.adelete_thread.await_args_list
            assert any(
                call.args and call.args[0] == "B_unpinned" for call in b_delete
            ), f"adelete_thread was not awaited for B_unpinned: {b_delete}"
            assert not any(
                call.args and call.args[0] == "A_pinned" for call in b_delete
            ), f"adelete_thread was awaited for pinned A: {b_delete}"
        except AssertionError as exc:
            return _fail(name, str(exc), survivors=sorted(survivors))
        return _ok(
            name,
            "A survived, B deleted; adelete_thread called for B only",
            survivors=sorted(survivors),
        )


async def scenario_2_history_cap_protects_pinned_oldest() -> ScenarioResult:
    """History cap (max=2) with 3 terminals: pinned A (oldest) protected,
    and one of B/C is pruned; A does NOT count toward the cap."""
    name = "history cap protects pinned oldest"
    with fresh_db() as engine:
        inst_repo, ui_repo = make_repos(engine)
        config = _PersistenceConfig(max_instance_history=2)
        checkpointer = make_checkpointer()
        job = make_job(config, checkpointer, inst_repo, ui_prefs_repo=ui_repo)

        t0 = utcnow() - timedelta(hours=5)
        # Insertion order = "age order" we want: A oldest, B middle, C newest.
        _insert_instance(
            engine, "A_oldest",
            status=InstanceStatus.COMPLETED.value,
            updated_at=t0,
        )
        _insert_instance(
            engine, "B_middle",
            status=InstanceStatus.COMPLETED.value,
            updated_at=t0 + timedelta(minutes=1),
        )
        _insert_instance(
            engine, "C_newest",
            status=InstanceStatus.COMPLETED.value,
            updated_at=t0 + timedelta(minutes=2),
        )
        ui_repo.upsert("A_oldest", pinned=True)

        await job._enforce_history_cap()

        survivors = _all_instance_ids(engine)
        try:
            assert "A_oldest" in survivors, (
                f"pinned oldest A should survive: survivors={sorted(survivors)}"
            )
            # Cap is 2 with 2 candidates (B + C, A excluded). 2 <= 2 so
            # nothing is pruned. This is the CORRECT behaviour per the
            # production source — the comment at line 564 says
            # ``if total_count <= max_history: return``. Confirm we hit
            # the no-op branch, NOT a prune of A.
            assert "B_middle" in survivors, (
                f"B should survive (under cap): survivors={sorted(survivors)}"
            )
            assert "C_newest" in survivors, (
                f"C should survive (under cap): survivors={sorted(survivors)}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), survivors=sorted(survivors))
        return _ok(
            name,
            "A pinned oldest preserved; B and C also under cap → no prune",
            survivors=sorted(survivors),
        )


async def scenario_2b_history_cap_overflow() -> ScenarioResult:
    """History cap (max=1) with 3 terminals + pinned A oldest → cap
    applies to NON-pinned candidates only. Pinned A is excluded, so
    candidates = {B, C}. With cap=1 and 2 candidates, the oldest
    candidate (B) is pruned. A and C survive."""
    name = "history cap overflow (pinned excluded from cap)"
    with fresh_db() as engine:
        inst_repo, ui_repo = make_repos(engine)
        config = _PersistenceConfig(max_instance_history=1)
        checkpointer = make_checkpointer()
        job = make_job(config, checkpointer, inst_repo, ui_prefs_repo=ui_repo)

        t0 = utcnow() - timedelta(hours=5)
        _insert_instance(
            engine, "A_oldest",
            status=InstanceStatus.COMPLETED.value,
            updated_at=t0,
        )
        _insert_instance(
            engine, "B_middle",
            status=InstanceStatus.COMPLETED.value,
            updated_at=t0 + timedelta(minutes=1),
        )
        _insert_instance(
            engine, "C_newest",
            status=InstanceStatus.COMPLETED.value,
            updated_at=t0 + timedelta(minutes=2),
        )
        ui_repo.upsert("A_oldest", pinned=True)

        await job._enforce_history_cap()

        survivors = _all_instance_ids(engine)
        try:
            assert "A_oldest" in survivors, (
                f"pinned A must survive (excluded from cap): survivors={sorted(survivors)}"
            )
            assert "B_middle" not in survivors, (
                f"B is the oldest non-pinned and must be pruned: survivors={sorted(survivors)}"
            )
            assert "C_newest" in survivors, (
                f"C should survive (newest non-pinned): survivors={sorted(survivors)}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), survivors=sorted(survivors))
        return _ok(
            name,
            "cap=1; pinned A excluded, B pruned, C survives",
            survivors=sorted(survivors),
        )


async def scenario_3_descendants_protected() -> ScenarioResult:
    """Tree root→child→grandchild: pinning root protects entire subtree."""
    name = "descendants of pinned root protected"
    with fresh_db() as engine:
        inst_repo, ui_repo = make_repos(engine)
        config = _PersistenceConfig(checkpoint_ttl_hours=1)
        checkpointer = make_checkpointer()
        job = make_job(config, checkpointer, inst_repo, ui_prefs_repo=ui_repo)

        long_ago = utcnow() - timedelta(hours=10)
        _insert_instance(
            engine, "root", status=InstanceStatus.COMPLETED.value, updated_at=long_ago,
        )
        _insert_instance(
            engine, "child", parent_id="root",
            status=InstanceStatus.COMPLETED.value, updated_at=long_ago,
        )
        _insert_instance(
            engine, "grandchild", parent_id="child",
            status=InstanceStatus.COMPLETED.value, updated_at=long_ago,
        )
        # Add an unrelated unpinned expired instance to prove the
        # exclusion list is precise (only the subtree is protected).
        _insert_instance(
            engine, "decoy", status=InstanceStatus.COMPLETED.value, updated_at=long_ago,
        )
        ui_repo.upsert("root", pinned=True)

        await job._cleanup_expired_terminal()

        survivors = _all_instance_ids(engine)
        try:
            for iid in ("root", "child", "grandchild"):
                assert iid in survivors, f"{iid} should survive: survivors={sorted(survivors)}"
            assert "decoy" not in survivors, (
                f"decoy (unrelated) should be deleted: survivors={sorted(survivors)}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), survivors=sorted(survivors))
        return _ok(
            name,
            "root+child+grandchild survive; decoy deleted",
            survivors=sorted(survivors),
        )


async def scenario_4_non_pinned_cleaned_normally() -> ScenarioResult:
    """Explicit assertion: a non-pinned expired terminal IS deleted.

    (Also covered by scenario 1's B, but re-asserted with a single
    instance to make the contract explicit.)"""
    name = "non-pinned still cleaned normally"
    with fresh_db() as engine:
        inst_repo, ui_repo = make_repos(engine)
        config = _PersistenceConfig(checkpoint_ttl_hours=1)
        checkpointer = make_checkpointer()
        job = make_job(config, checkpointer, inst_repo, ui_prefs_repo=ui_repo)

        long_ago = utcnow() - timedelta(hours=10)
        _insert_instance(
            engine, "lonely",
            status=InstanceStatus.COMPLETED.value,
            updated_at=long_ago,
        )
        # Note: NO pin, NO ui_prefs row at all.

        await job._cleanup_expired_terminal()

        survivors = _all_instance_ids(engine)
        try:
            assert "lonely" not in survivors, (
                f"unpinned lonely should be deleted: survivors={sorted(survivors)}"
            )
            # Confirm adelete_thread was awaited.
            await_args = checkpointer.adelete_thread.await_args_list
            assert any(
                call.args and call.args[0] == "lonely" for call in await_args
            ), f"adelete_thread not called for lonely: {await_args}"
        except AssertionError as exc:
            return _fail(name, str(exc), survivors=sorted(survivors))
        return _ok(
            name,
            "lonely deleted, adelete_thread awaited",
            survivors=sorted(survivors),
        )


async def scenario_5_w1_broken_ancestor_chain() -> ScenarioResult:
    """W1 edge case: pinned leaf whose parent chain is broken.

    Build root → middle → leaf (all terminal + expired). Manually
    delete the middle instance row, leaving leaf's ``parent_id``
    pointing at a non-existent middle. Pin the leaf. Run TTL
    cleanup. Expected: the leaf is STILL protected — ``get_tree_root_id``
    returns ``None`` (it walks up the chain and finds the missing
    middle), the fail-protect branch fires, the leaf's ``get_tree_ids``
    returns just [leaf], and the leaf survives cleanup.

    This is the most important edge case per the production docstring.
    """
    name = "W1 broken ancestor chain"
    with fresh_db() as engine:
        inst_repo, ui_repo = make_repos(engine)
        config = _PersistenceConfig(checkpoint_ttl_hours=1)
        checkpointer = make_checkpointer()
        job = make_job(config, checkpointer, inst_repo, ui_prefs_repo=ui_repo)

        long_ago = utcnow() - timedelta(hours=10)
        _insert_instance(
            engine, "root_orph", status=InstanceStatus.COMPLETED.value, updated_at=long_ago,
        )
        _insert_instance(
            engine, "middle_orph", parent_id="root_orph",
            status=InstanceStatus.COMPLETED.value, updated_at=long_ago,
        )
        _insert_instance(
            engine, "leaf_orph", parent_id="middle_orph",
            status=InstanceStatus.COMPLETED.value, updated_at=long_ago,
        )
        # Add an unrelated expired unpinned instance to prove the
        # candidate query still works in the broken-chain scenario.
        _insert_instance(
            engine, "control_unpinned",
            status=InstanceStatus.COMPLETED.value,
            updated_at=long_ago,
        )
        # Pin the leaf, then yank the middle out from under it.
        ui_repo.upsert("leaf_orph", pinned=True)
        _delete_instance_row(engine, "middle_orph")

        # Sanity: confirm the precondition we built.
        try:
            assert _all_instance_ids(engine) == {"root_orph", "leaf_orph", "control_unpinned"}, (
                "precondition failed: middle should be deleted from instances table"
            )
            assert inst_repo.get_tree_root_id("leaf_orph") is None, (
                "precondition failed: get_tree_root_id(leaf) should return None "
                "because the parent chain is broken"
            )
        except AssertionError as exc:
            return _fail(name, f"precondition: {exc}")

        await job._cleanup_expired_terminal()

        survivors = _all_instance_ids(engine)
        try:
            # Leaf MUST survive — the fail-protect branch protects it.
            assert "leaf_orph" in survivors, (
                f"leaf with broken chain should be protected: survivors={sorted(survivors)}"
            )
            # Root survives if it is also covered by the protected set.
            # The fail-protect branch calls get_tree_ids(leaf), which
            # only returns [leaf] (it has no InstanceHierarchy links
            # because the middle is gone and the leaf's parent_id
            # points at the missing middle, NOT a hierarchy link).
            # So root is NOT in the protected set. But root is also
            # not in the candidate set because the candidate query
            # iterates each terminal status; root IS in 'completed',
            # so it IS a candidate → gets deleted.
            # The crucial assertion: the LEAF survives despite the
            # broken parent chain. Root's fate depends on whether it
            # is in the protected set (it is not, per the production
            # logic) — so root may or may not survive depending on
            # that detail. We only require the leaf to survive.
            assert "control_unpinned" not in survivors, (
                f"control_unpinned should be deleted: survivors={sorted(survivors)}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), survivors=sorted(survivors))
        return _ok(
            name,
            "leaf protected despite stale parent_id; "
            f"survivors={sorted(survivors)}",
            survivors=sorted(survivors),
        )


async def scenario_6_all_protected() -> ScenarioResult:
    """All candidates are pinned: TTL AND history-cap are no-ops."""
    name = "all protected edge case"
    with fresh_db() as engine:
        inst_repo, ui_repo = make_repos(engine)
        config = _PersistenceConfig(
            checkpoint_ttl_hours=1,
            max_instance_history=1,  # aggressive cap
        )
        checkpointer = make_checkpointer()
        job = make_job(config, checkpointer, inst_repo, ui_prefs_repo=ui_repo)

        long_ago = utcnow() - timedelta(hours=10)
        for iid in ("X1", "X2", "X3"):
            _insert_instance(
                engine, iid,
                status=InstanceStatus.COMPLETED.value,
                updated_at=long_ago,
            )
            ui_repo.upsert(iid, pinned=True)

        await job._cleanup_expired_terminal()
        await job._enforce_history_cap()

        survivors = _all_instance_ids(engine)
        try:
            assert survivors == {"X1", "X2", "X3"}, (
                f"all-pinned set must be untouched: survivors={sorted(survivors)}"
            )
            # Confirm no destructive calls.
            delete_calls = checkpointer.adelete_thread.await_args_list
            assert delete_calls == [], (
                f"adelete_thread should not have been awaited: {delete_calls}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), survivors=sorted(survivors))
        return _ok(
            name,
            "all-pinned set untouched; no checkpointer calls",
            survivors=sorted(survivors),
        )


async def scenario_7_backward_compat_no_ui_prefs_repo() -> ScenarioResult:
    """Backward compat: with ``ui_prefs_repo=None`` the job runs as
    before — no protection at all, expired instances are deleted."""
    name = "backward compat (ui_prefs_repo=None)"
    with fresh_db() as engine:
        inst_repo, _ui_repo = make_repos(engine)
        config = _PersistenceConfig(checkpoint_ttl_hours=1)
        checkpointer = make_checkpointer()
        # NOTE: ui_prefs_repo=None
        job = make_job(config, checkpointer, inst_repo, ui_prefs_repo=None)

        long_ago = utcnow() - timedelta(hours=10)
        _insert_instance(
            engine, "BC1",
            status=InstanceStatus.COMPLETED.value, updated_at=long_ago,
        )
        _insert_instance(
            engine, "BC2",
            status=InstanceStatus.COMPLETED.value, updated_at=long_ago,
        )

        await job._cleanup_expired_terminal()

        survivors = _all_instance_ids(engine)
        try:
            assert survivors == set(), (
                f"with no ui_prefs_repo, both should be deleted: survivors={sorted(survivors)}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), survivors=sorted(survivors))
        return _ok(
            name,
            "ui_prefs_repo=None: both deleted (backward-compatible)",
            survivors=sorted(survivors),
        )


async def scenario_8_failsafe_on_prefs_lookup_error() -> ScenarioResult:
    """Fail-safe: when ``get_pinned_instance_ids()`` raises, the entire
    cleanup cycle is skipped — NOTHING is deleted (not even the
    non-pinned instances), and no ``adelete_thread`` calls fire.
    """
    name = "fail-safe on prefs lookup error"
    with fresh_db() as engine:
        inst_repo, _ui_repo = make_repos(engine)
        config = _PersistenceConfig(checkpoint_ttl_hours=1)
        checkpointer = make_checkpointer()

        # Build a ui_prefs_repo whose ``get_pinned_instance_ids`` raises.
        failing_ui_repo = MagicMock()
        failing_ui_repo.get_pinned_instance_ids = MagicMock(
            side_effect=RuntimeError("simulated db down")
        )
        job = make_job(
            config, checkpointer, inst_repo, ui_prefs_repo=failing_ui_repo
        )

        long_ago = utcnow() - timedelta(hours=10)
        _insert_instance(
            engine, "FS1",
            status=InstanceStatus.COMPLETED.value, updated_at=long_ago,
        )
        _insert_instance(
            engine, "FS2",
            status=InstanceStatus.COMPLETED.value, updated_at=long_ago,
        )

        await job._cleanup_expired_terminal()

        survivors = _all_instance_ids(engine)
        try:
            assert survivors == {"FS1", "FS2"}, (
                f"on prefs-lookup error, NOTHING should be deleted: "
                f"survivors={sorted(survivors)}"
            )
            delete_calls = checkpointer.adelete_thread.await_args_list
            assert delete_calls == [], (
                f"adelete_thread should not have been called: {delete_calls}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), survivors=sorted(survivors))
        return _ok(
            name,
            "prefs-lookup error: cycle skipped, no deletions, no checkpointer calls",
            survivors=sorted(survivors),
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


SCENARIOS = [
    scenario_1_ttl_protects_pinned,
    scenario_2_history_cap_protects_pinned_oldest,
    scenario_2b_history_cap_overflow,
    scenario_3_descendants_protected,
    scenario_4_non_pinned_cleaned_normally,
    scenario_5_w1_broken_ancestor_chain,
    scenario_6_all_protected,
    scenario_7_backward_compat_no_ui_prefs_repo,
    scenario_8_failsafe_on_prefs_lookup_error,
]


async def main():
    results: list[ScenarioResult] = []
    for fn in SCENARIOS:
        try:
            r = await fn()
        except Exception:  # pragma: no cover - bubble up as a fail
            tb = traceback.format_exc()
            r = ScenarioResult(
                name=fn.__name__, passed=False, note=f"exception: {tb}", details={}
            )
        results.append(r)

    print()
    print("=== Independent Mock Test: Pinned Instance Cleanup Protection ===")
    print()
    for r in results:
        verdict = "PASS" if r.passed else "FAIL"
        print(f"Scenario {r.name}: {verdict}")
        if r.note:
            print(f"  - {r.note}")
    print()
    overall = "PASS" if all(r.passed for r in results) else "FAIL"
    print(f"RESULT: {overall}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    started = time.monotonic()
    try:
        exit_code = asyncio.run(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        print("RESULT: FAIL (uncaught exception)", flush=True)
        exit_code = 1
    finally:
        elapsed = time.monotonic() - started
        print(f"Actual runtime: {elapsed:.2f} s", flush=True)
        # Disable the alarm so a clean exit doesn't get killed.
        signal.alarm(0)
    sys.exit(exit_code)
