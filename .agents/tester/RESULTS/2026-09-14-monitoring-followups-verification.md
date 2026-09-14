# Verification Gate — Monitoring Follow-ups (Branch B) + FE Error Transcript (Branch A)

**Date:** 2026-09-14
**Branch B:** `feature/monitoring-followups` @ `faa045b9` (base `76cbcc28` v0.12.9; commits `b1fc0fe8` severity promotion + `1a10123a` watchover fix + `faa045b9` sibling stamps; worktree `agents-ensemble-wt-recovery-ladder`)
**Branch A:** `feature/fix-fe-error-transcript` @ `5314a733` (dd0a6926 renderer + 5314a733 banner decoder; FE-only, 8 files +682/−2; worktree `agents-ensemble-wt-fe-transcript`)

**VERDICTS: Branch B ✅ GO · Branch A ✅ GO** — 6 dispatches, 0 re-dispatches, zero branch-caused failures on either side, zero gate-added commits (no gaps required pins).

---

## 1. Branch B — daemon (`faa045b9`)

### B1. Severity promotion (`b1fc0fe8`) — ✅ VERIFIED
- **Design**: leaf constant `RETRY_BUDGET_EXHAUSTED_MARKER` (constants.py) + ONE stamp at the `create_agent_node` loud-ERROR handler before all 3 raise exits (pre-terminal repair never raises — folds `abort_reason="second-exception"` — so one site covers every exit) + classification branch in `_classify_error_type` (`getattr(marker)` → `validation_error_exhausted`, else fallthrough) + `CRITICAL_ERROR_TYPES` frozenset 2→3 (only the exhausted lane added).
- **Plain `validation_error` byte-identical**: structural diff (unstamped fallthrough unchanged) + behavioral probe (unstamped `LLMResponseValidationError`/`EmptyLLMResponseError` → lane `validation_error` → `**Severity:** warning` at the real `_send_error_report`, base ≡ tip).
- **8 new tests PASS** (4 lineage integration incl. REAL-graph stamp pin + 4 unit severity classification).
- **Independent 3-shape re-derivation** (`/tmp/mon-b1/probe_severity.py`): (a) transient-recover → `connection_error`, never the validation lane; (b) unstamped/post-ladder-repaired → warning; (c) stamped exhaustion → `validation_error_exhausted` → `**Severity:** critical`. Discriminator claim holds end-to-end.

