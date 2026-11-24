# Design Amendments v2 — POK Write Gate + POK Librarian (authoritative delta doc)

Date: 2026-11-23 (late revision)
Author: planner[v2] via plan-creation worker
Status: Authoritative delta — folds leader addendum-2 + addendum-3 (Consolidated Emperor Ruling Set) into the plan-of-record.
Supersedes: technical-analysis.md R1–R6 defaults where contradicted; addendum-1; addendum-2 where addendum-3 reverses it.
Fences: planning docs only; no code, no DB/LLM contact; worktree only; no promote.

## 0. What this doc is

Single authoritative statement of the post-fold contract. `plan-overview.md` and `phaseN-plan.md` CONSUME it. Where an earlier artifact conflicts, THIS doc wins. Each section names what it supersedes and carries a grep-able distinctive phrase. Two addenda are folded here:
- **addendum-2** (leader via Ari, job 71f7c48d): gate = judge-not-filter, R0–R5, shadow-first, ledger, family-count fix, rider retraction, standing-defect retire-with-disclosure. *(Rider 2026-11-24: §1 extended by `R-RET`/`R-PROV` — the fold of census R6/R7; see `gate-rules-r6-r7-amendment.md`. The historical addendum-2 wording is preserved here as provenance.)*
- **addendum-3** (leader, board wi-48cf7f9c, ~14:20/14:22Z): Consolidated Emperor Ruling Set — REVERSES the rider retraction (grant removal back on), expands the gate to a POK-librarian maintenance agent (ACCEPT/MERGE/ASK/REJECT), binds the authority split, adds the ASK tool contract, routes an assess-and-report task, and adds the metrics + per-project soft/smart max subsystem.

## 1. Gate contract — R0–R5 + folded R-RET/R-PROV (near-verbatim from addendum-2; stands per addendum-3 item 2; rider 2026-11-24)

The gate is a **JUDGE / POK-LIBRARIAN MAINTENANCE AGENT, not a filter**. It emits a typed verdict + family + explanation + alternative. Judge input = caller's `reason`, treated as an **OPERATIONAL CLAIM** — the primary judge input. Judge question: *"what breaks in THIS repo if we drop this?"*

| Rule | Near-verbatim message / test | Enforcement class |
|---|---|---|
| **R0 — format** | Mechanical, existing fence, named. Body passes the content fence (one paragraph, ≤240 escaped chars, no leading `#`/`[SYSTEM CONTEXT:`/backticks). | **MECHANICAL — enforce NOW** |
| **R1 — operational** | *"Does the body still teach the same lesson in a repo with no ensemble?"* — NEVER *"is this general?"* | SEMANTIC — shadow |
| **R2 — rolling** | *"Does the rule survive the date/version it cites?"* | SEMANTIC — shadow |
| **R3 — no-handle** | Advisory flag ONLY — **never a rejection basis**. | SEMANTIC — shadow (advisory) |
| **R4 — already-in-RAG** | Never fires on operational rows. **R1 beats R4.** A RAG twin is NOT a rejection basis. | SEMANTIC — shadow |
| **R5 — no-home escape** | Every rejection must name a valid alternative home; a rejection with no home is itself a defect. | SEMANTIC — shadow |
| **R-RET — retrievability** (rider; census R6) | *"Would someone searching with the body's own operative terms find this row by its slug — does the slug share vocabulary with the terms the body actually teaches?"* Lexical slug↔body overlap is ADVISORY-only (no new mechanical pre-filter). NEW write → ASK-with-rename-proposal (never auto-reject); librarian use = detection-into-ruled-bases only. No new verdict/family token. | SEMANTIC — shadow (advisory lexical leg) |
| **R-PROV — provenance-vs-precedence** (rider; census R7) | *"Is the date/SHA EVIDENCE FOR an operational claim, or IS the claim itself 'as of date X'/'at commit SHA Y'?"* A date/SHA admissible as evidence does not make the row rolling — the row survives if the lesson survives the date. Admit-side PROTECTIVE clause, NOT a rejection ground. | SEMANTIC — shadow (admit-side protective) |

**Precedence (binding):** R1 beats R4. **R-PROV beats a rolling rejection the way R1 beats R4** (date-as-evidence admits; protects the 63-row C1 durable-with-provenance mass). R-RET never auto-rejects on lexical overlap alone. R3 advisory-only, never rejects. R0 + `reason` presence are the only rules with immediate enforcement force.

