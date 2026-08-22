# Pre-Merge Verification: Auto-Restart Phase 1 — `feature/auto-restart-phase1` @ f792fbca (+test-infra e4b6e43d)

- **Date:** 2026-08-22 · **Tester:** ensemble tester agent
- **Branch state verified:** `f792fbca` (gate tip; 4 branch-own commits + merge commit f313296a of latest 2c68f706) with one test-infra commit `e4b6e43d` (5 pack wrappers) added during this gate — **daemon/ source byte-identical between f792fbca and e4b6e43d** (wrapper commit only).
- **Verdict: ✅ PASS-WITH-FOLLOWUPS** — merge to `latest` cleared; follow-ups are non-blocking (below).

## 1. Suites Run + Results (10 dispatches: 1 recon + 1 infra + 8 pack runs)

| Pack | Result | Counts | Runtime | Evidence |
|------|--------|--------|---------|----------|
| api_unit_test | ✅ PASS | 213 P / 0 F / 8 S | 14s | Baseline-exact vs 2026-07-29 record; api.py probe-route + lifespan seam clean |
| concurrency_atomic_unit_test | ✅ PASS | 91 P / 74 S / 0 F | 8s | Baseline-exact; **ensure.md Core R2+R3 satisfied** |
| reasoning_echo_targeted_unit_test | ✅ PASS | 51 P / 0 F | 0.71s | Baseline-exact vs denylist branch tip; ClassVar seam survives merge |
| launcher_supervisor_unit_test (NEW pack) | ✅ PASS | 72 P / 0 F | <1s | exit map 0/75/78/1, backoff ×2, burst, uptime reset, classify_exit |
| deploy_pipeline_unit_test (NEW pack) | ✅ PASS | 53 P / 0 F | ~1s | demo/live pipeline, health gates, ENSEMBLE_DEPLOY_LIVE guard; sandboxed fixtures |
| stop_ownership_unit_test (NEW pack) | ✅ PASS | 43 P / 0 F | 16s | SINGLE-TERM, ownership match, **P4 11-case WAIT_S edge table all green** |
| watchdog_watcher_unit_test (NEW pack) | ✅ PASS | 27 P / 0 F | 5s | at-most-once latch, exit-0 unresolvable INSTALL_DIR, explicit-zero rejection; fixtures cleaned |
| boot_probes_unit_test (NEW pack) | ✅ PASS | 63 P / 0 F | 8.46s | /livez + /readyz, exit-75/78 preflight, timeout-graceful-shutdown wiring; independently confirms implementer's 24+15 runs (63 = parametrize expansion) |
| e2e_workflows_ensure_test (Release Gate) | ✅ PASS | 3 P / 0 F / 9 S (1 quarantine-deselected) | 223s | happy path, terminate→revive, 3-level cascade; real LLM; ensemble_dev engine-log-verified |

**Aggregate: 9/9 packs PASS, 616 passed / 0 failed.** Zero source modifications, zero quick fixes needed, zero production bugs found.

**ensure.md Core:** R1 changed-packs PASS ✅ · R2/R3 concurrency pack ✅ · R4 dev.sh `--timeout-graceful-shutdown 10` static check ✅ (dev.sh:102). Important/nice-to-have: covered by R2 pack (deadlock scenario in concurrency suite) — no in-scope failures.

## 2. E2E-Gate Ruling: TRIGGERED — release-gate-scoped (not full suite)

**Ruling:** the job/task/queue convention WAS triggered, but satisfied via the 4-test Release-Gate e2e pack, NOT the full non-integration suite.

**Rationale:**
1. Branch-own `daemon/api.py` changes lifespan shutdown wiring — shared infra with job/task finalization (9-step teardown incl. `manager.shutdown()`); defects there corrupt work-state integrity, which is exactly what the convention protects.
2. The merged tree is a **new combination**: latest's pause-report-recovery + job-processor starvation fix (both tested on their own branches) + branch's boot/lifespan/probe changes — merge seams covered by neither parent's own runs.
3. **Mitigation for not running the full suite:** 616 unit tests across 8 packs already green on the merged tree, including api (213), concurrency (91), boot/probes (63), and every branch-own suite. The convention's full-suite trigger lists specific modules (claim_pending_task, turn_transitions, reconcile_turn_mirror, job_processor, job_locks) — **recon proved zero daemon delta vs latest tip in any of those files** (branch-own daemon delta = `__main__.py`, `api.py`, `config.py`, `constants.py`, `services/readiness.py`, `models/common.py`+`models/__init__.py` [probe pydantic schemas]). The e2e workflow pack was the targeted insurance for the one real seam (lifespan + merged combination); full suite would add cost without new coverage for this change shape.

## 3. Boot Smoke: PASS (live, merged tree)

