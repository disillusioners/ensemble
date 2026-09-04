# RESULTS — queue-status + missions-badge FULL Gate (2026-09-04)

**Branch:** `feature/queue-status-missions-badge` — evidence @ `719bcaa2`; final gate HEAD **`13782089`** (= `719bcaa2` + 1 gate-owned test-infra commit: NEW pack `defer_blocked_api_unit_test`, tests/ only). Base **`96231383`** (cpv2 merge tip).
**Change under test (20 files, +3,086/−223):** badge truth (FE badge missions-N from missions projection), NEW read-only `GET /api/queues/defer-blocked` (3-severity transparency), `daemon/repositories/job_queue/_idle_predicate_sql.py` (NEW, extracted), `daemon/repositories/job_queue/repository.py` (+7/−7), `daemon/routers/queues.py` + `schemas.py`, `daemon/services/defer_block_resolver.py` (NEW), `daemon/api.py` (+13), FE indicator/models/service, `tests/unit/routers/test_defer_blocked_api.py` (NEW, 26 tests).

## VERDICT: ✅ FINAL PASS — CLEARED FOR MERGE
Zero branch-caused regressions across the full 12-partition suite (~18.7k collected). Acceptance 3/3 sets EXACT. Pin hardened-form verified red-under-mutation. Web matrix: badge-truth PROVEN live. ensure.md Core green.

---

## 1. Acceptance (independent, rev-parse gated @ 13782089)

| Set | Expected | Result |
|---|---|---|
| BE `test_defer_blocked_api.py` | 26 | ✅ **26 passed / 0 failed** (2.12s) — NEW pack `defer_blocked_api_unit_test.sh` (house style, 110s internal + 150s outer, branch guard armed), gate commit **`13782089`** |
| Census drift | drift 10, census 23 frozen | ✅ **24/24** (drift 10 + linkage 14, 5.24s). Census ground truth: **WRITERS 23** (frozen ✓) / CREATORS 1 / **MINTS 0** |
| FE | jest 69 suites / 2426; tsc; build @ 10-warning baseline | ✅ **69/69 suites, 2426/2426, 0F** (10.1s, `--no-cache`, SHA-bracketed) · **tsc exit 0** · **build exit 0** · **SCSS_WARNING_COUNT = 10 exact baseline** (zero branch-introduced) |

**Ledger correction (census):** prior gate bullets' "23/6/1" wording is **stale** — `KNOWN_MINT_SITES` is an EMPTY frozenset (comments only) and is **byte-identical at base `96231383` and HEAD** (constitution.py untouched by branch; `git log 96231383..719bcaa2 -- daemon/job_state/constitution.py` empty). Subset-only mint census passes by design. Not a defect; downstream automation must not hard-code "6".

## 2. FULL regression — 12 partitions, HEAD `13782089` vs base `96231383`

Every partition rev-parse gated; zero NEW failures anywhere; all failures map to existing QUARANTINE.md families.

| P | Pack | Collected (Δ vs 09-03 baseline) | Result | Failure adjudication |
|---|---|---|---|---|
| 1 | regression_unit_tools | 1,109 (+60 upstream) | 1,101P/2F/1S | 2F = upgrade_registration ×2 (ledger row, "corrected ×1→×2") — **0 NEW** |
| 2 | regression_unit_services | 1,178 (+46) | 1,171P/7F | 7F = proxy_phase1 exact (mission-gate standing "8→7") — **0 NEW** |
| 3 | regression_unit_smaller_subdirs_routers | 591 (+52 = defer_blocked **+26** + message-metadata repos +28 + missions_api −2 flag-matrix collapse) | **591P/0F** | All 26 branch tests PASS in-partition; ledgered slash_commands node now extinct — **0 NEW** |
| 4 | regression_unit_loose_a_d | 1,050 (0) | 1,017P/10F/21E/2S | EXACT baseline: api_module_is_small (own row) + misc ×9 + slash fixture-drift ×21E — **0 NEW** |
| 5 | regression_unit_loose_e_l | 1,116 (0) | 1,105P/11F | EXACT: job_processor ×4 + hide_kb ×5 + llm ×2 — **0 NEW** |
| 6 | regression_unit_loose_m_r | 1,890 (0) | 1,843P/7F/40S | EXACT: models_split + phase4 + paused_auto_resume ×5 — **0 NEW** |
| 7 | regression_unit_loose_s_z | 1,036 (0) | 971P/52F/2E/11S | EXACT: watchover 47-family + terminal_reason + vision + validate_agent_id + wanderer ×2 + webfetch 2E — **0 NEW** |
| 8 | regression_top_level_a_h | 1,082 (+10) | 1,007P/19F/2E/54S | F/E exact: test_api ×2 + jsonb ×1F+2E (context-flake row) + misc ×16 — **0 NEW** |
| 9 | regression_top_level_i_q | 2,443 (0) | 2,309P/61F/73S | 59 ledgered (sqlite-cascade 29 + injection 26 + misc 2 + M2-row 2) + 2 adjudicated below — **0 NEW** |
| 10 | regression_top_level_r_z_misc | 2,331 (+20) | 2,259P/13F/34S/5xf | 13 ledgered: sqlite ×9 + skill_evo ×2 + terminal_orphan + worker_notification (row-43 **second manifestation**); dequeue flakes passed this run — **0 NEW** |
| 11 | regression_job_queue | 1,674 (+24) | 1,629P/7F/38S | 7F = **node-for-node exact** quarantined settled-rename family — **0 NEW** |
| 12 | regression_integration_opencode_e2e | ~829 outcomes (+95) | 805P/16F/8E/2S | 16 ledgered: bucket5 ×6 + complete_cancel ×4 + w7 + vscode + answer_dismiss + pause-during-report ×3; httpx ×19 not reproduced; 8E = context_injection_hybrid (env-blocked by design, daemon down) — **0 NEW** |

