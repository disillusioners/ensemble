# Phase 2 — Orchestrator Agent Upgrade + Lane Skills (Central Maintenance System)

**Objective:** Upgrade the in-repo `maintenancer` agent from a solo repair coordinator into an orchestrator that can spawn/send per-lane worker children and carry one skill per lane, so the weekly run can fan out five isolated lanes plus a report writer without ever mutating the host itself.

**Status:** PLANNED

---

## Scope

### In scope
- `agents/maintenancer/meta.json`: add the `"instance"` tool category (plus `subtree_messages` / `subtree_status` read surfaces per leader precedent); keep the `deny` list unchanged; map the three existing `team_members` to lane roles.
- `agents/maintenancer/rule.md` + `workflow.md`: orchestrator rule/workflow for weekly-run dispatch, per-lane status capture, and the report-ALWAYS invariant (including total-failure), staged in-repo.
- Six new per-lane skills under `agents/maintenancer/skills-template/` + matching `skill-set.yaml` rows: `lane-disk-sweep`, `lane-pok-sweep`, `lane-skill-check`, `lane-abnormality-check`, `lane-metrics-watch`, `run-report-writer`.
- Failure-isolation design: per-lane child dispatch, per-lane status capture, report always authored even on total lane failure.
- Extension of the version-consistency and team-membership pins to cover the new skills.

### Out of scope
- **Live DB prompt adoption** — prompt artifacts are DB-versioned; in-repo file changes stage via the release and the live DB adoption is a standard **post-promote operator step**. Flagged here, NOT executed in this lane (R3).
- Phase 1 trigger chain (sibling worker) — cross-ref `phase1-plan.md (sibling)`.
- Lane implementations themselves (Phase 3) and report/receipt/FE (Phase 4).
- Any code, daemon run, DB write, or git mutation — this lane is plan-only.

---

## Preconditions & dependencies

- **Upstream:** Phase 1 (sibling) establishes the self-rescheduling one-shot trigger that dispatches DIRECTLY to `maintenancer` (R3). Phase 2 assumes the dispatched turn arrives at a `maintenancer` instance able to spawn children.
- **Downstream:** Phase 3 lane skills are the bodies Phase 2 registers; Phase 4's report writer is the `run-report-writer` skill registered here; Phase 5 (sibling) owns the test suite and staging.
- **Parallelizable-with:** Phase 3 (lane procedure design) and Phase 4 (report schema) can proceed in parallel with Phase 2 — the skill *names* and *contracts* are fixed here, the bodies are filled there. Coordinate on the exact skill file names listed above.
- **Base:** worktree `/home/nea/ensemble-src-wt-maintsys`, branch `feature/central-maintenance-system`, base `origin/latest @ 1b9797ab7`. NEVER operate from `/home/nea/ensemble-src` (detached canonical, stale for this area, zero hbk code).

---

## Design decisions consumed

- **R3** — scheduler dispatches DIRECTLY to `maintenancer`; agent upgraded IN-REPO; live DB prompt adoption is a post-promote operator step (flagged, out of lane scope).
- **R4** — each lane = isolated execution boundary; per-lane error capture; report ALWAYS written with per-lane status.
- **D-A** — the `maintenancer` seat stays non-mutating (`deny` keeps `write_file`/`edit_file`/`git_commit`); report + receipt + INDEX authoring routes through a dedicated worker child with ABSOLUTE paths. Fallback (scoped write capability) only if R4 cannot be guaranteed via a child — spec with tradeoffs, do not silently pick.
- Existing detail-plan precedent: `.agents/shared/planning/maintenancer-agent/detail-plan.md:976-986` (worker spawn + `load_skill` + evidence fan-in), `:108-115` (fan-in/scrutiny/team-membership pins).

---

## Tasks

### Task 2.1 — Add `instance` + subtree read categories to `meta.json`
- **Description:** Add `"instance"` to `tools.allow` so the seat can `spawn_instance` / `send_message` worker children, plus `"subtree_messages"` and `"subtree_status"` for subtree status reads (mirrors `agents/leader/meta.json:19-21`). Keep `tools.deny` EXACTLY `["git_commit", "edit_file", "write_file"]` (verified at `agents/maintenancer/meta.json:26`) — the seat must remain non-mutating per D-A. Do NOT add `instance_read`/`instance_ops` semantics here: those are the nest-ops fleet surfaces (`daemon/tools/_tool_registry.py:94-118`, 4 read + 6 act tools), not the spawn/send child surface; the spawn/send surface is the `"instance"` category.
- **Touch points:** `agents/maintenancer/meta.json` (`tools.allow`, lines 12-25; `tools.deny` line 26 unchanged).
- **Verification:** JSON parses; `"instance"`, `"subtree_messages"`, `"subtree_status"` present in allow; deny list byte-unchanged; version bump considered per house convention.
- **Agent hint:** `coder` (config-only edit) — but note this is a plan; the implementing instance does the edit.

