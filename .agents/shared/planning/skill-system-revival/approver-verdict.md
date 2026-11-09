# Approver Verdict — Skill-System Revival Phase 1 (ENSEMBLE-27 / board wi-4d1fa093)

**VERDICT: REJECT**
**Iteration: 001**
Date: 2026-11-09
Approver: approver (independent fresh-eyes pass)
Plan under review: `.agents/shared/planning/skill-system-revival/phase1-plan.md` (1,130 lines, rev 4)
Base: `ae5bbafed` @ `/home/nea/ensemble-src-wt-skill-revival-phase1`
Method: every blocking finding below was verified against the worktree code, the live schema (read-only SELECTs on `ensemble_dev`), and/or the staged-release scripts — not against the plan's own prose.

---

## Reasoning

This plan is unusually strong on the dimensions that usually fail. Anchor accuracy is excellent: I spot-checked ~60 `file:line` claims across all seven workstreams and found the overwhelming majority exactly correct, including subtle ones (`metrics_census.py:335`, the `filesystem.py` read-tool call sites `:596/:670` vs walk-tool `:754/:764/:1309/:1322`, `skill_evolution_service.py:1448-1457/:1474-1483`, `manager.py:1017/:1034/:2686/:2759`, `stage.sh:418/:437/:146-150`, `_build_frontend.sh:85/:105`, prompt baseline = 181 rows). The premise corrections P1–P9 are real (I re-derived the 15/10/21 → 31-of-46 roster, confirmed the keeper allow/deny lists byte-for-byte, confirmed the brake state and the cooldown numbers). §13.2's base-leg protocol is verified exactly against the evidence pack (4F/455P, 3F/456P, 4F/455P; intersection = the 3 migration reds), and the A2 polarity diagnosis is correct in both directions — production `gated` returns the enabled sense while the test fakes encode the corrected sense, so flipping `return not self._gate()` aligns them and the check-site census stays at 2. Ground rules are respected: no switch flipped, no promote, brake never engaged, canonical tree untouched, no in-lane DB write.

But five defects survive that are each individually verdict-driving, and four of the five are mechanically verifiable. Two of them (B1, B5) are defects of the exact class the plan's §19 ledger claims CLOSED — the ledger cross-check table (§19.1) verifies string identity between body and `decisions.md`, not logical or SQL validity, so it passes while the substance fails. That is the precise failure mode the project's own operating knowledge names ("a plan's ledger can assert FIXED while the BODY still gates on the old predicate"). Because §1 instructs that the A4 exit criterion wording "the tester inherits … and must not paraphrase it," one unsatisfiable acceptance criterion (B4) would be handed verbatim to the gate. Reject; all fixes are local and the plan should converge quickly.

---

## Blocking Issues

### B1. §7 Workstream C — both brake-verification reads are syntactically invalid SQL against the live schema

**Evidence (executed):**
- C1 (`phase1-plan.md:355`): `SELECT queue_id, name, is_paused, updated_at FROM job_queues WHERE queue_id='bd075f0b…'` → `ERROR: column "name" does not exist`. The column is `queue_name` (verified via `information_schema.columns` on `job_queues`: queue_id, project_id, **queue_name**, queue_name_lower, queue_type, concurrency_limit, is_system, is_paused, description, default_max_retries, created_at, updated_at; also `daemon/repositories/job_queue/models.py:203`).
- C2 (`:356`): `SELECT job_queue_paused FROM projects WHERE id='agents-ensemble'` → `ERROR: column "id" does not exist`. `projects` has `project_id` (PK, UUID) and `name` (`daemon/repositories/project/models.py:381-389`); `'agents-ensemble'` is a **name**, not an id. Correct form: `WHERE name='agents-ensemble'` (name is UNIQUE per `uq_projects_name`).

- Expected: executable, recordable read-only evidence for C1–C6 ("§7 Acceptance: C1–C6 are executed and their output is recorded").
- Found: C1 and C2 error on first execution; C's acceptance cannot be met as written.
- Remediation: C1 → `SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id='…';` C2 → `SELECT project_id, name, job_queue_paused FROM projects WHERE name='agents-ensemble';`. Sweep §17 #20 and the F-21 disposition row (§19) for the same two errors. Note the irony: F-21 fixed `WHERE id` → `WHERE queue_id` and declared the class closed while leaving `name` (wrong column) in C1 and a fresh `WHERE id=` in C2 one row below.

### B2. §14.2 step 3 — the documented staging command exits 78 and, if corrected, stages into the wrong install root

