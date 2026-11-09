# Plan Review: skill-system-revival Phase 1 (councilor peer)

**Reviewer:** worker (councilor, peer track)
**Date:** 2026-11-09
**Plan:** `.agents/shared/planning/skill-system-revival/phase1-plan.md` (902 lines) + `decisions.md` (563 lines, DS-01..DS-37)
**Base sha:** ae5bbafedde90c7af1065b9660196bbffbbbf9c1
**Worktree:** /home/nea/ensemble-src-wt-skill-revival-phase1
**Read-only:** yes (single permitted write = this file)

---

## VERDICT: REJECT

The plan is exceptionally thorough, well-structured, and largely correct on its facts and anchors — but it **ships vacuous pins in Workstream A3**, and that workstream **cannot meet its own acceptance criteria** with the design as written. Per the gate's stated rule ("a plan that ships vacuous pins must not pass"), this is a REJECT, scoped to A3. Every other workstream (A1, A2, A4, B, C, D, E, F, G) is implementable as written and is not blocked by this finding. The dispatcher may choose to re-run only A3 or to accept the finding and proceed; my recommendation is to re-design A3 before any implementation begins, because A3's pins (M-A3a, M-A3d) would fail the house non-vacuity bar at Gate 3 and waste a full tester cycle.

---

## Findings

