# Tiered Gate Doctrine — Authoring Record (docs-only commission)

- **Date**: 2026-10-31
- **Commission**: tiered-gate-doctrine (user-approved 2026-10-26/31)
- **Worktree**: `/home/nea/ensemble-src-wt-tiered-gate-doctrine` (branch `feature/tiered-gate-doctrine`, base `cbafcc8d9`, dev-fenced: `.env` ENSEMBLE_SELF_ENV=dev, POSTGRES_DB=ensemble_dev)
- **Author**: tester instance (docs authored directly per Dispatch Model docs lane; git via worker)

## Scope Decision

Docs-only commission — Tier 0 by its own doctrine. Writes confined to `.agents/tester/**`; zero daemon/agents/tests/shipped-code changes; no test-suite executions (none required; `--collect-only` not needed for authoring); no daemon access; no live contact; no push.

## Deliverable 1 — ensure.md tier amendment

`.agents/tester/rules/ensure.md`: 53 → 73 lines (+20 inserted). Surgical insertion between the "How to use this file" block and "## Core": one added how-to-use bullet (gate tiers pointer) + one new section "## Gate Tiers (tiered gate doctrine — user-approved 2026-10-26/31)" covering Tier 0/1/2, the mechanical core-seam escalation list (daemon/graph.py, daemon/manager.py, daemon/api.py, daemon/services/instance_messaging.py, daemon/services/task_processor.py, daemon/services/job_queue_service.py), 30-min HARD fail-loud Tier-1 budget, 90-min advisory Tier-2 budget, mandatory recipe-load rule, node-cap + measured-rate projection guard, pre-exclusion registry, unchanged proof form, and the pack-cap vs gate-budget tension resolution. **Core and Release Gate sections untouched — all pack mappings preserved.**

## Deliverable 2 — skills tree (`.agents/tester/skills/`)

| Skill | tier(s) | supersedes (dynamic-skill id) |
|---|---|---|
| blast-radius-leg | 1 | — (new; G2 counterfactual baked in) |
| whole-tree-base-proven-diff | 2 | 9e79ccad-1e2c-456b-8762-82d7f5eeec0f |
| regression-ab-partition-run | 2 | fd1e01dc-37d5-4a0b-a05e-1933d18a006d |
| failed-digest-equality-gate | 1, 2 | 76e3e1a7-1a5a-4228-957f-f8e556e04213 |
| ab-base-leg-provisioning-artifacts | 2 | 08e67030-1c11-498b-a451-04da3e552982 |

Each dir: exactly `skill.json` (schema v1: schema_version/name/version/description/category=gate-runner/scope=project/tiers/body/assets/loads/supersedes) + `<name>.md` body. Validation 4/4 PASS at fc266ad5a; 5/5 after the fold round (below). Checks: name==dir, exactly one skill.json per dir, body exists (directory listings + name-field grep on record).

Refinements baked into the canon bodies (per commission): mrpr measured-rate shard sizing (~3300 nodes/shard, timeout 1750 class) + xdist `-n auto` within a leg normative in whole-tree-base-proven-diff (legacy ≤100-node/300s shards demoted to bisection-only); DF-4 shared-scratch-PG serial-only rule with the 9-fabricated-reds measurement in both blast-radius-leg and regression-ab-partition-run; pre-exclusion registry + compensating-pin requirement in both execution recipes (blast-radius-leg + whole-tree-base-proven-diff); worked G2 counterfactual (blast radius ≈ low-hundreds of nodes ≈ 15–30 min vs actual 9h32m / 89 timeout-splits / 36 rc=124) in blast-radius-leg.

## Deliverable 3 — DB supersede

`supersedes` provenance set in each skill.json (above). DB body preamble updates ("SUPERSEDED — canonical version lives at .agents/tester/skills/<name>/ in the repo; load that"): NO direct body-edit tool exists on the tester tool surface → closest supported action taken: `skill_fix(skill_id, issue_description, suggested_fix=<exact preamble text>)` dispatched per skill (x3). **Outcome: ALL THREE requests NOT dispatched — the skill-evolution admission lane is disabled (kill-switch `ENSEMBLE_SKILL_EVOLUTION_ENABLED` off, or per-skill fix cooldown); the tool responses are the only record; NO skill-keeper job was created; the three DB bodies remain UNMODIFIED (no preamble prepended).** No DB skill deactivated — all three remain active/searchable. Limitation recorded per the dispatch's instruction to do the closest supported action and report exactly what was done. Note: the fd1e01dc body view truncated at 8000 chars mid-DF-4 example; all operative content retrieved is canonized (the truncation cut only the tail of an illustrative example whose numbers are preserved).