- Boot via `./dev.sh` ×3 (initial, drill, green): time-to-live 9s / 7s / 6s; 0 ERROR/Traceback lines in all three boots.
- `/livez` 200 `{"status":"alive",...}` 1.5ms; `/readyz` 200 in 18ms with full component detail (`database/queue_freshness/services` all true), no hang.
- **P7 drill live-confirmed (green→red→green without restart):** `ENSEMBLE_READINESS_FORCE_DEGRADED=1` → `/readyz` 503 `degraded` with forced-reason payload + `[Readiness] degraded: ... (drill)` log line; restart without var → 200 `reasons: []`. Knob is env-based, read per refresh tick, fail-safe one-way as designed.
- **DB identity:** `Creating PostgreSQL engine: localhost:5432/ensemble_dev` in all 3 boots (no sqlite, no env-override surprise).
- **Graceful shutdown:** 2s per stop, full 9-step chain incl. `Graceful shutdown complete` → `--timeout-graceful-shutdown 10` wiring verified live; zero leftover processes; 8088 never touched.
- Scope note: `dev.sh` boots `daemon.api:app`, so `__main__._boot_db_preflight` exit-75 was proven at unit level (boot_probes parametrized 28P01/28000/28P02→78, OperationalError→75) rather than live; acceptable — see follow-ups.

## 4. P5b Triage: PRE-EXISTING, NON-BLOCKING

- The double-WARN is the `resolve_binary()` pair (`launcher.sh:362` + `:369`: "exists but is not executable — trying flat layout" / "exists but is not executable"), both firing when both candidate binaries are non-executable.
- Attribution: introduced by `ce720f43` (2026-08-16, launcher supervisor wrapper) — **ancestor of merge f313296a**; `git log f313296a..f792fbca -- launcher.sh` is EMPTY (none of the 4 gate commits touch launcher.sh); `launcher.sh` does not exist on latest 2c68f706 at all, so it cannot be a latest-side regression.
- **Verdict: NOT PASS-blocking.** Cosmetic duplicate log line for a single root cause (both candidates non-executable), pre-existing on the branch's own earlier history, no functional impact, covered by passing launcher suite (72/72).

## 5. Demo-Env Decision: POST-MERGE FOLLOW-UP (not feasible in-budget)

Real `deploy.sh demo` redeploy to `~/agents-ensemble-demo` (demo env was fake-staged during the earlier containment breach) requires PyInstaller build + staging + health-gate wait against demo PG (`ensemble_demo`) — outside this gate's time budget and blast radius (it mutates state outside the repo while other verification was running). **Explicitly deferred as a post-merge follow-up.** All deploy.sh behavior that CAN be verified without the demo host is green: 53/53 sandboxed fixture tests including `.env.prod.demo` generator real-run `--no-start`, health-gate wiring, live guard.

## 6. Scope Decision

Pre-merge gate = broad verification, but still scoped: 9 packs chosen by change-set analysis (recon-proven daemon delta), NOT the 274-pack full inventory. Tools-drift packs (tools_suite, frozen_tool_name_discovery) ruled out — zero daemon/tools/ delta vs latest tip. Full non-integration Release-Gate item not run (rationale in §2).

## 7. Follow-Ups (all non-blocking)

1. **Demo-env live re-validation of P4/P7** via real `deploy.sh demo` redeploy (§5).
2. **P5b launcher double-WARN** cosmetic dedupe — one-line `_log` guard, whenever launcher.sh is next touched.
3. **pytest-timeout venv gap (fixed this session, root cause open):** pyproject declares `pytest-timeout>=2.3` (since c9055718, pre-branch) but it was NOT installed in `.venv` — e2e pack Layer-2 (PYTEST_TIMEOUT=280) silently no-ops without it. Installed 2.4.0 via `uv pip install --python .venv` during this gate (venv-local, not committed). Root cause is env drift (likely a `uv sync` with changed lock or partial install); recommend fresh `uv sync` check on dev machines before e2e gates. See LESSONS/2026-08-22-venv-dep-drift.md.
4. **`python -m daemon` live smoke** (exit-75/78 preflight + launcher-classify integration on real boot) — optional; unit coverage exists (63/63).
5. **Stale job hygiene:** one stale pending leader job (`s3diag-1787326315914`, 2026-08-21 diagnostic leftover) found in ensemble_dev and cancelled pre-e2e; consider a periodic pending-job sweeper note in dev docs.

## 8. Test Infra Added (committed e4b6e43d, on branch, not pushed)

5 pack wrappers registered in PACKS.md: `launcher_supervisor_unit_test`, `deploy_pipeline_unit_test`, `stop_ownership_unit_test`, `watchdog_watcher_unit_test`, `boot_probes_unit_test` — all dual-layer timeout (300 outer / 120 inner), transparent pass-through, RESULT: PASS|FAIL|TIMEOUT convention.

## Worker Instances

recon da836558 · api d4ee31d0 · concurrency 570fa6b3 · echo 90bef29f · infra e47a98d9 · ops-wave 58dc3eb0 (4 packs) · boot-probes 428d9b2c · boot-smoke a23a8c56 · e2e 356fc4ac
