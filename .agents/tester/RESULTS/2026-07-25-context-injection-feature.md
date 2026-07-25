# Test Report: Per-Agent Context Injection Feature

**Date:** 2026-07-25
**Branch:** `feature/context-injection`
**Commits:** `231253a9` (feat) + `5d8ec1f6` (harden)
**Project:** agents-ensemble
**Worker instances:** 4 × `load_skill="test-pack-execution"` (parallel)

## Scope Decision

> **Full requested (implicitly, via "broader regression"); change is SMALL/ISOLATED** → reduced scope to 4 relevant packs.
> Change touches **5 files / +200 lines**, all additive and opt-in:
> - `daemon/registry.py` (+5) — new optional `context_injection: bool` field on `AgentMetadata`
> - `daemon/services/instance_lifecycle.py` (+50) — new `append_context_injection` appender in `_apply_post_cache_appends` (flag-gated, XML fence, no-context guard, fail-open)
> - `agents/leader/meta.json` (+1) — flag enabled
> - 2 test files (+146)
>
> **Running:** 4 packs (feature tests, c2 lifecycle regression, orchestration regression, core registry regression).
> **Skipped:** the other ~190 packs (no architecture/DB-lock/concurrency/job-processing change).
> **Full suite NOT warranted** — small, isolated, prompt-composition-only change.
> **Release Gate NOT warranted** — not a big/critical/architecture change.

## Summary

| Pack | Result | Passed | Failed | Skipped | Runtime |
|------|--------|--------|--------|---------|---------|
| A: Feature tests (ad-hoc) | ✅ PASS | 20 | 0 | 0 | 0.75s |
| B: c2_messaging_lifecycle | ✅ PASS | 69 | 0 | 14 | 6.77s |
| C: services_orchestration_regression | ✅ PASS | 25 | 0 | 14 | 6.45s |
| D: core_unit_test (registry) | ⚠️ FAIL (pre-existing only) | 685 | 41 | 0 | ~24s |

**Aggregate:** All 4 packs in-scope PASS. Pack D reports FAIL (exit 1) but **0 NEW failures** — all 41 are pre-existing, verified empirically by the worker (re-ran on parent commit `fa3f68a0`; identical failures). Feature introduced **zero regressions**.

## Feature End-to-End Verification — ✅ ALL BEHAVIORS CORRECT

| # | Claimed behavior | Covering test(s) | Status |
|---|---|---|---|
| 1 | `context_injection: true` → context injected into prompt | `test_post_cache_appender_injects_context_when_enabled`, `test_post_cache_appender_resolves_child_context_key` | ✅ PASS |
| 2 | `context_injection: false` / absent → NOT injected | `test_post_cache_appender_skips_context_when_disabled`, `test_post_cache_appender_handles_none_agent_meta`, registry wiring tests | ✅ PASS |
| 3 | `<injected_project_context>` XML fence present when injected | `test_post_cache_appender_includes_security_fence` (both fence tags + read-only notice) | ✅ PASS |
| 4 | "no context yet" placeholder NOT injected as real context | `test_post_cache_appender_handles_empty_context` | ✅ PASS |
| 5 | Fail-open: `get_shared_context()` errors don't break spawn | `test_post_cache_appender_swallows_exception` (RuntimeError side_effect → prompt intact) | ✅ PASS |

**No claimed behavior lacks coverage.** All 5 directly asserted and green.

Bonus coverage verified: appender does NOT fetch critical notes (`test_post_cache_appender_does_not_fetch_critical_notes`); child instances resolve tree-root `context_key` (`test_post_cache_appender_resolves_child_context_key`).

## Pack D Failure Classification (pre-existing, not feature regressions)

| Cluster | Failures | Classification | Root cause |
|---------|----------|----------------|------------|
| Migration SQLite syntax | 39 | PRE-EXISTING (verified on `fa3f68a0`) | `DROP CONSTRAINT IF EXISTS` invalid on SQLite — migration `20260714_000001` from commit `843e2c34` (2026-07-14, 11 days before feature). Already documented in PACKS.md summary line 6. |
| test_agents_api fixture isolation | 2 | PRE-EXISTING (verified on `fa3f68a0`) | Registry `BASE_DIR` not patched by `client_with_temp_agents` fixture. |
| **NEW (context_injection)** | **0** | — | Feature is clean ✅ |

**Baseline drift note:** PACKS.md baseline was 10 failures (2026-07-12); now 41 because commit `843e2c34` (2026-07-14, post-baseline, pre-feature) added 31 pre-existing migration failures. The PACKS.md summary line already reflects "39 pre-existing SQLite-path failures" from the 2026-07-23 full-suite run.

## ensure.md Validation (Core, scoped)

| Requirement | Status | Notes |
|-------------|--------|-------|
| **Critical: No regressions in changed packs** | ✅ PASS | All 4 in-scope packs PASS (Pack D's failures are pre-existing, not regressions — verified empirically) |
| Critical: Deadlock/concurrency integrity (`concurrency_atomic_unit_test`) | ⏭️ OUT OF SCOPE | Change touches no concurrency/lock/DB-transaction paths |
| Critical: No sync DB calls on event loop | ⏭️ OUT OF SCOPE | Change adds no DB helpers on the async path |
| Critical: `dev.sh` graceful shutdown flag | ⏭️ OUT OF SCOPE | Static check, `dev.sh` unchanged by feature |
| Release Gate | ⏭️ NOT WARRANTED | Small/isolated change, not big/critical/architecture |

**No ensure.md contradictions found.** All in-scope requirements validate cleanly as packs with the dual-layer timeout.

## Quick Fixes Applied
None. No failures attributable to the feature. No commits made by workers.

## Issues / Edge Cases Discovered
- **None feature-related.** The feature's security posture (XML fence, no-context guard, fail-open) is all explicitly tested and green.
- **Pre-existing (out of scope, FYI):** the 39 SQLite-migration-syntax failures and the 2 `test_agents_api` fixture-isolation failures predate this feature and are tracked. They are not blockers for this PR.

## Documentation Updated
- [x] `PACKS.md` — registered the ad-hoc feature pack as `context_injection_feature_test`
- [x] `RESULTS/2026-07-25-context-injection-feature.md` — this report

## Overall Status

| Dimension | Status |
|-----------|--------|
| Feature tests | ✅ PASS (20/20, all 5 behaviors verified) |
| Appender-chain regression | ✅ PASS (exact baseline match) |
| Lifecycle/context-usage regression | ✅ PASS (+4 new tests, no regressions) |
| Core registry regression | ✅ CLEAN (0 new failures; 41 pre-existing verified) |
| ensure.md (Core, scoped) | ✅ PASS |
| **Testing Complete** | ✅ **READY — feature is safe to merge** |
