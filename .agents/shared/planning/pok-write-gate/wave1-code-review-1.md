# Wave-1 Code Review — POK Write Gate (agents-ensemble)

**VERDICT: APPROVE-WITH-ADVISORIES** — 0 CRITICAL · 1 WARNING · 10 ADVISORIES

| Field | Value |
|---|---|
| Date | 2026-11-24 (UTC) |
| Reviewer | Reviewer v2 (controller/dispatcher — Deep-Review council; the controller did not analyze code directly) |
| Mode | 🔴 Deep-Review — triggers: Data Integrity (ledger migration, carry-over), Security/Permissions (tool-layer gate, kb-writer grant removal), Cross-Cutting (tool + repo + service + 5 agent configs + test packs; 57 files, +8166/−61) |
| Sessions | Governor `221ff3b6-cc0d-4a5a-9bbe-583f04cb6edd`; councilors `13c1703d`, `36826da4` (agent `worker`, skill `code-review`, 2 distinct canonical models — dedup caps one per model). Controller follow-up (commission 2a) via governor terminal-revive, held evidence only. |
| Target | Worktree `/home/nea/ensemble-src-wt-pok-write-gate`, branch `feature/pok-write-gate` @ `74f4245c0`, merge-base == origin/latest == `bcebe7725`; porcelain CLEAN at review time; remote branch ABSENT (unpushed — re-verified by controller via `ls-remote`). |
| Plan of record | `.agents/shared/planning/pok-write-gate/` — plan-overview.md + phase1–13 plans + design-amendments-v2.md; architecture-recommendation.md binding; gate-rules-r6-r7-amendment.md + approver-pok-write-gate.md as adjudication record. |

This file is the record of authority for the Wave-1 verdict (commissioned instruction: a verdict that exists only as a message is one retention sweep from lost).

## Scope

**In-scope implementation commits (11):**

| Commit | Content |
|---|---|
| `7a5d4b5b2` | Phase-1 ledger substrate + mandatory archive basis |
| `ea9e4dff5` | T2.7 counter carry-over across supersede |
| `8d5c1e39e` | T2.8 mutation evidence (carry-over RED confirmed) |
| `f528a8fbf` | Phase-3 mechanical gate — reason REQUIRED + R0 named fence |
| `63ff5722f` | Stray `pok_upsert` invoke sweep in curate-basis helper |
| `fd7dc6480` | Phase-4 kb-writer grant removal + compensating capture duty |
| `5f4b60014` | Phase-5 metrics foundation (record_verification design seam) + standing-defect residual rows |
| `d76510eff` | plan-review-4.md docs (planning-gate close record) |
| `4234d2b82` | Phase-6 pack registration + mutation artifacts |
| `09b261b28` | T3.9 mutation evidence (service-signature defaults RED confirmed) |
| `74f4245c0` | Foreign-row hygiene 263→269 + sub-count correction |

