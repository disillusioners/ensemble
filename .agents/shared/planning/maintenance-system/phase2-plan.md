# Phase 2 — Orchestrator Agent Upgrade + Lane Skills (Central Maintenance System)

**Objective:** Upgrade the in-repo `maintenancer` agent from a solo repair coordinator into an orchestrator that can spawn/send per-lane worker children and carry one skill per lane, so the weekly run can fan out five isolated lanes plus a report writer without ever mutating the host itself.

**Status:** PLANNED

---

## Scope

### In scope
- `agents/maintenancer/meta.json`: grant four literal tool names (`spawn_instance`, `send_message`, `subtree_messages`, `subtree_status`) per BR-8 (the seat already holds `instance_read`+`instance_ops`); deny list byte-unchanged; map the three existing `team_members` to lane roles. BR-9 run-containment Cardinal rule lands in the same change.
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
- **Parallelizable-with:** Phase 3 (lane procedure design) and Phase 4 (report schema) can proceed in parallel with Phase 2 — the skill *names* and *contracts* are fixed here, and the skill BODIES are authored HERE (T2.5, all NEW files); P3 defines the weekly PROCEDURE each body encodes (P3 authors no skill file). Coordinate on the exact skill file names listed above.
- **Base:** worktree `/home/nea/ensemble-src-wt-maintsys`, branch `feature/central-maintenance-system`, base `origin/latest @ 1b9797ab7`. NEVER operate from `/home/nea/ensemble-src` (detached canonical, stale for this area, zero hbk code).

---

## Design decisions consumed

- **R3** — scheduler dispatches DIRECTLY to `maintenancer`; agent upgraded IN-REPO; live DB prompt adoption is a post-promote operator step (flagged, out of lane scope).
- **R4** — each lane = isolated execution boundary; per-lane error capture; report ALWAYS written with per-lane status.
- **D-A** — the `maintenancer` seat stays non-mutating (`deny` keeps `write_file`/`edit_file`/`git_commit`); report + receipt + INDEX authoring routes through a dedicated worker child with ABSOLUTE paths. Fallback (scoped write capability) only if R4 cannot be guaranteed via a child — spec with tradeoffs, do not silently pick.
- Existing detail-plan precedent: `.agents/shared/planning/maintenancer-agent/detail-plan.md:976-986` (worker spawn + `load_skill` + evidence fan-in), `:108-115` (fan-in/scrutiny/team-membership pins).

---

## Binding requirements (architect addendum §7 — BR-8..BR-14)

Each BR below is a labeled, grep-verifiable block carrying its operative text. The addendum stays as the verdict record.

