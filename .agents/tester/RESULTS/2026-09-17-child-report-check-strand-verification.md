# Verification Gate — child-report-check strand fix (Stage-0 task-less READY wedge)

- **Date**: 2026-09-17 (completed 20:00 UTC)
- **Branch**: `feature/fix-child-report-check-strand` — fix tip `91882f0a` (`d5e6adee` implementation + `91882f0a` comment-only tidy); tester additions `9bff3629` on top. Base `6e5c2c44` = `latest`.
- **Footprint** (`git diff 6e5c2c44..91882f0a --stat`): 5 files, +678/−9 — `daemon/services/child_reports.py` (+121), `daemon/repositories/message_queue/predicates.py` (+100), `daemon/repositories/message_queue/repository.py` (+8, **docstring-only** — see §6), `tests/unit/test_child_terminal_contradiction.py` (+258), `tests/unit/test_message_queue_pending_predicate.py` (+200).
- **Defect class**: LCA Stage-0 `child_report_check` advisory notes minted as task-less READY message_queue rows — undeliverable (delivery is task-driven only) yet counted pending by the root turn-end finalizer → parents permanently re-park WAITING_CHILDREN (live wedge: instance 421c6a3d, stranded row 36d0a2ef — the exact scenario spec (d) targets).
- **Fix**: (1) mint note row + PENDING PROCESS_MESSAGE Task in same SAVEPOINT + worker-pool notify post-commit; (2) skip mint for terminal parents (COMPLETED/TERMINATED/ERROR/FAILED); (3) `finalizer_counts_as_pending()` count-guard in predicates.py — task-less `child_report_check:` rows never count, task-carrying count, unknown task-less sources still count + WARNING.

## ✅ VERDICT: PASS — SHIP (merge-ready from testing)

7 workers (recon, seam pack, adversarial audit, edge-gap author, broad-regression base-compare, concurrency pack, results commit), all `uv run python -m pytest` only, dual-layer timeouts on every execution, verification-only except the authorized test-gap commit `9bff3629` (test files only, path-scoped). Zero branch-caused failures across the full `tests/unit` sweep; zero mock tautologies; the live-wedge regression test is GENUINE (mutation-killing); 3 real coverage gaps found by the audit were closed and committed.

---

## 1. Seam tests (mission item 1) — PASS

`timeout 300 uv run python -m pytest tests/unit/test_child_terminal_contradiction.py tests/unit/test_message_queue_pending_predicate.py -q --tb=short -rf --disable-warnings`
→ **`62 passed, 19 warnings in 1.69s`** (exit 0). Exactly the expected 62/62; collection count independently confirmed at recon (62 in 0.13s). After tester additions (§4): **67 passed in 2.40s**.

## 2. Adversarial mock check (mission item 2) — NO TAUTOLOGY FOUND

- **Claim "tests mock ONLY the worker pool": literally REFUTED, materially UPHELD.** The new async test wires 4 external-collaborator seams (worker pool `Mock()`, content-fetch, post-commit side-effects, instance-repo/live-hub); pre-existing suite adds an `_LLMGuard` sentinel and a class-level `Session.flush` fault-injection. **But zero mocks touch the persistence layer** — no mock objects near the engine, sessions, MessageQueue, or Task rows; every DB assertion in both files reads/writes real SQLite.
- Every core claim is asserted against **real DB rows**: (i) task-less exclusion (predicate :699-721 real row + real `_row_has_any_task` SELECT), (ii) task-carrying counts (:723-742 real Task row), (iii) mint shape (:1274-1324 real matched message_id/PENDING/work_id — atomicity leg was weaker, closed in §4), (iv) terminal suppression (:1326-1397 — 1/4 statuses, closed in §4), (v) unknown-source counts + WARNING against the production logger (:766-794).
- No test asserts its own mock's configuration; the `pool.notify_work.assert_called_once` sits AFTER the real production chain (real mint → real `_note_task_minted` flag → real wake branch), so removing the wake fails the test.
- FK enforcement: predicate file fixture `PRAGMA foreign_keys=ON` (:79-83) ✓; contradiction file fixture has no FK pragma — **immaterial today** (zero `ForeignKey` declarations in message_queue/task/instance models), noted as fixture-hygiene inconsistency.

