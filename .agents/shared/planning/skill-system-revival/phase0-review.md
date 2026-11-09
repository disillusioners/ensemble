# Phase 0 Quality-Gate Review — `phase0-investigation.md`

- **Date:** 2026-11-09
- **Reviewer:** Reviewer (v2) — review controller; adjudication of three parallel read-only lane audits
- **Target:** `.agents/shared/planning/skill-system-revival/phase0-investigation.md` (1,367 lines)
- **Evidence ground truth:** `findings/lane1-runtime-db.md` (719 ln) · `findings/lane2-code-config-history.md` (644 ln) · `findings/lane3-flakiness-evidence.md` (224 ln)
- **Auditors (worker instances, one skill each — `plan-review` v1.2.0, all confirmed loaded):**
  - Lane 1 (runtime/DB): `5d2ee8a3-aa4c-4480-8b1c-741f8eb85108` — 15 anchors
  - Lane 2 (code/config/history): `d7c41426-827c-456b-bfe0-d7caa73cc64d` — 23 anchors
  - Lane 3 (flakiness/deviation): `12c8b806-8d2d-4a75-845a-dfe6ca3b7b34` — 21 anchors, plus whole-report checks D/E/F
- **Discipline:** all three auditors were read-only; zero edits were made to the report, the findings files, the repo, the DB, or git by any participant. This review file is the only write.

---

## VERDICT

**APPROVE-WITH-FIXES** — the report is fundamentally faithful to its three evidence lanes (50 of 59 sampled anchors exactly clean, **zero** direct number-vs-source mismatches, zero confidence-inflation on mechanisms, exemplary no-action discipline, and an honestly-stated headline), but it cannot be approved as-is: §5.6–§5.7 print mutually exclusive metric-population numbers without flagging them as a variance (and one of them, the +479 counter drift, is used as a §6.2 monitoring baseline and a §6.3 Phase-3 exit criterion), one commission-mandated sub-answer (the daily-rule trigger state) is missing entirely, one causal-inference cluster is presented under lane brackets without an inference label, and §6 omits three lane-documented preconditions/residuals — all ten fixes are targeted edits, none require re-investigation.

**Plain statement per the strict bar:** under the commission's rule ("a report that carries one unanchored or wrong number is a failing report"), the report AS IT STANDS fails the bar — §5.6/§5.7 contain numbers that cannot all be true simultaneously (see B1/B2) and the report does not disclose that collision as it did for its other two variances. With the ten blocking fixes applied, it is approvable without re-running any lane.

---

## BLOCKING DEFECTS (must fix before approval)

**B1 — §5.7 drift table does not close arithmetically; the non-closing number is a monitoring baseline and a phase exit criterion.**
- Where: §5.7 lines 952–954; §6.2 line 1021; §6.3 Phase-3 exit criterion line 1038.
- Evidence: the report's table prints `Σ skills.total_applied | 6,069 | 6,536 | +467`, `actual applied usage rows | 5,646 | 5,057 | −589`, `counter drift (applied) | +423 | +479 | +56`. Computed: 6,069 − 5,646 = +423 ✅ (consistent, corroborated by lane3 L190); **6,536 − 5,057 = 1,479, not +479**. The +479/+56 pair is consistent only with a comparator of **6,057** (6,536 − 6,057 = +479; drift Δ = +56), which contradicts the printed 5,057/−589 pair in the same table. Note the provenance: the Lane-1 auditor certified the "+479" figure as faithful to Lane 1 B11, and Lane 1 itself records `actual applied usage rows | 5,057` — i.e. **the tangle originates inside Lane 1 and the report inherited it silently**. The report flagged Lane 1's 141-vs-146 and the 10,056/10,057/10,059 denominators as variances but NOT this one, while simultaneously using +479 as the §6.2 baseline and the Phase-3 "does not regress past +479" exit criterion.
- Exact fix: in §5.7, add an explicit variance disclosure mirroring §2.5's style — e.g. *"Lane 1 B11's own table does not close: it records 5,057 actual applied rows AND a +479 drift, which are consistent only if the comparator is 6,057. Either 5,057 is a typo for 6,057 (drift +479, Δ +56 stand) or the drift is +1,479 (grew by +1,056). This was not reconciled by any lane."* Until reconciled, mark the §6.2 baseline and the Phase-3 exit criterion **provisional** (or restate the criterion as "counter drift does not regress past the reconciled baseline — see §5.7 variance").