**Evidence:** the plan's command (line 868-870) is `ENSEMBLE_ROLLBACK_SAFE=1 VERSION=v0.29.8 bash scripts/upgrade/stage.sh v0.29.8`.
- `stage.sh`'s arg parser (lines 91–125) accepts only `demo|live|sandbox` (positional target), `--version <v>`, `--skip-build`, `--allow-stale-stage`, `-h|--help`; any other token hits `*)` → `stage: unknown flag 'v0.29.8' — see --help` → **exit 78**. Verified by simulating the exact case statement.
- With no target token, `resolve_env "${TARGET_ARG:-${TARGET:-demo}}"` (stage.sh:137) defaults to **demo** → the release lands in the DEMO install root. §14.3's promote (`promote.sh live`) resolves `REL="$INSTALL_DIR/releases"` against the **live** root (promote.sh:163-164) and would not find the payload.
- Live staging additionally requires `ENSEMBLE_UPGRADE_LIVE=1` (`require_live_guard`, lib.sh:1363-1376), absent from §14.2.

- Expected: a staging command that produces `releases/v0.29.8/` in the install root the documented promote consumes.
- Found: the literal command fails at argv parse; the corrected flag form still targets demo.
- Remediation: `ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=v0.29.8 bash scripts/upgrade/stage.sh live` (or explicitly `sandbox`/`demo` with a stated promote path). Also note staging to live writes the `ENSEMBLE_SELF_ENV` marker into the live `.env` and chmods it (stage.sh:718-760) — disclose that as an expected live-install mutation in §14.4.

### B3. §14.2 step 5 / §14.4 item 7 — the migration census's primary method is invalid: the staged payload contains no `daemon/` tree

**Evidence:** the census is specified as `diff -r <feature-tree>/daemon/migrations/versions <staged>/daemon/migrations/versions` → EMPTY, premised on "the stage copies the repo tree verbatim." Verified `stage.sh` copy list (lines 395–425): it copies **only** `ensemble-prod`, `agents/`, `frontend/dist/frontend/browser/`, `plugins/`, `config.yaml`, `launcher.sh`. The daemon — and therefore `daemon/migrations/versions/` — exists only inside the frozen PyInstaller binary. `<staged>/daemon/migrations/versions` does not exist; `diff -r` exits 2 rather than returning empty.

- Expected: a Gate-4 census that executes.
- Found: the primary method errors; the premise it rests on is false. The plan names the correct method (binary `CArchiveReader` TOC read with `YYYYMMDD_HHMMSS` prefixes + `uniq -d` empty) only as a conditional fallback "if that byte-identity premise is ever found to be false" — and it is false.
- Remediation: promote the TOC read to the primary (and only) method; strike the diff-`r` form from §14.2 step 5 and §14.4 item 7; keep `git diff --name-only <prev-tag>..HEAD -- daemon/migrations/versions/` → EMPTY as the cheap secondary (that part is valid). The `known_schema_gen` manifest field is a corroborating signal if needed.

### B4. §5 A3 — the headline acceptance ("3,457 → 0") is not achievable by the change A3 specifies, and A3's own acceptance #3 asserts the opposite

**Evidence:**
- The plan's census (§5 A3 / §17 #17): `feedback_applied` NULL=3,547 / TRUE=6,091 / FALSE=470; the false-eligible window is `selected AND task_succeeded AND feedback_applied IS NULL` = 3,457 rows.
- The specified fix (Implementation shape; §18 row 8; §19.1 C-1; DS-43): the gate predicate becomes **`feedback_applied IS NOT NULL`** (with `selected`/`task_succeeded`/`applied` explicitly "available as corroborating context", i.e. not in the predicate).
- Every row in the 3,457 window has `feedback_applied IS NULL`; `IS NOT NULL` is false for exactly those rows. Switching `== True` → `IS NOT NULL` widens the refusal set by only the **470 FALSE rows, which are not in the window**. The window population's gate verdict is unchanged.
- The "measured count" is defined (Acceptance 1) by a census predicate over DB rows — a property of the data, not of the code. §4 forbids DB writes from this lane, and the plan explicitly says the missing completion-hook writer is NOT built. Nothing in Phase 1 writes `feedback_applied`, so the count cannot move from 3,457 to 0.
- Acceptance 3 states the contradiction outright: "an instance with `selected=True, applied=False, task_succeeded=True, feedback_applied=NULL` is **capture-eligible**". That is precisely the window population — eligible under both the old and the new predicate.
- M-A3a ("neutralize the predicate → the measured count **returns to 3,457** → RED") is therefore vacuous: the count is 3,457 with or without the predicate.
- Acceptance 2 refers to "the metric that reports this population" — no such metric exists. There is no skill-* family in the metrics registry (catalog F0.1–F23, none skill-*; `grep skill daemon/services/metrics_query/wiring/*.py` → 0 hits), and §18 contains no row creating one. The plan itself concedes this in A4 ("The counter is NOT in the metrics registry (18 live families, none skill-*)").
- §19 row F-1 claims this FIXED; §19.1 C-1/C-2 mark the strings IDENTICAL=Y. The cross-check verified string equality, not reachability.

