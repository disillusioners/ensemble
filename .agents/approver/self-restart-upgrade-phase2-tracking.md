# Tracking: Self-Restart / Self-Upgrade Phase 2 (self-restart-upgrade-phase2)

Corpus: .agents/shared/planning/self-restart-upgrade-phase2/ (10 docs, ~2,191 lines)
Dispatch: 3 section-parallel plan-approval workers (coherence / safety / feasibility), cold context.

## Iteration 001 — 2026-08-22 — REJECTED

All 3 workers independently REJECTED. ~50 code citations spot-checked across workers; all verified accurate (including non-obvious corrections). Defects are all doc-level internal contradictions / one missing task — surgical fixes, no redesign.

### Blocking issues
1. plan-overview.md §1 item 2 (line 24) + §4 mermaid P22 node (line 77): enumerates 3 tools — `upgrade_status` missing; contradicts ruled 4-tool surface (§4 P2.2 row line 66, §8 Q3 line 140, ADR-023, D-FA2.1). Fix: add upgrade_status to both spots.
2. tool-api-design.md §3.2 + §12 OQ2: rejected PORT-derivation fallback still presented as documented degraded mode; OQ2 unstamped ⟪SEAM: W1⟫. Contradicts ratified D-FA2.3 (marker mandatory, fail-closed). Implementer told to follow §3.2 "verbatim" could build the rejected fallback.
3. tool-api-design.md §3.1 matrix "system_upgrade dry_run — live: free (issues nonce)" vs promotion-ladder.md §5 U4 (default: agents do NOT read live paths; per-request user approval required). Two safety-core docs mandate different behavior for the same operation; §4.3 confirmation flow depends on the free cell.
4. phase2-plan.md D5 (lines 46-47) + D6 gate matrix system_upgrade row (line 57) + T8 acceptance (line 76): two-factor gate required on sandbox/demo/dev, contradicting the requirement "demo/dev proceed freely", ADR-017(b), tool-api-design §2/§3.1/§4, test-strategy §1 (line 38). T8 acceptance and §1 unit test cannot both pass.
5. phase3-plan.md D1 clean-cycle table c1–c6 (lines 38-48) + T9 (line 81) vs test-strategy.md §4.1 (lines 118-126): two non-nested "objective" definitions of clean cycle; ADR-021 + ladder S3 bind test-strategy §4.1. T9 points implementers at the definition the ladder doesn't recognize. Fix: make §4.1 single definition, fold c5/c6 in as clauses/evidence, re-point T9.
6. phase1-plan.md Tasks T1–T10 (lines 69-80): ENSEMBLE_SELF_ENV marker staging assigned to P2.1 by D-FA2.3/S-02/tool-api-design §532, consumed by P2.2 (S-31: marker absent → ALL actor tools refuse) — but no P2.1 task stages it. Executed as written, P2.2 sandbox demos guaranteeably fail closed. Fix: add marker staging to P2.1 T2 with acceptance.

### Notes (non-blocking, carried for next iteration)
- plan-overview §6 #2 / ADR-017 wording: factor-count drift (2-factor vs 3-factor D-FA3.1 nonce); reconcile when minting ADR-032.
- Daemon-down alerting rests on unratified ADR-025(b) (NEEDS USER DECISION); define fallback or state accepted gap.
- Risk register lacks corrupted-artifact / TOCTOU entry (coverage exists corpus-wide via D-FA4.4/T2-T3).
- phase2-plan:22 cites nonexistent daemon-tool-registration-gotcha.md; cite code lines instead.
- Lock naming drift: phase1 interface sketch "rollback.lock" + test-strategy D8 "ADR-004 m7" vs canonical mkdir rollback.lock.d (D-FA5.1).
- DR-4 cap drill leaves 3 rollbacks in demo 24h window → blocks T9 clean cycles; sequence journal-reset.
- test-strategy line 37: label live gate-logic unit as zero-live-contact.
- phase1 D1: state explicit out-of-scope line for remote "fetch" (ADR-009 D3).
- Carry-overs (scope item 4) verified verbatim-faithful; live safety airtight (no test/drill reaches live); ruled decisions (ADR-029+, 4 tools, mkdir lock) coherent.

Status: iteration 001 REJECTED → resubmit after fixes; iteration 002 on re-request.

## Iteration 002 — 2026-08-22 — REJECTED

W1 (overview+phases) APPROVED, W3 (tool-api/risk/test) APPROVED, W2 (decisions/ladder/arch) REJECTED. All 6 iteration-001 blocking issues verified fixed in 002 (4-tool surface consistent; ENSEMBLE_SELF_ENV mandatory fail-closed + P2.1 T2 staging; §4.1 single canonical + phase3 D1 folded; gate matrix demo/dev free; live dry_run per-request approval ruled U4; journal-reset sequencing present). New rejection is different defects, concentrated in promotion-ladder.md + architecture-recommendation.md.