**B2 — the numeral 6,052 carries two mutually exclusive definitions 27 lines apart, unflagged.**
- Where: §5.6 line 937 vs §5.7 lines 964–965.
- Evidence: L937 (sourced Lane 3 H5, verified clean by the Lane-3 auditor): *"6,052 live rows (selected=true AND task_succeeded=true AND feedback_applied IS NULL) of 10,057 total; … feedback_applied=true=3,446"*. L964–965 (sourced Lane 1 B11 matrix, verified as a faithful quote by the Lane-1 auditor): *"applied=T/feedback=T 6,052 · applied=F/feedback=NULL 3,532 · applied=F/feedback=F 469 · applied=T/feedback=NULL 3 · applied=T/feedback=F 1"*. These cannot both be true: if 6,052 rows were applied=T/feedback=T, then feedback_applied=true would be ≥6,052, contradicting 3,446; and Lane 1's applied=T total from its own matrix (6,056) contradicts its own 5,057 applied-row count. Most plausibly Lane 1's matrix cell label is transposed/conflated with Lane 3's false-eligible population — but the report quotes both verbatim, adjacent, without noticing the collision.
- Exact fix: in §5.7 (or a new variance note after L965), flag it explicitly: *"The 6,052 in Lane 1 B11's feedback matrix (labeled applied=T/feedback=T) collides with Lane 3 H5's 6,052 (feedback_applied IS NULL false-eligible population) and is incompatible with both the 3,446 feedback_applied=true count and Lane 1's 5,057 applied-row count. One of the two 6,052 definitions is mislabeled at source; unreconciled by any lane."* — the same honest treatment §2.5 gives the 146-vs-141 split.

**B3 — Commission Task 1's daily-rule sub-answer is MISSING (mandated explicitly).**
- Where: §2.7 (lines 310–326) — the trigger census lists only count-based rules plus a `periodic_scan | interval 7 days` row.
- Evidence: the commission stated *"must cover trigger-engine COUNT-based AND DAILY rules … the daily-rule state must be ACTUALLY reported, not just the count-based one."* Lane 1 B4 line 196 holds the finding and the report never surfaces it: *"**No 'daily' condition_type exists in the table** — the daily cadence is a *config* value (`SKILL_EVOLUTION_DEFAULT_DAILY_SCAN_HOUR=3`, `.env.example:138`), not a trigger row."*
- Exact fix: insert immediately after the §2.7 trigger table (after line 326): *"**Daily trigger-rule state:** no `daily` condition_type exists in `skill_triggers` — the daily cadence is governed by the config setting `SKILL_EVOLUTION_DEFAULT_DAILY_SCAN_HOUR=3` (`.env.example:138`; YAML knob `default_daily_scan_hour: 3`, alongside `default_task_count_threshold: 20` and `metric_scan_interval_hours: 24.0` [Lane 2 config.yaml extract]), not by a database trigger row. [Lane 1 B4]"*

**B4 — the "concurrent writes during the census window" explanation is an UNSOURCED authorial inference presented under lane brackets, and its framing is incomplete.**
- Where: §5.7 lines 972–974.
- Evidence: the report writes *"…so concurrent writes are the most plausible cause — but the three denominators were not reconciled by any lane. … [Lane 1 B1/B11; Lane 3 H5/§6.1]"*. Lane 1 contains **zero** occurrences of "concurrent writes" or "census window"; no lane proposes the hypothesis. Additionally, all three denominators arise **within Lane 1's own query sequence** (B1 count = 10,056; B11 matrix sum = 10,057; B11 selected = 10,059 — Lane 3 H5 independently also produced 10,057), so the report's cross-lane window framing (Lane 1 vs Lane 3 query times) is not even the full story. Contrast §2.8, where the report labels an inference exemplary: *"(This is an inference from two anchored facts, not a lane finding…)"* — no such label exists here.
- Exact fix: relabel per the §2.8 pattern: *"*(Authorial inference, not a lane finding: concurrent writes during the live window are a plausible hypothesis; no lane states it. Note all three denominators — 10,056 (B1), 10,057 (B11 matrix sum), 10,059 (B11 selected) — arise within Lane 1's own query sequence; the variance was not reconciled by any lane.)*"*

