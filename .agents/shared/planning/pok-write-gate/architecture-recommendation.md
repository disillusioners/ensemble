# Architecture Recommendation: POK Write Gate + POK Librarian — Fork Resolution & Enrichment

Date: 2026-11-23 (architecture fan-in completed ~17:5xZ)
Author: architect (controller) — aggregated from 3 skill-equipped analyst reports (instance ids 07a6bbca / fb26f6b9 / 8322403c; skills: trade-off-analysis, data-flow-design, scalability-design)
Status: **BINDING PICKS for forks F/G/H/I/J + validation rulings for D1–D7, R1/R2/R4/R6, alias seam, switch surface. Enrichment of plan-of-record — does NOT reopen folded rulings.**
Worktree: /home/nea/ensemble-src-wt-pok-write-gate (branch feature/pok-write-gate, base a1130e257)
Consumes: plan-overview.md, design-amendments-v2.md (authoritative delta), technical-analysis.md, phase1/2/5/7/8/10/11/13-plans, store-census-2026-11-23.md, store-effectiveness-rag-overlap-brief.md. All anchors were re-verified in-worktree by the analysts — anchors are leads, re-grep at use time.

---

## 0. Reconciliation with prior rulings (read first)

- **Folded, NOT reopened:** Emperor ruling set (board wi-48cf7f9c) + leader sign-offs **B** (reason REQUIRED now, mechanical), **C** (ledger = admission + retrieval, the wider scope), **D** (dedicated additive ledger table), **E** (D2 fail-closed, distinct `pok_gate_unavailable`), authority split (gate NEVER writes / TOOL executes / audit records both), Emperor's ASK catch (missing answer NEVER defaults to accept), consolidation only under ruled bases.
- **Divergence register state after this doc:** B/C/D/E — architect SIGN-OFF recorded (§3). F/G/H/I/J — DECIDED here (§2). D1 — defaults OVERTURNED on evidence, shape CONFIRMED (§3). D6 — CONFIRMED. D7/Option B — CONFIRMED with three binding requirements.
- **Reconciles with pok-shape-review addendum-v2** (canonical checkout): its Wave sketch said "reason soft-required" (Wave 2) — superseded by leader B (REQUIRED now), already followed by the plan. Its §4 self-correction ranking and ledger-first ordering are honored by the Wave 1 composition.

---

## 1. Fork picks at a glance

| Fork | Question | PICK | Confidence | Dominant axis |
|---|---|---|---|---|
| **F** | MERGE-body authorship | **F2 — caller-proposed, ASK round-trip carrying the judge's merge proposal; `merge_policy` switchable enum, default `ASK_WITH_PROPOSAL`** + no-delta/delta split | Med-High | Maintainability (subsumes F1/F3) + Risk (no silent nuance loss) |
| **G** | ASK answer-path | **G1 — dedicated `pok_gate_answer(gate_id, answer)` tool**, TTL 1200s, owner-scoped, 6 typed refusals, 3-row audit | 0.8 | Risk (no payload laundering; reason-REQUIRED stays schema-level) |
| **H** | Periodic pass | **H3 — MaintenanceService job (UN_GATED, one global job looping projects) + on-write bound-check** (subsumed by T10.6 tightening) | 0.85 | Maintainability + Risk (off hot path, boot-SLO-safe) |
| **I** | Soft-max placement | **I1 — `project_metadata_records` key `pok.soft_max.v1`** (existing table, no migration); direct SELECT at write time | 0.9 | Risk/Complexity (I3 is doctrine-wrong; I2 conflates state with event log) |
| **J** | Trusted-vs-ignored metrics | **CONFIRMED plan §9 set + 3 amendments** (ledger retrieval supersedes access_count; timestamps corroborate-only; per-row vs per-store labels) + counter carry-over fix | High | — |

---

## 2. Fork rulings (each with rationale + what-changes-if-overturned)

### 2.1 Fork F — MERGE-body authorship = **F2, switchable**

