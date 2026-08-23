# Pre-Merge Verification: P2.2 Ari Agent-Facing Upgrade Tools

- **Date:** 2026-08-23 · **Tester:** tester agent (Test Leader) — 10 worker dispatches, 0 direct executions
- **Branch:** `feature/self-restart-p2p2-ari-tools` @ `ca5a404e` (base `d4c41d68` == P2.1 merge commit on `latest`)
- **Verdict: ✅ PASS — MERGE-READY** (all 6 mandated statements green; zero merge blockers; observations + P2.3 ledger items below)

## 0. Scope Decision

Full suite NOT run. Blast radius derived from the diff (`d4c41d68..ca5a404e`: 23–24 files, ~+7.4–7.7k/−26…130, 9 daemon files — two workers measured 23 files/+7367/−26 via merge-base vs the dispatch brief's 24/+7690/−130; measurement-window difference, zero safety impact). Change set = new `system_upgrade` tool category + additive manager/messaging drains + tool-auth/docs default-deny surface. Ran: the 2 new packs, the drift guard, the tools sweep (change set's module), concurrency pack (ensure.md Core, async surfaces touched), boot_report_recovery spot-run (manager-drain adjacency), full static + dynamic live-safety checks, e2e-gate evidence + independent ruling. Skipped: full e2e release gate (ruled NOT TRIGGERED — §4), non-tools packs (out of radius). Reason: single-category tool addition + additive daemon seams; smallest scope covering the change.

## 1. Pack / unit results — verbatim (task §1)

All runs at HEAD `ca5a404e`, branch verified before AND after; every run dual-layer timeout; VERIFICATION-ONLY (zero repo modifications, zero quick fixes, no merge/push).

| Pack / target | Expected | Actual (verbatim summary) | Result | Runtime | Log |
|---|---|---|---|---|---|
| `upgrade_tool_interlock_unit_test` | 135/135 | `135 passed in 2.87s` | ✅ PASS | 3s wall | `/tmp/p22-verify-interlock.log` |
| `upgrade_registration_unit_test` | 21/21 | `21 passed in 2.65s` | ✅ PASS | ~3s | `/tmp/p22-verify-registration.log` |
| `tools_suite_unit_test` | 836P/5 deselect/0F | `836 passed, 5 deselected, 2 warnings in 17.35s` | ✅ PASS | 18s | `/tmp/p22-verify-toolssweep.log` |
| `frozen_tool_name_discovery_unit_test` | drift test PASS | `6 passed in 0.98s` + `test_known_tool_names_matches_source_exactly_no_drift PASSED` quoted | ✅ PASS | ~2s | `/tmp/p22-verify-drift.log` |
| `tests/integration/test_boot_report_recovery.py` (scoped spot-run; no registered pack — P2.1 mandated-run precedent) | 14 | `14 passed in 1.43s` | ✅ PASS | 2.45s wall | `/tmp/p22-verify-bootreport.log` |
| `concurrency_atomic_unit_test` (canonical 13-file set, recovered from the provenance log `/tmp/concurrency_atomic_unit_test.log` cited by RESULTS/2026-08-21) | 91P/74S/0F baseline | `91 passed, 74 skipped, 44 warnings in 7.04s` | ✅ PASS baseline-exact | 8s | `/tmp/p22-verify-concurrency.log` |

**Quarantine confirmation:** tools_suite deselect set == EXACTLY the 5 quarantined `TestAccessMemoryArchive` tests (QUARANTINE.md 2026-08-20): `test_access_archive_valid_path`, `test_access_archive_path_traversal_rejected`, `test_access_archive_invalid_format_sanitized`, `test_access_archive_nonexistent_returns_not_found`, `test_access_normal_file_still_works`. Pytest reported exactly "5 deselected" == the pack's 5 flags → no second deselect source, **no new failures, no new deselects**. Same 5, confirmed.