- **BR-8** Grant literal tool names, NOT the `"instance"` category: allow += `spawn_instance`, `send_message`, `subtree_messages`, `subtree_status`; deny byte-unchanged; omit `terminate_instance`/`pause_instance`/`resume_instance`/`set_instance_tunable`/council quartet/`list_instances`/`get_instance_info` (fleet reads already held via `instance_read`; audited kill already held via `instance_ops`). Correct T2.1's premise (seat already holds `instance_read`+`instance_ops`). Note: the allow grammar accepts literal tool names (`resolve_tool_filter`, `daemon/tools/instance.py:455`); `"instance"` is NOT in `PRIVILEGED_TOOL_CATEGORIES` — literal grants carry no category-bypass hazard.
- **BR-9** Cardinal-tier run-containment rule (rule.md + workflow mirror), landing in the SAME change as the grant: touch ONLY children of THIS run; never message/pause/resume/terminate instances not spawned this run; wedged-child kill via audited `instance_terminate` only; never pass `join_room`/`member_name` at spawn.
- **BR-10** P5 seat pins — exact-set deny == `{git_commit, edit_file, write_file}`; resolved-surface assertion (via `resolve_tool_filter`) EXCLUDES `terminate_instance`, `pause_instance`, `resume_instance`, `set_instance_tunable`, `convene_council*`, `spawn_councilor`; one workflow line: effective spawnable set is 4 (kb-writer implied by `knowledge` via `TOOL_REQUIRED_AGENTS`, `daemon/tools/_auth.py:35`).
- **BR-11** Event-driven lane deadline — a lane is `failed-timeout` when a parent wake carries `source=system:watchdog` (`daemon/services/waiting_children_watchdog.py:627`, ≤3600s threshold+cadence, provenance `system:watchdog`) or `source=system:long-tool-nudge` (canonical literal `LONG_TOOL_NUDGE_SOURCE = "system:long-tool-nudge"`, `daemon/services/long_tool_nudge.py:347`; wake sites `:1471-1475`) naming that child; apply the valve once, else `[incomplete]`, then proceed. Deadline covers the initial dispatch AND its single re-dispatch. Report-writer fires on lane resolution OR global deadline (same wake mechanism bounds the writer wait ≈2h). NO polling (`workflow.md:71` forbids it). Loop-breaker (threshold 3, `daemon/config.py:6648`) and bash 1800s cap (`daemon/tools/bash.py:1283`) already bound the two likeliest wedge shapes — note them.
- **BR-12** Lane-status mapping table (P2 T2.4): child `ERROR` ⇒ `failed`; silent-past-deadline ⇒ `degraded` + `[incomplete]`; `COMPLETED` ⇒ `ok|degraded` per its result block.
- **BR-13** Report-writer ladder (pre-decided; replaces the "named, not chosen" fallback): L0 dispatch, write order run→receipt→INDEX last · L1 crash/wedge ⇒ ONE fresh minimal-stub writer (no skill load) · L2 ⇒ ONE bare worker child, no load_skill, skeleton all-failed + best-effort receipt/INDEX (this IS the scoped-write fallback, staying a CHILD — PD-6 seat-non-mutating intact) · L3 accepted documented reportless terminal, surfaced by BR-15. The seat never writes (PD-6 intact). DP-1 ruling row (verbatim, lane leader 2026-11-22, ACCEPTED): "BR-13 L3 accepts a surfaced, documented reportless week when both writer rungs fail. Rationale: failure-isolation contract = a lane failure never kills the report; double independent writer failure is beyond contract; BR-15 ensures the residual is surfaced (detector + orphan listing), never silent."
- **BR-14** run_id single authority = the report-writer child. Strike the seat-mint step in P2 T2.4 (the seat has no bash/filesystem). Name the daemon venv interpreter (`/home/nea/ensemble-src/.venv/bin/python` — bare `python3` fails on this host: ModuleNotFoundError sqlalchemy). Bash fallback `ckpt-$(date -u +%Y%m%d_%H%M%S%6N)-$(openssl rand -hex 4)` is a BINDING rung in the skill body. Carry a `run_token` in the dispatch task; re-dispatches reuse it (INDEX dedup survives re-mints).

---

## Tasks

### Task 2.1 — Grant four literal tool names to `meta.json` (BR-8)
- **Description:** BR-8 corrects the premise: the seat ALREADY holds `instance_read` + `instance_ops` (`agents/maintenancer/meta.json:19-20`). Do NOT grant the whole `"instance"` category (14 tools). Instead grant four literal names: allow += `spawn_instance`, `send_message`, `subtree_messages`, `subtree_status`. Keep `tools.deny` EXACTLY `["git_commit", "edit_file", "write_file"]` (verified at `agents/maintenancer/meta.json:26`) — deny byte-unchanged. OMIT by omission: `terminate_instance`, `pause_instance`, `resume_instance`, `set_instance_tunable`, the council quartet, `list_instances`, `get_instance_info` (fleet reads already held via `instance_read`; the audited wedged-child kill already held via `instance_ops`). Note: the allow grammar accepts literal tool names (`resolve_tool_filter`, `daemon/tools/instance.py:455`); `"instance"` is NOT in `PRIVILEGED_TOOL_CATEGORIES` — literal grants carry no category-bypass hazard. BR-9 (run-containment Cardinal rule) lands in the SAME change as this grant.
- **Touch points:** `agents/maintenancer/meta.json` (`tools.allow` lines 12-25; `tools.deny` line 26 unchanged).
- **Verification:** JSON parses; the four literal names present in allow; deny list byte-unchanged; resolved-surface assertion (BR-10) excludes the omitted names.
- **Agent hint:** `coder` (config-only edit).