### Task 2.2 — Map team_members to lane roles
- **Description:** Keep `team_members: ["explorer", "worker", "coder"]` (`agents/maintenancer/meta.json:28`) and document the mapping in `workflow.md`: `explorer` = read-only surveys (POK census reads, anomaly reads, skill-table reads, metrics digests); `worker` = bash sweeps + report/receipt/INDEX writing (the only member that writes, with ABSOLUTE paths, per D-A); `coder` = fix sketches only (route-to-lane recommendations, never applied inline). No new team members needed — the three existing roles cover all five lanes + report writer.
- **Touch points:** `agents/maintenancer/workflow.md` (new "Lane role mapping" subsection).
- **Verification:** workflow names all three roles with their lane responsibilities; no role is granted a capability outside its `meta.json`.
- **Agent hint:** `doc-writer` / `coder`.

### Task 2.3 — Orchestrator `rule.md` additions (weekly-run cardinal rules)
- **Description:** Add cardinal/guideline rules for the weekly run WITHOUT duplicating the END TURN invariant (already at `rule.md:7` and `workflow.md:69-73`). New rules: (a) the run dispatches five lanes + a report writer as isolated worker children; (b) each lane returns a status `ok|degraded|failed` + a result block; (c) REPORT ALWAYS written — even if every lane fails, the report writer child still runs with total-failure statuses; (d) report authorship goes to the `worker` child with ABSOLUTE paths (D-A), the seat never writes; (e) no idle gating — the run executes regardless of busyness (ruling). Keep report-sanity scrutiny (`rule.md:9`) and fan-in valve (`workflow.md:36-45`) intact.
- **Touch points:** `agents/maintenancer/rule.md` (new rules section, ~lines 12-19 area, before the `---` at line 13 or as new Guidelines).
- **Verification:** rules state report-ALWAYS incl. total failure; seat non-mutating preserved; no contradiction with Cardinal #3/#5.
- **Agent hint:** `coder` / `doc-writer`.

### Task 2.4 — Orchestrator `workflow.md` additions (weekly-run dispatch flow)
- **Description:** Extend the dispatch pattern (`workflow.md:19-32`, one skill per worker, spawn-then-send, END TURN) with a "Weekly Run" flow: (1) mint run_id (format from `daemon/services/maintenance_run_identity.py:6,30-47`, `ckpt-YYYYMMDD_HHMMSSffffff-<hex8>` — see P4 D-B); (2) dispatch up to 3 concurrent lane children per wave (concurrency cap at `workflow.md:29-32`), each with ONE `load_skill`, END TURN after each wave; (3) fan-in statuses via the existing fan-in escape valve (`workflow.md:36-45`, max re-dispatch = 1, then mark `[incomplete]`); (4) after all lanes resolve (ok/degraded/failed/incomplete), dispatch the `run-report-writer` child — this step is unconditional. Encode the **load_skill spawn contract**: `load_skill` rides `send_message` ONLY — spawn the child with no task first, then `send_message(..., load_skill="<one>")` while the child is free; NEVER pass the artifact in the task context (truncated at 4000 chars at the tool layer — pass file PATHS instead).
- **Touch points:** `agents/maintenancer/workflow.md` (new "Weekly Run Flow" + "load_skill spawn contract" subsections).
- **Verification:** flow makes the report-writer dispatch unconditional; path-passing rule stated; END TURN per wave preserved.
- **Agent hint:** `coder` / `doc-writer`.

### Task 2.5 — Create six lane skill bodies under `skills-template/`
- **Description:** Author one skill body per lane + report writer, each with frontmatter `version: "1.0.0"`, `category: maintenance`, `auto_load: false` (except as noted), following the shape of existing `health-check.md` (boundary + contract + focus areas):
  - `lane-disk-sweep.md` — bash sweep worker fence recipe (detail in P3 Lane 1).
  - `lane-pok-sweep.md` — read-only POK census + delta reconcile (P3 Lane 2).
  - `lane-skill-check.md` — consolidated skill-system check (P3 Lane 3).
  - `lane-abnormality-check.md` — anomaly + old-tier health watch (P3 Lane 4).
  - `lane-metrics-watch.md` — metrics digest read (P3 Lane 5).
  - `run-report-writer.md` — report + receipt + INDEX authoring with ABSOLUTE paths (P4).
  Each skill declares its result-block schema (feeds P4 receipt) and its three-class recommendation routing.
- **Touch points:** `agents/maintenancer/skills-template/lane-disk-sweep.md`, `lane-pok-sweep.md`, `lane-skill-check.md`, `lane-abnormality-check.md`, `lane-metrics-watch.md`, `run-report-writer.md` (all NEW).
- **Verification:** each has valid frontmatter; no skill writes outside its lane boundary; report-writer is the only skill that writes files, with absolute paths.
- **Agent hint:** `doc-writer` (bodies) reviewed by `coder`.

