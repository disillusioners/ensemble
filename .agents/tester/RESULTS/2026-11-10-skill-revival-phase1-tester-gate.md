# TESTER FUNCTIONAL GATE — agents-ensemble skill-system-revival Phase 1 (ENSEMBLE-27)

Date: 2026-11-10 (gate ran 04:24–05:50 UTC)
Worktree: `/home/nea/ensemble-src-wt-skill-revival-phase1`, branch `feature/skill-system-revival-phase1`
**Gate pin: `74914c435f6c1dd50e28152b71f5083aba353960` — verified MATCH at start.**
Base leg: `/home/nea/ensemble-src-wt-skill-revival-base1` @ `ae5bbafedde90c7af1065b9660196bbffbbbf9c1` — clean before AND after (porcelain empty, `--untracked-files=all`).
Gate workers: 9 (state, head-red, base-red, regA, regB, regC, confinement, e1/h2/e3, e4/f10) + 2 follow-ups.

---

## VERDICT: **PASS-WITH-NOTES**

**Net-new reds = 0 in BOTH scopes.** Every commissioned functional claim verified on real objects. The notes are (a) one MAJOR test-infrastructure defect found by the gate and fixed in-gate (pack cleanup destroyed shared sink evidence), (b) routed pre-existing minors (generic-fault wording, an un-runnable PG pack registration), (c) a false clean-slate premise on `data/` at gate START (disclosed, measured, receipt byte-identical START→END).

| # | Question | Verdict | Evidence |
|---|---|---|---|
| 1 | Net-new reds, touched-file scope | **∅ EMPTY** | §1 below |
| 2 | Net-new reds, registered-pack scope | **∅ EMPTY** (1 pack FAIL = pre-existing base family) | §2 below |
| 3 | Metrics honesty (A1–A4) | PASS | §3 |
| 4 | Confinement (B) — independent re-drive + attack | **PASS — 6/6 attacks REFUSED** | §4 |
| 5 | Flakiness (G) | PASS — 3× full-file-set 0 reds (pack) + ×5 gate legs green | §5 |
| 6 | Burst guard (E1) + H2 tool path | PASS — durable, identity-keyed, 429, truthful envelope | §6 |
| 7 | Observability (D) | PASS (with one PARTIAL disclosed by the lane itself) — **the "PARTIAL" qualifier is SUPERSEDED, see the §7 correction note (the counter is COMPLETE at 3 refusal sites); verdict itself UNCHANGED** | §7 |
| 8 | Deactivation mechanism + brake (E4) | PASS — 5/5 behavioral probes | §8 |
| 9 | Injection dedupe (F) | PASS — mutation re-driven, 5 pins RED | §9 |
| 10 | Display hygiene (E3) | PASS — incl. independent round-trip | §10 |
| 11 | F-10 coverage gap | **CLOSED** — e2e test added, counter-mutation-proven, commit `58c8cb655` | §11 |
| 12 | Migration census / prompt baseline / census population | 126 sql / 0 dups; 181 in-sync; population stated in §12 | §12 |
| 13 | `data/` clean at START/END | **PREMISE FALSE at START** — measured, disclosed (§13) | §13 |

---

## 1. Red-set protocol — SCOPE 1: touched files (the 12-file skill blast radius)

Command (both legs, identical): `.venv/bin/pytest <12 files> --tb=short -q --timeout=120 -p no:cacheprovider`, full 12-var env scrub, `PYTHONDONTWRITEBYTECODE=1`, `timeout 300` outer. Classifier: `grep '^FAILED'` ONLY.

**BASE leg (×3):** 459 collected. run1 3F/456P · run2 3F/456P · run3 5F/454P (the 2 extra = the AB flake pair, 1-of-3 each). **TRUE base = intersection ×3 =** the `tests/manager/test_skill_service_init.py` trio (SQLite `20260714_000001` migration defect, `runner.py:677`).

**HEAD leg (×5 — 3 protocol runs + a clean-tree confirm + an isolated-data-dir probe; all five byte-identical):** 459 collected. **3F/456P every leg.** FAILED list == the base trio, verbatim.

