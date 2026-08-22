# Independent Verification: Auto-Restart Phase 1 Follow-Ups — `fix/auto-restart-phase1-followups` @ c3cf909c

- **Date:** 2026-08-22 · **Tester:** ensemble tester agent (independent verification of own pre-merge follow-up list)
- **Branch state verified:** `c3cf909cf7f2b541dbbb0825d2a9dbafb472a23e`, linear on latest `5e33789d` (a24bf643 → e326b731 → c3cf909c; parent chain rev-verified). NOT pushed.
- **Verdict: ✅ PASS** — all pre-merge follow-ups (P4, P5b, P7, exit-75 smoke, venv drift) closed with evidence; zero material gaps; Phase 2 risks minor (§4).

## 1. Verdict Per Follow-Up

| Follow-up (pre-merge §7) | Verdict | Basis |
|---|---|---|
| **P4** — demo-env WAIT_S edge validation | ✅ PASS | DevOps report §C: all 14 scenarios (a–e, f1, f2, g, h1, h2, i + j1–j4 collapsed to one row ×4 values) + default, expected-vs-actual with verbatim resolution lines, case-for-case match vs unit pack `tests/test_stop_ownership.sh` §7; 3 genuine stops, SINGLE-TERM honored, live pids identical all checkpoints |
| **P5b** — launcher double-WARN dedupe | ✅ PASS | Fix = exact one-line guard prescription, over-suppression proven impossible (reachability argument); §9 9d/9e are genuine regression locks; 74/74 pack; **mutation-killed**; deployed demo launcher sha-identical to repo tip (line 369) |
| **P7** — readiness drill green→red→green in demo | ✅ PASS | §B: 5-row timestamped transition (15:36:31 → 15:36:49 → 15:37:27): 200/`reasons:[]` → 503/forced-reason payload + `[Readiness] degraded` log line → 200/`reasons:[]`; `/livez` stayed 200 (independence); one-way fail-safe confirmed; restore-via-restart matches `readiness.py:50-67` contract |
| **exit-75** — `python -m daemon` live smoke | ✅ PASS | §D: **actual captured exit code 75** (not a claim), wall 1.11s, unreachable-PG simulation (127.0.0.1:54329, throwaway DB never created), sandboxed (port 8377, throwaway data dir), post-conditions verified, live pids unchanged; unit regression 63/63 re-confirmed on this branch |
| **venv/uv-sync** — pytest-timeout drift | ✅ PASS (clean now) | `.venv` re-check: pytest-timeout **2.4.0 present + importable** (`uv pip list`; pip-show empty is uv-manager artifact); Layer-2 functional in the boot_probes run; root cause stays env-hygiene (LESSONS/2026-08-22-venv-dep-drift.md), `uv sync` before e2e gates remains the recommendation |

## 2. Evidence Audited (DevOps `.agents/devops/RESULTS/2026-08-22-ar-phase1-followups-demo-validation.md`, 157 lines, end-to-end)

Checklist a–h: **8 PASS / 0 FAIL / 0 UNCLEAR**. Key confirmations:

- **(a) P4 table complete** — count reconciliation: DevOps 12 rows + default = 14 scenarios = strict superset of unit pack §7; pre-merge "11-case" label counted lettered cases only (j treated as explicit-override contract) — definitional, not a coverage gap. Expected values match pack assertions case-for-case (e.g., f2: 599+10=609 → 600 cap ✓).
- **(b) P7 real transition** — actual state change with timestamps AND payloads, not a snapshot; per-tick env knob + restart-to-restore nuance reconciles with repo comment (pre-merge "without restart" applied to the in-process dev-harness refresher).
- **(c) Health gates** — `/livez` green at daemon-uptime 1.70s (deploy) / t+17.09s (restart run), both ≪60s budget; `/readyz` green 08:08:11.44Z ≪120s; timestamps internally coherent to the second (payload checked_at ↔ engine marker ↔ uptime).
- **(d) exit-75 transcript** — captured exit code + elapsed + failure mode; isolation correct (neither demo nor live mutated).
- **(e) Deployed-copy P5b** — grep at line 369 + 25,346 bytes + provenance from `e326b731`; **independently strengthened: shasum `bc353341…` identical repo↔deployed**.
- **(f) Environment identity** — demo path/port/DB asserted at 4 points + engine markers `ensemble_demo` in every visible boot; live pids `30054 31150` quoted at 4 checkpoints + summarized for 3 stops = **6 checkpoints** as expected.
- **(g) Final state** — all §E claims independently reproduced live (§3 below).
- **(h) Minor notation notes only** (none material): line 22 live-guard via command history; "BOTH boots" undercount (~5 boots, all `ensemble_demo` so identity unaffected); stop-time live-pid assertions summarized to one line; §E pid pair unquoted in report (now live-verified); `.launcher-state` relic claim asserted not evidenced (immaterial); §D artifact-removal is a ✓-claim. Demo `.env` restoration **verified**: no `ENSEMBLE_READINESS_FORCE_DEGRADED`, no `DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS`.