### Task 2.2 — Map team_members to lane roles
- **Description:** Keep `team_members: ["explorer", "worker", "coder"]` (`agents/maintenancer/meta.json:28`) and document the mapping in `workflow.md`: `explorer` = read-only surveys (POK census reads, anomaly reads, skill-table reads, metrics digests); `worker` = bash sweeps + report/receipt/INDEX writing (the only member that writes, with ABSOLUTE paths, per D-A); `coder` = fix sketches only (route-to-lane recommendations, never applied inline). No new team members needed — the three existing roles cover all five lanes + report writer.
- **Touch points:** `agents/maintenancer/workflow.md` (new "Lane role mapping" subsection).
- **Verification:** workflow names all three roles with their lane responsibilities; no role is granted a capability outside its `meta.json`.
- **Agent hint:** `doc-writer` / `coder`.

### Task 2.3 — Orchestrator `rule.md` additions (weekly-run cardinal rules + BR-9 run-containment)
- **Description:** Add cardinal/guideline rules for the weekly run WITHOUT duplicating the END TURN invariant (already at `rule.md:7` and `workflow.md:69-73`). New rules: (a) the run dispatches five lanes + a report writer as isolated worker children; (b) each lane returns a status `ok|degraded|failed` + a result block; (c) REPORT ALWAYS written — even if every lane fails, the report writer child still runs with total-failure statuses; (d) report authorship goes to the `worker` child with ABSOLUTE paths (D-A), the seat never writes; (e) no idle gating — the run executes regardless of busyness (ruling). **BR-9 Cardinal-tier run-containment rule (lands in the SAME change as the grant, rule.md + workflow mirror — T2.3 is the SOLE owner of the workflow mirror; T2.4 does not duplicate it):** touch ONLY children of THIS run; never message/pause/resume/terminate instances not spawned this run; wedged-child kill via audited `instance_terminate` only; never pass `join_room`/`member_name` at spawn. Keep report-sanity scrutiny (`rule.md:9`) and fan-in valve (`workflow.md:36-45`) intact.
- **Touch points:** `agents/maintenancer/rule.md` (new rules section, ~lines 12-19 area, before the `---` at line 13 or as new Guidelines); mirror in `workflow.md`.
- **Verification:** rules state report-ALWAYS incl. total failure; seat non-mutating preserved; run-containment Cardinal rule present in both rule.md and workflow.md; no contradiction with Cardinal #3/#5.
- **Agent hint:** `coder` / `doc-writer`.