### Blocking issues
1. promotion-ladder.md §4 line 69: invalid mermaid (mangled mash-up of lines 68/70, unterminated label `S6["S6 rollback to previous<br G6{{"🚨…`); §6 lines 100–101 orphan fragment (` up to S3.`) + duplicate of line 99. Artifact-integrity failure in the load-bearing ladder diagram. Fix: rewrite line 69, delete 100–101.
2. architecture-recommendation.md §Confidence lines 302–303: progressively truncated duplicate tail fragments of line 301. Same botched-write signature. Fix: delete.
3. promotion-ladder.md §5 USER-GATED table U5 (line 88): says "two-factor (param + server-side user-originated marker, ADR-015 mechanics)" — corpus-ruled gate is 3-factor (+ action-binding nonce; decisions.md ADR-017 + index:195, arch D-FA3.1, uniform everywhere else). Zero nonce mentions in ladder. Implementer copying U5 builds a weaker live gate. Fix: rewrite U5 row to 3-factor w/ nonce, cite ADR-017. (Iteration-001 note "factor-count drift" partially resurfaced here.)
4. promotion-ladder.md §2 Rollback Cap table line 38(a): "(a) further auto-rollback/promote refused" at cap — contradicts adjudicated D-FA4.2 (rollback NEVER refused on cap; sweep must execute past cap or the env is stranded; only promotes refused at entry). Fix: amend row so sweep/auto-rollback proceeds past cap, only new promotes refused.

### Notes (non-blocking, carried for iteration 003)
- SYSTEMIC: duplicate/partial-tail write corruption in ≥6 of 11 docs — blocking in ladder+arch (above); cosmetic in phase2-plan:118–120 (+T6 cell), plan-overview:182, tool-api-design:551–558, test-strategy:40 ("systemantics" cell). Recommend full-corpus tail/line scan before resubmission.
- tool-api-design: stale §4.2(b) sentence (contradicts §4.1 dated correction); stale §1 row-3 gate cell (superseded by §1a A2/§3.1 refused-this-initiative); `after-turn` mode semantics undefined vs graceful-now (mark ⟪SEAM⟫ or drop).
- risk-register: summary table omits R-SR20 (gap 17→20; R-SR18/19 live only in arch doc); "rollback.lock" naming drift (R-SR03, §7) vs canonical mkdir `rollback.lock.d`.
- test-strategy: deploy.sh citation 36-38 → actual 112-113 (substance correct); row 37 live-confirm case holds for system_upgrade only (live system_restart refused per A2).
- phase3 D1 table not a complete §4.1 clause mapping (no clause-2 row; c3/c4 diverge) — align or state mapping.
- "fetch" descoped to local-only version resolution per phase1 D1 with rationale — user should consciously accept this reading of scope item 1.
- U4 id collision: overview §7 risk table vs tool-api-design approval tier.
- Open user-gated rulings (N=3 ADR-021, alert channel ADR-025, ADR-016..020 deviations) correctly routed; should be ruled before P2.3 T9 finalization.
- W1/W3 verified 18+ repo citations exactly; hard constraint verbatim corpus-wide; no test/drill reaches live.

Status: iteration 002 REJECTED → surgical fixes to promotion-ladder.md (3 issues) + architecture-recommendation.md tail (1 issue) + corpus tail scan; iteration 003 on re-request. MAX 3 — one rejection remains before ESCALATED.

## Iteration 003 — 2026-08-22 — REJECTED (ESCALATED — max iterations reached)

W1 (phases) APPROVED, W2 (tool-api/ladder/decisions) REJECTED, W3 (safety) APPROVED. All iteration-002 blocking issues verified fixed by fresh workers (ladder mermaid + tail corruption gone; arch tail clean; U5 now 3-factor w/ nonce; cap table D-FA4.2-conformant — rollback past cap stated in ladder §2, tool-api interlock 3, D-FA4.2). ~60 repo spot-checks across workers; all verified accurate. Hard constraint verbatim corpus-wide; no test/drill reaches live; zero-live-contact by construction. New rejection: 2 propagation defects of ruled decisions into primary docs — both annotation/default-value fixes, not structural.