## 3. Spec-(d) live-wedge reproduction (mission item 3) — GENUINE

`test_legacy_stranded_note_does_not_block_root_completion` (:1442-1496) — all four legs verified by static trace:
- **(a)** seeds the stranded row inline, `source=f"child_report_check:{child_id}:legacy-report-1"`, READY, **no Task** (`_seed_task` never called);
- **(b)** drives the REAL finalizer: production `_process_child_completion_db_sync` → root carve-out → real DependencyBus count → real live-children COUNT → the fixed call-site SELECT+sum at child_reports.py:2490-2514 (`finalizer_counts_as_pending(_row, self._manager.engine)` on the real test engine);
- **(c)** asserts `outcome == "root_completed"` AND persisted `InstanceStatus.COMPLETED`;
- **(d)** asserts the stranded note RETAINED un-mutated (`status == READY`).
- **Mutation-kill proof**: reverting the call-site to the base predicate yields `pending_count == 1` → `root_waiting_children` → assertion fails. Not predicate-in-isolation.

## 4. Edge cases (mission item 4) — 3 real gaps closed, commit `9bff3629`

Audit-verified coverage BEFORE: (a) unknown task-less source + WARNING — already covered (:766); (b) `system:watchdog` + task counts — already covered (:796). NOT duplicated.
- **(c) terminal suppression ×4** (was COMPLETED-only): existing test parametrized over all four statuses. **Finding**: TERMINATED never reaches `db_terminal_parent` — `db_dead_parent` (child_reports.py:3100) fires first (outcome `dead_parent_skip`, report FAILED, no PROCESS_REPORT, `reason=dead_parent`), exactly as the production comment :3240 states. Tests assert the REAL rung per status; `[terminated]` cannot catch deletion of that tuple leg (defensive redundancy upstream) — honest caveat, no production change needed.
- **(d) non-READY advisory + task**: gap was REAL (task-carrying advisory test was READY-only). Added `test_task_carrying_advisory_non_ready_status_counts[processing|retrying]` — PROCESSING/RETRYING `child_report_check:` row + correlated PENDING Task → counts True (falls through to base predicate, carrier live in `_LIVE_TASK_STATUSES`, no WARNING for non-READY). In-flight delivery is never masked.
- **SAVEPOINT atomicity leg** (audit defect 1): `test_savepoint_fail_open_on_note_insert` now also asserts ZERO PROCESS_MESSAGE Task rows on the parent after rollback (probe by type — the completion path mints PROCESS_REPORT only; grep-verified exactly two TaskType mint sites :3135/:3352). Moving the carrier `session.add` outside the SAVEPOINT now fails the suite.
- **Verification**: full two-file pack **67/67 in 2.40s** (62 baseline + 5 new nodes). Zero new mocks; real-DB SELECTs only. `py_compile` + name-grep landed.
- **Commit**: `9bff3629` — 2 test files, +114/−18, path-scoped (`git show --stat` verified; foreign residue untouched; author stamps as repo-local `Councilor C2` identity per known project note).

## 5. Broad regression + base-compare (mission item 5) — CLEAN

