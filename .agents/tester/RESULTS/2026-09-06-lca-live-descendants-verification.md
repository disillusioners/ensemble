# VERIFICATION GATE — LCA live-descendants fix @ `8b083522`

Date: 2026-09-06 · Tester lane · Verdict: ✅ **PASS** (all 6 jobs green; 0 repo code changes, 0 commits — verification-only per mission order)

Commit under test: `8b083522` (`fix(lca): gate counts live descendants — kills waiting_children false-deny (809e2a59 class)`) on `feature/leader-completion-attestation`, base `e697b953` confirmed ancestor. Isolated worktree `/Users/nguyenminhkha/All/Code/opensource-projects/ens-lca-desc` (clean; main worktree on `fix/defer-self-witness-and-cleanup` @ `2f79417b` untouched). Single-commit diff = 1 new test file (827 lines); production change = `daemon/manager.py` (`count_live_descendants` facade) + `daemon/services/attestation_gate.py` (4-input deny AND, 16-field log schema).

Workers: recon `dd3d3da1` · matrix `9539f0d3` (test-pack-execution) · e2e `d405c2d7` (integration-test) · neighborhood `73fcd511` (test-pack-execution) · pg `2c566441` (test-pack-execution) · smoke `dad50f79` (infra). 6 workers, 0 drift incidents (rev-parse gates pre/post on every invocation).

## The bug is proven dead (incident-class evidence)

Original defect: leader whose child sat in `waiting_children` (watcher FIRED per-turn → no pending wakeup row) with a RUNNING grandchild was FALSE-DENIED — incident `809e2a59` escalated to FALSE `terminal_after_bound` at 09:30:19 while child `a135fb55` lived. Independently-constructed scenario (a) rebuilt EXACTLY this tree (real repo rows, real FIRED watcher, real BFS through the production facade):

| Scenario | Setup | Decision | Nudge | Counter | live_desc |
|---|---|---|---|---|---|
| (a) 809e2a59 class | C=WAITING_CHILDREN (real FIRED watcher, pending=0) + G=RUNNING; counter pre=2, flag=True | `allowed_legitimate_pending_wakeup` | **NO** | **2→2 unchanged**, flag preserved | **2** (real BFS — C and G both live) |
| (b) original protection | C+G both COMPLETED, nothing pending | `denied` ×3 | **YES** (3, checkpointed) | 0→3, chain [1,2,3] | 0 |
| (c) terminal-set edge | descendants ERROR + FAILED | `denied` ×3 | **YES** | 0→3 | 0 (ERROR/FAILED NOT live) |
| (d) counter/escalation | 6-phase live drive | deny×3 → `terminal_after_bound` event; atomic op end-state flag=True+count=0; D2 R2-allow at count=3 boundary → counter stays 3, flag stays True (**escalation prevented**); D5 bare denies leave escalated flag True | D1: 3 | see matrix | 1 (D2) |

Deterministic across 3 runs (1.27s/1.61s/1.59s). Atomicity = `reset_attestation_ledger_with_escalation` (repo.py:1331-1355).

## Job results

| # | Job | Result | Counts | Runtime |
|---|-----|--------|--------|---------|
| 1 | Attestation matrix (34-file glob incl. tests/migration/) | ✅ PASS | **340/340** (ground truth: 317 existing + 23 new; exact) | 7.78s |
| 2 | Incident-class E2E independent (a)–(d) + dev-test audit | ✅ PASS | audit clean + 4/4 scenarios | ~2s×3 runs |
| 3 | Do-not-touch neighborhood (dependency_bus + child_reports) | ✅ PASS | **139/139** exact per-file; all 8 `TestDeferFires*`/defer-bus nodes individually green | 2.86s |
| 4 | Real-PG execution (revive + live-descendants + dependency_bus) | ✅ PASS | **32/32** (2 revive EXECUTED-not-skipped + 24 PG-shadow incl. dialect canary + 6 `-m postgres`) | 11s |
| 5 | Boot smoke (disposable PG, natural leader turn-end) | ✅ PASS full variant | 16/16 fields ×2 gate rows; `live_descendants=` populated | 236s wall |

## Job 2 — audit findings (assertions match reality; 3 seam findings, NON-blocking)

Deny predicate confirmed 4-input AND at `attestation_gate.py:397-421` (R2-allow if ANY of pending_children/wakeups/live_descendants > 0); reason codes exact (`allowed_legitimate_pending_wakeup`, gate.py:141); counter/escalation semantics consistent (R2-allow non-reset, attested-precedence reset-to-0). 23 nodes confirmed real-seam (real repo ledger graph.py:7135-7163; `TestFacadeBfsCap` binds the real facade via MethodType).

Findings (quality observations for follow-up, NOT defects):
- 🟠 **MEDIUM — shadow facade**: `tests/support/conftest.py:155-176` reimplements `count_live_descendants` (own terminal-set literal + own BFS) instead of delegating to `InstanceManager.count_live_descendants`; graph-level tests exercise the copy. Semantics identical today (manager.py:8601-8655) but can drift silently (cap/terminal-set change). Recommend follow-up: route the helper through the real facade.
- 🟢 Minor: `test_no_repo_returns_zero` (test:548-564) is self-referential (asserts its own inline reimplementation; production never executes).
- 🟢 Minor gap: dev's flagship test passes `live_descendants=1` as a fixed override — the exact 809e2a59 tree is never built there; our scenario (a) closes this gap (real BFS → 2).