| # | Area | Section | Severity | Issue | Fix Suggestion |
|---|------|---------|----------|-------|----------------|
| F-1 | feasibility / pins | §5 A3; DS-06, DS-07 | 🔴 CRITICAL | A3's proposed fix is a no-op: the gate is repointed from `has_applied_for_instance` (gates on `feedback_applied == True`) to a new `has_used_skill_for_instance` (gates on `applied == True`), but `applied` is a **mirror** of `feedback_applied`. `update_feedback` (repository.py:1308-1316) sets `record.feedback_applied = applied` and then `if applied: record.applied = True` ("Monotonic — never reset to False"). Creation sets `applied=False` (skill_metrics_service.py:878, "Set later by skill_feedback tool"); the on-miss insert sets `applied=bool(applied_bool)` from the same feedback call (skill_metrics_service.py:1057). Therefore `applied == True` ⟺ `feedback_applied == True` in every code path, and the new predicate returns the **identical** result set. Acceptance #1 (false-eligible population drops from 3,782) cannot be met; mutation legs M-A3a ("revert to has_applied_for_instance → RED") and M-A3d ("leave one gate on the old predicate → RED") stay GREEN because reverting changes nothing — i.e. they are **vacuous**, the exact class the plan itself warns about (§5 A5, "a pin that stays green with the guard removed is vacuous and worthless"). | Re-scope A3 to a genuinely different signal, OR redefine what "applied" means. Two honest options: (i) gate on `selected == True AND task_succeeded == True` (a skill was offered and the task succeeded → success may be attributed to it) — this is the over-broad option the plan rejected, but it is the only one that changes behavior; the plan must then re-argue its rejection with the new numbers; (ii) acknowledge that "applied" is feedback-driven and that the capture gate's purpose ("don't create a sibling capture for a success already attributed to another skill") cannot be answered by any column that exists today, and split A3 into (a) a real signal (e.g. injection-record provenance) that must be added, and (b) an honest disclosure that the 3,782 figure is an upper bound that the current schema cannot refine. Either way, M-A3a/M-A3d must be rewritten so the mutation actually changes behavior. |
| F-2 | staged-release gate | §14.2 (items 1-9); §14.4 | 🟡 MAJOR | The staged-release gate omits the FE provenance sidecar handling, which the task's release-gate checklist requires. `scripts/upgrade/stage.sh:405-419` deliberately `rm -f`s `index.html.build-provenance.json` from the release payload (it is web-servable; shipping it would expose a per-deployment hash + git HEAD), and the verifier reads the sidecar from the **SOURCE tree** (`$REPO_ROOT`), not the staged payload. The manifest per-file map is computed **after** the rm, so the sidecar is not part of the release's integrity contract. The plan's §14.2 lists 4-way sha, migration census, prompt baseline, and interpreter measurement, but never states that (a) the FE sidecar's absence from the payload is asserted as **CORRECT**, (b) the verifier reads it from the source tree, (c) the check is **not** gated on `build_tool=="npm run build"` (the FE sidecar's label is a hardcoded literal naming a non-existent npm script — a disclosed deviation). | Add a §14.2 item (and a matching §14.4 checklist line) stating the FE sidecar handling: read from source tree, absence from payload asserted correct, not gated on `build_tool=="npm run build"`, with the npm-script-label deviation disclosed. |
| F-3 | clarity / anchors | §6 B3; §17 item 1; §18 row 16 | 🟡 MAJOR | B3's line-number citations for `glob_files` and `grep_files` are wrong. The plan says "`glob_files` (`:1114`) and `grep_files` (`:1518`) already resolve through `_resolve_search_root` (`:252-283`)", but `daemon/tools/filesystem.py:1114` is `write_file` and `:1518` is `edit_file` — both use `_resolve_within_workdir`, not `_resolve_search_root`. The substantive claim is correct (`glob_files` def is at `:754` and calls `_resolve_search_root` at `:764`; `grep_files` def is at `:1309` and calls `_resolve_search_root` at `:1322`), but the cited lines point at the two write tools the plan is trying to distinguish from. An implementer following the plan's anchors would land the scope check in `write_file`/`edit_file` instead of the read tools. | Correct the anchors to `glob_files` `:754`/`:764` and `grep_files` `:1309`/`:1322`. The prior-art D3 citations (`:584/:657/:748/:1040`) are also drifted; the plan's corrected set should be `read_file :663`/`:670`, `list_directory :590`/`:596`, `glob_files :754`/`:764`, `grep_files :1309`/`:1322`, `write_file :1105`/`:1114`, `edit_file :1508`/`:1518`. |
| F-4 | clarity / anchors | §18 row 16; §6 B3 | 🟢 SUGGESTION | The §18 file-by-file list for B3 cites `:197-225, :596, :670` (correct call sites) while the B3 prose cites `:1114/:1518` (wrong, per F-3). The two disagree. | Make §18 and §6 B3 cite the same anchor set (per F-3). |
| F-5 | pins / feasibility | §5 A1 (M-A1d); §5 A5 | 🟢 SUGGESTION | A1's M-A1d mutation leg ("make the resolver a boot-time snapshot → RED") requires the test to construct the provider before the watchdog exists and assert the fact tracks a watchdog that starts after. The plan should state explicitly that the leg must use a **real** `app.state.wedge_capture_watchdog` read (not a mock returning a constant), otherwise the pin is satisfied by any resolver and is vacuous. | Add a one-line note to M-A1d: "the leg must bind a real `app.state` read whose value changes between construction and read; a mock returning a constant fails the non-vacuity bar." |
| F-6 | completeness | §5 A3; §16.1 C-4 | 🟡 MAJOR | A3's design depends on the `applied` column meaning "actually loaded into the context" (per the model docstring at models.py:333-350), but the code populates `applied` exclusively from worker feedback (it is `False` at creation and only flips to `True` via `record_feedback`/`update_feedback`, which mirror `feedback_applied`). The plan's open item C-4 ("the skill_evolution table's outcome-field population") is the right place to have caught this, but C-4 asks about `selected`/`applied`/`task_succeeded` population and schema — it does not ask whether `applied` is populated by anything other than feedback. The census the plan requests cannot answer "is `applied` a provenance signal or a feedback signal" because the answer is in the code, not the data. | Add to C-4 (or a new C-5): "confirm whether any code path sets `applied=True` other than `record_feedback`/`update_feedback` (i.e., whether `applied` is a provenance signal or a feedback mirror)." The answer determines whether A3's predicate is meaningful. (It is a feedback mirror — see F-1.) |
| F-7 | staged-release gate | §14.2 item 6; §14.4 | 🟢 SUGGESTION | The plan states "PROMPT-BASELINE OK: no `agents/<name>/*.md` prompt artifact changes" and that F touches `meta.json` not prompt artifacts. This is correct, but the plan should also assert that the **roster manifest** (§F, new file under the planning dir) is not under `agents/` and therefore does not affect the prompt baseline. A careless implementer could place the manifest under `agents/<name>/` and silently break the 181 baseline. | Add a one-line guard in §14.2 item 6: "the roster manifest lives under `.agents/shared/planning/`, never under `agents/`." |
| F-8 | clarity / anchors | §17 item 1 | 🟢 SUGGESTION | §17's disclosed filesystem.py drift cites `:584/657/748/1040` (the prior-art D3 numbers) as the drifted anchors, but the actual drifted anchors in the current code are `:590/663/754/1309` (function defs) / `:596/670/764/1322` (call sites). The corrected anchors the plan proposes (`:596/:670/:1114/:1518`) are themselves wrong (per F-3). The disclosure is internally inconsistent. | Rewrite §17's filesystem.py line to cite the correct corrected set (per F-3) and note that the prior-art numbers are stale. |

### Severity Summary
- 🔴 Critical: 1 (F-1)
- 🟡 Major: 3 (F-2, F-3, F-6)
- 🟢 Suggestion: 4 (F-4, F-5, F-7, F-8)

### Positive Observations
- **P1 roster recompute (§3 P1, §10)** is verified exactly: 15 true / 10 false of 25 declaring, 21 absent, 46 total, 31 cannot capture. The plan correctly corrects the commission's 16/24 split and recomputes from `origin/latest` rather than trusting the commission.
- **P2 stale-skill reconciliation (§3 P2, §9 E4)** is verified: `76e3e1a7a` does not exist anywhere (0 hits); `08a4332f` + `fd1e01dc` are two ACTIVE rows of one never-resolved A/B lineage (`regression-ab-partition-run`, group `2600329e-…`); gen-0 `978ae5cc` is inactive. The plan's hard rule ("no row deactivated until first identified by query") and the rule-based (not id-list) mechanism are the right call.
- **P3 confinement status (§3 P3, §6 B0)** correctly states all three pieces as NOT BUILT / DEFERRED with recorded reasons, reconciling each against D6/D7/D8 rather than silently re-opening them. The plan explicitly says it does not overturn D6.
- **A1 `capture_active` source-of-truth ruling (DS-02)** is correct: the wedge-capture **process fact** (`app.state.wedge_capture_watchdog is not None`), not a switch value, mirroring the `listener_enabled` precedent (`provider.py:89-96`). The late-binding design (DS-03) is the right shape given the construction-order gap (provider at api.py:2571, watchdog at :3236-3258).
- **A2 resolution-layer fix (DS-04)** is correct and well-argued: fixing `CensusSampler.gated` to `return not self._gate()` (metrics_census.py:335) adds no new check site, flips zero existing pins (the test suite already encodes the corrected contract), and the R-i/R-ii label swap is real and corrected in code + disclosure.
- **A4 transition-based bump (DS-08)** is correct: the leak is at skill_metrics_service.py:1170-1186 (bumps on every `record_feedback(applied=True)` including repeats), and the service already holds the row at :1027-1029 so `prev_applied` is free. The three-step convergence (stop leak + audited reconciliation + two-point proof) is the only honest reading of "converge to 0."
- **E4 vehicle ruling (DS-10)** is correct and well-evidenced: the `.sql` migration is provably the wrong vehicle (runner is a PG no-op at runner.py:837-842; fresh SQLite boot dies at 20260714_000001's invalid `ALTER TABLE … DROP CONSTRAINT IF EXISTS`), so adopting both a boot-time reconciliation and an operator script (house precedent: `scripts/remediate_pause_report_orphans.py`) is right.
- **B2 inline `skill_evolve` gate (DS-14)** is correctly scoped: one call to `manager.is_skill_evolution_lane_enabled()` (the single resolution point), no new env var, `skill_analyze` stays ungated per D1's carve-out.
- **G root cause (DS-29)** is correctly diagnosed as a test-infrastructure defect (StaticPool single shared connection at tests/services/conftest.py:42-46 + concurrent `asyncio.to_thread` fan-out at skill_evolution_service.py:1448-1457/:1474-1483), with the decisive experiment (normal 11P/1F vs inline-monkey-patch 12P/0F) and the fixture-level fix preferred over a production lock.
- **Net-new-reds protocol (§13.2, DS-26)** carries the verbatim classifier rule (`grep '^FAILED'` only), the intersection-across-≥3-runs base definition, and the named quarantine list. This is the house measurement standard made exact.
- **Hard constraints (§2, §4)** are encoded and verifiable: no switch flip, no promote, no queue-brake engagement, no canonical-dir work, no hard-delete of skill rows, no `.sql` file, no `scripts/upgrade/**` touch, no prompt-artifact change.
- **Disclosures (§17)** are extensive and honest (14 items), including the base-red mechanism correction (P4), the read-reach residual being 2 tools not 4 (P5), the brake not being currently engaged (P7), and the capture→evolution diversity gap not being closed in Phase 1 (E2).
- **B1 behavioral proof (§6 B1)** is the right shape: capability-based (not name-based) check against the production resolver, plus an invocation leg with a directory digest before/after. This is what the commission actually asked for ("behavioral, not by inspection").

### Open Questions Surfaced
1. **A3's signal (F-1/F-6).** The plan's A3 cannot be implemented as written. The dispatcher must decide whether to re-scope A3 to `selected AND task_succeeded` (with re-argued rejection of the over-broad concern) or to split it into a provenance-signal addition + an honest disclosure. My recommendation: the latter — the current schema has no column that means "loaded into the prompt independent of feedback," so A3 should be re-titled "add an injection-provenance signal" rather than "repoint the gate."
2. **FE sidecar (F-2).** Whether the implementer adds the §14.2 item or the tester adds it to the staged-verification checklist. Either is fine; it must exist somewhere before staging.
3. **Anchor hygiene (F-3/F-4/F-8).** Whether the plan is amended in place or the implementer corrects anchors at implementation start. I recommend in-place amendment so the tester's gate pins reference correct lines.

### Unverified Items
- I could not query the live/dev PostgreSQL (read-only discipline forbids DB writes, and a SELECT against the live DB is out of scope for this review). The plan's P2/P4/P7 factual claims about DB state (is_paused=false, the 6 captured-origin ids, the 48/628 mismatch) are taken from the Phase-0 facts extract and the tester evidence pack, which I verified only by reading the committed artifacts, not by re-running the queries.
- I did not independently measure the interpreter (`libpython3.14`/`cpython-314`) — the plan's §14.2.9 measurement procedure is unverified but reproducible.
- I did not re-run the base-leg test suite (the plan's 4/455, 3/456, 4/455 numbers are taken from the evidence pack; I verified the classifier rule and the intersection definition are stated verbatim, not the raw counts).

---

*End of review. Written to: /home/nea/ensemble-src-wt-skill-revival-phase1/.agents/shared/planning/skill-system-revival/plan-review-council-peer.md*

---

## Census Addendum (Delta Pass) — 2026-11-09

Appended per the mandatory census addendum. The plan has already absorbed most census facts (lines 165-166, 168-170, 178-179, 438-443, 483-487, 508-509, 869, 912-915, and the Workstream C ID-resolution note). The findings below are NEW or REFINED relative to the original review above.

### Reconciliation of F-1 and F-6 against census fact 2

**F-1 (original): "A3's fix is a no-op because `applied` is a mirror of `feedback_applied`."**

**(a) How `applied` is actually populated (file:line + prose, no code).**
`applied` is declared `applied: bool = Field(default=False)` (models.py:395) — a **non-nullable boolean** with default `False`. It can never be NULL, which is why the census shows 0 NULLs for `applied` versus 3,547 NULLs for `feedback_applied`.

Three write paths, all feedback-driven:
1. `record_task_completion` (skill_metrics_service.py:878) inserts `applied=False` with the comment "Set later by skill_feedback tool" — the completion hook does not know whether the skill was used.
2. `record_feedback` on-miss insert (skill_metrics_service.py:1057) sets `applied=bool(applied_bool)` where `applied_bool` comes from the worker's feedback call.
3. `update_feedback` (repository.py:1308-1316) sets `feedback_applied = applied` and then `if applied: record.applied = True` — a **monotonic latch**: `applied` flips to `True` when any feedback says applied, and is **never reset to False** on a later "not applied" feedback.

Consequence: `applied` is **not** a mirror of `feedback_applied`. It is a monotonic "ever applied" latch. `feedback_applied=True` ⟹ `applied=True` (via the mirror in path 3), but `applied=True` ⟹ `feedback_applied` was True at some point (it may now be False if a later feedback said not-applied). And `feedback_applied IS NULL` ⟹ `applied=False` (no feedback ever filed, so the latch never fired).

**(b) Does F-1 stand, need refinement, or must be withdrawn?**
**REFINED, not withdrawn.** The original claim ("applied is a mirror → A3 is a no-op") was wrong on the mechanism: `applied` is a monotonic latch, not a mirror, and A3 is **not** a no-op — it does change behavior for the `applied=True, feedback_applied=False` population (prior-applied-then-later-not). However, the **core defect** stands and is actually worse than originally stated: A3's predicate (`applied == True`) does **not** address the census-identified false-eligible window (`selected AND task_succeeded AND feedback_applied IS NULL` = 3,457 rows), because every one of those rows has `applied=False` (the latch never fired — no feedback was filed). The switch only affects a different, smaller population. A3's acceptance criterion #1 ("false-eligible population drops from 3,457 to the corrected value") **cannot be met** by the proposed change.

Additionally, the plan's C-4 open item (line 869) says "No plan change — §A3's predicate and §E4's mechanism stand." That conclusion is **wrong** and must be reversed.

**(c) Should A3's acceptance criterion be restated as a row-count change?**
**Yes.** Restate as: "3,457 rows (selected + task_succeeded + feedback_applied IS NULL) → 0 eligible after the fix." The fix must make the capture gate treat `(selected AND task_succeeded AND feedback_applied IS NULL)` as "potentially applied" → capture-ineligible. The gate becomes: capture is ineligible unless `feedback_applied = False` (explicitly not applied) OR no skill was selected OR the task failed. Equivalently: `capture_eligible = (feedback_applied = False) OR (NOT selected) OR (NOT task_succeeded)`. Acceptance #2 must flip: an instance with `selected=True, applied=False, task_succeeded=True, feedback_applied=NULL` is **capture-INELIGIBLE** (not eligible), because the success may be attributed to the selected skill and no feedback refutes it.

**F-6 (original): "C-4 doesn't ask whether `applied` is populated by anything other than feedback."**
**CONFIRMED and STRENGTHENED.** The census confirms `applied` is populated only by feedback (paths 1-3 above). But the census also reveals `applied` is a **monotonic latch** (0 NULLs, can be True while `feedback_applied` is False) — a property the plan never mentions. The plan's C-4 says "No plan change" which is incorrect: the predicate must change to address the `feedback_applied IS NULL` window, and the monotonic-latch behavior must be documented for implementers (a monotonic latch that never resets means a skill once marked applied stays applied forever, even after a later "not applied" feedback — this is a design decision that should be explicit, not buried in a code comment).

### New Findings (delta)

| # | Area | Section | Severity | Issue | Fix Suggestion |
|---|------|---------|----------|-------|----------------|
| F-9 | feasibility / pins | §5 A3 lines 160-180; §16.1 C-4 line 869 | 🔴 CRITICAL | A3's predicate (`applied == True`) does not involve `feedback_applied IS NULL`, which is the actual defect per the census. The 3,457-row window (`selected + task_succeeded + feedback_applied IS NULL`) has `applied=False` for every row, so gating on `applied` does not touch it. The plan's C-4 says "No plan change" which is wrong. Acceptance #2 contradicts the census (it says those rows are capture-eligible; the census calls them false-eligible). | Rewrite A3's predicate to address the `feedback_applied IS NULL` window: capture is ineligible unless `feedback_applied = False` (explicitly not applied) OR no skill was selected OR the task failed. Flip acceptance #2 (those rows become capture-INELIGIBLE). Restate acceptance #1 as "3,457 → 0 eligible." Reverse C-4's "No plan change" conclusion. Document the monotonic-latch property of `applied`. |
| F-10 | clarity / anchors | §9 E4.2 line 525; §16.1 C-4 line 869 | 🟢 SUGGESTION | E4's "reversible" mechanism (--reactivate from receipt) is described, and "No tombstone column exists" is stated (line 483/915), but the plan never explicitly says "reversal = restore the recorded (is_active, status) pair per row from the receipt; there is no deleted_at column to restore." The census confirms no tombstone column exists, so the reversal is a value-restore, not a column-restore. An implementer could misread "reversible" as requiring a schema change. | Add one sentence to E4.2: "Reversal restores each row's recorded (is_active, status) pair from the receipt; no tombstone/deleted_at column exists or is added." |
| F-11 | completeness | §5 A3 M-A3a/M-A3d | 🟡 MAJOR | My original review (F-1) claimed M-A3a and M-A3d are vacuous. This was wrong: `applied` (monotonic latch) and `feedback_applied` (latest feedback) differ for some rows, so reverting the gate does change behavior for the `applied=True, feedback_applied=False` population. The mutation legs are NOT vacuous — they test a real (if wrong-target) behavioral difference. However, if A3 is re-scoped per F-9, the mutation legs must be rewritten to test the new predicate. | Rewrite M-A3a/M-A3d against the corrected predicate (feedback_applied IS NULL window). Keep the non-vacuity bar: each leg must change behavior. |

### Delta Severity Summary
- 🔴 Critical: 1 (F-9)
- 🟡 Major: 1 (F-11)
- 🟢 Suggestion: 1 (F-10)

### Census Facts Confirmed Against Plan (no new finding)
- **Fact 1 (A4):** Plan already redefines the gauge to within-identity-key (name, project_id) and states "A4 is a gauge redefinition, not a 'converge to 0' fix" (line 912). No global-dedupe is planned. ✓
- **Fact 3 (E3):** Plan already labels the three absent items as NOT-from-board (lines 438-443) and states the real item has exactly three bullets. ✓
- **Fact 4 (E4):** Plan already notes the desync (line 483/915), no tombstone (line 483), 6 already inactive (line 509/915), 52 ab_testing guard (line 487/508), and unverifiable diversity (E2 section). The only gap is the explicit "reversal = value-restore" statement (F-10). ✓
- **Fact 5 (Workstream C):** Plan already resolves the brake by ID (`WHERE id = 'bd075f0b...'`), not by name LIKE, and states the name-filter artifact explanation. ✓

### Revised Verdict
**REJECT (unchanged in severity, refined in cause).** The rejection still stands on A3, but the cause is now precisely identified: A3's predicate does not address the census-identified 3,457-row window, and the plan's C-4 incorrectly says "No plan change." The fix is clear (F-9) and the plan is otherwise strong. All other workstreams are implementable as written.

---

*End of census addendum. Appended to: /home/nea/ensemble-src-wt-skill-revival-phase1/.agents/shared/planning/skill-system-revival/plan-review-council-peer.md*
