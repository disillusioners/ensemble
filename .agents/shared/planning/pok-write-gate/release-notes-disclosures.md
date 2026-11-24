# Release-notes disclosures — POK write gate + librarian (Wave 2, Phase 12)

**Status:** DRAFT list of disclosures — the text below is what the release notes must carry when the Emperor promotes. Nothing here is ratified; the promote is the Emperor's ceremony.

**Scope:** the POK write gate (the judge + the authority split + the answer path) and the POK librarian (the metrics reader, the soft max, the ruled-bases consolidator, the board sink), as landed on `feature/pok-write-gate`. Every item below cites its in-branch anchor; where an item is NOT yet backed by an in-branch artifact, it says so explicitly and is flagged as a gap rather than filled in.

**Fence state at authoring (2026-11-24):** branch-local, unpushed, not promoted, no live-store write, no live config change. The disclosures that say "until the promote" are literally true at authoring time — the branch's own removal commits are not in the live install.

---

## (a) The kb-writer dual-grant is STILL LIVE until the promote

**Disclosure:** the `pok` grant was removed from `kb-writer` **on this branch only**. The **LIVE install still grants `kb-writer` BOTH `rag_insert_text` AND `pok`**, so the dual-write mechanism that produced the measured kb-writer skew is still live in production until the Emperor's promote lands. Any post-promote measurement of the kb-writer family will see the effect stop — not because the data was cleaned, but because the grant is gone.

**Measured basis (in-branch, committed):**

| Fact | Value | Source |
|---|---|---|
| The dual grant was added | `16b4e70b2` (2026-11-07 08:27:33Z) — "grant the `pok` category to all 45 agents" | git history; `evidence-rederiv-2026-11-24/kb-writer-trio.md` |
| How long it stood | **16d 22h 23m 18s** | `evidence-rederiv-2026-11-24/kb-writer-trio.md` |
| Population coverage | **0 of kb-writer's 138 active POK rows predate the dual grant** ⇒ the grant was in force for 100% of the population | same artifact, re-derived from the 562-row export |
| The removal | `fd7dc6480` — "Phase 4 — kb-writer grant removal + compensating capture duty" | git history |
| The removal's reach | **branch-local / unpushed** ⇒ the live install is unchanged | the fold ledger row `plan-overview.md:380` (the T12.2 hard-gate fold) + `phase13-plan.md:12` (T13.4) |
| The anomaly id | **`7c9763ca`** (named in-branch as the live dual-grant anomaly) | `plan-overview.md:319` / `:327` / `:380`, `phase12-plan.md:10` |

**Anchors:** `phase13-plan.md:12` (T13.4 — the release-notes task this file feeds), `plan-overview.md:327` (T13.4), `plan-overview.md:380` (the T12.2 hard-gate fold row), `phase12-plan.md:10` (the mechanism + the live-dual-grant statement).

---

## (b) The feeder assessment — experiencer + kb-importer (the Emperor decides)

**Disclosure:** the POK write gate's capture duty now sits with the **operational agents** (`ari`, `project-manager`, `leader`, `devops`). Two OTHER bulk knowledge writers — `experiencer` and `kb-importer` — still hold the `pok` grant **and** exercise it almost never. The assessment was carried; **NO grant change was made**; the decision is the Emperor's (parked follow-up **P3** — "Feeder grant changes (experiencer/kb-importer) — Emperor decides on assessment (Phase 9)").

**Assessment (measured, `feeder-assessment-2026-11-24.md`, committed in-branch):**

- **The grant posture is the same shape the kb-writer rider removed:** each feeder holds the `pok` grant while **none of its prompt files mentions POK even once** (0 files) — a grant nobody exercises by instruction.
- **The write volume is long-tail:** in the 499-active census @2026-11-23 12:29Z, `kb-importer` = **1** active row and `experiencer` = **0** active rows, against `kb-writer`'s **131**.
- **The two cases differ:** `kb-importer` demonstrably DOES write (4 day-rows of `pok_upsert` in the 3-day telemetry window) — it is a low-volume writer, not a non-writer; `experiencer` shows NO POK write at all in the measured window.
- **RECOMMENDATION (the Emperor decides):** remove the `pok` grant from BOTH feeders, mirroring the kb-writer rider — but treat them differently: `experiencer` is the clean case (zero operational loss); `kb-importer` needs a look at whether its few POK writes are wanted. The change, if ordered, rides the same rail as the kb-writer rider: the grant census pin (`tests/unit/test_pok_grant_pins.py`, 53 explicit grants) + a resolved-tool-set re-pin + the capture/routing guidance in the operational agents' prompts.