### Task 2.4 — Orchestrator `workflow.md` additions (weekly-run dispatch flow + BR-11/BR-12/BR-13/BR-14)
- **Description:** Extend the dispatch pattern (`workflow.md:19-32`, one skill per worker, spawn-then-send, END TURN) with a "Weekly Run" flow: (1) **BR-14: run_id single authority = the report-writer child** — STRIKE the seat-mint step (the seat has no bash/filesystem and cannot mint); carry a `run_token` in the dispatch task; the writer child mints via the named-interpreter shell-out (`/home/nea/ensemble-src/.venv/bin/python` per BR-14; see P4 D-H/Task 4.1) and re-dispatches reuse the `run_token` so INDEX dedup survives re-mints; (2) dispatch up to 3 concurrent lane children per wave (concurrency cap at `workflow.md:29-32`), each with ONE `load_skill`, END TURN after each wave; (3) fan-in statuses via the existing fan-in escape valve (`workflow.md:36-45`, max re-dispatch = 1, then mark `[incomplete]`); (4) **BR-12 lane-status mapping:** child `ERROR` ⇒ `failed`; silent-past-deadline ⇒ `degraded` + `[incomplete]`; `COMPLETED` ⇒ `ok|degraded` per its result block; **never-dispatched** (a lane whose wave was never sent before the global deadline — 5 lanes / 2 waves at the concurrency cap 3 + the writer) ⇒ `failed` + `[incomplete]`; (5) after all lanes resolve (ok/degraded/failed/incomplete), dispatch the `run-report-writer` child — this step is unconditional. **BR-11 event-driven lane deadline:** a lane is `failed-timeout` when a parent wake carries `source=system:watchdog` (`daemon/services/waiting_children_watchdog.py:627`, ≤3600s threshold+cadence) or `source=system:long-tool-nudge` (`daemon/services/long_tool_nudge.py:1471-1475`) naming that child; apply the valve once, else `[incomplete]`, then proceed. The deadline covers the initial dispatch AND its single re-dispatch. The report-writer fires on lane resolution OR the global deadline (the same wake mechanism bounds the writer wait ≈2h). Global-deadline arithmetic (binding): 5 lanes / 2 waves (concurrency cap 3) ⇒ global deadline = dispatch of wave 1 + its single re-dispatch allowance + watchdog wake latency (≤3600s) + wave 2 the same + writer allowance — the run NEVER exceeds it; the deadline covers never-dispatched lanes too. NO polling (`workflow.md:71` forbids it). Loop-breaker (threshold 3, `daemon/config.py:6648`) and the bash 1800s cap (`daemon/tools/bash.py:1283`) already bound the two likeliest wedge shapes — note them. **BR-13 report-writer ladder (pre-decided, replaces the "named, not chosen" fallback):** L0 dispatch (write order run→receipt→INDEX last — crash leaves no half-ledger row) · L1 crash/wedge ⇒ ONE fresh minimal-stub writer (no skill load) · L2 ⇒ ONE bare worker child, no load_skill, skeleton all-failed + best-effort receipt/INDEX (this IS the scoped-write fallback, staying a CHILD — PD-6 seat-non-mutating intact) · L3 accepted documented reportless terminal, surfaced by BR-15. DP-1 ruling row (verbatim, lane leader 2026-11-22, ACCEPTED): "BR-13 L3 accepts a surfaced, documented reportless week when both writer rungs fail. Rationale: failure-isolation contract = a lane failure never kills the report; double independent writer failure is beyond contract; BR-15 ensures the residual is surfaced (detector + orphan listing), never silent." Encode the **load_skill spawn contract**: `load_skill` rides `send_message` ONLY — spawn the child with no task first, then `send_message(..., load_skill="<one>")` while the child is free; NEVER pass the artifact in the task context (truncated at 4000 chars at the tool layer — pass file PATHS instead).
- **Touch points:** `agents/maintenancer/workflow.md` (new "Weekly Run Flow" + "load_skill spawn contract" + "lane deadline" subsections; the BR-9 run-containment WORKFLOW MIRROR is owned by T2.3 — do NOT duplicate it here). **No file claim on `run-report-writer.md` here** — T2.4 states the REQUIREMENT (the skill body must encode the BR-13 ladder + BR-14 mint mechanics); the file is written solely by T2.5.
- **Verification:** flow makes the report-writer dispatch unconditional; path-passing rule stated; END TURN per wave preserved; run-containment rule present; lane-status mapping table present; BR-13 ladder + DP-1 row verbatim; BR-14 run_token + venv + bash fallback present.
- **Agent hint:** `coder` / `doc-writer`.