**NET-NEW = (head ∩) − (base ∩ of 3) − (quarantine list) = ∅.**
Quarantine classification: `test_ab_resolution_threshold_met` and `test_ab_resolution_force_resolve` were collected and GREEN in all 5 head legs (0 occurrences); the base leg flaked 1-of-3 on both — exactly the documented base instability, correctly classified, not counted.

## 2. Red-set protocol — SCOPE 2: registered-pack registry (17 packs + 2 registered invocations)

| Pack | RESULT | Counts | Wall | Note |
|---|---|---|---|---|
| metrics_capture_active_wiring_unit_test | PASS | 15P | 85s | A1 |
| metrics_census_polarity_unit_test | PASS | 13P | 39s | A2 incl. honesty legs (gated ⇒ dark) |
| metrics_capture_lane_read_unit_test | PASS | 15P | 63s | |
| metrics_capture_lane_off_seam_unit_test | PASS | 11P | 91s | ⚠️ cleanup leg destroyed shared sink (§13, F-G1) |
| skill_capture_observability_unit_test | PASS | 19P | 25s | D |
| skill_counter_drift_unit_test | **TIMEOUT→PASS post-split** | 37P pins + legs | 300s kill → 189s (a–d) + 185s (e–h) | internal 600s budget violated the 300s cap; split landed `60972185f`; M-A4g/h proven RED→GREEN by manual driver first |
| skill_evolution_unit_test | PASS | 112P across legs; **G acceptance 0/0/0 reds ×3** | 134s | |
| skill_capture_killswitch_unit_test | PASS | mutation legs in-pack RED→GREEN, restores byte-identical | 69s | |
| skill_id_verbatim_contract_unit_test | PASS | 135P | **288s** | ⚠️ 12s under the 300s cap under load — near-cap, split candidate |
| skill_services_gzip_regression_unit_test | **FAIL (pre-existing)** | 3F/177P | 27s | the 3 F == the base migration trio byte-identical ⇒ pre-existing, NOT net-new |
| context_skills_unit_test | PASS | 164P | 9s | |
| skill_dual_column_deactivation_unit_test | PASS | 11P + 3 legs | 42s | |
| skill_stale_deactivation_unit_test (MUTATIONS=0) | PASS | 56P | 25s | E4 pin arms |
| skill_injection_budget_unit_test (MUTATIONS=0) | PASS | 33P | 6s | F pin arms |
| skill_keeper_confinement_behavioral_unit_test (MUTATIONS=0) | PASS | 19P | 16s | import-guard proved the pinned checkout |
| skill_keeper_read_scope_unit_test (MUTATIONS=0) | PASS | 68P | 43s | |
| skill_tools_display_hygiene_unit_test (MUTATIONS=0) | PASS | 13P | 20s | |
| **F-2 census invocation 1** (`test_metrics_query_scope_cage.py`) | **PASS 7/7** | DECLARED_ROUTE_CENSUS 114 assert green | 11s | F-2 remediation PROVEN; 0 collection errors under full-family scrub |
| **F-2 census invocation 2** (`test_router_scope_manifest.py` + integration class manifest) | **PASS 12/12** | `== 114` green; 38 mounts / 34 families unchanged | 12s | |
| skill_evolution_pg_test | **NOT RUN** | — | — | structurally un-runnable (B2 below); pre-existing rot |

**NET-NEW (pack scope) = ∅.** The single FAIL is the pre-existing base family, executed-attributed at base by §1.

## 3. Workstream A — metrics honesty
- **A1** capture_active wiring pack 15P (real watchdog presence drives the verdict; no unconditional `capture_off`).
- **A2 polarity honesty verified BOTH directions**: enabled+running+publishing ⇒ `live`; the pack's dark-legs (`disabled ⇒ census_gated`, non-running wins order, enabled-but-no-series ⇒ series-absent) all assert `dark` and their mutation legs M-A2a/b/c proved RED→GREEN. If the fix had made everything green, these legs would have failed — they did not.
- **A3** attribution predicate `has_genuinely_evolved_for_instance` (3-conjunct OUTCOME-FIELDS) landed at `daemon/repositories/skill/repository.py:1699`; retired `has_applied_for_instance` retained and labelled NOT-A-GATE; both consumers retargeted; the retargeted stub files make the "gate never consults the retired predicate" assertions LIVE.
- **A4** counter-drift pack: pins + 8 mutation legs now ALL proven (g/h by gate driver, then in-pack post-split); transition-bump pin green.

