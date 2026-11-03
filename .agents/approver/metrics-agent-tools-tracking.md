# Approver Tracking: metrics-agent-tools

Plan: metrics-agent-tools — agent tool surface over the central metrics system
Plan set: .agents/shared/planning/metrics-agent-tools/ (worktree /home/nea/ensemble-src-wt-metrics-tools, branch feature/metrics-agent-tools, 15 files / ~5,040 lines)
Mode: Plan Approval — 2 section-parallel workers (large-plan exception), one skill each (`plan-approval`)

---

## Iteration 001 — 2026-11-03 — VERDICT: REJECTED (2/2 workers REJECTED)

Workers (fresh-context dispatch, zero tracking/rejection history in prompts):
- approve-worker-plan = 620d8ae9-2a36-4725-81f2-18b5ecc94206 → REJECTED (2 blocking, 8 notes)
- approve-worker-tech = 18c096ff-4061-4750-8272-3a49269290f8 → REJECTED (3 blocking, 6 notes)

Aggregation: dedup B1≡B1 (identical finding, merged most-specific variants); B2∪B2 merged (W1's 4-site enumeration + W2's semantic footprint); B3 unique to approve-worker-tech. No Blocking→Note downgrades (all three are gate-facing internal contradictions or a self-failing close-out criterion). No Note→Blocking upgrades, no approver-introduced blockers.

### Blocking findings (aggregated, deduped)

**B1. [consistency — BOTH workers] E-1 amendment-2 count not propagated to the phase-3 gate criterion.**
- Where: `phase3-plan.md:44` (Phase 3 per-phase success criteria, row "E-1 discharged under the adjudication").
- Expected: "the amendment applied to EXACTLY the **FIVE** canonical line-ranges (`:31-32`, `:318-322`, `:379-380`, `:387`, `:404-405`)" — binding per `decisions.md` Part 4 (E-1 amendment-2 / lane-owner re-adjudication 2026-11-03: "widens to the FIVE canonical line-ranges (`:387` is IN)"), and restated at `phase3-plan.md:7` (objective), `:36` (task 3.6), `:37` (phase fence — itself lists five ranges incl. `:387`), `plan-overview.md:26` (P-1), `:113` (R9), `:132` (P3 criterion).
- Found: "EXACTLY the **4** cited sites (guardrail (a))" — the pre-amendment-2 count, contradicting the same file and five other sites.
- Failure mode: the per-phase success criterion is what a gate runner checks; read literally it FAILS a compliant 5-range amendment (or drives an implementer to drop `:387` — the `maintenance:read` revocation-lever site — reproducing exactly the doc/code divergence E-1 amendment-2 exists to eliminate). `fold-ledger.md:158` (council W1) omits this site from its propagated-sites list while claiming the fold done.
- Fix (mechanical): one-line text correction at `phase3-plan.md:44` + add the site to W1's propagated list in `fold-ledger.md:158`.