- Expected: an acceptance criterion derivable from the specified change.
- Found: an unsatisfiable criterion, inherited verbatim into the §1 DoD ("(iii) the capture-attribution gate honors `feedback_applied IS NOT NULL` so the false-eligible population is 0 post-fix (3457 -> 0, measured)") and into the A4 exit criterion the tester "must not paraphrase."
- Remediation (pick one and state it):
  1. If the intent is that the 3,457 window should be refused capture, the predicate must include the outcome fields (e.g. `∃ row WHERE instance_id=? AND (feedback_applied IS NOT NULL OR (selected AND task_succeeded))`) — the original diagnosis ("gates on feedback_applied==True **and ignores `selected`**", facts-extract §A3 defect 1) points here. Then re-derive M-A3a/b against that predicate.
  2. If the intent is the 470-row FALSE band (feedback filed but recorded-not-applied), restate the acceptance as that measured population with its own baseline, and re-derive the pins.
  3. If the deliverable is a metric, add the §18 row that creates it (family registration + label) and pin against it.
  In all cases reconcile Acceptance 1 with Acceptance 3.

### B5. §9 E2 — acceptance #1/#2 are false against the shipped code, M-E2c mutates a nonexistent field, and §18 contains zero E2 rows

**Evidence:**
- `SkillEvolutionService.evolve_skill` (`daemon/services/skill_evolution_service.py:452-457`) **explicitly rejects CAPTURED**: `raise ValueError("CAPTURED evolution cannot be initiated from evolve_skill(). Use capture_skill(instance_id, task_details) instead.")`; the docstring (":419") says "The CAPTURED path is intentionally NOT routed through this method." Via `_invoke_service` (skill_evolution_tools.py:92-150) a CAPTURED call returns an `ERROR: …` string. It is refused by design, not routed.
- E2 Acceptance 1 ("The `evolution_type` dispatch accepts **FIX / DERIVED / CAPTURED** and routes each to the right `SkillEvolutionService.evolve_skill` branch") and Acceptance 2 ("the `skill_evolve` tool's `evolution_type` argument routes `DERIVED` and `CAPTURED`") are therefore false against the worktree. There is no CAPTURED branch to route to.
- M-E2c ("make the capture admission path emit `FIX` instead of `CAPTURED` → RED") targets a field that does not exist: `enqueue_capture` (`skill_job_dispatcher.py:683-735`) stamps `metadata={"task_details": task_details}` with `job_type='skill_capture'`; **no `evolution_type` key is written anywhere on the capture path** (verified). E2's own §17 #18/#21 disclosure says the 116 rows are JOB rows with metadata `{skill_id, direction}` — yet another hint the field is absent.
- §18 ("every proposed edit, with anchors") has **no E2 row at all** (verified: 0 matches for E2 in the §18 table). E2 carries an Acceptance block, four mutation legs (M-E2a–d) and a pack assignment, but no file-by-file edit. Either it requires an unlisted production change that would reverse a deliberate guard, or it is already unprovable.

- Expected: acceptance criteria either already-true or delivered by listed edits.
- Found: false criteria + a vacuous mutation leg + an empty change-list footprint.
- Remediation: state E2's actual deliverable. If it is "prove FIX/DERIVED dispatch + the capture path produces a `skill_capture` job with `task_details`," write that (and drop CAPTURED from the tool-routing acceptance). If it is "route CAPTURED," add the §18 row for `skill_evolution_service.py:452` / the tool and disclose that a documented deliberate refusal is being reversed. Re-point M-E2c at a real field or delete it.

---

## Major Issues (non-blocking individually; fix before or during implementation)

### M1. §13.3 — `skill_evolution_lane_gate` is listed as "EXISTING — extend"; no such registered pack exists
`test/packs/skill_evolution_lane_gate.sh` does not exist (verified), and PACKS.md has no row by that name — the only occurrence is a *file path* (`tests/unit/test_skill_evolution_lane_gate.py`, PACKS.md:867) inside the `skill_id_verbatim_contract` pack's file list. §4 requires every pin to live in a REGISTERED pack and Gate 3 requires PACKS.md registration, so B2's four mutation legs currently have no legal destination. Fix: point B2 at `skill_id_verbatim_contract` (which already runs that test file) or register a new pack and say "NEW."