### B2. Watchover family — ✅ 268/268 claim VERIFIED (claim scope = 9 files)
- 9 watchover files (the dev's family boundary): **268/268 GREEN**. Historical cluster 5/6 files fully green (143/143).
- `test_watcher_repository_concurrent.py` ×2 failures = **different pre-existing class** (`'settled' != 'failed'` watch_events normalization; zero references to the fixed LLM symbols) — separate-dispatch follow-up, NOT this arc.
- **Fake-LLM sibling sweep CLEAN**: all 9 factory sites stamped; adjacent `patch(ThinkingChatOpenAI)` users safe-by-MagicMock; one suspected sibling (`TestWatcherContextBuilderConfig` bare lambdas) dismissed by construction — `_make_manager_with_llm` never patches the LLM.

### B3. Full-suite A/B (`faa045b9` vs `76cbcc28`, 12 partitions × both legs, detached worktrees) — ✅ 0 branch-caused
- **Common 185 / tip-only 0 / base-only 47** — all 47 base-only = **exact set-match to the watchover fix** (decision ×28, context_builder ×9, integration ×4, phase5 ×3, edge_cases ×3). The task-cited "49" was approximate; actual 47, fully accounted.
- 8-test collection delta (tip 18,128 vs base 18,120) = the severity tests, all PASS.
- Known-family checklist confirmed COMMON; count drift vs older baselines is the newer v0.12.9 base lineage (httpx row-60 now 0, job_queue 15, maintenancer 1) — both legs equal, informational.
- **Flake `TestContinuousEmptyGraphEndToEnd` (tests/integration/test_empty_guard_error_lineage.py) independently verified PRE-EXISTING order-dependent, NOT branch-caused**: full-file default-order runs fail a DIFFERENT parametrization per run on BOTH legs (tip: test_c/test_a/test_b across 3 runs; base: pass/test_b); solo 3× matrices flake on BOTH legs (tip test_b P,F,P; base test_a F,P,P + test_b P,P,F). NOT quarantined (report-only; leader routes — quarantine candidate or test-architecture fix).
- Artifacts: `/tmp/mon-b3/` (rosters, flake logs, per-partition outputs).

### B4. Baselines + PG spot — ✅ all byte-exact
- emptyguard **343/343** · ladder_symptom 87/87 · ladder_durable_sqlite 3/3 · ladder family 106/106 (per-file exact).
- PG spot: disposable PG14, `tests/postgres/test_dependency_bus_pg.py` (the `emit_terminal` PG lane) **6/6**; teardown fully verified.

## 2. Branch A — FE (`5314a733`)

### A1. Static + suite — ✅
- node_modules fresh (no install needed; tree clean).
- FE pack `fe_static_typecheck_build_test`: **RESULT: PASS** (tsc 0 errors, ng build 0 errors; SHA bracket 5314a733→5314a733). ⚠️ Operational note: the pack's default `EXPECTED_BRANCH=feature/mission-class` — non-default branches MUST pass `EXPECTED_BRANCH=<branch>` or it hard-fails DRIFT spuriously.
- Full suite **3190/3190 in 12.3s** (claim ~3185 reconciled: +5 = spec grew 23→28 `it()` at final commit). The 28 new specs = `sse-error-row.spec.ts` single file, 8 describe blocks, 28/28.
- Diff FE-only as claimed. Cosmetic: `chat-interface.scss` +7.07 kB over component budget (warning-class, baseline-tolerated).

### A2. Real-DOM web-automation — ✅ 9/9
- Playwright harness exists (e2e/, 19 specs) but stock config boots the daemon — forbidden. Worker drove **Playwright-as-library** with bundled Chromium against a one-origin mock: fresh `npm run build` (14.5s, bundle grep-verified for branch strings) served with `/api` SSE mock on **127.0.0.1:10180** (>10000 ✓; relative `API_BASE` = no override needed). Wire shapes emitted exactly per pins.
- Assertions (DOM evidence captured): dict-shape → red `[data-testid="sse-error-row"]` `⛔ Message processing failed (streaming) / LLM provider timeout after 120s`, computed red styling + `role="alert"`; **redelivery dedupe** (same message_id ×2 → 1 row, id-keyed upsert); bare-string shutdown lane card; `status_change{error}` title-only card; banner decoder outputs strings (`{"message":...}`) — **`[object Object]` negative scan false**; **System-toggle OFF still renders all 3 error rows** while hiding the plain-system canary.
- Cleanup proven: mock SIGTERM'd, port 10180 zero connections, prod daemon 9797 untouched (same PID), 8079/4199 never used.
- Lanes: dict/bare/status/dedupe/decode/toggle-negative AUTOMATED; 3 lanes post-deploy-only (real daemon shutdown mid-stream; real LiveEventHub reconnect-redelivery; session-transience across real reload) — documented manual recipe remains the gate for those.
- Scoping note (not a defect): `latestError` has no DOM binding in this build (console-only consumer via chat.component effect).

## 3. Findings / Follow-ups (leader routes)
1. 🟡 `TestContinuousEmptyGraphEndToEnd` order-dependent flake — verified pre-existing (both legs flake; matrices recorded); quarantine candidate or test-architecture fix (likely shared-state/order coupling in the lineage file).
2. 🟡 `test_watcher_repository_concurrent.py` ×2 (`'settled' != 'failed'`) — pre-existing separate class, needs its own fix.
3. 🟢 FE pack `EXPECTED_BRANCH` default trap — document or default to current branch.
4. 🟢 `chat-interface.scss` component style budget overage — cosmetic.
5. 🟢 `latestError` console-only — observability scoping note.

## 4. Gate-added commits
**None** — no coverage gaps required pins on either branch.

## 5. Bottom line
- **Branch B: ✅ GO** — severity promotion verified (design + byte-identity + independent re-derivation), watchover fix set-exact (47 base-only), 0 branch-caused reds suite-wide, baselines byte-exact, PG spot green.
- **Branch A: ✅ GO** — 3190/3190 + tsc/build clean + 9/9 real-DOM on the pinned wire shapes with dedupe/negatives, prod daemon untouched, post-deploy lanes documented.
