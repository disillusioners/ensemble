# FINAL E2E VERIFICATION — Stale-Messages-on-Revive Fix (context-message identity)

- **Date:** 2026-09-05 (run window ~07:40–08:16 UTC)
- **Branch:** `feature/fix-context-message-identity` @ `00e2a814` (commits `9674b95b` + `33d65e5a` + `00e2a814`; base `5d7a0695`)
- **Supersedes/completes:** the premature verification attempt whose only surviving artifacts were `RESULTS/2026-09-05-completion-regression-verification.log` (pack PASS at HEAD, re-confirmed below) and the PRE-fix diagnosis set `/tmp/stale-repro/` + `RESULTS/2026-09-05-stale-messages-revive-repro.md` (recipe reused for criterion E). The premature tester's claimed "criterion B PASS" and "D-202 A/B evidence" had **no on-disk post-fix artifacts** — both were **re-verified fresh** here.
- **Mode:** verification-only. ZERO code modifications (main worktree byte-identical before/after every dispatched job; two disposable base-attribution worktrees created and force-removed). Dev env only: BE :8079, FE :4199. Prod 9797 + 8088 untouched.
- **Workers:** 20 (IDs in §Worker Ledger)

## FINAL VERDICT: ✅ **FIX VERIFIED** — all six acceptance criteria PASS; zero branch-caused regressions; three pre-existing defect clusters disclosed with worktree A/B evidence (none block this branch).

---

## Environment Proof

- **Dev daemon provably on HEAD `00e2a814`:** booted 13:41:16 +0700 (`lsof` PID 4368/4370, `uvicorn daemon.api:app --port 8079`), AFTER last commit 13:31:30, with zero daemon-code dirt in the tree; `/livez` → `{"status":"alive","version":"0.12.0"}`. Criterion A's "restarted on branch code" precondition satisfied by this boot (evidence: repo `data/logs/ensemble.log:15976` `Creating PostgreSQL engine: localhost:5432/ensemble_dev` at 13:41:19).
- **FE restarted for guaranteed post-fix bundle:** old `ng serve` PID 86290 → new PID 16278 (started 14:54:55), verified-safe kill (port 4199 + command check only; 8079/8088/9797 never touched).
- **Known allowlist honored:** fresh-SQLite migration TRAP (`test_progressive_dispatch.py`) not re-run (disclosed allowlist); the ~19 disclosed proactive-compaction/dispatch-area failures sit in partitions not in this gate's pack set, and the two partitions that WERE run (`loose_s_z`, `loose_a_d`) were adjudicated node-for-node (below).

---

## Criterion A — Variant B closed (dispatch-time SSE id == GET row id) — ✅ PASS

Fresh project `stale-fix-verify-E1` (`f0a50cbf`, blueprint_active=true, blueprints initialized). Two independent first-context instances, same query.

**T1** `24931842-be68-4978-ae01-c5ae9353436f` (artifact `/tmp/fixverify-a/E1_T1_id_compare.json`):

| row | context_kind/role | SSE id | GET id | MATCH |
|---|---|---|---|---|
| injected | project / user | `6a46f1e1-dc59-4cb4-90b0-d1855ab09393` | `6a46f1e1-dc59-4cb4-90b0-d1855ab09393` | ✓ |
| user query | — / user | `45fd3415-536f-4faa-bfab-6a21d1af2a66` | `45fd3415-536f-4faa-bfab-6a21d1af2a66` | ✓ |

**T2** `784bc8c4-1c8e-4360-932f-31488bbfda0f` (artifact `A2_T2_id_compare.json`):

| row | context_kind/role | SSE id | GET id | MATCH |
|---|---|---|---|---|
| injected | project / user | `64ae5d2f-71e2-4b46-b241-07685e078893` | `64ae5d2f-71e2-4b46-b241-07685e078893` | ✓ |
| user query | — / user | `e438352f-e745-4e9b-921e-04ff86e9f409` | `e438352f-e745-4e9b-921e-04ff86e9f409` | ✓ |

Cross-check: per-instance fresh ids (T1≠T2 on both rows) — no stale reuse. Pre-fix contrast (from diagnosis): same flow produced SSE `49a48c71…` vs GET `15105d45…` and SSE `6a7f72c7…` vs GET `090f513c…` — **the mismatch class is gone**.

## Criterion B — Incident scenario closed — ✅ PASS (re-verified fresh; prior tester's PASS had no surviving evidence)

Instance `a1aaaf59` (project `91178018`): turn 1 blueprint-matching query → driven to `completed` → revive send.

