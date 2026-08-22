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


---

## Mock Test: Reasoning-Echo Denylist Real-Behavior Verification

### Metadata
- **Created**: 2026-08-22
- **Script**: `tests/mocks/reasoning_echo_denylist_mock.py`
- **Language**: Python
- **Status**: ACTIVE

### Configuration
- **Timeout**: 180 s self (`signal.alarm`) + `timeout 200` outer guard (dual-layer)
- **Service Port**: n/a — pure in-process; no network listener, no daemon start
- **Mock Ports**: n/a
- **Cleanup**: env vars saved/restored around every scenario; no processes spawned

### What It Tests
Real-behavior verification of the allowlist→denylist flip in
`ThinkingChatOpenAI` (`daemon/graph.py`, branch `feature/reasoning-echo-denylist`,
commits `28ea76a9` + `018800b8`):
- ALL models echo `reasoning_content` in outgoing request payloads by default
- Models matching env `OPENAI_REASONING_ECHO_DISABLED_MODELS`
  (comma-separated, case-insensitive substring) are EXCLUDED
- Old env `OPENAI_REASONING_ECHO_MODELS` is dead but logs a deprecation
  warning (`warn_deprecated_reasoning_echo_env`)
- Reasoning-presence gate unchanged: message without `reasoning_content`
  never echoes (any model/env)

Asserts against the REAL `ThinkingChatOpenAI` class — the class under test
is never stubbed.

### Mock Services Required
- None — in-process construction of `ThinkingChatOpenAI` + message history;
  request payload inspected at the `_get_request_payload` seam (or the exact
  seam the code exposes — implementer adapts to actual wiring:
  `LLMConfig` → env parsing → ClassVar set at startup per `daemon/__main__.py`).

### Test Scenarios
1. **S1 default**: no echo env vars set → model `gpt-4o` payload assistant
   message INCLUDES `reasoning_content`.
2. **S2 denylist spares others**: `OPENAI_REASONING_ECHO_DISABLED_MODELS=gpt-4o`
   → `gpt-4o` payload EXCLUDES it; `deepseek-chat` payload still INCLUDES it.
3. **S3 case-insensitive**: env value `GPT-4O` disables `gpt-4o`.
4. **S4 empty-string env**: `OPENAI_REASONING_ECHO_DISABLED_MODELS=` → parses
   to `[]` → all models echo (no `[""]` poison entry that would disable everything).
5. **S5 deprecation**: `OPENAI_REASONING_ECHO_MODELS=deepseek` set →
   deprecation warning fires (exactly once), behavior unchanged
   (`gpt-4o` still echoes — old key no longer gates anything).
6. **S6 presence gate**: plain non-tool-call assistant turn WITH
   `reasoning_content` echoes; assistant message WITHOUT `reasoning_content`
   never echoes (any model/env).

### Success Criteria
- [ ] All 6 scenarios pass with assertion evidence (payload includes/excludes
      `reasoning_content` per scenario)
- [ ] Total runtime well under 5 min (target < 60 s)
- [ ] No process leaks, no network calls
- [ ] Env fully restored after run

### Implementation Notes
- Follow the pattern of `tests/mocks/pinned_cleanup_protection_mock.py`
  (in-process, per-scenario isolation, dual-layer timeout, RESULT: PASS/FAIL).
- Env control: save/restore `os.environ`; set the ClassVar the same way
  `daemon/__main__.py` does at startup (read the code for exact wiring).
- Deprecation-warning capture: `warnings.catch_warnings(record=True)` or the
  project's logging capture — implementer adapts to the helper's mechanism.
- Test code only — production code is NEVER modified. Genuine production
  bugs are reported, not fixed.

### Last Run
- **Date**: 2026-08-22T11:02 (local)
- **Worker Instance**: tester worker (real-behavior verification dispatch)
- **Result**: PASS (6/6 scenarios; exit 0; runtime 0.15 s under `timeout 200` + `signal.alarm(180)`)
- **Quick Fixes**: none — production behavior matched the spec on all six scenarios
- **Report**: stdout of `tests/mocks/reasoning_echo_denylist_mock.py` (per-scenario
  evidence inlined); notable observation from S5: `warn_deprecated_reasoning_echo_env`
  dedups via a per-process module flag that is consumed even when the env var is
  absent at the first call, so a later call with the env var set stays silent
  (per-process budget, not per-env-state).