**B5 — §4.1 credits a 2026-09-17 gate with a 2026-09-01 boundary — internally incoherent and the one place the Emperor's flakiness framing creeps back into explaining the stop.**
- Where: §4.1 lines 620–624.
- Evidence: *"**A mitigation already landed — and it is the reason capture has not produced junk since 2026-09-01:** blank `task_message` captures are refused **before enqueue** since commit `09d50c78b` (2026-09-17 …)"*. The gate's own date (quoted two lines later) postdates the boundary it is credited with explaining; the underlying fact is Lane 3's (L113, verified verbatim), but the causal gloss *"and it is the reason … since 2026-09-01"* is the report's own addition, unlabeled — and it contradicts §2.8 (L338), which declares the 2026-09-01 stop cause **UNVERIFIED**. Note also `09d50c78b` does not appear in Lane 2 (0 hits) — Lane-3-sourced only, correctly so.
- Exact fix: reword to *"**A mitigation already landed — and it is why capture has produced no junk since the gate landed on 2026-09-17:** …"* and, if the 2026-09-01 linkage is retained at all, label it: *"…reinforcing (not causing) the 2026-09-01 stop, whose cause remains UNVERIFIED (§2.8)."*

**B6 — §6.1 omits Lane 2's documented capture precondition: `skill_injection: true` (Gate 3).**
- Where: §6.1 preconditions P1–P11 (lines 999–1009) contain no agent-eligibility precondition, although the report itself states the fact at §3.9 L577 and §4.3 L658–663.
- Evidence: Lane 2 L414: *"**Capturing** additionally requires `ENSEMBLE_SKILL_CAPTURE_ENABLED` **and** an agent whose `skill_injection` is `true` (Gate 3) — 16 of 24 agents qualify."*
- Exact fix: add row **P12**: *"| **P12** | Ensure the intended capturing agents have `skill_injection: true` | Gate 3 gates capture eligibility — a non-injection agent can never capture; only 16 of 24 agents qualify, so the 8 `false` agents are silent capture sources [Lane 2 A1.1 S6 / A2.4 / A9] | confirm the target agent set's flags at the target tree; verified by `skill_capture` rows appearing only for opted-in agents | CONFIG/AGENT-META |"*

**B7 — §6 never states that shipping the P1/P2/P3 code fixes to the live daemon requires a PROMOTE (frozen per-release binary), not just an env-flip restart.**
- Where: §6.1/§6.3 — Phase 1 (L1036) says only "Land P1…, P2…, P3…"; the sole promote mention (L518) is a cost aside.
- Evidence: Lane 2 L407: *"for the **frozen PyInstaller release binary** the *code* itself is also frozen per release directory (`releases/<ver>/`), so the switch semantics only change after a **promote + restart**"*; L413: *"and, if the change is to reach a frozen release, a **promote** of a build that contains the intended code."*
- Exact fix: add **P5b**: *"| **P5b** | A code fix reaches live only via PROMOTE, not env-flip | the live daemon is a frozen per-release build (`releases/<ver>/`); P1–P3 land nothing until a promoted release contains them [Lane 2 A9 item 2] | confirm `/livez` reports a release containing the fixes before flipping any switch | DEPLOY |"* and amend Phase 1's exit criteria to "…and the fix is present in the promoted release."