### M2. §6 B3 / §18 rows 16-17 — the change list omits the agent-identity plumbing the scope check requires
`_resolve_within_workdir(path, workdir)` (`filesystem.py:197-210`) receives only path and workdir. `read_file`/`list_directory` are module-level tools wrapped by `_make_workdir_aware(read_file, get_current_workdir)` (`daemon/tools/instance.py:6342-6343`), which injects only the project workdir. The calling agent's identity is available at the factory (`caller_agent_id`, instance.py:3069) but is not threaded into the resolver, and §18 lists only `filesystem.py` + the meta.json. As specified, the declared `read_scope` is unreadable at the enforcement site. Fix: add the `daemon/tools/instance.py` row (a scope-aware wrapper or closure binding), or bind the scope into the tool at construction and say so.

### M3. §5 A4 — acceptance #7 is ambiguous/contradictory and requires Phase-2-only facts
"#7: The per-skill mismatch set is **empty** against the corrected identity key" reads as the divergence gauge, which #4 and the §1 DoD pin at **32** with the 0 target BLOCKED-ON the A/B decision (and E4 declares no winners in Phase 1). If #7 means the counter-drift set, it duplicates #6 and should say so. Additionally "re-measured ≥ 24 h later **with the code fix live**" cannot occur in Phase 1 (§2 N2 no promote; §14.4 #8), and the counter repair is forbidden in-lane (§4 "No DB write to the live/dev DB from this lane"), so #6/#7 can only be proven in a pack environment — state that explicitly.

### M4. §9 E1 / §18 row 13 — the durable-cap SQL is dialect-fragile and its index claim is false
`metadata->>'skill_id'` is PostgreSQL JSONB syntax; `JSONBType` maps to plain JSON/TEXT on SQLite (where `->>` needs SQLite ≥ 3.38), and the house's own pattern is an explicit dialect split (`job_queue/repository.py:3249-3250`, `task/repository.py:2410-2422` → `json_extract` on SQLite). E1's pins land in `skill_evolution_unit_test`, which runs the SQLite path; a raw `->>` string breaks there. Also "a small, bounded, **indexed-on-`created_at`** scan" is wrong — `job_queue_items` has no `created_at` index (indexes: instance, project, queue, admission_state, idempotency_key, terminal_reason, deleted_at), and the JSON-path filter has no index at all. And the table is `job_queue_items`, not `job_items` (the plan writes `job_items` in four places including §18 row 13 and D2).

### M5. §2 N1 — the enforcement clause greps the staged tree for the kill-switch names and asserts absence; the names are compiled into the binary by necessity
`SKILL_CAPTURE_KILL_SWITCH_ENV = "ENSEMBLE_SKILL_CAPTURE_ENABLED"` (`skill_job_dispatcher.py:72`) and `SKILL_EVOLUTION_KILL_SWITCH_ENV` (:202) are string literals embedded in `ensemble-prod`. A whole-tree grep always hits. Scope the check to env surfaces (`.env`, effective environ, config.yaml) — which is what §14.4 item 4's second half already says.

### M6. §1 Goal 5 promises "retire the stale/shadow skill rows"; E4 retires zero rows
E4: R1 refused and left ACTIVE, R2 already inactive (no-op), R3 = 0 rows, and "NO winners are declared in Phase 1." Net Phase-1 flips: 0. The §1 DoD does not list retiring rows, so the plan is internally consistent at the DoD level — but Goal 5's wording over-promises. Reword Goal 5 to "build and prove the retirement mechanism; zero flips in Phase 1 (DS-44)."

---

## Minor / Notes