**Out of scope:** merge `8a0a4a702` foreign content (the parallel lane's family-count strict fix) — verified only for non-revert/non-alteration: **byte-identity proven** (30-line extraction diff empty between `bcebe7725` and HEAD). Planning-dir files were review context, not targets.

## Method & provenance

Two councilors dispatched identically with the read-only directive; both reports completion-grade (concrete file:line anchors, explicit verified-vs-unverified splits, no `[REPORT SANITY]` markers). **Read-only compliance: held** — sanctioned write-free verification carve-out only (worktree venv, scratch sqlite, dead-port probe, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`), porcelain re-verified clean afterwards; no writes, commits, pushes, or inter-instance actions. No refinement round was needed: **zero factual contradictions** between the two reports; the single divergence was a severity label (adjudicated under W-1). The controller's follow-up closed commission leg 2a from held evidence (no new council).

## High-confidence convergence (both councilors, independent)

1. **Merge `8a0a4a702` foreign content intact** — read-verified (coding) + byte-identity proven (agentic).
2. **No agent-reachable bypass** — sole production `POKService.upsert` caller is the gated `daemon/tools/pok.py:276`; nothing in routers/MCP/job paths reaches the ungated service function.
3. **Migration additive-only, single new `.sql`** — `daemon/migrations/versions/20260724…` correction: `20261124_060000_create_pok_write_gate_ledger.sql`, `IF NOT EXISTS` throughout; DOWN scoped to the ledger + its 5 indexes; `project_operating_knowledge`/embeddings untouched; model↔.sql parity pinned by real `create_all`+inspector tests.
4. **Carry-over uses the persisted prior row** (not a caller snapshot) with additive counters — stale-snapshot zeroing hazard defended in-code.
5. **Typed-envelope vocabulary exact** — precedence `store→scope→reason→fence/budget/family` byte-for-byte; `pok_reason_required` correctly placed; R0 label on every fence refusal (only stale-variant grep hit was a compiled `.pyc` — false-positive class).
6. **Grant-removal rails** — kb-writer `meta.json` pok removed; `test_pok_grant_pins.py` 54→53 + declared exception + absence pin + mutation leg; `tool-categories.json` untouched (absent from the 57-file diff); baseline changed exactly 4 rows, re-hashed byte-for-byte.
7. **Capture duty exactly-4** (ari, project-manager, leader, devops), routing-contract wording conformant; no fifth agent; kb-writer has zero POK mentions.
8. **Fences clean** — no push; planning-dir add-only (36 added / 0 modified; `d76510eff` lands plan-review-4.md as a new file per the in-branch-docs fence); canonical checkout dirt decomposes to known baseline + other-lane state, no lane leakage.
9. **Test packs reproduce independently** — 162/162 (both councilors re-ran: 2.55s / 2.98s, 0 skipped); 269/269 house pack (agentic re-ran: 4.89s). The 263→269 arithmetic re-derived: the old breakdown summed to **260** vs its stated 263 (pre-existing stale `injection 42`; real 45 at both shas); the new sums to exactly 269; the +6 delta = exactly the 6 `TestPhase3MechanicalGate` tests. Correction disclosed inline with both measurements — honest.
10. **T3.9 non-vacuity** — static proof: 97 `.upsert(` occurrences / 0 `reason=` kwargs in the pack file; keyword-only params at `pok_service.py:333-334` guarantee the claimed `TypeError` at every site when defaults drop; 72 RED = 58 service + 14 tool sites (sites ≠ nodes — the evidence's distinction is correct).

## Findings

### 🔴 CRITICAL
None.

### 🟡 WARNING (1)

**W-1 — Plan gap (not an implementation defect): dead retrieval-metric chain.**
`on_retrieval` hook + `make_retrieval_recorder` landed with **zero production callers** — both `admit_view` call sites omit it. `phase10-plan.md:16` asserts "ledger retrieval counts supersede access_count once Phase 1 lands," yet no phase 1–13 task row owns the wiring → the metric chain stays dead code and the future librarian reads zeros.
- Where: `daemon/services/pok_injection.py:323,626-681`; call sites `daemon/services/context_messages.py:790`, `daemon/services/pok_ari_seam.py:163`; owning commit `7a5d4b5b2`.
- Fix: add a Wave-2 task row wiring `on_retrieval=make_retrieval_recorder(repo, project_id, agent_id=…, source="injection")` at both call sites — or record an explicit stay-dark-until-librarian decision in the plan.
- Found by BOTH councilors (coding: 🟢; agentic: 🟡). Severity adjudicated to 🟡 — the fact is consensual; plan text creates a live obligation with a real consequence (asserted supersession vs dead metric chain). Fold into Wave-2 planning **before Wave 2 starts**.

### 🟢 ADVISORIES (10; finder: A=agentic, C=coding, A+C=both)

1. **[C]** `reason`/`gate_fn` not forwarded from tool to `POKService.upsert` — seams inert in Wave 1 (`daemon/tools/pok.py:276` vs `pok_service.py:333-334`). Plan-conformant (semantic judge shadow OFF); wire in Wave 2 when the switch arms.
2. **[A+C]** Ledger appender idempotency is application-level only — read-then-append, no DB uniqueness; concurrent callers can duplicate gate-chain rows (`daemon/repositories/pok/repository.py:498,640-675`). Wave 2: partial unique index `(project_id, gate_id, phase) WHERE gate_id IS NOT NULL`, or document the single-writer discipline.
3. **[A+C]** Curate-path archive `basis` validated + echoed but persisted only in the INFO log — no ledger row/column (`daemon/tools/pok.py:503-561`). W1-conformant (T1.6 = tool-layer validation); Wave 2: curate-path ledger row or an explicit gate-events-only decision.
4. **[C]** `record_verification` zero callers — by design (T5.1); first real writer is Wave-2 Phase 7/8 verdicts (`repository.py:683`). Disclosed.
5. **[C+A]** Ledger fields store caller-supplied text verbatim, no redaction layer (`daemon/repositories/pok/models.py`). W1 write paths capture only ids/slugs/dispositions and the R0 fence blocks fence-triggering content — **W1 is clean**; posture note: document that `reason`/`proposed_body` must not carry secrets when Wave-2 bodies land.
6. **[A]** `POK_LEDGER_SOURCES` defined but missing from `__all__` (`models.py:227`). Add it. (`7a5d4b5b2`)
7. **[A]** `standing_defect_rows` loads all ledger rows then filters by prefix in Python (`repository.py:612-622`). Push the `LIKE 'standing-defect:%'` predicate into SQL when the ledger grows. (`5f4b60014`)
8. **[A]** `supersede_and_insert` re-reads `db_prior` without row lock — concurrent `record_verification` between get and commit is lost (`repository.py:351-405`). `SELECT … FOR UPDATE` on the PG path if contention appears. (`ea9e4dff5`)
9. **[A]** Dual-home drift risk: trusted/ignored metric set lives in docstrings now; Phase-12 will restate it (`repository.py` docstrings vs design-amendments-v2 §9). The Phase-12 fold must cite-or-supersede the docstrings explicitly (plan-residue sweep rule). (`5f4b60014`)
10. **[A — commission 2a addendum]** Same-txn vs post-commit for execution/verdict ledger rows must be an **EXPLICIT** Wave-2 decision (Phase 7/8). Deciding implicitly (best-effort by omission) risks crash-window audit undercount; the opposite extreme couples write latency/rollback to the audit append. See addendum below.

### Addendum — Commission 2a: Ledger-row transactionality (same-txn vs best-effort)

**1. What the PLAN OF RECORD requires**
- `phase1-plan.md`, task 4 (T1.4) acceptance, verbatim: **"W1 half: schema + repo accept the 3-row chain shape… W2 half: rows actually written."** → Wave 1 requires **schema-representability only**; admission/verdict/execution chain rows are the W2 half.
- `design-amendments-v2` **§4.9**: retrieval capture is **never-raise best-effort** — the discipline the code itself cites.
- Net plan requirement: **NO same-transaction ledger semantics in Wave 1.** The same-txn-vs-post-commit decision is explicitly a Wave-2 obligation (when execution/verdict rows land, Phase 7/8).

**2. What the CODE does**
- **Gated write path:** tool layer `daemon/tools/pok.py:276` (sole production `POKService.upsert` caller, reason-gated) → `pok_service.py` contains **zero `append_ledger` calls**. ⇒ **No ledger append shares the gated write's transaction in W1 — by construction.** The transactionality question is vacuous on the runtime write path, which is exactly the plan's W1 shape.
- **Actual W1 ledger writers (both decoupled from the gated write):**
  - `append_ledger` (`daemon/repositories/pok/repository.py:498`) — bare single INSERT; reached only by `record_standing_defects` (`repository.py:640-675`, read-then-append app-level idempotency) — a standalone single-writer setup path for the Phase-5 standing-defect residual rows (`5f4b60014`), not a per-gated-write runtime path.
  - Retrieval recorder seam (`make_retrieval_recorder` → `append_ledger`) — **not wired** into either live `admit_view` call site (`context_messages.py:790`, `pok_ari_seam.py:163`) (= W-1); where exercised (unit tests), failure semantics are never-raise best-effort with **double containment**: hook-site try/except + per-row recorder try/except, per §4.9.
- **Failure coupling:** none exists at runtime in W1. No production sequence places a ledger append after a successful gated write; on the only runtime-shaped append path (retrieval recorder), exceptions are swallowed-and-continued at two levels and can never fail an injection/read. `record_standing_defects` failures propagate only to its own standalone caller.
- **Not pinned line-level by either councilor** (immaterial to the W1 verdict): whether `append_ledger` commits its own session vs riding the caller's session — immaterial because no production call site couples it to a gated write. Flagged as an input to the W2 decision.

**3. Verdict**
**CONFORMS — yes.** The plan requires schema-only in W1; the code writes no ledger row on the gated path and implements the capture seam as never-raise best-effort per §4.9. Residual severity: 🟢 ADVISORY (= Advisory 10 above).

*Evidence provenance: councilor-agentic report §2a + verdict-drivers (1d); councilor-coding findings #1/#3/#4. Folded from held evidence; no new reads, writes, or re-runs. Load-bearing claims (`zero append_ledger calls in pok_service.py`; §4.9 double-containment wording) come from councilor-agentic's full-file reads, independently corroborated by councilor-coding for the bare-INSERT shape and the unwired recorder; the single inference-flagged fact is the `append_ledger` session-commit granularity, disclosed above.*

## Deviations D-1..D-4 (adjudicated; both councilors concur)

| Dev. | Adjudication | Grounds |
|---|---|---|
| D-1 docstring-homed notes | **Equivalent-with-disclosure** | T5.2/T5.3/T5.5 File cells target Phase-12 artifacts that don't exist yet; a diff-reviewable home was required now; content matches design-amendments-v2 §9; no single-ownership violation. Residual = Advisory 9. |
| D-2 3-table fixture | **Equivalent-with-disclosure** | The "third table" is the ledger itself — unnamed by the pre-ledger plan wording; acceptance ("no live write") fully intact: in-memory sqlite + `create_all` only; fence discriminating power re-proven by the dead-port run. |
| D-3 no duplicate Phase-6 file | **Not a violation** | T6.1's File cell is generic; a per-phase-named duplicate would itself have been residue. |
| D-4 T3.9 bound via overview row | **Equivalent-with-disclosure** | plan-overview T3.9 + phase3-plan row 9 name and bind the leg to the same pack row; landed at `09b261b28` post-registration; Phase-3 exit criterion satisfied at HEAD; non-vacuity proven. |

Caveat (disclosed by the council): D-1..D-4 were adjudicated from the controller's commission descriptions — the developer's deviation report is not in-tree; the plan text cited covers the described shapes and nothing in-tree contradicts them.

## Evidence boundary (verified vs unverified — no silent gaps)

- **Independently re-executed:** 162-node pack (both councilors); 269-node house pack (agentic); premises HEAD/base/porcelain/remote-absent/57-file diffstat (both); prompt-baseline `--check` 217 artifacts (coding) **plus** byte-for-byte re-hash of the 4 changed rows (agentic).
- **Statically verified:** migration additive-only + single `.sql` + parity tests; typed-envelope precedence + no stale variant; bypass hunt; grant rails; capture-duty content; carry-over; merge foreign-content byte-identity; 263→269 arithmetic; node-count reconciliation; planning-dir add-only; canonical-checkout leak decomposition; T3.9 trigger conditions (97 sites / 0 `reason=` / kw-only ⇒ TypeError).
- **Resting on recorded evidence (disclosed):** the six mutation RED **executions** were not re-executed by either councilor — trigger conditions were proven in-tree and all GREEN baselines reproduced exactly; the six `evidence-pok-write-gate-w1/mutation-*.md` artifacts carry reproducible procedures, not badges alone. Not re-derived line-by-line: the T3.6 class split (immaterial — both packs green).
- **Inference-flagged:** `append_ledger` session-commit granularity (see addendum; verdict-neutral).
- **Provenance caveat:** D-1..D-4 adjudications inherit the controller-description caveat above.

## Recommendations

1. **Fold W-1 into the Wave-2 plan before Wave 2 starts** — one task row (or an explicit stay-dark decision). The only item with plan-level consequence.
2. Track Advisories 1–3 as Wave-2 wiring prerequisites (they are the seams Wave 2 exists to close); make the Advisory-10 same-txn-vs-post-commit choice an explicit Wave-2 decision record; Advisories 4–9 as backlog hygiene.
3. Confidence: **2/2 models, normal path — no degraded-confidence notice.** Residual risk surface is narrow and named (mutation RED executions on recorded evidence, statically corroborated; D-1..D-4 caveat).

— Landed by the Reviewer controller (v2) on the commissioning instruction. Council closed cleanly: both councilors terminal, parent-error flag cleared and verified, manifest removed.