**Git drift:** every worker recorded `git status --porcelain` before AND after — identical; zero drift introduced by any run. Two pre-existing local modifications observed by all: ` M .agents/approver/active.md` (protected reviewer state, never touched) and ` M .agents/tester/MOCK_TESTS.md` (**the tester's own planning spec for the dynamic sandbox, written before the wave — disclosed, committed with this verification, see §7**).

## 2. Live-safety — STATIC (task §2a) — third independent check

**Verdict: live-reachable paths found: NONE. UNSAFE: 0. NEEDS-DYNAMIC: 0.**

- Diff-wide + full-content literal sweep (`9797`, `agents-ensemble`, `ENSEMBLE_DEPLOY_LIVE`, `ENSEMBLE_UPGRADE_LIVE`, `Path.home`, `environ[HOME]`, `live_pid`, `prod`): every hit classified SAFE (doc text, poison-sentinel tests asserting absence, display-only strings, docstrings, refusal tests) or ZERO-hit. `ENSEMBLE_DEPLOY_LIVE`: **zero occurrences** in daemon/scripts/tests on this branch. Port `9797`: **zero literals** in branch code/tests/scripts.
- **Live topology reachable only behind a fail-closed marker chain**: `_self_env_marker()` reads `ENSEMBLE_SELF_ENV` (staged into `INSTALL_DIR/.env`, exported by launcher; port-derivation fallback deliberately rejected) → `_resolve_install_dir` returns `~/agents-ensemble` ONLY for `self_env=="live"` AND `target_env=="live"` (symmetric self-match gate runs before any live-gate logic; marker-absent actors fail closed `reason=env-marker-absent`). A dev/demo/sandbox daemon cannot resolve the live dir.
- **Executor env-allowlist (verbatim, `upgrade_journal.py:740-743`)**: `EXECUTOR_ENV_ALLOWLIST = ("PATH","HOME","INSTALL_DIR","PORT","POSTGRES_DB","TMPDIR")`, `EXECUTOR_ENV_PREFIXES = ("PG",)` → `ENSEMBLE_UPGRADE_LIVE` and `ENSEMBLE_DEPLOY_LIVE` stripped at the ONLY spawn seam (both tool layer and `manager.drain_pending_system_execution` route through it; `upgrade_tools.py` contains zero subprocess refs). Consequence: even a hypothetical live executor can never satisfy lib.sh `require_live_guard` (exit 78) — its only key is stripped at spawn.
- **`system_restart` on live refuses outright** (`upgrade_tools.py:1353`) — no gate, no override, no dry-run exception.
- **`restart.sh`**: target REQUIRED (absent/invalid → exit 78 before anything); all writes keyed to `INSTALL_DIR` resolved from the explicit target; stop is ALWAYS ownership-scoped `stop-ensemble.sh` (never raw kill); the single live-path read in the subsystem is the defensive sandbox port-collision check (read-only, fail-closed).
- **Tests cannot read real markers** (proven 3 ways): `ENSEMBLE_SELF_ENV` via monkeypatch; `_resolve_install_dir` → pytest tmp fixture; every subprocess gets fake `HOME` so real lib.sh canon-checks read the fake home.
- **Port audit**: no 9797/8079 usage/conflict; ⚠ cosmetic convention notes — test literals 8399 (sandbox probe), 7979/5432 (demo/env-dict semantics) sit outside the 10000–19999 mock range; none can bind or touch live.
- **ensure.md static check**: `dev.sh` `--timeout-graceful-shutdown 10` — PRESENT (`dev.sh:102`).
- Residuals (documented, not unsafe): U4 live-resident read-only self-observability (materializes only if user deploys to live); pre-existing deferred origin else-branch defect (`instance_messaging.py:1310-1319`, mitigated at the tool by `USER_ORIGIN_SOURCES`, P2.3).

## 3. Live-safety — DYNAMIC (task §2b)

**Verdict: PASS — 7/7 scenarios, 3 consecutive stable runs (~3s each; caps 240s/300s), zero live contact.**
Script: `tests/mocks/upgrade_tools_live_safety_mock.py` (developed/run from `/tmp/p22-dynamic-sandbox/`, committed as test-infra with this report; spec in MOCK_TESTS.md). Real seams, fake state: real `create_upgrade_tools()` factory, REAL `InstanceManager.stamp_user_origin_window`/`set_pending_system_execution` bodies (only outermost DB edges mocked), real nonce store/journal/lock, real `lib.sh`/`status.sh` fixture writers, REAL `spawn_executor`; fake state via `HOME` redirection to a fake home so the REAL `_resolve_install_dir("live"/"demo")` resolves FAKE install trees — zero monkeypatching of repo code.

| # | Scenario | Result | Evidence essence |
|---|---|---|---|
| S1 | Read-only parity vs real `status.sh` on same lib.sh fixture | ✅ | 16/16 field checks (current/previous/in-flight/rollback-window/quarantine/releases/labels/symlink/lock/history/livez-degrade); known divergences: env-triple + port (sandbox-mode vs daemon-port semantics), `dir=` exact |
| S2 | Live restart refusal | ✅ | `reason=live-restart-refused` even with `user_confirmed=true` + HUMAN origin + valid nonce + `dry_run=false`; journal byte-identical, no lock, no marker |
| S3 | Refusal taxonomy (dynamic) | ✅ | 7 cases, distinct tokens: `invalid-target-env` ×2, `env-marker-absent`, `env-self-match`, `user-confirmation-missing`, spoofed-origin (real stamping path POPS window → gate sees no HUMAN), `nonce-mismatch`, `nonce-expired` |
| S4 | 3-factor LIVE gate PASS on fake live marker | ✅ | nonce minted by real dry_run, bound (kind/env/target/instance); armed SCHEDULED with SAME run_id; `pending_op` human-confirmed; nonce consumed; replay → `nonce-already-used` |
| S5 | Demo arming + REAL spawn env-allowlist proof | ✅ | dry_run default TRUE zero mutation (byte-identical); child env-dump table below; cwd=install dir, stdio→`data/upgrade.log`, child reaped |
| S6 | Journal-poll after fake restart | ✅ | shell-twin finalize via real lib.sh; `upgrade_status(run_id=<armed>)` → TERMINAL `outcome=restarted (intentional)`, SAME run_id round-trip, lock free |
| S7 | Zero-live-contact guards | ✅ | all 4 green (below) |

**S5 env-allowlist proof (REAL spawned process):** `ENSEMBLE_UPGRADE_LIVE` ✂, `ENSEMBLE_DEPLOY_LIVE` ✂, `ENSEMBLE_SELF_ENV` ✂, `OPENAI_API_KEY` ✂, `ANTHROPIC_API_KEY` ✂, `POSTGRES_PASSWORD` ✂, `POSTGRES_URL` ✂, `SECRET_POISON_CANARY` ✂, `AWS_SECRET_ACCESS_KEY` ✂, `XDG_CONFIG_HOME` ✂ — all present in parent, ABSENT in child. `PGPASSWORD` survives **by design** (`PG*` prefix allowlist, R-SR09) → observation #1 below.

**S7 guards:** (a) `lsof -nP -iTCP:9797 -sTCP:LISTEN` byte-identical before/after (read-only); (b) `~/agents-ensemble` + `~/agents-ensemble-demo` top-level stat unchanged; (c) all 8 fixture/journal/log paths resolve under the sandbox tmp root (macOS `/tmp`→`/private/tmp` handled); (d) no leaked processes (inline pgrep collectors; zombie reaped), port 10797 free after every run. **Provably untouched: real live install, live port listener, prod DB, `ENSEMBLE_DEPLOY_LIVE` (never exported), live pids.**

## 4. e2e-gate ruling — INDEPENDENT (task §3)

**Ruling: NOT TRIGGERED. Release Gate not run. Reasoning:**

ensure.md + the critical note mandate full e2e when changes touch the job/task/queue system — the 5 trigger systems. Hunk-level classification of every daemon hunk (`git diff d4c41d68..ca5a404e`, 9 daemon files):

- **No trigger-system module is in the diff at all**: `task_processor.py`, `job_queue_service.py`, `worker_pool.py`, `repositories/task/*`, `repositories/job_queue/*`, turn/mirror modules — all absent. `upgrade_tools.py`'s `reconcile_pending_op` is the upgrade file-journal's own reconcile (naming coincidence, unrelated to `reconcile_turn_mirror`).
- **`manager.py` (+187/−0, 0 deletions)**: purely additive — state dicts in `__init__`; 3 NEW methods (`stamp_user_origin_window`, `set_pending_system_execution`, `drain_pending_system_execution`); one top-of-body insertion in `_process_message_with_tracking` (pre-graph entry stamp) before the UNCHANGED delegation. No turn-finalize/reconcile/task-processing statement modified.
- **`instance_messaging.py` (+38/−0)**: two 6-line pure INSERTIONS into post-graph `finally:` blocks, placed after the pre-existing watchover drain and before the UNCHANGED task-unregister — mirroring the established drain-consumer pattern (T2.9 precedent). Graph/turn flow: zero modified lines.
- **`job_queue.py` (+13/−3)**: modifies pre-existing logic but ONLY the `source` string stamped on the job (`job_create` agent-caller forcing made unconditional — closes the F2 forging seam; `job_continue` fallback literal `"api"`→`"internal_agent:unknown"`). JobItem creation mechanics, queue resolution, admission, locking: untouched.
- **Trigger-symbol grep over the full diff**: 5 hits, ALL docs/test comments (planning doc quoting the rule; PACKS.md prose; test scoping comments). Zero in daemon code.
- **Caveat recorded**: the dev/reviewer "additive-only" claim is NOT strictly true for all 9 daemon files — `job_queue.py`, `loader.py`, `help.py`, `instance.py` contain genuine (c)-class modifications (source forcing; privileged-category stripping in docs/authz default-allow surface, R-SR16). **None of those hunks fall inside the 5 enumerated trigger systems**, so the ruling stands. Precedent-consistent: AR-Phase-1's TRIGGERED ruling rested on `api.py` lifespan shutdown changes (shared finalization infra); nothing comparable here — the new drains are pre-spawn markers, not finalization-order changes.
- Belt-and-braces: the post-graph-insertion surfaces are covered by the manager-drain adjacency spot-run (`test_boot_report_recovery.py` 14/14, §1) and the concurrency pack (91P/74S/0F baseline-exact — deadlock/thread-identity integrity intact with the new awaits in the finally path).

## 5. Mock quality — TrueAuto rule (task §4)

**Verdict: MOCKS-MATCH-REAL.** (Naming correction: the interlock suite is `tests/unit/tools/test_upgrade_journal.py` (53) + `test_upgrade_tools.py` (76 defs + 6 parametrized = 82) — the dispatch brief's `test_upgrade_tool_interlock.py` does not exist; count arithmetic 53+82=135 matches the pack.)

- **Seam test (dispatch→stamp→gate) uses real objects**: real `job_create` tool from the real factory (forcing logic `if caller_agent_id: source=f"agent:{id}"` runs REAL, asserted `agent:ari`); REAL `InstanceManager.stamp_user_origin_window` (the actual method body, which calls real `is_user_origin_source`); REAL gate code reading the chained real stamped state; nonce minted by real tool dry_run; journal read via real `uj.journal_read`. Mocked = outermost edges only (DB repos, network port, install-dir resolution, process spawn + AST pin that tools have no direct spawn).
- **Fixture writers are real lib.sh/status.sh**: journal trees written by REAL `journal_init/journal_set_current/journal_open_txn/journal_quarantine/journal_history_append`; cross-twin interop via real `journal_update/journal_read`; status parity via real `status.sh` subprocess. Pack scripts are pure pytest wrappers (no hand-rolled writers).
- **Marker parity table**: all ✅ (env-marker contract, journal tree by construction, lock fields, `.launcher-state` 6 keys key-for-key, window dict keys + real helpers, nonce via real mint, `USER_ORIGIN_SOURCES` content-pinned, manifest = documented identity-field subset of stage.sh's 13-field schema — checksums belong to executor-side integrity, out of tool scope).
- **Env-allowlist test pins the REAL imported constant** (no duplicate) + a REAL-spawn behavioral test asserting the observed child env.
- 4 cosmetic notes (opaque `run_id` fixture value — the two real writers themselves disagree, no reader validates format; TTL 600 vs real 900 same-direction; epoch placeholder vs realistic epochs in dedicated tests; manifest subset) — none affect fidelity.
- **Honest scope**: the fully-wired dispatch funnel (job_processor→instance_messaging→task_processor) is explicitly deferred to P2.3 (docstring-flagged; consistent with the M2 source=None carry-over) — not silently missing.

## 6. Original-symptom closure — tools usable + safe (task §5)

**CLOSED.** (a) Real `agents/ari/meta.json` via the get_version/get_resolved convention (`AgentRegistry(REPO_ROOT/"agents")` — the REAL agents tree): `system_upgrade` in `meta.tools.allow` → ALL 4 tools resolve (`_upgrade_names(by_name) == UPGRADE_TOOL_NAMES`); (b) worker/jober/watcher parametrized: NONE resolve; (c) empty-allow agent: NO tools (`_upgrade_names == set()` while ordinary categories still granted — R-SR16) AND NO docs (none of the 4 names in `load_tools_doc_for_agent` output — no system-prompt leak; paired `_get_allowed_tools` ∩ upgrade == ∅); (d) `PRIVILEGED_TOOL_CATEGORIES == frozenset({"system_upgrade"})` pinned. No skip/xfail markers on any of these tests. Plus the registration pack (21/21) and tools sweep (836/836 non-quarantined) prove it end-to-end through the real registry at the exact tip.

## 7. Documentation & artifacts

- **RESULTS** (this file) + `2026-08-23-p2-2-daemonized-executor-survival.md` (M5, committed by implementer).
- **PACKS.md**: rows updated (interlock, registration, tools_suite, frozen_tool_name_discovery, concurrency) + verification bullet.
- **MOCK_TESTS.md**: dynamic-sandbox spec (written pre-wave — the ` M` several workers flagged as anomalous is this file; benign, tester-authored) + Last Run filled.
- **Test-infra commit**: `tests/mocks/upgrade_tools_live_safety_mock.py` (from `/tmp/p22-dynamic-sandbox/`) + the doc updates above, committed on the feature branch AFTER all parallel runs finished (verification ran at exact tip `ca5a404e`; the commit touches tests/mocks + .agents/tester only — daemon byte-identical). `.agents/approver/active.md` NEVER staged.

## 8. Non-blocking observations → P2.3 ledger

1. 🟠 **`PG*` prefix passthrough widens executor env surface** — any `PG*`-prefixed var in the daemon env (e.g. real `PGPASSWORD`) reaches the daemonized executor. Deliberate (pipeline DB access, R-SR09) but re-examine when the live rung opens (F2 lane).
2. 🟢 "Additive-only" claim overstated for 4 daemon files (see §4 caveat) — recorded for review-accuracy hygiene; no trigger system touched.
3. 🟢 Test port literals 8399/7979/5432 outside the 10000–19999 mock range (none can touch live) — cosmetic convention cleanup.
4. 🟢 Dispatch brief stat (24 files/+7690) vs measured (23/+7367) — measurement-window difference, no impact.
5. 🟢 Dispatch-funnel full integration (T7–T9 e2e drills) = P2.3 DR-5 scope per plan (consistent with M5 scope note).

## 9. ensure.md status (blast-radius scoped)

- Core Critical #1 (no regressions in changed packs): ✅ all packs in change set PASS (§1).
- Core Critical #2 (deadlock/concurrency integrity): ✅ 91P/74S/0F baseline-exact (§1).
- Core Critical #3 (no sync DB on asyncio loop): ✅ same pack, thread-identity green (§1).
- Core Critical #4 (`dev.sh --timeout-graceful-shutdown 10`): ✅ present (§2).
- Core Important (async callers awaited / deadlock scenario): ✅ covered by the same pack + diff audit (no un-awaited new calls; drains awaited in finally).
- Release Gate: NOT TRIGGERED (§4) — not run, reasoning documented per requirement.

---

### Overall Status — P2.2 pre-merge

- Packs/unit reproductions: ✅ 6/6 exact (135, 21, 836/5/0, 6, 14, 91/74/0)
- Live-safety static: ✅ NONE reachable (third independent confirmation)
- Live-safety dynamic: ✅ 7/7, real-spawn allowlist proof, zero live contact
- e2e gate: ✅ NOT TRIGGERED (independent ruling, hunk-evidenced)
- Mock quality: ✅ MOCKS-MATCH-REAL
- Tool-availability closure: ✅ CLOSED

**Verdict: PASS — MERGE-READY. Nothing blocks the merge.** (P2.3 ledger items in §8.)
