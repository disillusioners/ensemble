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


---

## Mock Test: Pattern (f) Kill-Path Matrix (council criticals, real scenarios)

### Metadata
- **Created**: 2026-08-29
- **Script**: test/packs/pattern_f_killpath_matrix_test.py (+ .sh wrapper, dual-layer)
- **Language**: Python (pytest-style, real repos, file-backed SQLite under /tmp)
- **Status**: PLANNED (gate: feature/orphan-active-job-recovery @ ba39a40e)

### Configuration
- **Timeout**: internal 240s / outer `timeout 300`
- **Ports**: none (no daemon, no sockets; SQLite files under /tmp)
- **Cleanup**: delete /tmp sqlite files on exit; no repo-tracked file modified

### What It Tests
Recovery machinery that can KILL LIVE WORK — proves the guards don't leak, in real scenarios (real `JobRecoveryService._pattern_f_orphan_active_job_recovery`, real repositories, real lock rows; only manager/LLM-adjacent seams stubbed).
- (a) PAUSED Task past grace → JobItem stays ACTIVE (`orphan_active_skipped_paused`, job_recovery_service.py:1949), Task resumable PAUSED→PENDING after sweep.
- (b) FAILED/CANCELLED Task + live retry child (fresh work_id, same instance, PENDING/RUNNING retry task) → stays ACTIVE (`orphan_active_skipped_retry_child_live:2012`); retry completes → next sweep finalizes via boundary (`_pattern_f_finalize_failed_terminal:2907`, NO_RETRY, failed_at, terminal_reason, lock release).
- (c) healthy waiting_children parent mid-wait → f2 no-finalize. **Per-leg mutation check**: for each leg L (bus_pending:2172-2214/helper:3133, PENDING instance tasks:2220-2237/helper:3188, completed_at 60s floor:2240-2256/helper:3314): construct scenario where L is the ONLY blocking leg → unmutated: skip with L's label; monkeypatch L permissive: finalize occurs (leg is load-bearing, scenario lethal, not vacuously safe).
- (d) genuine restart-orphan: ACTIVE JobItem + NO Task + created_at past 900s grace (`min_orphan_age_seconds`, config `drift_reconcile_min_orphan_age_seconds`) + instance.created_at mid-mint conjunct satisfied (:2419-2452) → DEAD (`orphan_active_no_task_dead:2475`) + lock row released (triple scope, :2626-2660).
- (e) f2 lock release on c=1 queue: ACTIVE JobItem A + COMPLETED Task (all 3 legs pass) → DONE + lock released (:2869) → new JobItem B on same queue admits via real claim (no wedge).

### Success Criteria
- [ ] All 5 scenarios: unmutated guard → correct skip/finalize, DB rows asserted (not just return codes)
- [ ] (c) each of 3 legs: load-bearing proven (mutation → wrongful finalize observed + documented)
- [ ] No lock rows leaked in any terminal path; watchers notified after lock release

### Implementation Notes
- Bind exact symbols from recon (job_recovery_service.py:1731 sweep, :79 floor const, :562/:1800 grace param)
- Fail-safe leg: bus unavailable → `orphan_active_skipped_bus_unavailable:2177` (negative probe optional)

---

## Mock Test: child_still_running_defer Bus-Emit Fix (02fb2e01)

### Metadata
- **Created**: 2026-08-29
- **Script**: test/packs/defer_bus_emit_probe_test.py (+ .sh wrapper, dual-layer)
- **Language**: Python (real child_reports defer path, real dependency_bus repository, file-backed SQLite)
- **Status**: PLANNED (gate: feature/orphan-active-job-recovery @ ba39a40e)

### Configuration
- **Timeout**: internal 180s / outer `timeout 300`
- **Ports**: none

