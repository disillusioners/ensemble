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
| 7 | Observability (D) | PASS (with one PARTIAL disclosed by the lane itself) | §7 |
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
Packs 19P + 11P + 15P green: every refusal site increments its own counter; deviation announced through the REAL production holder chain (M-C1, no injected reporter); prompt SHAPE never carries raw prompt; admission/execution signals never merged. Sink schema verified live: keys `stage/outcome/refusal_reason/prompt_source/run_id/deviation` (the dispatch's `kind`/`status` names do not exist — corrected here). The lane's own disclosure stands: the production `capture_lane_off` refusal site in the metrics lane returns before the dispatcher gate, so that ONE counter is partial today; the seam exists and is pinned (M-C2), Phase-2 wires it.

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
- **Census population (stated per dispatch):** this gate did NOT re-derive the "46 agents" figure. Population relied upon = the pack-verified roster manifest (`skill_roster_manifest` arm, M-F4/M-F5 re-derivation pins). For reference, W1's independent tool-census observed the keeper's bound set = 35 tools / 10 modules, matching the council's F-3 trace — a tool census, not an agent census. If the leader needs the agent-population figure re-derived (44+2 vs registry 45), that is a 2-minute follow-up not blocking this verdict.

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
| F-G1 | MAJOR (test-infra) | `clean_marker_leaks()` deletes the WHOLE shared daily sink file — destroyed 116 evidence rows; false "never touches residue" comment | fixed in-gate (surgical row removal + comment correction) — test-code commit, see PACKS |
| F-G2 | minor (pre-existing) | `skill_fix` generic-fault branch emits empirically-false "still recorded … will be picked up" | skill-tools lane wording fix (cap-branch wording applied to generic branch) |
| F-G3 | minor (pre-existing, registration) | `skill_evolution_pg_test` invokes deleted `tests/unit/test_auto_load_skills.py`; `set -e` aborts rc=4 BEFORE any RESULT line | pack owner: repair file list or retire the pack |
| F-G4 | minor | `skill_id_verbatim_contract_unit_test` ran 288s/300s under load (near-cap) | tester/lane: split candidate before next registration |
| F-G5 | info | `data/` clean-slate premise false at START (26 rows + receipt pre-existed) | none — measured + disclosed; future gates must snapshot, not assume |
| F-G6 | info | 2-var `ENSEMBLE_AUTH` scrub leaves 5 family vars — collection errors that mask census reds; full 7-var family scrub is REQUIRED (matches POK `auth-lane-scrub-ensemble-auth-env`) | none — encoded in this gate's recipes |

## Disclosures (could not verify / did not run)
1. **`skill_evolution_pg_test` NOT RUN** (F-G3) — the PG-leg surface of the lane is covered by the M3 dialect pins (in `skill_capture_killswitch`/fix-wave packs) but not by a live PG run at this gate. No PG DSN was provisioned (live contact forbidden by the envelope).
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
- Commits added by the gate: `58c8cb655` (F-10 test), `60972185f` (counter-drift pack split), + the F-G1 cleanup fix and this RESULTS/PACKS/QUARANTINE/LESSONS doc commit (pathspec-limited; foreign jsonl never staged).

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