### Task 2.6 — Add six matching rows to `skill-set.yaml`
- **Description:** Add one row per new skill with `version: "1.0.0"`, `auto_load: false`, `category: maintenance`, and a one-line description. Existing 7 rows (`agents/maintenancer/skill-set.yaml:1-37`) stay unchanged. Seeding keys off `skill-set.yaml` — a skill needs BOTH a template body AND a manifest row (research finding). Keep `kb-curator` as the only `auto_load: true` row.
- **Touch points:** `agents/maintenancer/skill-set.yaml` (append 6 rows after line 37).
- **Verification:** `tests/unit/test_maintenancer_skill_versions_consistent.py` passes — manifest set == template set, frontmatter version == manifest version (the test asserts both, per its docstring at lines 1-9).
- **Agent hint:** `coder`.

### Task 2.7 — Extend pins for new skills + team membership
- **Description:** The existing pins are `test_maintenancer_skill_versions_consistent.py`, `test_maintenancer_report_scrutiny.py`, `test_maintenancer_fan_in_valve.py`, `test_maintenancer_team_members_within_team.py` (detail-plan.md:112-115). Confirm the version-consistency pin automatically covers the 6 new skills (it enumerates template files + manifest, so no edit needed — verify, don't assume). Extend `test_maintenancer_team_members_within_team.py` only if the role mapping (Task 2.2) introduces a new member (it does not). Name the pins Phase 5 (sibling) must keep green.
- **Touch points:** verification-only in this phase; pin files are in `tests/unit/` (owned by P5 sibling for additions).
- **Verification:** all four existing pins green with the new skills present; report-ALWAYS invariant has a pin named for P5.
- **Agent hint:** `tester` (P5 sibling) — Phase 2 names the required pins.

---

## Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Adding `"instance"` category grants broader spawn power to a seat with a deny list — must not also grant host-write | High | Keep `deny` byte-unchanged (Task 2.1); report writing stays in the `worker` child (D-A); verify with pin. |
| Report-ALWAYS not guaranteed if the report-writer child itself fails | High | Fan-in valve marks child `[incomplete]` after 1 re-dispatch; report-writer dispatch is unconditional; spec a scoped-write fallback with tradeoffs if a child cannot write (D-A) — do not silently pick it. |
| Skill seeding misses a lane (body without manifest row or vice versa) | Medium | Version-consistency pin asserts set equality (Task 2.6/2.7). |
| `load_skill` attached at spawn (wrong) → skill not loaded | Medium | Encode spawn-then-send contract explicitly (Task 2.4); detail-plan.md:976-986 precedent. |
| In-repo prompt edits assumed live immediately | Medium | Flag post-promote DB adoption as operator step (R3); this lane stages only. |
| Task-context truncation drops a skill payload | Low | Pass file PATHS, never artifacts (4000-char limit). |

---

## Acceptance criteria

- `agents/maintenancer/meta.json` has `"instance"`, `"subtree_messages"`, `"subtree_status"` in allow; `deny` unchanged; JSON valid.
- Six new skill bodies + six matching `skill-set.yaml` rows exist; version-consistency pin green.
- `rule.md` / `workflow.md` encode: 5-lane + report-writer dispatch, per-lane status, report-ALWAYS incl. total failure, seat non-mutating, no idle gating, load_skill spawn contract, path-passing rule.
- Post-promote DB prompt adoption is explicitly flagged as an operator step (not done in-lane).
- All four maintenancer pins stay green; new pins named for P5.

---

## Verification plan (pins this phase needs; P5 sibling owns the suite)

- Extend/keep green: `tests/unit/test_maintenancer_skill_versions_consistent.py` (set equality + version match — must cover 6 new skills), `tests/unit/test_maintenancer_team_members_within_team.py`, `tests/unit/test_maintenancer_report_scrutiny.py`, `tests/unit/test_maintenancer_fan_in_valve.py`.
- NEW pin required (name for P5): report-ALWAYS-on-total-failure — assert the workflow dispatch of the report writer is unconditional and the seat `deny` list is unchanged.
- Integration precedent to mirror (P5): `tests/integration/test_maintenancer_end_to_end.py` flow at detail-plan.md:971-988 (spawn → load_skill → evidence fan-in → scrutiny → report carries evidence).

---

## Open questions

- **Scoped-write fallback (D-A):** if the `worker` child cannot write the report under the current tool policy, the named fallback is a scoped write capability on the report-writer child only. This plan does NOT pick it; the implementing lane must confirm the child path works first and escalate with tradeoffs if not. No blocker to planning.
- No contradiction found with the one-shot-at-now-to-agent capability (that is Phase 1 / D-D territory, verified in Phase 4).