### Base A/B legs (disposable worktree `/private/tmp/qsmb-base` @ `96231383`, isolation-proven via `daemon.__file__`)
- **P-11 job_queue: MATCH 7≡7** — all 7 HEAD failures reproduce at base with byte-similar signatures (observer-guard ×4 + settled-vocabulary ×3). **Branch-caused = 0** on the branch's most-adjacent surface.
- **P-9 ambiguous pair:**
  - **N1** `test_instance_messaging_queue_routing.py::…::test_router_forwards_queue_id_to_enqueue_message_job` — FAILS at base solo with the IDENTICAL `messages.py:258` MagicMock-await signature → **PRE-EXISTING** (13th node of the mock-await class; added to that ledger row).
  - **N2** `test_memory_integration.py::…::test_concurrent_writes_no_corruption` — PASS at base solo, PASS at HEAD solo, **3× PASS retry budget** (1.07s/1.20s/1.06s); fails only in xdist partition context (`Errno 2` tmp-dir thread race) → **CONTEXT-FLAKE**, quarantined (new row).

## 3. Pin hardened form — all 3 legs PASS
- **LEG 1 clean:** 5/5 selected (`bind_contract`, byte-match ×2, BoundedQueryCount ×2) @ `13782089`, 1.15s.
- **LEG 2 mechanism:** `_expected_runtime_binds` is **paramstyle-agnostic by construction** — it replays SQLAlchemy's dialect-independent `expanding=True` rule over a canonical body-order tuple (`_DEFER_SYSTEM_BIND_BODY_ORDER`, `terminal_statuses` twice = two textual occurrences). Docstring "regardless of paramstyle" is accurate BECAUSE the dialect is never consulted — dialect-independent value-equality (stronger guarantee than paramstyle-awareness).
- **LEG 3 mutation kill:** 1-line sentinel appended to the expected bind tuple in a disposable worktree (`/private/tmp/qsmb-pinmut`, uv-synced, isolation-proven) → the fresh-engine bind-VALUES assertion **FIRED** with its designated error (expected 10-tuple incl. sentinel vs observed 9-tuple). Worktree removed; main worktree untouched (`git diff` empty).
- ⚠️ Spec note: the driver-level bind-VALUES pin lives inside `TestFreshEngineNegativeFixture::test_terminated_instance_with_residual_active_job_is_not_a_witness` (not in `BoundedQueryCount`); `-k` filters for future re-runs must include `residual_active`.

## 4. Web automation (real chromium, BE :8079 on PG `ensemble_dev` + FE :4199, serial)

