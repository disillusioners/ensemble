# Tracking: aegis-security-family

Plan set: .agents/shared/planning/aegis-security-family/ @ 948be899b (branch feature/aegis-security-family)

## Iteration 001 — REJECTED (2026-11-04)
Workers: strategy layer APPROVED (0 blocking, 9 notes) · execution layer REJECTED (6 blocking, 8 notes).

Blocking issues (all execution-layer; anchors are plan-set file:line):
1. Phase 6 cannot produce the staged release — three stage.sh preconditions unmet (no tag-creating task; no merge to origin/latest => tip-identity refusal exit 78; stage command missing target arg => stages to demo while gates read live dir). phase6-plan.md:27,28,68,69,72-75,81-85,102.
2. P4/P5 scope gate (complement == planning-set only) contradicts the merge-to-latest + version-bump that staging requires (bump touches pyproject/daemon.__init__/uv.lock outside agents/+tests/). phase4-plan.md:61,108-114; phase5-plan.md:38,54,97-103; phase6-plan.md:27-34,102.
3. G3.8 sandbox-containment gate unsatisfiable against its own persistent user-space install root (no write-target exemption classification). phase3-plan.md:41,50,65,162; phase2-plan.md:49.
4. No mechanical pin for any legality-line guardrail — T1–T28 pin allowlists/counts/taxonomy only (T16 = cardinal count); G2.5 is an unpinned human read; deletion of the "no external targets, ever" cardinal stays green. phase4-plan.md:39; phase2-plan.md:66; technical-analysis.md:848-875.
5. "Findings private" (goal guardrail family #4) is cardinalized in NO persona — aegis workflow prose only, absent in all three children; cracker/sweeper cardinal cap already saturated (6+1). phase1-plan.md:48-49; phase2-plan.md:46-48; plan-overview.md:169,334 (off-repo Q2 deviation documented).
6. P6 task 8 removes the lane worktree = destroys the only provenance-sidecar copy (release dir carries none; FE sidecar stripped) — contradicts "full provenance" and the plan's own P6-R6 mitigation. phase6-plan.md:34,55,75,131; stage.sh:227,418.

Notes carried (non-blocking, deduped): strategy-layer N1-N9 (council-trail row unfilled in decisions.md §F; developer->aegis hand-off label unreachable-peer; exploit-attempts coverage implicit; worker-offensive-boundary prose pin; "network-free" wording; T23 byte-identical qualifier; shared_meta_kv findings-content clause; F-001 id collision undispositioned; research count wobble) + execution-layer N1-N8 (P2 file-count 18-vs-21; bare-python pytest leg; R2 fail-open shape-check insufficiency; P5 base-leg worktree understated; per-tool source-build checksum story; findings in-project vs off-repo goal wording; cardinality ledger privacy pressure; verified-correct arithmetic/claims census).

Fold guidance for 002: blockers 1+2 share one root cause — the release-lifecycle steps (merge/bump/tag/audit-push, staging worktree KEPT) are owned by NO phase; fold must either add a release-mechanics phase or write the coordination contract, AND redefine the scope gate over lane-own commits. Blocker 5 needs an explicit fold-or-cap decision recorded (council-D7 pressure). G2.5 should become an AM-6-style prose-assertion pin covering all five legality lines.

## Iteration 002 — REJECTED (2026-11-04, @ d0fea922a; fresh-context workers, zero fold history passed)
Workers: strategy REJECTED (1 blocker, 0 design defects) · execution REJECTED (9 blockers).

FOLD VERIFIED FIXED (convergence evidence): cardinal sets all-five-families in every persona (§B.3 authority + T16 re-pin) ✓; T29 5×4 verbatim matrix + named mutation leg (persona deletion reds) ✓; G3.8 three-class write-target table satisfiable vs installer contract ✓; scope-gate/giter-set separation DESIGN ✓; council-trail §F filled ✓; exploit-attempt coverage explicit ✓.

RESIDUAL BLOCKERS (fresh-derived, not inherited):
A1. plan-overview.md:5,:65,:356-359 stale Phase-0 status (council+approver "PENDING") contradicts decisions.md:362-363 review trail — trivially foldable doc-state.
B1. phase6-plan.md:38,:97 stage command lacks `live` target arg → resolve_env demo → INSTALL_DIR=agents-ensemble-demo while G6.2–G6.5 read live dir; ENSEMBLE_UPGRADE_LIVE=1 is only the guard, not the selector (stage.sh:133; lib.sh:1244-1247,1361-1372; canonical form docs/runbooks/upgrade-drills.md:520).
B2. phase6-plan.md:35,:76,:66 merge step unrunnable — `latest` checked out at shared /home/nea/ensemble-src (277+ behind origin/latest); guard at :35 checks remote not local; no park/advance recipe encoded.
B3. phase6-plan.md:38,:44,:66,:97,:184-185 no task CREATES the staging worktree at the tag; stage run from lane worktree fails exact-tag/tip-identity/sidecar-identity exit-78s; P6-R6:160 factual error (release dir keeps NO sidecar copy — FE sidecar stripped stage.sh:~418).
B4. phase6-plan.md:38 rollback adjudication stranded — ENSEMBLE_ROLLBACK_SAFE unbound; 96/119 migrations match destructive-DDL grep (stage.sh:336-346) → exit 78.
B5. phase6-plan.md:38 no FE build owner — stage.sh:283-286 refuses UI-less release; lane worktree has no dist/, no task runs npm ci + _build_frontend.sh.
B6. phase6-plan.md:89-92,:116-119,:155 live fence wrong both directions — state.json FILE-sha false-FAILs on legitimate journal rows (stage.sh:666-669) while the .env marker write (stage.sh:718-785) is unfenced false-PASS; fence must target promote fields + .env sha.
B7. phase4-plan.md:109-115; phase5-plan.md:104-110 lane-own scope predicate reds TODAY — complement=13 vs planning=12; extra path .agents/approver/active.md (approver bookkeeping committed in d0fea922a); predicate must tolerate .agents/approver/** or the file moves out of lane-own diff.
B8. phase2-plan.md:52,:54,:70,:161 vs phase1-plan.md:112-114 P2 task-8 bidirectional alignment fixes (aegis adopts child wording) contradict G2.9/exit-criterion-9/task-10 (only child trees change).
B9. phase2-plan.md:49,:66-68; phase3-plan.md:55,:71,:143-144; phase4-plan.md:39 (T23) installer-skill FENCES guardrails not RED-able — uniform deletion of NO-Docker/no-system-packages/no-fetch-and-execute/no-plaintext bullets keeps cross-body byte-identity + checksum bullet → no test reds; .lib.sh pinned by single-keyword grep (any-one-pass); G3.2-G3.6 human inspection only.

Notes carried: A-remainder (TA stale restatements lacking [SUPERSEDED] markers :923,:995-997,:1024,:917; overview :89-92 5-family prose divergence; child-bash egress residual unrouted; T29 absence-vs-not-asserted wording) + B-remainder N1-N11 (placeholders, FAIL-without-exit-1, sidecar paths, T29 not in P0 pre-registration, base-leg worktree unnamed, no phase owners, un-manifested .md read-target exemption, install/run mode, shared toolbench root, skill_injection benign-omission, D-2 sweep gap).

PATTERN: iteration-001 blockers 1/2 (release mechanics) folded on paper but NOT executable; blockers 3/4/5 (content) genuinely fixed. Worker B's cheapest-path list (park shared checkout → advance latest → merge --no-ff → 4-file bump → tag push → detached staging worktree at tag → build FE+binary → ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=<v> stage.sh live → fence on promote fields + .env sha; scope predicate fix; G2.9 scoping; FENCES per-bullet pin + library-contract pack; exit 1 + placeholders) is the fold recipe for 003.

NEXT: iteration 003 of 3 — third rejection escalates to Leader.

## Iteration 003 — REJECTED (2026-11-04, @ 9969bb0d3; fresh-context workers, zero fold history passed) — CAP REACHED, ESCALATED

Workers: strategy REJECTED (2 blockers) · execution REJECTED (3 blockers).

PRIOR-FOLD VERIFICATION (convergence): 8 of 10 iteration-002 blockers GENUINELY FIXED at fresh anchors — live target + gate/read-path agreement ✓; park/advance/merge recipe runnable on this host (git 2.43 refusal avoided, ff-only ancestry proven) ✓; staging worktree at tag created with all three exit-78 guards owned ✓; ENSEMBLE_ROLLBACK_SAFE bound ✓; FE build task ✓; scope predicate passes today (13 = 12 planning + approver tolerance) ✓; P2 exception leg not silently voidable ✓; T30(a) closes the .md FENCES uniform-deletion class (per-bullet presence per body, acceptance test REDs) ✓.

BLOCKERS (final, fresh-derived):
S1. Guardrail-family cardinal voice contradiction — decisions.md §B.3:146-147 asserts "EVERY persona carries ALL FIVE families as cardinals" while its own enumeration (:151-173) carries 4/5 for aegis/hacker/sweeper (synthetic-data absent-by-applicability); phase2-plan.md:46,:48 self-contradicts in one sentence; T29 count stated as 20 asserted (technical-analysis.md:876, phase4-plan.md:39, phase5-plan.md:183) vs decisions.md:423-425 n6(d) "recorded, not asserted" (17+3). Substantive exposure: no cardinal outside cracker forbids real credential/hash ingestion while aegis/hacker/sweeper all hold bash and sweeper's gitleaks leg reads real secrets — family-3 ownership is the contradicted surface.
S2. decisions.md §F single-source review trail self-contradicts — record header :5-7 and §F intro :356-357 say council+approver PENDING directly above rows :362-363 recording them DONE; NO iteration-002 row while plan-overview.md:5,:65,:360 + phase0-plan.md:6,:57 cite §F for "001 REJECT → 002 REJECT → 003 pending"; G0.1's named evidence artifact cannot be satisfied from itself. Same class as iter-002 A1, now in the authoritative record.
E1. 4-way sha reads a non-existent path — phase6-plan.md:131 cats <staging>/ensemble-prod.build-provenance.json (real: dist/ensemble-prod.build-provenance.json per _provenance_path + stage.sh:166; confirmed on two live staging worktrees); no exit check → silently degrades to 3-way; git_head/git_dirty asserted nowhere though G6.2/S13 require them.
E2. Live fence weaker than stated + factually wrong premise — phase6-plan.md:111-117,:145-152 diffs only 5 promote-field keys (no exit semantics); .env sha :116/:151, symlink :114/:148, livez/PID :117/:152 PRINTED only; no journal-delta attribution command exists. Weakening rationale wrong: journal_init is a no-op when live state.json exists ([ -f ] && return 0) and quarantine_clear no-ops for a fresh version → whole-file sha fence WOULD have held.
E3. Secret scan cannot fail — phase5-plan.md:119 `git diff $BASE | grep -nE 'sk-(proj|ant)-|…' ; echo …` — the `;` discards grep's exit; the exact risk P5-R6 names passes silently; violates FAIL-CLOSED house rule.

TRAJECTORY (for Leader adjudication): 001 = 6 blockers (content + release design) → 002 = 10 (content fixed; release mechanics unrunnable) → 003 = 5 surgical (8/10 prior fixed; residuals are 2 doc-voice contradictions + 3 command-level defects — wrong path, weak fence, discarded exit). All five are mechanically foldable without redesign; the cap is the cap — no APPROVED on promises.

Notes on record (deduped, 13 execution + 8 strategy): stale T1–T28 restatements post-T29/T30; AM-9/AM-8 typo across architecture:542 vs decisions:234; installer-body read-target discovery implicit; AM-6 value-rule aegis-only; worker boundary doctrine-not-mechanism (read at ceremony); findings-privacy reinterpretation belongs in ceremony text; F-001 collision undispositioned; evidence-location undefined + carve-out narrow (spurious Gate-H red risk); latest-advance worktree fate + shared-checkout restore + MISSING feature-branch audit push; 277+→861 commit figure; guard-vs-advance moved-tip recovery path; uv/PyInstaller prerequisite + uv.lock dirty-build risk; G6.3 census command missing; Gate-A capture truncates FAILED list (tee idiom prescribed); CWD-order at :109/:124; vX.Y.Z substitution sites; T30(b) behavioral-leg wording; overview task-count/path/live-version bookkeeping.

STATUS: ESCALATED (3/3 rejections). Leader adjudication required.