### What It Tests
- Exactly-once called-twice: defer outcome fires BOTH emits (task-keyed `_emit_terminal_via_bus` child_reports.py:3456 + corrective `_emit_terminal_for_child_instance_via_bus`:3461); calling the guarded transition twice (`transition_state` WHERE state='PENDING', dependency_bus/repository.py) → second fire rowcount=0, exactly ONE FollowUp delivered (real DB).
- Legitimate defer → NO emit: real deferral (child genuinely still running) → no bus terminal emit, SSE waiting_children preserved, no completion-registry call.
- Incident replay 02fb2e01: parent watcher PENDING on child; child task completed; outcome = child_still_running_defer (multi-turn shape) → both emits fire → watcher PENDING→FIRED → parent completion gate released.

### Success Criteria
- [ ] Called-twice: exactly one delivered FollowUp, one FIRED watcher, no duplicate
- [ ] Legitimate defer: zero bus emits (assert helper call sites)
- [ ] Replay: watcher FIRED + parent gate released (dependency cleared / parent completable)

---

## Mock Test: E2E Capstone — 092c5ed3-class Zombie Active JobItems

### Metadata
- **Created**: 2026-08-29
- **Script**: test/packs/pattern_f_capstone_test.py (+ .sh wrapper, dual-layer)
- **Language**: Python (real engine assembly: real drift sweep + real claim path, file-backed SQLite)
- **Status**: PLANNED (gate: feature/orphan-active-job-recovery @ ba39a40e)

### Configuration
- **Timeout**: internal 270s / outer `timeout 300`
- **Ports**: none

### What It Tests
End-to-end on real engine components (no mocks below repository/service seam): seed zombie ACTIVE JobItems BOTH shapes (shape-1 active+no-Task stale past grace+mid-mint; shape-2 active+COMPLETED-Task old past all 3 legs) + pending watchers + a queued defer-queue job behind the gates → run real `reconcile_drift_states` (job_recovery_service.py:1402 → Pattern (f) sweep) → assert shape-1 DEAD, shape-2 DONE, locks released, watchers fired/notified, gates released, and the queued defer job ADMITS via real claim (real worker claim resolves the wedge — the 092c5ed3 incident class).

### Success Criteria
- [ ] Both shapes recovered with correct terminal states + lock rows gone
- [ ] Watchers released; no stranded PENDING rows
- [ ] Queued defer job admitted by a REAL claim after sweep (no wedge)

### Last Run
- **Date**: 2026-08-29 (gate, @ ba39a40e)
- **Kill-path matrix**: PASS 5/5 (per-leg mutation table in RESULTS/2026-08-29-orphan-active-job-recovery-gate.md §2)
- **Bus-emit probe**: PASS 3/3 (P1 exactly-once real-DB; P2 no premature completion; P3 incident replay, gate released)
- **Capstone**: PASS 4/4 (both shapes recovered; wedge = defer idle gate 2→0; C admitted via real claim)
- **Scripts**: test/packs/pattern_f_killpath_matrix_test.{py,sh}, test/packs/defer_bus_emit_probe_test.{py,sh}, test/packs/pattern_f_capstone_test.{py,sh} — all ACTIVE, registered in PACKS.md
- **Report**: RESULTS/2026-08-29-orphan-active-job-recovery-gate.md


## Mock Test: defer_gate_runtime_matrix (W-round, fix/defer-gate-post-settle-window)

### Metadata
- **Created**: 2026-09-03
- **Script**: test/packs/defer_gate_runtime_matrix_test.{py,sh}
- **Language**: Python (+ .sh wrapper, dual-layer timeout)
- **Status**: PLANNED (dispatched this gate; worker implements + runs)

### Configuration
- **Timeout**: 150s script-internal / 300s command-level
- **Service Port**: none (no HTTP daemon; in-process repositories)
- **Mock Ports**: none
- **Cleanup**: tmp file-backed SQLite via tmp_path-style tempdir, auto-removed; no processes started, nothing to kill

### What It Tests
The widened defer-gate admission semantics at RUNTIME (real repository code, not unit-test SQL-string asserts): the three admission scenarios, the folding (claim) layering proof, and self-deadlock exclusion.

