# FOLD LEDGER — central-metrics-v1

Date: 2026-11-02 (UTC) · Lane: `feature/central-metrics-v1` (worktree `/home/nea/ensemble-src-wt-central-metrics-v1`)
Status: **the artefact the fresh approver cites.** Every architect amendment (round 1), every plan-council
warning (round 2), and every advisory disposition is recorded here with its **exact fold site**.

**Provenance of the rounds folded here**

| Round | Verdict | Source | Fold rule |
|---|---|---|---|
| 1 — architect | **SOUND-WITH-AMENDMENTS** (AM-1…AM-27) | `architecture-recommendation-v2.md` (239 L) — the enriched deliverable; **supersedes v1 for planning purposes**; v1 (`architecture-recommendation.md`) retained unmodified for diff provenance | each amendment's own *Fold:* directive names the target file + section |
| 2 — plan council | **APPROVE-WITH-ADVISORIES** (0 critical / 11 warnings / 14 advisories, 2/2 councilors converged) | the plan-council dispatch (W1…W11 + R-a/R-b/R-c) | the dispatch names the fold sites; gate-integrity items (W5/W8/W9/W11) are folded exactly as ruled |

**Neither architect file was edited by this lane.** The shared main checkout was never modified. No version
string exists for this lane; no promote was performed.

---

## A. Architect round — AM-1…AM-27 (fold sites as directed)