## 4. Workstream B — confinement (independent re-drive, production factory + resolver)
Construction: `create_instance_tools(manager, …, agent_id="skill-keeper")` + real `resolve_tool_filter` on the real meta.json (deny-wins, 24 allowed names, 35 bound tools) + production read-scope wrapper chain + dispatch through a real compiled langgraph `ToolNode`. Fresh canaries (`GATECONF-24e7d3b0…`).
1. **b7da3ed0 shape (24× edit_file + git commit): REFUSED 28/28** — the write tools never bind for the keeper; tree digest unchanged; porcelain clean. No warn-but-write path.
2. **F-1 symlink: CLOSED** — grep_files returns NO canary content (symlink excluded by `filesystem.py:567` `not f.is_symlink()`); glob excludes the link; read_file refuses (outside-anchor + workdir-argument refusals); list_directory drops it entirely. Canary bytes never returned.
3. **Traversal ×10: ALL REFUSED** (`../`, absolute, workdir=anchor, workdir outside, grep/glob path escapes).
4. **Undeclared agent: BYTE-IDENTICAL** outputs (sha-matched) on legitimate reads; the scoped/unscoped delta is exactly the hazard exclusion. Confirmed the fence is opt-in-by-meta (defense lives in the declared-scope filter — as designed, DS-15).
**F-dedupe mutation re-drive:** pass-through `dedupe_injected_entries` ⇒ **5 pins RED**; restore sha `6951f9e2…` == pre-mutation; green 14P re-confirmed. Non-vacuous.

## 5. Workstream G — flakiness
Pack acceptance leg: **3 consecutive full-file-set runs, 0 reds each**, pinned classifier, `assert_full_file_set_acceptance` OK. Gate's own HEAD legs: AB pair green ×5. Base leg still flakes 1-of-3 (expected — old fixture). QUARANTINE rows 45/56/90 removal re-confirmed on gate evidence; the integration-file sibling node formally quarantined by this gate (new row, deselect-registered).

## 6. Workstream E1 + H2 — burst guard
Real `SkillJobDispatcher` + real `JobRepository` + real `AnomalyService`: 4th admission REFUSED (`SkillFixBurstCapRefused`) with anomaly row + counter + WARNING log + sink record; **durable across a fresh dispatcher (RAM cooldown empty)**; **identity-keyed** (3 admissions with 3 distinct job keys trip the 4th; job-keyed counting proven dead — 1 row per key); per-skill independence proven. REST route: **429** with reason body (control skill → 202). Tool envelope: truthful refusal (`NOT dispatched … NO job was created and NOTHING will be picked up`); the false "still recorded" sentence ABSENT on the refusal path; 3 job rows persist (no phantom 4th).
**Routed finding (pre-existing):** the GENERIC-fault branch still says "request is still recorded above for the next skill-keeper run" while empirically 0 rows persist when dispatch raises before insert — the same defect class H2 fixed for the cap branch. Owner: skill-tools lane; wording-only fix.