**Confidence / ASK (addendum-3 generalization):** confidence ≤2/3 → **ASK**, never auto-reject. Low-confidence negatives request caller confirmation instead of rejecting. This generalizes addendum-2's "≤2/3 → review-not-reject."

**Rejections routable** (knowledge→`experience()`/RAG; work→board; incident→critical note; rolling→release tree/KV). `pok_curate` stays curator-authority (leader/PM) and may cite a gate rejection as archive basis. Canonical rejection trace (Emperor example, verbatim): *"the information not belong to POK, please use rag tool if it exist for you"*.

## 2. `reason` = REQUIRED (mechanical, enforce NOW)

- `pok_upsert` gains a **required** `reason: str` (no default). Missing/empty → typed `pok_reason_required`. `reason` = OPERATIONAL CLAIM, judge's primary input.
- **DIVERGENCE B (leader vs architect):** leader = REQUIRED now (Wave 1); architect v2 §5 Wave 2 = soft-required. **Plan follows leader.** Flag architect.

## 3. Shadow / enforcement split (addendum-3 item 6 — CONFIRMED, do not re-plan)

- **MECHANICAL — enforce NOW:** `reason` REQUIRED + R0 named fence. No LLM.
- **SEMANTIC — SHADOW default OFF:** R1/R2/R4/R5 judge logs verdicts to the ledger, refuses no write. Release exposes verdict + enforcement switch. Rider 2026-11-24 adds R-RET (advisory lexical leg) and R-PROV (admit-side protective) to this same semantic shadow class — both ride the `pok_gate.semantic_enforcement` sibling switch; neither is mechanical-now.
- **Enforcement switch:** daemon-settings surface (`settings:write`, DB-backed), default OFF, NOT an agent tunable (Decision-Module-P1 kill-channel precedent).
- **Enforcement wave parked P1** — before next curation sweep; shadow mix ≈ census mix first; Emperor's ceremony owns the flip.

## 4. Admission ledger (Wave 1) — options + lean

- Architect decision: dedicated additive table vs project-history `entry_type`. **Lean: dedicated additive table** (read-only-cheap decision substrate; project-history is human-ledger-shaped).
- **DIVERGENCE C:** leader admission-only vs architect v2 §3 admission/retrieval. **Plan follows architect (wider)** — admission-only cannot distinguish "useless" from "needed-but-never-asked-for" (112 zero-retrieval rows (499-era architect-prose figure, NO in-branch source — S-2 reconciliation in plan-overview Research Insights)). Flag leader.
- **DIVERGENCE D:** D3 "audit sink = project-history `entry_type`" is **subsumed** by the ledger shape. If dedicated table lands, gate audit goes there. Flag architect.
- Addendum-3 item 3: the audit records BOTH the verdict AND the tool's execution (one writer, auditable).

## 5. Family-count fix spec (Wave 1, do FIRST among code fixes) — **DE-SCOPED 2026-11-24 to a parallel lane; see the dated pointer at the end of this section**

