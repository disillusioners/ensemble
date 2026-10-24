# Approval Tracking: nest-ops shakedown defect patch

Plan: nest-ops shakedown defect patch — 12 defects F1–F12 on live v0.27.1 instance_read/instance_ops privileged tool surface + 2 class-level design-doc duties + 1 statement-only cleanup note
Slug: nestops-shakedown-defects
Artifact: .agents/shared/planning/nest-ops-shakedown-defects/ (worktree /home/nea/ensemble-src-wt-nestops-shakedown, branch fix/nest-ops-shakedown-defects @ b257842c9)

## Iteration 001 — 2026-10-24 — REJECTED

Dispatch: 3 section-parallel plan-approval workers, cold context (no tracking/planning history passed)
- 47418fea-07b4-4271-a578-cbe5f95bc385 — whole-plan coherence + cross-cutting constraints → REJECTED (1 blocking, 5 notes)
- 2d11b588-58e8-475d-b1ad-df3173f1aad0 — read-side F1/F2/F3/F4/F8/F12 + husk class doc → APPROVED (0 blocking, 6 notes)
- ef4e42cb-b80c-4e05-9fb2-07ac04e8aeb5 — ops-side F5/F6/F7/F9/F10/F11 + C2 doc → REJECTED (1 blocking, 8 notes)

Blocking (deduplicated to 1 unique — flagged independently by 2 of 3 workers):
- B1: phase5-plan.md T5.2(c) mandates stale pre-D-8 whole-gate F6 text ("a dry-run preview on a protected target is always allowed; it reports act_gate_would_pass — apply still requires confirmation + a live user-origin window") into the agent-facing agents/maintenancer/tools_note.md versioned prompt artifact. Contradicts decisions.md D-8, plan-overview.md SC8 (line 130), phase3-plan.md T3.10/AC3.6, architecture-recommendation.md §8 (line 197). False under BOTH the ratified minimal reading (unconfirmed preview still refuses protected-target-confirmation-required) AND the recorded veto-flip (apply would not require confirmation). Root cause: arch §8's stale-spot sweep covered only technical-analysis-ops-side.md (5 spots, corrected in place at :346); the phase files were never scanned. Remediation: one-line rewrite to the T3.10 wording (confirmation BOTH paths / provenance APPLY ONLY / act gate unchanged) + re-sweep the whole plan set for the superseded sentence before Phase 5 writes the prompt artifact. Dispatcher downgrade consideration: declined — true internal contradiction (verdict rule: internal contradiction ⇒ REJECTED), lands in a shipped agent-visible privileged-gate surface, independently graded Blocking by both finding workers.

Key notes (non-blocking, deduplicated):
- GT-10/T2.1 pin origin/latest = d4ecdbd3d; latest has advanced (074833b45+, code-bearing). DF-4 dependency substantively holds (11c75357f is an ancestor of latest, re-verified independently by 2 workers); soften to an ancestry assertion so an equality failure is not misread as "DF-4 absent". §10 anchor table likewise written against the older rev — T2.4 re-grep is load-bearing.
- arch §8 mis-states shipped preview payload: protected_confirmation_required is computed on EVERY protected preview (instance_ops.py:715-721), not refusal-only; T3.6 should say keep existing keys byte-identical, add only act_gate/act_gate_would_pass.
- F6 refusal-discriminator (confirmation-refusal vs provenance-refusal → refuse vs render-as-data) derivable but implicit; state it in T3.6 via the closed token set.
- F11 blast-radius bookkeeping: phase3 §F11 header lists instance_ops.py (not an F11 touchpoint) and omits job_processor.py (T3.3 touchpoint); count (8) and plan-overview §2 correct.
- F9 replacement copy: add the provenance-reality clause (agent-dispatched apply still refuses no-user-provenance — same posture the plan applies to F6); copy names instance_health/instance_get (instance_read category) possibly outside an instance_ops-only agent's tool set.
- T4.3 does not specify how the job claim is driven (worker pool vs direct claim) — name the e2e harness shape.
- C3 phrasing: "a spawn-without-dispatch cleanup DOES NOT exist" overstates its own cited sources (research §A.4 / read-side §11.3 scope it to live-parent/root never-dispatched); adopt precise scoping in the design doc.
- CHANGELOG tension: T5.5 "untouched when absent" vs Phase-5 exit criteria "entry present" — state the lane adds "## [0.27.7] — <date>" (dated-section convention; [Unreleased] headers are historical).
- Tag convention: D-15/T5.6 local-only tag vs newest house convention f13a4f59e (pushed, per v0.27.6) — confirm at stage time.
- Minor anchors: RESERVED_SOURCE_PREFIXES "agent:" is constants.py:956 (cited :946/:884/:949 are comments); read-side ±1–2-line citation drift (non-behavioral, R2 re-grep covers); T2.2(1) vs arch §9 check 1 intent-equivalent not byte-identical.
- Dated input docs carry superseded rows (technical-analysis-read-side.md §4.4 direction reversal, §9.3 pre-GT-7 mutation row) — frozen docs, executable plan correct everywhere.
- Plan set currently untracked in the worktree; Phase 1 entry criteria already commits it as the branch's first commit.

