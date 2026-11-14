# Approver Tracking: guideline-read-tool

Plan: guideline-read-tool — the read-only `guidelines` privileged category #22 (`guidelines_list` + `guideline_read`) over the shipped release payload.
Planning dir: `.agents/shared/planning/guideline-read-tool/` @ worktree `/home/nea/ensemble-src-wt-guideline-read-tool`, branch `feature/guideline-read-tool`.
Lane commits: `681de8081` (phases 1+2: category #22 tools + registration + 4 grants + 21→22 pin census), `ccb7962d5` (phase 3: stage payload seam + stage-smoke), phase-4 commit (doctrine homes + sha re-stamp + this record).

## Ruling record

- **Design FIXED 2026-11-13 (Emperor ruling).** The release payload IS the source of truth: no DB copy, no cache, no write path — a read-only window (`guidelines_list` parsed summaries + `guideline_read` verbatim) onto `releases/<v>/guidelines/`, staged flat from `docs/guidelines/` by `stage.sh`.
- **Architecture adjudication Q1–Q4 BINDING 2026-11-13** (`.agents/shared/planning/guideline-read-tool/architecture-adjudication.md`): Q1 payload-resolution ladder (explicit `base_dir` → frozen `<exe-dir>/guidelines` → dev repo `docs/guidelines` → typed refusal); Q2/Q3 seam + dev-fallback rulings; Q4 kill-switch ruling (below).

## Allow-list record — EXACTLY 4

`guidelines` is granted to EXACTLY: **ari, leader, devops, sonic-leader** (explicit `tools.allow: ["guidelines"]` entries; verified in-tree against every `agents/*/meta.json`). No other agent carries the category key — in particular NOT sonic-coder / sonic-developer / sonic-reviewer / sonic-tester (they cannot see the category). Enforcement is two-layer: the privileged default-deny strip (`PRIVILEGED_TOOL_CATEGORIES` entry #22 — opt-in only, never default-granted) plus the per-tool `GUIDELINES_AUTHORIZED_AGENTS` frozenset gate (a literal tool name in an allow list bypasses the category gate, so the body gate is required).

## Ceremony note — sonic-first

**`guidelines` is the FIRST privileged-category grant to a sonic agent (sonic-leader).** Both evidence anchors, independently verified on this worktree:

1. **Census:** `PRIVILEGED_TOOL_CATEGORIES` holds exactly 22 members (`daemon/tools/_tool_registry.py:466`); NO sonic-* allow array carries any privileged member EXCEPT sonic-leader's `guidelines` (this lane's grant — verified programmatically over every `agents/*/meta.json`). sonic-leader's `midflight` is NON-privileged: it is not a member of the 22-entry set, and the category is opt-in per agent, never auto-granted (`daemon/tools/midflight_report.py:62` — the `@register_tool_category("midflight")` site with the "Opt-in per agent … NOT auto-granted" docstring; held by leader, devops, sonic-leader as a cross-team QA channel). A negative-claim pin (`test_guidelines_registration.py` leg B5) asserts `midflight` did NOT confer the grant.
2. **Grant authority:** the sonic roster v0.30.2 amendment (merge `82e2bd849`) — sonic-leader exists as a leader-tier sonic agent with `tools.allow` semantics, and THIS lane's same-commit meta.json diff is the grant instrument.

## Kill-switch ruling — NO (binding)

Adjudication Q4 (BINDING 2026-11-13): **NO kill-switch, NO env knob.** Rationale: (i) pure read surface — no writes, no ledger, no external effects, nothing to kill; (ii) already doubly gated (privileged default-deny strip + in-body frozenset authority gate), and per-agent revocation exists as the config-level off (remove the category key from the agent's `tools.allow`); (iii) the payload is the natural off-switch — a release without `guidelines/` yields the typed refusal, and rollback to a pre-v0.30.3 release removes the tool with the binary; (iv) an env knob would mint the FIRST env knob in the runtime payload-base lane — new config surface for zero protective value; (v) boot-safe by construction (no factory-time dir check).

## Tester RESULTS

- Developer-lane evidence: `.agents/tester/RESULTS/2026-11-13-guideline-read-tool.md` — this lane's honest per-suite runs (registration 39P, payload seam 49P/0F, tripwire class 1P, baseline suite 19P, generator re-stamp 201/201). Marked DEVELOPER-LANE: the independent tester gate owns further verification and red-set accounting.
- Shell pack registered: `test/packs/guidelines_read_tool_unit_test.sh` — a THIN wrapper around the two existing suites (`tests/unit/tools/test_guidelines_registration.py` + `tests/test_guidelines_payload.sh`); zero new test logic. Registered in `.agents/tester/PACKS.md` (top section, 2026-11-13).