- **Target (re-verified):** `daemon/repositories/pok/repository.py:221` `count_active_in_family`, query `slug.startswith(f"{family}-")` at `:250`. (Addendum's `:114-122` stale; anchor is a lead.)
- **Defect:** prefix-superset count — longer family (`skill-e6`) inflates shorter prefix (`skill`); one-directional leak; self-propagating sprawl (writer refused at `skill-04` → invents `skill-x-01` → inflates `skill` further).
- **Fix:** strict per-key membership by live multi-token family regex. One repository method. **21 operative breaches → 19 dissolve; 2 real (`guard-inv`=4, `prompt-assent`=4) — originally drafted as a merge-supersede; SUPERSEDED by §13.4#1 + review A1: SPLIT/RE-KEY + ARCHIVE under a ruled basis (no content-merge).** **FAMILY OVER-CAP RECONCILIATION: 21 is the PRE-FIX prefix-counting figure (the sprawl-engine evidence); POST-FIX strict membership yields 2 true over-cap families (`guard-inv`=4, `prompt-assent`=4) — the lane plans against the POST-FIX figure and split/re-keys + archives those 2 (never a content-merging supersede).**
- *(De-scope 2026-11-24: the family-count fix specified in this section — and the 2-family SPLIT/RE-KEY + ARCHIVE (`guard-inv`, `prompt-assent`) — are DE-SCOPED to a PARALLEL LANE (leader de-scope dispatch); this lane executes none of them. This lane's residual = coordination + refusal-path consumption + read-only re-verify + the counter carry-over at `supersede_and_insert` (`repository.py:286-311`). The design constraints TRANSFER to the parallel lane: split-never-merge (standing `pok-sweep-01-merge-cap-split`), ≤240-char escaped-body fence, archive only under a ruled basis. The section body above is preserved as provenance — it describes the pre-de-scope state.)*

## 6. Standing defects — addendum-3 REVERSES record_verification routing

- **`record_verification` — NOW GETS A CALLER (addendum-3 item 7 SUPERSEDES addendum-2 retire-with-disclosure).** Definition only at `repository.py:384` (addendum's `:253` stale), zero callers today → verify_count≡0. The gate/librarian's verification verdicts become the FIRST REAL WRITER: merges, refusals, admissions, verification verdicts stamp the metrics, which feed its next pass. **Wire `record_verification` into the gate path — do NOT retire.**
- **Vector leg — still retire-with-disclosure (addendum-3 item 7).** Embeddings table 0 rows AND seam never passes `query_embedding`; no caller identified. Document, do not wire.
- Never age / never access_count-alone as worthlessness signal (112 zero-retrieval rows (499-era architect-prose figure, NO in-branch source — S-2 reconciliation in plan-overview Research Insights) prove it).

## 7. Supersession notes

- **RIDER RESTORED BY ADDENDUM-3 / EMPEROR RULING.** Addendum-2's retraction is REVERSED. KB-writer LOSES the `pok` grant. Emperor verbatim: *"I think remove tool from kb writer. POK tool for big agent like Ari, pm, leader, devops which really need manage the POK. They are doing operation task too."* Census 54→53. **DIVERGENCE A RESOLVED** — Emperor supersedes BOTH architect §4.3 and addendum-2 retraction.
- **Rider rails (corrected by tech verification):** pin that REDs = `tests/unit/test_pok_grant_pins.py` (`EXPECTED_EXPLICIT` 54→53 + completeness-walk exception for kb-writer + refresh stale docstring); `tool-categories.json` NOT touched (vocabulary, not grants — no regen, no version bump); `meta.json` UNSEALED (no baseline regen for the rider); NEW pin asserting kb-writer does NOT hold `pok` with mutation leg (re-adding must RED).
- **Compensating measure (addendum-3 item 1):** capture duty migrates to operational agents (ari, project-manager, leader, devops); their guidance updated so operational lessons ARE captured in POK with a reason. The one-fact-one-store routing contract MIGRATES to these 4 (operational→POK with reason, knowledge→RAG); kb-writer keeps RAG-side routing only. Release notes DISCLOSE: kb-writer's verify family gets no NEW captures from that path; capture moves to operators.
- **Addendum-1 Item 2 reconciled via R1-beats-R4** (no separate redundancy rule). **Addendum-1 Item 3 (scope) unchanged** — `pok_default_project` on the leader instance = scope classification, never a verdict (AC10).
- **Numbers (addendum-3 supersedes earlier set):** 499 active @13:5xZ snapshot; composition OH 396 / RS 74 / EPISODE 21 / GK 4 / INCIDENT 2 / DUP 2; tiers fully inert — curated 0, pinned 0, verify_count 0 everywhere, 109 never-accessed (`access_count=0`, in-branch sourced: `census-mechanics:381` / `store-census:39`). Cite snapshot times, NEVER bare counts. (The briefs' 12,968-doc RAG corpus and overlap classes BOTH-USEFUL 337/65.8%, RAG-DUPLICATE 81/15.8%, POK-ONLY 78/15.2%, NEITHER 16/3.1% remain for the guideline overlap section.)

## 8. Gate = POK-librarian maintenance agent — outcomes, authority, tool contract (addendum-3 items 2–4)

### Outcome set (expands ACCEPT/REJECT → four outcomes)
- **ACCEPT** — admit a new fact.
- **MERGE** — an active row already carries the fact; name the target slug, do NOT create a second row. **Open architect design point: merge-body authorship** — judge-proposed merged body executed as a count-preserving supersede vs caller-proposed vs escalate to ASK.
- **ASK** — on conflict or low confidence (≤2/3), request the caller's confirmation/explanation: interactive round-trip with a gate id + structured question.
- **REJECT** — routable alternative home (per R5).

### Authority split (BINDING)
The gate **NEVER WRITES** — it returns a verdict. The **TOOL executes**: write (ACCEPT); supersede (MERGE delta, count-preserving); NO write (MERGE no-delta); archive residue under a ruled basis. The **audit records BOTH verdict and execution**. One writer, auditable. Confirms the in-process pure-function judge shape.

### Tool contract (ASK answer path)
- `pok_upsert` returns a structured **"confirmation required"** payload for ASK.
- A completable answer path in ONE agent turn: **dedicated `pok_gate_answer(gate_id, answer)` tool OR re-invocation with an answer field — architect picks and justifies.**
- **A MISSING ANSWER MUST NEVER DEFAULT TO ACCEPT.** Pending-gate state is IN-PROCESS with a TTL; expiry = no write (fail-closed). The ask+answer outcome lands in the audit trail. **No live-store writes for pending state.**

## 9. Metrics + per-project soft/smart max (addendum-3 item 7 — NEW SUBSYSTEM)

Emperor verbatim: *"POK entry have metrics, so the POK librarian can access for it management job. Ask them try to keep a reasonable max (soft/smart max) per project. Hard cap is 1024."*

- **Librarian reads per-entry metrics** (access_count, verify_count, pinned/disputed/curated tier, family, author, timestamps) for merge-target selection, redundancy evidence, staleness signals.
- **Per-project soft/smart max:** adaptive working ceiling BELOW the 1024 hard backstop. METRICS-DERIVED, not a hand-set constant — derived from measured utilization (rows actually injected/cited) with a floor, recomputed on the librarian's periodic pass, drift reported. **Architect decides WHERE it lives** (project metadata vs computed-on-pass vs curated row) and says why.
- **Near soft max:** admission TIGHTENS (more ASK rounds; prefer MERGE over new rows) + a proactive consolidation pass. **Consolidation ONLY under ruled archive bases — covered-by-surviving-row · relocated-to-routed-home · NOW-FALSE — NEVER age, NEVER access_count alone.**
- **Close the loop:** the metrics are DEAD CODE today (§6). The gate/librarian's own activity is the FIRST REAL WRITER (verification verdicts, merges, refusals, admissions stamp them; they feed its next pass). Do NOT ship a metrics-driven librarian flying only on access_count.
- **Trusted vs ignored metrics (plan states with reasons):**
  - TRUST: gate-stamped verify_count (once wired, §6); pinned/disputed/curated tier (curator signals, leader/PM); family (post strict-count fix, §5); verdict-derived merge/refusal/admission counts (librarian's own output); timestamps as provenance.
  - IGNORE: access_count ALONE (retrieval-driven not use-driven; 112 zero-retrieval rows (499-era architect-prose figure, NO in-branch source — S-2 reconciliation in plan-overview Research Insights)); age (never a consolidation basis); vector/embeddings (dead, retire-with-disclosure); verify_count BEFORE gate stamping (structurally 0 — absence is not evidence); tier=0 as a signal (all inert today).
- **Periodic-pass design:** architect's call — house `MaintenanceService` precedent (`daemon/services/maintenance.py:259`, registered interval jobs) vs on-write-only recompute. Keep simple.

## 10. Assess-and-report — feeder agents (addendum-3 item 5, NO unilateral action)

Emperor named kb-writer only. The plan carries an ASSESSMENT task; the REPORT recommends, the Emperor decides. Evidence gathered (read-only, worktree):
- `experiencer` and `kb-importer` BOTH hold the `pok` grant (1 explicit entry each in `agents/*/meta.json`).
- NEITHER has any `pok` mention in its `.md`/`.yaml` prompts (same silent-grant shape as kb-writer pre-rider).
- NEITHER appears in the top-12 creator census (worker 148, kb-writer 131, leader 49, giter 27, project-manager 24, tester 22, developer 20, coder 19, skill-keeper 7, devops 7, maintenancer 6, reviewer 6; remaining 53 long tail).
- **Recommendation to carry (Emperor decides):** the "feeders are not operators" logic plausibly extends to both, but their measured POK write volume is in the long tail (≤53 combined) vs kb-writer's 131 — lower priority; assess exact volume before any grant change.

## 11. Divergence register

| ID | Status | Leader | Architect | Plan follows | Flag |
|---|---|---|---|---|---|
| A | **RESOLVED** | Emperor: rider back on | §4.3 grant drop | Emperor ruling (grant removal) | none — resolved |
| B | **RESOLVED** | reason REQUIRED now | soft-required Wave 2 | Leader (REQUIRED now) — architect sign-off §3 | none — resolved |
| C | **RESOLVED** | ledger admission-only | admission/retrieval | Architect (wider) — architect sign-off §3 | none — resolved |
| D | **RESOLVED** | D3 audit sink = project-history | ledger shape subsumes sink | Ledger decision (dedicated table) — architect sign-off §3 | none — resolved |
| E | **RESOLVED** | D2 fail-closed `pok_gate_unavailable` | v1 §78 fail-open | Leader D2 fail-closed + detail discriminator `reason` ∈ {timeout, unparsable, llm_error} — architect sign-off §3 | none — resolved |
| F | **RESOLVED** | — | merge-body authorship | **F2** — caller-confirmed merge; `merge_policy` switchable enum default `ASK_WITH_PROPOSAL`; no-delta/delta split | none — resolved |
| G | **RESOLVED** | — | ASK answer-path shape | **G1** — dedicated `pok_gate_answer(gate_id, answer)` tool; TTL 1200s; 6 typed refusals; ASK-cached-as-re-ask; answered-never-cached-as-accept | none — resolved |
| H | **RESOLVED** | — | periodic-pass design | **H3** — hybrid: MaintenanceService job (UN_GATED) + on-write bound-check | none — resolved |
| I | **RESOLVED** | — | soft-max placement | **I1** — `project_metadata_records` key `pok.soft_max.v1`; ZERO migration; curated-row variant doctrine-wrong | none — resolved |
| J | **RESOLVED** | — | trusted-vs-ignored metric set | **CONFIRMED plan §9 set + 3 amendments** (ledger retrieval supersedes access_count; timestamps corroborate-only; per-row vs per-store labels) + counter carry-over | none — resolved |

## 12. Fences (unchanged)

No live-store writes during build (scratch/synthetic/project-scoped child only); no promote; no live config change; branch+worktree only (carve-out: the Phase-13 staged-release tag push is sanctioned staging under ruling (7) — push = sanctioned staging, PROMOTE stays the Emperor's ceremony); cd into worktree for every command; env scrubs PER-VARIABLE; pytest scoped and run from worktree; commit planning docs in-branch (`docs(<scope>):`). Anchors are leads — re-grep at use time. Cite snapshot times, never bare counts.

---

## 13. ARCHITECT FOLD (2026-11-23, ~17:5xZ) — BINDING PICKS

Source: `architecture-recommendation.md` (186 lines, in this directory) — architect fan-in from 3 analyst reports. Status: **BINDING**. It enriches the plan-of-record and does NOT reopen the folded rulings in §1–§12; where it conflicts with them, THIS section wins. §1–§12 above remain the historical record of what was folded when.

### 13.1 D1 OVERTURNED — latency defaults changed

**Shape CONFIRMED; defaults OVERTURNED.** 12s/attempt sat BELOW an observed 13.6s judge success (measured quick successes 2.6–13.6s, 4/8 >15s). New defaults: **20s/attempt, ≤1 retry, 45s wall**, env `ENSEMBLE_POK_GATE_TIMEOUT_S`, min 10 / max 180, Pattern-C resolver shape (cached global, one-shot WARN, min clamp, restart-read). Failure mode benign (timeout ⇒ `pok_gate_unavailable`, no write, retryable); 45s sits inside the 180s graph heartbeat. **R1 is hereby RESOLVED/OVERTURNED** with these defaults.

### 13.2 Fork picks (F–J RESOLVED)

| Fork | PICK | Binding detail |
|---|---|---|
| **F** | **F2 — caller-confirmed merge** | Judge outcome carries `target_slug` + `merged_body_proposal`; ASK payload carries the **FULL target body** (not the 80-char digest slice) + candidate body; caller confirms/edits; TOOL executes the caller-final body as a count-preserving supersede. `merge_policy` enum ∈ {`ASK_WITH_PROPOSAL` (default), `AUTO`, `ASK_NO_PROPOSAL`} on the same switch family as enforcement — P1 ceremony moves the dial, no rebuild. **No-delta merge** (fact fully carried) = **NO write**: verdict row + `record_verification` stamp on the target. **Delta merge** (candidate adds nuance) = the only case a merged body is authored. |
| **G** | **G1 — dedicated `pok_gate_answer(gate_id, answer)` tool** | Answer carries NO body; pending entry stores the adjudicated stripped body + all upsert inputs; execution re-runs `POKService.upsert` with `gate_fn=None` so scope/fence/budget/no-op/caps re-check at answer time. **TTL 1200s default**, env `ENSEMBLE_POK_GATE_ASK_TTL_S`, min 120 / max 3600 (derivation: `request_timeout=610s` ⇒ TTL < ~700s expires legitimate answers and turns the gate into a write-blocker). **Six typed refusals:** `pok_gate_unknown`, `pok_gate_expired`, `pok_gate_already_answered` (audited no-op), `pok_gate_not_owner` (AUDITED), `pok_gate_answer_required`, `pok_gate_answer_unrecognized` (unclassifiable free-text ⇒ NO write). **Cache × ASK:** ASK cached AS-ASK; replay re-asks with the SAME question but re-mints a fresh `gate_id` + TTL; the ANSWERED outcome is NEVER cached as ACCEPT. **Audit = 3 rows chained by `gate_id` + `phase`:** `ASK_MINTED` → `ASK_ANSWERED` → `ASK_EXECUTED`; plain ACCEPT/MERGE/REJECT keep 2 rows. **Shadow-ASK is advisory-only** — verdict logged (`phase=ASK_SHADOW`), `gate.advisory=true`, NO pending mint, NO "confirmation required" payload ⇒ the answer path ships dark until P1 and **must be exercised in ENFORCE mode in unit tests** (plus one dev-lane exercise). |
| **H** | **H3 — hybrid pass** | ONE global MaintenanceService job (registered at manager initialize, `min_interval_hours` per cadence) looping project_ids internally + the on-write bound-check (subsumed — upsert already loads `active_count` for the 1024 cap at `pok_service.py:458-465`). Registration requirements: `idle_policy=UN_GATED` (IDLE_GATED starves on a busy nest), membership in `JOB_MAINTENANCE_SECTIONS` (`maintenance.py:218`), per-project try/except, boot-SLO 120s unaffected (dict-append registration; `_loop` sleeps 60s before first tick; hydration fail-soft). |
| **I** | **I1 — `project_metadata_records` key `pok.soft_max.v1`** | Table EXISTS (`project/models.py:358-373`, `uq_project_metadata_project_key`) ⇒ **ZERO migration**. Value = JSON `{value, inputs, computed_at, drift}`, written by the pass job (daemon-internal writer — does not violate the authority split, which governs agent-facing tool calls), read at write time by one unique-index SELECT (sub-ms), NO in-process memo. Per-DB row = per-env scoping free. **I3 (curated POK row) is DOCTRINE-WRONG** — a count-that-changes is rolling state, which the store's own rule (`pok-scope-01-rolling-state-out`) routes OUT of POK; it would also steal an injection slot, burn a family slot, be BM25-ranked, and be self-gating. **I2 (ledger row) is REJECTED** — conflates state with an event log; retention could silently delete the soft-max. **Prohibition recorded: pass summaries do NOT live in the admission ledger.** |
| **J** | **CONFIRMED plan §9 set + 3 amendments + 1 fix** | TRUST: gate-stamped verify_count, curator tiers (cold signals), post-fix family, verdict-derived counts, timestamps as provenance. IGNORE: access_count ALONE, age, vector/embeddings, pre-stamp verify_count, tier=0. **Amendments:** (1) ledger retrieval counts supersede access_count once Phase 1 lands (they disambiguate query-vs-injection); access_count becomes legacy corroboration. (2) Timestamps corroborate, never trigger — NOW-FALSE usually co-occurs with ancient `last_verified`, but staleness alone never fires. (3) Label per-row vs per-store metrics separately (per-row verify stamps drive row decisions; refusal-rate aggregates are pass telemetry). **Cold-start policy:** derived = `max(floor, f(utilization))`; the **floor must sit ≥ current active count band** so tightening stays DORMANT until real gate activity lifts the derived value — else the first pass retroactively criminalizes the 499 existing rows. First weeks = observe-and-stamp only. Required unit test: **"no tightening when active_count < floor"** (highest-value test in the soft-max derivation task). |

**Partition (J):** MERGE-TARGET selection ← verify_count + tier + family-match + recency + high-access corroboration. Redundancy evidence ← family + content_sha256 + prior MERGE verdicts (a RAG twin is NEVER redundancy). Staleness ← corroborator for ruled bases only. access_count in COMBINATION (never sole): high-access+zero-verify+stale → verification rounds (ASK), never archive; zero-access+boundary-ELSEWHERE → surfaced for relocated-to-routed-home.

### 13.2a Correction note — stale markers in the binding arch doc (NOT edited)

`architecture-recommendation.md` is BINDING and is **not edited**. Two of its markers are stale against the worktree and are superseded here (this section wins on conflict):
- `architecture-recommendation.md:57` `KNOWN_TOOL_NAMES` `271→272` — **true value 269→270** (re-derived in-worktree; `pok_gate_answer` absent today). The plan already carries 269→270 (see §13.6 pin G).
- `architecture-recommendation.md:120,:184` the bare "71" dup-slug figure — **uncited**; the plan carries no bare number (the reconciliation gate stands, see §13.8).

### 13.3 Binding requirements for the developer

1. **Service-signature defaults are MANDATORY:** `reason: str | None = None` AND `gate_fn=None` on `POKService.upsert` — otherwise **97 direct `svc.upsert` test sites** break (verified: `tests/unit/services/test_pok_service_unit.py` = 97 `.upsert(` sites).
2. **R4 digest K 32 → 48** within the SAME 8000-char cap (per-row ≈126 chars ⇒ 8000 admits ~60 rows; K=32 left ~3000 chars unused; K=48 ≈7250 < 8000). Do NOT raise the cap (latency is the binding risk) or shorten bodies (destroys distinctive tokens). Zero-overlap fallback = same-family-first / most-recently-verified, NOT slug order. **[DATED POINTER — leader disposition fold 2026-11-24: this R4 text is RETAINED AS PROVENANCE/HISTORY, not the operative acceptance wording. The ≤8000-char budget is the binding constraint; K=48 is the observed sizing within it, NOT a pinned constant. The operative sites (plan-overview SC#20 + T7.9; phase7-plan.md rows 9/exit) now restate the bound. architecture-recommendation.md:105 is left untouched as the HISTORICAL architect record.]**
3. **R6 stub is PROD-FORCED to `llm`:** stub reachable ONLY via test config (mode=stub in prod ⇒ loud one-shot WARN + forced llm); stamp `model_alias="stub"` on every stub verdict; segregate stub cache entries.
4. **Alias seam = purpose-bound `POK_GATE_MODEL` env** (default literal `"quick"`), NEVER a `model_quick` fallback rewrite: `config.llm.model_quick` can be silently rewritten to the GLOBAL model by `set_title_model_fallback` (`config.py:573-584`). Follow the `image_gen.py` purpose-bound pattern; the proxy resolves; **stamp the resolved alias on every verdict row**.
5. **Enforcement switch = TWO SIBLING settings keys**, both default OFF at build: `pok_gate.semantic_enforcement` + `pok_gate.softmax_tightening`. Soft-max is computed + drift-reported from day one (safe), but the TIGHTENING ACTION changes admission outcomes ⇒ rides the switch family. Daemon-settings surface, DB-backed, `settings:write` — NOT env (an env flip is a live config change at deploy + restart). Ceremony flips later via the operator route; the build makes NO live config change.

### 13.4 Cross-cutting plan defects — amendments applied in this fold

| # | Defect | Severity | Amendment (now binding on the plan) |
|---|---|---|---|
| 1 | Phase 2 T2.3/T2.4 "merge via supersede" for `guard-inv`(4) / `prompt-assent`(4) is **content-destructive** — four DISTINCT durable rules cannot fit one ≤240-char paragraph; standing POK `pok-sweep-01-merge-cap-split` says SPLIT, never merge-with-drop | 🔴 Wave 1 | T2.3/T2.4 become **SPLIT/RE-KEY + ARCHIVE** under a ruled basis — never a content-merging supersede |
| 2 | Supersede resets counters (successor verify_count/access_count = 0, `models.py:251-260`) — every merge breaks the J trust chain | 🔴 | **Counter carry-over** at `supersede_and_insert` (`repository.py:286-311`) in Phase 2's repository touchpoint, WITH a pin (mutation: dropping carry-over → RED) |
| 3 | Pinned/curated merge target is a dead end — superseding PINNED/CURATED rows is curator-only (`pok_service.py:592-611`) and the judge cannot see tier from the digest | 🟡 | TOOL pre-checks target tier; untouchable target → **ASK routed to leader/PM (CURATOR)**, never auto-merge |
| 4 | Phase 1 ledger schema as-scoped misses `gate_id`, `phase`, merge-proposal fields | 🟡 | Land them in the Phase 1 table definition UP FRONT (additive now; a migration later otherwise) |
| 5 | `UpsertOutcome` lacks an ASK variant (`pok_service.py:237-242`) | 🟡 | Added to the Phase 7/8 boundary task list |
| 6 | Unanswered ASKs are silent losses for non-returning callers (fail-closed ⇒ the lesson is never written) | 🟡 | Librarian's periodic pass surfaces expired/never-answered gates; audit records ask+expiry (`phase=ASK_EXPIRED`) |
| 7 | dup-slug count (P4) uncited vs census DUPLICATE=2, exact-body dup groups=0 | 🟡 parked | **PRE-P4 RECONCILIATION GATE** — the sweep lane must reconcile the figure before Phase-4-era consolidation acts on it; NOT a pre-build blocker |

### 13.5 Binding schema consequences (no-pick-foreclosed rule)

Phase 7 typed outcome AND Phase 1 ledger rows MUST carry from day one:
`{target_slug, merge_kind (no_delta|delta), proposed_body, proposed_body_sha256, target_body_sha256, executed_body_sha256, executed_by}`
plus ledger `gate_id` + `phase` columns (the 3-row ASK chain is keyed by them). The ledger is additive and cheap now; a schema that cannot represent a proposal forces a migration at P1.

### 13.6 Fork pins (leg rows must become pins on the CHOSEN shapes, with mutation legs)

| Pin | Assertion | Mutation leg |
|---|---|---|
| **G** | `pok_gate_answer` exists in `KNOWN_TOOL_NAMES` (verified 269 names in this worktree → 270) | answer-field branch inside `pok_upsert` → RED |
| **F** | MERGE delta-execution requires a completed ASK answer under `merge_policy=ASK_WITH_PROPOSAL` | auto-execute without confirmation → RED; no-delta merge writing a row → RED (verification-stamp only) |
| **TTL** | expiry ⇒ `pok_gate_expired`, no write | expiry-defaults-to-accept → RED (bound to 1200s) |
| **Cache** | cached ASK replay re-mints `gate_id` and NEVER returns accept | ASK-cache-hit → accept → RED |
| **Soft-max** | value lives at `pok.soft_max.v1` in project metadata | a soft-max/meta row inside the POK store → RED (doctrine pin) + "no tightening when active_count < floor" unit |
| **Pass** | job registered UN_GATED + in `JOB_MAINTENANCE_SECTIONS` | boot path contains pass execution → RED (SLO guard) |
| **Counter carry-over** | merge/supersede preserves verify_count lineage | counter reset on supersede → RED |
| **Alias / stub / switch** | `POK_GATE_MODEL` is the literal purpose-id `"quick"` (never the global model); mode=stub in prod ⇒ forced llm; both sibling settings keys OFF at build | alias resolves to the global model → RED; stub reachable in prod → RED; either switch ON at build → RED |

**Pin-set reconciliation (Phase 11 mirror):** `phase11-plan.md` + `plan-overview.md` carry all SEVEN fork pins — G, F, TTL, Cache, Soft-max, Pass, Counter carry-over — plus the alias/stub/switch pin. §13.6 above lists the same set (TTL now named as its own pin row; alias/stub/switch added). The two surfaces are one-to-one.

### 13.7 Fold-picks consequences for the "architect picks" leg rows

The task-map leg rows that read "architect picks" are now **pins on the CHOSEN shapes** (F2 / G1 / H3 / I1 / J) with the mutation legs in §13.6 — a plan gate must check the ownership CHAIN, and the chain now has concrete shapes to check. Specific row amendments: T7.6 → F2 + merge-policy pin; T8.5 → G1 dedicated tool + KNOWN_TOOL_NAMES pin; T10.3 → I1 key pin; T10.4 → H3 UN_GATED + JOB_MAINTENANCE_SECTIONS pin; T10.8 → J metric set + 3 amendments.

### 13.8 P4 dup-slug count — pre-P4 reconciliation gate

Census measures DUPLICATE = 2/499 and exact-body duplicate groups = 0. If the higher figure is real, the merge population is ~35× the census estimate and F hardens further toward confirmation-gating. **This is a PRE-P4 RECONCILIATION GATE, not a pre-build blocker**: the parked sweep lane must reconcile the figure before any Phase-4-era consolidation work acts on it.

### 13.9 Remaining decisions pending (leader/Emperor — not blocking build)

1. **P4 dup-slug reconciliation** — before the parked sweep is planned, not before this build.
2. **`merge_policy` default at P1** — stay `ASK_WITH_PROPOSAL` vs promote to `AUTO`, decided by shadow evidence (merge rate + proposal fidelity), which the ledger records by design.
3. **Feeder grants (experiencer/kb-importer)** — assess-and-report only; unchanged by this fold.
4. **Re-measure the quick-latency distribution before P1** — the 2.6–13.6s / 4-of-8->15s probe is from 2026-09-07; it is the single assumption that most weakens this set.
