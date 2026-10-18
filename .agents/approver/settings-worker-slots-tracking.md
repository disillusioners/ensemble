# Approver Tracking: settings-worker-slots

Plan: Worker-slot sizing — un-hardcode pool sizes into config (env tier effective) + DB-backed managed keys + FE restart badge + NEW agent-facing settings tools for ari/leader (honest restart semantics)
Artifact set: .agents/shared/planning/settings-worker-slots/ — plan-overview.md (entry), phase1..4-plan.md, decisions.md, architecture-recommendation.md
Worktree: /home/nea/ensemble-src-wt-settings-worker-slots | branch feature/settings-worker-slots | base latest @ d0b475466

---

## Iteration 001 — 2026-10-18T08:35Z
Verdict: **APPROVED** (0 blocking, 4 non-blocking notes)
Worker: approve-worker-plan 5fe354fc-312c-4a8d-bacb-d8cbcc035dcb (skill: plan-approval)
Evidence: ~50 code anchors spot-checked in the worktree; all 35 seal shas recomputed (27 green / 8 stale; constants.py pin 64c1dbe3… vs actual 7026ed31…, stale set matched the plan char-for-char); fifth privileged pin confirmed (test_secrets_registration.py:212); caller census re-run (1 production caller api.py:608; 21 test call sites, all explicit); FOUR registry touch-points verified; no-migration claim confirmed (20261018_000001_create_app_settings.sql present); commission coverage complete (env-tier ladder settings_service.py:334-350 + shadowing defect excluded config.py:6068; FE generic registry; agent tools via secrets playbook; scopes/frozen-GET/no-migration all check out).

### Notes (non-blocking, for the developer lane)
1. **D4 cross-test order-dependence residual not named in D6/risk table.** PoolOrchestrator.setup `set_invoke_semaphore_cap()` + RuntimeError guard create two couplings: (a) tests/integration/test_daemon_startup_smoke.py:47 (num_workers=0 → override 1) and tests/integration/test_wc_wake_pure_hang.py:411 (num_workers=1 → override 1) leave `_invoke_cap_override` set with no reset fixture — default alphabetical order (integration before unit) is safe (no test asserts cap outside fixture-protected modules), (b) guard fires only if `setup_worker_pool` runs after a singleton-materializing test — all known ones reset. Both caught loudly by phase4 t1 "0 new failures vs base". Suggested: extend the F3 fixture requirement to name those two modules, or note the residual in the risk table.
2. **Anchor micro-drifts** (plan already mandates edit-time re-grep): routers/settings.py `_settings_service` at :143 (plan says :142); arch Ruling 4 cites config.py:4070-4073 but the operative claim verifies at config.py:585 (`class LimitsConfig`); secrets_tools RuntimeError raises at :258/:263 vs cited :257-268/:261-263.
3. **`ge=1` floor vs cap ≤ pool−1 invariant at pool=1** (max(1,0)=1 = pool size; free-worker guarantee void) — already documented in overview risk 5; acceptable for restart-required v1.
4. **D3 "ONE commit" wording vs phase1 task granularity:** D3 requires field (settings_service.py:116) + computation (:144) + pin (tests/test_daemon_settings_api.py:455) in one commit; phase1 places the pin work in the "Phase-1-owned test updates" section without an explicit commit boundary — both land in P1; developer should keep them in one commit to honor D3 literally.

### Carried observations (not gaps)
- Non-default pytest invocations pairing a singleton-materializing test before an unprotected pool-booting test — default order safe; phase4 t1 gate would surface any collision.
- Runtime pydantic behavior under partial `limits:` yaml + env var — correctly assigned to runtime verification by the plan.