Detached worktrees (`git worktree add --detach`), both bootstrapped `uv sync`, no PG/env overrides, main checkout untouched. Branch side pinned at `91882f0a` (pre-`9bff3629` — apples-to-apples with the developer's method; tester additions verified separately in §4).

| Side | Failed | Passed | Skipped | Errors | Wall |
|------|--------|--------|---------|--------|------|
| Branch `91882f0a` | 65 | 11,946 | 58 | 23 | 392s |
| Base `6e5c2c44` | 65 | 11,936 | 58 | 23 | 392s |

- **Failure+error sets BYTE-IDENTICAL** (`diff` exit 0 both FAILED and ERROR lists; `comm -23`/`comm -13` empty). **Zero branch-caused, zero divergent.**
- All 65 FAILED + 23 ERROR map to QUARANTINE.md families (AccessMemoryArchive ×5, job_queue_proxy_phase1 ×8, find_near ×13, paused_auto_resume ×5, coder_developer_migration ×5, bootstrap/context7/webfetch error family ×23, etc.).
- Known reds adjudicated: `test_in_progress_guard` ×2 **absent from BOTH sides** (fixed upstream of base — allowed per mission clause); `TestAccessMemoryArchive` in both (5/5 byte-identical); `test_dotdot_traversal_blocked` in both (consistent pre-existing, not divergent this run).
- **Count delta vs developer claim** (64F vs our 65F branch): 1 node flipped within the pre-existing red set between runs (normal flake, e.g. mcp_tool_timeout ctx-flake class); set identity is the load-bearing result.
- Branch carries **+10 passing** over base. Logs preserved: `/tmp/ae-crcs-branch-run.log`, `/tmp/ae-crcs-base-run.log`, `/tmp/{branch,base}-{failed,error}.txt`. Worktrees removed post-run.

## 6. Footprint anomaly adjudicated

`daemon/repositories/message_queue/repository.py` (+8) — NOT in the dispatch brief's file list — is a **docstring-only hazard note** on `add()` (commit `91882f0a`, no executable change). The real wiring lives in child_reports.py: import flip :2479-2483 + call :2514, exercised end-to-end by the spec-(d) test. Benign; no action. Residual note: the mint path bypasses `repository.add()` (raw `session.add` in the SAVEPOINT), so the `add()` docstring is documentation-only protection for future callers.

## 7. ensure.md validation — PASS (Core scoped; Release Gate not warranted)

- **Core #1** (no regressions in changed packs): PASS — seam pack 62/62 (then 67/67), broad sweep zero branch-caused.
- **Core #2 + #3** (deadlock/concurrency integrity; no sync DB on event loop): PASS — `concurrency_atomic_unit_test` pack **98 passed / 74 skipped / 0 failed in 8.24s** (baseline parity; includes test_deadlock_fix, cascade races, observer race, atomic locks, thread-identity gate, finalize_job h15).
- **Core #4** (dev.sh `--timeout-graceful-shutdown 10`): PASS — present at dev.sh:102 (static grep).
- **Release Gate**: NOT RUN — scope decision: scoped bugfix (message-completion path, 2 production files + docstring), not a big/critical/architecture change; broad `tests/unit` A/B already exceeds Core requirements. PG existence-probe (optional per mission) skipped — not gating.

## 8. Follow-ups surfaced (non-blocking)

1. 🟠 Residual same-class count-gates at child_reports.py:945 and :2726 still use raw status-COUNT queries — if reachable on the root path with a stranded note present, the wedge could resurface as a suppressed completion report (fix author documented deliberate scope-out; reachability unverified — needs a targeted probe).
2. 🟢 FK-pragma fixture inconsistency between the two test files (immaterial while models declare no FKs; will silently tolerate orphans if constraints are ever added).
3. 🟢 Test seeds bind tz-aware `datetime.now(timezone.utc)` where convention is `now_utc_naive()` — inert on SQLite + UTC-pinned PG session; cosmetic.
4. 🟢 `test_in_progress_guard` ×2 stale-fixture reds now pass at base — positive lineage drift; QUARANTINE rows may be retried upstream.

## 9. Workers

recon `33ffbf79` (10/10 steps, footprint anomaly surfaced) · seam pack `aa2213d0` (test-pack-execution) · adversarial audit `7d3b40b6` (3 verdicts + 2 gap discoveries) · broad-regression `aee0947c` (test-pack-execution, base-compare contract) · concurrency pack `ca5130d3` (test-pack-execution) · edge-gap author `6acdf686` (quick-fix, commit `9bff3629`) · results commit (see below).

**Overall Status**: Unit/seam ✅ · Mock audit ✅ · Spec-(d) ✅ · Edge gaps ✅ (closed) · Broad regression ✅ CLEAN · ensure.md Core ✅ → **SHIP**.