1. **§17 numbering is broken** — two items numbered 9 and two numbered 10; and §17 #11 says the outcome-field population "is not yet measured (C-4)" while §16.1 C-4 says "ANSWERED (census)" with the numbers and §17 #17 says "A3 is now exact … pinned against 3,457 at census time." #11 is a stale disclosure contradicting its own C-4 answer. The §19.1 cross-check table has no row covering §17, which is why it passed.
2. **§5 A4 FIND-IT prose is self-contradictory**: it says `grep -rn "GROUP BY name HAVING COUNT" daemon/` → 0 hits (verified correct) and then lists "the only sites the named grep actually returns" (`manager.py:8609/8662`, `checkpoint_adapter.py:1198/1898`, `maintenance.py:1477`) — those match `HAVING COUNT(*)`, not the named pattern; the named grep returns nothing. The substantive claim (no such aggregation exists) is true; the parenthetical is wrong.
3. `provider.py:69` is cited for the `publisher_cadence` kwarg; the kwarg is at :70 (storage :78 is right). Trivial.
4. A1's closure should use `getattr(app.state, "wedge_capture_watchdog", None)` — the house pattern (`routers/wedge_capture.py:102`, `wiring/sources.py:233`) — since the attribute may not exist before `api.py:3250` and a direct attribute read in `availability_facts` would raise.
5. G's preferred fix (i) with file-backed SQLite: the A/B fan-out issues three concurrent **write** transactions (`deactivate` + `resolve` + `update`) on one file; without WAL/`busy_timeout` this can trade the old flake for `database is locked`. The fixture sets only `PRAGMA foreign_keys=ON`. State the pragma requirements (or accept option (ii)).
6. A3 pins 3,457 as "exact"; the facts-extract's row-level figure is 3,450 (06:11–06:14Z) and the plan's own census is later. Record the census timestamp next to the pinned boundary so the tester's re-measure is comparable.
7. E4's `M-E4a` is labelled "the 'deactivates R1' pin" while E4 acceptance 1–3 require R1 to be **refused and verified still ACTIVE**. The discriminator is the refusal receipt, not a deactivation; relabel or the pin is unprovable as named.

---

## What the plan gets right

- **Anchor discipline is exemplary.** Across ~60 spot-checked `file:line` claims spanning all seven workstreams and the release scripts, essentially all verified correct — including the corrected B3 walk-tool anchors (`:754/:764`, `:1309/:1322`), the E3 echo sites (`:1260/:1491/:402/:1213`), and the staging internals (`stage.sh:418/:437/:146-150`, `_build_frontend.sh:85/:105`, `package.json` lacking `build:frontend`, prompt baseline = 181).
- **The premise corrections are real, not rhetorical.** P1's roster (15 true / 10 false / 21 absent = 31 of 46) re-derived exactly; P5's two-tool residual confirmed in code and docstring; P6's cooldown arithmetic confirmed; P8's allow-list confirmed byte-for-byte against `agents/skill-keeper/meta.json`.
- **§13.2's base-leg protocol is verified against the raw evidence.** Run counts (4F/455P, 3F/456P, 4F/455P with timings) match the evidence pack exactly; the intersection = the 3 migration reds; the `grep '^FAILED'`-only classifier warning is well-founded; the ≥3-run set-diff rule is the right response to a non-stable base.
- **G's decisive experiment is exactly as recorded** (11P/1F normal vs 12P/0F inlined, same tree/interpreter/command) and the flake mechanism is diagnosed at the right seam (`skill_evolution_service.py:1448-1457/:1474-1483` over the StaticPool shared connection at `tests/services/conftest.py:39-46`).
- **A2's polarity analysis is correct in both directions** — production `CensusSampler.gated` returns the enabled sense while the existing test fakes already encode the corrected sense, so `return not self._gate()` is the minimal fix, flips no existing pin, and keeps the behavioural check-site census at exactly 2 (verified).
- **A4's counter-drift diagnosis is correct**: the bump at `skill_metrics_service.py:1170-1186` is call-based with no transition guard, and the on-miss insert path is handled correctly in the proposed fix.
- **Honest reframing where honesty costs something.** A4's gauge redefinition (global-name divergence is a non-defect; within-identity-key = 32, 0 target BLOCKED-ON) is the right call and is stated without euphemism; E4's zero-flip deliverable is disclosed rather than dressed up; the A4 gauge home is downgraded to an honest FIND-IT rather than invented.
- **Ground rules respected throughout**: no switch flipped (N1 + §14.4 #4), no promote (N2 + §14.4 #8), brake never engaged (§7 is genuinely read-only in intent — see B1 for the SQL defect), canonical tree untouched (N4), no in-lane DB write (§4; E4.1's boot-time writer ships post-promote within the stated envelope).

---

## Skills Used
plan-approval (evaluation performed directly by the approver under the commissioning Leader's explicit instruction; no worker dispatch — the commission specified the artifact path and the fresh-eyes single-pass posture)

## Session IDs
approver (this instance) — no child instances dispatched

## Verdict
**REJECT — iteration 001.** Five blocking issues (B1–B5), all locally fixable. B1/B2/B3 are one-line SQL/CLI corrections; B4 requires choosing one of three stated framings and re-deriving two pins; B5 requires stating E2's actual deliverable and adding or removing a change row. None of the five undermines the plan's architecture or sequencing; all five would fail at Gate 1–4 if inherited as written.