## Fold round (review APPROVE-WITH-ADVISORIES @ fc266ad5a: 0 critical / 11 warnings / 6 suggestions)

Single fold commit on top of fc266ad5a (docs-only, `.agents/tester/**`). Dispositions:
- **W1 addressed** — `ab-base-leg-provisioning-artifacts` canonized as the 5th skill dir (substantive recipe material; reviewer's lean); ab-partition order-step 9 cross-ref re-pointed to the canon path; `loads` entry added (skill.json v1.0.1).
- **W2 addressed** — Deviations entry 6 (below) discloses the `--amend` single-commit shape.
- **W3 addressed** — the false "DB copy retained … with a SUPERSEDED pointer" claim reworded in ALL THREE bodies where grep found it (whole-tree, ab-partition, digest); now "preamble requested via skill_fix, pending evolution-lane enablement — not yet applied". Note: blast-radius-leg.md contained NO such claim (grep-verified) — the reviewer's naming of it appears to be a misattribution; the third unnamed occurrence (failed-digest) was fixed on the same principle.
- **W4 addressed** — mechanical-escalation claim scoped to the core-seam arm; promote-blocking arm inherits the Release Gate's judgment trigger.
- **W5 addressed** — pre-exclusion registry scoped to REGRESSION legs; Release-Gate live-daemon E2E items named the sanctioned exception.
- **W6 addressed** — digest 3× isolation scope harmonized to ensure.md SSOT: "diff reds under adjudication" (net-new ∪ fixed ∪ surprising pre-existing before classification).
- **W7 DEFERRED** per reviewer ruling (whole-tree --ignore for the perf family — next touch of that file).
- **W8 addressed** — slicing-proof step inserted as ab-partition order-step 6; list renumbered to 10 steps.
- **W9 addressed** — serial constants inlined (≈3300 nodes/shard, per-shard timeout 1750) at the blast-radius pointer.
- **W10 addressed** — "all gate recipes" corrected to the actual two execution recipes.
- **W11 addressed** — named compensating-pin mapping added: ensure.md registry bullet (inline pins) + pin-column tables in both execution recipes. Pins grounded in PACKS.md rows: `regression_performance` (registered serial pack, tests/performance/), `e2e_workflows_ensure_test` (Release Gate sanctioned lane), registered pack scripts (g7 trap — packs never run via pytest collection).
- All 🟢 suggestions DEFERRED per reviewer ruling.

Validation after fold: 5/5 skill dirs (name==dir, exactly one skill.json per dir, body exists).

## Deviations (disclosed)

1. **ensure.md is user-owned/read-only by standing tester rule** — amended here ONLY under the explicit user approval trail (user decisions 2026-10-26/31 relayed in the dispatch; corroborated by the giter worktree claim KV `wt.claim.tiered-gate-doctrine` "ensure.md amendment"). Amendment lives on the lane branch only; the user retains merge control. Disclosed for the record.
2. `tiers: [1, 2]` used for failed-digest-equality-gate (it genuinely serves both tiers); spec examples showed single-tier lists.
3. `supersedes` key omitted in blast-radius-leg/skill.json (no predecessor; null-equivalent).
4. `assets: []` everywhere — the optional `scripts/` subdirs were not needed; recipes embed commands inline.
5. DB preamble edits are job-requested via `skill_fix` rather than direct writes (tool-surface limitation, anticipated by the dispatch).
6. The post-commit line-count correction (53→81 typo → 53→73) was folded via `git commit --amend` on the unpushed lane-local branch — an in-history rewrite, disclosed here per review W2; the final record is accurate and the process shape is now part of the record.

## Commit

House message: `docs(tester): tiered gate doctrine + project skill canon (.agents/tester/skills)` — worker-executed immediately after this file; sha + stat + scope proof (`git diff --name-only cbafcc8d9..HEAD` all under `.agents/tester/**`) in the tester's final commission report.