Positive (worker-verified): all 12 defects covered with code-accurate root causes (~60 read-side + ~15 ops-side/coherence citations re-derived independently, all holding), per-defect pinned mutation-verified regressions, real-PG/never-live discipline incl. per-variable POSTGRES_* scrub, e2e through the real spawn order, F12→F2/F4 and F11→DF-4 dependencies hard-gated and phased correctly (DF-4 confirmed landed), version line v0.27.7 correct, GT-7/D-1 premise correction verified sound.

Next: expect a narrow revision (one-line T5.2(c) fix + full-set stale-sentence re-sweep); resubmission → iteration 002.

## Iteration 002 — 2026-10-24 — REJECTED

Dispatch: 2 plan-approval workers (fresh instances, cold context), scoped to remediation surface + whole-set coherence
- 86460812-2db6-437a-9c70-5a6eb669b1de — F6 preview-gate chain end-to-end + full-set contradictory-wording sweep → APPROVED (0 blocking, 5 notes)
- 5486c2d6-368c-47ba-ba42-b59017fe42bb — whole-set coherence + completeness + constraints → REJECTED (2 blocking, 13 notes)

Iteration-001 blocking issue (phase5 T5.2(c) stale whole-gate text): VERIFIED REMEDIATED — D-8 → T3.6/T3.10 → AC3.6 → T4.4/T4.5 → T5.2(c) chain now uniform; agent-facing tools_note text is a verbatim-semantic match to D-8; T3.6 payload spec byte-accurate vs shipped code at origin/latest (:715-721); independent sweep: ZERO live contradictory instructions.

Blocking (new — both in phase2-plan.md DF-4 gate artifacts, one-line fixes each):
- B1: T2.2 check (b) + AC2.2 marker command UNSCOPED — `git grep -c 'direct-provenance-insufficient'` returns 7 files (planning prose / .agents/tester RESULTS) at origin/latest AND at base; it can never yield the stated expected 0. The tightening to a "machine-checkable count" dropped the pathspec the arch doc had. Remedy: `git grep -c 'direct-provenance-insufficient' -- daemon/ tests/` → 0 at origin/latest (3 at base = exactly what DF-4 removes).
- B2: AC2.1 exit criterion retains the equality pin "(origin/latest = d4ecdbd3d)" — plus stale headers decisions.md:3, architecture-recommendation.md:7, phase2-plan.md T2.4 — contradicting the plan's own ancestry rule (GT-10 "verify the ANCESTRY, not the SHA"; T2.1; §9 assumption 1). False today: origin/latest = 074833b45 (d4ecdbd3d is an ancestor, verified). Remedy: ancestry-form criterion + journal the CURRENT latest SHA; mark the three headers as point-in-time snapshots (2026-10-24).