**Overall evidence quality: HIGH** — verbatim quotes, second-level timestamp coherence, two self-disclosed harness imperfections handled transparently, every current-state-checkable claim reproduced.

## 3. Independent Runs Performed (this session, branch @ c3cf909c)

| Run | Result | Detail |
|---|---|---|
| `launcher_supervisor_unit_test` pack | ✅ PASS **74/74** (<1s) | Baseline 72 + exactly 9d/9e (assert-count verified 68+4 → 70+4 across revisions); all 12 sections green |
| `boot_probes_unit_test` pack (exit-75 unit baseline) | ✅ PASS **63/63** (8.58s) | Baseline exact-match vs pre-merge 2026-08-22; zero drift; Layer-2 timeout functional (pytest-timeout present) |
| **Mutation check** (pre-fix launcher, /tmp copy) | ✅ **MUTATION-KILLED** | 9d FAIL exit 81 (= two-WARN detection) on guard-less `a24bf643^` launcher; control 9d PASS on fixed copy → guard is the sole cause; repo byte-identical before/after (`git status --porcelain` clean); 9e PASS on pre-fix as expected (flat-only was never the defect) |
| Demo read-only probes | ✅ 200 / 200 | `/livez` 200 `{"status":"alive","uptime_seconds":867.7,"version":"0.10.5"}`; `/readyz` 200; listener pid **12245** `ensemble-prod`, cwd/exe under `~/agents-ensemble-demo`, chain 12146→12158→12245 boot 15:39:02 — **exact match to report §E**; NOT live, NOT repo dev.sh |

Commit-level audit: `a24bf643` launcher.sh-only single guard hunk (matches pre-merge prescription verbatim); `e326b731` tests-only (9d/9e + cleanup-list hygiene); `c3cf909c` evidence-doc only (+157 lines, no code); cumulative launcher.sh delta = the one guard hunk.

## 4. Gaps / Risks for Phase 2 (all minor, non-blocking)

1. **Launcher retry loop not observed end-to-end live**: the exit-75 smoke proves the daemon exits 75 and the launcher *message* says it will retry; the full real loop (exit 75 → supervisor actually respawns with capped backoff → recovery) remains unit-level (launcher suite) + sandboxed. Phase 2 (auto-restart hardening) should include one live tempfail→recovery cycle on demo.
2. **Exit-78 (config error) path** remains unit-covered only (63/63) — consistent with pre-merge "optional" framing; fold into Phase 2 live smoke if cheap.
3. **P7 drill on deployed daemon requires restart to restore** (env knob read per refresh tick; `readiness.py:50-67`) — document in Phase 2 drill runbook so green-restore steps aren't assumed instant.
4. **venv drift root cause open** (hygiene): run `uv sync` before e2e gates; Layer-2 currently functional on this machine.
5. DevOps report notation: "BOTH boots" undercounts (~5); stop-time live-pid assertions summarized — cosmetic; suggest verbatim pid lines in future reports.

## 5. Constraint Compliance

Live/prod (`~/agents-ensemble`, :9797, prod DB, `ENSEMBLE_DEPLOY_LIVE`) never touched by tester or any worker. Demo (:7979) GET-probes + read-only inspection only — no restart/redeploy/reconfig; daemon verified running continuously since 15:39:02 (+07). Port 8088 never contacted. All mutations confined to /tmp (cleaned). No pushes.

## 6. Worker Instances

commit-audit 2280f361 · launcher-pack fd145066 · boot-probes 88ad8ece · mutation b6bc386d · demo-audit 22ba6edd