**Anchors:** `feeder-assessment-2026-11-24.md` §1 (grant posture), §2.1 (write volume), §5 (assessment), §6 (recommendation, quoted above); `design-amendments-v2.md` §10 (the assess-and-report mandate) + §13.9 item 3 (the parked decision).

---

## (c) The approver-cap disposition — cited VERBATIM from the fold ledger

**Disclosure:** the plan set's approver gate was exhausted at **3 rejections** and the disposition was a **leader** ruling, not an approver approval. The release notes must carry that the build proceeded under a leader disposition with a reviewer spot-check, not under a clean approver pass.

**The disposition row — `plan-overview.md:378` (the fold ledger, "leader disposition fold 2026-11-24"), VERBATIM:**

> | **leader disposition fold 2026-11-24** | **Approver cap exhausted** — 3×REJECT, iteration-003 REJECTED-FINAL (`approver-pok-write-gate.md:157`, max iterations reached, escalated to Leader). All three blockers are mechanical propagation-cousin residue (fold commit `6db44aa3c` touched 9 files, not phase1–7): (1) withdrawn transposed precedence predicate at `phase3-plan.md:11`; (2) withdrawn self-inconsistent sweep breakdown at `phase3-plan.md:14`; (3) live same-task contradiction on the parser fail-closed path (`plan-overview.md:246` vs `phase7-plan.md:11`). iter-002 primaries verified fixed this round (B-1 anchors, B-2 pin at primary sites, B-3 rider, S-1 gate, S-2 stamps); spine + phases 8–13 APPROVED (0 blockers each). |
>
> | FOLDED — **LEADER DISPOSITION: proceed to build under TrueAuto, with a reviewer spot-check closing the three disposition sites; disclosure rides the release notes.** Executed: three disposition-site edits (byte-identical canonical string; content-verified breakdown + count-named acceptance; D2-aligned parser cell); ATTESTATION scope extended to cover the phase-file row char-for-char across BOTH surfaces; zero-variant cousin sweep over the WHOLE planning set (commands + per-class hit counts in the fold report); cheap notes — T0.5 orphan merged + Phase-0 three-way counts restated 5/5/5, K=48 restated as the ≤8000-char bound at SC#20/T7.9/phase7 rows 9+exit with a dated provenance pointer at `design-amendments-v2.md:163` (`architecture-recommendation.md:105` untouched, HISTORICAL). **INSTITUTIONALIZED RULE: any future canonical-string fold MUST grep ALL planning files for variants (spacing, wording, transposition), not only the sites a reviewer names — cures landing at the primary site only is the named defect class.** |

**Anchors:** `plan-overview.md:378` (the row above); `approver-pok-write-gate.md:157` (iteration-003 REJECTED-FINAL); `plan-review-4.md` (the reviewer spot-check that closed the three disposition sites — see (f1)).

---

## (d) The judge digest's K=48 is OBSERVED SIZING, not a constant — the ≤8000-char bound governs

**Disclosure:** the gate's judge digest is bounded by **≤ 8000 chars TOTAL**. **K=48 is the observed sizing WITHIN that bound (≈7250 chars observed), NOT a pinned constant and NOT a target.** The architect's historical wording ("R4 digest K 32 → 48") is retained as PROVENANCE only; the operative acceptance wording is the bound.

**Verbatim (the dated pointer, `design-amendments-v2.md:163`):**