### Blocking issues
1. tool-api-design.md §2.1 :73 + §2.2 :120 — `dry_run: bool = False` contradicts ratified ADR-022(b) (decisions.md :97, index :200) + architecture-recommendation D-FA2.2 :91 ("dry_run default TRUE … a hallucinated parameter set must never execute a real promote on the first call"). §2.1 received same-day edits (soak budget 180s→300s at :87) — the default was missed. Implementer copying §2.1 verbatim ships the exact hazard ADR-022 prevents (no gate on demo/dev). Fix: flip both defaults to True or stamp superseded by ADR-022.
2. decisions.md ADR-017 :33 still specifies the REJECTED env-derivation mechanism ("install-dir path + port + DB triple assertion … never from caller claims"); ENSEMBLE_SELF_ENV appears nowhere in decisions.md; test-strategy.md §1 "env identity" row :39 specs a unit test against that rejected mechanism AND omits live from the derived set. Contradicts ruled D-FA2.3/D-FA2.4 (:94-104) + tool-api-design §3.2 :221 (marker mandatory, PORT-fallback rejected, fail-closed). Fix: supersession-stamp ADR-017's mechanism sentence (intent preserved — caller claims verified via marker self-match; triple assertion remains canonical at the script layer per D-FA4.6); rewrite test-strategy :39 pass criteria around env-self-match + script-layer triple assertion.

### Notes (non-blocking)
- Cooldown-refusal semantics ambiguous: which rollback class refuses during the 10-min cooldown (manual rollback.sh / gate-fail auto-rollback / ADR-012 sweep); ADR-024 "sweep respects cooldown" (decisions.md :121) vs ladder §2 :36-38 cap-only carve-out vs phase1 T6/T7 silent. Needs one decision-table row (recovery-first recommended). [W1#7 + W3#1 + W2-note merged]
- Unstamped resolved seams: tool-api §1a A1, §12-4/§12-5/§12-7, §4.4 SEAM :336 still read "awaiting architect" though S-01/S-04/S-05 resolved — P2.2's own gate ("seams require architect finalization", :9) would show red.
- tool-api §2.3 :162 stale error enumeration ("No errors beyond unknown section/version") contradicts env-self-match read-refusal at :142 + §3.1/§3.2; §2.4 error surface (unknown run_id) unspecified.
- rollback.lock.d clean-release path (who removes the dir, when) unstated; stale-break bounds impact — state it.
- phase1-plan :94 pseudocode still legacy `rollback.lock` (non-.d) despite D-FA5.1 CONFORMED stamp.
- Journal record naming drift: phase1 D4 `in_flight` / phase2 D2-T4-T7 `pending_restart` vs canonical D-FA1.1 `pending_op` (phase2 T6 already references it).
- `dev` target seam: tool enum includes dev (tool-api :70) but marker staged only into INSTALL_DIR/.env → dev-resident Ari fail-closes (safe direction); make the dev stance explicit (dev-staging rule or "staged-install-only" statement).
- decisions.md :177 "executed as FIRST P2.2 task (T0)" — no T0 exists in phase2-plan; closures actually wired T5/T8/P2.3-T8/PR-time.
- plan-overview §8 Q2 :139 points to "phase1 Task 4"; decision lives in phase1 D2 :36 + ADR-027.
- Makefile `stage-demo` :347 vs new `upgrade-stage` wrapper (P2.1 T9) — rename or disambiguation note.
- Drill taxonomy: runbook DR-1..5 (phase3 T1 :76) vs test-strategy D1..D8 unmapped; D8 (concurrent-attempt refusal) absent from DR set — add mapping line.
- `scripts/upgrade/restart.sh` authorship implicit (referenced as executor payload; not a named P2.1 script task).
- launchd KeepAlive (ThrottleInterval ~10s) may respawn inside the launcher-swap stopped window (D-FA4.1 / phase1 T2) — one runbook line.
- architecture-recommendation: stale "§6" :26 / "§7" :126 labels (content resolves); D-FA1.1 :44 retains dropped `after-turn` enum value (annotate reserved-future or trim); R-SR18/R-SR19 lack detail sections.
- phase3 :50 maps §4.1 clause-3 (window-wide log-scan) to point-in-time probe c4 — §4.1 governs; keep window-scan as required evidence.
- plan-overview :98 stale "P2.3 defines N and clean" sentence (superseded by §4.1 canonicity ruling; §8 Q4 already correct).
- test-strategy :16 legend omits "e2e" level used at rows 28/43.
- §2.2 inline comments stale vs A2 (live system_restart refused outright; params deliberately schema-stable per A3); `mode: "graceful-now" = "graceful-now"` syntax at :118.
- "fetch" interpreted corpus-wide as local version resolution only (ADR-009 D3) — coherent; user consciously accepted reading of scope item 1 (carried from 002).

Status: iteration 003 REJECTED — MAX 3 iterations reached → Status: ESCALATED in active.md; escalated to Leader. Both blocking fixes are surgical (2 default flips or supersession stamps in tool-api-design.md; supersession-stamp ADR-017 + rewrite one test-strategy row). Recommend Leader route: apply the 2 fixes (+ cooldown decision-table row while in there), then re-approval limited to a delta check of the 3 touched docs rather than full-corpus iteration 004.