**Ruling.** A MERGE verdict lands as an ASK round-trip: the judge's typed outcome carries `target_slug` + `merged_body_proposal`; the ASK payload additionally carries the **full target body** (not the 80-char digest slice) + the candidate body so the caller can check coverage; the caller confirms or edits; the TOOL executes the caller-final body as a count-preserving supersede. Execution ships behind a `merge_policy` enum ∈ {`ASK_WITH_PROPOSAL` (default, =F2), `AUTO` (=F1), `ASK_NO_PROPOSAL` (=F3)} on the same switch family as enforcement — the P1 ceremony moves the dial from shadow evidence, not a rebuild.

**Mandatory refinement — split the merge population:**
- **No-delta merge** (candidate's fact already fully carried by the target): execution = **NO write**. Record the verdict row + stamp `record_verification` on the target (the "first real writer" of the dead metrics, amendments §6). No body authored, no round-trip.
- **Delta merge** (candidate adds nuance): the only case a merged body must be authored → F2 round-trip governs.

**Evidence.** (1) The judge sees the target only as an 80-char digest slice (technical-analysis :134) — F1 asks an LLM to reconcile a 240-char candidate against an 80-char slice into one ≤240-char post-escape paragraph: the named nuance-loss risk, mechanized. (2) The decision precedes the evidence that could validate F1 — shadow is default OFF, enforcement parked at P1, and "shadow mix ≈ census mix" is the P1 precondition (amendments :44); F2 keeps every option reachable. (3) Measured rarity: DUPLICATE = 2/499 rows, exact-body duplicate groups = 0 (census :33,:39) — the round-trip tax is negligible at observed merge rates. (4) `reason` is an operational claim ("why it matters"), not a content spec — a poor sole grounding for authorship. (5) Weighted 5-axis: F2 3.70 / F1 3.50 / F3 3.05 (F3 is F2 with two dials turned, not a third architecture; it permanently forecloses the fast path and self-throttles the near-soft-max "prefer MERGE" policy).

**Binding schema consequence (no-pick-foreclosed rule).** Phase 7 typed outcome AND Phase 1 ledger rows MUST carry `{target_slug, merge_kind(no_delta|delta), proposed_body, proposed_body_sha256, target_body_sha256, executed_body_sha256, executed_by}` from day one — the ledger is additive and cheap now; a schema that cannot represent a proposal forces a migration at P1.

**What-changes-if-overturned (to F1):** phase7 T7.6 becomes content-producing (mandatory `merged_body` + budget-aware judge prompt); the digest must widen to the FULL target body for merge candidates (changes the K/8000 budget); phase8 T8.2 executes the judge body directly; a body-fidelity acceptance check becomes REQUIRED (F1 has no other loss detector). **(to F3):** ASK payload carries full target + candidate bodies; the answer vocabulary must carry a caller-authored body — materially constraining G toward a body-bearing answer tool.

### 2.2 Fork G — ASK answer-path = **G1, dedicated tool**

**Ruling.** New agent tool `pok_gate_answer(gate_id, answer)`; the answer carries NO body (the pending entry stores the adjudicated stripped body + all upsert inputs; execution re-runs `POKService.upsert` with `gate_fn=None` so scope/fence/budget/no-op/caps re-check at answer time).

- **TTL = 1200s default**, env `ENSEMBLE_POK_GATE_ASK_TTL_S`, min 120s / max 3600s, Pattern-C resolver shape (cached global, one-shot WARN, min clamp, restart-read — mirror `attestation_judge_timeout_resolver.py:84-180`). Derivation: the ASK→answer gap contains ≥1 full LLM step; `request_timeout=610s` (config.py:202) ⇒ any TTL < ~700s expires legitimate answers and turns the gate into a write-blocker. 1200s ≈ 2× worst-case step, bounded staleness window; staleness is doubly bounded by answer-time re-validation.
- **Typed refusals (never silent accept, never fall-through to a fresh accept):** `pok_gate_unknown` (no id / post-restart), `pok_gate_expired`, `pok_gate_already_answered` (audited no-op), `pok_gate_not_owner` (agent_id+instance_id mismatch; AUDITED), `pok_gate_answer_required`, `pok_gate_answer_unrecognized` (unclassifiable free-text ⇒ NO write — the Emperor's catch covers a garbage answer, not only a missing one).
- **Cache × ASK:** cache ASK verdicts AS-ASK under the D5 key; replay re-asks with the SAME question but re-mints a fresh gate_id + TTL. The ANSWERED outcome is NOT cached as ACCEPT (caller self-attestation must not launder into a durable gate accept for later writers); idempotence for a completed write comes from the service no-op echo (:417-432) which precedes the gate slot. Stub-mode and llm-mode cache entries are segregated (mode in the key or separate store).
- **Audit = 3 rows chained by `gate_id` + `phase`:** `ASK_MINTED` (verdict row) → `ASK_ANSWERED` (post-answer adjudication, answer excerpt, answered_by) → `ASK_EXECUTED` (execution row). Plain ACCEPT/MERGE/REJECT keep 2 rows. ⇒ **Phase 1 ledger schema needs `gate_id` + `phase` columns from the start.**
- **Shadow-ASK:** advisory-only — verdict logged (`phase=ASK_SHADOW`) + envelope `gate.advisory=true`; NO pending mint, NO "confirmation required" payload (that framing on an already-committed write is misleading and IS a behavior change, which shadow forbids). Consequence: **the answer path ships dark until P1 — T8.4–T8.7 acceptance must be exercised with the switch ON in unit tests** (plus one dev-lane exercise); never trusted on shadow evidence.

**Evidence.** One-turn completability is mechanically satisfied by BOTH shapes (tool→agent edge graph.py:14930, recursion_limit 300) — the fork is surface/safety, not turn mechanics. G1 wins Risk decisively: under G2 the re-invocation must ignore-or-refuse re-sent slug/body/reason (else it adjudicates one body and writes another), LangChain cannot express conditional requiredness (schema-level `reason` REQUIRED weakens to runtime-only — kills success criterion 1), and an answerless retry re-enters the gate (re-ASK ping-pong). G1 costs: `KNOWN_TOOL_NAMES` 271→272 (+ drift test), 3-tool pin → 4 names, +1 schema per pok-holder; **no meta.json edits** (category auto-expansion, instance.py:455-520), **no tool-categories.json change** (category-level artifact, V11/66).

**What-changes-if-overturned (G2):** T8.5 rewrites to dual-mode with branch-before-reason-check, stored-inputs execution, `pok_gate_payload_mismatch` guard, docstring-as-schema teaching, and a NEW pin that the answer path never re-enters the gate. (Full delta list in analyst report §10.)

### 2.3 Fork H — periodic pass = **H3 hybrid**

**Ruling.** ONE global MaintenanceService job (registered at manager initialize, `min_interval_hours` per pass cadence) looping project_ids internally + the on-write bound-check Phase 10 T6 needs anyway (upsert already loads `active_count` for the 1024 cap at pok_service.py:458-465 — the check is subsumed, not added).

**Binding registration requirements:** `idle_policy=UN_GATED` (IDLE_GATED would starve on a continuously busy nest — the checkpoint-cleanup failure mode, critical note 2026-10-22); membership in `JOB_MAINTENANCE_SECTIONS` (persisted clock via `maintenance_runs` + free observability); per-project try/except (never boot- or write-path-coupled). Boot SLO 120s unaffected: registration is dict-append; `_loop` sleeps 60s before first tick in a background task; hydration fail-soft.

**What-changes-if-overturned (H2):** T10.5 drift loses its cadence basis and contradicts the Emperor ruling's "recomputed on the periodic pass, drift reported" — that is a ruling refresh, not a silent re-scope. (H1-only: T10.6/T10.7 have no home — H3 is load-bearing for them.)

### 2.4 Fork I — soft-max placement = **I1 project metadata**

**Ruling.** `project_metadata_records` (EXISTS: project/models.py:360-373, uq(project_id, meta_key) — **no migration**) key `pok.soft_max.v1` = JSON `{value, inputs, computed_at, drift}`. Written by the pass job (daemon-internal writer — does not violate the authority split, which governs agent-facing tool calls). Read at write time by one unique-index SELECT (sub-ms) — direct read, NO in-process memo (a cache adds coherence risk for no gain). Per-DB row = per-env scoping free. Ceremony-clean: settings keys = policy switches (operator, global per env); metadata key = machine-computed value (per env per project); the flip moves no value.

**Rejected:** I3 (curated POK row) is **doctrine-wrong**: a count-that-changes is rolling state, which the store's OWN rule routes OUT of POK (`pok-scope-01-rolling-state-out`) — the gate would have to reject its own state row; it also steals an injection slot (8-row budget), burns a family slot (cap 3), is BM25-ranked (absent when needed), and is self-gating. I2 (computed-only / ledger row) conflates state with an event log and risks retention silently deleting the soft-max; record the prohibition: **pass summaries do NOT live in the admission ledger.**

**What-changes-if-overturned (I2):** new ledger row type + latest-of-type reader + retention carve-out. (Settings-surface variant: needs per-project key embedding + an authority exception — a re-decision, never silent.)

### 2.5 Fork J — metric set = **CONFIRMED + 3 amendments + 1 fix**

**Confirmed TRUST:** gate-stamped verify_count (once wired; today 0 callers, repository.py:384-397), curator tiers (all inert today — cold signals), post-fix family, verdict-derived counts, timestamps as provenance. **Confirmed IGNORE:** access_count ALONE, age, vector/embeddings (dead), pre-stamp verify_count, tier=0.

**Amendments:**
1. **Ledger retrieval counts supersede access_count once Phase 1 lands** (they disambiguate query-vs-injection, which the effectiveness brief :104 explicitly could not); access_count becomes legacy corroboration.
2. **Timestamps corroborate, never trigger.** NOW-FALSE usually co-occurs with ancient last_verified, but staleness alone never fires.
3. **Label per-row vs per-store metrics separately** (per-row verify stamps drive row decisions; refusal-rate aggregates are pass telemetry).

**Partition (clean):** MERGE-TARGET selection ← verify_count + tier + family-match + recency + high-access corroboration. Redundancy evidence ← family + content_sha256 + prior MERGE verdicts; a RAG twin is NEVER redundancy. Staleness ← corroborator for ruled bases only. **access_count in combination** (never sole): high-access+zero-verify+stale → verification rounds (ASK), never archive; zero-access+boundary-ELSEWHERE → surfaced for relocated-to-routed-home.

**Cold-start policy:** derived = `max(floor, f(utilization))`; **floor must sit ≥ current active count band** so tightening stays DORMANT until real gate activity lifts the derived value — else the first pass retroactively criminalizes the 499 existing rows as spurious ASK-tightening. First weeks = observe-and-stamp only. Required unit test: "no tightening when active_count < floor" (highest-value test in T10.2).

**Fix (structurally required, from the F analysis):** `_supersede` builds the successor with fresh counters (verify_count/access_count default 0, models.py:248-264) — **every merge would reset the trusted metrics and break the J trust chain.** The merge execution must carry prior counters to the successor (or equivalently stamp `record_verification` on the successor at merge execution). This belongs in Phase 2's repository touchpoint (`supersede_and_insert`, repository.py:286-311).

---

## 3. Validation rulings (leader forks + carried flags)

| Target | Ruling | Evidence / reason |
|---|---|---|
| **D1** sync gate, 12s/attempt, ≤1 retry, ≤25s | **Shape CONFIRMED; defaults OVERTURNED → 20s/attempt, ≤1 retry, 45s wall**, env `ENSEMBLE_POK_GATE_TIMEOUT_S`, min 10 / max 180, Pattern-C | Measured quick successes 2.6–13.6s with 4/8 >15s (resolver :36-45) — a 12s default sits BELOW an observed success and would fail-close calls proven to succeed; the attestation judge's own history is 10s ("false-positive amplifier") → 25s → 180s (:25-60). The gate prompt is larger than the attestation judge's (digest ≤8000 chars). Failure mode is benign (timeout ⇒ `pok_gate_unavailable`, no write, retryable). 45s stays inside the 180s graph heartbeat. |
| **D2** | **CONFIRMED** + one refinement | Distinct code with a detail discriminator (`reason=timeout\|unparsable\|llm_error`) — unparsable garbage also fails closed, never re-labelled `pok_gate_reject`. Message contract: "write NOT committed; not a judgement of your content; retry shortly". |
| **D4** | **CONFIRMED** + alias-seam ruling below | In-process judge under `daemon/services/pok_gate.py` (naming family verified, no collision); sync seam `invoke_raw_with_failover` (wall cap fires BETWEEN attempts ⇒ per-request HTTP timeout mandatory). |
| **Alias seam** | **CONFIRM the rule (alias only, never internal pool ids); dedicated env knob `POK_GATE_MODEL`, default literal `"quick"`** | `config.llm.model_quick` can be silently rewritten to the GLOBAL model by `set_title_model_fallback` (config.py:575-584) — the room_naming precedent shares this hole. Follow the image_gen.py pattern (:266-300): purpose-bound env → literal purpose-id, never reads `OPENAI_MODEL`/`config.llm.model`, proxy resolves. Stamp the resolved alias on every verdict row. |
| **D5** | **CONFIRMED** + ASK amendments (§2.2) | Cache key `(project_id, slug, sha256(stripped body), sha256(normalized reason))`; both ACCEPT+REJECT replayed; ASK replays as re-ask. |
| **D6** | **CONFIRMED** | `pok_curate` stays curator-authority; document in guideline. |
| **D7 / R2 (Option B)** | **CONFIRMED Option B** + 3 binding requirements | Service-layer gate slot strictly BETWEEN the no-op echo (:432) and supersede (:433) — a supersede must be judged (digest carries the prior row ⇒ MERGE detection works). Explicit `gate_fn=None` trusted bypass (only write callers in-tree: the tool + the dev seeder — B covers 100% of agent writes, zero extra queries). Requirements: (i) `reason: str \| None = None` AND `gate_fn=None` defaults, or **97 direct `svc.upsert` test sites break**; (ii) slot position as above; (iii) `UpsertOutcome` needs an ASK variant (entry is non-optional, pok_service.py:238-243) — plan gap, add to Phase 7/8 boundary. |
| **R4** | **CONFIRM shape; K 32→48** | Per-row digest cost ≈126 chars ⇒ 8000-char cap admits ~60 rows; K=32 leaves ~3000 chars unused. K=48 ≈ 7250 < 8000 (1.5× duplicate-candidate coverage now, headroom at the 1024-row horizon). Do NOT raise the cap (latency is the binding risk) or shorten bodies (destroys distinctive tokens). Zero-overlap fallback: same-family-first / most-recently-verified, not slug order. |
| **R6** | **CONFIRM + tightening** | Stub is rule-based, rejects a known-bad fixture, never pass-through; `llm` code default. Tighten: stub selectable ONLY in non-production (mode=stub in prod ⇒ loud one-shot WARN + forced llm); stamp `model_alias="stub"` on every stub verdict; segregate stub cache entries. |
| **R1** | Same ruling as D1 (knobs approved, defaults adjusted) | In shadow, latency costs log gaps not refusals; at P1 enforce the defaults become decisive — set them right now. |
| **Enforcement-switch surface** | **CONFIRMED + key precision** | Daemon-settings surface, DB-backed, `settings:write`, default OFF (decision/kill.py precedent verified). NOT env (env flip = live config change at deploy + restart). **Two sibling keys: `pok_gate.semantic_enforcement` + `pok_gate.softmax_tightening`, both OFF at build** — soft-max is computed + drift-reported from day one (safe), but the TIGHTENING ACTION changes admission outcomes ⇒ rides the switch family. Ceremony flips later via operator route; build makes no live config change. |

---

## 4. Cross-cutting plan defects found (amend the plan, none architecture-blocking)

| # | Defect | Severity | Amendment |
|---|---|---|---|
| 1 | **Phase 2 T2.3/T2.4 "merge via supersede" for `guard-inv`(4) / `prompt-assent`(4) is content-destructive** — four DISTINCT durable rules cannot fit one ≤240-char paragraph; standing POK `pok-sweep-01-merge-cap-split` says SPLIT, never merge-with-drop | 🔴 (Wave 1) | Change T2.3/T2.4 to **split/re-key + archive**, not merge |
| 2 | **Supersede resets counters** (successor verify_count/access_count = 0) — breaks the J trusted-metric chain on every merge | 🔴 (Wave 2) | Counter carry-over (or merge-time verification stamp) at `supersede_and_insert`; put it in Phase 2's repository touchpoint |
| 3 | **Pinned/curated merge target is a dead end** — superseding PINNED/CURATED rows is curator-only (pok_service.py:592-611) and the judge cannot see tier from the digest | 🟡 (0 pinned/curated today) | TOOL pre-checks target tier; untouchable target → convert to an ASK routed to leader/PM instead of hard-fail |
| 4 | **Phase 1 ledger schema as-scoped misses `gate_id`, `phase`, merge-proposal fields** needed by the F2+G1 audit shape | 🟡 | Land them in the Phase 1 table definition (additive now; a migration later otherwise) |
| 5 | **"71 dup-slug→supersede-merge" (P4, plan-overview:62) untraceable** — census says DUPLICATE=2, exact-body dup groups=0 | 🟡 (parked P4, not this build) | Reconcile before P4 sweep planning; if 71 is real the merge population is ~35× the census estimate and F hardens further toward confirmation-gating |
| 6 | **`UpsertOutcome` lacks an ASK variant** (plan gap at the Phase 7/8 boundary) | 🟡 | Add to Phase 7 task list |
| 7 | **Unanswered ASKs are silent losses for non-returning callers** (fail-closed ⇒ the lesson is never written) | 🟡 | Librarian's periodic pass surfaces expired/never-answered gates; audit records ask+expiry (`phase=ASK_EXPIRED`) |

---

## 5. Sanity checks requested (verdicts)

- **Wave1/Wave2 split: SOUND.** Wave 1 = substrate (ledger, family fix, mechanical enforcement, rider, metrics foundation) — every Wave 2 consumer (P7/P8/P10) is tight-coupled to P1/P5 per the coupling map; nothing in Wave 2 can land without Wave 1's ledger+metrics. One ordering note: amendment #2 (counter carry-over) naturally rides Phase 2; amendment #4 (ledger columns) must ride Phase 1.
- **Ownership chain (task → artifact → File cell → leg row → PACKS.md row): PRESENT for all fork-bearing tasks** (T7.6, T8.5, T10.3, T10.4, T10.8 verified with leg+pack rows). GAP: the leg rows currently say "architect picks" — after these rulings they must become **pins on the CHOSEN shapes** with mutation legs (see §6). A plan gate must check the CHAIN, and the chain now has concrete shapes to check.
- **ASK TTL bound:** 1200s default, justified vs `request_timeout=610s` (§2.2).
- **Cache × ASK:** cached ASK re-asks (fresh gate_id); answered outcome never cached as ACCEPT (§2.2).
- **Quick-alias seam:** purpose-bound `POK_GATE_MODEL`, literal alias, never internal pool ids, never `config.llm.model` (§3) — the `model_quick` fallback-rewrite hole is avoided by construction.
- **Enforcement switch:** settings-surface, two sibling keys, both OFF, ceremony-flipable with zero build-time live config change (§3).
- **Deterministic stub:** never a silent pass-through; prod-forced llm (§3 R6).

---

## 6. Pin/mutation implications for the task map (fork pins)

1. **G pin:** `pok_gate_answer` exists in `KNOWN_TOOL_NAMES`; mutation leg: an answer-field branch inside `pok_upsert` → RED (the G2 shape must not creep back).
2. **F pin:** MERGE delta-execution requires a completed ASK answer under `merge_policy=ASK_WITH_PROPOSAL`; mutation legs: auto-execute without confirmation → RED; no-delta merge writing a row → RED (must be verification-stamp only).
3. **TTL pin:** expiry ⇒ `pok_gate_expired`, no write; mutation: expiry-defaults-to-accept → RED (already T11.7 — keep, now bound to 1200s).
4. **Cache pin:** cached ASK replay re-mints gate_id and NEVER returns accept; mutation: ASK-cache-hit → accept → RED.
5. **Soft-max pins:** value lives at `pok.soft_max.v1` in project metadata; mutation: a soft-max/meta row inside the POK store → RED (doctrine pin); "no tightening when active_count < floor" unit (§2.5).
6. **Pass pins:** job registered UN_GATED + in `JOB_MAINTENANCE_SECTIONS`; boot path contains no pass execution (SLO guard).
7. **Counter carry-over pin:** merge/supersede preserves verify_count lineage; mutation: counter reset on supersede → RED.

---

## 7. Compact 5-axis comparison (aggregated)

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **F1** judge-auto-merge | Low | Good | Med | **High** (80-char-slice authorship; silent loss on ACTIVE row) | Lowest | Switch-selectable (`AUTO`) |
| **F2** caller-confirmed w/ proposal ✅ | Med-Low | Good | **Best** (subsumes F1/F3) | Low-Med | Med | **DEFAULT** |
| **F3** always-escalate | Med-Low | Poor at soft-max | Med | Lowest | Highest | Switch-selectable |
| **G1** dedicated answer tool ✅ | Med | Neutral | Good | **Lowest** (no payload laundering; schema fence intact) | Low (registry churn only) | **PICK** |
| **G2** answer-field | Med (dual-mode) | Neutral | Poor | **Highest** (laundering; reason-REQUIRED weakened; ping-pong) | Lowest | REJECT |
| **H1** maintenance job only | Low | Good | High | Low | Low | Subsumed |
| **H2** on-write only | Med | Poor-Med | Low | High (hot-path; no drift cadence) | Med | REJECT |
| **H3** hybrid ✅ | Low-Med | Good | High | Low | Low | **PICK** |
| **I1** project metadata ✅ | Lowest | Good | High | Low | Lowest | **PICK** |
| **I2** computed/ledger | Med-Low | Med | Med | Med (retention can erase state) | Med | Fallback only |
| **I3** curated POK row | Low code | Poor | Low | **High** (doctrine-wrong: rolling state in POK) | Highest | REJECT |

---

## 8. Risks (aggregated, deduped, highest severity first)

- 🔴 **Cached-ASK→silent-accept** (Emperor's catch): ruled out by construction (§2.2) — pin it (§6.4).
- 🔴 **Merge resets metrics** (trust-chain break): fixed by amendment #2 — without it, J's trusted verify_count is structurally self-defeating.
- 🔴 **Shadow answer-path ships dark**: T8.4–T8.7 must be unit-tested in enforce mode; do not trust shadow evidence for the answer path.
- 🟡 **Cold-start tightening trap**: floor ≥ current-active band + unit test (§2.5).
- 🟡 **D1 tail at enforce**: 45s worst case inside a 180s-heartbeat turn; measure fail-closed rate in shadow before P1.
- 🟡 **Starvation if registered IDLE_GATED**; **TTL too short ⇒ write-blocker**; **97 `svc.upsert` sites** if defaults are omitted; **rubber-stamped proposals** (mitigate: full target body in payload; ledger records proposal AND final body for edit-distance measurement).
- 🟢 Pass cost ms-scale at 1024 rows; I1 read sub-ms; G1 no meta.json/grant churn; H3 reuses the persisted-clock machinery.

## 9. Confidence

F **Med-High** (High on the shape — F2's data model subsumes the rivals; Medium on the execution default, deferrable to P1 evidence — but the ledger schema makes every outcome measurable either way). G **0.8**. H **0.85**. I **0.9**. J **High**. D1 overturn **High** (measured + historical precedent). Flip assumptions stated per-fork above; the single assumption that most weakens the set: **the measured quick-latency distribution (2.6–13.6s, 4/8 >15s, probed 2026-09-07) still describes today's proxy tail** — re-measure in shadow before P1.

## 10. Decisions pending (for the leader/Emperor — not blocking build)

1. **P4 "71 dup-slug" reconciliation** (defect #5) — needed before the parked sweep is planned, not before this build.
2. **`merge_policy` default at P1**: stay `ASK_WITH_PROPOSAL` vs promote to `AUTO` — decided by shadow evidence (merge rate + proposal fidelity), which the ledger now records by design.
3. **Feeder grants (experiencer/kb-importer)** — already assess-and-report; unchanged by this doc.
