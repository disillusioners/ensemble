# Phase 4: Full-suite verification + seal re-check + merge to latest (NO promote)

## Objective

Prove the slice end-to-end against base d0b475466, re-verify seal + env discipline on the final tree, pass the reviewer council, and merge to `latest` — explicitly WITHOUT a release/promote ceremony.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Full backend suite: `pytest` (shard discipline per prior-set experience: `workers=1` shards, per-test timeouts honored; the resolved-path tests use modest 8/3 overrides so shard contention stays low) | P1-P3 | 0 new failures vs base d0b475466 (pre-existing failures, if any, must be named and base-proven) |
| 2 | Full FE suite: jest (frontend/ local toolchain only — NEVER bare `tsc`/`npx tsc` outside frontend/ per the 2026-10-03 incident convention) | P3 t4 | 0 new failures |
| 3 | Pre-merge seal verification (re-run of the D8 targeted check on the FINAL tree): seal test red on EXACTLY the 7 OTHER stale pins {install-mermaid-cli.md, ari/workflow.md, approver/rule.md, developer/rule.md, planner/rule.md, reviewer/rule.md, tidier/rule.md}, GREEN on `daemon/constants.py` — reds 8→7 (corrected, iteration 002 — council B1; constants.py was ALREADY stale at base); `git diff d0b475466 -- tests/test_chart_image_delivery_e2e.py` touches ONLY the :1346 constants entry | P1 t8 + P2-P3 | Exactly that state; any other red/green arrangement or extra pin diff = stop and re-derive |
| 4 | Scope + discipline checklist: (a) `grep -rn "LIMITS_WORKER_POOL_SIZE\|LIMITS_CHAT_WORKER_POOL_SIZE" tests/conftest.py` = 0; (b) `grep -rn effective_max daemon/` = 0 OUTSIDE the unrelated `blueprint_matcher.py:189/:228` locals (allowlist those two — O3, iteration 002); (c) `ls daemon/migrations/versions/` — NO new `.sql` vs base; (d) registry = exactly 3 managed keys; (e) `git diff d0b475466 --stat daemon/api.py daemon/routers/settings.py daemon/auth/scopes.py daemon/tools/_auth.py` = empty; (f) sealed `.md` files untouched (`agents/ari/workflow.md`, `agents/ari/rule.md`, `agents/leader/rule.md` — exactly 3, O7); (g) `test_frozen_tool_name_discovery` green (BOTH `DYNAMIC_TOOL_NAMES` and `KNOWN_TOOL_NAMES` carry the 3 new names — F2) | P1-P3 | All greps/diffs clean — the explicit exclusions held |
| 5 | Review gate + merge: hand to reviewer — reviewer convenes the governor council via `convene_council_with_skill(councilor_skill="code-review", max_councilors≤4)` (agents/reviewer/workflow.md idiom, same as the prior commission); address findings; then commit + merge to `latest` with the D8 attribution in the PR/merge description. **NO promote, NO version bump, NO release staging** | 1-4 | Council verdict: no 🔴 critical; merge lands on `latest`; merge description carries the constants.py re-pin justification (git-log attribution from P1 t8) |

## Tests

This phase re-runs every phase gate on the final tree; it authors no new tests. The load-bearing pre-merge assertions are the seal state (t3) and the exclusion greps (t4).

## Runtime-verification residuals (tester phase — appended, iteration 002, per council review)

The council's stated residuals become RUNTIME-verification items for whoever executes this phase (static analysis cannot close them):

1. **Pydantic partial-kwargs env fidelity:** boot the daemon (dev boot or harness lifespan) with a PARTIAL `limits:` yaml — i.e., NO yaml literal for the new keys — plus `LIMITS_WORKER_POOL_SIZE` set in the env, and verify post-boot that `config.limits.worker_pool_size` carries the env value after `apply_db_settings_overrides` (the env tier must survive the pydantic init-kwargs merge even when the limits section is partially populated). Complements the ladder unit tests with a real-boot observation.
2. **Proposed-test runtime behavior:** run the NEW resolved-path tests (config-object injection + autouse reset fixture) in varied orders and shard splits to prove no order dependence (F3) and no vacuous pass (F4) — minimally: (a) run the cap pin twice in one session (second run must still pass post-reset-fixture); (b) run the resolved-pool sibling with the sentinel resolution temporarily reverted to the constant (must FAIL — proves the test actually exercises the config seam).
3. **FE GET scope:** with auth ON, verify `GET /api/settings/daemon` returns the two new keys only for `settings:read`-holding principals (403 otherwise), and the FE Daemon section renders/hides accordingly — confirms the registry-driven render path respects the existing scope gate at runtime.

## Edit-time notes for the developer (optionals O1-O8 — ALL landed in the planning docs during iteration 002; re-verify at edit time)

- O1 `manager.py` semaphore anchor = :562 (docs updated; re-grep at edit time).
- O2 settings wiring block = api.py:2582-2588 (setter call at :2588; reconcile exact line at edit time).
- O3 `effective_max` grep allowlist = `blueprint_matcher.py:189/:228` (encoded in overview criterion 5, phase1 t2, phase4 t4b).
- O4 phase legend added to plan-overview (arch A–D ↔ plan 1–4).
- O5 phase1 t5/t6 "parallel-safe" tags corrected to ordering constraints.
- O6 manager.py:8367 stale docstring cites (api.py:369→:608; one-test-caller→~20) — fix inside phase1 t4.
- O7 sealed-`.md` count = 3 (D8); boot line carries ONLY the chat count (phase1 t3 + D6 row 8 reworded).
- O8 symbolic re-confirmation of the three chat_source_* files: DONE @ base (zero literal 5/2 pins — decisions D6 row 11 + arch row 11).

## Coupling

- **Loose with Phases 1-3** — consumes every contract; runs after all three.
- Sequencing: t3 requires P1 t8's commit; t4 requires the full diff; t5 requires everything.

## Risks

- Vacuous green on resolved-path tests — guard: they were written first against the pre-change tree in P1 (fail-before-implement discipline where feasible).
- Scope creep in review (council suggesting live re-arm, scheduler lane, or more keys) — out of scope per overview; feedback goes to future-commission notes.
- Shard contention on the shared box during the full run — workers=1 shards + per-test timeouts; no test pool size exceeds current prod max.

## Exit Criterion

Full backend + FE suites green vs base, seal tripwire in the exact adjudicated state (red-8 / green-constants), all exclusion greps clean, council approved with no critical findings, branch merged to `latest` — and explicitly NO promote performed.