**B8 — §0 verdict-table row 13 over-asserts what its own §5.3 body hedges (table-vs-body inconsistency; the commission designates this class blocking).**
- Where: §0 row 13 (line 56) vs §5.3 F3 row (line 878).
- Evidence: row 13: *"…a code-proven DOUBLE inversion makes F1/F3/F6 report `dark` while publishing live series…"*. §5.3 (F3): *"in dev the verdict was *correct*; the disclosure's 'publishes in production' claim is **not verifiable from code alone**"* — and §5.4 makes the live-series claim for F1 (9 series) with F6's 15 series in §5.3, but F3 published **0** in dev. The mechanism (double inversion) is consistent; the "publishing live series" clause is asserted for F3 beyond what the body establishes.
- Exact fix: amend row 13 to *"…makes F1/F3 report `dark / capture_off` unconditionally and F6 report `dark / census_gated`, with F1/F6 publishing live series (F3's production publishing not verifiable from code alone — §5.3)"* or append the parenthetical hedge.

**B9 — §6.3's junk-skill rationale is mis-anchored: neither cited anchor supports connecting evolution-ON to the junk-skill class.**
- Where: §6.3 lines 1043–1045.
- Evidence: the report: *"re-enabling evolution while capture is still off and the A/B groups are wedged would reproduce the 2026-08/09 'junk-skill class' that the `09d50c78b` gate was written to stop. [Lane 3 M2; Lane 3 §7.4]"*. Lane 3 M2 defines the junk-skill class as the **capture-side** duration-only trivial-filter class, and `09d50c78b` gates the capture admission path; Lane 3 §7.4 supports only the A/B-backlog rationale ("Unblock or clean the A/B backlog…"). The parallel recommendation at L1058–1059 carries no junk-skill clause, so the correction is confined to L1043–1045.
- Exact fix: either (a) drop the junk-skill clause and keep the A/B-deadlock rationale (which §7.4 supports), or (b) re-anchor the junk-skill clause to **capture** re-enable ordering (before the P2 telemetry/threshold repair), citing Lane 3 M2 + §7 item 2.

**B10 — §6 omits the fourth of Lane 3's explicitly-instructed "residuals to at least re-state in the re-enable decision".**
- Where: §6.1 — the report re-states three of Lane 3 §7 item 7's four residuals (per-agent read scoping §4.6 L756; unauthenticated admin endpoint §4.5 L737; ungated inline `skill_evolve` §3.9/§7.C-23) but the fourth appears nowhere (grep-verified).
- Evidence: Lane 3 L204: *"…**inactive-but-served skills still accumulating usage** ([DB] sample `cb1813ea` used by coder TODAY 05:09Z, census finding 2)."* Materially relevant: an inactive skill still served to live agents is exactly the hygiene risk a re-enabled capture lane compounds.
- Exact fix: add a §6.1 row (or extend P4): *"| **P13** | Re-state residual: inactive-but-served skills still accumulating usage | an inactive skill (`cb1813ea`) was served to a live coder agent as recently as 2026-11-09T05:09Z [Lane 3 §7 item 7, census finding 2] | served-skill roster cross-checked against active flags before capture re-enable | HYGIENE |"*

---

## NON-BLOCKING IMPROVEMENTS

