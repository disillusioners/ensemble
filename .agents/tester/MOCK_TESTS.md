# Job Queue Tests — Mock Tests Inventory

## Phase 1: Schema & Migration (COMPLETE)
- Models: QueueType, JobQueue, JobItem queue_id
- Migration: table creation, seeding, constraints, idempotency
- Schemas: CreateRequest, UpdateRequest, Response validation

## Phase 2: Backend Core Services (COMPLETE)
- JobQueueRepository: CRUD, atomic operations, job counting, reassignment
- JobQueueMgmtService: auto-provision, CRUD with IDOR, queue deletion rules
- JobLockManager: per-queue atomic locking, concurrency limits
- JobProcessor: per-queue polling, two-level pause (queue + project level)
- JobQueueService: queue-aware enqueue with system queue fallback
- JobRepository: list_pending_by_queue, start_job_atomic, delete_by_project

---

## Updating MOCK_TESTS.md

Update when mock tests are added/modified.

## Mock Test: Pinned Instance Cleanup Protection

### Metadata
- **Created**: 2026-07-31
- **Script**: `tests/mocks/pinned_cleanup_protection_mock.py`
- **Language**: Python
- **Status**: ACTIVE

### Configuration
- **Timeout**: 120 s (self + `timeout 130` outer guard)
- **Service Port**: n/a — pure in-process; no network listener
- **Mock Ports**: n/a
- **Cleanup**: Each scenario uses a fresh in-memory SQLite engine; engine is
  disposed on context exit so no SQLite file leaks.

### What It Tests
- `CheckpointCleanupJob._cleanup_expired_terminal` (Op B) protects pinned subtrees
- `CheckpointCleanupJob._enforce_history_cap` (Op C) protects pinned subtrees
  (excluded from the cap count and from pruning)
- `_get_protected_instance_ids()` resolves a pinned ID up to its tree root and
  collects the full subtree, including the W1 broken-ancestor-chain fail-protect
  branch.
- Backward-compat: `ui_prefs_repo=None` ⇒ no protection.
- Fail-safe: `get_pinned_instance_ids()` raising ⇒ entire cycle skipped.

### Mock Services Required
- None — uses real `SQLModelInstanceRepository`, real
  `InstanceUiPrefsRepository`, and `MagicMock`/`AsyncMock` for the checkpointer
  (`adelete_thread`, `list_thread_ids`, `find_excess_checkpoint_groups`,
  `get_checkpoint_ids`, `delete_checkpoints_excluding`, `delete_writes_excluding`
  are all `AsyncMock`s absorbing the calls).

### Test Scenarios
1. TTL protects a pinned terminal; non-pinned twin is deleted; `adelete_thread`
   is awaited only for the deleted instance.
2. History cap with `max=2` + 3 terminals + pinned oldest: A preserved; under
   cap ⇒ no prune.
2b. History cap overflow with `max=1` + 3 terminals + pinned oldest: pinned A
   excluded from cap, oldest non-pinned (B) pruned, C survives.
3. Tree root→child→grandchild all terminal+expired: pinning root protects
   the entire subtree; an unrelated decoy IS deleted.
4. Non-pinned expired terminal IS deleted and `adelete_thread` is awaited.
5. W1 broken-ancestor-chain: middle instance deleted out from under leaf,
   leaf's `parent_id` points at the now-gone middle. Pinned leaf survives via
   the fail-protect branch (log line: *"Pinned instance ... has a broken parent
   chain ... protecting it as its own root"*).
6. All candidates pinned: TTL AND history-cap are no-ops, no
   `adelete_thread` calls fire.
7. `ui_prefs_repo=None`: both expired instances deleted (backward-compat).
8. `get_pinned_instance_ids()` raises: nothing is deleted, no
   `adelete_thread` calls.

### Success Criteria
- [x] All 9 scenarios (8 spec'd + 1 bonus overflow) pass
- [x] Total runtime well under 5 min (≈ 0.2 s observed)
- [x] No process leaks; engines disposed
- [x] All scenarios isolated (fresh in-memory DB per scenario)

### Implementation Notes
- Each scenario runs against a fresh in-memory SQLite engine via
  `StaticPool`, so a failed scenario cannot poison later ones.
- The script does NOT import or call the dev's pytest tests
  (`tests/test_maintenance.py::TestCheckpointCleanupJobPinnedProtection`) —
  it builds its own assertions against the production code paths.
- Self-timeout via `signal.alarm(120)` plus outer `timeout 130 .venv/bin/python`
  is the dual-layer guard required by the `test-pack` skill.
- Failures are reported (this is independent verification, not a fix-it PR);
  production source is never modified.

### Last Run
- **Date**: 2026-07-31 18:03:55 UTC
- **Session**: in-process mock run
- **Result**: PASS (9/9 scenarios)
- **Runtime**: 0.20 s
- **Quick Fixes**: none — production code matched the spec
- **Report**: see "Result" section of the script's stdout output
