# Fold Ledger — metrics-agent-tools

Status: the fold table with provenance. §A = IN folds (with their exact fold site in this plan set); §B = OUT exclusions (with reasons + citations); §C = the provenance-gap protocol (P-2); §D = recorded deviations; §G = the implementation-tidier backfill placeholder (P-2 — filled during the implementation lane's own tidier pass, never before, never invented).

---

## §A — IN folds (what this lane folds in, and where)

### A.1 — Fold (a): the FE `registry_read_ok` mirror (the routed `fe_registry_read_ok_mirror` residual)

| Field | Value |
|---|---|
| **Verdict** | **IN — THIS LANE** (Phase 4, tasks 4.1–4.8) |
| **Provenance (in-repo, verbatim)** | `.agents/tidier/notes.md:760` — *"Known residual for the next lane: H5's additive `registry_read_ok` is NOT carried by the FE mirror (MetricsActivation is 3 members; the BE now sends 4). Contract-compliant per §4, but the FE still renders a failed registry read as an empty catalog."* AND `.agents/tester/RESULTS/2026-11-02-central-metrics-v1-functional-gate.md:107-111` — the Known-and-routed block names *"FE `registry_read_ok` mirror (next lane)"*. **This lane IS the "next lane"** (item #2 of the ratified metrics sequence). |
| **The defect** | the BE publishes FOUR activation members (`daemon/services/metrics_query/envelope.py:56-71`, member at `:70`; flips false at `provider.py:152-160` when the registry read RAISES); the FE mirror declares THREE (`frontend/src/app/models/metrics.model.ts:111-119`); the FE shell never reads `payload.activation` (`frontend/src/app/pages/metrics/metrics.component.ts:186-216` — neither the success nor the error branch), so a FAILED registry read (200 + empty `families`) is byte-for-byte indistinguishable from a legitimately empty catalog (`metrics.component.ts:127-129` ⇒ the empty panel at `metrics.component.html:33-41` renders; the Families tab's own empty line at `families-tab.component.ts:90-92` agrees). |
| **Target files (from research-folds §1)** | `metrics.model.ts:111-119` (the mirror) · `metrics.component.ts:186-216` (loadCatalog) + `:127-129` (showEmptyPanel) + `:91-99` (signals) · `metrics.model.ts:352-362` (the copy-map neighbourhood — the new page-local copy constant) · `metrics.component.html:9-21` (the existing banner neighbourhood — NOT reused; see §D-1) + `:23-31` (the badge precedents) · spec fixtures: `metrics.component.spec.ts:47-59` (`envelope()`, `activation` at `:53-57`), `metrics.model.spec.ts:265`/`:302`, `frontend/e2e/metrics-gate.spec.ts:33-43` (`ENVELOPE`, `activation` at `:38-42`) · **size: ~25-40 FE lines** + fixture lines. |
| **The BE/tool twin** | the SAME honesty defect on the agent side is closed by the design's Section A: D-UX-8's uniform `read_status: {registry_read_ok, mode, note}` block on all three tools (research-folds §1.5 F5 declares this NOT-an-exclusion). Fold site: Phase 2 tasks 2.2/2.3 + the Phase 5 ladder/persona pins. |
| **The shape ruling** | **P-3** (binding): optional member + `=== false` predicate; DISTINCT badge/hint; never the error banner. |

### A.2 — The Protocol-completeness first-trip item (the house lesson)

| Field | Value |
|---|---|
| **Verdict** | **IN — THIS LANE** (Phase 1, tasks 1.1/1.6) |
| **Provenance** | the house lesson from the central-metrics tidier pass — `.agents/tidier/notes.md:762` (research-folds §2.1 table): *"a Protocol that no consumer references guarantees nothing — extend the Protocol BEFORE typing the consumer, or the consumer resorts to `Any` + getattr and the Protocol's incompleteness stays invisible."* |
| **The implementation** | the design's ADDITIVE Protocol amendment (D-UX-13): `now`/`max_series` kwargs on `history()` + the `registry_read_ok` property — **AmRULING-3 trim (B2): GAP-1/GAP-2 + GAP-3 only; `listener_enabled`/`census_enabled` are DROPPED (no v1 consumer; the tool consumes `activation_block(provider)`)** (`technical-analysis.md` §A.2, the member-by-member GAP table at :144-160 and the amendment block at :161-182) — this lane IS the Protocol's first consumer. |
| **The numeric-fact rider** | the two numeric facts stay OUT of the Protocol by design; the seam's own tolerant reader is promoted (`envelope.read_numeric_fact = _provider_float`, `envelope.py:135-146`; `effective_interval_s` at `:149-151`) — Phase 1 task 1.2. |
| **The pin** | Phase 1 task 1.6's Protocol-completeness pin (mutation-meaningful: deleting a declaration reds it) — the lesson's whole point, made durable. |
| **The audited cost** | exactly two fakes grow members (`tests/helpers/metrics_query_fixtures.py:47` `RecordingProvider` → `registry_read_ok = True`; `tests/unit/metrics/test_metrics_query_provider.py:557-572` `_Fake` → **EXACTLY ONE member (`registry_read_ok = True`)** — the fake cost SHRINKS three booleans → ONE (B2/AmRULING-3: the trimmed Protocol declares no GAP-4/5 members)) — both already accept the new kwargs (design §A.2 :192-196). Phase 1 task 1.5. Risk register R2. |

### A.3 — The D6 / DEF-3 scope split (the named commitment)

| Field | Value |
|---|---|
| **Verdict** | **IN — THIS LANE** (Phase 3; the D6 trigger is MET — the shipped API doc names "the deferred agent-tool door" as the mint trigger) |
| **Provenance** | `central-metrics-v1/decisions.md:641-658` (**D6**, leader LR-1: *"binding guidance for the deferred lanes"* + the 4-line retrofit map + the recorded trigger) · `central-metrics-v1/fold-ledger.md:119` (**DEF-3**, owner "the deferred agent-tool / Grafana lanes") · `metrics-query-api.md:286-292` (the public restatement naming this lane) |
| **Fold site** | Phase 3 tasks 3.1-3.8; bounded by **P-1 / E-1 ADJUDICATED 2026-11-03 as amended by E-1 amendment-2** (the amendment is PRIMARY — 3.6b, same-commit, the FIVE canonical ranges, guardrails (a)-(d) as restated; the debt-record path only as the guardrail-(d)/no-viable-wording fallback) and **P-4** (route censuses do NOT move). |

---

## §B — OUT exclusions (fenced out of this lane, with reasons)

Source of the routed list: `.agents/tester/RESULTS/2026-11-02-central-metrics-v1-functional-gate.md:107-111` (the verbatim Known-and-routed block) + the central-metrics-v1 lane record's `routed_followups`; the full per-item table is research-folds §4.2/§4.3.

| Id | One-line description | Target file:line | Owning lane | Why OUT of this lane |
|---|---|---|---|---|
| **R-i** | the census `gated` polarity is INVERTED against the contract truth table in BOTH switch states (the docstring says "gated OFF", it returns True when work is ALLOWED) — a family publishing 15 live series reports dark/`census_gated`; zero behavioural pins (the switch-invariance pack pins against a FAKE census) | `daemon/services/metrics_census.py:323-335`; the provider implements the docstring at `daemon/services/metrics_query/provider.py:104-109` | the metrics-collection lane | the tool TOLERATES it (the generic dark-with-live caveat, D-UX-7) and never fixes or attributes it; a fix here would be redesign + a polarity lane of its own (research-folds §2.1 the escalation note) |
| **R-ii** | `capture_active` is never wired — the provider is constructed without it, so F1/F3 report `capture_off` unconditionally | construction site `daemon/api.py:2527-2533` → `daemon/services/metrics_query/provider.py:71,75` | metrics-collection / mechanical-file lane | same tolerance rationale; the caveat text is GENERIC precisely so it does not encode this defect (risk R4) |
| **R-iii** | the F7 non-internal histogram never publishes a snapshot | `wiring/w2.py:13/:56/:74`; `wiring/registry.py:141-142`; the adjudication row at `wiring/adjudication.py:27` | the canonical-registry lane | a producer-side defect; the tool reads published state only (read-path purity) and carries `series_absent` verbatim |
| **R-iv** | `stale_pause_sweep` never publishes to app state; its counters readers are missing (the family renders no series) | `wiring/sources.py:224`; `daemon/services/stale_pause_sweep.py:436`; `daemon/api.py:3058-3074`; `daemon/services/pool_orchestrator.py:341-350` | sweeps + mechanical-file lanes | same producer-side rationale |
| **`family_a_dbpool_baselines`** | the family-A DB-pool baselines are stale at base (`438/439` vs observed `466/467`; last set 2026-10-29 pre-lane; zero family-A sites in `metrics_query`) | the baselines file (family A); `wiring/adjudication.py` (`FAMILY_F_SITES`) | the base-debt lane | pre-existing base debt, disclosed not-gated; untouched by this lane's diff |
| **`pg_census_fence_shadow`** | the PG-census pack's 1 red (`test_the_fence_rejects_a_disallowed_db_name_before_connecting`) is pre-existing at base (a shared autouse fence shadows the module marker); a fix shape is provided by the tester | `tests/postgres/test_metrics_census_pg.py` (the autouse fence) | the owning lane (functional-gate RESULTS `:99-101`) | pre-existing at base; this lane adds no PG code and runs no PG leg |

| **`contract_RevocationLever_prose`** (advisory A2) | the frozen contract's `:58-60` carries `maintenance:read` revocation-lever prose that goes STALE post-split (the door's emergency-off lever prose names only `maintenance:read`) | `docs/architecture/metrics-query-contract.md:58-60` | the contract owner | **KNOWN-RESIDUAL — deliberately NOT edited**: `:58-60` is OUTSIDE the five canonical ranges and guardrail (a) holds; documented divergence owned by the contract owner, the same class as the E-1 fallback debt record |

| **`contract_activationShape_3v4`** (approver N10 / research F6) | the frozen contract's activation shape at `:104-106` lists THREE members while the BE emits FOUR (`registry_read_ok`, `envelope.py:70`) — pre-existing doc drift | `docs/architecture/metrics-query-contract.md:104-106` | the contract owner | **KNOWN-RESIDUAL — NOT edited** (outside the five canonical ranges); recorded so the task 6.4 sweep sees it |

Also OUT (research-folds §4.3): **`R-v`** (the house canon comparator — already landed in-repo; **inherit, do not re-do**) and the Grafana layer (**item #3**, deferred by D6's own second deferred-lane designation).

---

## §C — The provenance-gap protocol (P-2): the six numbered tidier Mediums

**The gap.** The central-metrics-v1 lane record routed **27 standing tidier Medium findings**, flagging **items 3, 5, 10, 16, 17, 21** as "first-trip for the agent-tool lane". The tidier's per-finding numbered list **does not exist in this tree** — proven by research-folds §2.2's search table (no `central-metrics-v1/tidier-report.md`; `registry_read_ok` appears in exactly 2 `.agents` files; no `T-M<n>` markers in code; `.agents/tidier/` holds only `notes.md` + a scratch file; the tidier's write scope is `.agents/tidier/` only, `agents/tidier/rule.md:22`). Only the aggregate + headline Mediums exist at `.agents/tidier/notes.md:750-763`.

**The protocol (binding, P-2).** The plan does NOT invent mappings. During THIS lane's implementation, the lane's own tidier pass (post-code-council) records into §G below:
1. for each of items **3, 5, 10, 16, 17, 21**: the item id → its finding text (verbatim where obtainable from the tidier lane/leader, or re-derived with the explicit caveat that re-running the lens over `1497bf51f..c3a003668` will NOT reproduce the original numbering), its target file:line, and its disposition (fold-here / still-routed, with the owning lane named); or
2. if the list cannot be recovered: §G records THAT outcome verbatim, and the six items stay routed exactly as the central-metrics-v1 lane record left them.

No third option exists. An invented mapping would fabricate gate obligations and is a plan violation.

---

## §D — Recorded deviations (where this plan deliberately departs from a research-proposed shape)

| # | Deviation | The research-proposed shape | The binding shape | Authority |
|---|---|---|---|---|
| **D-1** | the FE degraded surface | research-folds §1.5 F3: reuse the existing `metrics-catalog-error` banner with a distinct `data-test` | a DISTINCT badge/hint element (`data-test="metrics-registry-degraded"`) — the error banner is NEVER used for the degraded state, so the `:15` 200-EMPTY pin stays true | **P-3**; Phase 4 task 4.3 |
| **D-2** | the contract-prose amendment timing | the design §E recommended PROCEED (amend the prose now, cited, under D6's authority) | DEFAULT = no prose edit; the divergence logged as contract-owner debt; the amendment is the CONDITIONAL APPENDIX behind explicit lane-owner confirmation. **SUPERSEDED 2026-11-03 by the P-1 / E-1 adjudication (lane owner): the amendment is PRIMARY (3.6b, same-commit, guardrails (a)-(d)); this deviation row now stands ONLY for the fallback path (guardrail (d): a substantive council/approver objection → re-adjudication → 3.6a).** | **P-1**; Phase 3 task 3.6 |
| **D-3** | the factory's phase placement | the dispatch skeleton placed the factory in Phase 1 (seam) | the factory + `*_if_available` wrapper + extend live in Phase 2 with the tool module they import — the frozen-drift pin's source closure stays green within ONE commit | the grouping rationale (plan-overview §Phase map); Phase 2 tasks 2.1/2.6/2.9  **(SUPERSEDED-IN-PART by AmM-3/RULING-1 — decisions.md Part 5: the `_if_available` wrapper is DROPPED; the factory called directly; tools stay present on degraded boot.)** |
| **D-4** | the watch-outline doc authoring time | D-WATCH-1: authored during the FUTURE lane's implementation | authored during THIS lane's close-out (Phase 6 task 6.1) from Section C verbatim — still planning no machinery | **P-7** |

---

## §F.1 — Approver iteration 001 (REJECTED; both workers; tracking file `.agents/approver/metrics-agent-tools-tracking.md` — READ-ONLY, not edited)

Dispositions (iteration 002 resubmission):

| Finding | Disposition | Sites edited |
|---|---|---|
| **B1** (E-1 amendment-2 count not propagated to the phase-3 gate criterion) | **FOLDED** — `phase3-plan.md` E-1 success-criterion row corrected to the FIVE canonical line-ranges (`:387` IN); W1's propagated-site list widened to name EVERY reached site incl. this one + `:387` | `phase3-plan.md` task-table success criterion (the "E-1 discharged" row) · `fold-ledger.md` W1 row |
| **B2** (AmRULING-3 stale three-boolean shape at four sites) | **FOLDED — all four:** plan-overview In-Scope item 1 (entry-point scope statement → GAP-1/2+3 only); plan-overview R2 mitigation (`_Fake` grows EXACTLY ONE member); fold-ledger §A.2 The-implementation (GAP-4/5 DROPPED); fold-ledger §A.2 The-audited-cost (one member). W2's row widened site-by-site + the semantic footprint recorded (activation_block getattr + the 1.6(iii) block-equality pin) | `plan-overview.md` In-Scope item 1 + R2 · `fold-ledger.md` §A.2 ×2 + W2 row |
| **B3** (manifest ≠ tree: `architecture-recommendation.md` omitted) | **FOLDED** — the row added to BOTH the §Provenance Inputs table and the §Plan-file manifest (15/15 files) | `plan-overview.md` both tables |
| **N1** | **FOLDED** — the provenance rows refreshed: 1757 L, pointer-amended only (SEVEN pointers cited), not "untouched" | `plan-overview.md` Provenance + manifest rows |
| **N5** | **FOLDED** — NEW Phase-1 task **1.0** entry residue-sweep (concept-token greps for BOTH variants of every folded enumeration + manifest-vs-ls); task 6.4 re-runs it at close-out | `phase1-plan.md` task 1.0 · `phase6-plan.md` task 6.4 |
| **N9** | **FOLDED** — task 2.8's A6 text now precise: stale AT BASE (says fifteen; set holds sixteen; count pins assert 16) → becomes SEVENTEEN with metrics-read | `phase2-plan.md` task 2.8 |
| **N11 → PL-4** (numbering reconciled: the owner directive's "N11" = the tracking file's N5-class entry-sweep, folded as N5 above; the tracking file's N11 = the `read_numeric_fact` export reconciliation) | **FOLDED — planner ruling PL-4:** KEEP the direct `envelope`-module import; `__init__.py` NOT widened; TA :190 inline pointer + task 1.2 clarifying clause | `decisions.md` Part 5 PL-4 · `technical-analysis.md` :190 pointer · `phase1-plan.md` task 1.2 |
| **N12** | **FOLDED** — `test_service_tool_flag_off_byte_identical.py:814` ("the EIGHT-site pin") added to task 2.8's same-commit docstring/count-fix list (sibling of A6/N9) | `phase2-plan.md` task 2.8 |
| **N10** | **FOLDED** — F6 (contract `:104-106` activation shape 3-vs-4) recorded as a §B known-residual + a plan-overview out-list row | `fold-ledger.md` §B · `plan-overview.md` Out of Scope |
| **N6** | **FOLDED** — the one-line rollback posture statement for the ceremony record | `plan-overview.md` (after the completion test) |
| **N2** | **FOLDED** — §D-3 supersede note (pre-RULING-1 factory shape; superseded by AmM-3, no content rewrite) | `fold-ledger.md` §D-3 |
| **N3** | **FOLDED** — in-place supersede pointers at Part 1 D-UX-12 (`_if_available`/returns-`[]`) and D-UX-13 (the three-property amendment), pointer-only | `decisions.md` Part 1 |
| **N4** | **FOLDED** — task 1.7 names the venv provisioning step (`uv sync` in the worktree, lazy-venv rule) before the first pack run | `phase1-plan.md` task 1.7 |
| **N7** | **LEFT RECORDED (no action)** — already a §B known-residual row with the owner named (`contract_RevocationLeverProse`); disclosed | `fold-ledger.md` §B |
| **N8** | **LEFT RECORDED (no action)** — the historical block whose supersede note already follows immediately; not a live contradiction | `decisions.md` Part 4 |

**Worker credit:** both approver workers' structural re-derivations HELD (route censuses; 15+1 pin arithmetic; the three api.py handle sites; any-of semantics; the in-memory read path; AmM-1 arithmetic; the VERSIONED_PROMPT_ARTIFACTS exclusion; FE mirror facts; the five line-ranges; the six pinned blocks).

**Calibration:** remediation mechanical — 5 one-line text fixes + 1 manifest row + the named notes; no redesign, no scope implication. Iteration 002 resubmission follows; cap 3.

---

## §F.2 — Approver iteration 002 (APPROVED FINAL) — the advisory dispositions

The iteration-002 notes (`i2-N1..N10`, all non-blocking) are folded at their named points. The W1 column records
what landed in the **W1 commit** (the planning-dir + approver-artifact commit); the rest fold at the task named.

| Note | Disposition | W1 (plan-doc one-liners) | Folded at |
|---|---|---|---|
| **i2-N1** (phase-map task-count drift: Phase 1 = 7 → 8) | **FOLDED** — the phase-map cell now reads 8 (tasks 1.0–1.7) | `plan-overview.md` §Phase map row 1 | — |
| **i2-N2** (In-Scope item 1 understates the lifespan touch set) | **FOLDED** — "one-line lifespan mount" → the **THREE-site** mount (`api.py:2533` mount / `:2541` boot-degradation / `:3778` shutdown) + the lifecycle-triple pin (task 1.6(ii-c)) | `plan-overview.md` In-Scope item 1 | — |
| **i2-N3** (the verbatim-preservation list covers only the six fenced blocks) | **FOLDED** — the list is widened to the pack's ~18 PROSE literals, the load-bearing one named (`checkpoint-cleanup-EXECUTE` at `:404`, INSIDE the canonical range `:404-405`), plus the explicit consequence ("a rewrite that drops any of these is NOT a viable wording") | `phase3-plan.md` task 3.6 · `phase5-plan.md` task 5.8(iv) | — |
| **i2-N4** (no FE toolchain provisioning step) | **FOLDED** — task 4.7 gains an explicit `cd frontend && npm ci` provisioning step (a TOOLCHAIN step, not a dependency change: `package.json` + `package-lock.json` stay byte-identical) and task 4.8 records that the gate leg ASSUMES the provisioned tree | — | `phase4-plan.md` tasks 4.7/4.8 |
| **i2-N5** (the entry sweep names historical exemptions only for pairs (i)/(iii)) | **FOLDED** — the historical-exclusion clause is added: a hit inside a block marked superseded/historical, or inside a READ-ONLY input (`technical-analysis.md`, `architecture-recommendation.md`, the three `research-*.md`), is EXEMPT and NEVER triggers an edit to a frozen input; the pairs (ii)–(iv) sites are named | — | `phase1-plan.md` task 1.0 |
| **i2-N6** (the `contract_RevocationLeverProse` residual must be stated plainly in the ceremony text) | **FOLDED** — task 6.3 gains an explicit DISCLOSURE line: the `:58-60` emergency-off-lever prose still names only `maintenance:read`, the `metrics:read` grant is revoked by the same token-revocation path, and the residual is contract-owner-owned and is NOT a wire/behaviour defect | — | `phase6-plan.md` task 6.3 |
| **i2-N7** (observation-dependent counts are not data) | **FOLDED** — task 6.4 states that §A.4's rationale figures are observation-dependent, are NOT pins, and MUST NOT be asserted; task 5.4 asserts shapes/vocabularies only | — | `phase6-plan.md` task 6.4 |
| **i2-N8** (the "1800 s wedge trigger" truth-in-docs blemish) | **FOLDED as a POINTER amendment** (the design is a BINDING INPUT, pointer-amended only — NO content rewrite): both sites now carry the inline correction (`DEFAULT_LAG_S = 5.0` sustained `DEFAULT_LAG_SUSTAIN_S = 15.0` at `wedge_capture/config.py:100-101`; `1800 s` is the self-alarm `MIN_HORIZON_SECONDS`); the parameter choice is unaffected | `technical-analysis.md` :461 + :1582 (pointer-style, the established 7-pointer pattern) | — |
| **i2-N9** (cite imprecision: `envelope.py:64` → `:70`) | **FOLDED** — the `getattr` default cite now reads `envelope.py:70` | `decisions.md` Part 4 P-3 | — |
| **i2-N10** (harmonize "16 sites" vs "15 EDIT + 1 VERIFY-ONLY") | **FOLDED** — every NORMATIVE plan occurrence is harmonized onto "10 files / 15 EDIT sites + 1 VERIFY-ONLY"; the registry doctrine's "16 assertion sites" is retained only where it quotes `_tool_registry.py:314-356`; the READ-ONLY research inputs are NOT edited. The 6.4 sweep carries the check | `plan-overview.md` In-Scope item 2 + R1 mitigation · `phase2-plan.md` §Objective + ONE-commit-cluster · (sweep item) `phase6-plan.md` task 6.4 | — |

**House note:** every W1 edit above is a PLAN-DOC edit (the plan set is the lane's own working surface). The ONE
edit outside the plan set is the `technical-analysis.md` POINTER amendment (i2-N8) — pointer-style only, matching
the SEVEN inline fold pointers the plan already records; the design's prose is NOT rewritten.

---

## §H — The implementation lane's running log (Phase 1)

### H.1 — Task 1.0 PHASE-1-ENTRY residue sweep (approver N5 / i2-N5)

Run BEFORE any Phase-1 work; re-run at close-out by task 6.4. Every grep +
its hit count is recorded; the exemption rule is the i2-N5 clause (a hit
inside a block marked superseded/historical, or inside a READ-ONLY input,
is EXEMPT and NEVER triggers an edit to a frozen input).

| # | Concept-token pair | Grep | Hits | Disposition |
|---|---|---|---|---|
| (i) | `4 cited` / `four cited` / `exactly the 4` vs `FIVE canonical` | `grep -rn "4 cited\|four cited\|exactly the 4\|the 4 cited\|4 prose places" *.md` | **4** — `decisions.md:184` (the ORIGINAL guardrail-(a) enumeration inside the marked historical block; its supersede note follows at `:189`) · `decisions.md:195` (the amendment-2 ruling QUOTING the original ambiguity — a historical record) · `decisions.md:202` (the marked P-1 pre-adjudication record) · `phase1-plan.md:23` (the sweep task's own enumeration — self-referential, not a normative variant) | **0 LIVE HITS** — all four are historical/self-referential and EXEMPT under the i2-N5 clause |
| (ii) | `three booleans` / `three facts` / `three properties` vs the ONE-member GAP-1/2+3 shape | `grep -rn "three booleans\|three facts\|three properties\|all three properties\|the three booleans" *.md` | **5** — `fold-ledger.md:29` (the audited-cost row: "SHRINKS three booleans → ONE" — the CORRECT fold wording) · `fold-ledger.md:213` (the W2 propagation list) · `phase1-plan.md:23` (the sweep's own text) · `phase1-plan.md:28` (task 1.5: "the fake cost SHRINKS from three booleans to ONE" — the CORRECT wording) · `plan-overview.md:109` (R2 mitigation: "the fake cost SHRINKS three booleans → ONE") | **0 LIVE HITS** — every hit is the CORRECT fold wording (describing the REDUCTION), plus the sweep's own text |
| (iii) | `quintet` vs `six-member bound block` (AmB-14) | `grep -rn "quintet" *.md` | **6** — `architecture-recommendation.md:83` (READ-ONLY architect input) · `decisions.md:39` (the Part 1 VERBATIM fold of D-UX-6) · `decisions.md:283` (the AmB-14 supersede row) · `fold-ledger.md:184` (the B-14 fold row) · `phase1-plan.md:23` (the sweep's own text) · `technical-analysis.md:224` (the POINTER-amended design line) | **0 LIVE HITS** — the Part 1 VERBATIM fold + the design's POINTER amendment + two READ-ONLY inputs + two fold/supersede rows; the pointer amendment already states the six-member form |
| (iv) | `sixteen` / `fifteen` count wording vs the 15-EDIT+1-VERIFY / 16→17 arithmetic | `grep -rn "sixteen categories\|fifteen categories\|exactly sixteen\|exactly fifteen\|EIGHT-site" *.md` | **2** — `fold-ledger.md:98` (the N12 disposition row, quoting the stale docstring) · `phase2-plan.md:31` (task 2.8's binding header — the CORRECT "15 EDIT sites + 1 VERIFY-ONLY") | **0 LIVE HITS** |
| (v) | manifest-vs-ls (approver B3's class) | `ls *.md` vs the plan-overview §Plan-file manifest rows | **tree 15 / manifest 15** — the three `research-*.md` share ONE manifest row (a combined row), so the naive single-name parse under-counts; the manifest covers every file | **MATCH** |

**Verdict: ZERO LIVE (non-historical) hits. The Phase-1 entry gate PASSES.**

### H.2 — Phase-1 Tier-2 scoped dual-side leg (task 1.7)

* **D1** (HEAD-only, base-absent by construction): the new seam pack
  `tests/unit/metrics/test_metrics_query_protocol_consumer.py` — **28 nodes, 28 passed**.
* **D2** (dual-side comparable): the DERIVED metrics blast radius — the
  `metrics_p4_w1_dual_side` 21-file set + the 13 widened P3 files + four
  `manager.py`-adjacent unit packs + the WHOLE `tests/unit/metrics/` corpus
  = **30 file arguments**. HEAD **1032 collected / 1029 passed / 3 failed**;
  BASE **1004 collected / 1001 passed / 3 failed** (the +28 delta is exactly
  the D1 seam pack).
* **FAILED-list diff**: the three FAILED node-ids are **IDENTICAL** on both
  legs and the extracted error-line digests are **byte-identical**
  (`md5 a942dc2d6469275924af14b977148515` on both legs) ⇒ **net-new
  deterministic reds = 0**, every residual base-proven:
  * `tests/unit/metrics/test_metrics_family_adjudication.py::TestNoProducerInstrumentation::test_the_daemon_diff_is_confined_to_the_allowed_files` — `AssertionError: daemon/__init__.py`
  * `tests/unit/persistence/test_checkpoint_metrics_vocabulary.py::TestSweepLaneVocabulary::test_rdrs_lanes_come_from_the_config_kill_switchs` — the RDRS lane-count drift
  * `tests/unit/test_phase4_manager_decomposition.py::TestFacadeDelegationPattern::test_manager_terminate_instance_delegates_to_lifecycle_service` — `expected call not found.`
* **Changed-pack legs**: `central_metrics_contract_doc` — **21 collected, green** (the contract-doc pack is unaffected by the seam amendment; the E-1 amendment lands in Phase 3).
* **Whole-tree collection census**: **4855 collected + 1 collection ERROR, IDENTICAL on both legs** ⇒ the collector aborts on the KNOWN base debt (`tests/packs/g7_unique_index_smoke_test.py:121` — a module-level `sys.exit(0)`, the "g7" item routed out of the v0.27.13 lane). The whole-tree RUN leg was therefore SCOPED to the derived metrics blast radius + the core-seam-adjacent packs (the plan's task 1.7 wording: "run the scoped dual-side FAILED-list diff vs base"). DISCLOSED, not hidden.
* **Fences**: every leg ran with the EXPLICIT per-variable scrub
  (`env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
  -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT`), `-p no:randomly`,
  `--timeout=120`, `timeout 900` outer. The worktree-local
  `.venv/bin/python` (3.14.7) was provisioned by `uv sync` (lazy-venv rule).
* **Throwaway base worktree**: `/home/nea/ensemble-src-wt-base-metrics`
  (detached at `03a2297c3`, the branch base), created + owned + torn down by
  the IMPLEMENTER (council A9) — see H.3 for the teardown confirmation.
* **Mutation-meaningfulness spot-check**: planting a deletion of the
  `registry_read_ok` declaration reds the completeness pin (3 nodes RED),
  and the restore is byte-identical.

---

## §G — The implementation-tidier backfill (placeholder — DO NOT fill before the tidier pass)

> **Status: EMPTY by design (P-2).** This section is filled by the implementation lane's tidier pass per the §C protocol.
>
> | Item | Finding text | Target file:line | Disposition | Provenance |
> |---|---|---|---|---|
> | tidier Medium **3** | *(to be recorded by the tidier pass — never invented)* | — | — | — |
> | tidier Medium **5** | *(to be recorded)* | — | — | — |
> | tidier Medium **10** | *(to be recorded)* | — | — | — |
> | tidier Medium **16** | *(to be recorded)* | — | — | — |
> | tidier Medium **17** | *(to be recorded)* | — | — | — |
> | tidier Medium **21** | *(to be recorded)* | — | — | — |
>
> If the numbered list cannot be recovered, record that outcome here verbatim instead of any row above.

---

## §E — Architect fold round (2026-11-03)

Source: `architecture-recommendation.md` (READ-ONLY input — verdict SOUND-WITH-AMENDMENTS, zero blocking). Every ID below is folded per the consolidated fold-map; decisions content lives in `decisions.md` **Part 5 — Architect-fold amendments**; the E-1 adjudication block + guardrails are BYTE-INTACT throughout (AmA-8 lands in the API doc, not the contract amendment).

| ID | Cluster / class | Disposition | Landing site(s) |
|---|---|---|---|
| **M-1** (B-5+C-2) | output-budget enforcement — 🟡 must fold | **FOLDED** — binding tool design: (a) returned-leg clamp ≤ MAX_TOTAL_POINTS=12 000 (kills the 12 240 > 12 000 breach); (b) compare leg's transient provider work declared separately (22 %-not-11 % framing corrected); (c) tool-local byte ceiling with the door's trim + six-member bound block; (d) default-call agent-context cost in CATEGORY_DOC. Amends D-UX-4 (Part 5 AmM-1); design inline pointer at the budget claim | `decisions.md` Part 5 AmM-1 · `phase2-plan.md` tasks 2.2/2.4 · `phase5-plan.md` task 5.2 (clamp/byte-ceiling mutation legs) · `technical-analysis.md` inline pointer (budget claim) |
| **M-2** (B-8+C-5+C-10) | freshness semantics — 🟡 must fold (one root cause, three landing sites) | **FOLDED** — (i) per-series `last_sample_age_s`/`points_span_s` on history rows; (ii) explicit ring-state signal (`trend_empty_since_restart`) in `read_status`; (iii) the outline's §C.2 "Gap: none" cell corrected (design-only) | `decisions.md` Part 5 AmM-2 · `phase2-plan.md` tasks 2.2/2.4 · `phase5-plan.md` task 5.2 (empty-ring + per-series legs) · `phase6-plan.md` task 6.1 (the outline correction) |
| **M-3** (C-3) | mount/unavailable contradiction — 🟡 must fold, resolved by RULING-1 | **FOLDED** — Option A: tools stay PRESENT on degraded boot; body reads the mount per call; typed `metrics-unavailable` refusal; `_if_available` dropped from the factory name; registration-pack degraded-boot leg; task 1.6(ii) identity pin stands | `decisions.md` Part 5 AmM-3 + RULING-1 · `phase2-plan.md` tasks 2.1/2.6/2.9 · `phase5-plan.md` task 5.3 |
| **A-1** | citation hygiene | **FOLDED** — D-SCOPE-2's bypass cite corrected to `instance.py:550-560` + `:561-568` (the `_tool_registry.py:559-560` range is a comment) | `decisions.md` Part 5 AmA-1 |
| **A-2** | dead-tool hazard (unthreaded `agent_id`) | **FOLDED** — task 2.6 states the factory passes `agent_id=agent_id` (`instance.py:3017`); registration-pack leg asserts the THREADED value | `phase2-plan.md` tasks 2.6/2.9 · `decisions.md` Part 5 (RULING-1 constraints) |
| **A-3** | two-set divergence | **FOLDED** — equality leg added: `{agents whose meta.allow holds "metrics-read"} == METRICS_AUTHORIZED_AGENTS` | `phase2-plan.md` task 2.9 |
| **A-4** | anti-relitigation | **FOLDED** — precedent-composition rationale (read = 2 layers; write/act = +kill-switch/ledger) folded into D-SCOPE-2 | `decisions.md` Part 5 AmA-4 |
| **A-5** | deterministic RED + false criterion | **FOLDED** — `tests/unit/metrics/test_metrics_query_auth_matrix.py:114-131` exact-set amended to the any-of set; the file ADDED to D-SCOPE-6's pin list; task 3.5's "original rows unchanged" criterion rescoped to the non-amended rows | `decisions.md` Part 5 AmA-5/A-6/A-7 · `phase3-plan.md` task 3.5 |
| **A-6** | truth-in-pins | **FOLDED** — `:133-137` test + file docstring `:3-15` rename/reword SAME-COMMIT as 3.2 | `phase3-plan.md` task 3.5 · `decisions.md` Part 5 |
| **A-7** | truth-in-docs (routers/metrics.py) | **FOLDED** — docstrings `:32,:53,:55,:62-63,:180,:190` + the four OpenAPI 403 descriptions `:153,:564,:609,:677` folded into task 3.2 (description text only) | `phase3-plan.md` task 3.2 · `decisions.md` Part 5 |
| **A-8** | forward-declared item-#3 seam — 🟡 substantive | **FOLDED (per the dispatch ruling: API-doc path, NOT the contract amendment — E-1 guardrails byte-intact)** — new Phase 3 task 3.8 adds the item-#3 debt entry to `metrics-query-api.md:280-292`; task 3.5 row (ii) labelled a REVERSAL pin; `tests/unit/test_router_scope_manifest.py:169` named as the second-amendment site | `phase3-plan.md` tasks 3.5/3.8 · `decisions.md` Part 5 AmA-8 |
| **A-9** | mutation-pack coupling | **FOLDED (standing rule)** — this lane's FE fold touches NO 403 copy, so the FIX2-A5 `old=` block stays matching; recorded rule: any future FE 403-copy change carries the `test/packs/metrics_fe_mutation_matrix_test.py:212-231` update SAME-COMMIT or the mutation silently no-ops | `decisions.md` Part 5 AmA-9/A-10 · `fold-ledger.md` (this row) |
| **A-10** | conditional decorator-literal pin | **FOLDED** — task 3.2's acceptance states the text-route decorator literal stays verbatim (`tests/unit/metrics/test_metrics_query_scope_cage.py:159-166`); the §6 implementation-time pin is discharged into the acceptance text | `phase3-plan.md` task 3.2 · `decisions.md` Part 5 |
| **B-1** (+RULING-3) | Protocol size | **FOLDED** — the tool consumes `activation_block(provider)` (`envelope.py:56-71`, exported `__init__.py:108`); Protocol amendment TRIMS to GAP-1/GAP-2 (+GAP-3 kept — the pack wants the typed pin surface); GAP-4/5 dropped; construction-path pin in phase 2 | `decisions.md` Part 5 AmRULING-3 · `phase1-plan.md` tasks 1.1/1.2/1.6 · `phase2-plan.md` task 2.2 |
| **B-2** | mis-cited selector-ceiling guard — 🟡 must fold | **FOLDED** — the tool refuses on `len(set(names)) + len(series) > MAX_NAMES` (the router formula `routers/metrics.py:719`, mirrored exactly); citation corrected (`provider.py:346-349` caps names only) | `decisions.md` Part 5 AmB-2 · `phase2-plan.md` task 2.4 · `phase5-plan.md` task 5.2 (the formula mutation leg) · `technical-analysis.md` inline pointer (D-UX-4 decision text) |
| **B-3, B-4, B-7** | — | **NOT CARRIED** — absent from the architect's consolidated fold-map (§2); no fold required; not invented | — (this row) |
| **B-5+C-2** | merged into M-1 | see M-1 | — |
| **B-6** | false ordering promise | **FOLDED** — the tool sorts BEFORE the `top_n` cut (the provider returns registration order) — keeps the ascending promise true and makes `dropped_count` meaningful; design inline pointer at §A.3's provider-call claim | `decisions.md` Part 5 AmB-6 + the §A.3 pointer · `phase2-plan.md` task 2.3 · `phase5-plan.md` task 5.2 |
| **B-8+C-5+C-10** | merged into M-2 | see M-2 | — |
| **B-9** | trailing-only windows | **FOLDED** — `end_offset_s: int = 0` on `metrics_history` (clamp `[0, WINDOW_SECONDS − window_s]`; compare leg anchors at `now − end_offset_s − window_s`); no-cursor doctrine preserved | `decisions.md` Part 5 AmB-9 · `phase2-plan.md` task 2.4 |
| **B-10** | multi-family snapshot | **FOLDED — planner ruling PL-1: INCLUDE** (see below); the A.3 kwargs deviation pinned | `decisions.md` Part 5 AmB-10 + the §A.3 pointer · `phase2-plan.md` task 2.3 · `phase5-plan.md` task 5.2 |
| **B-11** | non-goal declaration | **FOLDED** — no anomaly-first ordering / cross-family top-N; the anomaly signal is `delta_change` | `decisions.md` Part 5 AmB-11 · `phase2-plan.md` task 2.1 (CATEGORY_DOC) |
| **B-12** | RED-if-copied example string — 🟡 must fold, derivation demanded | **DERIVATION SUCCEEDED (method recorded in decisions.md Part 5 AmB-12)** — the offending strings are the persona caveat examples embedding "(R-ii)": §A.12 PERSONA (a) Call 1 + PERSONA (b) Call 1 `caveats[0]`, and the §A.9 :573 presentation string. Fix is PLAN-SIDE (the design file takes no content rewrite): task 5.4 asserts the generic caveat form; the canonical shipped wording is attribution-free (task 2.2/AmB-13). All other example strings (availability/error tokens, family-id lists) check clean against the pinned sets | `decisions.md` Part 5 AmB-12 · `phase5-plan.md` task 5.4 · `phase2-plan.md` task 2.2 |
| **B-13** | caveat wording | **FOLDED** — "the series below are real"; no cause attribution; no invented availability member | `decisions.md` Part 5 AmB-13 · `phase2-plan.md` task 2.2 |
| **B-14** | "quintet" naming | **FOLDED** — "the six-member bound block" everywhere in the plan set; design inline pointer at §A.3's block description; Part 1's verbatim D-UX-6 heading retained as the historical record, superseded by AmB-14 | `decisions.md` Part 5 AmB-14 · plan-overview/phase2/phase5 wording sweep · `technical-analysis.md` inline pointer |
| **B-15** (+RULING-2) | half-met criterion | **FOLDED** — the phase-4 criterion scoped to the catalog/shell surface; the Overview/Charts tab-poll residual recorded as ROUTED DEBT (below) | `phase4-plan.md` success criteria · `decisions.md` Part 5 RULING-2 |
| **C-1** (+RULING-4) | sync/async silence | **FOLDED** — NEW decision D-UX-16 (`async def` + `db_offload.offload` body build, mirroring `routers/metrics.py:441-451`); registration-pack callable-shape leg; the loop question RESOLVED NEGATIVE with proof (architect §0) | `decisions.md` Part 5 AmRULING-4/D-UX-16 · `phase2-plan.md` tasks 2.2/2.3/2.4/2.9 |
| **C-2** | merged into M-1 | see M-1 | — |
| **C-3** | merged into M-3 | see M-3 | — |
| **C-4** | racy-bool semantics | **FOLDED** — per-call read via `activation_block` (composing RULING-3); the racy-bool doctrine stated in `read_status` and pinned | `decisions.md` Part 5 AmC-4 · `phase2-plan.md` task 2.2 · `phase5-plan.md` task 5.2 |
| **C-5, C-10** | merged into M-2 | see M-2 | — |
| **C-6** | risk register | **FOLDED** — R13/R14/R15 added to `plan-overview.md` §Risk register; re-rank note recorded (R7 stays High for process; R1-R6/R8-R12 verified real, content unchanged) | `plan-overview.md` §Risk register |
| **C-7** | pin-census arithmetic | **FOLDED** — task 2.8 header now "10 files / 15 EDIT sites + 1 VERIFY-ONLY" (the ari trailing pin needs no change) | `phase2-plan.md` task 2.8 |
| **C-8** | seal/baseline verification commands | **FOLDED** — the SEALED_SHA_BASELINES no-match assertion (`tests/test_chart_image_delivery_e2e.py:1425`) + a RUN of `scripts/generate_prompt_artifact_baseline.py --check` as REAL gate legs | `phase5-plan.md` task 5.8 · `phase6-plan.md` task 6.3 |
| **C-9** | post-merge grep artifact | **FOLDED** — task 5.7's grep re-derivation re-run POST-MERGE as a recorded artifact | `phase5-plan.md` task 5.7 |
| **C-11** | D-WATCH-4 landing sites | **FOLDED** — the asymmetry sentence lands in `CATEGORY_DOC` + the three `tools_note.md` sections; the 5.6 fixture asserts it (the repo-only outline doc is NOT sufficient) | `decisions.md` Part 5 AmD-WATCH-ii · `phase2-plan.md` tasks 2.1/2.7 · `phase5-plan.md` task 5.6 |
| **RULING-1..4** | architect rulings | **FOLDED VERBATIM** — see `decisions.md` Part 5 | `decisions.md` Part 5 + the per-ruling fold sites above |
| **PL-1** | PLANNER RULING (B-10) | **INCLUDE the multi-family snapshot** — the 7→3 patrol round-trip win is a direct commission goal (the patrol personas); the deviation from A.3's kwargs-mirroring is pinned (Part 5 AmB-10 + the design inline pointer); the tool-side implementation composes with B-6 (sort) and M-1 (ceiling order: filter → sort → cut → ceiling) | `decisions.md` Part 5 AmB-10 · `phase2-plan.md` task 2.3 |
| **PL-2** | PLANNER RULING (B-15 in-lane extension) | **NO in-lane tab extension** — the Overview/Charts tab polls are a pre-existing v0.28.6 FE blind spot, not created by this lane; extending them would widen the FE diff beyond the shell mirror P-3 scoped, pull the tab-poll machinery into this lane's review, and delay the fold for a gap the v0.28.6 FE followup family already owns. Cost documented: both tabs keep rendering "no data" copy where the truth is "unknown" until that family lands | `phase4-plan.md` success criteria (the scoped criterion) · this row |
| **ROUTED DEBT** | the tab-poll residual (RULING-2) | `overview-tab.component.ts:284-296` ("No series published yet.") + `charts-tab.component.ts:301-311` ("No samples in the window.") do not read `payload.activation` — ROUTED to the v0.28.6 `fe_registry_read_ok_mirror` FE followup family, joining `fe_registry_read_ok_mirror`'s own family; cost = the same optional-member + `=== false` + distinct-surface pattern per tab poll | this row + `phase4-plan.md` criterion note |
| **§6 pins** | implementation-time notes | **FOLDED as task notes** — mount-handle reachability leg (phase1 1.6(ii-b)); A-10 decorator literal (phase3 3.2 acceptance); C-1 cost-figure provenance (~3.7 ms is the house AM-12 figure, not a local measurement — recorded in Part 5 AmRULING-4 context; re-measure only if the pack wants a latency leg); custom-agent bare-id registration-path trace note (phase2 2.9) | the named tasks |


---

## §F — Plan council round 3 (REJECT fold-and-resubmit; governor session 9dac9801, deep-review)

Verdict: REJECT fold-and-resubmit — 1 critical (C-1), 8 warnings (W1-W8), 11 advisories; **zero design/scope/security defects — a fold cycle, NOT a redesign.**

| Finding | Disposition | Landing site |
|---|---|---|
| **C-1** (critical — the contract-doc amendment needs enforcement mechanisms, not intent) | **FOLDED — all four mechanisms:** (i) the VERBATIM-PRESERVATION LIST as an explicit acceptance artifact (the six fenced blocks' pinned substrings extracted from `tests/unit/metrics/test_central_metrics_contract_doc.py` — block inventory cited in task 3.6, exact strings extracted at implementation time); (ii) the line-scoped allow-list (the FIVE canonical ranges) as the ONLY permitted edit surface (the diff assertion); (iii) Phase-5 leg: the pack runs GREEN WITH THE AMENDMENT APPLIED; (iv) the NON-VACUITY mutation leg (a planted pinned-substring violation REDS the pack — proving the list bites). Composes with the RESTATED guardrail (b) | `decisions.md` Part 4 (amendment-2) · `phase3-plan.md` task 3.6 · `phase5-plan.md` tasks 5.2/5.8 |
| **W1** | **FOLDED — site-by-site propagation list (widened per approver B1; every site this fold actually reached):** `decisions.md` Part 4 (the amendment-2 block + the guardrail-(a) supersede marker on the original block + the pre-adjudication marker extension) · `plan-overview.md` (P-1 adjudications row; the Out-of-Scope frozen-contract bullet; R9; the P3 success criterion) · `phase3-plan.md` (the Objective; task 3.6 execution + acceptance cells; the phase fence; **task 3.6's E-1 success-criterion row — the approver-found omitted site**); `technical-analysis.md` (2 inline pointers at the :1471-area and :1705-area enumeration sentences — no content rewrite) · `fold-ledger.md` (§A.3 fold-site row + this row + §D-2). **The amendment surface: the FIVE canonical ranges (`:31-32`, `:318-322`, `:379-380`, `:387` — IN, `:404-405`)** | the named sites |
| **W2** | **FOLDED — site-by-site propagation list (widened per approver B2; every site this fold actually reached):** `phase1-plan.md` task 1.5 (the fake cost re-stated: `_Fake` grows ONLY `registry_read_ok`; `RecordingProvider`'s GAP-4/5 attributes become inert, left untouched — note: the fake-cost text lives in task **1.5**, not 1.2/1.3 as the directive guessed; folded where the text is) · **approver-B2 additions:** `plan-overview.md` In-Scope item 1 (the ENTRY-POINT scope statement now names the GAP-1/2+3-only amendment) · `plan-overview.md` R2 mitigation (`_Fake` grows EXACTLY ONE member — three booleans → ONE) · `fold-ledger.md` §A.2 The-implementation row (GAP-1/2+3 only; GAP-4/5 DROPPED) · §A.2 The-audited-cost row (one member, not three) · this row. **Semantic footprint (why the stale variant breaks a pin):** `envelope.activation_block()` getattr-reads `listener_enabled`/`census_enabled` (`daemon/services/metrics_query/envelope.py:56-71`) and phase1 task 1.6(iii) pins `activation_block(provider)` block EQUALITY fake↔provider — under the stale three-boolean variant the fake's block cannot match the concrete provider's | the named sites |
| **W3** | **FOLDED** — AmM-1 non-silence: the clamp must SURFACE itself (echo the effective bounds and/or `truncated: true` + a `caveats[]` entry naming the clamp); the 5.2 mutation leg added ("clamp applied silently → the non-silence pin RED") | `phase5-plan.md` task 5.2 (the requirement rides task 2.4's AmM-1 text) |
| **W4** | **FOLDED** — the handle census is THREE sites (`:2533` mount, `:2541` degradation, `:3778` shutdown/cleanup — grep-verified); task 1.4 extends the mount/unmount wiring to clear the manager-held reference at the shutdown site too (symmetric teardown — the tool handle must never outlive the door's), and task 1.6 gains the lifecycle-triple pin (ii-c) | `phase1-plan.md` tasks 1.4/1.6 |
| **W5** | **FOLDED** — the script path corrected to `scripts/protection/generate_prompt_artifact_baseline.py` (verified on disk) at every appearance | `phase5-plan.md` task 5.8 · `phase6-plan.md` task 6.3 |
| **W6** | **FOLDED** — the BASELINE-EXISTS precondition added BEFORE the `--check` run (the script's exit contract is `0 = clean OR baseline absent` — a missing baseline is a silent PASS, so the precondition is load-bearing) | `phase5-plan.md` task 5.8 (ii) · `phase6-plan.md` task 6.3 |
| **W7** | **FOLDED** — the baseline delta is **3 files, NOT 6**: `VERSIONED_PROMPT_ARTIFACTS` (`daemon/tools/prompt_artifacts.py` — soul/rule/workflow/tools_note/tools/skill/growth/memory across agent dirs) does NOT include `meta.json` ⇒ of the 6-file `agents/` delta only the 3 `tools_note.md` move the baseline; derivation stated in the tasks; the payload delta stays 6 | `phase5-plan.md` tasks 5.6/5.8 · `phase6-plan.md` task 6.3 · `plan-overview.md` R11 |
| **W8** | **FOLDED** — FOUR further truth-in-docs sites found by grep and folded same-commit: `daemon/auth/scopes.py:8-20` (the taxonomy enumeration docstring; `maintenance:read` at `:15`) → task 3.1; `docs/architecture/metrics-query-api.md:35` / `:142` / `:212` (the prose rows naming `maintenance:read` for the query routes) → task 3.2 (the scope-cage test docstring was already covered by A-6; `:280-292` is task 3.8's separate debt entry) | `phase3-plan.md` tasks 3.1/3.2 |
| **A8** | **FOLDED (disclosure)** — the tool door closes on the category allow-list + in-body gate, NOT the HTTP auth posture; disclosure rows added (plan-overview §Disclosures + phase5 5.6) | `plan-overview.md` §Disclosures · `phase5-plan.md` task 5.6 |
| **A9** | **FOLDED (ownership named)** — the Phase-1 Tier-2 throwaway base worktree is IMPLEMENTER-owned (created/owned/torn down by the developer in this worktree; the tester lane consumes the recorded results); hand-off notes in task 1.7 + the phase5/6 verification text | `phase1-plan.md` task 1.7 · `phase5-plan.md` task 5.8 · `phase6-plan.md` task 6.3 |
| **Advisories** | **FOLDED — 7 condensed advisory texts supplied by the lane owner (2026-11-03) and folded per above (A1..A7):** A1 citation sweep (all working files) · A2 known-residual (§B row) · A3 AmA-6 wording (phase3 3.5) · A4 completion-test wording (plan-overview) · A5 15+1 split (phase2 2.8) · A6 stale docstring site (phase2 2.8) · A7/PL-3 constants single-sourcing (decisions Part 5 + phase2 2.1 + phase5 5.2). **Delta note (honest):** the council counted 9 deferred while 7 condensed forms were supplied — **2 not carried in the owner's condensed set; treated as closed by the condensation — owner may re-open.** | `decisions.md` Part 5 · the named task files · this row |
| **Re-adjudication** | **RECORDED** — E-1 amendment-2 (lane owner, 2026-11-03) supersedes the E-1 site enumeration: FIVE canonical line-ranges (`:387` is IN); guardrail (b) RESTATED (green WITH the amendment, verbatim-preservation list + line-scoped allow-list; re-pinning forbidden; no-viable-wording ⇒ return to the lane owner / 3.6a) | `decisions.md` Part 4 · this row |
| **Calibration note** | findings 1-7 landing makes the plan implementation-ready — **no redesign**; the round-3 cycle is closed pending the fresh-approver round | — |