1. **§5.7 drift-baseline provenance:** the 2026-11-04 comparison point rests on `.agents/tester/RESULTS/2026-11-04-skill-metrics-census.md`, which Lane 3 (L190) discloses is *"untracked file, committed nowhere"* — append that provenance limit at L962 so the two-point trend's non-re-verifiability is visible.
2. **Attribution-precision nits (all 🟢, class (i)/(iii) at the bracket level, claims themselves lane-anchored elsewhere):** (a) L362 "26 enumerated fields" is Lane 1 L691, not Lane 2 — split the bracket or drop the count; (b) L371 live-install `config.yaml` line 447 is Lane 1-sourced — attribute the lane2 half only to the repo checkout `config.yaml:553-586`; (c) L112 `job_queue_service.py` byte-identity between trees is Lane 3 §0 — remove the Lane 2 §0 co-citation.
3. **Restore the trailing ellipsis** in the `decisions.md:16-19` quote (L444) or drop the word "verbatim" (L440).
4. **Strengthen §3.4 with Lane 2's unused 4-axis disproof** — `HANDOVER.md:20`: *"Skill capture was NEVER on — disproven on 4 axes … (d) code history — flag landed 2026-09-17 default-OFF, never substantively enabled."* The report reaches the same conclusion only indirectly via Lane 1's DB cut-off.
5. **§4.4 log provenance:** the forensic quotes are log-corridor extractions in +07 local (Lane 3 L94); the report restates them in UTC — add "(log-corridor timestamps, +07 local in source; UTC here)".
6. **Anomaly-report zero statement:** §2.6 argues coverage structurally (38 rows, 2026-11-06→09 only); if Lane 1 A9/B9 supports it, add the explicit "0 of 38 concern skill-keeper" zero-count.
7. **Row 15 / §2.4 wording:** L274 "paused mid-flight" vs board note 87's "paused mid-edit BEFORE land.py executed" — soften to "paused mid-edit (board note 87)".
8. **Appendix B `CST` node** names three env surfaces; §0 row 5 / §3.1 enumerate four — add the live process env to the node text.
9. **Board-note-89 quote (L449–450):** append an inline "(incomplete — see §6.4)" so a skimming §3.4 reader cannot mistake the quoted prior record for the report's instruction.
10. **16-day precision (cosmetic):** "16 days" is correct as calendar-date arithmetic; exact elapsed UTC is ≈14d 23h 31m (commit stamped 04:16:45 +0700 = 2026-09-16T21:16:45Z). Optional: "16 days by date (≈15 days elapsed UTC)".

---

## ANCHOR AUDIT RESULT

**59 anchors sampled (target ≥25) · 50 clean · 9 flagged · 0 direct number-vs-source mismatches · 0 contradicted SHAs/dates · 0 confidence inflations on mechanism findings.**

| Lane | Checked | Clean | Flagged |
|---|---|---|---|
| Lane 1 (runtime/DB) | 15 | 12 | 3 (→ B3, B4, B2) |
| Lane 2 (code/config/history) | 23 | 19 | 4 (all 🟢 → improvements 2a–2c, 3) |
| Lane 3 (flakiness) | 21 | 19 | 2 (→ B9 🟡; improvement-class co-citation nit) |

Failed/flagged list: (1) §2.7 daily-rule finding omitted [Lane 1 B4 L196] → B3; (2) §5.7 L972–974 unsourced inference under lane brackets → B4; (3) §5.6 L937 vs §5.7 L964–965 conflicting 6,052 definitions vs Lane 1 L375/L384 → B2; (4) L362 "26 fields" bracket misattribution; (5) L371 live-config.yaml bracket misattribution; (6) L112 byte-identity bracket misattribution; (7) L444 verbatim-quote ellipsis dropped; (8) L1043–1045 junk-skill rationale anchor does not support the claim → B9; (9) L980 `[Lane 3 §c.8]` co-cited for a demo count Lane 3 explicitly declined to query (claim true via Lane 1, which the report itself notes at L1133 — delete the co-citation).

Notable clean results worth certifying: every lane-attributed SHA in the report exists in its lane (15/15 verified for Lane 2); Appendix B's byte/line counts are machine-exact (719/87,229 · 644/73,593 · 224/39,607 · 1,587/200,429); the 6,052/10,057/3,446 triple is verbatim Lane 3; the `skill_injection` census (16/8/24) is verbatim-identical; the "created, not captured" correction and the `job_processor.py` SPLIT correction are carried faithfully. The B1 arithmetic defect surfaced under internal-consistency checking (F), not anchor-vs-source sampling — the report faithfully copied a Lane-1-internal tangle; its duty was to flag it as a variance, which it did for two other variances but not this one.

---

## COVERAGE MATRIX

