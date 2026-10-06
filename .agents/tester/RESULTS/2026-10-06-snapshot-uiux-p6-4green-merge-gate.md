# snapshot-uiux P6 — 4-GREEN Merge Gate (FINAL REPORT)

> **RE-GATE ADDENDUM (2026-10-06, later): the gate is GREEN.** dev-FE landed `6b3b1dc2` (NG8002 fix); four tester-lane spec commits followed (`75316ee5` seed schema-compat → `871d7fea` scoped selectors → `81f239c6` rider → `afcf0cbc` toHaveURL auto-retry), each 1-file test-lane. Final: **A ✅ B ✅ C ✅ D ✅ 12/12** at tip `afcf0cbc` — see §10.

- **Commission:** P6 verification + 4-GREEN merge gate, `feature/snapshot-uiux`
- **Worktree:** `/home/nea/ensemble-src-wt-snapshot-uiux` (base `ac399874`)
- **Tips:** gate ran at `2ee644b7` (c8) → `b87e20a8` (boot-PATH hardening, BE-identical) → `11a203f9` (router-pin admission, test-only)
- **Date:** 2026-10-06 (00:5x–03:0x UTC)
- **Tester instance:** this dispatcher; 33 worker instances (see roster §9)
- **Gate outcome: A ✅ · B ✅ (baseline-exact) · C ✅ (0 new-at-delta after 1 disclosed test-pin fix) · D ❌ FAIL+HALT (deterministic FE template compile defect in reviewed HEAD code — dev-FE lane) · deferred (e)/(f) ✅**

## 0. Verdict

| Gate | Command (§6.4) | Result |
|---|---|---|
| A — tsc | `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` | **PASS** — `EXIT:0`, clean, ~20s (npm ci skipped: node_modules present) |
| B — jest full FE | `CI=true timeout 300 npm test -- --watch=false` | **PASS-with-baseline** — 107 suites / 3731 tests / **3727P + 4F = EXACTLY the documented baseline** (jobs-filter-state ×1 enum-drift `:49`, jobs-grouping ×3 timeAgo `:497/:559/:578`); snapshots suites 100% green (service 7, table 15, page 16, drawer 13, settings-clean ×2 additions, route present); 20.4s |
| C — pytest full BE | worktree-rooted `uv run pytest`, sharded (see §3) | **PASS — 0 new-at-delta failures.** Snapshot suites **110/110 strict green** (26 router + 10 list_with_filters + 32 repository-ext + 42 search-ext). 216 HEAD-side reds across the tree → **1 branch-caused (FIXED, verified) · 3 env-shaped (fence-proven) · 3 already-documented (QUARANTINE/critical-notes) · 209 machine-proven pre-existing at base `ac399874`** (6 base legs + 1 mini-leg + 1 fence cross-check) |
| D — Playwright e2e | `cd frontend && npx playwright test --config playwright.snapshots.config.ts e2e/snapshots.spec.ts` | **FAIL + HALT** (plan §7 flaky rule; failure deterministic) — `NG8002: Can't bind to 'backdropClass' … mat-drawer-container'` @ `snapshots.component.html:306`, committed in dev-FE `abeb5229`; **zero browser tests executed** (both runs aborted at webServer startup). Run 1: env (initdb off PATH, fixed). Run 2: the compile error. Handoff: `.agents/shared/handoff/2026-10-06-snapshot-uiux-gate-d-halt-backdropclass.md` |
| (e) deprecated proxy | `curl -i …/api/settings/snapshot-usage-metrics` | **PASS 3/3** — `deprecation: true` · `sunset: Sun, 31 Dec 2026 23:59:59 GMT` · `link: </api/snapshots/metrics>; rel="successor-version"` (verbatim block in worker log) |
| (f) live smoke | `GET /api/snapshots?limit=1` | **PASS** — HTTP 200, `{"items":[],"total":0}` — shape `{items[], total}` holds on a fresh disposable DB |

**Testing Complete: NOT READY TO MERGE — sole blocker is the Gate-D FE compile defect (dev-FE lane); A/B/C are green at `11a203f9` (C) / `2ee644b7` (A/B; BE-identical through `11a203f9`).**

## 1. STEP 1 — Authoring (commit c8 `2ee644b7`)

