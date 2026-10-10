# Tracking: designer-critic-orchestration

## Iteration 001 — REJECTED (2026-10-10T14:23:35Z)
Workers:
- approve-worker-dco-core (1a66a430-8880-444c-9b9a-3f636d9fdcb8, plan-approval: plan-overview + architecture-recommendation) → APPROVED, 0 blocking / 4 notes
- approve-worker-dco-phases (7535567d-df74-413b-bd06-522f4ecd2913, plan-approval: phase1–4) → REJECTED, 3 blocking / 5 notes

Blocking (aggregated; none downgraded, none upgraded, none introduced):
1. Completeness — Q1.2 capture-recipe migration unowned/unjournaled. Binding source architecture-recommendation.md:109 ("recipe migrates agents/designer/tools_note.md:55-110 → agents/sketcher/tools_note.md") is executed by NO phase task; phase4 T5 deferred-debt has 5 entries, this not among them; phase1 AC8 passes without touching it (section has zero od.* tokens) → silently survives. Fix: add task row (extend phase1 T7 conditional edit) OR deferred-debt entry with rationale.
2. Consistency — phase1 edit scopes + prescribed prose contradict the set's own zero-token gates. (a) rule.md:38 carries a live od.generate reference but T5 anchors are only :12/:15/:35/:67 → survives phase 1; phase4 T9 TestDesignerHasZeroOdPorts then fails (also falsifies phase1 §5 hand-off claim). (b) T4 soul-identity sentence + T7 tools_note framing quote od.compose_brief verbatim → the plan's own AC8/T9 zero-greps reject the plan's own prescribed text. Fix: add :38 to T5 scope+outcome; reword T4/T7 token-free.
3. Feasibility — phase2-plan AC7 (:110): both verification greps use BARE | in plain grep (BRE = literal pipe) → false-red by construction vs mandated T7 prose. Byte-verified by approver: raw file shows `grep -n "DECLARED|EFFECTIVE"`; GNU grep 3.11 bare-pipe probe = 0 matches; AC1 fixed this same trap class, AC7 did not. Fix: add -E or split into individual greps.

Notes (non-blocking, merged/deduped across workers):
- arch-doc canonical JSON omits image_save from tools.deny (architecture-recommendation.md:89-90); executing phases correct (phase2 T3/AC2) — align the doc literal
- §5.1 (:150) deny-mechanism parenthetical is wrong (deny subtracts bare tool names, code-verified instance.py:379-393); verdict stands on other grounds — fix wording before someone "fixes" the mechanism away
- overview calls the three controls "Cardinals"; phase3 encodes as Guidelines (e)-(h) (cap-7 forced, only feasible encoding) — harmonize overview wording
- transient-red: tests/unit/plugin_subsystem/test_designer_rewire.py (:199/:204) red between phase 1 and phase 4 (SC-13 one-commit-per-phase) — fold into phase-1 commit or record as accepted
- stale cite: instance_messaging.py:1486-1510 → revival block now ~:2224-2254/:2342+ (shared blueprint drift; mechanic real)
- phase4 AC10 "15 commits behind" → 33 today (context only; gate drift-immune)
- phase4 T12 still uses the two-dot diff AC10 retires — sync command
- "mirrors sketcher pattern" imprecision in phase2 D6 rows (sketcher: ["dynamic-skill"], 12, has watchover; critic: +todo, 7, dropped) — wording only
- phase3 T7b span hedge ("-13 or whatever") vs overview :132-147

Positive: all 6 directive requirements served (both workers); ~80 line-anchored claims verified across partitions; bounded ≤3-round severity-gated loop, read-only critic (5-tool resolved set), worktree-only writes, promote-only go-live all confirmed sound.

Next: iteration 002 pending plan revision.

## Iteration 002 — REJECTED (2026-10-10T15:14:14Z)
Workers (3-way section partition, all plan-approval, fresh-context):
- approve-worker-overview (67d8a5e6-d203-4bf0-bef3-9fa7641010b0, plan-overview + architecture-recommendation) → APPROVED, 0 blocking / 8 notes (+5 unverified architect-cites flagged as executor first-run gates, not blockers)
- approve-worker-phases12 (27787b32-a17f-4309-9980-4dba82b881e0, phase1 + phase2) → REJECTED, 2 blocking / 9 notes
- approve-worker-phases34 (f8fe2b2a-2921-444a-9131-642f9dbd4e3a, phase3 + phase4) → REJECTED, 1 blocking / 11 notes (phase4 itself: zero blocking, approval-ready)

Blocking (aggregated; none downgraded, none upgraded, none introduced):
1. Consistency (gate vs edit) — phase1-plan.md §4 AC4 (:79) vs §3 T5 (:61). AC4 demands ≥4 grep-hit lines for lane_preference|same-code-class|other:user-requested-text-only|other:proxy-ceiling- in rule.md post-edit, but T5's verbatim binding text lands all alternatives on the single re-keyed :35 bullet (1 hit line) and never contains the literal token `same-code-class` (it says "the same `error.code` class"). Faithful execution → 1 hit line; ≥4 unreachable; phase self-rejects. Fix: add the literal token to T5's binding text AND/OR restructure AC4 per-alternative greps ≥1 each (the identical cure phase2 AC7 already carries as its Pass-5 fix).
2. Feasibility/safety (stale range + blind gate) — phase1-plan.md §3 T7 (:63) + §4 AC8 (:83), propagated to phase2-plan.md §3 T12 (:96); binding root at architecture-recommendation.md:110 (planner to align). Capture-recipe migration cites agents/designer/tools_note.md:55-110 but the section is header-delimited :55→~:140 ('## Capture Procedure' :55 … '---' before '## Two-Channel Image Reality' :142); literal :55-110 cuts the sidecar JSON block mid-fence (~:105-123) and drops the mandated 'Provenance tag policy' (:124-131) + 'Failure modes' (:132-~140) subsections. AC8 stays green on a partial move (orphaned tail carries zero od.* tokens — byte-verified) → broken fence parity in designer's file with a green gate. Fix: header-delimited range in T7/AC8-context/T12 + orphan-greps (designer-side `grep -c "Provenance tag policy"` = 0 post-move).
3. Consistency — phase3-plan.md §5 Hand-off :104. Bullet reads "Three new architect-mandated Cardinals in `rule.md` (…) — Cardinal cap preserved at 7", contradicting §1 :25, the D-rows :40-43, §2 :57 ("encoded as Guidelines (e), (f), (g) — NOT as numbered Cardinals"), T6 :74, AC9's note :95, and §5's own next bullet :105. Executed literally, AC9's grep (`grep -cE '^[0-9]+\.' agents/designer/rule.md` ≤ 7) returns 10 → phase fails its own gate and violates its own no-Cardinal-renumbering rule. Fix: reword to "Three architect-mandated controls encoded as Guidelines (e)/(f)/(g) in `rule.md` — NOT numbered Cardinals; numbered Cardinals unchanged at 7."