### Mock Services Required
- None. Real `JobQueueRepository` (and instance/task repositories as needed) over file-backed SQLite at a temp path (WAL, NullPool — file-backed recipe; NEVER StaticPool+WriteGuardSession).

### Test Scenarios
1. **S1 defer BLOCKED**: project P has a settled mirror (JobItem job_type='message', admission_state='done', instance_id set, on a NON-defer queue, non-deleted) whose instance is NON-terminal (waiting_children) → defer candidate on the defer queue: `_defer_idle_check`-equivalent (`has_active_non_deferred_work`) returns BUSY AND `_select_next_eligible_job` defer branch returns None.
2. **S2 defer ADMITTED**: same shape but instance is TERMINAL (completed) → gate reads IDLE, candidate admitted via real claim path.
3. **S3 PAUSED blocked (by-design)**: settled mirror whose instance is PAUSED → gate reads BUSY (pinned semantics `7ecf09e2`).
4. **S4 folding layering proof**: defer candidate pending; parent message Task COMPLETED; instance waiting_children; mirror done → Gate B returns None (gate blocks on mission liveness) WHILE the task-granular claim folding (`claim_pending_task` t2 guard) correctly finds NO active task and would proceed — documenting the two-leg layering (gate = mission liveness, claim = task liveness; claim proceeding is CORRECT, gate is the fix layer).
5. **S5 self-deadlock exclusion**: defer candidate whose OWN instance is waiting_children, candidate's own row on the defer queue → gate admits (queue-type exclusion holds; no self-block).

### Success Criteria
- [ ] All 5 scenarios pass with REAL repository/gate functions (no monkeypatched predicates)
- [ ] S4 asserts both legs (gate None + claim finds no active task)
- [ ] File-backed SQLite only; no StaticPool
- [ ] Total runtime < 60s

### Implementation Notes
- Use the REAL gate entry points from the branch: `_defer_idle_check` / `_background_idle_check` (daemon/services/job_processor.py) and `_select_next_eligible_job` (daemon/services/job_queue_service.py) — or the repository predicates directly if service-level construction is impractical; state which layer was exercised in the report.
- Terminal statuses: ('completed','error','terminated','failed').

### Last Run
- **Date**: 2026-09-03 (defer-gate FULL gate)
- **Worker Instance**: dg-rt-matrix (5d93e67a) — mock-test skill; +4 independent re-verification runs (workers dg-p07/p10/p11/p12) all PASS 5/5 (determinism evidence)
- **Result**: **PASS 5/5** (0.4–0.6s per run) — S1 blocked / S2 admitted / S3 paused-blocked / S4 two-leg layering (Gate B None + claim t2 proceeds) / S5 self-deadlock exclusion. Layers exercised: A repository predicate, B `_defer_idle_check`, C `_select_next_eligible_job`, D `claim_pending_task`.
- **Quick Fixes**: 3 harness-only during authoring (import path `daemon.services.job_lock_manager`; per-scenario fresh file-backed SQLite isolation; S4 SQLite bool coercion). S5 background-sister assertion corrected to INFO (defer work IS non-background work per 2026-07-23 defer-leak fix — by-design).
- **Commit**: ab567195 (test-code only: the pack pair)
- **Report**: RESULTS/2026-09-03-defer-gate-full-gate.md

## Mock Test: fe_edit_agent_e2e_test (source-edit-agent real-DOM smoke)

### Metadata
- **Created**: 2026-09-16
- **Script**: test/packs/fe_edit_agent_e2e_test.sh (+ fe_edit_agent_e2e_test/mock_server.js, harness.js — untracked)
- **Language**: Bash + Node (plain http) + Playwright-as-library
- **Status**: ACTIVE (gate 2026-09-16, branch feature/fix-source-edit-agent @ 55de0a16)

