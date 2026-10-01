# Final Verification — feature/designer-handoff-contract @ cf51132b

Date: 2026-10-01T22:1x UTC (verification wave)
Repo: /home/nea/ensemble-src
Chain: 82fc6131 (latest) → 75908c08 → cf51132b
Workers: A `verify-static-scope` (8c6843f4, no skill) — checks 1/5/6 · B `verify-contract-seams` (4271809d, no skill) — checks 2/3 · C `verify-fixture-pack` (d42c8d68, test-pack-execution) — check 4
Mode: verification only — zero file modifications, zero commits by testers.

## Verdict: READY-TO-MERGE — 6/6 checks PASS

| # | Check | Verdict |
|---|-------|---------|
| 1 | RENAME (coder→developer sweep) | PASS |
| 2 | CONTRACT (both copies, items i–vi + one-canonical-home) | PASS |
| 3 | SEAMS (4-site fields, §4.5, canonical paths) | PASS (1 minor note) |
| 4 | FIXTURE (test_report_integrity_prompts.py) | PASS (known pre-existing [designer] RED only) |
| 5 | SCOPE FENCE (exactly 12 files) | PASS |
| 6 | CHAIN/CONTAINMENT | PASS |

E2E: N/A per lane-intersection rule — markdown/prompt files only, no daemon/job/task/queue execution lane touched. ensure.md NOT cited as gate (dev.sh boot probe only).

## Check 1 — RENAME: PASS
`grep -rn "coder" agents/leader/ agents/designer/ agents/developer/ 'agents/developer[v2]/' .agents/shared/planning/designer-agent/` → 101 hits, ALL classified justified:
- **J1** (1): agents/developer/rule.md:25 `Opencode is an autonomous coder.` — substring in opencode noun.
- **J2** (3): arch doc :23/:45/:82 — peer-agent anatomy citations; `agents/coder/` pre-exists at base (`git ls-tree --name-only 82fc6131 agents/ | grep coder` → `agents/coder`).
- **J3** (20): historical implementation-plan subdirs (phase1-3 plans, decisions.md, verdicts/) — untouched planning artifacts.
- **J4** (77): `agents/developer[v2]/` tree (workflow, rule, soul, memory, meta.json, skill-set.yaml, dev-strategy, tools_note) — coder = executor peer-agent dispatcher idiom (`spawn_instance(agent="coder")`, `team_members: ["coder","worker"]`).
- Zero hits in agents/leader/ and agents/designer/. Zero hits denoting the renamed implementer role.

**Adjudication note (tester):** Worker A extended J4 beyond the anchored file (workflow.md) to the whole v2 tree and flagged it. Ruling = justified: agents/coder/ is a real distinct dispatch-target agent; every v2 hit spawns/references that peer, never the developer role; v2 files outside the branch diff (soul.md, meta.json, skill-set.yaml, dev-strategy.md, tools_note.md) are pre-existing. `developer[v2]/soul.md:7` "I am **NOT a direct coder**" is a self-distinction, not role collapse.

Fence sub-checks: `git diff 82fc6131..cf51132b --name-only | grep '^agents/coder/'` → empty (no new coder dir). `git diff 82fc6131..cf51132b -- agents/leader/meta.json` → empty; leader team_members contains `developer` + `designer`, no `coder`.

## Check 2 — CONTRACT: PASS (both copies, all six items)
Base `agents/developer/workflow.md` §Designer-Sourced (≈lines 95–136) and v2 `agents/developer[v2]/workflow.md` (Pre-Dispatch Gate 124–132, Relay 138–140, Return 148–152, Cosmetic-Skip 158–160) each verified:
- (i) `status: approved` + `pinned_spec_sha` match gate; escalate on mismatch AND on absent-on-re-conformance; explicit "SHA absence on new/amend is not a mismatch" (W-1); "never guess / never re-derive a SHA" — both copies.
- (ii) `Validation: pack <name>; static: grep <pattern>` convention quoted in both (base :116, v2 :138).
- (iii) `token_change_set` / `blast_radius` / `do_not_touch` in both (base :108–110, v2 :139).
- (iv) Canonical path triple in both (base :118, v2 :140).
- (v) §4.2 return fields commit_sha/diff_stat/pages_changed/conformance_iter/capture paths in both (base :132–136, v2 :148–152).
- (vi) Cosmetic-skip + flag-back on non-trivial UI (layout shift / new tokens / a11y) in both (base :124–126, v2 :158–160).