Key notes (deduplicated across both workers):
- 2 uncorrected stale whole-gate spots in the dated ops-side doc (§2:48 "Chosen design… Move only the protected gate behind the dry_run fork"; §3.3:306 table row "protected gate | apply only") — missed by BOTH the §3 header enumeration and arch §8's five-spot list (true count: seven spots); AND arch G-7's bracket corrections have NO discharging task (T2.4 assigns to "orchestrator", none named) — assign an owner + verify-before-Phase-3 line.
- Phase-5 tools_note.md edit lacks a post-edit run of the forbidden-layer pin (tests/unit/test_instance_ops_prompt_layer.py::test_maintenancer_tools_note_is_clean) — add to AC5.2 (Phase-4 containment runs before Phase 5, so a Phase-5 red is never measured).
- Merge tasks will abort on dirty scratch (.agents/approver/active.md modified + tracking file untracked; upstream PACKS.md +128 vs T4.6 row edit) — add explicit remediation lines per merge task (house no-git-stash rule: checkout <sha> -- <path> + saved patches).
- CHANGELOG T5.5 self-contradictory (dead "fold into [Unreleased]" clause alongside the correct dated-section instruction) + version derivation inverted vs T5.6 step 2.
- T1.10 "update the docstrings" wording wrong — the "ten" strings are TEST METHOD NAMES (rename 3 methods in tests/unit/test_instance_ops_registration.py); no "ten" docstrings exist in the tool files.
- F11 label: "8-file additive chain" vs 7 enumerated daemon files (7 daemon + 1 pin file = 8) — align the label.
- New test files must NOT spell the retired token `direct-provenance-insufficient` verbatim — DF-4's tree-wide pin (test_gate_refusal_token_mapping, scans daemon/ + tests/) reconstructs it from two halves; constraint unstated in the plan.
- Module-docstring "[apply only]" annotation imprecise (2 of 3 tokens stay BOTH-paths under D-8) — use a split-precise annotation in T3.10 + ops-side §3.6.
- F9 provenance clause: absent from the §6.2 exact string / D-11 / F9-i pin; AND over-broad ("an agent-dispatched apply still refuses no-user-provenance" is wrong for the F11 admitted chain — a dispatch carrying a live user-origin chain IS admitted) — re-scope to "without a live user-origin chain".
- D-15 tag "local-only" stale vs current push practice (v0.27.5 / v0.27.6 pushed to origin).
- Minor: manager.py anchor 13588 (cited 13585); work_status.py is daemon/services/work_status.py; base-relative test anchors drift post-DF-4 (R2/T2.4 cover); legacy-pack rationale loose; severity labels diverge from brief (F7/F11); F2↔F12 coupling overstated in §5 vs ACs; Phase-1 entry criterion tense ("clean at b257842c9" + "commit the planning set" cannot both hold — read as "branched at").

Positive (worker-verified): requirement coverage complete (all 12 defects + 3 class duties → tasks/SCs/mutations); F12 root cause re-verified in code (manager.py:13588 forward → lifecycle :5086-5095 drop → repo :1135-1137 honours); F11 dependency ancestry-robust in executing tasks, S3-pop hole + multi-line source-mint census empirically validated at origin/latest (2/3/0); §10 anchor table byte-accurate at 074833b45; real-PG discipline sound (per-variable scrub, TEXT-ISO trap, husk UUIDs byte-identical across all six citing docs); v0.27.7 census verified free.

Next: iteration 003 = FINAL before mandatory ESCALATED (max 3). Expected revision: the two one-line phase2 fixes + note folds.

## Iteration 003 — 2026-10-24 — REJECTED (3rd rejection — STATUS: ESCALATED, max-3 cap reached)

Dispatch: 2 plan-approval workers (fresh instances, cold context)
- a6ffca44-eddc-4ac5-b262-e061fec16c9f — gate-command/criteria soundness audit → REJECTED (1 blocking, 6 notes)
- 6c7b1528-8418-45fb-8485-f4ce3c063c8e — whole-set consistency + D-8 wording sweep + completeness → REJECTED (4 blocking, 9 notes)

Iteration-002 blockings VERIFIED REMEDIATED: B1 scoped marker command (gates worker re-derived: scoped `-- daemon/ tests/` → 0 files at origin/latest, exactly 3 at base with per-file counts 2/1/3); B2 AC2.1 ancestry form (every DF-4 dependency statement across all 11 files now ancestry-form; equality pins only in explicitly-labeled point-in-time snapshots / T2.4-superseded input docs).