### Configuration
- **Timeout**: internal 240s / outer 300s (dual-layer); actual pack runtime 17s; build (~13s) outside the pack
- **Service Port**: 127.0.0.1:10180 ONLY (mock range; serves frontend/dist/ + /api on one origin — relative API_BASE needs no override)
- **Mock Ports**: none beyond 10180
- **Cleanup**: server PID recorded, SIGTERM in harness finally{}; lsof-proven zero listeners post-run; prod 9797 / dev 8079 / FE 4199 / 8088 never touched

### What It Tests
Edit-Source modal agent selection fix (WeakMap memoization of toSelectOptions + frozen EMPTY_SELECT_OPTIONS): typed search text survives CD cycles with a preselected value; a DIFFERENT agent is selectable and persists in the PUT payload; ADD flow not regressed.

### Mock Services Required
- One-origin Node http server: static dist + GET /api/agents (7 fake agents incl. shared-prefix pair coder/code-reviewer), GET /api/sources (4 fake sources: preselected-leader Slack, null-default_agent telegram, wanderer-default Slack), GET source-by-id, PUT/POST /api/sources (capture bodies to JSON log), minimal boot endpoints + SSE stream. App-level polling endpoints (/api/queues, /api/missions, …) intentionally 404 — harness-noise, documented.

### Test Scenarios
1. S1 EDIT different agent: type "cod" → retained across CD cycles (mouse moves + input events + 1.6s) → filtered panel → select coder → PUT config.default_agent = new agent
2. S2 ADD: type → select → POST config carries agent
3. S3 null default_agent: type → select → PUT carries agent, sibling config keys preserved
4. S4 re-select SAME agent: PUT still carries it
5. S5 switch sources: cancel S1, edit S3 → no stale state → PUT to S3's id

### Success Criteria
- [x] 5/5 scenarios PASS; payloads captured verbatim
- [x] 0 uncaught page errors
- [x] Port freed, prod daemon untouched (PID-verified)

### Last Run
- **Date**: 2026-09-16 · **Worker**: 070f5c22 (e2e-test) · **Result**: PASS 5/5
- **Report**: RESULTS/2026-09-16-source-edit-agent-verification.md + smoke-artifacts/


## Mock Test: LCA Stage-2 Boot Smoke (fused path, stubbed judge)

### Metadata
- **Created**: 2026-09-16
- **Script**: `test/packs/lca2_boot_smoke_mock_test.sh` (+ helper `test/packs/lca2_helpers/mock_llm.py`)
- **Language**: Bash + Python
- **Status**: ACTIVE (gate artifact; committed `473e1428` on `feature/lca-resolver-stage2`)

### Configuration
- **Timeout**: 300s outer / script-internal; EXIT-trap teardown always runs
- **Daemon Port**: 15777 (uvicorn `daemon.api:app`, NO --reload, never dev.sh)
- **Mock Ports**: 15778 (OpenAI-compatible mock LLM endpoint serving leader turns + judge verdicts)
- **DB**: disposable PG14 on port 15434 (initdb -A trust; BOTH per-field POSTGRES_* and POSTGRES_URL set to the same DB — split-brain guard); fresh tmp DATA_DIR
- **Cleanup**: SIGTERM uvicorn; pg_ctl stop + rm -rf; port-freedom asserted (15777/15778/15434); foreign ports 15432/8079/8088/9797 never touched

### What It Tests
- Boot from branch with ENSEMBLE_LEADER_ATTESTATION_MODE unset → default `enforce` boot line + zero Traceback/CRITICAL post-baseline
- Scripted leader→spawn child→final report through the FUSED judge (stubbed): `not_complete → deny_nudge` then `complete → allow`, with `event=leader_completion_gate_fused_judge ... judge_invoked=True` and `event=leader_completion_resolver_eval ... resolver_outcome=...` rows (rows are logger.info events, NOT DB rows)
- Clean shutdown, zero process leaks

### Last Run
- **Date**: 2026-09-16
- **Result**: PASS (pack ~25s; health 200/200)
- **Report**: `RESULTS/2026-09-16-lca-stage2-flip-verification.md` (Job 7)


