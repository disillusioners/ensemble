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


## Mock Test: P2.2 Upgrade-Tools Live-Safety Dynamic Sandbox

### Metadata
- **Created**: 2026-08-23
- **Script**: `tests/mocks/upgrade_tools_live_safety_mock.py` (during verification: developed/run from `/tmp/p22-dynamic-sandbox/` to keep the worktree pristine; placed into `tests/mocks/` + committed as test-infra after all parallel pack runs complete)
- **Language**: Python
- **Status**: ACTIVE (PASS 7/7, 2026-08-23 — see Last Run below)

### Configuration
- **Timeout**: 240 s internal (`signal.alarm` + per-subprocess timeouts) + `timeout 300` outer guard
- **Service Port**: none (in-process tool invocation through the real tool-factory seam); if a daemon boot proves unavoidable, port 10797 ONLY
- **Mock Ports**: n/a — no external service mocks; fake filesystem state only
- **DB**: in-memory SQLite / tmp files ONLY; throwaway PG on 15433 ONLY if the real seam requires PG — never dev/prod DBs
- **Cleanup**: fixture tmp sandbox removed; spawned executor trees collected via inline `pgrep -P` collectors (macOS: shell-function collectors silently fail) then TERM'd; ports verified free

### What It Tests
Real P2.2 tool paths end-to-end against FAKE deploy state — proving (a) the 4 tools genuinely work through the real factory/stamping/gate/spawn code and (b) they cannot touch the real live install:
- `release_info` / `upgrade_status` read-only parity vs real `scripts/upgrade/status.sh`-written fixture
- `system_restart` live refusal (unconditional, including the all-factors-satisfied case)
- `system_upgrade` refusal taxonomy dynamic spot-checks
- 3-factor LIVE gate PASS against a FAKE live marker (tmp install)
- restart arming on FAKE demo env + REAL `spawn_executor` spawn with env-allowlist strip proof
- journal-poll after fake restart (terminal state, SAME run_id round-trip)

### Mock Services Required
- None external. Fake deploy trees under a tmp sandbox root written by REAL `scripts/upgrade/lib.sh` + `status.sh` (fixture parity with real marker/journal formats — same discipline as the interlock pack).

### Test Scenarios
1. **S1 read-only parity**: `release_info`/`upgrade_status` fields == `status.sh` output on the same tmp fixture (P2.1-state parity).
2. **S2 live restart refusal**: target_env=live → `system_restart` refused outright with its unconditional refusal token, even with user_confirmed + HUMAN origin + valid nonce.
3. **S3 refusal taxonomy** (≥1 dynamic case each): missing user_confirmed; spoofed/api-origin (through the REAL origin-stamping path → no HUMAN stamp → refuse); nonce mismatch; expired nonce; replayed nonce; invalid target env.
4. **S4 3-factor PASS on FAKE live marker**: nonce minted via the real nonce store (bound target/kind/env/instance) → arm succeeds, journal pending_op written, nonce consumed; identical replay → refused.
5. **S5 demo restart arming + REAL spawn**: poison sentinels (`ENSEMBLE_UPGRADE_LIVE=1`, `ENSEMBLE_DEPLOY_LIVE=…`, HOME-adjacent probe vars) set in the PARENT env; fixture executor payload dumps its received env to a sandbox file → assert the allowlist stripped every poison var in the ACTUAL spawned process; `dry_run` default TRUE zero-mutation check.
6. **S6 journal-poll completion**: fake executor completes (marker/journal transition) → `upgrade_status` reports terminal state with SAME run_id as armed.
7. **S7 zero-live-contact guards**: (a) `lsof -nP -iTCP:9797 -sTCP:LISTEN` output identical before/after (read-only); (b) read-only `stat` of `~/agents-ensemble` top-level (mtime/size) unchanged, if it exists; (c) every resolved path in fixtures/logs/journal stays under the sandbox tmp root (assert no escape); (d) no leaked processes/ports after cleanup.

### Success Criteria
- [ ] All scenarios pass with per-scenario evidence
- [ ] Env-allowlist strip PROVEN in a real spawned process (S5 env-dump file)
- [ ] Zero live contact: guards (a)–(d) green
- [ ] Runtime well under 5 min per run; cleanup verified
- [ ] Branch/reviewed source NOT modified; only the new mock script (committed later as test-infra)

### Implementation Notes
- Precedent: `tests/mocks/reasoning_echo_denylist_mock.py` (in-process, per-scenario isolation, dual-layer timeout, `RESULT: PASS/FAIL`).
- Invoke tools through the REAL factory (`create_instance_tools`) with a real instance context; mock ONLY at the outermost edges (LLM/network) — none expected here.
- Fake markers must be built by real `lib.sh`/`status.sh` writers — do not hand-roll formats.
- NEVER: modify `~/agents-ensemble` (read-only stat allowed), bind/connect 9797, use prod DB, export `ENSEMBLE_DEPLOY_LIVE`, kill live pids. If any scenario cannot be exercised without live contact: STOP that scenario and report — do not force.
- Complements the committed M5 drill (`RESULTS/2026-08-23-p2-2-daemonized-executor-survival.md`: real `spawn_executor` survival across parent SIGKILL).

### Last Run
- **Date**: 2026-08-23 (P2.2 pre-merge tester verification)
- **Worker Instance**: tester worker (mock-test skill dispatch)
- **Script**: developed/run from `/tmp/p22-dynamic-sandbox/` (shared-worktree isolation); placed at `tests/mocks/upgrade_tools_live_safety_mock.py` + committed as test-infra after all parallel pack runs finished
- **Result**: PASS — 7/7 scenarios, 3 consecutive stable runs (~3s each vs 240s/300s caps); S1 parity 16/16; S2 live-restart refused with all factors satisfied; S3 7 distinct refusal tokens incl. spoofed-origin via real stamping path; S4 3-factor PASS + nonce consumed + replay refused; S5 real-spawn allowlist proof (ENSEMBLE_UPGRADE_LIVE/ENSEMBLE_DEPLOY_LIVE/ENSEMBLE_SELF_ENV/OPENAI_API_KEY/ANTHROPIC_API_KEY/POSTGRES_PASSWORD/POSTGRES_URL/AWS_SECRET_ACCESS_KEY/XDG_CONFIG_HOME/SECRET_POISON_CANARY all stripped; PGPASSWORD present by design per PG* prefix, R-SR09); S6 terminal same-run_id round-trip via shell-twin finalize; S7 guards all green (9797 lsof identical, live-install stat unchanged, sandbox-contained paths, no leaks)
- **Quick Fixes**: none — no production bugs found; 4 real-behavior observations reported (PG* passthrough → P2.3 ledger; opportunistic GC of dead nonce records at write time — gate TTL is the real guard; Python/shell twin protocol interop confirmed; harness marker-drain note)
- **Report**: `RESULTS/2026-08-23-p2-2-premerge-verification.md` §3