One-canonical-home: `grep -n "Designer-Sourced\|pinned_spec_sha\|token_change_set\|do_not_touch" agents/developer/tools_note.md 'agents/developer[v2]/tools_note.md'` → zero hits (exit 1). PASS.

## Check 3 — SEAMS: PASS (1 minor note)
- 4-site matrix (leader :244 ↔ designer :21–32 ↔ developer base :78–89 ↔ v2 :128/130/166): task_id, phase (`new | amend | re-conformance`), files, notes, plan_ref, conventions, escalation_path, pinned_spec_sha (verbatim on re-conformance) — **verbatim identical, divergence list empty**.
- Edge-contract fields match arch doc §4.5 (:188) at all sites (task_id, pinned_spec_sha re-conf-only, AC IDs, token_change_set, blast_radius, do_not_touch ↓ commit_sha, diff_stat, pages_changed, conformance_iter, capture paths). Divergence empty.
- Canonical paths identical across the four workflow sites.
- **Minor note (non-blocking):** arch doc :129 abbreviates `planning/{feature}/design/mockups/` (drops leading `.agents/shared/`) vs the full form in developer files — same logical location, doc-style shorthand only.

## Check 4 — FIXTURE: PASS (expected-RED-only)
`timeout 300 .venv/bin/pytest tests/unit/test_report_integrity_prompts.py --tb=short -q` → exit 1, **50 passed, 18 skipped, 1 failed** (69 collected), 6 s, no collection errors (no py3.13 rot on this file), no timeout.
Failure set == exactly `{test_every_parent_agent_carries_scrutiny_guidance[designer]}` — missing snippets `['[REPORT SANITY:', 'interim, not completion']` in designer/{rule,workflow,soul}.md + skills-template.
Pre-existence proven: `git show 82fc6131:agents/designer/rule.md | grep -c "REPORT SANITY"` → **0**; same for workflow.md → **0** (also 0/0 at HEAD). RED is pre-existing at latest, NOT introduced by this branch; fast-follow commission stands.

## Check 5 — SCOPE FENCE: PASS
`git diff 82fc6131..cf51132b --name-only` → EXACTLY the 12 intended files (extra: 0 / missing: 0). Per-commit: 82fc6131..75908c08 = 9 files; 75908c08..cf51132b = 7 files; 4 overlap → union 12. Forbidden audit clean: no meta.json, daemon/, frontend/, MCP config, pyproject.toml/daemon/__init__.py, and neither pre-existing dirt group appears in either commit.

## Check 6 — CHAIN/CONTAINMENT: PASS
`git rev-parse --short HEAD` → cf51132b; branch → feature/designer-handoff-contract; `git log --oneline -3` → cf51132b (contract port) / 75908c08 (handoff contract) / 82fc6131 (v0.16.9 closure note). `git branch -a --contains 75908c08` and `... cf51132b` → ONLY feature/designer-handoff-contract (no latest/master/master-new/remotes). `git status --porcelain` → exactly 6 lines: 5× `M frontend/src/app/pages/maintenance/…` + `?? .agents/shared/planning/scheduled-tasks/` — the 2 known pre-existing dirt groups, nothing else.

## Residuals / follow-ups (none blocking)
1. Known pre-existing RED `[designer]` scrutiny-guidance case — accepted fast-follow commission (do NOT fix in this branch).
2. Arch doc :129 mockups-path shorthand vs full canonical form — cosmetic doc-style; optionally normalize in the fast-follow.