Adjudications: (1) no distinct live-descendants allow reason exists — `allowed_legitimate_pending_wakeup` covers it, accepted per brief fallback criterion; (2) actual live value in (a) is **2** (the WAITING_CHILDREN child itself is live — terminal set excludes only COMPLETED/TERMINATED/ERROR/FAILED); brief's "=1" was imprecision; (3) escalation flag is set by the atomic op on the deny-eval FOLLOWING the 3rd increment (4th un-attested zero-input eval, `3+1 > 3`) — matches the dev suite's own comment; observed live.

## Job 4 — PG execution detail

- **Canary-proven shadow**: `/tmp/lca-pg-8b083522/test_live_descendants_pg.py` imports the 23 test nodes verbatim and shadows `file_sqlite_engine` at module level (module scope beats conftest); canary asserted `engine.dialect.name == 'postgresql'`. 24/24 PASS incl. canary. Disposable DB `ensemble_blob_prune_53d239fb8af2` created + FORCE-dropped; zero `ensemble_blob_prune_%` leftovers.
- **Revive harness**: 2/2 EXECUTED (not skipped) on real PG 14.22 — the `6ab16261` boolean-default hotfix surface remains intact behind the new commit.
- **dependency_bus PG**: 6/6 (`-m postgres --override-ini="addopts="`; default addopts hides them — collect gate honored).
- Env finding: pre-existing local gap — `ensemble_test.public` schema lacked CREATE for role `ensemble` (`InvalidSchemaName`); one-time operator `GRANT CREATE ON SCHEMA public TO ensemble` applied; NOT a code defect.

## Job 5 — boot smoke detail

Disposable DB `ensemble_lca_smoke_180ed879f1a2` (prod/dev untouched; engine line verbatim confirmed). Boot→healthy ≈16s; `/livez` v0.12.1; attestation boot line `deny_bound=3` ✓; default mode resolved **enforce** (consistent with `d6bd7e31`). Natural leader turn (agent `leader`, instance `2d547890`): reply → gate `denied` → nudge injected → leader self-attested via `attest_completion` → gate `allowed` → COMPLETED in 74s. **Both gate rows verbatim, all 16 canonical fields programmatically checked**, `live_descendants=0` both rows (correct — leader had no children), plus 2 additive extras (`next_denied_count`, `should_inject_nudge`) beyond the canonical 16 — informational, not a contract break. Clean own-PID-tree shutdown; port 8079 re-free; 8088 untouched. Benign: MCP `plane` connection ERRORs at boot (optional integration absent in dev boot).

## Scope Decision

Scoped verification per mission (6 jobs on the fix commit), NOT the 15.6k full suite: single-commit fix (+1 test file; 2 production files) on a lineage already whole-repo-gated at `6ab16261`/`42cb9518`. Coverage of the seams: matrix (fix surface), neighborhood (do-not-touch contract), PG lane (typing class that hid the last defect), live boot (integration truth). Full suite not warranted.

## Operational notes (see LESSONS)

1. Shell env carries `POSTGRES_DB=ensemble_prod` (LIVE PROD) — scrub the **full `POSTGRES_*` family** (`factory.py` reads parts individually); `POSTGRES_DB` alone is insufficient isolation. All 6 workers neutralized; prod untouched (verified by engine-line grep + catalog queries).
2. Worktree venv editable-install is **cwd-sensitive**: without `cd $WT`, `sys.path[0]=''` resolves the MAIN tree's `daemon/` first — silent wrong-tree testing. Enforced on every invocation.
3. Dev claim drift: "+23 new tests" → actual matrix delta **+26** (23 new file + 3 additions in existing files between `42cb9518`..`8b083522`); matrix total exactly 340 as claimed.
4. PG-shadow per-test cleanup must use raw asyncpg TRUNCATE (NullPool SQLAlchemy silently retained rows in diagnostics).

## Artifacts & hygiene

- Pack wrappers (untracked, disposable worktree, NOT committed): `$WT/test/packs/lca_matrix_8b083522_test.sh`, `lca_dontouch_neighborhood_test.sh`, `lca_pg_execution_test.sh`.
- Scratch: `/tmp/lca-e2e-8b083522/`, `/tmp/lca-pg-8b083522/`, `/tmp/lca_matrix_8b083522.log`, `/tmp/lca_dontouch_pack.log`, `/tmp/lca-smoke-boot.log`.
- `git status` in $WT: only untracked pack wrappers + dev.sh runtime byproducts (`data_dev/`); zero tracked-file changes; zero commits.

## Overall Status — ✅ READY

Matrix 340/340 · E2E 4/4 + audit clean · neighborhood 139/139 (un-wedge intact) · PG 32/32 · smoke full-variant PASS. **Verdict: PASS** — the live-descendants third input kills the 809e2a59 false-deny class while leaving the original protection (b), terminal-set semantics (c), and counter/escalation atomicity (d) intact.