Five files, parent `9d35d9ae` (matched expected HEAD exactly), exec bit verified:
```
100755 357970e3… frontend/scripts/boot-e2e-snapshots-daemon.sh   (git ls-files -s)
```
| File | Lines | Notes |
|---|---|---|
| `frontend/playwright.snapshots.config.ts` | 132 | plan §4.3.1 pinned content; ports 18279/14199, `reuseExistingServer:false` ×2, clipboard permissions, globalTeardown, **testMatch added** (`/snapshots\.spec\.ts/` — without it a bare `--config` run sweeps all 25 e2e specs into the strict-port lane) |
| `frontend/scripts/boot-e2e-snapshots-daemon.sh` | 235 | maintenance mirror line-for-line; PG 15532 / daemon 18279; POSTGRES_* scrub incl. `POSTGRES_URL`+`ENSEMBLE_DB_DSN`; foreign-cluster refusal; canary `GET /readyz → status=="ready"` ×60 |
| `frontend/proxy.conf.snapshots.json` | 13 | plan-verbatim |
| `frontend/e2e/snapshots.spec.ts` | 595 | steps 1-9 + 11a-11c; closed 13-selector contract (fe-plan §5.6); **psql seed** (2 projects, 4 snapshots, 1 capture counter, 1 pref row; fixed UUIDs + ON CONFLICT; disposable-cluster guard, no fallback) — closes the plan's seed gap (empty disposable PG vs row-clicking steps) |
| `frontend/e2e/global-teardown-snapshots.ts` | 115 | race-loser backstop on the strict port pair |

**Ratified deviations (disclosed):**
1. **Step-4 regex substitution** — the leader-pinned literal `toMatch(/\/api\/snapshots\?$/)` is provably dead (D-6: paginator always sends `limit`+`offset`). Spec asserts `?limit=\d+&offset=\d+$` **plus** `searchParams.getAll(p)===[]` for all 7 filter params — strictly stronger than the pinned intent. **Needs leader ack.**
2. Step 2: 8 `<th>` asserted, 7 carry text (Actions header empty by design).
3. Step 6: capture-card rows via `ul.metrics-list li` (card renders a list, not `tbody tr`).
4. **Step-11a risk (pre-flagged, unaudited at runtime):** no explicit Escape binding found in dev-FE sources; mat-drawer native handling unverified — tests never ran. Dev-FE should verify before the re-gate.
5. `snapshots_router` is unconditional (no maintenance-style env flag) — documented in-file.