| Commission task | Status | Section refs | Notes |
|---|---|---|---|
| 1. Most recent capture runs (existence, dates, what, outcomes) | **Answered — one mandated sub-item MISSING** | §2.1–§2.2, §2.4, §2.6 | 25 rows, 2026-08-24→09-01 cut-off, 21/2/2 outcome census, 6 captured skills all inactive, wedge zero — all verified clean. **Daily-rule state missing → B3.** Count-based rules fully covered (§2.7 simulation table matches Lane 1 A6/B4 exactly). |
| 2. Disabled state + disabling EVENT | **Answered — strong** | §3.1–§3.4 | Both lanes' switch surfaces with 4-surface absence table, precedence chain, and dated events: capture `b96c0d671` 2026-09-17 (born OFF), evolution `605f50f16` 2026-10-22 21:09:17Z (born OFF as containment, `decisions.md:16-19` quoted) + board note 89. Thin only: Lane 2's S5 knob (`fix_cooldown_seconds`) never appears in §3. |
| 3. Flakiness evidence (logs / anomaly reports / wedge captures / misreads / forbidden edits) | **Answered** | §2.1, §2.4, §2.6, §4.1–§4.7 | Logs: named sources (§4 banner) — thin provenance (impr. 5). Anomaly reports: window argument given, explicit zero statement absent (impr. 6). Wedge captures: explicit zero, correctly reasoned, correctly attributed to Lane 1. "What it did wrong": concrete acts with verbatim evidence (bash/python3 YAML edit from `$HOME`, 24 edit_file calls across 10 files, git stash dance + unpushed commit `660e73453`, forced A/B resolve at comparisons 2/10, direct-SQL landing paused mid-edit). |
| 4. Skill metrics inventory incl. FE Skills page | **Answered — carries numeric tangles** | §5.1–§5.7 | 17 families (10 live / 6 dark / 99 series), FE Skills page traced to real endpoints (`GET /api/skills`, `/metrics`, `/fix`, `/usage-records`, `/ab-test/stats`, `/triggers`) with what a user sees on each page, `capture_active` never wired, R-i/R-ii label swap, false-dark reasons — all verified. §5.6–§5.7 population numbers unreconciled → B1/B2. |
| 5. Re-enable plan input (preconditions / monitoring / phasing) | **Answered — three gaps** | §6.1–§6.4 | 11 preconditions with anchors + verification methods, 9 monitoring signals with "observable TODAY?" honesty column, 7 phases with exit criteria + rollback levers; all explicitly labeled RECOMMENDATION / NOT IMPLEMENTED / NO DECISION TAKEN. Gaps → B6, B7, B10. |

---

## CHECK RESULTS (D / E / F / G)

**D — Nothing-promised / no-action-taken: PASS (exemplary).** Declared at the top (L9–15: "No fix was made. Nothing was re-enabled. Nothing was changed…"), re-asserted at the §6 banner, carried by heading labels, closed at L1366–1367. A full verb sweep found only negations, quoted prior records, and config descriptions. The single borderline item — the verbatim board-note-89 quote at L449–450 — is neutralized twice elsewhere; improvement 9 makes it skimmer-proof.

**E — Safety / secrets: PASS.** No credential value, token, or raw .env VALUE anywhere (pattern sweep clean); variable names only, with values explicitly withheld at L191–193/L196–197. Both required caveats present at every re-enable discussion point: restart-requirement (L51, L63, §3.6, L1006, L1027–1031, L1055) and canonical-env override (§3.2, L1039, L1050–1053); capture's no-YAML-path fact correctly stated (L1054–1055).