| Case | Verdict | Evidence |
|---|---|---|
| **W1 Badge truth** | ✅ **EXERCISED — PASS** | Badge `missions: 5`; tooltip `Running: 0 / Pending: 0 · Live missions: 5 (from missions projection)`; `missions-live` class + pulse; dropdown "5 live missions". API oracle (the FE's exact call, `GET /api/missions?liveness=processing,pending,paused&limit=1`) = **5**. Equality holds; **NOT 0/0 while missions live**. Screenshots `w1_badge.png`, `w1_tooltip.png` |
| W2a AMBER (pending + paused holder) | 📄 DOCUMENTED-UNREACHABLE | `8d8a5591` → `INSTANCE_NOT_FOUND` (precondition void); no safe defer-lane enqueue exists without touching live/leader entities on shared PG (leader-authorized fallback exercised). Verified via 4 unit specs (`test_amber_paused_holder_via_legacy_clause`/`_via_settled_mirror`, `test_paused_holders_sort_first_and_dedupe_by_instance`, `test_paused_since_falls_back_to_updated_at`) + live API GET showing the holders mechanism real (`defer_blocked:true`, 1 `kind:"live"` holder, since 2026-08-29). Screenshot `w2_header_no_affordance.png` |
| W2b INFO (live-only holders) | 📄 API-VERIFIED + spec-pinned render | Live API IS in live-holder state (pending_count=0), but the FE render gate (`defer-blocked.model.ts`: icon only when `pending_count > 0`; spec `defer-blocked.model.spec.ts:39-41` asserts null) shows NO icon — **implemented contract**; BE semantics covered by `test_info_live_only_holders`. 🟡 See Follow-ups #1 |
| W2c NONE (no affordance) | ✅ EXERCISED | pending=0 → no icon; DOM-asserted `{hasWarningIcon:false}` + screenshot |
| W3 Settled rows in recent list | ✅ EXERCISED | Panel renders RECENT rows with `SETTLED` chip + check_circle (8× mission: completed, 1× cancelled); a11y labels verbatim. Screenshot `w3_settled_rows.png` |

Zero probe entities created (zero writes to shared PG). Teardown: only self-started processes killed after port-verified identity (8079/4199 freed; **8088 never touched**). Screenshots + REPORT in `/tmp/qsmb-webauto/`.

## 5. ensure.md (blast-radius scoped)
- **Critical #1** (no regressions in changed packs): ✅ — acceptance pack 26/26 + P-3/P-11 branch-adjacent partitions green-or-ledgered; 0 product regressions.
- **Critical #2/#3** (concurrency/deadlock, sync-DB-off-loop): ✅ `concurrency_atomic_unit_test` **98P/0F/74S exact baseline** (7.69s).
- **Critical #4** (dev.sh flag): ✅ `dev.sh:102` `--timeout-graceful-shutdown 10`.
- **Important** (async callers awaited): ✅ 9/9 call sites audited awaited.
- **Release Gate E2E:** out of scope — scoped feature (not architecture); full non-integration suite WAS covered via the 12 partitions. Contradictions: none (all pack-mapped).
- Worker RESULTS: `2026-09-04-ensure-validation-queue-status-missions-badge.md`.

## 6. Scope Decision
Full suite run — **warranted**: full merge gate, cross-module (BE routers/repos + FE), leader-mandated standard protocol (HEAD vs base). Release-Gate E2E excluded per blast radius.

## 7. Quick Fixes / Code Changes by the gate
- **`13782089`** (tests/ only): NEW pack `test/packs/defer_blocked_api_unit_test.sh` (+51 lines, house style). No production changes anywhere in the gate.

## 8. Quarantine updates (this gate)
- N1 → appended to mock-await family row (base-verified @ 96231383).
- N2 → NEW context-flake row (memory_integration concurrent-writes tmp-race; partition-only).
- Settled-rename 7-node family → re-verified stamp (7≡7 byte-similar @ base 96231383).
- worker_notification (row 43) → second in-partition manifestation noted.
- test_api_module_is_small → re-verified failing @ 13782089 (2047 lines; row unchanged in substance).

## 9. Gaps / Follow-ups
1. 🟡 **W2b leader ruling:** matrix wording "live-only holders => INFO [icon]" vs implemented contract "no icon unless pending_count>0" (spec-pinned). If an INFO icon at pending=0 is desired, that is a FE change request, not a defect.
2. `fe_static_typecheck_build_test.sh` EXPECTED_BRANCH default is stale (`feature/mission-class`) — needs env override or re-point; also unregistered in PACKS.md (blueprint carries its contract).
3. Pack docstring drift: `regression_unit_tools` header says 1,049 collected; actual 1,109.
4. Census ledger wording "23/6/1" → correct to **23/1/0** in future bullets.
5. Standing: 7-node settled-rename fixture migration; worker_notification flake accumulating manifestations (2nd); httpx ×19 family did not reproduce this run (watch).

## 10. Deploy Checklist Addition (leader's)
Post-merge restart: `curl` `/api/queues/defer-blocked` **once on live PG** — incident-family paranoia check.

## Dispatch Summary
21 workers + 1 revive (base-ab ×2 tasks), ≤6 concurrent, all dual-layer-timeout wrapped or worktree/read-only. IDs: c3104fa3 (recon), db6fd24e (acc-be+pack), f2f39a59 (census), 3067db86 (jest), 1a5594d8 (tsc/build), 2904b3b6 (pin ×3 legs), 774095ca (web), e2a3a225/4f7827cd/868d9bf4/76ea96cd/ac50b150/609263d1/da4e5341/b4350567/ad421f6e/daa2f583/2c9f1d30/64b09a3d (P-1..P-12), ccd1b94c (ensure), f0dddd5c (base A/B ×2). Worktrees created and removed: qsmb-pinmut, qsmb-base (×2 rounds). No timeouts (all packs ≤73s vs 300s cap).

## Overall Status
- Acceptance: ✅ 3/3 EXACT · Full regression: ✅ **0 branch-caused failures / 0 NEW unrecognized** (12 partitions + base A/B) · Pin: ✅ 3/3 legs · Web: ✅ matrix verified (2 exercised states + badge-truth headline; 2 documented-unreachable with authorized fallbacks) · ensure.md: ✅ Core 3/3 Critical + Important 1/1
- **FINAL: ✅ PASS — recommend MERGE `feature/queue-status-missions-badge` @ `13782089`.**
