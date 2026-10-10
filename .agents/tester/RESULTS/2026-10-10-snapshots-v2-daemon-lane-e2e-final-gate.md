# Snapshots v2 Redesign — DAEMON-LANE E2E FINAL GATE @ b5cafd3f7 — VERDICT: ❌ GREEN NOT MET — 11/12; race fix VERIFIED; zero regressions; leg #3 reds at a NEW deeper site = (d) click-locator text-anchor (test-side; never-executed-before territory)

Date: 2026-10-10 · Final confirmation run after `b5cafd3f7` ("test(e2e): race-immune response arming…", parent `64b351b8b`, sole file `frontend/e2e/snapshots.spec.ts`, +39/−9) on `test/snapshots-v2-spec-rebase`, executed in the reused developer worktree `/tmp/ens-wt-snapv2-rebase` (HEAD `b5cafd3f7b0daeb262f4a3dafd697ece6b665423` verified, clean pre/post).

Worker instance: `c60c890f-ab5d-4243-b3bb-fb2654847f33` (e2e-test, feedback 8/10). Evidence: `/tmp/ens-snapv2-e2e-evidence-final/` (report.json, html/, run-stdout.log, daemon-myrun.log, leg3-trace-final.zip + leg3-trace/). Runs #1/#2 evidence dirs verified intact.

### Summary
- 12 executed | 11 PASS | 1 FAIL | flaky=0 — `1 failed, 11 passed (1.7m)`, 100.5s, single invocation within `timeout 300`
- **b5cafd3f7 race fix VERIFIED**: leg #3 sub-legs (a) project / (b) agent / (c) tags all GREEN with param-correct GET pairs in trace (`project_id=…`, `+agent=coder`, `+tags=kind:implementation`) — run #2's (c) arming race is closed
- **Zero regressions**: all 11 run-#2-green legs PASS→PASS (incl. hardened step-4 clear-click 7-params-absent wait, and #7 money census `17:34:01 PUT /api/settings/snapshot-create 200`)
- Leg #3 FAIL→FAIL but the failure site ADVANCED: (c)-race @ :387 → (d)-locator @ :414 — a NEW pre-existing spec defect exposed because this sub-leg had never executed before (runs #1/#2 died earlier)
- **Product failures: NONE** across all three runs of this loop

### Leg #3 — three-strata discrimination (trace method, `leg3-trace-final.zip`)
1. **Arm-race? FIXED.** (a)/(b)/(c) green; each filter write fires its param-correct GET pair.
2. **Predicate over-constraint? NO.** After the tags pair, ZERO `/api/snapshots` requests fire for the rest of the leg (only the 8s background poller); daemon census `status=` GETs = 0 — nothing existed to reject.
3. **Root cause = click locator never resolves (test-side).** Trace action log: the (d) first-chip click locator `dialog[Status filter] >> label.status-menu-item >> hasText=/^active$/` logs "waiting for…" and never resolves (pending at timeout). DOM snapshot #71: shipped markup is `LABEL.status-menu-item` = checkbox + `SPAN` with MAT-ICON ligature `check_circle` + text `" active "` → label textContent = `check_circle active ` — the anchored `/^active$/` cannot match (icon ligature + whitespace). Click never landed → no refetch could fire → TimeoutError @ spec :264←:414. The (d) second-chip wait is race-immune and fine once clicks land.
- **Fix direction (developer, one line)**: unanchored `/active/`, or scope the locator to `SPAN.status-chip-inline` / the checkbox role.

### Lane record
Daemon banner v0.18.5 @ 127.0.0.1:18279; native PG16 :15532 (`/tmp/pg_e2e_snap_2935965` main, `…2936686` diagnostic); ng serve :14199; canary ready; env fenced; ports pre-checked; teardown verified (0 listeners 15532/18279/14199; protected ports untouched; `/tmp/pg_e2e_snap_*` removed; worktree intact + clean, HEAD unchanged, no commits/pushes; main checkout untouched).

### Harness actions (labeled)
Focused leg-3 `--grep "step 3" --trace on` diagnostic (CLI knobs only); gitignored transient outputs cleaned. Census-method note: daemon access log strips query strings — param evidence is trace/browser-side.

### Loop scoreboard (3 runs)
| Run | Commit | Result | Leg #3 failure site |
|---|---|---|---|
| #1 | c7b467444 (v1 spec) | 7/12 | #2,#3,#4,#6,#7 v1-contract rot (whole-leg) |
| #2 | 64b351b8b (rebase) | 11/12 | (c) response-arming race @ :387 |
| #3 (final) | b5cafd3f7 (race fix) | 11/12 | (d) click-locator text-anchor @ :414 — newly-exposed, never executed before |

Pattern: leg #3 is revealing latent spec defects at successively deeper sites as earlier ones get fixed — each fix extends execution reach. One locator line remains.

### Action Needed
- [ ] Developer: one-line (d) locator fix (unanchored `/active/` or SPAN/checkbox-role scoping) → final 12/12 gate re-run
- [ ] Optional pre-gate to avoid a 4th round-trip: focused `--grep "step 3"` leg locally before commissioning the full pack

### Documentation Updated (unstaged; closing giter pass commits)
- [x] RESULTS/2026-10-10-snapshots-v2-daemon-lane-e2e-final-gate.md — this report
- [x] PACKS.md — final-gate section (11/12 @ b5cafd3f7, race fix verified, locator defect named)
- [x] LESSONS/2026-10-10-anchored-text-locator-vs-mat-icon-ligature.md — locator pattern

### Code Changes Summary
None by this lane; worktree clean at close.

### Overall Status
- Pack @ b5cafd3f7: ❌ 11/12 — one locator line from GREEN
- Product: ✅ exonerated across the entire loop (zero product failures in 3 runs; §2.3 wire contract proven; PUT round-trip green)