NEW blocking (5, deduplicated across workers — none downgraded; all narrow fixes):
- G1: architecture-recommendation.md §9 check 5 (:220) still carries the UNSCOPED marker-count gate (`git grep -c 'direct-provenance-insufficient'` → 0 files) — unsatisfiable at EVERY rev (7 files at base AND latest; prose under .agents/**, and this plan set's own committed docs grow it further). The fix landed in phase2 T2.2(b)/AC2.2 but was never folded back into the doc declared AUTHORITATIVE (plan-overview:6); the blanket authority clause + T5.3 no-contradiction sweep resolve conflicts IN FAVOR of the broken form and make phase2's documented deviation itself a T5.3 "contradiction". Remedy: fold `-- daemon/ tests/` into arch §9 check 5 (or add an enumerated-exception mechanism for adjudicated supersessions).
- S1: F9 reclaim copy — decisions.md D-11 (:113) and phase3 T3.9 (:31) both cite ops-side §6.2 (:452-462) as "the exact corrected string", but §6.2 lacks the provenance-reality clause T3.9 mandates and F9-iii pins verbatim; the required final wording exists NOWHERE in the 11 files → two implementers produce two shipped copies and two pins. Remedy: write the single canonical final copy (with the clause) into §6.2; cite it from D-11/T3.9.
- S2: ops-side §3.6 second bullet (:347) still carries the superseded whole-gate "[apply only]" module-docstring annotation (T3.10 carries the refined "[apply only — provenance] on the provenance line only"); §3.6 is one of the seven spots the §3 callout (:275) claims corrected, but only its first bullet (:346) carries a bracket. The G-7 discharge check (phase3-plan:4, `grep -c 'CORRECTED per D-8' ≥ 7`) counts exactly 7 with ZERO headroom — it counts §3.4's second bracket (:323) and misses §3.1's differently-phrased correction (:281 "CORRECTED 2026-10-24 — SUPERSEDED by D-8") — so it PASSES while the half-corrected spot remains: structurally unable to detect the defect it was written to prevent. Remedy: bracket :347; re-key the discharge check to per-spot presence of the enumerated corrections, not a raw count.
- S3: F4 `truncated` — the condensed claim "empty page past the end ⇒ True" (decisions.md D-3 :37, phase1 T1.1 :28, plan-overview SC3 :125, arch §3.2 :118, read-side §9.3 :674) contradicts the mandated formula `bool(total_matching > applied_offset + len(census))` on the REACHABLE overshoot case (offset > total_matching; no upper clamp, instance_read.py:495-502; read-side's own precise form at :391 carries the missing qualifier "and total > offset"). T1.1's pin case "exhausted" is never defined → an implementer can write a pin contradicting the formula. Remedy: restore the qualifier at all five sites; define "exhausted" = len(rows)==0 ∧ total ≤ offset.
- S4: phase1 AC1.10 (:65) requires updating the "all ten" DOCSTRINGS — no such docstrings exist anywhere in the tree (the strings are three TEST METHOD names: tests/unit/test_instance_ops_registration.py:100/:120/:124); contradicts the correct T1.10 (:37) in the same file; decisions.md D-6 (:63) and arch §6 (:159) repeat the wrong surface. Remedy: re-point AC1.10/D-6/arch §6 at the test method names.

Key non-blocking notes (both workers): arch §0/§8 "5 stale spots" enumeration stale vs seven (authoritative doc carries superseded count); T2.1 active.md rider commit guarantees a merge conflict NOT in the conflict-watch (upstream rewrote the same lines; suggest newest-wins + history-in-note like PACKS.md); PACKS magnitude "+128" → measured +102 at latest; D-12 (:121) retains the "exactly two conditions" phrasing arch §9 itself calls a wording defect; construction-never-literal constraint at T4.3 but not T3.1 (structurally enforced by the Phase-3 exit re-run — redundancy, not a hole); CHANGELOG T5.5 version derived before T5.6's authoritative re-derivation; ~5 line-cite anchors drifted (all covered by T2.4 re-grep-authoritative rule); read-side §9.3 F3 row = covered historical row (header supersession map names it); §3.4 bracket "(bypassed, apply-path)" whole-gate vocabulary residue; "in-flight lanes untouched" honored by construction but never stated explicitly; R3 ~2,002-query estimate ~15% high (T4.8 arbiter + D-20 threshold unaffected).

Verified sound across all three iterations (for the Leader's escalation decision): 12/12 defect coverage (task → AC → mutation pin, mechanically re-derived); F12 root cause exact in code; F11 chain + DF-4 hard gate correct and now ancestry-robust; D-8 ruling uniform in every live instruction across plan/decision/arch files; scoped DF-4 marker numbers exact; ~40-anchor table byte-accurate at origin/latest; real-PG/never-live discipline, TEXT-ISO trap, husk fixtures (UUIDs byte-identical across all citing docs); corrected net-new ladder; v0.27.7 version census; stage-only/no-promote mechanics. ALL FIVE REMAINING BLOCKINGS ARE ONE-LINE/NARROW FIXES — the artifacts keep failing on fold completeness, not substance.

STATE: ESCALATED. Per max-3 rule: Leader presents the full tracking history (.agents/approver/nestops-shakedown-defects-tracking.md, iterations 001-003) to the user. A remediation + fresh-eyes pass can be re-queued as a NEW approval cycle (iteration 001 reset) only by Leader direction.