Cure verification vs iteration 001 (post-verdict comparison, not worker input):
- 001-blocker-1 (capture-recipe migration unowned): CURED — migration now owned + journaled (phase1 T7 conditional edit + phase2 T12 propagation); the fresh pass found a NEW defect in the same area (stale range/blind gate = new #2).
- 001-blocker-2 (phase1 edit-scope/prose vs zero-token gates): CURED — no re-flag of rule.md:38 or T4/T7 od.* prose; NEW gate-vs-edit defect surfaced at AC4/T5 (new #1).
- 001-blocker-3 (phase2 AC7 bare-pipe greps): CURED — per-alternative form confirmed applied; no re-flag.
- New #3 is the 001-Note "overview calls the controls Cardinals" family materialized as a gate-failing hand-off bullet in phase3 §5 — worker-rated blocking with byte evidence; stands.

Notes (non-blocking, merged/deduped across workers; highlights):
- Tail corruption (multi-edit silent-write class): phase1-plan.md :107,109-111 duplicate fragments; phase2-plan.md :138; plan-overview.md :226 — clean up before the phase-4 commit.
- phase4 AC9 capture-format dependency: `grep -cE "0$|^0 hits"` ≥ 18 needs T11 to pin per-gate capture format; several of the 18 gates expect non-zero hits.
- phase4 T13/AC11 diff form: two-dot working-tree diff ≠ branch-vs-pre-commission claim; use merge-base form (origin/latest...HEAD).
- phase3 §3 hard-exclusion wording vs T1/T8 plan-dir evidence files — add "outside this planning dir".
- phase1 AC12 positive-control line list wrong (count 6 correct); AC7 needs T2 to store a tail-slice sha1; phase2 T1 references a phase1-T11 schema-sha that T11 never records.
- Architect-cited executor gates to verify on first run: compare_tools.py:1265-1269 pinned_spec_sha binding; registry.py:1191/:1207 exists()/get_registry(); _auth.py:155-161 design→image-comparator auto-extension.
- sketcher byte-parity with live v0.18.5 (P1-Pre-2) not diffed (read-only scope) — existence + structure verified.
- Positive: ~100+ additional anchors verified exact across partitions; positive-control greps reproduce (AC3 = 2 lines; phase4 AC8 self-grep = 8; SC-9 probe clean with predicted `maintenancer` noise); resolved-tool-set math, deny-wins guard, worktree-only writes, no-promote posture all confirmed sound.

Next: iteration 003 = FINAL pass before ESCALATED (2 of 3 rejections consumed). All three blockers are one-line-class cures; phase4 and the overview/architecture partition are already clean.

## Iteration 003 — APPROVED (2026-10-10T15:43:45Z)
Workers (single whole-package pass, fresh-context; no partition — cross-phase sightlines preserved):
- approve-worker-plan (f0fd1448-6d8b-45c1-bd8b-1fb217f48e5e, plan-approval: all 6 in-scope files + read-only worktree cross-checks) → APPROVED, 0 blocking / notes only

Aggregation: no blocking issues raised by worker; none downgraded, upgraded, or introduced. Verdict = APPROVED.

Cure verification vs iteration 002 (post-verdict comparison; worker never saw tracking history):
- 002-blocker-1 (AC4 unsatisfiable ≥4-hit arithmetic): CURED — per-alternative grep split + positive-control greps confirmed in place ("AC4 and AC7 include positive-control greps that confirm the verification pattern is not false-green").
- 002-blocker-2 (stale capture-range :55-110 cutting JSON fence): CURED — Pass-6 re-pin :55-:139 cited; no range/fence-partiality/orphan-gate defect re-flagged.
- 002-blocker-3 (phase3 §5 :104 'three new Cardinals' vs Guidelines encoding): CURED — controls encoded as Guidelines (e)-(h), numbered-Cardinal cap intact at 7, confirmed binding and verifiable.

Notes carried forward (worker, non-blocking):
- compare_tools.py:1217 off-by-2 (actual :1219); phase1 T5 stale :35 narrative cite (implementing anchor :38 is correct); phase3 Cardinal #5 '(sketcher + critic)' parenthetical descriptive-only
- Correctly deferred/unverified at approval time: OQ-A view-views for critic, Q7.2 design.capture_mockup (spec-only + [VISUAL-QA-DEFERRED]), Q7.4 cap-math, live promote (out of scope per C3), T9 test line anchors (owned by the task itself)

Final: plan package APPROVED at iteration 003 (within 3-iteration cap). Release path: implement phases 1-4 in worktree, promote-only go-live per requirement 6.
