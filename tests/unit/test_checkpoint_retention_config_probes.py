"""Behavior probes for the checkpoint-retention config change.

GENUINE-GAP-ONLY probes for ``PersistenceConfig.checkpoint_max_per_thread``
+ ``CheckpointCleanupJob._prune_per_thread_checkpoints`` Op D wiring.

Author's existing tests in ``tests/test_maintenance.py`` cover:
  - default == 3 (P1)
  - explicit value override (P2 partial)
  - env override binds ``CHECKPOINT_MAX_PER_THREAD`` (P2 partial)
  - env flows to ``find_excess_checkpoint_groups`` (P5 partial — empty excess)
  - ge=1 rejects 0/-1/-50 (P3)
  - non-int "0"/"-5"/"abc" fails loud (P4)

This file adds probes for the GENUINE GAPS:
  - P5  env flows to ALL FOUR adapter calls (not just find_excess)
  - P6  N=1 boundary (only the newest kept, delete set has exactly 1 id)
  - P7  inner defensive no-op when ``get_checkpoint_ids`` returns []
       (top-level empty-excess early-return is already covered by the author)
  - P8  multiple threads pruned independently
  - P9  per-checkpoint_ns retention (ns threaded correctly to adapter)
  - P10 REPORT-ONLY: does ``PERSISTENCE_CHECKPOINT_MAX_PER_THREAD`` also bind?

PG-FREE / mock-based. ``asyncio_mode = "auto"`` in pyproject.toml so plain
``async def test_...`` works. The repo conftest (``tests/conftest.py``)
injects langgraph/MCP module mocks at collection, so imports of
``daemon.services.maintenance`` and ``daemon.config`` work without a live
LangGraph / MCP install.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.config import PersistenceConfig
from daemon.services.maintenance import CheckpointCleanupJob


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _build_checkpointer(excess_pairs, ids_to_keep=None, deleted_cp_rows=0, deleted_w_rows=0):
    """Build an AsyncMock checkpointer shaped like the existing tests.

    Mirrors the mock shape in ``tests/test_maintenance.py``:
      - ``find_excess_checkpoint_groups`` → list of (thread_id, ns, cnt) tuples
      - ``get_checkpoint_ids`` → list of keep IDs (or [] for P7)
      - ``delete_checkpoints_excluding`` / ``delete_writes_excluding`` → rowcount
    """
    checkpointer = AsyncMock()
    checkpointer.find_excess_checkpoint_groups = AsyncMock(return_value=excess_pairs)
    if ids_to_keep is None:
        # Default to "give back one id per call" so tests don't accidentally
        # hit the defensive ``if not ids_to_keep: return 0`` early-return.
        # Callers that want to test the inner no-op pass ``ids_to_keep=[]``.
        checkpointer.get_checkpoint_ids = AsyncMock(
            side_effect=lambda tid, ns, n: [f"keep-{tid}-{ns}-{i:032d}" for i in range(n)]
        )
    else:
        checkpointer.get_checkpoint_ids = AsyncMock(return_value=ids_to_keep)
    checkpointer.delete_checkpoints_excluding = AsyncMock(return_value=deleted_cp_rows)
    checkpointer.delete_writes_excluding = AsyncMock(return_value=deleted_w_rows)
    return checkpointer


def _build_job(config, checkpointer):
    """Build a CheckpointCleanupJob with the same mock shape as the existing tests."""
    instance_repo = MagicMock()
    return CheckpointCleanupJob(config, checkpointer, instance_repo)


# ─────────────────────────────────────────────────────────────────────────────
# P5 — env override FLOWS config → job → ALL FOUR adapter calls
# (author only proves find_excess_checkpoint_groups receives the env value;
# the other three adapter calls were never asserted with the env value because
# the author's probe uses empty excess → early-return path)
# ─────────────────────────────────────────────────────────────────────────────


async def test_env_override_flows_to_all_four_adapter_calls(monkeypatch):
    """``CHECKPOINT_MAX_PER_THREAD=17`` reaches every adapter call in Op D.

    Closes the P5 gap: the author's ``test_checkpoint_max_per_thread_env_
    override_threads_to_cleanup`` uses ``return_value=[]`` for excess, which
    hits the early-return path so ``get_checkpoint_ids`` / ``delete_*_
    excluding`` never fire. We must prove the env value threads through to
    ALL FOUR adapter calls when there is real work to do.
    """
    monkeypatch.setenv("CHECKPOINT_MAX_PER_THREAD", "17")

    config = PersistenceConfig()
    assert config.checkpoint_max_per_thread == 17  # sanity — env actually bound

    # Real excess → all 4 adapter calls fire
    excess_pairs = [("thread-alpha", "", 20)]
    keep_ids = [f"keep-{i:032d}" for i in range(17)]
    checkpointer = _build_checkpointer(
        excess_pairs=excess_pairs,
        ids_to_keep=keep_ids,
        deleted_cp_rows=3,
        deleted_w_rows=6,
    )

    job = _build_job(config, checkpointer)
    await job._prune_per_thread_checkpoints()

    # All four adapter calls must receive the env value (17)
    checkpointer.find_excess_checkpoint_groups.assert_awaited_once_with(17)
    checkpointer.get_checkpoint_ids.assert_awaited_once_with("thread-alpha", "", 17)

    # And the set passed to delete_*_excluding must be exactly the keep set
    expected_keep_set = set(keep_ids)
    checkpointer.delete_checkpoints_excluding.assert_awaited_once_with(
        "thread-alpha", "", expected_keep_set
    )
    checkpointer.delete_writes_excluding.assert_awaited_once_with(
        "thread-alpha", "", expected_keep_set
    )


# ─────────────────────────────────────────────────────────────────────────────
# P6 — N=1 boundary: only the newest is kept, delete receives a 1-element set
# ─────────────────────────────────────────────────────────────────────────────


async def test_n_equals_1_keeps_only_newest_checkpoint():
    """``checkpoint_max_per_thread=1`` keeps exactly one checkpoint per thread.

    Boundary pin: ``get_checkpoint_ids(tid, ns, 1)`` must receive ``N=1``;
    ``delete_*_excluding`` must receive a SET with exactly one id. If the
    N=1 path silently regresses to N=0 (silent clamp) or N=2 (off-by-one),
    the ``delete_*_excluding`` call set size is the first signal.
    """
    config = PersistenceConfig(checkpoint_max_per_thread=1)

    newest = "ffffffff-ffff-ffff-ffff-ffffffffffff"  # lexicographically newest
    checkpointer = _build_checkpointer(
        excess_pairs=[("thread-only-one", "", 5)],
        ids_to_keep=[newest],
        deleted_cp_rows=4,
        deleted_w_rows=8,
    )

    job = _build_job(config, checkpointer)
    await job._prune_per_thread_checkpoints()

    checkpointer.get_checkpoint_ids.assert_awaited_once_with("thread-only-one", "", 1)
    # The set passed to delete_*_excluding MUST be exactly {newest}, not [] (no-op)
    # and not {newest, ...other...} (off-by-one).
    checkpointer.delete_checkpoints_excluding.assert_awaited_once_with(
        "thread-only-one", "", {newest}
    )
    checkpointer.delete_writes_excluding.assert_awaited_once_with(
        "thread-only-one", "", {newest}
    )


# ─────────────────────────────────────────────────────────────────────────────
# P7 — inner defensive no-op when ``get_checkpoint_ids`` returns empty
# (top-level ``find_excess_checkpoint_groups=[]`` early-return is already
# covered by the author; this probes the INNER defensive branch
# ``if not ids_to_keep: return 0`` at maintenance.py:1269)
# ─────────────────────────────────────────────────────────────────────────────


async def test_inner_defensive_noop_when_get_checkpoint_ids_returns_empty():
    """Inner defensive ``if not ids_to_keep: return 0`` at maintenance.py:1269.

    Even when ``find_excess_checkpoint_groups`` reports a non-empty excess
    pair, ``get_checkpoint_ids`` may legitimately return ``[]`` (defensive
    case — e.g. concurrent prune, race with saver). The cleanup job MUST
    no-op without raising and MUST NOT call ``delete_*_excluding`` with a
    non-empty set (which would wipe every checkpoint on the thread — the
    silent-prune-everything class the ge=1 guard is designed to prevent).
    """
    config = PersistenceConfig()  # default 3

    # Excess reports 5 checkpoints exist, but the adapter says "keep none"
    checkpointer = _build_checkpointer(
        excess_pairs=[("thread-empty-keep", "", 5)],
        ids_to_keep=[],  # defensive no-op path
    )

    job = _build_job(config, checkpointer)
    # Must not raise
    result = await job._prune_per_thread_checkpoints()

    assert result is None  # _prune_per_thread_checkpoints returns None always
    checkpointer.find_excess_checkpoint_groups.assert_awaited_once_with(3)
    checkpointer.get_checkpoint_ids.assert_awaited_once_with("thread-empty-keep", "", 3)
    # The DELETE calls MUST NOT have fired with a non-empty keep set —
    # the defensive early-return at maintenance.py:1269 prevents them.
    checkpointer.delete_checkpoints_excluding.assert_not_called()
    checkpointer.delete_writes_excluding.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# P8 — multiple threads pruned independently (per-thread retention)
# ─────────────────────────────────────────────────────────────────────────────


async def test_multiple_threads_each_pruned_with_the_same_n():
    """Each thread in the excess list is pruned independently with N.

    Loops over ``excess_pairs`` calling ``_prune_thread_checkpoints`` per
    (thread_id, ns, cnt). The probe asserts ``get_checkpoint_ids`` and
    ``delete_*_excluding`` are called PER-THREAD with the correct N and
    thread_id — proving no cross-thread bleed (e.g. one thread's keep set
    landing in another's delete call).
    """
    config = PersistenceConfig(checkpoint_max_per_thread=5)

    excess_pairs = [
        ("thread-alpha", "", 50),
        ("thread-bravo", "", 30),
    ]
    # Per-thread keep sets (mock returns the same value for every call,
    # which is OK because each thread receives a fresh set of IDs).
    keep_ids = [f"keep-{i:032d}" for i in range(5)]
    checkpointer = _build_checkpointer(
        excess_pairs=excess_pairs,
        ids_to_keep=keep_ids,
        deleted_cp_rows=5,
        deleted_w_rows=10,
    )

    job = _build_job(config, checkpointer)
    await job._prune_per_thread_checkpoints()

    # Each thread gets its own get_checkpoint_ids call with N=5
    expected_calls = [
        (("thread-alpha", "", 5)),
        (("thread-bravo", "", 5)),
    ]
    actual_calls = [
        c.args for c in checkpointer.get_checkpoint_ids.await_args_list
    ]
    assert actual_calls == expected_calls

    # And each thread gets its own delete_*_excluding calls with the matching
    # thread_id and the same keep set (no cross-thread bleed)
    expected_keep_set = set(keep_ids)
    expected_delete_calls = [
        (("thread-alpha", "", expected_keep_set)),
        (("thread-bravo", "", expected_keep_set)),
    ]
    assert [
        c.args for c in checkpointer.delete_checkpoints_excluding.await_args_list
    ] == expected_delete_calls
    assert [
        c.args for c in checkpointer.delete_writes_excluding.await_args_list
    ] == expected_delete_calls


# ─────────────────────────────────────────────────────────────────────────────
# P9 — per-checkpoint_ns retention: ``ns`` threaded to get_checkpoint_ids
# and delete_*_excluding (prod uses ns="" but code must handle ns correctly)
# ─────────────────────────────────────────────────────────────────────────────


async def test_per_checkpoint_ns_threaded_to_adapter_calls():
    """``checkpoint_ns`` is threaded into get_checkpoint_ids and delete_*_excluding.

    The schema distinguishes (thread_id, checkpoint_ns) — code MUST forward
    the namespace to every adapter call. Probe with two distinct ns values
    sharing a thread_id to prove the ns arg is not silently dropped or
    hardcoded.
    """
    config = PersistenceConfig(checkpoint_max_per_thread=4)

    excess_pairs = [
        ("thread-shared", "", 8),              # prod default ns
        ("thread-shared", "child-ns", 8),      # distinct ns under same thread
    ]
    checkpointer = _build_checkpointer(
        excess_pairs=excess_pairs,
        ids_to_keep=[f"keep-{i:032d}" for i in range(4)],
        deleted_cp_rows=4,
        deleted_w_rows=8,
    )

    job = _build_job(config, checkpointer)
    await job._prune_per_thread_checkpoints()

    actual_calls = [
        c.args for c in checkpointer.get_checkpoint_ids.await_args_list
    ]
    expected_calls = [
        ("thread-shared", "", 4),
        ("thread-shared", "child-ns", 4),
    ]
    assert actual_calls == expected_calls, (
        f"checkpoint_ns must be threaded per-pair; got {actual_calls}"
    )

    expected_keep_set = set(checkpointer.get_checkpoint_ids.return_value)
    expected_delete_calls = [
        ("thread-shared", "", expected_keep_set),
        ("thread-shared", "child-ns", expected_keep_set),
    ]
    assert [
        c.args for c in checkpointer.delete_checkpoints_excluding.await_args_list
    ] == expected_delete_calls
    assert [
        c.args for c in checkpointer.delete_writes_excluding.await_args_list
    ] == expected_delete_calls


# ─────────────────────────────────────────────────────────────────────────────
# P10 — REPORT-ONLY: does ``PERSISTENCE_CHECKPOINT_MAX_PER_THREAD`` also bind?
# ─────────────────────────────────────────────────────────────────────────────
#
# pydantic-settings with ``env_prefix="PERSISTENCE_"`` AND an explicit
# ``validation_alias=AliasChoices(...)`` SHOULD disable prefix matching
# for that field (per pydantic-settings precedence: explicit alias wins
# over the class-level prefix). The config docstring confirms this is the
# intended contract ("validation_alias is explicit (no PERSISTENCE_ prefix)
# so the env name matches the historical Python constant").
#
# We probe by setting the prefix-form env var and asserting the value
# stays at the default (3). If pydantic-settings does bind the prefix
# form, this test fails — which is itself the finding to report.
# ─────────────────────────────────────────────────────────────────────────────


def test_persistence_prefix_alias_does_not_bind(monkeypatch):
    """``PERSISTENCE_CHECKPOINT_MAX_PER_THREAD`` does NOT bind (prefix disabled).

    REPORT-ONLY: per pydantic-settings precedence, the explicit
    ``validation_alias=AliasChoices(...)`` on ``checkpoint_max_per_thread``
    disables class-level ``env_prefix="PERSISTENCE_"`` matching for this
    field. The docstring at daemon/config.py:597-626 codifies this contract
    so operators can use the historical ``CHECKPOINT_MAX_PER_THREAD`` name.

    If this test fails (the prefix form binds), the contract is broader
    than documented — flag as a finding; do not "correct" without an
    explicit contract decision.
    """
    # Set ONLY the prefix-form env var; leave the canonical name unset
    monkeypatch.setenv("PERSISTENCE_CHECKPOINT_MAX_PER_THREAD", "99")
    monkeypatch.delenv("CHECKPOINT_MAX_PER_THREAD", raising=False)

    config = PersistenceConfig()

    # Contract per docstring: prefix form does NOT bind; default 3 holds.
    assert config.checkpoint_max_per_thread == 3, (
        f"PERSISTENCE_CHECKPOINT_MAX_PER_THREAD unexpectedly bound to "
        f"{config.checkpoint_max_per_thread}; explicit validation_alias "
        f"was supposed to disable the env_prefix. Update contract or docstring."
    )