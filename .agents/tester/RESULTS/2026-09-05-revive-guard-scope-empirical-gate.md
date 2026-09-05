# Empirical Gate — ReviveGuard Scope Fix (2026-09-05)

**Branch**: `feature/fix-revive-guard-scope` @ `1683cd40` (base `770da22a`)
**Feature**: only ERROR/FAILED prior-status agent-tool revives consume the ReviveGuard counter; COMPLETED/TERMINATED revives granted without incrementing; refusal check (counter ≥ 1) unchanged; job_continue FAILED still consumes.
**Worktree**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-revive-fix` (main repo checkout BUSY — never operated in; verified by all workers)
**Branch diff**: exactly 6 files — `daemon/manager.py`, `daemon/tools/instance.py`, `daemon/tools/job_queue.py` + `tests/helpers/send_message_fixtures.py`, `tests/test_job_queue_tools.py`, `tests/unit/tools/test_instance_tools.py` (+813/−99, +8 net tests)

## VERDICT: ❌ FAIL as merge gate — feature VERIFIED WORKING, but ONE branch-correlated test-hygiene regression (intermittent, test-isolation class)

- Feature behavior: ✅ fully verified (T1 + T2 below).
- Deterministic regressions: ✅ none (all sweep failures base-attributed).
- Blocker: ❌ `regression_unit_tools` pack fails 2/4 runs at HEAD (0/3 at base) — branch-introduced xdist test-isolation pollution (see §5). Fix is test-code-only; precedent: 2026-08-27 singleton-pollution blocker (fixed with a +7/−0 MagicMock filter).

## Scope Decision
Scoped gate, not full suite. Change set = ReviveGuard counter scope (manager + instance/job tools area); 3 production files, no cross-module architecture. Registered packs covering the touched surfaces + manager facade suites + targeted files. Release Gate not warranted.

## 1. T1 — Targeted suite at HEAD (`c7736f8d`)
`timeout 300 .venv/bin/pytest tests/unit/tools/test_instance_tools.py tests/test_job_queue_tools.py --tb=short -q`
**RESULT: PASS — 285 passed / 0 failed / 0 skipped / 0 deselected** (12.71s, exit 0).
Developer's "29/29" maps to the guard-subset; the FULL two-file suite is 285P/0F — strictly stronger. All 27 collected revive/guard/counter nodes enumerated PASS, incl.:
- matrix a `test_scope_a_completed_reuse_allowed_twice`, b `test_scope_b_completed_revive_leaves_counter_at_zero`, c `test_scope_c_error_then_terminal_again_second_revive_refused`, d `test_scope_failed_prior_consumes_unit_level`, e `test_scope_e_terminated_prior_not_consumed`, f `test_user_api_path_still_unaffected_after_scope_fix`
- `TestReviveOnceGuard` t1 (first revive granted), t2 (second refused), t3–t5, refusal-text/provenance doc tests
- accepted-edge: `test_accepted_edge_error_revoke_then_completed_still_refused`, **`test_accepted_edge_real_manager_counter_through_send_path`** (the new test)
- job_continue: `test_w1_t1_first_failed_continue_succeeds_and_increments` (+ t2 refusal, t3 not-counted)
Pre-existing noise recorded, no action: SyntaxWarning `\s` at tests/unit/tools/test_instance_tools.py:326; 32× SAWarning.

## 2. T2 — Independent regression proof at base `770da22a` (`996cd5b1`)
Scratch worktree, ONLY the 3 test files copied in (production at base). **10 failed / 275 passed** (15.05s). MUST-FAIL core CONFIRMED with exact predicted modes:
- **(a)** `test_scope_a_completed_reuse_allowed_twice` → `AssertionError: second COMPLETED-revive must be granted; got "Refused: Instance 'child-1' has already been revived once and failed again. Spawn a replacement instance instead."` (old guard refusal)
- **(b)** `test_scope_b_completed_revive_leaves_counter_at_zero` → `AssertionError: assert 1 == 0` on `get_agent_tool_revive_count("child-1")`
8 additional base failures — ALL branch-authored tests asserting the new `prior_status` API/call-shape (scope_e, scope_failed_prior `TypeError: ... unexpected keyword argument 'prior_status'`, t1/t2/t5, w1_t1, w1_scope_fix) — diff-verified: TestReviveOnceGuardScope (+458 lines) does not exist at base; prior_status occurrences 9→29 / 0→9.
Pre-fix passes confirmed LIMITED to tests not built to catch the bug (t3, t4 ×2, doc-tests ×2, w1_t2, w1_t3, accepted-edge pair, user-api/svc-layer non-regression guards — pass at base by design) + ~255 untouched passes.
Scratch removed (`worktree list` clean, dir gone). Feature worktree untouched at 1683cd40.

## 3. T3 — Bounded regression sweep at HEAD
| Pack | Worker | Result | Attribution |
|---|---|---|---|
| `job_queue_tools_unit_test` (registered) | `a79f72bb` | **PASS 81P/0F/0-des** (2.12s) | Baseline delta fully reconciled: un-quarantine `d663ec9a` (−4 des) + 1 new test. All 19 TestJobContinue* ran green incl. revive-consumption core. Note: stale quarantine comment in pack script lines 5–11 (doc-truth rot). |
| `regression_unit_tools` (registered) | `fd5ae94f` | **FLAKY-FAIL**: 2F/1,110P/1S (1st run) → see §5 | Branch-correlated test-isolation pollution — **the blocker** |
| manager facade ad-hoc (phase4 71 + work_id guard 4) + dev.sh static | `2b263c4c` | FAIL 1F/74P → **PRE-EXISTING-CONFIRMED** | `test_manager_pause_instance_cascade_delegates_to_lifecycle_service` — identical node+signature at base (runtime, scratch `770da22a`); kwarg `cascade_to_root=True` from ancestor `fdd2cd12`; zero `cascade_to_root` lines in branch diff. work_id facade guard 4P both sides. ensure.md static: dev.sh:102 `--timeout-graceful-shutdown 10` ✅ MATCH. |
| `tests/services/test_instance_messaging_queue_routing.py` (single-file) | `9f2e29bc` | FAIL 1F/15P at HEAD → **PRE-EXISTING-CONFIRMED** | `test_router_forwards_queue_id_to_enqueue_message_job` — `TypeError: object MagicMock can't be 'await' expression` @ `daemon/routers/messages.py:258`; IDENTICAL node/class/message/frame at base; test file byte-identical across range. |