**B2. [consistency — BOTH workers] AmRULING-3 trim survives in four stale working copies, including the entry point's scope bullet.**
- Binding: `decisions.md` Part 5 AmRULING-3 + `phase1-plan.md` task 1.5 — Protocol amendment = GAP-1/GAP-2 + GAP-3 only (`listener_enabled`/GAP-4 and `census_enabled`/GAP-5 DROPPED); `_Fake` grows EXACTLY ONE member (`registry_read_ok`); "the fake cost SHRINKS from three booleans to ONE".
- Stale sites (pre-fold wording in normative present tense):
  - `plan-overview.md:40` (Scope → In Scope item 1 — the ENTRY POINT's scope statement): still "registry_read_ok / listener_enabled / census_enabled properties".
  - `plan-overview.md:106` (Risk R2 mitigation): still "`_Fake` needs exactly the three booleans (defaults True) — a mechanical two-line cost".
  - `fold-ledger.md:26` (§A.2 "The implementation"): still lists all three properties.
  - `fold-ledger.md:29` (§A.2 "The audited cost"): still "`_Fake` → the three booleans, defaults True".
- Ledger self-certification defect: `fold-ledger.md:159` (council W2) identified the text as STALE vs AmRULING-3 and re-stated it ONLY in task 1.5 — the row claims FOLDED while leaving ≥3 additional sites stale.
- Semantic footprint (approve-worker-tech): `envelope.activation_block()` getattr-reads `listener_enabled`/`census_enabled` (`daemon/services/metrics_query/envelope.py:56-71`), and `phase1-plan.md` task 1.6(iii) pins `activation_block(provider)` to return the exact wire block against BOTH the concrete provider and the duck-typed fake — under the stale three-boolean variant the fake's block cannot match the concrete provider's.
- Fix (mechanical): restate the one-boolean / GAP-1+GAP-2+GAP-3 shape at all four sites; widen the W2 row (`fold-ledger.md:159`) to name every site it reaches.

**B3. [completeness/consistency — approve-worker-tech] Plan-file manifest does not match the tree: `architecture-recommendation.md` omitted.**
- Where: `plan-overview.md` §Provenance Inputs table (5 rows) AND §"Plan-file manifest (this plan set)" (11 rows / 14 files).
- Expected: the manifest lists EVERY file in the lane dir — `research-folds.md` §5.2 makes it a REQUIRED section "listing EVERY file in the lane dir"; the immediately-preceding lane's fold-ledger `central-metrics-v1/fold-ledger.md:78` (R-c(ii)) records omission-from-manifest as a council finding; this lane's own Phase-6 task 6.4 requires "the plan-file manifest matches the tree".
- Found: the lane dir contains 15 `.md` files; the manifest covers 14. `architecture-recommendation.md` (139 L) is absent from BOTH tables despite being the declared source of the architect rulings (`decisions.md:232`: "Source: `architecture-recommendation.md` … verdict SOUND-WITH-AMENDMENTS — READ-ONLY input") and cited 11× across the plan set as authority for AmRULING-1/3/4, AmM-1/2/3, AmA-1/2/3/5/6/7/8/9/10, AmB-2/6/9/10/11/12/13/14, AmC-4/6/7/8/9/11, C-1, C-11. A cold-start reader of `plan-overview.md` has no pointer to the artifact that produced a third of the binding rulings. Verbatim repeat of lane #1's council finding.
- Fix (mechanical): add the row (file + purpose + provenance) to both the Inputs table and the plan-file manifest.

### Notes (non-blocking, merged + deduped)

- N1 `plan-overview.md:161/:181` — provenance row "technical-analysis.md … BINDING INPUT, untouched / 1755 L" is now false: 1757 L with seven inline fold pointers (`:224`, `:313`, `:315`, `:459`, `:1471`, `:1473`, `:1581`) per `decisions.md` Part 5 + fold-ledger W1. Truth-in-docs class the plan itself folded (W8).
- N2 `fold-ledger.md` §D-3 — deviation row still describes the superseded "factory + `*_if_available` wrapper + extend" shape (dropped by RULING-1/Option A; AmM-3).
- N3 `decisions.md:77` (Part 1 D-UX-12; D-UX-13 same pattern) — still names `create_metrics_read_tools_if_available` "returns `[]` when absent", contradicting AmM-3/RULING-1 (tools stay present and refuse loudly); supersede is recorded in Part 5 but there is no in-place marker at the point of reading.
- N4 Unstated prerequisite — Phase 1 task 1.7 / Phase 5 task 5.1 mandate `WT=<worktree>` + worktree-local `.venv/bin/python` in every PACKS.md row fence; the worktree has NO `.venv` (lazy-venv rule; KV records venv absent at creation) and no task provisions it (`uv sync` appears nowhere). Name the provisioning step or adopt the house-certified cd-worktree + main-venv interpreter form.
- N5 The only consistency sweep (task 6.4) runs at Phase 6 close-out — AFTER all code phases. Add a pre-implementation sweep leg at Phase 1 entry; and make the sweep catch wording-variant residue: concept-token greps for BOTH variants of every folded enumeration + a manifest-vs-listing check (would have caught all three blockers).
- N6 No explicit rollback posture statement (structurally safe: no promote, no migration, no new dependency, `maintenance:read` retained ⇒ zero behavior change for existing holders, staged-not-promoted) — record one line for the ceremony record.
- N7 Known residual `fold-ledger.md` §B `contract_RevocationLeverProse` (contract doc `:58-60` names only `maintenance:read` as emergency-off lever) deliberately left stale outside the five canonical ranges, owner named — disclosed; will read as doc/code divergence to the next reader.
- N8 `decisions.md:181` — guardrail (a) "the 4 cited prose places" inside the P-1/E-1 ruling block; historical record, explicit supersede note follows immediately (not a live contradiction).
- N9 `phase2-plan.md` task 2.8 advisory A6 wrong count — the docstring at `tests/integration/test_maintenancer_spawn_resolves_tools.py:307` is ALREADY stale at base (set holds sixteen; all five count pins assert `== 16`) and becomes SEVENTEEN with `metrics-read`, not sixteen. Write seventeen or de-hardcode; the action (update same-commit) is right, the number is wrong.
- N10 research item F6 (frozen contract activation shape lists THREE members at `metrics-query-contract.md:104-106` while BE emits FOUR) has no `fold-ledger.md` §B row and no overview out-list row — invisible to the task 6.4 sweep; add both (the analogous residual `contract_RevocationLeverProse` has one).
- N11 `technical-analysis.md` §A.2 (`:190`) says `read_numeric_fact` imports "from the package", but `daemon/services/metrics_query/__init__.py` does not export it (`__all__` has `activation_block`/`effective_interval_s` only) and no Phase-1 task adds it; task 1.2's acceptance pins the DIRECT `envelope`-module import. Reconcile the design wording or add `__init__.py` to P1's file list (else an implementer following the design triggers an unplanned edit that P1's "envelope.py diff = exactly the 1-line promotion" criterion flags).
- N12 `tests/integration/test_service_tool_flag_off_byte_identical.py:814` carries another stale count ("the EIGHT-site pin") at a file the lane DOES edit; advisory A6 flags only the sibling docstring.

### Worker credit (both workers, independently verified against the tree)
- All load-bearing structural claims re-derived and HELD: declared-route census 111 / mount 37 / class-manifest 33; privileged category 16→17 across exactly 15 EDIT + 1 VERIFY-ONLY sites (5 `EXPECTED_PRIVILEGED_SET` + 5 equality asserts + 5 `frozenset` literals + 5 count pins across 10 files — exhaustive, zero missed sites); the three `api.py` provider-handle sites (`:2533/:2541/:3778`); variadic any-of `scope_required` semantics; in-memory provider read path (no psycopg/persistence/engine imports in `daemon/services/metrics_query/*.py`) with body build still off-loop per RULING-4; AmM-1 budget arithmetic (12×240>12,000 by 240; 22% not 11%); `VERSIONED_PROMPT_ARTIFACTS` meta.json exclusion; FE mirror facts (3 activation members, `showEmptyPanel`, `metrics-catalog-error`); five contract-doc line-ranges all naming `maintenance:read`; six pinned fenced blocks present with no scope string.
- Safety posture verified honest: read-only category (no write tool exists), two unbypassable gates (category + in-body `agent_id`), fail-closed 4-token ladder with authorization BEFORE substrate check, `maintenance:read` retention (no existing holder breaks), auth-matrix narrowing PROOF, D-A8 tool-vs-HTTP disclosure, empty migration delta / no new deps / no kill-switch each with lane-level criteria, four OUT exclusions routed with owners.
- Remediation is mechanical: 5 one-line text fixes + 1 manifest row for the blockers; no redesign, no scope implication.

### Next
Iteration 002 upon resubmission. Fresh workers will be dispatched with zero rejection history (independence preserved). Iteration cap: 3.

---

## Iteration 002 — 2026-11-03 — VERDICT: APPROVED (2/2 workers APPROVED) — FINAL

Workers (fresh instances, zero rejection history in prompts — independence preserved):
- approve-worker-plan-i2 = 2e4825a8-d76a-4942-85ab-c9b877631c00 → APPROVED (0 blocking, 6 notes)
- approve-worker-tech-i2 = cc98bb21-3c64-4b86-ba7a-f49dbdfa4c83 → APPROVED (0 blocking, 6 notes)

Aggregation: two duplicate pairs merged (phase-map task-count drift; entry residue-sweep exception scope) → 10 distinct non-blocking notes. Zero blocking issues from any worker → APPROVED. No downgrades or upgrades applied.

Iteration-001 blocker closure — verified by COLD-CONTEXT workers (they were not told what to look for):
- B1 (E-1 five-range propagation): worker A re-derived the E-1 enumeration across every normative copy (plan-overview P-1 / out-of-scope / R9 / P3 criterion; phase3 objective + task 3.6 + fence + success-criterion row; decisions.md Part 4; technical-analysis pointers) — superseded "4 cited" variants now confined to explicitly-marked historical blocks; worker B independently confirmed the four surviving "4 cited" hits are exactly the two marked blocks the task-1.0 sweep permits.
- B2 (AmRULING-3 trim): worker A verified the trimmed Protocol member set (GAP-1/GAP-2 + GAP-3, one-member `_Fake`) states identically in every normative working copy.
- B3 (manifest): worker B verified the plan-file manifest matches the tree 15/15 incl. architecture-recommendation.md; worker A checked every cited artifact exists in the worktree.

Notes carried forward (merged i2-N1..N10 — all non-blocking; failure modes loud, not silent):

1. [A≡B] Phase-map task-count drift — plan-overview.md:70 says Phase 1 = 7 tasks; phase1-plan.md carries 8 rows (1.0–1.7; task 1.0 entry sweep + task 1.7 venv provisioning recorded in fold-ledger §F.1). Phase file is authoritative; set the cell to 8 at (or before) the 6.4 sweep.
2. [A] plan-overview.md:42 (In-Scope item 1) says "the one-line lifespan mount" but Phase 1 task 1.4 clears the manager handle at THREE sites (api.py:2533/:2541/:3778) + lifecycle-triple pin 1.6(ii-c); summary cell understates the touch set — amend (same cell class as the iter-001 B2 entry-point residue).
3. [A] Task 3.6 VERBATIM-PRESERVATION LIST covers only the six fenced blocks; the contract-doc pack also asserts ~18 prose substrings — "checkpoint-cleanup-EXECUTE" occurs exactly once in the doc at :404, INSIDE canonical range :404-405; a rewrite dropping it trips a pack RED for a non-"no viable wording" reason. Widen the list (at minimum include that literal). Phase-5 leg 5.8(iv) enforces green-with-amendment, so the failure is loud.
4. [A] Phase 4 has no FE toolchain provisioning step — frontend/node_modules absent in the worktree; the house FE pack refuses to install deps (loud FAIL, never green skip); task 4.7 type-check/build legs need an npm ci provisioning counterpart (Phase 1's analogue is task 1.7 uv sync).
5. [A≡B] Entry residue-sweep (task 1.0) names permitted historical sites only for pairs (i) and (iii); live historical hits exist for pairs (ii)–(iv) (decisions.md:39 D-UX-6 quintet; D-UX-13 three-property text; technical-analysis.md:154-155/:1626/:190 pointer-amended design; architecture-recommendation.md:83). Add a clarifying clause (name the sites, or exclude "any hit inside a block explicitly marked superseded/historical") so the Phase-1 entry gate neither blocks nor drives edits to the frozen design.
6. [A] contract_RevocationLeverProse residual (:58-60) knowingly goes stale after the re-point (outside the five canonical ranges, guardrail (a)); disclosed in three places; recommend the ceremony text state it plainly.
7. [B] Observation-dependent counts internally inconsistent inside the binding design (§A.4 F18-band/F12:117/F9:53 figures vs §A.12 catalog F18=0/F12=17/F9=14, series sum 129, vs "112 measured"); all counts declared observation-dependent and never pinned (task 5.4 asserts shapes/vocabularies only) — add a 6.4-sweep clarification that §A.4 rationale figures are not data.
8. [B] "1800 s wedge-capture trigger" claim unsupported — real lag trigger is DEFAULT_LAG_S=5.0 sustained DEFAULT_LAG_SUSTAIN_S=15.0 (daemon/services/wedge_capture/config.py:100-101); 1800 s is the self-alarm MIN_HORIZON_SECONDS (self_alarm_service.py:77) named in the same sentence — one value stated twice. Truth-in-docs blemish; parameter choice unaffected (900 s window covers the 15 s sustain ~60×).
9. [B] Minor cite imprecision — decisions.md:211 cites the getattr at "envelope.py:64"; actual site is :70 (:64 is inside the docstring).
10. [B] Cosmetic phrasing — overview says "10 files / 16 sites" (scope item 2; R1 mitigation :108; phase2-plan.md:8/10) vs the binding "15 EDIT + 1 VERIFY-ONLY"; both true as written ("16 assertion sites" is the registry doctrine's own wording, _tool_registry.py:314-356) — harmonize at 6.4.

Worker credit (both, cold-context, independently verified against the tree): route censuses 111/37/33 live-true; 16 pin-fan-out sites verified at cited lines (5 set-constants + 5 asserts + 5 frozensets + 5 count pins, 10 files); privileged set holds exactly 16 at base; E-1 guardrail-(b) premise verified against the real contract (five canonical ranges name maintenance:read; six machine-pinned fenced blocks carry no scope string — "green by construction" holds); both stale docstring sites real; architect must-fold list fully folded; SEALED_SHA_BASELINES 35 rows / none of the 6 touched agents/ files; manager handle is a lifespan local (api.py:705) so the identity pin is feasible; deliberate authority widening disclosed with verified holder arrays.

### Final state
Status: APPROVED (iteration 002 FINAL, 2026-11-03). Plan set cleared for implementation; notes i2-N1..N10 are advisory for the implementation lane (several fold naturally at the task 6.4 sweep).