> **[DATED POINTER — leader disposition fold 2026-11-24: this R4 text is RETAINED AS PROVENANCE/HISTORY, not the operative acceptance wording. The ≤8000-char budget is the binding constraint; K=48 is the observed sizing within it, NOT a pinned constant. The operative sites (plan-overview SC#20 + T7.9; phase7-plan.md rows 9/exit) now restate the bound. architecture-recommendation.md:105 is left untouched as the HISTORICAL architect record.]**

**Operative sites (all bound-carrying):** `plan-overview.md:125` (SC#20), `plan-overview.md:254` (T7.9), `phase7-plan.md:17` (row 9) and `:35` (the exit criterion). The architect document stays byte-frozen and is marked from outside.

**Disclose-only nit (advisory A-2, see (f1)):** the asserted `≈7250` constant (126×48 = 6048) still appears at `design-amendments-v2.md:163` / `architecture-recommendation.md:105` / SC#20's threshold — the bound governs everywhere and SC#20 labels the figure `observed, not a target`.

---

## (e) The `merge_policy` THIRD settings key — three-key form, both plan-named keys OFF at build

**Disclosure:** the gate's switch family ships with **THREE** DB-backed settings keys, not the two the task map names:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `pok_gate.semantic_enforcement` | bool | **OFF** | the SEMANTIC judge gates writes (ON ⇒ the semantic verdict decides; OFF ⇒ shadow) |
| `pok_gate.softmax_tightening` | bool | **OFF** | the librarian's near-soft-max tightening changes admission outcomes |
| `pok_gate.merge_policy` | enum | `ASK_WITH_PROPOSAL` | the F-fork's merge dial: `ASK_WITH_PROPOSAL` / `AUTO` / `ASK_NO_PROPOSAL` |

The third key is **kept per the architect's F-fork** — the merge policy sits "on the same switch family as enforcement" so the P1 ceremony moves the dial instead of a rebuild. **Both plan-named keys are OFF at build**; the build makes NO live config change. All three are **DB-backed** (`settings:write`), with **no env tier** — an env flip would be a live config change at deploy + restart.

**Anchors:** `daemon/services/settings_service.py:781` / `:800` / `:817` (the three `ManagedKeySpec`s); `design-amendments-v2.md:146` (the F-fork pick — "`merge_policy` enum ∈ {`ASK_WITH_PROPOSAL` (default), `AUTO`, `ASK_NO_PROPOSAL`} on the same switch family as enforcement — P1 ceremony moves the dial, no rebuild"); `design-amendments-v2.md:166` (the two-sibling-keys requirement and the no-env-tier rule); `plan-overview.md:271` (T8.11, the two plan-named keys) + `design-amendments-v2.md:212` (the parked P6 decision — `merge_policy`'s default at P1 is decided by shadow evidence, not by the build).

---

## (f) The reviewer spot-check advisories

### (f1) The PLANNING-gate spot-check advisories — IN BRANCH, cited verbatim

**Disclosure:** the planning gate closed **UNCONDITIONALLY-CLOSE** with **0 CRITICAL · 0 WARNING · 5 ADVISORY** ("disclosure/nits, fold as one-liners, none gates the close"). Per the leader disposition, **disclosure rides the release notes** — these five advisories are that disclosure. Verbatim from `plan-review-4.md` ("Advisories (disclosed; fold as one-liners; none gates)"):

- **A-1** `plan-overview.md:342` — append the K=48 mention census: `(in-set mentions: 9 — 7 operative + 1 dated pointer + 1 historical; all bound-carrying)`.
- **A-2** The asserted `≈7250` constant (126×48 = 6048) still appears at `design-amendments-v2.md:163` / `architecture-recommendation.md:105` / SC#20 threshold — bound governs everywhere and SC#20 labels it `observed, not a target`; disclosure-only. One-liner: note it in the `:163` dated pointer.
- **A-3** Phase-0 map IDs are content-matched, not positional (fully enumerated at `:240`, but outside the task-map table). One-liner: repeat `(Phase-0 IDs are content-matched, not positional: T0.5↔phase-0 row 2)` at the map header.
- **A-4** The merged T0.5 subject's literal tokens survive only in the approver artifact; T1.1 readers can't see the absorption. One-liner: append `(absorbs the former T0.5 ledger-additive-table feasibility probe)` to `plan-overview.md:193`.
- **A-5** `architecture-recommendation.md:105` has no IN-PLACE historical marker (byte-frozen by design; marked from outside at `:163`). Acceptable as-is; add the in-place marker only if a future fold un-freezes the doc.

**Anchors:** `plan-review-4.md` (the spot-check record — verdict, the 7 asks, the advisories); `plan-overview.md:378` (the disposition row that names the spot-check as the closing act).

### (f2) The WAVE-2 CODE review's advisories — PENDING, NOT YET COMMITTED

- **Fixed in-branch (the Phase-12 addendum round, commit `7d37d4888`):** C-1 — the R-RET re-slug leg was not count-neutral (a confirmed rename left the mis-slugged active row behind and added a second row) — and W-1 — the `merge_policy` dial never read its DB-backed settings row — are both FIXED, so item (e)'s "the P1 ceremony moves the dial" is now literally true; the review's five advisories ride this (f2) item (the leader splices them verbatim at Phase-13 from `wave2-code-review-1.md`, on disk, untracked, the reviewing lane's artifact — referenced, never committed or edited here).

**Gap — flagged, not filled.** The Wave-2 **code** review (the review of the landed Wave-2 code range, through `942055e22`) is **not committed in this branch** — the only COMMITTED code-review artifact is `wave1-code-review-1.md` (the WAVE-1 review). *(The wave-2 review artifact was observed ON DISK but UNTRACKED during the Phase-12 fix round, 2026-11-24 ~14:2xZ — `wave2-code-review-1.md`, verdict APPROVE-WITH-ADVISORIES carrying 1 latent CRITICAL (enforcement-gated) · 1 WARNING · 5 ADVISORIES. It is not committed, so it is not yet an in-branch source of record.)* Its advisories are therefore **NOT reproduced here**: inventing them would put words in the reviewer's mouth, and the plan's rule is that a relayed figure or claim without a source is a defect class of its own.

**What the release-notes author must do:** splice the Wave-2 code review's advisories VERBATIM at Phase-13 authoring, under this section, when the artifact is committed. **Do not paraphrase them** — the review's own evidence standard is byte-level mechanical comparison, and a paraphrase would drop exactly the detail that makes the advisory checkable.

**Reference (for the splicer):** the Wave-2 code review's subject range — the gate core (`3f35f9d76`, `96a14de54`, `97f269ea4`), the librarian (`bf23c8c10` … `942055e22`) and the drift-sync merge (`9cf64bacd`). This lane's own commits (`aee27f358` — the query-side wiring + the board sink; `e14001121` — the guideline amendment) **postdate** that range and are outside it.

---

## (g) The shadow-first posture — what is enforced NOW and what is not

**Disclosure:** the write gate ships with its **mechanical** layer enforced and its **semantic** layer DARK. An operator reading a green `pok_upsert` must know exactly what that green does and does not prove.

**Enforced NOW (mechanical, no LLM):**

- **`reason` is REQUIRED** — a mandatory parameter carrying the caller's OPERATIONAL CLAIM; a missing or empty value is the typed refusal `pok_reason_required`.
- **R0 — the named content fence** (one paragraph, ≤240 escaped chars) is checked at the tool layer.
- The rest of the write path's form checks (slug regex, escaped body budget, family cap, active-entry cap) are unchanged.
- The refusal precedence is load-bearing: **store → scope → reason → fence/budget/family** — a degraded store or a bad scope always wins over a missing reason.

**NOT enforced (semantic, shadow):** R1 (operational), R2 (rolling), R3 (advisory-only), R4 (already-in-RAG), R5 (no-home escape), plus the R-RET advisory leg and the R-PROV protective clause. These **log their verdicts to the write-gate ledger and refuse NO write** while `pok_gate.semantic_enforcement` is OFF. The librarian's near-soft-max **tightening** rides its own sibling switch (`pok_gate.softmax_tightening`, also OFF at build) — the soft max is **computed and drift-reported** from day one, but it does not change admission outcomes until that switch flips.

**The flip is a ceremony, not a build step.** The enforcement wave is **parked before the next curation sweep**; the shadow verdict mix is the evidence the Emperor reads; the flip happens at the P1 ceremony via the operator route (DB-backed `settings:write`, no env flip, no restart).

**Fail-closed where it matters:** a gate that cannot judge returns the typed `pok_gate_unavailable` and **no write** — it never falls open into an accept.

**Anchors:** `design-amendments-v2.md` §2 (`reason` = REQUIRED), §3 (the shadow/enforcement split — "MECHANICAL — enforce NOW: `reason` REQUIRED + R0 named fence. SEMANTIC — SHADOW default OFF"), §13.3 item 5 (the switch family, no env tier), §13.9 item 2 (the parked `merge_policy`-at-P1 decision); `daemon/tools/pok.py:1340` (the `pok_reason_required` refusal + the precedence comment at `:1332`); `daemon/services/settings_service.py:781` / `:800` (the two OFF keys); `daemon/services/pok_gate.py` (the rule set + the fail-closed discriminator).