**F — Internal consistency: 3 real defects.** Verdict table 14/15 rows consistent (row 13 → B8); §1 hard limits (transcripts swept, detached-HEAD divergence, log contamination, dirty checkout +819) all carried consistently wherever they bear on claims — one provenance omission (impr. 1); §5.7 drift arithmetic non-closure (→ B1). Appendix B diagram present, mermaid fences well-formed (full-file fence census balanced), BRAKE `O1` node matches §3.6, both hard-evidence fact nodes (`EVA` swept-transcripts, `EVB` metrics defect) match §1.3/§5.2–§5.3; one node-text nit (impr. 8). The report also *corrects* a stale pinned critical note (queue `is_paused=false` since 2026-10-26 vs the note's claim) rather than repeating it — a genuine quality signal.

**G — Honesty of the headline: PASS, stated loudly and not softened.** The executive summary states it in the first paragraph: *"the Oct-22 incident did not cause capture to stop — capture had been dark since **2026-09-01**, roughly **seven weeks before** the incident"* (L33–35); both switches presented as born OFF by ABSENCE (L24), the evolution switch explicitly as the *response*, not the cause (L446, with `decisions.md` "Why OFF" quoted); the 2026-09-01 cause left **UNVERIFIED** and carried as open question 1; and §3.5/§3.4 explicitly correct the commission's own premise ("created, not captured"; "the correction to the commission's own premise"). The Emperor's broken/flaky framing is NOT preserved as the established cause anywhere — the single regression is the §4.1 sentence (→ B5), a wording fix, not a framing failure.

---

## VARIANCE RESOLUTIONS (C)

**C(i) — §2.5 queue split, 146 vs 141: RESOLVED — Lane 1 internal tabulation error; the report's presentation is honest and adequate.**
Lane 1 F2.3 (L595–600) states all 141 skill jobs rode exactly two queues, then tabulates `bd075f0b` = 25 capture + **114** evolution and `cd02c5d2` = **7** evolution; 114 + 7 = **121** evolution jobs, exceeding the true population of 116 (Lane 1 A2: 116/116 FIX rows; B3 per-day census sums to 116; B6: "25 + 116") by exactly +5 — hence 146 vs 141. It is a manual markdown tabulation error **within Lane 1**, not double-counting and not a different denominator. The report's §2.5 handling is exemplary: it quotes the table, states *"The per-queue split is therefore not self-consistent as written"*, and anchors every operational conclusion to the robust facts (141 total, two queues, `bd075f0b` identity). **No fix required.**

**C(ii) — §5.6/§5.7 denominators 10,056 / 10,057 / 10,059: RESOLVED — unsourced authorial inference, insufficiently labeled.**
All three denominators arise within **Lane 1's own** query sequence (B1 `count(*)` = 10,056; B11 feedback-matrix cell sum = 10,057; B11 "actual selected usage rows" = 10,059; Lane 3 H5 independently reported 10,057). No lane mentions "concurrent writes" or a "census window" — the hypothesis is the report author's. The report half-discloses ("the three denominators were not reconciled by any lane") but presents the causal hypothesis under lane brackets without an inference label, unlike its own exemplary §2.8 labeling. **Required fix: B4.** The report's claim that every §5 finding's *shape* is unaffected by the variance is accepted (dead `task_message`/`iterations` and the false-eligible magnitude hold under any of the three denominators).

**16-day inference ("the capture switch was born 16 days after capture stopped"): arithmetic RIGHT as printed; labeling RIGHT.**
`b96c0d671` dated 2026-09-17 confirmed in Lane 2 L88 (with subject and diffstat); the 2026-09-01T21:45:19Z cut-off confirmed in Lane 1 A1/B2 and Lane 3 §1.2 (Lane 2 itself only bounds "all ≤ 2026-09-17" — the report attributes the exact date to the correct lanes). Calendar-date difference 2026-09-17 − 2026-09-01 = 16 days ✅ (exact elapsed UTC ≈ 14d 23h 31m because the commit is stamped +0700 — cosmetic, impr. 10; the conclusion is unaffected under either reckoning). The conclusion is explicitly labeled twice as an inference, not a lane finding (L339–340), and carried as open question 1 (L1086). **No fix required** beyond the optional precision note.

---

## SESSIONS

- Lane-1 audit: `5d2ee8a3-aa4c-4480-8b1c-741f8eb85108` (plan-review, loaded ✅, feedback recorded)
- Lane-2 audit: `d7c41426-827c-456b-bfe0-d7caa73cc64d` (plan-review, loaded ✅, feedback recorded)
- Lane-3 audit: `12c8b806-8d2d-4a75-845a-dfe6ca3b7b34` (plan-review, loaded ✅, feedback recorded)
- Adjudication, variance resolutions, verdict: Reviewer (this file) — the only write performed in this review lane.