| AM | Content | Folded into (file + section) | Status |
|---|---|---|---|
| **AM-1** | Series identity `(name, labels)`; label-aware `?series=` selector; `_label_key` serialization | `plan-overview.md` §frozen contract · `phase1-plan.md` 1.4 · `phase2-plan.md` 2.5/2.6/2.9 · `phase3-plan.md` 3.3 | folded |
| **AM-2** | `cardinality_class: closed\|open\|none`; mandatory `allowed_values` declaration | `plan-overview.md` envelope · `phase1-plan.md` 1.5 · `phase4-plan.md` 4.1/4.3/4.5/4.7 + pin `P4-M9` | folded |
| **AM-3** | Family metadata lives at registration (no second family table) | **`phase2-plan.md` new task 2.7b** · `phase2-plan.md` 2.3/2.9 · `phase4-plan.md` 4.1/4.3/4.5/4.7 · `plan-overview.md` envelope + AC-A.1 · `decisions.md` D4 · pin `P4-M10` | folded |
| **AM-4** | Typing honesty (`kind` without `summary`; `monotonic`; histogram as-stored) | `plan-overview.md` envelope · `phase1-plan.md` 1.4 · `phase2-plan.md` 2.3 | folded |
| **AM-5** | Aware-UTC timestamps; window anchoring; bucket-max downsample; `stale` flag | `plan-overview.md` envelope · `phase1-plan.md` 1.4/1.5 · `phase5-plan.md` 5.1 | folded |
| **AM-6** | Opaque `next_cursor`; `400 invalid_cursor` | `plan-overview.md` envelope · `phase1-plan.md` 1.5 · `phase2-plan.md` 2.9 · `phase3-plan.md` 3.3 | folded |
| **AM-7** | `schema_version` bump policy; tolerant FE mirrors (`unknown(<value>)`) | `plan-overview.md` envelope · `phase1-plan.md` 1.4/1.6 · `phase5-plan.md` 5.1 + pin `P5-M9` | folded |
| **AM-8** | Non-Prometheus deviations clause | `plan-overview.md` envelope · `phase1-plan.md` · `decisions.md` D4 · `phase6-plan.md` 6.4(a) | folded |
| **AM-9** | Array-backed ring, 16 B/pt, 11.4 MiB (deque rejected at 129–161 B measured) | `decisions.md` D3 sizing table · `phase2-plan.md` 2.5 · `plan-overview.md` R7 + budget · pin `P2-M9` | folded |
| **AM-10** | Depth formula (registry-native divisor) + age eviction + recompute rule | `decisions.md` D3 · `phase2-plan.md` 2.5 · `plan-overview.md` budget · pins `P2-M10`/`P2-M11` | folded |
| **AM-11** | DB pulls = one-shot `psycopg.connect` per tick (never persistent) | `plan-overview.md` P-R1/P-R14 + scope notes · `phase2-plan.md` 2.4 · `phase4-plan.md` 4.2 · pin `P4-M11` | folded |
| **AM-12** | Body **build** off the event loop | `phase3-plan.md` 3.1 · `plan-overview.md` P-R1/P-R15 + envelope · pins `P3-M9`/`P3-M12` — **amended by W6** (build-only claim + bounded residual encode) | folded (amended by W6) |
| **AM-13** | Sampler starts LAST / stops FIRST; `last_publish_monotonic()`; dedupe/GC keys | `phase2-plan.md` 2.3/2.6 · `phase3-plan.md` 3.4 · pins `P2-M12`/`P3-M10` | folded |
| **AM-14** | `restart_epoch` = `bash_spawn_reaper.CURRENT_BOOT_ID` | `decisions.md` D3/D4 · `phase2-plan.md` 2.6 · `plan-overview.md` envelope | folded |
| **AM-15** | Cadence synthesis: key = TICK; `interval_s` = observed `max(sampler,publisher)` | `decisions.md` D3/AD4(d) · `plan-overview.md` AD4(d)/D-1/P-R5 · `phase2-plan.md` 2.6/2.8 | folded |
| **AM-16** | D-11a: exposition = structural identity + 3-carrier allowlist | **`requirements.md` AC-A.5** (commission-annotated) · `plan-overview.md` objective + L4 · `phase3-plan.md` 3.8 + `P3-M8` | folded |
| **AM-17** | D-11b: D1(b) wins; requirements re-scoped to the listener surface | **`requirements.md` FR-A7/FR-D3/AC-A.6/AC-D.1** (6 annotations) · `plan-overview.md` flag D-11 · `phase1-plan.md` 1.1/1.6 · `phase3-plan.md` 3.7 | folded |
| **AM-18** | Corrected invariance pin + `census_gated` from public `CensusSampler.gated` | `plan-overview.md` R4/L6 · `phase1-plan.md` 1.6 (normative truth table) · `phase2-plan.md` 2.4/2.9 · `phase3-plan.md` 3.7 · pin `P3-M11` | folded |
| **AM-19** | D1 evidence demotion + disclosure corrections (a)–(d) | `decisions.md` D1 (evidence/c/d) · `phase1-plan.md` 1.1/1.7 · `phase5-plan.md` 5.7 + pin `P5-M10` · `phase6-plan.md` 6.4(b) · `plan-overview.md` D1 row | folded (+ DEV-6 residual) |
| **AM-20** | Scope posture + retrofit map + over-grant disclosure | `decisions.md` D4 (+ **new §D6** = LR-1's named commitment) · `plan-overview.md` scope notes · `phase6-plan.md` 6.4(c) | folded |
| **AM-21** | Cage target 108→111 (the "34→37" target was a docstring ⇒ vacuous) | `phase3-plan.md` 3.5/`P3-M7`/P3-G2/exit · `plan-overview.md` D-9 · `decisions.md` D4 (Evidence/cage/disclosure #4) | folded |
| **AM-22** | Tier-2 provisions: hang-class clause, 137-min precedent, expected-skip census, PROJECTION | `plan-overview.md` §gate doctrine · `phase3-plan.md` whole-tree section · all node counts marked PROJECTION | folded |
| **AM-23** | ADD a production-config build leg | `phase5-plan.md` 5.9 + build guard + pin `P5-M11` · `phase6-plan.md` 6.7 (FE-manifest delta) | folded |
| **AM-24** | Vendor the inventory; WS-A rc semantics; provider ≠ `/readyz` component | **`inventory-vendor/`** (3 copies + `PROVENANCE.md`, byte-identical) · `plan-overview.md` provenance · `phase4-plan.md` 4.4 + P4-G1 row · `phase3-plan.md` 3.4 note · `phase6-plan.md` 6.1 | folded |
| **AM-25** | F2 dropped (value ruling) + flip condition verbatim | `decisions.md` D5 F2 row · `plan-overview.md` AD4(c)/budget/R10/OUT-list · `phase4-plan.md` W1 cluster + 4.3 + risk | folded (+ DEV-4 residual) |
| **AM-26** | Budget table replaced + F7c reconciliation prerequisite | `decisions.md` D5 · `plan-overview.md` §Re-derived budget · **`phase6-plan.md` 6.2** (outcome + ledger amendments + R-a recipe) | folded (+ DEV-5 residual) |
| **AM-27** | Cap-enforcement wording (per-metric cap ≠ global budget) | `plan-overview.md` R6/P-R7 · `decisions.md` D5 | folded |

---

## B. Plan-council round — W1…W11 (fold sites as ruled)

| W | Content | Folded into (file + section) | Status |
|---|---|---|---|
| **W1** | Enumeration staleness (DEV-1…DEV-3 → DEV-1…DEV-6 at the council round; D-1…D-10 → D-1…D-11 — the register now carries **DEV-1…DEV-8**, see §L) | `phase6-plan.md` 6.6 + risk row · `plan-overview.md` LR-3 row · `plan-overview.md` §Disclosed adjudicated deviations intro · `plan-overview.md` open-questions (Pending #3) | folded |
| **W2** | DEV-5/DEV-6 site coverage — the third budget copy and the stale AC-E.3 | **`requirements.md` §Theme-B series-budget check** (re-scoped + commission-annotated "council W2, 2026-11-02") · **`requirements.md` AC-E.3** (re-scoped + annotated) · `plan-overview.md` DEV-5/DEV-6 **narrowed** to the rows that remain (C-10/NFR-5 and FR-E5/NFR-9) | folded (both sites) |
| **W3** | Task 6.3 ⊂ 6.2 (pre-AM-26 artifact) | `phase6-plan.md` 6.3 — marked **SUPERSEDED-BY-6.2** inline; every element of 6.3 is inside 6.2 (which now also carries the R-a recipe) | folded (superseded inline) |
| **W4** | `parseNaiveUtc` wrong-variant wording (5.1 vs the risk row) | `phase5-plan.md` 5.1 · `phase5-plan.md` risk row — both now state: the payload is **aware**-UTC (AM-5) and is parsed by the **same** house helper `parseNaiveUtc` (`resources.models.ts:291-296`, which already accepts a zone designator); **no new variant, no behavioral change** | folded |
| **W5** | Task 4.9 tier misclassification (the C17 docstring edit targets a mechanical-list file) | `phase4-plan.md` 4.9 → **DEFERRED OUT of v1** (recorded, not shipped) with the rationale · `phase4-plan.md` P4-G2 row · `phase4-plan.md` risk row + exit criterion · `plan-overview.md` P4-G2 gate row + P4 scope bullet · `decisions.md` D5 F12 row + disclosure item 7 | folded (dispatcher adjudication: defer; the false assertion dies) |
| **W6** | AM-12's encode half (`db_offload` returns an object; FastAPI encodes on the loop) | `plan-overview.md` envelope + P-R15 + M-delta row · `phase3-plan.md` 3.1 + pins `P3-M9`/`P3-M12` — **BUILD-OFF-LOOP ONLY**, residual on-loop encode **bounded and pinned**; **no pre-encoded-`Response` precedent**. *(The bound quoted at council time — `max_points` ≤ 120 × ≤ 50 names ⇒ ≤ 6 000 points ≈ 1.8 ms — was **superseded by approver B1 + leader ruling**: the default is now **240**, so the bound is **240 × ≤ 50 names ⇒ ≤ 12 000 points ≈ 3.6 ms** — see §I.)* | folded |
| **W7** | Live "17 families" in a scope row | `plan-overview.md` P4 scope bullet → **16/26 families** (the architect-context sites were left untouched) | folded |
| **W8** | Census conflation in the P3 criterion | `plan-overview.md` P3 criterion + `phase3-plan.md` P3-G2 pin → the **three-way** form: **declared-route == 111 · mount == 37 · class manifest == 33** | folded |
| **W9** | D2 sets must be invocable as declared | `plan-overview.md` §gate doctrine (registered-pack table + ids) + D1/D2 table · `phase1/2/3/4/5/6` gate rows now name the **registered pack ids** (`metrics_p2_dual_side`, `metrics_p3_dual_side`, `metrics_scope_cage_dual_side`, `metrics_p4_w1\|w2\|w3_dual_side`, `metrics_fe_dual_side`, `central_metrics_ledger_reconciliation`, `central_metrics_contract_doc`) | folded |
| **W10** | Mandatory recipe load omitted | `plan-overview.md` §gate doctrine recipe table · `phase1/2/3/4/5/6` gate sections — per-leg recipes named with their deterministic paths: `blast-radius-leg` (Tier-1), `whole-tree-base-proven-diff` / `regression-ab-partition-run` (Tier-2), `failed-digest-equality-gate` (every digest comparison) | folded |
| **W11** | Vacuous mutation `P3-M5` | `phase3-plan.md` P3-M5 — **re-pointed at the listener-reorder defect**: "the **listener start moved AFTER the census block** (the order AM-13 forbids: the listener starts FIRST, the sampler starts LAST of the trio)". The planted mutation is now the real defect class | folded |

---

## C. R-a / R-b / R-c dispositions

| Item | Disposition | Fold site | Status |
|---|---|---|---|
| **R-a** | The 89/87 baseline needs a standalone derivation recipe (no archaeology) | **`phase6-plan.md` 6.2** — the recipe is written out as numbered steps: (1) read catalog §1 `:224` (total **582** = "F0.1→F21 + F7b + F7c"), §1 `:251` (the trail carrying the `+2 (F7c)` term) and §1 `:232` (phase row-sum P0 28 + P1a 63 + P1b **265** + P1c 226 = 582); (2) read §14.1 `:1057` ("F0.1–F21 + F7b" — F7c absent), §14.2 `:1783-1790` (P1b **263**) and §14.2 `:1075` (trail without the term) ⇒ the ledger is stale-by-2; (3) **582 wins** (§1 carries the explicit F7c row worth 2 at `:206`; the 580 is the F7b-era carry-forward); (4) **89** = F0.1 16 + F0.2 3 + F0.4 1 + F7-today 21 + F7b 46 + F7c 2, and **87** = that set minus F7c's 2 — **both denominators pinned**. Pointer also added to `plan-overview.md` §Re-derived budget | folded |
| **R-b** | The fold ledger must live in the plan set and carry both rounds + the advisory disposition | **this file** (`fold-ledger.md`) — §A (27 architect rows), §B (11 council rows), §C, §D (advisory register), §E (deferred items), §F (verification record). Added to the `plan-overview.md` plan-file manifest | folded |
| **R-c(i)** | The stranded "Explicitly NOT in this phase" list in Phase 1 | `phase1-plan.md` — the heading is **re-attached** to its bullet list ("any `daemon/config.py` edit, any managed-key addition, any kill-switch consultation site, any route or package code. The posture is *documented*, not *changed*."); the truth table now follows the list | folded |
| **R-c(ii)** | The plan-file manifest omitted 3 files vs task 6.8's checklist | `plan-overview.md` plan-file manifest — now lists **every** file in the lane dir: `fold-ledger.md`, `architecture-recommendation.md`, `architecture-recommendation-v2.md`, `research-findings.md`, `inventory-vendor/PROVENANCE.md` (plus the three vendored copies named individually) | folded |
| **R-c(iii)** | Executor-re-derives annotation missing | `phase2-plan.md` gate section — "**Executor re-derives** the node counts: the D1/D2 node figures are PROJECTIONS re-derived at run time via `--collect-only -q` (the pack's own collect-only output is the record; a hand-maintained count is a finding)" | folded |

---

## D. Advisory register (14 — 3 folded, 11 noted-not-folded)

**Folded (3):** see §C (R-c i/ii/iii) — the stranded Phase-1 list, the manifest completeness, the
executor-re-derives annotation.

**Noted-not-folded (11 — non-blocking).** The council's advisory text lives in the council report; the fold
dispatch enumerated only the three folded items, so each remaining advisory is carried here as a
traceable, non-blocking note rather than reproduced:

| # | Disposition |
|---|---|
| A-4 | noted-not-folded (non-blocking) — council advisory text not enumerated in the fold dispatch; no plan-set change required; carried for the implementation lane's awareness |
| A-5 | noted-not-folded (non-blocking) — as A-4 |
| A-6 | noted-not-folded (non-blocking) — as A-4 |
| A-7 | noted-not-folded (non-blocking) — as A-4 |
| A-8 | noted-not-folded (non-blocking) — as A-4 |
| A-9 | noted-not-folded (non-blocking) — as A-4 |
| A-10 | noted-not-folded (non-blocking) — as A-4 |
| A-11 | noted-not-folded (non-blocking) — as A-4 |
| A-12 | noted-not-folded (non-blocking) — as A-4 |
| A-13 | noted-not-folded (non-blocking) — as A-4 |
| A-14 | noted-not-folded (non-blocking) — as A-4 |

**Effect on the plan set: none.** None of the 11 is gate-blocking (the council's own verdict was
APPROVE-WITH-ADVISORIES with 0 criticals); they are carried so the fresh approver can trace every advisory
in one place. **Note for the approver:** if any A-4…A-14 turns out to be gate-relevant, it is not folded
here — the council report is the authority and this ledger records only its disposition.

---

## E. Deferred items (recorded, not shipped)

| # | Item | Why deferred | Owner |
|---|---|---|---|
| **DEF-1** | **C17 docstring correction** — `daemon/services/job_queue_service.py:828-836` says the counter is cumulative while the code returns a per-tick value (`:837`) | `daemon/services/job_queue_service.py` is **ON the mechanical Tier-2 list** (ensure.md §Gate Tiers), so a one-line comment edit would mechanically force a whole-tree dual-side leg — disproportionate for a comment, and doctrine says mechanical is never a judgment call. The F12 adapter accumulates the per-tick counters correctly regardless of the docstring (the disambiguation lives in the metrics layer). **Plan council W5, dispatcher adjudication.** | a lane that legitimately edits that file |
| **DEF-2** | **F2 thread CPU** (+22 series, one family pack) | dropped from v1 by **value** ruling (AM-25 / LR-2) — prod is Darwin-primary, so its darkness is Certain. **Flip condition (verbatim):** "if the ops surface becomes Linux-primary or the operator confirms near-term per-thread CPU need, re-admit (+22, one pack)". | a future lane, on the flip condition |
| **DEF-3** | `metrics:read` scope split | **named commitment** (LR-1), not built now — the retrofit map (1 taxonomy line + 1 manifest line + 4 decorator lines) is recorded in `decisions.md` §D6 as binding guidance for the deferred lanes (items #2/#3). | the deferred agent-tool / Grafana lanes |

---

## F. Verification record (self-greps after the council fold)

| Check | Result |
|---|---|
| Live "17 families" outside untouched-architect contexts | **0 live hits** in the plan set — the only occurrences are this ledger's own description of the fix; `architecture-recommendation.md` / `-v2.md` retain theirs (untouched) |
| Two-way "route count == 37" | **0 hits** — the three-way form (declared-route 111 · mount 37 · class manifest 33) is the only live form |
| `P3-M5` mutation | re-pointed to the **listener-reorder** defect (it can now go RED) |
| D2 legs naming registered packs | **all 9 legs** name a registered pack id |
| Stale DEV/D enumerations | `DEV-1…DEV-3` → **0**; `DEV-1..DEV-3` → **0**; `D-1…D-10` → **0**; `D-1..D-10` → **0** |
| False "no mechanical-list file is touched" assertion | **0 hits** — the corrected true statement is in `phase4-plan.md` 4.9/P4-G2/risk/exit, `plan-overview.md` P4 gate row + P4 bullet, `decisions.md` D5 F12 + disclosure #7 |
| AM-12 encode claim | "BUILD-OFF-LOOP ONLY" + the pinned residual bound (≤ 6 000 points ≈ 1.8 ms) — **SUPERSEDED by approver B1 + leader ruling: the default `max_points` is 240, so the bound is ≤ 12 000 points ≈ 3.6 ms (see §I / §K); this row records the council-round figure only**; no pre-encoded-`Response` precedent |
| Architect files | **untouched** (v1 + v2 sha256 unchanged) |
| Shared main checkout | **untouched** (source mtimes 19:46–19:59; `git status` still `??`) |
| Version strings / promote | none / none |

---

## G. Reading order for the fresh approver

1. `plan-overview.md` — objective, adjudications (AD + **LR-1…LR-3**), scope fences + out-list, phase map,
   coupling, the frozen contract, the gate doctrine (registered packs + recipes), the flag table
   (**D-1…D-11**), the disclosed deviations (**DEV-1…DEV-8**), the re-derived budget (89/87 + 363),
   the risk register (R + M + P-R), the success criteria, and the provenance.
2. `phase1-plan.md` … `phase6-plan.md` — the per-phase tasks (traceable to FR/AC → D → seam), each gate leg
   with its tier, registered pack, recipe, D1/D2 split and mutation table.
3. `decisions.md` — D1…D6 (D6 = the LR-1 named commitment) with the amended sizing/cage/budget rulings.
4. `requirements.md` — the LR-3 + council-W2 re-scopes, each commission-annotated.
5. **this file** — the fold ledger for both rounds.

---

## H. Approver iteration 001 → ITERATION 002 (B1…B5 + N1…N5)

**Verdict folded:** iteration 001 **REJECTED** — 5 blockers, **all editorial/decision-level**; the
architecture is **UPHELD** (no architectural change). Tracking file:
`/home/nea/ensemble-src/.agents/approver/central-metrics-v1-tracking.md` (shared main checkout — **read-only**,
never modified by this lane).

### H.1 Blockers

| # | Blocker (approver anchor) | LEADER RULING applied | Folded into (file + section) | Status |
|---|---|---|---|---|
| **B1** | Hard-pinned `max_points`/resolution contradiction — `requirements.md:47` (FR-A3) · `:114` (FR-C1) · `:209` (AC-A.3) · `:228` (AC-C.1) ↔ `plan-overview.md:246` · `phase3-plan.md:97` (P3-M12) | **RAISE the default `max_points` to 240** — the AC is NOT amended or scoped down | `plan-overview.md` envelope (`:246` + the `/series` default) + P-R15 + M-delta row · `phase3-plan.md` 3.1 + 3.3 + P3-M9 + P3-M12 · `phase2-plan.md` 2.9 · `phase5-plan.md` 5.4 — the residual is re-derived to **240 × 50 names = 12 000 points ≈ 3.6 ms** in **all 6 sites**; P3-M12's RED threshold moved with it; the **FE Charts tab inherits 15 s effective resolution out-of-the-box** (it requests `window_s=3600` → 240 points at the 15 s spacing) | folded |
| **B2** | FR-C2 chart-primary floor set unimplemented — `requirements.md:115` ↔ `phase2-plan.md:34` · `phase4-plan.md:32` · `phase1-plan.md:28` · `phase5-plan.md:27` | enumerate the roster as an explicit task; **FOLD the requirements row** (F2 removed from the floor set) rather than leaving a DEV row for something LR-2 already decided | **the roster now lives in `phase2-plan.md` task 2.7b** (+ a new § *The chart-primary roster* section): **12 families** = FR-C2's floor set **minus F2** + the qualifying D5 partials **F7, F8, F18, F21**, each with its per-family/per-series `chart_role`. `requirements.md` **FR-C2 folded** (F2 struck) + **AC-A.3 / AC-C.1 folded** (subject set = the roster; the ≥240 threshold unchanged). Follow-ons: `phase1-plan.md` 1.4 (envelope carries `chart_role`), `phase4-plan.md` 4.1 (registration takes the roster role), `phase5-plan.md` 5.4 (Charts tab renders the roster) | folded |
| **B3** | Frozen `/families` envelope drops `buckets` — `requirements.md:54` (FR-A8) · `:207` (AC-A.1) ↔ `plan-overview.md:192-196` · `phase2-plan.md:29` | **ADD `buckets`** to the frozen family shape (the provider already derives them; deferred consumers #2/#3 need them on the wire) | `plan-overview.md` §frozen contract (the `/families` shape now carries `buckets:[…]\|null` per name) · `phase2-plan.md` 2.3 (the provider serves them) · `phase5-plan.md` 5.1 (the FE model mirror) · new mutation `P2-M15` (rides the EXISTING registered pack `metrics_query_provider` — **no pack-set expansion**) | folded |
| **B4** | FR-D2 governing-evidence self-contradiction — `requirements.md:127` ↔ `decisions.md:34-39` · `plan-overview.md:63` vs `:546` · `phase1-plan.md:25` | disclosure-only: the plan set must say ONE thing; the requirements row is **not** edited | `plan-overview.md` §Research insights (the two-class rule is now a **supporting precedent**; the load-bearing basis = intra-family consistency + the two-site blast-radius census) · **new DEV-7** in §Disclosed adjudicated deviations (the requirements-side FR-D2 wording is outside every re-scope family) | folded (disclosure) |
| **B5** | FR-E8(a) step rendering omitted while traced — `requirements.md:144` ↔ `decisions.md:183-184` · `phase5-plan.md:26`/`:32` | **ADD step rendering for counters** (the ring stores step-function samples; linear interpolation asserts unobserved values) | `decisions.md` D2 ruling + rationale (the primitive freeze now reads **lines + steps + areas**; counters STEP, gauges LINEAR) · `phase5-plan.md` 5.3 (the primitive family) · `phase5-plan.md` 5.4 acceptance + `phase5-plan.md` 5.9 (the e2e asserts a counter renders as a STEP) | folded |

### H.2 Non-blocking notes

| # | Note | Folded into | Status |
|---|---|---|---|
| **N1** | Stale mutation counts | `phase3-plan.md` exit criterion → **12/12** (P3-M1…P3-M12 — the W6 on-loop-encode guard is P3-M12, so an executor stopping at 11 would never run it) · `phase6-plan.md` risk row → **12/12** (P6-M1…P6-M12) | folded |
| **N2** | `phase4-plan.md:32` (task 4.1) stale "except F7 bytes call site / C17 docstring" clause contradicting the W5 deferral | `phase4-plan.md` 4.1 → the diff-review clause now reads "except the F7 bytes call site — an existing observe gaining a real value — and **no other producer-module edit**: the C17 docstring fix is **DEFERRED** per council W5, so **no mechanical-list file is touched** by this lane" | folded |
| **N3** | `P3-M8` + `P4-M11` mutation pins lacked a declared pack home (pack-set expansion forbidden) | `phase3-plan.md` P3-M8 → rides the registered pack **`metrics_query_routes`** · `phase4-plan.md` P4-M11 → rides the registered pack **`metrics_family_adjudication_w2`** — both **existing** registered packs, **no pack-set expansion** | folded |
| **N4** | `phase6-plan.md:19` — 6.1 ↔ 6.6 dependency inversion | `phase6-plan.md` 6.1 now depends on **none** (it is a precondition of 6.6) · `phase6-plan.md` 6.6 depends on **Phases 2–5 + 6.1** | folded |
| **N5** | Duplicated "Feeds:" line (`phase1-plan.md:5-6`) — the tracking file's "Feets:" is a typo | `phase1-plan.md` → the duplicate line removed (one "Feeds:" remains; verified `^Feeds:` == 1) | folded |

### H.3 Remaining approver notes — disposition

| Note | Disposition |
|---|---|
| FR-A4 "semver" phrasing vs the plan's integer `schema_version` | **NOTED-NOT-FOLDED** — the requirement row is outside every re-scope family (amending it would repeat the B4 defect class); recorded as **DEV-8** for disclosure |
| NFR-1 p95 measurement not scheduled in any route pack | **FOLDED** (trivially fixable in a file already edited) — `phase3-plan.md` 3.6 route pack now **measures** the two NFR-1 latencies (snapshot ≤ 250 ms p95, 60-min trend ≤ 1 s p95) and reports them |
| D5 "Ten families stay OUT" enumerates 13 mixed items | **NOTED-NOT-FOLDED** — the count is in `decisions.md`'s D5 promote-disclosure prose, which this fold does not edit (the *substantive* disclosure — which families and why — is correct); the ledger records the imprecision |
| ~25 vs ~24 node-count drift | **FOLDED** — `plan-overview.md` P4 row now reads ~24, aligned with `phase4-plan.md` P4-G1 |
| Anchor-form drift (`:449`/`:447` vs `:408-419`/`:332-340`) | **FOLDED as a stated convention** — `plan-overview.md` §Research insights now states the rule: the *gate method* is cited as `<file>:<method range>`, the *behavioural check site* as `<file>:<call line>`; both forms are correct and the usage is now uniform |
| P4↔P5 coupling "loose" vs "independent" | **FOLDED** — both now say **loose** with the same parenthetical (`plan-overview.md` coupling map + `phase4-plan.md` §Coupling) |
| Lane-criteria ordering (`L7` sat after `L11`) | **FOLDED** — the lane criteria are re-ordered L1…L11 (the gate-legs criterion is back in sequence) |
| Stale architect cell `architecture-recommendation-v2.md:200` ("9 fams … F2 was partial") | **NOTED-NOT-FOLDED** — it lives in the architect's own file, which this lane does not edit. **The plan set's own statement was corrected in iteration 003 (NB4)** to "**ten** families … F2 was adjudicated IN (full, 22 series); its removal changes the full-IN family set **11→10**"; the architect's cell retains the superseded phrasing and is flagged here so the two are never conflated |

---

## I. Pinned numbers after iteration 002

| Quantity | Value | Sites |
|---|---|---|
| `/series` `max_points` default | **240** | plan-overview envelope + `/series` contract · phase2 2.9 · phase3 3.1/3.3 · P3-M12's RED threshold |
| Residual on-loop encode bound | **240 × ≤ 50 names = ≤ 12 000 points ≈ 3.6 ms** | plan-overview envelope + P-R15 + M-delta · phase3 3.1 + P3-M9 + P3-M12 (**6 sites, all consistent**) |
| FE Charts-tab effective resolution | **15 s out-of-the-box** (`window_s=3600` → 240 points) | plan-overview envelope · phase5 5.4 |
| `/families` shape | carries **`buckets`** per name + the family-level **`role`** (roster chart role) | plan-overview §frozen contract · phase2 2.3 · phase5 5.1 |
| Chart-primary roster | **12 families** (FR-C2 floor − F2 + F7/F8/F18/F21), per-series `chart_role ∈ {line, step}` | **phase2 task 2.7b** (+ its roster section) — referenced by phase1 1.4, phase4 4.1, phase5 5.4, requirements FR-C2/AC-A.3/AC-C.1 |
| Counter rendering | **STEP** (gauges LINEAR) | decisions D2 · phase5 5.3/5.4/5.9 |

---

## J. What did NOT move (explicit non-regression statement)

| Axis | State after iteration 002 |
|---|---|
| Gate tiers | **unchanged** — P1 Tier 0 · P2 Tier 1 · P3 **Tier 2** (mechanical) · P4 Tier 1 ×3 · P5 Tier 1 · P6 Tier 0 |
| Registered packs | **unchanged** — 9 ids; the only pack-adjacent change is that `P2-M15` rides the **existing** `metrics_query_provider` pack (no pack-set expansion) |
| Extension subset | **unchanged** — 16/26 families (10 full + 6 partial) |
| Budget | **unchanged** — +274 → 363 ≈18.2 % (F7c-inclusive) / 361 (ledger basis); 89/87 denominators; F7c reconciliation outcome (582) untouched |
| Ring sizing | **unchanged** — array-backed, 16 B/pt, 11.4 MiB, ≈480 points per 2 h window (the `max_points` default is a *response* bound, not the ring depth) |
| Cages | **unchanged** — declared-route 108→111 · mount 37 · class manifest 33 |
| Kill-switch posture | **unchanged** — auth-only door, two-site census, `CensusSampler.gated` source; the AM-19 demotion is now stated in ONE place (B4) |
| Migrations / dependencies / promote / version | **unchanged** — zero migrations, zero backend deps, zero npm deps, no promote, no version string |

---

## K. Verification record (iteration 002)

| Check | Result |
|---|---|
| Live `max_points … 120` (outside correction notes) | **0 hits** — *after* the post-report residual fix below (the first pass missed one site) |
| `12 000 points` / `3.6 ms` consistency | **7 / 7** sites carry both (6 + the `decisions.md` D4 `/series` cell added in the iteration-002 micro-fold) |
| Live `6 000 points` / `1.8 ms` | **0 hits** |
| `buckets` present in the frozen shape + provider + FE model | **yes** (12 mentions across the three files) |
| Chart-primary roster present in task 2.7b | **yes** (roster section + 12-family table) |
| Step rendering (counters STEP / gauges LINEAR) | **yes** (decisions D2 + phase5 5.3/5.4/5.9) |
| `Feeds:` duplicate | **removed** (one line remains) |
| Mutation counts | phase3 **12/12** · phase6 **12/12** |
| Pack homes for `P3-M8` / `P4-M11` | named, existing packs, no expansion |
| Broken markdown table rows | **0** |
| Architect files / shared main checkout | **untouched** |
| **Post-report residual (iteration 002)** — a **live stale `max_points` default in `decisions.md`** (the D4 route table, the `GET /api/metrics/series` cell): it still read "`max_points` defaults ≤ 120 so the off-loop body stays bounded", contradicting B1's ruling, **and** it still carried the pre-W6 claim that the body is "built **and JSON-encoded** off the event loop". Caught by the **leader's post-report verification**, not by this ledger's own grep (the earlier pattern matched only the plan-overview/phase3 phrasing). | **FIXED** — the cell now reads: build-**off-loop only** (`db_offload.offload` returns a Python object; FastAPI still encodes on the loop), `max_points` defaults **≤ 240** (approver B1 + leader ruling), and the residual on-loop encode is bounded at **240 × ≤ 50 names ⇒ ≤ 12 000 points ≈ 3.6 ms**. The ledger's own W6 row was marked **superseded by B1** so the council-round figure cannot be misread as current. A **full-plan-set sweep** for the `120`-family variants (`≤ 120`, `≤120`, `defaults to 120`, `cap of 120`, `120 points`, `120 × 50`, `120 pts`) then found **0 further live hits** — the only surviving `120` strings are the line anchors `metrics_listener.py:120-144` and `message_tap.py:120-124`, plus this ledger's own correction/verification text. |

---

## L. Approver iteration 002 → ITERATION 003 (NB1…NB5 + N-a…N-d + the mandatory class-sweep)

**Verdict folded:** iteration 002 **REJECTED** — five NEW blockers, **all introduced by the fold edits
themselves** (iteration 001's B1–B5 were verified CLOSED). Tracking file:
`/home/nea/ensemble-src/.agents/approver/central-metrics-v1-tracking.md` (shared main checkout — read-only).

### L.1 Blockers

| # | Blocker (approver anchor) | Folded into (file + section) | Status |
|---|---|---|---|
| **NB1** | `/families` shape **dual-stated** — `decisions.md:336` (D4 row) omitted `buckets` while `plan-overview.md:192-198` + `phase2:29` + `phase5:24` carried it; `decisions.md` is the designated contract | **ONE canonical frozen shape**, now quoted **token-identically in all four sites**: `plan-overview.md` §frozen contract (the source, in a fenced block) · `decisions.md` D4 `/families` row · `phase2-plan.md` 2.3 · `phase5-plan.md` 5.1 — the plan-overview states the single source and the `\|` cell-escape convention. **Verified mechanically: 4/4 sites, all token-identical after normalising the markdown escape.** | folded |
| **NB2** | `phase2-plan.md:157` exit criterion "14/14" vs 15 declared rows (P2-M15 would never be planted) | `phase2-plan.md` exit criterion → **15/15 (P2-M1…P2-M15)**, with the reason stated (P2-M15 is the buckets-dropped guard from B3) | folded |
| **NB3** | DEV register enumeration drift — 8 rows exist but six binding sites still said "six" / "DEV-1…DEV-6" | `plan-overview.md` LR-3 row (**Eight** … **DEV-1…DEV-8**) · `plan-overview.md` §Disclosed adjudicated deviations intro (**eight (DEV-1…DEV-8)**, with the fold history) · `plan-overview.md` open-questions (Pending #3) → **DEV-1..DEV-8** · `plan-overview.md` DEV table **re-ordered 1→8** (DEV-8/DEV-7 had been inserted out of sequence) · `phase6-plan.md` 6.6 → dispositions for **all EIGHT rows** · `fold-ledger.md` W1 row (annotated as the council-round statement + current count) · `fold-ledger.md` §G reading order → **DEV-1…DEV-8** | folded |
| **NB4** | Budget-derivation clause: "9 families" while enumerating ten, and "F2 was a partial" is FALSE (F2 was adjudicated IN (full), 22 series — `requirements.md:77`, DEV-4, `decisions.md:460`); its removal changes the full-IN set 11→10 | `decisions.md` D5 series-delta bullet → **ten families** + "**F2 was adjudicated IN (full, 22 series)**, so its removal changes the full-IN family set **11→10** — it was NOT a partial" · `plan-overview.md` §Re-derived budget (the full-IN row) → the same clause. **Headline totals unchanged** (+274 → 363 ≈18.2 %) | folded |
| **NB5** | `CensusSampler.gated` does not exist at the tree (only the private `_gate()`); no task created it; `metrics_census.py` was in no phase scope — yet it is load-bearing for Phase-1 invariant (iv), P2-M13, P3-M11 and AM-18's census==2 | **Phase-2 micro-task 2.4a** added: a **public `gated` property wrapping the EXISTING private `_gate()`** — a read-through, **NO new behavioural check site** (the census stays == 2) · `daemon/services/metrics_census.py` **declared in the P2-G1 file scope** (additive property only) · `P2-M13` re-based on the property (a third check site) · cross-references added at `phase1-plan.md` 1.6 (invariant), `phase3-plan.md` 3.7, `plan-overview.md` envelope + R4, `decisions.md` D1, and the plan-overview phase map (P2 task count 10 → **11** — the actual phase-2 task-table rows are **11**: 2.1–2.9 + 2.4a + 2.7b) | folded |

### L.2 Gate-executability notes

| # | Note | Folded into | Status |
|---|---|---|---|
| **N-a** | **PACK REGISTRATION OBLIGATION** — all nine "REGISTERED PACK" ids had **0** hits in `.agents/tester/PACKS.md`, so every gate leg would be unexecutable | `phase1-plan.md` 1.7 (the gate-pack declaration) — now carries the **explicit registration obligation**: all nine ids must be registered in `.agents/tester/PACKS.md` before any leg runs; the nine ids are listed; and the **division of labour** is stated (the plan set commits the ids + their declared node scopes; the `PACKS.md` rows land **with the lane's evidence**, the tester lane owning the file) | folded |
| **N-b** | `P3-M5`/`P3-M6`/`P3-M10` mutated `test_metrics_api_wiring`, which sat only in the D2 composition | `phase3-plan.md` P3-G1 **D1 pack list** → new **`metrics_query_api_wiring`** D1 pack (the wiring test's D1 home) · `P3-M5`/`P3-M6`/`P3-M10` now name that D1 pack | folded |
| **N-c** | AM-5 staleness fields (`last_sample_age_s`, `stale`) missing from the phase-1 1.4/1.5 and 2.9 lists | `phase1-plan.md` 1.4 (envelope fields) · `phase1-plan.md` 1.5 (window semantics + the stale rule) · `phase2-plan.md` 2.9 (envelope builders) | folded |
| **N-d** | Three small items | `phase2-plan.md` 2.6 → "**the cadence-key deviation is DEV-3**" · `plan-overview.md` manifest (inventory-vendor row) → the sha256 pointer now says **`inventory-vendor/PROVENANCE.md`** (not README.md) — **and its sibling in the §Provenance table was corrected in the same sweep** (the class-sweep found 1 further `sha256 … README.md` pointer; 0 remain) · `fold-ledger.md` §K → the 12 k bound is **7 / 7** sites (6 + the `decisions.md` D4 `/series` cell added in the iteration-002 micro-fold) | folded |

### L.3 Remaining notes (carried, NOT folded — scope discipline)

| Note | Disposition |
|---|---|
| F12/F7 label drift | **NOTED-NOT-FOLDED** — the two families' catalog labels are owned by the catalog (§1 is authoritative on names); the plan set cites them by pointer |
| FR-F1 / G2 / G3 traceability | **NOTED-NOT-FOLDED** — the requirement rows' traceability is the requirements file's own; the plan set's phase traceability columns already cite the FR ids they implement |
| P4 task count "10 → 9 executing" | **NOTED-NOT-FOLDED** — the phase-map task count is a plan-time figure; the executor's executed count is the pack's own collect-only output (PROJECTION by construction) |
| F7 revert-path line | **NOTED-NOT-FOLDED** — a catalog-owned detail (the J-4 write seam's revert path), outside this lane's surface |
| v1 "(system view)" phrasing | **NOTED-NOT-FOLDED** — it describes the architect's v1 file, which this lane does not edit; the plan cites **v2** as the fold source and v1 only for diff provenance |
| Open-questions annotations | **NOTED-NOT-FOLDED** — the architect v2's open-question annotations are historical record; the plan set's §open-questions table carries the **adjudicated** outcomes |

### L.4 MANDATORY class-sweep results (run over the WHOLE plan set)

| Pattern | Pre-fix hits | Post-fix hits | Disposition |
|---|---|---|---|
| `six` (as a DEV count) | 1 | **0** | fixed (NB3 — the deviations intro now says "eight (DEV-1…DEV-8)") |
| `14/14` | 1 | **0** | fixed (NB2 — phase 2's exit criterion is 15/15) |
| `9 fams` | 1 | 1 | **carved**: the hit is in the architect's own file `architecture-recommendation-v2.md:200` (untouched by instruction) **plus** the ledger's §H.3 row that flags it; the plan set's own statement is corrected to "ten families" (NB4) |
| `nine fam` | 0 | **0** | n/a |
| `was a partial` | 2 | **0** | fixed (NB4 — both sites now say "F2 was adjudicated IN (full, 22 series) … 11→10") |
| `DEV-1…DEV-6` | 4 | 1 | 3 fixed (NB3); the survivor is the ledger's **W1 row**, explicitly labelled "at the council round … the register now carries DEV-1…DEV-8" — a labelled correction context |
| `D-1…D-10` | 2 | 2 | **carved**: both survivors are ledger rows — the W1 row (labelled, historical) and the §F verification-record row that lists the swept patterns with their 0-hit results |

### L.5 Iteration-003 verification battery

| Check | Result |
|---|---|
| Canonical frozen shape | **4/4 sites token-identical** (mechanically verified after normalising the markdown `\|` escape) |
| `max_points` 240 · 12 000 points · 3.6 ms | consistent across plan-overview / phase2 / phase3 / decisions |
| Chart-primary roster | 12 families, in `phase2-plan.md` task 2.7b |
| Step rendering | decisions D2 + phase5 5.3/5.4/5.9 |
| Mutation counts | declared vs claimed agree per phase: **3** (no numeric claim) · **15/15** · **12/12** · **13/13** · **11/11** · **12/12** |
| Registered pack ids | **9** distinct; registration obligation tasked at phase 1.7 |
| `census == 2` wording | invariant (iv) + P2-M13 + P3-M11 + plan-overview R4 + decisions D1 all name the **public property created by task 2.4a** (a read-through — no new check site) |
| Live stale values | **0** for every swept pattern outside labelled/architect-untouched contexts |
| Broken markdown table rows | **0** |
| Architect files / shared main checkout | **untouched** |

---

## M. LEADER-DISPOSITION SWEEP — FINAL PASS (approver cap reached)

**Status:** the fresh-approver iteration cap was reached (iteration 003 is not run). The leader performed a
**strictly mechanical disposition sweep** of the remaining findings — 7 blockers (F1…F7) + 3 named extras
(X1…X3), **zero scope movement**, each item one cell / one line — and the plan is
**APPROVED-BY-LEADER post-sweep**; implementation proceeds. Every quote below is the **actual post-fix
artifact**, verbatim, with its `file:line`.

| # | Item | Post-fix artifact (verbatim quote + `file:line`) |
|---|---|---|
| **F1** | D5 F18 row dual-stated | `decisions.md:479` — "\| F18 gateway \| **PARTIAL 5/9** \| **+5** \| `mcps_gateway/store.py:79/:103` roster + `tombstones.py` + the durable `mcp_gateway_audit` table \| **ADJUDICATED (D-5 + D-6 + AD4(a)) — PARTIAL 5/9, +5 series**: sessions (1) + tombstones (1) + `ensemble_mcps_gateway_audit_total{outcome}` (3) under the **amended vocabulary `{ok, error, timeout}`** …" — agreement re-grepped at `decisions.md:538` ("… F18 5 …"), `decisions.md:569` ("`{ok, error, timeout}`"), `plan-overview.md:384` (D-6), `plan-overview.md:428` ("**F18 5** (sessions 1 + tombstones 1 + audit_total{ok,error,timeout} 3 …)"), `phase4-plan.md:38` (amended vocabulary), `phase2-plan.md:67` (`ensemble_mcps_gateway_audit_total{outcome}` \| **step**) — **6/6 agree** |
| **F2** | D5 F7 row broke the 10-full+6-partial partition | `decisions.md:468` — "\| F7 completion \| **PARTIAL (wiring only)** \| +10 (21→31) \|" |
| **F3** | P3-G1 D1 list mismatch + missing overview rows | `plan-overview.md:310` — "\| P3-G1 \| `metrics_query_routes` · `metrics_query_auth_matrix` · `metrics_query_pagination` · `metrics_query_switch_invariance` · **`metrics_query_api_wiring`** (the D1 home of the lifespan wiring test — approver N-b) \| ~40 (PROJECTION) \| …" · `plan-overview.md:308` — "\| P1-G1 \| `central_metrics_contract_doc` \| ~16 (PROJECTION) \| none (Tier 0 — docs-only diff; changed-pack PASS, no dual-side leg) \| ~0 \|" · `plan-overview.md:313` — "\| P6-G1 \| `central_metrics_ledger_reconciliation` \| ~12 (PROJECTION) \| none (Tier 0 — docs-only diff; changed-pack PASS, no dual-side leg) \| ~0 \|" |
| **F4** | Registered pack-id list was 8 of 9 | `plan-overview.md:345` — "The registered ids are: `central_metrics_contract_doc` (P1-G1) · `metrics_p2_dual_side` · `metrics_p3_dual_side` · …" — **9 distinct ids** now |
| **F5** | P2 task count | **verified BEFORE writing:** the phase-2 task table has **11** rows (2.1–2.9 + 2.4a + 2.7b) — no STOP condition. `plan-overview.md:141` — "\| 11 (9 base + 2.4a + 2.7b) \|" · `fold-ledger.md:257` — "P2 task count 10 → **11** — the actual phase-2 task-table rows are **11**: 2.1–2.9 + 2.4a + 2.7b)" |
| **F6** | FR-D1 third surface (FE polling) | `phase1-plan.md:24` — "… and the **FE charts polling** surface (the third activation surface, leader F6): the FE polls the door's `snapshot` (10 s) and `series` (15 s) routes; when the door is unreachable or the session has expired the tab **degrades to its cached/last-good view, marked stale** (per `phase5-plan.md` task 5.4) rather than polling harder or blanking; the FE never polls the listener surface." + "The doc names **all six surfaces** (listener body · `/api/metrics` text · query routes · census DB work · registry accumulation · **FE charts polling**) …" · `phase6-plan.md:22` — "**the three activation surfaces, named together: the query API (auth-gated, always serving), the trend buffer (in-RAM, accumulating in both switch states), and the FE charts polling (degrading to its cached/stale view, never blanking)**" — **no DEV-9 row** (the leader chose the matrix+disclosure option) |
| **F7** | Ledger claimed the DEV table was re-ordered; the disk order was 1–5, 8, 7, 6 | **Option 1 executed — the table IS now re-ordered 1→8.** `plan-overview.md:401-408` — "\| **DEV-1** \| … \| **DEV-2** \| … \| **DEV-3** \| … \| **DEV-4** \| … \| **DEV-5** \| … \| **DEV-6** \| … \| **DEV-7** \| … \| **DEV-8** \| …" (the ledger claim at `fold-ledger.md` §L.1 NB3 therefore matches the artifact) |
| **X1** | Three stale "one ng-build leg" statements | `plan-overview.md:79` — "… Playwright e2e, **two build legs** (one `ng build --configuration development` for AOT **and** one `ng build --configuration production` for the budgets — AM-23)." · `plan-overview.md:505` — "… Phase-5 exit criterion + **two build legs**: one `ng build --configuration development` (AOT) **and** one `ng build --configuration production` (the only configuration carrying `budgets` + `outputHashing` — AM-23)." · `plan-overview.md:560` — "… — plus **two build legs** (`--configuration development` for AOT and `--configuration production` for the budgets, AM-23) — plus a service with static URL constants…" |
| **X2** | Duplicated sentence block in the overview | `plan-overview.md:200` — "The family-level **`role`** is the chart role from the **chart-primary roster** (task 2.7b; approver B2). **`buckets` is ON the wire** (approver B3 + leader ruling): the provider already derives the bucket boundaries…" — the fragment now occurs **exactly once** (verified: 1 occurrence of each half) |
| **X3** | Stale 6,000-pt row in the ledger's §F | `fold-ledger.md:133` — "\| AM-12 encode claim \| "BUILD-OFF-LOOP ONLY" + the pinned residual bound (≤ 6 000 points ≈ 1.8 ms) — **SUPERSEDED by approver B1 + leader ruling: the default `max_points` is 240, so the bound is ≤ 12 000 points ≈ 3.6 ms (see §I / §K); this row records the council-round figure only**; no pre-encoded-`Response` precedent \|" |

**Remaining approver notes** stay ledger-noted-not-folded (§L.3) — no action in this sweep.

**Nothing else moved:** gate tiers (P1 0 · P2 1 · P3 **2** · P3-G2 0/1 · P4 1×3 · P5 1 · P6 0) ·
9 registered pack ids · subset **16/26** · budget **+274 → 363 ≈18.2 %** with the 89/87 denominators ·
cages **108→111 / 37 / 33** · `max_points` 240 with the **≤ 12 000 points ≈ 3.6 ms** bound (7/7 sites) ·
canonical frozen shape token-identical at 4/4 sites · broken markdown table rows **0** ·
architect files v1/v2 **untouched** (sha `d0f573c3…` / `199ef838…`) · shared main checkout **untouched** ·
**no version string, no promote**.

## PLAN-AUTHORITY — the ordering/census pins (D-e)

**The durable rule:** every **ordering** or **census** pin is quoted from the plan set
**VERBATIM, with its `file:line`**. A dispatch brief, a task summary, a chat instruction or a
paraphrase is **never** the authority for an ordering pin — **the plan row is**.

**The authoritative order row — `phase3-plan.md:36` (task 3.4, AM-13).** Verbatim:

> start the sampler **LAST of the metrics trio** — after the census block (`api.py:2467-2483`),
> and **the listener starts FIRST and must NOT be reordered** (AM-13 corrects the earlier
> un-implementable "after census, before listener" instruction); on shutdown, the sampler stops
> **FIRST**, before the listener stop (`api.py:3700-3709`) with the detached-join discipline
> (listener `stop()` precedent `:551-593`).

and its acceptance clause, verbatim:

> The wiring test asserts the exact start order (listener → census → sampler) and the reverse
> stop order (sampler → listener)

**So the order IS `listener → census → sampler`** — the sampler is **LAST of the trio** — with
the **reverse stop order `sampler → listener`**. The earlier "census before listener start"
phrasing is **EXPLICITLY FORBIDDEN**: the row quoted above says so in its own words ("AM-13
corrects the earlier un-implementable 'after census, before listener' instruction"), and the
plan's own risk row records it — `plan-overview.md:511` — *"Lifespan start order ('before the
listener start' was un-implementable)"*, mitigated by *"Sampler starts **LAST** of the trio;
stops **FIRST** (AM-13)"*.

**The precedent (recorded, per the council):** *the developer's Stage-B dispatch brief inverted
this pin; the code followed the plan and was RIGHT; the brief was WRONG.* The brief's own text is
**not** in the repository, so this is recorded as the **council's ruling**, not as a quote of the
brief — the quotable authority is the plan row above.

**Why the rule exists (the pin surface):** `phase3-plan.md:92` (`P3-M5`) plants the defect that
the order forbids — *"the **listener start moved AFTER the census block** (the order AM-13
forbids: the listener starts FIRST, the sampler starts LAST of the trio)"* — and
`phase3-plan.md:98` (`P3-M10`) plants the un-implementable form — *"the sampler start moved
before the listener start (the un-implementable instruction)"*. Both are **RED-when-planted**
pins on the D1 pack `metrics_query_api_wiring`, so an inverted brief surfaces as a **RED leg**,
never as a shipped inversion. That is the whole point of quoting the row: the brief is not the
authority, and the authority is machine-checked.

**Census pins are covered by the same rule.** The census-related pins in the same row — the
census block at `api.py:2467-2483`, the check-site census (exactly **2**), and the
`CensusSampler.gated` public property (the read-through of the EXISTING private `_gate()`,
**no new check site**) — are quoted from `phase3-plan.md:36` and `decisions.md` §D1/§D3 with
their `file:line`, never from a summary.
