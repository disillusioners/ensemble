# Blast-Radius Leg — Tier-1 Gate Recipe (project canon)

Tier 1 is the DEFAULT regression gate for feature lanes: a **scoped dual-side FAILED-list diff over the declared blast radius**, ≤ 30 min HARD, fail-loud. Canonical, deterministic recipe — load it from `.agents/tester/skills/blast-radius-leg/`, never discover it via `skill_search` (the G2 worker never found the cheaper runner and burned 9h32m; that failure mode is why this file exists).

- Tier contract: `.agents/tester/rules/ensure.md` § Gate Tiers.
- Digest comparison: `.agents/tester/skills/failed-digest-equality-gate/`.
- Escalation to Tier 2 is MECHANICAL (core-seam path-prefix list in ensure.md), never a judgment call.

## (a) Derivation (generated) → declaration (tester-owned)

1. `git diff --name-only <base>...HEAD` → changed files → changed modules (directory mapping).
2. Referencing test files — three discovery passes, unioned:
   - **Import/reference scan**: grep the tests tree for the changed modules' import paths, class/function names, tool/category names, migration prefixes.
   - **Directory mapping**: `tests/<family>/` mirroring `daemon/<module>/` (e.g. `daemon/services/job_queue_service.py` → `tests/job_queue/`).
   - **Pin packs**: PACKS.md packs registered for changed tools/categories (the pin-ceremony packs).
3. Result = **CANDIDATE set**. The tester declares the FINAL set in the gate report — derivation is generated, declaration is tester-owned. An executor never silently expands or contracts the set.

## (b) Slicing proof (mandatory)

`--collect-only -q` over the declared set: **executed == declared**, node counts recorded in the report. A leg without a slicing proof is INVALID.

## (c) Budget mechanics — hard 30-min cap

| Phase | Budget |
|---|---|
| Setup (two worktrees, scratch PG(s), collect) | 10–15 min |
| Legs (BASE + HEAD) | 2 × 5–10 min |
| Flaky probes (3× isolation, diff nodes ONLY) | ~5 min |

Projected OR actual breach → **STOP + fail-loud report + ONE auto re-scope attempt**. Never silent grinding; never a quiet cap raise.

## (d) Execution recipes

- Preferred: `xdist -n auto` WITHIN one leg (scoped sets are small; parallelism inside the leg is safe for the non-PG corpus).
- Each leg gets its **OWN worktree + OWN scratch PG**. NEVER share a scratch PG across concurrent legs — DF-4 lesson (2026-10-22): parallel legs racing the shared scratch DB fabricated **9 reds** (4 fake NET-NEW + 5 fake BASE-only). Chunks hitting the shared-scratch helpers (`grep -rln 'dual_backend_engine\|auth_pg_scratch_helper\|scratch_pg'`) run strictly one-leg-at-a-time.
- PG packs (`-m postgres`): serial only — schema races, no xdist.
- Serial alternative: mrpr-style measured-rate shard sizing — ≈3300 nodes/shard with per-shard `timeout 1750`, shards sized so projected shard wall ≈ 25–30 min (sizing method: `.agents/tester/skills/whole-tree-base-proven-diff/` § 2).
- Calibration evidence (scoped precedents): running-zombie ~90s/run, nest-ops 82–91s/run, wedge-capture ≤55s/pack. A scoped leg finishing in minutes is the NORM, not the exception.

## (e) Fail-loud node-cap / projection guard

Before legs: `node_cap` = declared-set node count; measured rate from one small probe shard; `projected = setup + nodes/rate + probes`. Projected > 30 min → abort BEFORE running, report, re-scope once. Actual breach mid-leg → same treatment.

## (f) Named pre-exclusions + compensating pins

| Excluded family | Why excluded | Compensating pin |
|---|---|---|
| `tests/performance/test_message_api_cost.py` | >300s solo, rc=124 at every bisection level — unschedulable | `regression_performance` pack (registered, serial, `tests/performance/`) |
| e2e cells requiring a live `:8079` daemon — incl. `tests/e2e/test_context_injection_hybrid.py` (module-IMPORT-time HTTP GET; collection ERROR even when deselected) | need a live daemon | `e2e_workflows_ensure_test` (Release Gate sanctioned lane) |
| `tests/packs/g7` sys.exit(0) collection trap | module-level sys.exit aborts collection | packs execute via their own registered scripts (PACKS.md) — never via collection |

Each exclusion MUST carry a named compensating pin in the gate report (wedge-capture pattern).

## (g) Worked scale example — the G2 counterfactual

The notify-user diff (2026-10-25/26): one new tool file + registration + migration + pin packs. Blast radius ≈ low-hundreds of nodes → Tier-1 leg ≈ **15–30 min total** (setup + 2 legs + probes). What actually ran: whole-tree dual-side, serial, 1300-node shards under 300s caps → **9h32m wall, 89 timeout-splits, 36 rc=124 outer kills, performance/e2e cells pure waste** — for a diff whose blast radius was one tool surface. The tier doctrine exists so this never recurs.

## Fences (house rules, unchanged)

Per-variable env scrubs (`env -u POSTGRES_*` globs are a SILENT NO-OP — bash does not glob env names); scrub+fence written ONCE into a runner script file invoked by both legs; `mktemp` FAKE_HOME fences; ports 9797/8079 untouched; never the shared dirty checkout; record the main checkout's porcelain sha before AND after.