## Mock Test: Job-Queue Panel Idle-Hiding UI Smoke (FE, feature/job-queue-hide-idle-instances @ 93bd86eb)

### Metadata
- **Created**: 2026-09-19
- **Script**: `test/packs-fe-smoke-mock/mock_be.py` (untracked smoke asset) + `frontend/proxy.conf.mock.json` (untracked)
- **Language**: Python 3 stdlib (mock BE) + Angular dev server (real FE) + browser automation
- **Status**: ACTIVE (first run 2026-09-19 PASS)

### Configuration
- **Timeout**: browser-drive portion ≤ 300 s (pack cap); mock BE self-timeout 600 s; FE boot wait ≤ 120 s
- **Service Port**: 4199 (FE dev server, `ng serve`)
- **Mock Ports**: 10080 (mock BE; FE proxy pointed at it via untracked `proxy.conf.mock.json` — real proxy target 8079 left untouched/free)
- **Cleanup**: kill only spawned PIDs (mock python + ng serve/node) after per-PID cmdline verification; verify 10080/4199 freed. NEVER touch 8088 (self-system), 9797 (prod daemon), 8079.

### What It Tests
- Real rendered DOM of the job-queue indicator panel ("Live conversations"): idle rows hidden (root + child), live child of idle parent orphan-promoted to root, terminal statuses (completed/error) visible, receipts (incl. receipt bound to a hidden idle instance) still surfaced, all-idle dataset → graceful empty state, no crash.
- Exercises BOTH fetch legs through the `instanceRoots` seam (8s poll `listInstanceTree` + menu-open `listInstanceTreeFull`) because the panel is the real component.

### Mock Services Required
- Mock BE on 10080 serving canned wire-shaped JSON for the indicator's forkJoin legs (`/api/instances` tree, `/api/missions` liveness, jobs legs — exact paths discovered from `instance.service.ts` / `mission.service.ts` / `job.service.ts`); unknown paths → 404 JSON.
- DATASET env switch: `mixed` (6-instance matrix + receipts incl. one bound to hidden IDLE-ROOT) / `allidle` (all roots idle, no jobs → empty state). Mock restart switches datasets; ≤2 poll ticks (16 s) to refresh.

### Test Scenarios
1. MIXED: RUN-ROOT + ORPHAN-CHILD (promoted root) + DONE-ROOT + ERR-ROOT visible; IDLE-ROOT + IDLE-CHILD absent from entire panel; receipts render.
2. ALLIDLE: empty state shown, app responsive, no uncaught console errors (404s for unmocked endpoints like notifications SSE are acceptable).

### Success Criteria
- [ ] All MIXED name-presence/absence assertions hold in real DOM
- [ ] Empty state renders without crash on ALLIDLE
- [ ] No uncaught JS errors beyond expected 404s
- [ ] Ports 10080/4199 freed after; zero commits; zero tracked-file edits

### Implementation Notes
- No real daemon boot (8079 left down deliberately — avoids any prod-PG risk); FE-only + mock BE.
- Untracked assets kept for reproducibility; not registered as a permanent pack.

