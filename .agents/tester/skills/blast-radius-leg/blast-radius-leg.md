# Blast-Radius Leg — Tier-1 Gate Recipe (project canon)

> **v1.0.2 (2026-10-31)** — doctrine maintenance driven by the SECOND real gate run
> (project-skill-loader Phase-2, Tier-1) + the Phase-1 carry-over. Two folds:
> **(a)** Fences gains the SIBLING-ENV-ASSERTION rule (Phase-1 🔴, re-confirmed by two
> independent lanes: the ambient env is prod-poisoned — `POSTGRES_DB=ensemble_prod`,
> `POSTGRES_HOST=10.44.0.2` — and a sibling worker's env claim is NOT evidence about
> yours); **(b)** mutation-label namespace: every mutation leg is labeled `P{n}-Mn`
> (phase, mutation) with a mutation→test table in PACKS.md — bare `M1-M4` collided
> across phases (Phase-2 gate finding F1).
>
> v1.0.1 (2026-10-31) — doctrine maintenance driven by the FIRST real gate run
> (project-skill-loader Phase-1, Tier-1, ~6 min legs / ~28 min window). Three folds:
> **(a)** the two-class declaration rule for additive lanes (GAP-1); **(b)** digest
> capture greps `^(FAILED|ERROR)`, not `^FAILED ` (GAP-2); **(c)** the budget table now
> carries the measured parallel-legs shape (FRICTION-1).

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

### Two-class declaration (ADDITIVE lanes) — GAP-1, folded in v1.0.1

A purely-additive lane (new modules / new test packs, no existing seam touched) breaks
the single-set assumption above: the lane's own pins exist ONLY at HEAD, so a literal
"executed == declared on both legs" either produces BASE collection errors on absent
files or invites counting absent-at-base files as net-new. Declare TWO classes:

- **D1 — lane-owned (HEAD-only) pins.** Test files the lane ADDS. Status rule: **MUST PASS
  at HEAD** (0 failed / 0 error). A base-side "red" is **IMPOSSIBLE BY CONSTRUCTION** and is
  therefore neither net-new nor "fixed" — record the absence as expected-by-construction,
  never as a failure and never as a pass.
- **D2 — dual-side comparable set.** Files existing at BOTH revs, enumerated by the (a)
  derivation (priority order is tester-owned; cap it and record drops). This is the ONLY
  set that carries the FAILED-digest diff.

The gate report MUST state both classes, their node counts per leg, and the asymmetry
explicitly: the HEAD leg runs D1 + D2, the BASE leg runs D2 only. That is **not** a broken
"identical commands" invariant — the command SHAPE is identical; only the file list differs
by construction, and the report says so.

## (b) Slicing proof (mandatory)

`--collect-only -q` over the declared set: **executed == declared**, node counts recorded in the report. A leg without a slicing proof is INVALID.

**Digest capture form — GAP-2, folded in v1.0.1.** Extract failing node ids with
`grep -E '^(FAILED|ERROR)'` — **NOT** `grep '^FAILED '`. A bare `^FAILED ` grep silently
drops every `ERROR` line (collection/setup errors), so an additive lane — whose new files
are ABSENT at BASE and therefore produce collection ERRORS there — can look digest-clean
while BASE was erroring. Digest BOTH prefixes; explain every `ERROR` id in the report, never
only the `FAILED` ones. (The sibling `.agents/tester/skills/failed-digest-equality-gate/`
carries the same corrected form.)

## (c) Budget mechanics — hard 30-min cap

| Phase | Budget |
|---|---|
| Setup (two worktrees, scratch PG(s), collect) | 3–10 min |
| Legs (BASE + HEAD) — **sequential**, each a scoped set | 2 × **minutes** (measured: 26 s + 40 s on a ~400-node scoped set) |
| Flaky probes (3× isolation, diff nodes ONLY) | ~5 min, and only when the diff set is non-empty |

**Target wording — FRICTION-1, folded in v1.0.1:** *a scoped leg is MINUTES.* The older
10–15 min setup + 2 × 5–10 min leg numbers summed to 25–40 min and overran this recipe's own
30-min cap before anything ran. Measured reality from the first real outing
(project-skill-loader Phase-1): **gate legs ≈ 6 min**; **total window ≈ 28 min**, and that
28 min = recon + `max(legs, mutation-lane)` + one discretionary closeout — i.e. **the legs and
the mutation-verification lane ran in PARALLEL**, so the window is not their sum. Serialising a
mutation lane behind the legs is what turns a 6-min gate into a 26-min one.

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

## Fences (house rules; v1.0.2 adds the sibling-env rule)

Per-variable env scrubs (`env -u POSTGRES_*` globs are a SILENT NO-OP — bash does not glob env names); scrub+fence written ONCE into a runner script file invoked by both legs; `mktemp` FAKE_HOME fences; ports 9797/8079 untouched; never the shared dirty checkout; record the main checkout's porcelain sha before AND after.

**SIBLING-ENV-ASSERTION (v1.0.2, Phase-1 🔴 carry-over — the highest-value fold):** a
sibling worker's env claim is NOT evidence about YOUR env; verify per-invocation,
per-variable (`[ "$POSTGRES_DB" != "ensemble_prod" ]` or equivalent assert in the runner;
the ambient env on this host is prod-poisoned — `POSTGRES_DB=ensemble_prod`,
`POSTGRES_HOST=10.44.0.2` — re-confirmed by two independent lanes 2026-10-31).
`PG_TEST_ALLOWED_DBS` is unset by default: set it explicitly per run or the fence's
intended mechanism is absent.

**Mutation-label namespace (v1.0.2, Phase-2 F1):** every mutation leg carries a
`P{n}-Mn` label (phase n, mutation m) and lands in the lane's mutation→test table in
`.agents/tester/PACKS.md`. Bare `M1-M4` labels collided across phases once already —
do not reintroduce them. (The Phase-4 plan's `M0-M11` degraded-MODE codes are a
different namespace and are unaffected.)