| Assertion | Result | Evidence |
|---|---|---|
| No NEW injections on revive | ✅ | injected rows pre=1, post=1 |
| Pre-block intact, new at END | ✅ | `post[0:25] == pre[0:25]` (signature-identical); rows 25-26 = revive user msg + assistant reply |
| Reload-equivalent stability | ✅ | GET_post == GET_reload (27==27 rows, identical signatures); **all 25 pre-block message_ids identical across pre/post/reload — zero re-mints** |
| SSE revive stream | ✅ | 1 `user_message` event (the revive msg); **0** events with `injected_message:true` (no old-context re-emit) |

**Browser-level (Playwright headless Chromium, post-restart FE):** pre-reload 19 bubbles → post-reload 19 bubbles, **ORDER MATCH: true** (index 0 = preserved turn-1 SYSTEM CONTEXT; 17/18 = revive user/assistant). Screenshots `/tmp/fixverify-b/fe_B1_{pre,post}_reload.png`. No stale block above the new message; nothing vanished.

## Criterion C — Legacy rows protected — ✅ PASS (with 1 informational residual)

Named fixtures `d88c5917/4e0e2bb6/07b27d8b/6a7f72c7` were **404-gone** (dev DB churn); surviving pre-fix instances used instead (all status=completed, project `fb992ebd` stale-repro-P2, created pre-fix):

| Instance | rows | injected | GET×2 order | stamp moves | id re-mints |
|---|---|---|---|---|---|
| `e78d0a39` | 26 | 3 | ✅ stable | 0 | 0 |
| `fbccf21d` | 24 | 2 | ✅ stable | 0 | 0 |
| `af2f39dc` | 41 | 2 | ✅ stable | 0 | 0 |

**Legacy revive (`e78d0a39`, "Reply with the single word: pong"):** injected 3→3; pre-block (26 rows) intact at `post[0:26]`; new block = exactly 2 rows at END; SSE 0 injected re-emits; assistant "pong". No legacy time-travel despite pre-fix rows.

**Informational residual (accepted class, not a fail):** legacy row `e78d0a39:[2]` (`7935d2f7`, blueprint block) had `created_at` bumped `06:51:11 → 08:12:05` (revive time) — id and content unchanged. Same family as the disclosed "legacy pre-fix rows re-mint/stamp" residual; harmless now because ordering is server-array-derived and FE no longer re-sorts by `created_at`.

## Criterion D — Quick-display regression — ✅ PASS

- **Jest (targeted, 192/192):** `mergeMessagesById — merge order (stale-message fix, 2026-09-05)` 4/4 incl. *preserves server array order through a post-send refetch — an old row re-stamped LATER than the new message must NOT jump above it*; `user_message dedup` 3/3 incl. *preserves ARRAY/ARRIVAL order regardless of created_at (no re-sort)*; optimistic/pending set green (*provisional → SSE echo → refetch keeps ONE confirmed bubble in its send position*; *pending stays cleared after both arrival orders*; *NOT evict aged pending on optimistic-append MIN-5*). Logs `/tmp/fva-fejest-{targeted,full}.log`.
- **Browser live:** send via UI → optimistic bubble appears at index 19 (END, not above block 0) → settles at 21 bubbles with reply "pong"; message text appears **exactly once** (no duplication). Screenshots `fe_D_{optimistic,settled}.png`.
- **D-202 disposition (honest salvage finding):** the prior tester's D-202 content and A/B evidence **did not survive** — all `/tmp/stale-repro` captures are pre-fix raw evidence (10:44–11:26, before the 12:07+ fix commits) and no `D-202` string exists anywhere in `.agents/tester/` or `/tmp`. Fresh re-derivation of the quick-display surface found **zero defects** (jest + live clean), so nothing required pre-existing attribution; the prior attribution claim is neither contradicted nor confirmable — documented rather than fabricated.

## Criterion E — Repro recipe re-run post-restart — ✅ PASS

Exact recipe from `RESULTS/2026-09-05-stale-messages-revive-repro.md` executed against the restarted daemon (fresh project/instance): SSE `user_message` ids == GET row ids for BOTH the injected project-context block and the user query (table under criterion A/T1); ordering invariants hold (synthetic-system → injected → user → assistant); GET #1 vs GET #2 message_id sequence **equal (19==19 rows)** — no post-send reordering. **Negative control:** second non-matching turn on T1 → `context_kind`-row delta = **0** (the +1 `injected_message:true` row is the user's own delivery flag, `context_kind=None`) — once-per-instance gate holds.

## Criterion F — Regression sweep @ 00e2a814 — ✅ PASS (0 branch-caused failures)

### Touched-area packs (messaging / persistence / context / graph / watchover / injection / FE)