### Last Run
- **Date**: 2026-09-19
- **Worker Instance**: cdc756fc-6b5a-4169-9dc1-85ac7efca124 (e2e-test skill)
- **Result**: **PASS** — MIXED 11/11 (IDLE-ROOT + IDLE-CHILD absent panel-wide; RUN-ROOT visible aria-level=1; ORPHAN-CHILD promoted root aria-level=1; DONE-ROOT/ERR-ROOT in RECENT; receipts incl. MOCK-RCPT-IDLEPARENT Z4 bound to hidden idle root surfaced via recentFlat) + ALLIDLE 4/4 ("No jobs" + "Queue is currently idle", app responsive). 0 uncaught pageerrors (expected 404 noise: notifications SSE, /api/projects, /api/settings/*, /api/health, /api/agents, /api/migration/availability). Playwright-as-library 1.60.0 headless Chromium; browser-drive ~23s. Ports 10080/4199 verified freed; 8088/9797/8079 untouched.
- **Quick Fixes**: none (smoke)
- **Report**: `.agents/tester/RESULTS/2026-09-19-fe-job-queue-hide-idle-instances.md`; evidence `test/packs-fe-smoke-mock/evidence/`

---

## Mock Test: Job-Event Watch Replay Incident Scenario (branch fix/job-event-watch-replay)

### Metadata
- **Created**: 2026-09-23
- **Script**: `test/scenarios/job_watch_replay_incident_scenario.py` (new; script-style, NOT pytest-collected)
- **Language**: Python
- **Status**: PLANNED

### Configuration
- **Timeout**: 120s script-internal / 300s command-level (dual-layer)
- **Service Port**: NONE (in-process, no server)
- **Mock Ports**: NONE
- **Cleanup**: No ports; in-memory/temp-file SQLite only; delete temp DB files on exit
- **ENV SAFETY (MANDATORY)**: `unset POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_USER POSTGRES_PORT POSTGRES_DB POSTGRES_URL DATABASE_URL` before ANY execution — ambient POSTGRES_* points at LIVE ensemble_prod. The scenario MUST construct its own SQLite stack explicitly (never rely on ambient env).

### What It Tests
Reproduces the ORIGINAL incident shape from mission f27e2d15 on a synthetic SQLite stack (real resolver/repos where reachable, honest fakes otherwise — same atomicity contract). PARKED MISSION f27e2d15 MUST NOT BE TOUCHED — synthetic fixtures only.
- Mission instance with N receipts: 9 already-terminal + several live
- (a) `watch_mission` → ONLY live receipts armed (registration-time terminal filter)
- (b) flip mission instance → completed: armed set fires EXACTLY ONCE; ZERO notifications for the 9 pre-terminal receipts
- (c) re-call `watch_mission` → flip again → NO second burst (delta-arm; no duplicates)
- (d) NO non-failed receipt ever carries assistant-message content in the `Error:` slot — inspect rendered payload if reachable, else the notify_watchers call args (failed→error=, else result_summary=)
- (e) `unwatch_job` after consumption → NO NEW emissions after unwatch (already-enqueued backlog delivery semantics unchanged by design — only confirm zero new)

### Reference Harness
- `.agents/tester/RESULTS/2026-09-19-mission-watch-toolset-replay-script.py` (SQLite tool-layer, real resolver/repos, CAS exactly-once) — adapt its stack-construction pattern

### Success Criteria
- [ ] All five scenario verdicts (a)–(e) PASS with evidence (counts, payload/args excerpts)
- [ ] Exit 0; RESULT: PASS line printed
- [ ] Zero live-DB contact (no POSTGRES_* in env during run)

### Last Run
- **Date**: 2026-09-23T18:25Z
- **Worker Instance**: c22292f3-1df1-4fc3-8214-348d5b7d2ef9 (verify-jw-scenario)
- **Result**: ✅ PASS — 5/5 scenario verdicts (a)–(e); runtime 4.5s; commit `347896a2` (script only, parent ac789d45)
- **Quick Fixes**: none
- **Report**: RESULTS/2026-09-23-job-watch-replay-verification-ac789d45.md

---

## Mock Test: Embedding-Gzip Wire Exemption (strict-server red→green repro)

### Metadata
- **Created**: 2026-09-26
- **Script**: `tests/mocks/embed_gzip_wire_mock.py` (server + driver); pack wrapper `test/packs/embed_gzip_wire_mock_test.sh`
- **Language**: Python
- **Status**: IMPLEMENTED (green side = permanent pack; red side = one-off scratch at base commit)

### Configuration
- **Timeout**: 120 s self (`signal.alarm`) + 240 s pack-internal `timeout` + `timeout 300` outer (dual-layer)
- **Service Port**: n/a — no daemon started; in-process code path only
- **Mock Ports**: **18771** (strict embeddings server; 10000-19999 mock range)
- **Cleanup**: kill leftovers on 18771 at start (after verifying the port is not 8088 and belongs to this repro); EXIT-trap teardown; server thread daemonized

### What It Tests
- ORIGINAL-BUG closure (green, fix `ce644d4a`): with `request_gzip=True` and `EMBEDDING_BASE_URL` → local strict server, `SkillEmbeddingService.embed_text` must send PLAIN JSON (no `Content-Encoding: gzip` header, no gzip magic bytes) and receive 200.
- Chat-path separation (green): a client built via `daemon.services.llm_gzip.resolve_gzip_client(True)` still sends `Content-Encoding: gzip` — gzip transport remains active for chat, untouched by the fix.
- Symptom reproduction (red, base `139ba352`, scratch): the SAME driver against base code must show gzip body on the wire → strict server 400 → client-side failure surfaces. Proves the repro captures the real bug.

### Mock Services Required
- Local strict HTTP server on 18771 (any path accepted, path logged): if request carries `Content-Encoding: gzip` OR body fails `json.loads` → HTTP 400 with OpenAI-style body `{"error": {"message": "We could not parse the JSON body of your request. ...", "type": "invalid_request_error", "param": null, "code": null}}`; else 200 with embeddings-shaped JSON (`{"object":"list","data":[{"object":"embedding","index":0,"embedding":[...]}],"model":"...","usage":{...}}`).

### Test Scenarios
1. GREEN embed: `embed_text("repro probe")` under `request_gzip=True` → 200, vector returned, no gzip on wire.
2. GREEN chat-path: POST via gzip-enabled client → server observes `Content-Encoding: gzip`.
3. RED (scratch, base only): `embed_text` → server observes gzip body → 400 → failure surfaces client-side.

### Success Criteria
- [ ] GREEN: 200 + no `Content-Encoding: gzip` on the embeddings request + valid JSON parse server-side
- [ ] Chat-path: `Content-Encoding: gzip` observed
- [ ] RED scratch: gzip observed + 400 returned + client failure surfaced (exit 0 only when the expected side's outcome holds)
- [ ] Per-request transcript lines captured (path, Content-Encoding, body head hex, parse verdict, response status)
- [ ] Port 18771 freed after run; no process leaks

### Implementation Notes
- Driver flags: `--expect {green,red}` (exit 0 iff expected outcome observed), `--repo-root PATH` (defaults to cwd — lets the SAME driver file run against a temp worktree at base while `uv run` resolves the venv at that root), `--port` (default 18771).
- Driver sets its own env (`OPENAI_API_KEY=sk-repro-local`, `EMBEDDING_BASE_URL=http://127.0.0.1:<port>/v1`, `OPENAI_REQUEST_GZIP=true`) — overridable; exact env/config-key names verified against service source at both commits (constructor unchanged by `ce644d4a`).
- If `resolve_gzip_client` returns a wrapper rather than a raw httpx client, the chat-path check adapts per `daemon/services/llm_gzip.py` source and discloses the adaptation.

### Last Run
- **Date**: 2026-09-26
- **Worker Instance**: 44a65efa-5ac2-4dcf-80c1-4bac2eb86876 (W5)
- **Result**: PASS — GREEN (permanent pack, :18771): embed `Content-Encoding=absent`, plain JSON, 200; chat-path `Content-Encoding=gzip` (magic `1f8b`) present. RED (scratch, temp worktree @ base 139ba352, :18772): embed gzip on wire → 400 "We could not parse the JSON body of your request" → client RuntimeError. Two-sides closure proof satisfied.
- **Quick Fixes**: driver repaired @ `7375503e` (gzip_magic scope hoist + PROBE_TEXT ~2160 chars above gzip-shrink threshold — see LESSONS/2026-09-26-gzip-wire-repro-body-threshold.md)
- **Report**: RESULTS/2026-09-26-embedding-gzip-exempt-verification.md