## 4. Environment evidence
- `daemon.__file__` HEAD: `.../agents-ensemble-wt-revive-fix/daemon/__init__.py` (all workers); scratches: `...-scratch-base-regproof/`, `...-scratch-base-attr/`, `...-scratch-base-attr2/`, `...-scratch-base-phase4/` — each verified inside its own worktree.
- Rev-parse brackets: EVERY invocation bracketed; zero drift anywhere (feature worktree stable at `feature/fix-revive-guard-scope @ 1683cd40`; scratches detached at `770da22a`).
- Scratch cleanup: all 4 scratches removed + confirmed gone; feature worktree intact; main repo never touched; port 8088 never touched.
- Trap discovered: `daemon.__file__` check false-positives if run from the main-repo cwd (`sys.path[0]=''` wins over the editable `.pth`) — always run from the target worktree cwd (see LESSONS).

## 5. Blocker analysis — TestDocsDefaultDeny xdist pollution (branch-correlated)
Nodes: `tests/unit/tools/test_upgrade_registration.py::TestDocsDefaultDeny::{test_allow_agent_docs_include_category, test_default_documented_tools_excludes_privileged}`
Signatures: (1) `AssertionError: assert {'release_info','system_restart','upgrade_status'} <= {'system_upgrade'}`; (2) `KeyError: 'system_upgrade'`.
Context matrix:
| Context | HEAD 1683cd40 | base 770da22a |
|---|---|---|
| solo (file only) | PASS (21P) | PASS (21P) |
| two-file combo (+test_instance_tools.py) | PASS (225P) | — |
| full pack (`-n auto`) | **2F/4 runs** (2P/2F, identical node set) | **0F/3 runs** (1,103P/1S each) |
Post-fail solo re-verify: 21P (transience confirmed). Victim reads process-global `daemon.tools._tool_registry.list_tools_by_category()` with no isolation; failure = registry left partially booted/reset by a same-worker predecessor. Suspect pool: branch's +528 lines in `tests/unit/tools/test_instance_tools.py`. Small-n caveat (2/4 vs 0/3) acknowledged; mechanism + clean base make branch-correlation the operative conclusion.
2026-09-03 baseline "1F upgrade_registration" did NOT reproduce at base today (3/3 clean) — stale baseline, re-registered.

## 6. ensure.md (scoped, blast radius)
- Critical #1 no regressions in changed packs: ❌ FAIL (regression_unit_tools flaky-fail, branch-correlated)
- Critical #4 dev.sh `--timeout-graceful-shutdown 10`: ✅ MATCH (dev.sh:102)
- Critical #2/#3 (concurrency pack), Important #1/#2, Nice-to-have: out of scope (no lock/cascade/async-conversion surface in diff)

## 7. Action needed (path to green)
1. Polluter fix (test-code only): isolate/restore global tool-registry state in the new guard tests (setup/teardown or fixture), or mock-filter per 2026-08-27 precedent (+7/−0 class fix).
2. Bisect protocol if needed: full pack `-n 1` at HEAD (forces deterministic adjacency) or xdist `--dist loadgroup`.
3. Re-gate criteria: 4× clean `regression_unit_tools` full-pack runs at fixed HEAD + T1 matrix still green (other evidence stands).
4. Hygiene follow-ups (non-blocking): stale quarantine comment in job_queue_tools pack script; QUARANTINE WATCH row added; phase4 + queue_routing pre-existing failures registered for visibility.

