# Reload-Tabs Regression Fix — Verification Report

Date: 2026-08-19 · Branch `fix/reload-tabs-regression` @ 4f5a254a (fix UNCOMMITTED, 2 files) · Verdict: **SHIP** ✅

Instance IDs: recon 38e923bb · spec-author 2e44ae74 · jest d168b443 · build 32249344 · fe-boot 715c3aeb · e2e-core×2 ad835bdb/9013f9e4 · e2e-reg×3 7636c7f4/b085217f/2a5a13e3 · reload-tabs a726e1b6

## Original user symptom (verification target)
"Opened tabs/projects are not remembered when I reload the page. Before it was remembered."

## Fix under test
`this.tabStateService.restoreState()` in App constructor (app.ts:302), AFTER instancesViewState.restoreState() (:287), BEFORE syncDetailVisibility() (:309) and NavigationEnd subscription (:310-317) — hydrate-before-NavigationEnd invariant. Diff: app.ts +15 / app.component.spec.ts +353. `tab-state.service.ts` UNTOUCHED (service semantics already correct; only call-site was missing).

## Scope Decision
Frontend-only change, 2 files, no architecture change → scoped run. Executed: frontend Jest full suite, prod build, 2 instances-state e2e packs (user-named), + NEW reload-tabs pack (authored for the symptom). Release-gate e2e NOT triggered (no job/task/queue files — ensure.md convention). Backend packs not in scope (zero backend files touched).

## Pack matrix

| Pack | Result | Evidence |
|------|--------|----------|
| frontend_jest_regression | ✅ PASS | 2055/2055, 57 suites, 8.7s. +7 delta = dev's regression tests (incl. L933 fix-path, L997 reverse-verification CONTROL that reproduces the pre-fix clobber, L1103 static source-ordering canary) |
| frontend_prod_build | ✅ PASS | 5.82 MB initial — exact base parity, 178 kB under 6 MB maximumError gate; all warnings pre-existing |
| instances_state_e2e_core | ✅ PASS 4/4 | Isolated run (feature-interaction scope item: detail→Plan→back restores same instance, draft+scroll, DOM identity, SSE net=0). First run FAILed → triaged as MY parallel-pack interference (two browser packs on one ng serve), isolated re-run green; see LESSONS |
| instances_state_e2e_regression | ⚠️ functional PASS 5/5 / pack-hygiene FAIL | ALL functional asserts passed per-test (R6 reload-restore ×2 runs; R2/R4/R5/terminate via --grep evidence after serial cascade). Pack result of record: FAIL (environmental) |
| reload_tabs_e2e (NEW, spec ad35bf68) | ⚠️ functional PASS 4/5 + partial | R-TAB-1 (3 tabs → reload → ALL restored + localStorage intact) ✅; R-TAB-3 (cold deep-link ADDS tab, persisted 3 survive, activeTab switches) ✅; R-TAB-4 (fresh browser → [All] only, stable across reload) ✅; R-TAB-5 (detail→Plan→back: same URL, same activeInstanceId, same DOM node) ✅ all interaction asserts; R-TAB-2 browser-UNEVIDENCED (see Gap) |

## The environmental noise (affects both e2e packs, NOT the fix)
Deterministic (2 runs, identical): 2-3× per page load `Framing 'https://plane.ensem.dev/' violates CSP "frame-ancestors ... http://localhost:8079 http://localhost:9797"` — allowlist lacks dev port :4199. Always-mounted Plane iframe (PLANE_BASE_URL set). Serial-mode specs cascade-bail on the hygiene tail → evidence recovered via no-modify --grep runs. Contrast: closure cycle 2026-08-18 recorded PASS 5/5 → environment delta between cycles, not repo code. Root fix (owner): add `http://localhost:4199` to frame-ancestors allowlist (one line, nginx-side) — or extend the spec's console filter.

## Gap
R-TAB-2 (detail-URL reload no-clobber — the exact original bug path) never ran its clobber asserts in a browser: serial cascade + alone-precondition (needs R-TAB-1's 3-tab state). Compensating evidence: (1) Jest L933 exercises the identical branch (cold detail boot, tabExists-hit → addTab NOT called, persisted [All,A,B,C] survive); (2) L997 CONTROL reproduces the clobber without the fix; (3) R-TAB-3 live-proves the same boot-time mechanism (constructor restoreState → F3) on the tab-miss branch. Unblocking R-TAB-2 requires any of: CSP allowlist + re-run / filter extension / spec relaxation — all file modifications, forbidden by the no-fix brief.

## ensure.md (in-scope)
Core Critical #1 (no regressions in changed packs): PASS with annotation — scoped packs green modulo environmental CSP hygiene; Core #3-#4 + Release Gate: N/A (frontend-only change; no job/task/queue files). No contradictions found. QUARANTINE.md: 1 pre-existing unrelated skip.

## Action needed (leader/user, non-blocking for this verdict)
- [ ] CSP allowlist: add `http://localhost:4199` to plane.ensem.dev frame-ancestors (restores all console-hygiene gates in dev e2e)
- [ ] After allowlist lands: re-run `reload_tabs_e2e` as-is → expect 5/5 full evidence incl. R-TAB-2
- [ ] Commit the fix (tree is uncommitted; spec already committed ad35bf68)

## Overall Status: SHIP ✅
Original symptom live-verified fixed; zero functional failures across 2,055 Jest + 9 e2e functional tests + 4/5+partial new-pack scenarios; build parity exact; feature interaction intact.