### Task 2.5 — Create six lane skill bodies under `skills-template/`
- **Description:** Author one skill body per lane + report writer, each with frontmatter `version: "1.0.0"`, `category: maintenance`, `auto_load: false` (except as noted), following the shape of existing `health-check.md` (boundary + contract + focus areas):
  - `lane-disk-sweep.md` — bash sweep worker fence recipe (detail in P3 Lane 1).
  - `lane-pok-sweep.md` — read-only POK census + delta reconcile (P3 Lane 2).
  - `lane-skill-check.md` — consolidated skill-system check (P3 Lane 3).
  - `lane-abnormality-check.md` — anomaly + old-tier health watch (P3 Lane 4).
  - `lane-metrics-watch.md` — metrics digest read (P3 Lane 5).
  - `run-report-writer.md` — **T2.5 is the SOLE owner of this file (package-wide)**: report + receipt + INDEX authoring with ABSOLUTE paths (P4 spec), the BR-13 ladder, the BR-14 mint mechanics (venv PRIMARY + bash FALLBACK + `run_token`), and **the BR-16 artifact root stated ONCE in the body: reports/receipts/INDEX are written to the LIVE-INSTALL root `.agents/maintenancer/RESULTS/` (resolved against the daemon's live-install project root — NEVER a worktree; absolute `write_file` bypasses the boundary check, so the root lives in this skill body)**. No other task writes this file; T2.4 and P4 T4.1–T4.8 are spec-only consumers.
  Each skill's result block is the PD-4a CANONICAL RESULT BLOCK, frozen in `decisions.md` PD-4/PD-4a — CITED BY REFERENCE, never re-embedded (this removes the P2∥P3 schema-authoring ordering dependency; any lane body whose block drifts from PD-4a is a defect). Each skill also carries its three-class recommendation routing.
- **Touch points:** `agents/maintenancer/skills-template/lane-disk-sweep.md`, `lane-pok-sweep.md`, `lane-skill-check.md`, `lane-abnormality-check.md`, `lane-metrics-watch.md`, `run-report-writer.md` (all NEW).
- **Verification:** each has valid frontmatter; no skill writes outside its lane boundary; report-writer is the only skill that writes files, with absolute paths.
- **Agent hint:** `doc-writer` (bodies) reviewed by `coder`.

### Task 2.6 — Add six matching rows to `skill-set.yaml`
- **Description:** Add one row per new skill with `version: "1.0.0"`, `auto_load: false`, `category: maintenance`, and a one-line description. Existing 7 rows (`agents/maintenancer/skill-set.yaml:1-37`) stay unchanged. Seeding keys off `skill-set.yaml` — a skill needs BOTH a template body AND a manifest row (research finding). Keep `kb-curator` as the only `auto_load: true` row.
- **Touch points:** `agents/maintenancer/skill-set.yaml` (append 6 rows after line 37).
- **Verification:** `tests/unit/test_maintenancer_skill_versions_consistent.py` passes — manifest set == template set, frontmatter version == manifest version (the test asserts both, per its docstring at lines 1-9).
- **Agent hint:** `coder`.

### Task 2.7 — Extend pins for new skills + team membership
- **Description:** The existing pins are `test_maintenancer_skill_versions_consistent.py`, `test_maintenancer_report_scrutiny.py`, `test_maintenancer_fan_in_valve.py`, `test_maintenancer_team_members_within_team.py` (detail-plan.md:112-115). **The version-consistency pin does NOT automatically cover the 6 new skills — it MUST be updated:** `test_seven_skills_in_manifest` (`tests/unit/test_maintenancer_skill_versions_consistent.py:150-158`) asserts `len(manifest["skills"]) == 7`; with the 6 additions the manifest carries 13 ⇒ the pin REDs unchanged. This task owns updating it to the final state (set-equality over the 13-skill set — name-level, not count-level, so future additions reddened deliberately). `test_maintenancer_team_members_within_team.py` needs no change (Task 2.2 introduces no new member). Name the pins Phase 5 (sibling) must keep green, INCLUDING the updated count pin.
- **Touch points:** `tests/unit/test_maintenancer_skill_versions_consistent.py` (update `test_seven_skills_in_manifest` :150-158 to the 13-skill set-equality — THIS task owns the edit; P5 keeps it green); `tests/unit/` pin files otherwise owned by P5 sibling for additions.
- **Verification:** all four existing pins green with the new skills present; report-ALWAYS invariant has a pin named for P5.
- **Agent hint:** `tester` (P5 sibling) — Phase 2 names the required pins.

---

## Risks & mitigations

| ID | Risk | Impact | Mitigation |
|---|---|---|
| R-P2-1 | Granting spawn power to a seat with a deny list — must not also grant host-write (BR-8/BR-9) | High | Literal four-name grant only (no category); deny byte-unchanged; BR-9 run-containment rule lands in the SAME change as the grant (T2.3); BR-10 pins are P5-OWNED (implemented in P5 T5.5, asserted here by name only). |
| R-P2-2 | Report-ALWAYS not guaranteed if the report-writer child itself fails (BR-13) | High | BR-13 ladder L0→L1→L2→L3 (L2 is the pre-decided scoped-write fallback, stays a CHILD); DP-1 ruling row verbatim; BR-15 surfaces a reportless week; fan-in valve marks `[incomplete]` after 1 re-dispatch. |
| R-P2-3 | Skill seeding misses a lane (body without manifest row or vice versa) | Medium | Version-consistency pin asserts set equality (Task 2.6/2.7). |
| R-P2-4 | `load_skill` attached at spawn (wrong) → skill not loaded | Medium | Encode spawn-then-send contract explicitly (Task 2.4); detail-plan.md:976-986 precedent. |
| R-P2-5 | In-repo prompt edits assumed live immediately | Medium | Flag post-promote DB adoption as operator step (R3); this lane stages only. |
| R-P2-6 | Task-context truncation drops a skill payload | Low | Pass file PATHS, never artifacts (4000-char limit). |

---

## Acceptance criteria

- `agents/maintenancer/meta.json` grants the four literal names (`spawn_instance`, `send_message`, `subtree_messages`, `subtree_status`); deny byte-unchanged; JSON valid (BR-8).
- BR-9 run-containment Cardinal rule present in `rule.md` + `workflow.md`.
- BR-11 event-driven lane deadline + BR-12 lane-status mapping present in `workflow.md`.
- BR-13 ladder + DP-1 row verbatim present in `workflow.md`/`run-report-writer` skill.
- BR-14 run_token + venv interpreter + bash fallback present; no seat-mint step.
- Six new skill bodies + six matching `skill-set.yaml` rows exist; version-consistency pin green.
- `rule.md` / `workflow.md` encode: 5-lane + report-writer dispatch, per-lane status, report-ALWAYS incl. total failure, seat non-mutating, no idle gating, load_skill spawn contract, path-passing rule, run-containment, lane deadline, status mapping, writer ladder.
- Post-promote DB prompt adoption is explicitly flagged as an operator step (not done in-lane).
- All four maintenancer pins stay green; new pins named for P5 (BR-10).

---

## Verification plan (pins this phase needs; P5 sibling owns the suite)

- Extend/keep green: `tests/unit/test_maintenancer_skill_versions_consistent.py` (set equality + version match — must cover 6 new skills), `tests/unit/test_maintenancer_team_members_within_team.py`, `tests/unit/test_maintenancer_report_scrutiny.py`, `tests/unit/test_maintenancer_fan_in_valve.py`.
- NEW pin required (name for P5, BR-10): exact-set deny == `{git_commit, edit_file, write_file}`; resolved-surface assertion (via `resolve_tool_filter`) EXCLUDES `terminate_instance`, `pause_instance`, `resume_instance`, `set_instance_tunable`, `convene_council*`, `spawn_councilor`; one workflow line: effective spawnable set is 4 (kb-writer implied by `knowledge` via `TOOL_REQUIRED_AGENTS`, `daemon/tools/_auth.py:35`).
- NEW pin required (name for P5, BR-11): event-driven lane deadline — a lane is `failed-timeout` on a parent wake carrying `source=system:watchdog` or `source=system:long-tool-nudge` naming that child; apply the valve once, else `[incomplete]`; NO polling.
- NEW pin required (name for P5, BR-13): report-writer ladder L0→L1→L2→L3 + DP-1 ruling row verbatim; L2 is the pre-decided scoped-write fallback (stays a CHILD); BR-15 surfaces a reportless week.
- Integration precedent to mirror (P5): `tests/integration/test_maintenancer_end_to_end.py` flow at detail-plan.md:971-988 (spawn → load_skill → evidence fan-in → scrutiny → report carries evidence).

---

## Open questions

- **Scoped-write fallback (D-A / BR-13):** BR-13 L2 pre-decides the fallback as a bare worker child (no load_skill, skeleton all-failed + best-effort receipt/INDEX) — the seat never writes (PD-6 intact). DP-1 accepts a surfaced, documented reportless week when both writer rungs fail (lane leader 2026-11-22, ACCEPTED); BR-15 surfaces it. No open decision remains for planning.
- No contradiction found with the one-shot-at-now-to-agent capability (that is Phase 1 / D-D territory, verified in Phase 4).