## Workers
`c7736f8d` (targeted), `996cd5b1` (regproof), `fd5ae94f` (tools sweep), `a79f72bb` (jqt pack), `2b263c4c` (manager + base confirm ×2 tasks), `9f2e29bc` (routing attribution), `4a62cea4` (upgrade attribution + retry budget ×2 tasks). 7 instances, 9 dispatches, 0 direct executions by tester.

**Gaps**: none — all nodes reported with evidence. Minor: HEAD retry-run-2 assertion text truncated by worker capture (node set + counts verbatim; signatures carried from sweep run, identical node set).

---

# ADDENDUM — Round 3 re-verify gate @ `1d166d54` (2026-09-05)

**Commit under test**: `1d166d54` (parent `bb5327ce`; 7 files +160/−129, test-code/comments only; dev AST-equality on the 3 daemon files after string normalization — not independently re-derived, but T1 behavioral parity + 4/4 pack green corroborate no production drift).
**VERDICT: ✅ PASS — MERGE-READY.** The R1 re-gate bar is met in full.

## A. Re-gate — 4/4 CLEAN (worker `ae343e2a`)
`regression_unit_tools` full pack, registered script unmodified, dual-layer timeout, every run bracketed (feature/fix-revive-guard-scope @ 1d166d54, zero drift):
| Run | Result | Counts | pytest wall |
|---|---|---|---|
| 1 | PASS | 1,112P/1S/0F, 5 des | 18.61s |
| 2 | PASS | 1,112P/1S/0F, 5 des | 11.49s |
| 3 | PASS | 1,112P/1S/0F, 5 des | 10.93s |
| 4 | PASS | 1,112P/1S/0F, 5 des | 11.30s |
Prior TestDocsDefaultDeny pollution did not reproduce in any run. (Dev's 10× claim not trusted; 4× independently re-derived — meets the defined bar.)

## B. T1 intact — 285P/0F exact (worker `ae343e2a`)
Split confirms dev claim precisely: test_instance_tools.py 204 + test_job_queue_tools.py 81 = 285, 0 failed. Full guard matrix present + PASS: scope a–f (d = `test_scope_failed_prior_consumes_unit_level`, docstring "Matrix (d)"), accepted-edge ×2, `TestReviveOnceGuard` t1–t5 (+2 doc tests), job_continue w1 trio + scope-fix node.

## C. Victim-fix spot-check — all four properties CONFIRMED (worker `588d96a1`)
Fixture `_ensure_upgrade_category_populated` (tests/unit/tools/test_upgrade_registration.py:316-354):
- (i) `autouse=True` (line 320) ✓ (ii) class-scoped to TestDocsDefaultDeny only ✓
- (iii) NON-VACUOUS: conditional `_build_instance_tools(...)` + hard `assert UPGRADE_CATEGORY in list_tools_by_category()` before deny assertions (lines 352-357); inclusion assertion `UPGRADE_TOOL_NAMES <= allowed` (line 393) and deny assertion (line 402) run against a populated registry ✓
- (iv) Idempotent: conditional-skip on repeat (lines 352-353); no-teardown is documented-by-design — entries byte-identical to production boot writes, post-fixture world == booted-daemon world ✓
Behavioral: solo file 2× → 21P/21P, zero flakes.
**Lazy-registration mechanism validated**: direct runtime probe in HEAD venv — `import daemon.tools._tool_registry` → categories `['language']` only, `system_upgrade` absent; static: `_tool_registry.py:18` (empty at import), `:395` (`scan_tools_for_full_docs`), `instance.py:4475` (factory-time invocation). Dev's victim-latent-isolation rationale confirmed; fix-location deviation (victim-side, not guard-test-side) **adjudicated ACCEPT**.

## D. Correlation probe @ `bb5327ce` (worker `588d96a1`)
4× full pack at the test-identical old commit: **4/4 PASS** (1,112P/1S each). Flake did not re-manifest — honest caveat: single 4-run probe, does not refute the original 2/4 observation (xdist scheduling nondeterminism); the mechanism + fix stand regardless. Combined fresh evidence post-fix-window: 8 consecutive clean pack runs today (4 @ 1d166d54 with fix + 4 @ bb5327ce without manifestation).

## E. Environment
daemon.__file__ inside feature worktree (no re-sync needed — commit is test-code only); scratch `...-wt-scratch-bb5327ce` created/verified/removed (worktree list + dir gone); all invocations bracketed, zero drift; main repo untouched (its `.agents/approver` mods are another chain's, pre-existing); port 8088 untouched; worktree clean post-run.

## F. Final ledger
- R1 blocker: RESOLVED (QUARANTINE.md row → RESOLVED with fix + confirming runs; PACKS.md row un-FLAKY'd).
- Verdict flip justification: R1 defined bar = "4× clean full-pack runs at fixed HEAD + T1 matrix intact" → 4/4 + 285/0 matrix-complete + mechanism-validated fix + TestDocsDefaultDeny well beyond 3× clean (2 solo + 4 pack).
- **Overall: ✅ PASS — merge-ready at `1d166d54`** (docs commit to follow).