| Pack | Result | Counts | Runtime |
|---|---|---|---|
| `context_messages_unit_test` (primary fix file) | PASS | 71P / 1 pre-existing skip | 1.0s |
| `context_injection_unit_test` | PASS | 90/90 | 1.0s |
| `c2_messaging_lifecycle_unit_test` | PASS | 62P/14S (baseline-exact) | 7.1s |
| `instance_messaging_regression_test` | PASS | 28/28 | 0.9s |
| `wc_wake_d1_w5_pairing_unit_test` (incl. branch-NEW W5 file, 14 tests) | PASS | 57/57 | 1.1s |
| AD-HOC `identity_fix_injection_graph` (/tmp-resident; `tests/test_injection_graph.py` +329 branch lines are in NO registered pack) | PASS | 42/42 | 0.8s |
| `compaction_unit_test` | PASS | 325/325 | 9.5s |
| `completion_regression` (prior tester's surviving artifact, re-confirmed at HEAD) | PASS | 96P/37S/1D | 2.6s |
| `core_unit_test` (persistence: `test_persistence.py` **all PASS** incl. +29 branch lines) | FAIL 4 | 715P/30S/4F | 19.6s |
| `api_unit_test` | FAIL 2 | 211P/8S/2F | 13.0s |
| FE jest FULL | PASS | 69 suites / 2434 / 0F | 7.7s |
| FE jest targeted (merge-order/dedup/optimistic) | PASS | 192/192 | 2.2s |
| FE `fe_static_typecheck_build` (EXPECTED_BRANCH override) | PASS | tsc 0, build 0 (5.83 MB/1.25 MB); SCSS 10 = documented baseline | ~30s |

### Partitions (adjudicated)

| Partition | Observed | Baseline | Adjudication |
|---|---|---|---|
| `regression_unit_loose_s_z` | 1,044 → 973P/58F/2E/11S | ledger 1,036 → 971P/52F/2E (4-gate stable) | **0 branch-caused NEW.** Watchover 47-family + 5 singles + webfetch 2E exact. The 6-node `test_task_reconciliation` family (the "loose-s_z partition adjudication" left outstanding) — **sealed PRE-EXISTING at base**: full partition at `5d7a0695` in isolated worktree → **58F == 58F node-for-node fingerprint**, same 6 nodes, same `TypeError: Logger._log() got an unexpected keyword argument 'work_id'/'count'`. Solo runs pass BOTH sides (13/13) → family is environmentally gated (xdist sibling arms `daemon.*` logger ≤ INFO, defeating the `isEnabledFor` short-circuit). HEAD delta vs base = exactly **+1 passing test** (branch's `test_utils.py::test_serialize_message_mints_id_once_for_id_less_message` ✓). +7 collected drift = latest-side, sealed not-branch-owned. Logs `/tmp/fvb-loosesz.log`, `/tmp/ab-sz-{base,base-solo,head-solo}.log` |
| `regression_unit_loose_a_d` | 1,052 → 1019P/10F/21E/2S | ledger 1,050 → 1,017P/10F/21E | **0 NEW** — `api_module_is_small` + coder/devops misc ×9 + slash/blueprint fixture-drift ×21E, exact family match; branch-owned `test_context_messages.py` all PASS in-partition; +2/+2 collected/passed reconciled |

### All non-green nodes attributed (worktree A/B at base `5d7a0695`, main worktree untouched)

| Node(s) | Base result | Verdict |
|---|---|---|
| `test_api.py::test_send_message_success`, `::test_global_exception_handler` (`TypeError: object Mock can't be used in 'await' expression` @ `daemon/routers/messages.py:258`) | FAIL, byte-identical signature | **PRE-EXISTING** (test mock not migrated to AsyncMock) |
| `test_agents_api.py` ×2 (34 agents vs hardcoded 1) | FAIL identical | **PRE-EXISTING** |
| `test_migration_api_comprehensive.py::TestIntegration::test_manager_tests_pass` (skip-vs-pass) | FAIL identical | **PRE-EXISTING** |
| `test_models.py::TestErrorCodes::test_error_codes_values` (19 vs 18) | FAIL identical | **PRE-EXISTING** |
| `test_task_reconciliation.py` ×6 (see partition row above) | FAIL identical (partition context) | **PRE-EXISTING** (latent prod defect — see Findings) |

---

## Findings (reported, NOT fixed — per mandate)

1. 🔴-upstream 🟢-this-branch **PRODUCTION HAZARD (pre-existing since 2026-08-11):** `daemon/repositories/task/repository.py:2975` (`reconcile_terminal_task`, `work_id=`) and `:3104` (`batch_reconcile_bad_state_tasks`, `count=`) pass structlog-style kwargs to a **stdlib** logger → `TypeError` whenever that logger's effective level ≤ INFO (any prod INFO-logging config crashes the Pattern-f terminal-task reconciliation path). Sealed pre-existing at base (partition A/B + blame `114d1cc5`/`1595568c`). Durable fix direction: `logger.info(..., extra={...})`. Tracked in LESSONS + QUARANTINE (sweep-visible family).
2. 🟢 Test-debt cluster (pre-existing, sealed): `test_api` AsyncMock drift ×2; `test_agents_api` hardcoded-count ×2; `test_migration_api_comprehensive` skip-vs-pass ×1; `test_models` error-codes drift ×1. None touch branch scope.
3. 🟢 Accepted residual confirmed empirically: legacy pre-fix rows can have `created_at` refreshed on revive (id/content unchanged; order server-derived). FE no longer re-sorts by `created_at`, so no user-visible time-travel (proven by criterion C + jest merge-order specs).
4. 🟢 Browser console noise unrelated to fix: CSP iframe block (`plane.ensem.dev`), SSE reconnect notices.
5. 🟢 Registered-pack gap: `tests/test_injection_graph.py` (this branch's heaviest test additions, +329 lines) is in NO registered pack — covered this run by /tmp ad-hoc pack. Recommend registering `identity_fix_injection_graph` (repo-side) as follow-up.

## Gaps

- D-202 original content unrecoverable (see Criterion D disposition) — fresh re-derivation was clean.
- Criterion C used `e78d0a39/fbccf21d/af2f39dc` instead of the named 404-gone fixtures (equivalent: pre-fix completed instances with injected rows in a blueprint_active project).
- Fresh-SQLite migration TRAP (`test_progressive_dispatch.py`) and the ~19 disclosed proactive-compaction/dispatch-area failures were NOT re-run (allowlist; their partitions were not in this gate's scope except as adjudicated above).
- Full 12-partition mega-sweep not re-run (prior gates' 09-03/09-04 ledgers + this gate's touched-area + 2 partitions cover the change set; blast radius = 17-file diff, all areas exercised).

## Worker Ledger (20)

| Worker | ID | Scope |
|---|---|---|
| env-recon-salvage | d90ee7ca | git/daemon/FE state, /tmp salvage, legacy DB |
| f-test-discovery | 2f0d73f4 | change-set → pack map |
| live-A-E | 40fbd3ae | criteria A + E + negative control |
| live-B-C | 40683709 | criteria B + C + browser B/D + FE restart |
| pack-c2 / pack-im-regression | 8d3f75da / 00ef6d0c | messaging packs |
| pack-context-messages / pack-context-injection | a594a269 / f2993aa0 | context packs |
| pack-wcwake-w5 / pack-adhoc-injection-graph | 443405a9 / 1a2fd8b8 | injection/W5/graph-tap packs |
| pack-api-unit / pack-core-persistence / pack-compaction | 88a5614b / aec7137a / 824c2254 | api/core/compaction packs |
| pack-loose-sz-adjudication / pack-loose-ad | 248ecfd7 / 328e68f2 | partition adjudications |
| fe-jest-sweep / fe-tsc-build | 3f694516 / afcaf364 | FE jest + tsc/build |
| api-fail-attribution-ab / core4-attribution-ab / sz-reconcile-attribution | ae60275a / be30d574 / b72fbcdf | base worktree A/B seals |

## Documentation Updated
- [x] RESULTS/2026-09-05-fix-e2e-final-verification.md (this file)
- [x] PACKS.md — gate summary entry + last-run refresh
- [x] QUARANTINE.md — test_task_reconciliation family row (context-gated, base-evidenced)
- [x] LESSONS/2026-09-05-stdlib-logger-kwargs-latent-crash.md — production hazard + adjudication method
- [ ] MOCK_TESTS.md — n/a (no mock packs)

## Overall Status
- Criterion A ✅ · B ✅ · C ✅ (1 informational residual) · D ✅ · E ✅ · F ✅ (0 branch-caused)
- **Testing Complete: ✅ READY — FIX VERIFIED**

---

# INTEGRATION GATE ADDENDUM — merge commit `b76d6a74` (2026-09-05, later same day)

**Context:** base under the fix moved (`latest` → `e14f09f9`, ctx-limit 180k→700k); branch integrated as merge `b76d6a74` (parents `00e2a814` + `e14f09f9`). File sets disjoint → SMOKE gate on the integrated result; the six E2E criteria above were NOT re-run (verdict stands). Verification-only, zero repo writes.

## VERDICT: ✅ **INTEGRATION VERIFIED — green-light the merge to `latest`.** Zero blockers; one disclosure (report-not-block per gate rule).

### T1 — Identity packs @ b76d6a74 (all exact-baseline)

| Suite | Result | vs @00e2a814 |
|---|---|---|
| ad-hoc injection_graph (+ message_tap_slot) | 42/42 PASS | exact |
| `context_messages_unit_test` | 71P/1S PASS | exact |
| `tests/test_persistence.py` (ad-hoc solo) | 24/24 PASS | exact |
| `wc_wake_d1_w5_pairing` (incl. W5 14) | 57/57 PASS | exact |
| `test_utils` + `test_serialize_message` (ad-hoc) | 58/58 PASS — incl. branch-owned `test_serialize_message_mints_id_once_for_id_less_message` ✓ (re-confirmed solo) | exact |
| `context_injection_unit_test` | 90/90 PASS | exact |
| `c2_messaging_lifecycle` | 62P/14S PASS | baseline-exact |
| `instance_messaging_regression` | 28/28 PASS | exact |
| `regression_unit_loose_s_z` (watchover+utils partition) | 1,044 → 973P/**58F**/2E/11S | **node-for-node EXACT vs sealed fingerprint** (watchover 47 + reconcile 6 + singles 5 + webfetch 2E); zero new deltas; ctx-limit merge added no s_z tests |

### T2 — Compaction-adjacent

| Suite | Result | Disposition |
|---|---|---|
| `compaction_unit_test` (325) | **325/325 PASS** | A/B not triggered — clean |
| `tests/test_injection_compaction.py` | 6P/**1F** — `TestProactiveCompactionPreservesInjection::test_all_injected_messages_skips_compaction` expects `CompactionResult is None` but the `compaction.py:2009` anti-refire skip returns a result (`skipping: every message carries injected_message flag … anti-refire stamp engaged`) | **A/B-sealed IDENTICAL at `e14f09f9`** (1F/6P, same node, same assertion, same WARNING; disposable worktree, removed) → **ctx-limit/latest-side, NOT ours — REPORT, not blocker**. Belongs to the proactive-compaction soak family ("skipped_injections_dominate"); recommend re-baseline/quarantine on `latest` — and note `9674b95b` (this branch) is the fix candidate under review for that live behavior. Logs `/tmp/ig-injcomp.log`, `/tmp/ig-ab-injc.log` |

### T3 — FE
- Jest targeted: **192/192 PASS** — `mergeMessagesById — merge order (stale-message fix, 2026-09-05)` 4/4; `user_message dedup` 3/3 (incl. *preserves ARRIVAL order regardless of created_at*); optimistic/TOCTOU/MIN-2/3/5 sets green. `tsc --noEmit` → **0 diagnostics**. Logs `/tmp/ig-fejest.log`, `/tmp/ig-fe-tsc.log`.

### T4 — Boot smoke
- Daemon restarted on `b76d6a74`: old 13:41 hierarchy (4368/62332) TERM'd, port free in 1s, new PIDs **72525/72527** (lstart 15:32:17); boot line `15:32:19 … Creating PostgreSQL engine: localhost:5432/ensemble_dev` (fresh, correct DB); `/livez` → `{"status":"alive","version":"0.12.0"}`.
- GET-stability on existing instance `e78d0a39`: **28 rows / 28 rows, `id_sequence_equal: True`** across two GETs on the freshly restarted daemon (survives restart — stronger than pre-restart stability). Artifacts `/tmp/ig-boot/`.
- Port safety: 8088 no listener; 9797 (prod) + 4199 (FE) untouched.

### Integration-gate worker ledger (13)
ig-adhoc-injection-graph 99537877 · ig-ctx-messages 3467f1e4 · ig-persistence 429964ae · ig-wcwake cae380e6 · ig-utils-serialize df4cfc1f · ig-ctx-injection ea19400a · ig-c2 0444b69a · ig-im-regression cd6e61be · ig-loose-sz d875aefa · ig-compaction-ab 926b1a69 · ig-injection-compaction-ab 097f9b24 · ig-fe-jest-tsc 06795b72 · ig-boot-smoke 81bf33b8

### Integration gate follow-ups (non-blocking)
1. `tests/test_injection_compaction.py::test_all_injected_messages_skips_compaction` — re-baseline on `latest` (fails at `e14f09f9` too; expectation encodes pre-skip behavior vs the anti-refire stamp).
2. Carry-over from main gate: register `identity_fix_injection_graph` as a repo pack; upstream `repository.py:2975/:3104` logger-kwargs hazard.