**Tester-lane follow-up commits:**
- `b87e20a8` — boot script + global-teardown PATH hardening for PG server binaries (`/usr/lib/postgresql/*/bin` glob-prepend; closes the run-1 `initdb: not found` class; 34 lines across 2 test-lane files; 100755 preserved).
- `11a203f9` — **the single branch-caused BE red, fixed:** `test_no_new_http_endpoint_in_api_router` (arm-notify AC6 exact-27-router pin) is GREEN at base / RED at HEAD solely because this branch legitimately adds `snapshots_router` (#28). One-line admission into `PRE_FEATURE_API_ROUTER_LIST` (api.py appearance order, between `settings_router` and `skill_bank_router`); whole file re-run **8/8 PASS, exit 0**. Cosmetic note: commits `b87e20a8`/`11a203f9` carry the worktree's ambient `designer` git identity.

## 2. Gates A/B — detail
- A: `EXIT:0` zero output; crosschecks `git log -1` → `2ee644b7…` + c8 message; `git ls-files -s` → 100755.
- B: summary verbatim `Test Suites: 2 failed, 105 passed, 107 total / Tests: 4 failed, 3727 passed, 3731 total / 20.419 s`. Failure-for-failure, line-for-line identical to the documented baseline (matched-identical ×4). +5 suites / +262 tests vs maintenance-era reference = the snapshot FE specs. No flake-ruling re-run needed (no 5th failure).

## 3. Gate C — full-BE sweep (sharded, env-fenced, base-attributed)

**Idiom:** worktree-rooted `uv run python -m pytest`, `env -u POSTGRES_* -u DATABASE_URL` (house fence), `timeout 300` outer + repo ini `timeout=30/thread` inner (later overridden `--timeout` where a test class needed real budget), `-q/-v --tb=short|line`, `--continue-on-collection-errors`, no `-x`. Hardened LLM fence (`-u OPENAI_API_KEY`, `OPENAI[_API]_BASE_URL=http://127.0.0.1:9`) on integration/e2e/perf shards — kills the real-LLM ssl.recv hang class by making calls fail fast.

**Shard matrix (HEAD-side):**
| Shard | Collected/ran | Result | Runtime |
|---|---|---|---|
| C0 snapshot suites (4 files) | 110 | **110/110 PASS, exit 0** | 21.1s |
| unit/routers (full dir) | 505 | 501P / **4F = documented baseline exactly** (stop_instance_subtree case7/7bis/8 + work_router shape) | 105.9s |
| job_queue A/B/C (sorted thirds) | 598/689/737 | 574P/5F · 684P/5F · 732P/5F (19S+3D) | 150/78/138s |
| unit/services (full dir) | 2399 | 2381P/18F | 235.8s |
| unit/tools A/B (sorted halves) | 927/2424 | 918P/5F · 2413P/10F | 150/166s |
| unit remainder Q1–Q4 (find-based quarters, subdirs excluded) | 2107/2033/2386/1892 | 2099P/8F/17E · 1996P/33F/4E · 2330P/20F · 1880P/10F/2E | 212/226/176/108s |
| misc-1 (services+tools+repositories+api+manager+migration+lint+static+property) | 1439 | 1367P/53F/19S | 159.1s |
| misc-2 → split | e2e 18; postgres 0-by-policy; perf probe | e2e 14P/**4F**; perf structural (see §3.3); 397/429 deselected project-wide by addopts | 7.6s (e2e) |
| opencode + message_queue_redesign | 989 | 969P/3F/13S/4D | 80.1s |
| integration B (orig + hardened B2 — byte-identical results) | 668 sel | 371P/**18F**/279D | 164/165s |
| integration A sub-shards s1–s5 + tail + solo | s1 106 sel (59P/4F @59% kill); s2 155→150P/1F; s3 55→51P/4F; s4 97→74P/1E-env; s5 175→102P/3F; tail 47→41P/5F/1-killed; lca solo2 → 1F@43.4s | full dir covered; see §3.2 | 8–300s each |

**3.1 Attribution legs (detached worktrees @ `ac399874`, `uv sync`, `daemon.__file__` in-worktree proof, removed after):**
| Leg | Corpus | Verdict |
|---|---|---|
| 1 | job_queue 15 reds | **15/15 RED at base — pre-existing** |
| A | Q1+Q2+Q3 reds + 2 error-reps | **60 RED / 1 GREEN** — the GREEN = the router pin (→ branch-caused → FIXED `11a203f9`); error-reps (builtin_mcp, context7) ERROR at base (documented `service_tool` family) |
| B | Q4+services+tools reds + webfetch rep | **42F+1E ALL at base — pre-existing** (proxy_phase1 ×7, archive ×5, prompt-integrity ×6, etc.) |
| C1 | misc-1 + mqr reds | **52 RED / 2 GREEN-at-base** → fence cross-check at HEAD (hardened fence): the 3 tests (2× skill_evolution AB-resolution, 1× atomic_status) **3/3 PASS → env-shaped, not branch-caused** |
| C2 | attestation quintet + complete_cancel ×4 + e2e ×4 | **13/13 RED at base — pre-existing** |
| C3 | int-B 9 items (16 tests) | **16/16 RED at base — pre-existing** |
| D | 2 new live_descendants reds | env-blocked at base (fixture patch bypass → real-LLM retry sleep → ini-30s guard); HEAD-side = same `ScriptedChatModel exhausted` family as 5 machine-proven siblings — **not branch-caused (disclosed nuance: base run env-killed, HEAD run scripted-exhaustion; both red, zero branch causality)** |

**3.2 Pre-existing red families (test-debt ledger for the repo, all base-proven):** observer `fake_sync` arity 5→6 (job_feedback_observer) ×3+3; watch-events `settled` taxonomy ×6; terminal-write census line-pins ×2; `create_job_tools` order/count ×2; `ScriptedChatModel exhausted` attestation family ×6+5 (deny-loop + degenerate-retry turns unscripted); `VisionModelNotAllowedError` cluster ×22+8+2 (fixtures don't add `vision` to allowed_models — arch §8 guard fires); terminal-state mirror `active`≠`done` family (complete_cancel ×4 + e2e ×4 + property ×1); `service_tool` mock-config setup errors ×17+4+2; PG-only migration-on-sqlite ×4; `SimpleNamespace.priority` fixture ×12; Mac-path host-shape ×2; SPA-fallback 404 ×2 (worktree `frontend/dist` absent); stale pins (api.py size, release-tag v0.12.4, models `__all__`, leader team_members 15, wanderer/devops/gaia meta drift, worker skill-set 1.3.0); vscode proxy ConnectError ×1; misc singles.

**3.3 Structural disclosures (scope decisions, none gate-blocking):**
1. `tests/packs/*.py` excluded — pytest-incompatible companions (`g7_unique_index_smoke_test.py:121` module-level `sys.exit(0)` aborts any flat `tests/` traversal; real packs are the `.sh` wrappers).
2. `tests/postgres/` — **0 tests by marker policy** (`pyproject addopts -m 'not integration and not postgres and not slow'`); by-design env-gating.
3. `tests/performance/` — 14 items selected; probe (300s cap, `--timeout=280` override): **3 PASSED / 0F / 0E**, cell `test_cell_runs_and_records_metrics[10x1k]` ≥~260s and killed at the cap, **11 cells never started**. Perf-matrix cells are multi-minute by design — structurally incompatible with the 5-min pack cap; needs a dedicated perf commission (env-scaled cells or per-cell packs). Zero failures observed on completed cells.
4. `tests/e2e/test_context_injection_hybrid.py` — collection error by design (import-time live HTTP to :8079); needs a daemon or a fixture-level skip guard.
5. Integration double-coverage: half B ran 3× (B, B2, s5-overlap) — byte-identical 18-red set each time (free consistency check; my 9-slice split computed over the full 172-file list rather than the 86-file half — disclosed).
6. Hidden ini guard: `pyproject timeout=30/thread` — caused three phantom "structural timeout" readings; solo-with-override resolved the lca test in 43s. Recorded in LESSONS.
7. Baseline reconciliation: the documented 2-file pytest baseline was measured on a narrower surface than "full BE pytest"; this gate's extended surface produced 213 additional reds, **all** attributed pre-existing/env/already-documented above — the lanes' baseline claim (only those 2 files red) holds within their measured surface, and extends cleanly here.

## 4. Gate D — FAIL + HALT detail
- Run 1 (~5s): webServer exit 127 `initdb: command not found` — PG16 server bins off non-interactive PATH. Env-prep failure, zero tests.
- Run 2 (~240s): PG→DB→daemon chain green (`readyz status:'ready'`), **ng serve compile FAIL**: `NG8002 … 'backdropClass' … mat-drawer-container` @ `snapshots.component.html:306` (dev-FE commit `abeb5229`). Deterministic (`git show HEAD:` proof). Retry budget spent → HALT per §7. Re-run without a fix cannot change the outcome.
- Why A+B missed it: `tsc` does no template binding checks; jest TestBed is schema-tolerant. Only the full compiler run catches NG8002 — the runtime gate earning its keep.
- Cleanup: surgical (pg_ctl stop w/ pid+cmdline evidence; trio freed; 8088/8079/9797/7979 never touched); `playwright-report`/`test-results` empty dirs remain (untracked).
- (e)/(f) ran against a standalone boot (ready in ~10s) — both PASS (§0).
- **Re-gate protocol:** dev-FE fix → fresh Gate D on the new tip (Phase 1 exports `/usr/lib/postgresql/16/bin`; boot script now self-hardened) + prudent Gate B re-run (template/spec adjacency); Gate A cheap re-run; Gate C unaffected by FE-template changes.

## 5. ensure.md
`.agents/tester/rules/ensure.md` = 4-line dev.sh boot probe (repo convention; NOT an e2e-suite mandate — grep-verified in blueprint). No contradiction with this gate's methods. Validated N/A beyond gate structure (no dev.sh boot in this commission; the e2e daemon boots via the dedicated boot script).

## 6. Quarantine
No new quarantines — all pre-existing reds are deterministic (not flaky), documented in §3.2 as a test-debt ledger instead. Existing QUARANTINE entries honored (attestation-migration + orphan-sweep pair + mcp_server_crud legacy).

## 7. Documentation updated
- PACKS.md — new commission section + pack table
- LESSONS/2026-10-06-snapshot-uiux-gate-lessons.md — 5 lessons (ini-guard, tb-flags, shard sizing, ScriptedChatModel family, LLM fence)
- this RESULTS file; handoff note (§4) already in `.agents/shared/handoff/`

## 8. Action needed
- [ ] **dev-FE**: fix NG8002 (`[backdropClass]` on `mat-drawer-container`, `snapshots.component.html:305-308`, commit `abeb5229`) — keep `data-test="drawer-backdrop"` on the container (spec contract). Verify Escape-close (step 11a) while there.
- [ ] **Leader**: ack the step-4 regex substitution (§1.1).
- [ ] Re-gate D (+B, +A) after the fix; C stands.
- [ ] (follow-ups, non-blocking) §3.2 test-debt ledger; §3.3 items 3/4; `OPENAI_SELECTABLE_MODELS` vision gap; `designer` ambient git identity on 2 tester commits.

## 10. Re-gate closure (2026-10-06, final)

Tip chain after the original report: `2ee644b7 → b87e20a8 → 11a203f9` (tester) `→ 6b3b1dc2` (dev-FE NG8002 fix, 1 file) `→ 75316ee5 → 871d7fea → 81f239c6 → afcf0cbc` (tester, each 1-file spec-only).

| Round | Tip | Result |
|---|---|---|
| D-r1 | 6b3b1dc2 | webServer chain GREEN first time (zero NG8002) → seed aborted (my spec's projects INSERT missed NOT NULL `job_queue_paused`); 2/2 deterministic |
| D-r2 | 75316ee5 (seed live-proven: idempotent apply ×2, counts 4\|1\|1, API 200 total=4) | **first browser execution: 8/12** — steps 2/3 strict-mode selector ambiguity (mine), 11a focus-dependent Escape (mine), step 1 sync-URL race (mine); 2/2 deterministic |
| D-r3 | 81f239c6 (scoped locators + drawer-focus + menu wait + :285/:328 riders) | **11/12** — only step 1; click fires, item visible, sync assert races the router |
| D-r4 | afcf0cbc (`await expect(page).toHaveURL(/\/snapshots$/, 5s)`) | **12/12 PASSED, EXIT:0, 76s** — step 1 green under auto-retry; **app router/menu CORRECT** |

- **A at 6b3b1dc2 and 75316ee5: EXIT:0 both** (7.5s). **B at both: 107/3731/3727P+4F baseline-exact** (20.0/20.2s). Invariance to `afcf0cbc` is path-proven: every commit after `6b3b1dc2` touches only `frontend/e2e/snapshots.spec.ts`, which is outside `tsconfig.app.json` scope (A) and jest's collection (B) — each quick-fix report carries the 1-file `git diff --stat`.
- **Step-4 substituted assertion (leader-ACKED): PASSED live** in rounds 2/3/4 — `?limit=\d+&offset=\d+$` + all-7-filter-params-empty held against the real intercepted reset URL.
- **Step-11a adjudication:** Material closes the drawer natively on Escape (keydown-ESCAPE listener; dev-FE's sidenav.mjs:207-208 citation CORRECT) **when the drawer pane holds focus**; the spec now focuses the pane before Escape. The spec commentary at `snapshots.spec.ts:75-80` ("no Escape binding… plan-vs-implementation gap to route") is now MISLEADING — no app binding is needed and none should be added; a doc-comment polish may ride any future spec touch (not done — tip frozen for merge).
- Seed header comment nit (single-transaction phrasing; actual mechanism `ON_ERROR_STOP`) — doc-only, same ride-later rule.
- Final commits ledger (tester lane, this commission): `2ee644b7` c8 quintet · `b87e20a8` boot PATH hardening · `11a203f9` router-pin admission · `75316ee5` seed schema-compat · `871d7fea` scoped selectors · `81f239c6` spec riders · `afcf0cbc` toHaveURL. Ambient `designer` git identity on tester commits disclosed.

**FINAL VERDICT: 4-GREEN at `afcf0cbc` — MERGE-READY (giter's lane to merge).**

## 9. Worker roster (33 + re-gate wave: 8 more)

c8-author 6bd61a88 · gate-a f432b81e · gate-b 8c4fded9 · gate-d 5b8b9cb2 · C0 5a608dae · jq a/b/c dd0f60ff/5a5e60c6/96f59e90 · routers a9259e5b · services f77d4381 · tools-a2 a77b266f · rest1-4 4308912a/9ad1fbd3/972f5703/b5e46822 · misc1 07b08b1a · misc2 eb6317c4 · oc-mqr 296f5593 · int-a 3685c60a · int-b f13407df · int-a2 90c2a16e · int-b2 978af32e · s1-s5 6421d1c4/a9e9a902/bd77c13b/ec786d31/bc9d1db8 (s6-s9 spawned-then-terminated, redundant) · quickfix-path aca17aa4 · quickfix-pin 225c66da · leg1 6a53a7d4 · legA d5001fdc · legB cc877fe8 · legC1 a86fc11f · legC2 7d8b6486 · legC3 8a8811ac · legD 9d8d4dbb · fence-xcheck 19390ea6 · e2e-rerun 4a042fae · perf-probe 36d74ce8 (silent; re-dispatched b8291352) · tail 19f436df · tb-rec c3769f1b · lca-solo e903a9e0 · lca-solo2 09499ef1