## 7. Workstream D — observability
Packs 19P + 11P + 15P green: every refusal site increments its own counter; deviation announced through the REAL production holder chain (M-C1, no injected reporter); prompt SHAPE never carries raw prompt; admission/execution signals never merged. Sink schema verified live: keys `stage/outcome/refusal_reason/prompt_source/run_id/deviation` (the dispatch's `kind`/`status` names do not exist — corrected here). The lane's own disclosure stands: the production `capture_lane_off` refusal site in the metrics lane returns before the dispatcher gate, so that ONE counter is partial today; the seam exists and is pinned (M-C2), Phase-2 wires it. **[that sentence is SUPERSEDED — see the correction note below; it is retained verbatim as the trail of what I believed at gate time]**

### CORRECTION NOTE (2026-11-10 — annotation only; verdict, findings list, severities, remediations and every other measurement in this artifact are UNCHANGED)

My §7 conclusion — that the `capture_lane_off` counter is **PARTIAL** in production because the metrics-lane refusal site returns before the dispatcher gate, and that the Oct-22-class deviation would therefore still be under-counted and need Phase-2 wiring — was **WRONG in its outcome**. The mechanism half was right (the metrics seam does return before the dispatcher gate) and irrelevant: a refusal is counted **at the site that refuses**, so the ordering across sites changes nothing about completeness.

**Corrected conclusion: the counter is COMPLETE — three refusal sites, one counter key, each incrementing BEFORE it refuses.**
- The capture switch is read in exactly **4** places: **3 refuse, 1 reports**. `SKILL_CAPTURE_KILL_SWITCH_ENV = "ENSEMBLE_SKILL_CAPTURE_ENABLED"` (`daemon/services/skill_job_dispatcher.py:75`), single resolver `_resolve_capture_enabled()` (`:153-176`); no other read spelling exists.
- `daemon/services/skill_metrics_service.py` — guard `:663` (`:659` at the gate pin), recorder `_record_capture_lane_off_refusal(...)` `:682` → refusal `return None` `:687`.
- `daemon/tools/skill_evolution_tools.py` — guard `:522` → `observability.record_capture_lane_disabled_refusal(...)` `:539` → refusal `:563`.
- `daemon/services/skill_job_dispatcher.py` — guard `:1126` → `record_admission_refusal("capture_lane_off")` `:1136` → refusal `:1148`.
- All three land on the **same** key `_COUNTERS.admission_refusals["capture_lane_off"]` (`daemon/services/skill_capture_observability.py:290-292`, `:355`); only the sink record's `prompt_source` differs. The 4th read site (`daemon/services/skill_roster.py:382`) only REPORTS `armed: false`.
- The twin seam `check_and_capture` (`daemon/services/skill_evolution_service.py:1622`) has **no** capture-enable check (0 hits) — it cannot refuse because the lane is off.
- Wiring provenance: `ae99a5be9` — `git merge-base --is-ancestor ae99a5be9 74914c435` ⇒ ANCESTOR_YES; all three call sites present AT THE GATE PIN.
- Newly pinned by `tests/unit/test_skill_capture_lane_off_cross_path.py` (8 nodes): each of the three refusal paths driven SEPARATELY, an **AST census of the guard SITE SET** (`:249-286`, path-set equality + count), and a **path-sensitivity leg** (M-C2e) that neutralises the metrics recorder and shows the metrics assertion REDs.

**Root cause of my error:** I inherited the metrics lane's own PRESENT-TENSE disclosure ("the counter is PARTIAL") from the lane's summary material instead of re-deriving the call sites at the pinned sha — a stale disclosure read as a current fact. The metrics lane has since corrected that disclosure; the corrected status is recorded in the plan's §17 item 44. The lesson generalises: *a lane's own narrative about its own partiality is a hypothesis, not evidence — re-derive the call sites before repeating it.*

**Two nuances recorded for honesty (neither changes the conclusion):** (a) the metrics guard moved `:659`→`:663` after the pin — same site; the AST census compares PATHS only (line drift cannot red; the count assert catches a second guard inside one file); (b) the M-C2e leg machine-asserts "something went RED", while the sharper discrimination ("ONLY the metrics node REDs; tools/dispatcher stay GREEN") is observed-and-logged (`PACKS.md:4741`), not machine-asserted.

**Boundary facts (they do not reopen the conclusion):** admission refusals are complete; at EXECUTION time there is **no** capture-off gate (`daemon/services/job_processor.py` gates `skill_evolution`, not `skill_capture` — a `skill_capture` row admitted while the lane was ON still executes after a flip-off), and `skill_roster.capture_lane_enabled()` has **no production caller** in Phase 1 (it feeds the staged payload only). Admission refusal and execution refusal stay two signals (DS-19).

**Separately sharpened, NOT re-scored here:** the PG-execution gap — the PostgreSQL dialect branch of the identity-keyed burst-cap count (`daemon/repositories/job_queue/repository.py:68-79`, consumed by `count_fix_admissions_since` `:659-711`) is **compiled-only**: its pin (M3, `tests/unit/test_skill_revival_fix_wave1_pins.py:290`) is STATIC by design (no DB contact — the ambient `POSTGRES_*` points at live `ensemble_prod`), and the PG pack that should exercise it is un-runnable AND would collect **0** postgres-marked tests even if repaired (no `pytest.mark.postgres` in the surviving files; the PG-marked skill files are referenced by **zero** packs). Tracked as F-G3; its severity is unchanged by this note.

## 8. Workstream E4 — deactivation + brake (scratch SQLite + `ENSEMBLE_DATA_DIR` fence; real boot entry `daemon/manager.py:5768`)
(a) brake INERT unset AND =0: 0 constructions, 0 receipt bytes, 0 row changes, only 2 DEBUG lines. (b) A/B refusal: rows stay `(True,'ab_testing')`, receipt enumerates `skipped_open_ab_test` + blocking test id. (c) H3: injected mid-apply failure leaves an on-disk receipt covering flipped AND intended-unflipped rows; reversal restores the EXACT prior pair. (d) no-winner: `PairResolutionRefused: skipped_no_winner_decision`; zero call sites supply a winner (static). (e) atomic `os.replace` same-dir; refusal list REPLACES across runs. Worktree receipt sha **`fcd7105c…` IDENTICAL START→END**.

## 9. Workstream F — injection gate
33 pin arms green (budget cap binds; low_match bounded; roster manifest re-derived, staged payload capture-gated); identity dedupe mutation-proven (§4). No agent flipped; no meta.json edit.

## 10. Workstream E3 — display hygiene
No bare 8-hex id echo in `skill_fix`/`skill_feedback` envelopes (full uuid rendered); `pf-` pseudo-id only on an additive line; emitted id ROUND-TRIPS through `skill_view` (bare 8-hex prefix correctly REFUSED by skill_view). Independent scratch scenario + 13P pack.

## 11. F-10 — ADDED
`tests/unit/routers/test_skill_patch_dual_column_e2e.py` (3 nodes, 299 lines): real store+repository through the real PATCH route; persistence read back on a FRESH engine; boundary fact recorded — the both-columns guarantee is enforced at the ROUTER (`SkillUpdateRequest` has no `status` field; Pydantic drops caller-supplied status; the repo layer alone CAN write one column — boundary, not defect). Counter-mutation (old pop-and-translate shape emulated at the real seam) ⇒ nodes 1+3 RED. 3P standalone; 12P with sibling; routers-dir baseline 72F identical with/without (pre-existing). **Commit `58c8cb655cfc67fd1e73ca72f6de53a8d88c14c7`.**

## 12. Census / baseline / population
- Migration census: **126** `.sql`, all `^[0-9]{8}_[0-9]{6}_`, **`uniq -d` EMPTY**; count unchanged by the gate's commits (re-verified at END). Date-only form not used.
- Prompt baseline: `.venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py --check --tree .` → **"PROMPT-BASELINE: OK — 181 versioned artifact(s) match" rc=0**.
- **Census population (stated per dispatch) — DERIVED BY THIS GATE:** `agents/*/meta.json` glob = **46** (44 real + `_baby_template` + `_mother`; `_inner_soul` and `_prompt_system` have no meta.json); the **registry registers 45**. Diff = exactly one name: `_baby_template` is in the glob but skipped by `SKIP_DIRS`; **`_mother` is in the glob AND registered as a live (spawnable) agent** — the `SKIP_DIRS` comment ("…not standalone agents (no meta.json)") is stale, and a leading-underscore template is registered. **Flagged for a leader ruling** (anomaly `03f5379d`), not a lane defect. Registry composition: 40 untagged + 5 tagged (`approver`/`developer`/`planner`/`reviewer`/`tidier[v2]`).

## 13. `data/` state — premise FALSE at START (disclosed)
- **START (04:26Z):** `data/skill-capture-observability/` = **1 file / 8,201 B / 26 rows** (`skill-capture-20261110.jsonl`, written 04:18:21–04:18:44Z — BEFORE this gate session began 04:22:54Z; writer unattributed, rows = 21 refused/capture_lane_off + 5 admitted). `data/skill_stale_deactivation_receipt.json` PRESENT (152 B, run_id `c38cd9a3e788`, all lists empty, 2026-11-09T19:01Z — the implementation lane's own E4 run) + 5 rotations + candidate manifest.
- **DURING:** the HEAD red-set legs appended +78 rows (3×26; per-file attribution closes exactly: killswitch 12 + job_dispatcher 4 + metrics_service 10 = 26/leg — the metrics_service 10/leg is the ROUTED, pre-existing leak the dispatch predicted).
- **END: 0 files / 0 B.** The file was **DELETED** by `metrics_capture_lane_off_seam` leg M-C2d's `clean_marker_leaks()` (`rm -f` on the whole dated file when the planted marker is present) — **116 rows of observability evidence destroyed, gitignored, unrecoverable.** This is gate finding **F-G1 (MAJOR, test-infra)**: the pack's "a concurrent lane's residue is never touched" comment is false at file granularity; the sink appends all lanes into one daily file. Surgical-cleanup fix routed (row-rewrite, not file-delete) — landed in-gate as a test-code commit; see PACKS gate section.
- Receipt sha IDENTICAL START→END (§8). Live install never touched.

## Quarantine actions (this gate)
- Confirmed removal of rows 45/56/90 on gate evidence (already moved to Resolved by the lane).
- **Added** one Active row: `tests/integration/test_skill_evolution_e2e.py::TestSkillABTesting::test_ab_test_resolves_when_threshold_met` (own-fixture StaticPool flake, base-rate-higher, deselect-registered in `skill_evolution_unit_test` leg 3c; fix = port G's fixture shape to that file).

## Findings register
| id | severity | finding | owner / action |
|---|---|---|---|
| F-G1 | MAJOR (test-infra) | `clean_marker_leaks()` deletes the WHOLE shared daily sink file — destroyed 116 evidence rows; false "never touches residue" comment | **FIXED in-gate** `b7e0269e3` — row-granular surgical removal (salted per-run marker, atomic temp+rename, file never unlinked, mode preserved); pack re-run PASS 88s. The 116 lost rows are NOT restored (gitignored, unrecoverable) · **CLOSE-OUT (2026-11-10, post-gate):** the fix is intact and re-proved green (pack `RESULT: PASS (4 mutation legs RED→GREEN, 162s)`; the pack itself deleted nothing). **Class-level closure NOT achieved** — three residuals: (a) the same unlink-the-whole-shared-file class is LIVE at `daemon/services/skill_deactivation.py:641` (receipt rotation unlinks shared `skill_stale_deactivation_receipt.json.*.json` copies — mtime-keyed, keep=5, NOT lane-scoped; the dir already holds exactly 5 copies); (b) an **UNATTRIBUTED shared-sink unlink RECURRED at 08:29:28Z** — the daily sink file (4,115 B / 12 rows, recreated by my verification run) was deleted 12 s AFTER the pack exited cleanly; no repo code path can do it (pack never unlinks; the 90-day prune cannot touch today's file; `remove_rows_carrying` never unlinks); the concurrent lane was running pytest in the worktree; (c) the lane's new P6 static class-proof does NOT scan `test/packs/metrics_capture_lane_off_seam_unit_test.sh` — the pack that caused F-G1 |
| F-G7 | info (leader ruling wanted) | `_mother` is registered as a live spawnable agent while `SKIP_DIRS`' comment claims underscore names have no meta.json; `_baby_template` is glob-only | leader ruling (anomaly `03f5379d`) — not a lane defect; registry/policy question |
| F-G8 | MAJOR (evidence-integrity, lane-facing; found at close-out 2026-11-10) | The lane's PACKS.md mutation-matrix claims for H2/F-G2 are uncorroborated or contradicted on disk: the ONLY surviving matrix TSV (`/tmp/skill-tools-display-hygiene-pack-OaWmZH/mutation-matrix.tsv`, 02:40Z) records **M-E3d `YES NO`** with both logs containing `ERROR: file or directory not found:` (empty node id) — the leg's "RED" was a pytest usage error and it never went GREEN — contradicting the row's "H2 MICRO-PASS … 4/4 legs RED→GREEN"; the later "F-G2 … 6/6 legs" claim has NO surviving artifact (the pack `rm -rf`s its own `$FAKE_HOME` including the TSV) and was superseded in-place by "7/7" (`cfb53c03e`) without a new artifact. Separately, the re-aimed M-E3d pin IS discriminating but **only via its `"dispatcher raised during dispatch"` assertion** — the `"burst cap"` assertion is satisfied by BOTH branches because `SkillFixBurstCapRefused`'s own message carries the `refused: burst cap` prefix (`skill_job_dispatcher.py:336-343`) | owning lane: (1) stop deleting the mutation-matrix artifact (export it outside `$FAKE_HOME`), (2) re-run phase 2 with the matrix preserved, (3) correct the PACKS row. Gate limitation recorded: the M-E3 legs were NOT re-driven by the gate (production source owned by a live lane); the discrimination verdict is static |
| F-G9 | minor (in-flight WIP, lane-facing; snapshot at close-out 2026-11-10) | Two non-reproducibility defects in the lane's UNCOMMITTED WIP observed during close-out: (a) `test_p9_the_sentinel_catches_a_teardown_that_restores_none` poisons `tests/helpers/skill_sink_redirect.py::_LAST_LANE_STATE=(nodeid, safe=False)` and is not restored in `finally` → every later lane test in the session ERRORs (12 errors observed in one run); (b) the sink-retention pin file's full-invocation result flips between runs while the concurrent lane runs pytest in the same worktree (4F/17P then 12E/15P on identical commands; P1+P2 pass in isolation) | owning lane: restore the helper state in `finally`; re-verify the pin files on a quiet worktree before registering. Gate recorded the A4 close-out pytest leg as UNVERIFIABLE-UNDER-CONCURRENCY rather than PASS/FAIL |
| F-G2 | minor (pre-existing) | `skill_fix` generic-fault branch emits empirically-false "still recorded … will be picked up" | skill-tools lane wording fix (cap-branch wording applied to generic branch) |
| F-G3 | minor (pre-existing, registration) | `skill_evolution_pg_test` invokes deleted `tests/unit/test_auto_load_skills.py`; `set -e` aborts rc=4 BEFORE any RESULT line | pack owner: repair file list or retire the pack |
| F-G4 | minor | `skill_id_verbatim_contract_unit_test` ran 288s/300s under load (near-cap) | tester/lane: split candidate before next registration |
| F-G5 | info | `data/` clean-slate premise false at START (26 rows + receipt pre-existed) | none — measured + disclosed; future gates must snapshot, not assume |
| F-G6 | info | 2-var `ENSEMBLE_AUTH` scrub leaves 5 family vars — collection errors that mask census reds; full 7-var family scrub is REQUIRED (matches POK `auth-lane-scrub-ensemble-auth-env`) | none — encoded in this gate's recipes |

## Disclosures (could not verify / did not run)
1. **`skill_evolution_pg_test` NOT RUN** (F-G3) — the PG-leg surface of the lane is covered by the M3 dialect pins (in `skill_capture_killswitch`/fix-wave packs) but not by a live PG run at this gate. No PG DSN was provisioned (live contact forbidden by the envelope). **CLOSE-OUT ADDENDUM (2026-11-10):** the PG leg is **RUNNABLE on this host** — postgresql-16 binaries are already on disk (`/usr/lib/postgresql/16/bin/{initdb,pg_ctl,postgres,createdb}`; no install, no Docker needed); the owning lane provisioned a disposable native cluster during gate close-out (`/tmp/skillrev-pgdev`, port 54333) and is authoring a postgres-marked dialect test (`tests/postgres/test_skill_revival_pg_dialect.py`); `tests/postgres/conftest.py` forbids `ensemble_prod`/`ensemble_dev` unconditionally (`FORBIDDEN_DB_NAMES`), requires an allowlisted scratch DB, and serial execution. **The leg is therefore recorded as NOT COVERED AT GATE TIME — verifiable later on this host — NOT as host-unverifiable.**
2. **Whole-tree Tier-2 dual-side sweep NOT run** — the diff touches core seams (`daemon/manager.py`), which per ensure.md Gate Tiers would trigger Tier 2; the dispatch's explicit resource constraint (≈10G box, no swap, "never run a large test farm", bounded fan-out) and the commission's own two-scope protocol were honored instead. The lane's own review ran a scrubbed 26-file blast radius (2,019P/26F) with all 26 attributed. Residual risk: registered packs outside the lane-adjacent set were not re-run. This is the gate's largest deliberate scope reduction.
3. **Agent-census population (44+2 vs registry 45) not independently recounted** (§12) — non-blocking.
4. **87 commits sit after the reviewed sha `cfe4480be`** — the review's F-1/F-2/H1/H2/H3 remediations were verified LANDED by this gate (static + behavioral), but the council's 11 minors were not individually re-audited.
5. **HEAD moved during the gate by THIS gate's authorized commits**: `74914c435` (pin, all red-set + registry legs 1–4) → `58c8cb655` (F-10, additive test file only) → `60972185f` (pack split, test code only) → final docs+cleanup commit (see below). W8a re-verified its 8 exercised surfaces content-identical across the F-10 move; W4's packs 5–6 ran post-move (additive-only diff). All production source identical to the pin at every point (verified by porcelain + per-worker fingerprints).
6. One foreign dirty tracked file throughout: `.agents/shared/planning/designer-agent/install-audit.jsonl` (+9 `kms_issue` rows, mtime 2026-11-09 22:29Z, predates the gate) — untouched, unstaged, attributed to the designer-agent lane.
7. Concurrent foreign pytest lanes observed (pok worktrees) — never touched; timing variance disclosed (near-cap pack F-G4).

## Byte counts / digests / exit codes (of record)
- Base leg: 459 collected; 3F/456P ×2, 5F/454P ×1; porcelain empty before/after.
- Head leg: 459 collected; 3F/456P ×5 (incl. clean-tree + isolated-data-dir legs).
- F-dedupe restore sha256 `6951f9e2b5b127027c7faa09f9cb57b57e6a24008e4a06616a03c2dfbc5c939c` (pre == post).
- Receipt sha256 `fcd7105c547d0a1f8ad5f4e6687881893a73695b8641a7f1337944ea84782c9a` (START == END).
- Sink: START 8,201 B/26 rows → +78 rows (gate legs) → 0 B (deleted by F-G1).
- Migrations: 126 both at START and END.
- Commits added by the gate (all pathspec-limited, no production source, no push): `58c8cb655` (F-10 e2e test, +299) · `60972185f` (counter-drift pack split, 2 files) · `b7e0269e3` (F-G1 surgical cleanup, 1 file) · `c6b64484a` (tester docs: PACKS/QUARANTINE/RESULTS/LESSONS). **Final HEAD `c6b64484ac8f4ada8e82c2a1d461079fd118082a`**; porcelain carries only the foreign `install-audit.jsonl`.

## Action needed
- [ ] F-G2: skill-tools lane — generic-fault envelope wording (small, agent-facing string).
- [ ] F-G3: repair or retire `skill_evolution_pg_test` registration.
- [ ] F-G4: split `skill_id_verbatim_contract_unit_test` before the next load-bearing gate.
- [ ] Port G's fixture shape to `tests/integration/test_skill_evolution_e2e.py` (un-quarantine path for the new row).
- [ ] POK store at 64-entry cap — two gate facts could NOT be recorded (dual-column guarantee boundary; auth-scrub family already recorded). Curator curation needed.

### Overall
- Red-set (both scopes): ✅ NET-NEW 0
- Confinement / E1 / E3 / E4 / F / G / A / D: ✅ verified on real objects
- F-10: ✅ closed
- **Gate verdict: PASS-WITH-NOTES — cleared to proceed to staged verification (v0.29.10), with F-G1's fix verified and F-G2/F-G3 routed.**
