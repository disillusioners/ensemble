# Evidence pack — central-metrics-v1

> **Assembled:** 2026-11-02 (UTC) by the implementation lane, Phase 6 task **6.6**.
> **Lane:** `feature/central-metrics-v1` (worktree `/home/nea/ensemble-src-wt-central-metrics-v1`).
> **Plan set:** `.agents/shared/planning/central-metrics-v1/` — `plan-overview.md` (the frozen
> contract + the flags), `decisions.md` (the rulings), `fold-ledger.md` (why every pin exists),
> `phase{1..6}-plan.md` (the per-phase task rows and the declared mutation tables).
> **Contract:** [`docs/architecture/metrics-query-contract.md`](../../../../docs/architecture/metrics-query-contract.md)
> carries the per-phase evidence blocks this pack cites.
> **Machine-readable evidence preserved in this directory:**
> `evidence/am16-exposition-{base,head}.txt` (the AM-16 structural diff inputs),
> `evidence/p4-failed-digests.txt` (the recomputed P4 FAILED digests),
> `evidence/p1-mutations-final.log` + `evidence/p2-mutations-final.log` (the surviving Stage-A
> mutation logs) and `evidence/p1-p2-mutation-logs.sha256.txt` (their sha256 table + leg census).

**How to read this pack.** Every leg is recorded with its **declared tier**, its **D1/D2
split**, the **slicing proof**, the **pre-exclusion record** and the **expected-skip census**.
Node counts are the executor's **`--collect-only -q`** re-derivations, never the plan's
projections (the plan's rule: *projections only*). The pack records **what was observed**; where
a number could not be re-derived from a cited anchor it is marked **NOT RECOVERABLE** rather than
guessed.

---

## 1. The gate legs

### 1.1 The leg table

| Leg | Tier | Scope | D1 (lane-owned, HEAD-only) | D2 (dual-side) | D1 nodes | D2 nodes | Verdict |
|---|---|---|---|---|---|---|---|
| **P1-G1** | **0** | docs-only diff (the contract doc) | `central_metrics_contract_doc` | none (changed-pack PASS) | **21** | ~0 | PASS |
| **P2-G1** | **1** | the query substrate (provider · ring · sampler · narrowing · vocabulary) | `metrics_query_provider` 66 · `metrics_history_ring` 28 · `metrics_sampler` 20 · `metrics_registry_narrowing` 12 · `metrics_vocabulary_amendment` 11 | `metrics_p2_dual_side` | **137** | **281** | net-new **EMPTY** (digest-equal) |
| **P3-G1** | **2** | the query door (routes · auth matrix · pagination · switch invariance · API wiring) | `metrics_query_routes` 30 · `metrics_query_auth_matrix` 25 · `metrics_query_pagination` 24 · `metrics_query_switch_invariance` 18 · `metrics_query_api_wiring` 13 | `metrics_p3_dual_side` | **110** | **302** | net-new **EMPTY** (digest-equal) |
| **P3-G2** | **0/1** | the three-way scope cage | `metrics_query_scope_cage` (7) | `metrics_scope_cage_dual_side` (12) | **7** | **12** | PASS (the three-way form) |
| **P4-G1** | **1** | the W1 family cluster (F0.3 · F1 · F3 · F4/F5 · F6) | `metrics_family_w1` + the adjudication pack | `metrics_p4_w1_dual_side` | (the cluster file) | **494** | net-new **EMPTY** |
| **P4-G2** | **1** | the W2 family cluster (F7 · F8 · F9 · F10 · F11 · F12 · F13) | `metrics_family_w2` + the adjudication pack | `metrics_p4_w2_dual_side` | (the cluster file) | **494** | net-new **EMPTY** |
| **P4-G3** | **1** | the W3 family cluster (F17 · F18 · F21) | `metrics_family_w3` + the adjudication pack | `metrics_p4_w3_dual_side` | (the cluster file) | **494** | net-new **EMPTY** |
| **P5-G1** | **1** | the FE charts page (models · service · SVG primitive · page · bindings pins) | `metrics-fe-models` · `metrics-fe-service` · `metrics-fe-svg-chart` · `metrics-fe-page` · `metrics-fe-bindings-pins` | `metrics_fe_dual_side` | **119** | **138** declared / **1 801** derived | net-new **EMPTY** |
| **P6-G1** | **0** | the docs-only diff (catalog + docs + plan set) | `central_metrics_ledger_reconciliation` | none (changed-pack PASS) | ~12 (declared) | ~0 | see §7 (the pack id is the tester lane's to register) |

### 1.2 The observed results

| Leg | HEAD (D1 + D2) | BASE (D2) | FAILED digest (BARE-ID / GREP-FULL) | Verdict |
|---|---|---|---|---|
| P1-G1 | 21 nodes, changed-pack PASS | n/a (docs-only) | — | PASS |
| P2-G1 | D1 137 + D2 281 green | D2 281 green | digest-equal | net-new **EMPTY** |
| P3-G1 | D1 110 + D2 302 green | D2 302 green | digest-equal | net-new **EMPTY** |
| P3-G1 blast-radius supplementary | 226 nodes, both legs green | 226 nodes, both legs green | digest-equal | net-new **EMPTY** |
| P3-G1 whole-tree (sharded) | HEAD **32 725** nodes | BASE **32 450** nodes | net-new **0** | net-new **EMPTY** |
| P3-G2 | D1 7 + D2 12 green | D2 12 green | digest-equal | PASS |
| P4-G1 (W1) | 534 passed · 1 failed · exit 1 | 493 passed · 1 failed · exit 1 | `d78b09cf2549c371` / `d9bff11a731d8ba3` — **IDENTICAL both sides** | net-new **EMPTY** |
| P4-G2 (W2) | 536 passed · 1 failed · exit 1 | 493 passed · 1 failed · exit 1 | `d78b09cf2549c371` / `d9bff11a731d8ba3` — **IDENTICAL both sides** | net-new **EMPTY** |
| P4-G3 (W3) | 528 passed · 1 failed · exit 1 | 493 passed · 1 failed · exit 1 | `d78b09cf2549c371` / `d9bff11a731d8ba3` — **IDENTICAL both sides** | net-new **EMPTY** |
| the P4 widened supplementary leg | 295 / 296 / 289 passed · 2 failed · exit 1 | 278 passed · 2 failed · exit 1 | `ec0572e69c9ab178` / `041380df27ec0d4e` — **IDENTICAL both sides** | net-new **EMPTY** |
| P5-G1 D1 | 119 passed · 0 failed · exit 0 | SKIPPED-BY-CONSTRUCTION (the files do not exist at the base) | — | the lane's own corpus is green |
| P5-G1 D2 (declared) | 138 passed · 0 failed · exit 0 | 137 passed · 0 failed · exit 0 | (empty both sides) | net-new **EMPTY** |
| P5-G1 D2 (derived, the widened supplementary) | 1 799 passed · 2 failed · exit 1 | 1 789 passed · 2 failed · exit 1 | `95c48e0111632e3` / `92b3ecbf67931b30` — **IDENTICAL both sides** | net-new **EMPTY** |
| P5-G1 the full FE corpus | **4 279 passed · 9 failed · 4 288 total** (134 suites, 110 s) | 9 failed on the same four files | `05d054bb483a90b0c769e8f270d6ba2e15ecc375bc61c22d8c8c4d73830c1b7d` — the 9 failing node ids **BYTE-IDENTICAL** on both sides | net-new **EMPTY** |
| P5-G1 the e2e | **2/2 PASS** (31.3 s) — the journey + the deep-link | n/a | — | PASS (incl. the **STEP-rendering** assertion) |
| P5-G1 the scoped type-check | exit 0 | n/a | — | PASS |
| P5-G1 the build legs | dev **exit 0** · production **exit 0** (10 pre-existing budget WARNs, **none** in the lane's files) | — | — | PASS |

**The P4 digests were independently RE-VERIFIED** in this pack (recomputed 2026-11-02 from the
surviving gate logs, not copied): see `evidence/p4-failed-digests.txt` — all six D2 legs and all
six widened-leg digests match the table above **exactly**, both sides.

### 1.3 The slicing proof

Every leg's selected node set equals its declared scope. The node identities were rebuilt from
`pytest --collect-only -q` (never from a hand-maintained list), and the per-file breakdown is
recorded in the contract doc's per-phase tables.

**Two slicing-proof notes:**
- The **P3-G2 D1 pin** lives in its **own** file on purpose — a D1 pin embedded in a D2 file
  would change that file's node count between the legs, and the slicing proof would stop holding.
- The **P3-G1 `gate-pack-ids` block was NOT widened**: the P3-G2 D1 pin has no id in the plan's
  nine-id table, so its file + registration input are recorded here (7 nodes) rather than adding
  a tenth id — the `central_metrics_contract_doc` pack pins that block at exactly nine.

### 1.4 The pre-exclusion registry (applies to every leg)

- `regression_performance` — excluded (> 300 s).
- e2e specs that need a live daemon on `:8079` — excluded (→ Release Gate).
- `tests/packs/g7` — excluded (a module-level `sys.exit` collection trap; run via packs, never
  bare pytest).

### 1.5 The expected-skip census (AM-22)

- The `no_xdist` self-skip (`tests/conftest.py`).
- PG-gated packs that skip without `PG_TEST_ALLOWED_DBS`.
- **The rule:** a pack whose skip count differs between the HEAD and BASE legs is a **finding**,
  not a convenience. **No skip-count divergence was observed on any leg.**

---

## 2. The mutation → test tables

### 2.1 The declared tables and the observed results

| Phase | Declared legs | Observed | Where the table lives |
|---|---|---|---|
| P1 | `P1-M1…P1-M3` (3) | **3/3** RED→GREEN | `phase1-plan.md` |
| P2 | `P2-M1…P2-M15` (15) | **16 observed** (P2-M1..M15 **+ the M13b variant**) — a **superset** of the declared 15 | `phase2-plan.md` |
| P3 | `P3-M1…P3-M12` (12) | **12/12** RED→GREEN | `phase3-plan.md` |
| P4 | `P4-M1…P4-M13` (13) | **13/13** RED→GREEN | the contract doc §"The P4 evidence" (the table carries the RED node per leg) |
| P5 | `P5-M1…P5-M11` (11) | **11/11** RED→GREEN | the contract doc §"The P5 evidence" (the table carries the RED node per leg) |
| P6 | `P6-M1…P6-M12` (12) | declared for the reconciliation pack | `phase6-plan.md` |

> **The P2 count — RESOLVED BY SURVIVING EVIDENCE.** The **16** observed legs are the plan's
> declared **15** (`P2-M1…P2-M15`) **plus an explicitly-named `P2-M13b` variant** — a **second
> planted form of the M13 pin** (M13 plants a *direct switch read* in the query package; M13b
> plants the *removal of the public `CensusSampler.gated` property*). It is therefore an observed
> **SUPERSET** of the declared 15 — **not** a discrepancy and **not** a supersession: the declared
> 15 all ran, and one extra leg ran beside them.
>
> **The surviving Stage-A log is preserved** at `evidence/p2-mutations-final.log` (sha256
> `be0fe2e24da1ce20e254dc5c14fa1e584b6bb21e468e5e3435af370b44fd29c1`; byte-identical to the
> `/tmp` original) — it records **16** `RED-OK` legs and ends with the **RESTORED** leg
> (`156 passed` → `GREEN-OK`). The P1 log is preserved beside it at
> `evidence/p1-mutations-final.log` (sha256
> `f59dd12b5b22bbef3777761047688c046ae0e3f518608943da1fea7e514e835e`; **3** legs, `GREEN-OK`).
> The sha256 table + the leg census are in `evidence/p1-p2-mutation-logs.sha256.txt`.
>
> **A tester re-run is OPTIONAL at functional-gate time — never a blocker.** The surviving log
> is the evidence; the declared 15 and the observed 16 agree in substance (the extra leg is a
> second form of a declared pin, not an undeclared behaviour).

### 2.2 The observed digests

The **observed digests** are the FAILED-list digests of §1.2 (BARE-ID and GREP-FULL forms). They
are the digest evidence for the net-new verdict on every dual-side leg, and they are the
**equality** the gate asserts — not the pass/fail counts.

### 2.3 The surviving machine-readable mutation evidence

| Evidence | Where | What it records |
|---|---|---|
| the **P1** mutation log | `evidence/p1-mutations-final.log` (sha256 `f59dd12b5b22bbef3777761047688c046ae0e3f518608943da1fea7e514e835e`) | **3** legs (`P1-M1…P1-M3`), each `RED-OK`, ending with the RESTORED leg (`21 passed` → `GREEN-OK`) |
| the **P2** mutation log | `evidence/p2-mutations-final.log` (sha256 `be0fe2e24da1ce20e254dc5c14fa1e584b6bb21e468e5e3435af370b44fd29c1`) | **16** legs (`P2-M1…P2-M15` **+ `P2-M13b`**), each `RED-OK` with its RED target node, ending with the RESTORED leg (`156 passed` → `GREEN-OK`) |
| their sha256 table + leg census | `evidence/p1-p2-mutation-logs.sha256.txt` | the digests, the `/tmp` source paths + sizes + mtimes, the leg census and the RESTORED-leg lines |
| the **P5** mutation summary (machine-readable) | the lane's own evidence dir (`/tmp/metrics-fe-mutations/summary.json`) | each of the 11 P5 mutation legs with `red_exit`, `green_exit`, `restored_byte_identical` and the log path. All 11 carry `status: PASS` and `restored_byte_identical: true` — i.e. **every mutation was reverted byte-identically** after planting, so no mutation leaked into the tree |
| the **P4** FAILED digests | `evidence/p4-failed-digests.txt` | the recomputed digests for all six D2 legs and all six widened-leg legs (bare-id + grep-full forms, both sides) |

---

## 3. The re-derived budget table (measured, not projected)

**Three DIFFERENT quantities — never conflate them.** The first two are the catalog's; the third
is this lane's measurement.

| # | Quantity | Value | Provenance | Recipe |
|---|---|---|---|---|
| 1 | The **daemon-origin family budget** (the CATALOG's plan-time budget for the whole program, F0.1→F22) | **582** (P1b **265**) | catalog §1 + §14.1/14.2/14.3 — the ledger itself | the catalog §14.1 four-step derivation recipe |
| 2 | The **registered-today baseline** — the two published denominators | **89** (F7c-inclusive) / **87** (ledger-rows basis) | catalog §1 + §14.1's derivation recipe; the shipped code pins the F7c-inclusive one | `F0.1 16 + F0.2 3 + F0.4 1 + F7-today 21 + F7b 46 + F7c 2 = 89`; the same set minus F7c's 2 = 87 |
| 3 | The **lane's pinned planning projection** | **+274 → 363** (F7c-inclusive; 361 on the ledger-rows basis) | the plan set's `decisions.md` D5 + `plan-overview.md` §Re-derived budget; the shipped constant `adjudication.BUDGET_SERIES = 363` | the D5 per-family delta table (10 full-IN families = 217, 6 partial-IN families = 57) |
| 4 | The **lane's MEASURED closed label space** (the ceiling the shipped gate measures) | **336** lane series ⇒ **425** against the 89 baseline | **measured from the shipped runtime** (this pack) | the shipped pack's own `_closed_label_space` recipe (a gauge/counter contributes one series per label combination; a histogram contributes `buckets + 2` per combination), applied to `registry.registered_family_metrics()` |
| 5 | The **lane's LIVE count** | **at or UNDER the pinned 363** | the lane's P4 evidence (three named shortfalls, independently re-verified below) | see the shortfall table |

### 3.1 The measured closed label space, per family

| Family | measured (closed label space) | the pinned band | the catalog's live estimate | note |
|---|---|---|---|---|
| F0.3 | 8 | 8 | 8 | at the band |
| F1 | 9 | 9 | 9 | at the band |
| F3 | 6 | 6 | 6 | at the band |
| F4 | **14** | 14 | 8 | above the catalog's live estimate — the estimate assumed a subset of the label product |
| F5 | **4** | 4 | 3 | same |
| F6 | 15 | 15 | 15 | at the band |
| F7 | **20** | 20 | +10 | same |
| F8 | 4 | 4 | +4 | at the band |
| F9 | 53 | 53 | 53 | at the band |
| F10 | 6 | 6 | 6 | at the band |
| F11 | 16 | 16 | +16 | at the band |
| F12 | **104** | 117 | 69 | under the band — see the shortfalls |
| F13 | 19 | 19 | +19 | at the band |
| F17 | 40 | 40 | 40 | at the band |
| F18 | 5 | 5 | +5 | at the band |
| F21 | **13** | 32 | +3 | the release-version label set is dynamic (the release directory's own names) |
| **TOTAL** | **336** | — | **274** | the ceiling vs the live estimate — **different conventions**, not a discrepancy |

**Reading the table.** The shipped gate asserts `BASELINE_SERIES + measured_closed_space <=
GLOBAL_SERIES_CAP` — i.e. it measures the **ceiling** and compares it against the **global
≤2 000 cap** (425 ≤ 2 000 ✓), **not** against the plan's 363 planning projection. The plan's
+274 is the catalog's **live-estimate** convention. The ceiling is ≥ the live estimate for every
family by construction, so the two rows are **not** comparable — and neither may be substituted
for the other.

### 3.2 The three named shortfalls (why the LIVE count lands under the pinned figure)

Each was **independently re-verified** in this phase:

| # | Shortfall | Independent verification |
|---|---|---|
| 1 | **F12's `ensemble_sweep_cycle_seconds` is OUT** — no sweep service publishes an observed cycle DURATION; publishing the configured interval under an observed name would be a fabrication | the metric is **not registered** — `daemon/services/metrics_query/wiring/w2.py:327-335` carries the explanatory comment, and the registered F12 metric list holds only `cycles_total` / `errors_total` / `rows_total` |
| 2 | **10 of the 13 sweep lanes render no series** — they expose no `counters()` reader | `daemon/services/metrics_query/wiring/sources.py:169-190` registers **six** sweep services; only **three** of them expose a `counters()` method (`watch_reconcile_sweep.py`, `eligible_pending_sweep.py`, `orphan_watcher_sweep.py` — each has `def counters`); `job_lock_sweep.py`, `upgrade_journal_sweep.py` and `stale_pause_sweep.py` have **none** ⇒ 13 − 10 = **3** lanes with a reader |
| 3 | **F4's manager-pool `max` / `available` render no series** — the SQLAlchemy pool exposes neither natively | `daemon/services/metrics_query/wiring/w1.py:555-570` — the manager pool publishes `size` + `overflow` only; the checkpoint pool publishes `size` / `max` / `available`. The comment states the reason verbatim |

**The exact live total is observation-dependent** (it depends on which lanes publish at runtime
and on the observed `(lane, outcome)` combinations) and is therefore **NOT statically derivable**
from the repository. It is **not** asserted here. What is asserted: the live count is **at or
under** the pinned 363, and the three shortfalls above are the named reasons.

---

## 4. The F7c reconciliation outcome — **582 WINS**

| Where | Before | After |
|---|---|---|
| catalog §1's daemon-origin total | 582 (F0.1→F21 + F7b + F7c) | **unchanged** — §1 was already correct |
| catalog §1's delta trail | `… + 46 (F7b) + 2 (F7c) = 582` | **unchanged** |
| catalog §7's roll-up + trail + bound | 580 / 263 / ≈1 160 | **582 / 265 / ≈1 162** |
| catalog §7.1's task/owner rows | no F7c row | **the F7c row added**; the "no §4 task row" note now names **F7b AND F7c** |
| catalog §8's freeze marker | not stated | **stated**: `ensemble_metrics_vocabulary_version` = **2** |
| catalog §14.1's total row | **580** · "F0.1–F21 + F7b" | **582** · "F0.1–F21 + F7b + F7c" |
| catalog §14.1's dependent rows | grand total ≈651 · headroom 1 420 · families 26 · bounds ≈1 160 / 822 / 1 145 · headroom 1 178 / 855 | **≈653 · 1 418 · 27 (28 = 27 + F22) · ≈1 162 / 824 / 1 147 · 1 176 / 853** |
| catalog §14.2's P1b row | **263** (`…+46`) | **265** (`…+46+2`); the daemon total **582** |
| catalog §14.3 | no F7c row | **the F7c row (2 series) added** |
| catalog §14.4 | `F7 checkpoint op` 4 · `Checkpoint op` 4 · `Gateway audit outcome` 2 | **2 · 2 · 3** (all flagged **CHANGED**) |
| catalog §14.5 | 580 / 263 / ≈651 | **582 / 265 / ≈653**, plus the 89/87 denominators' pointer |
| catalog §11 | C96 was the last entry | **C97 · C98 · C99 added** |

**The verdict, stated once:** §1 (the canonical name registry) carried an explicit **F7c row
worth 2 series** and its own arithmetic included the `+2 (F7c)` term; the §14 ledger rows were
**stale-by-2** — the F7b retro-cataloguing (C95) carried F7b's 46 but never carried F7c's 2.
**582 wins.** **Both denominators are published: 89 (F7c-inclusive) / 87 (ledger-rows basis).**

---

## 5. The F0.3 WS-A ancestry verdict

| Field | Value |
|---|---|
| The probe | `git merge-base --is-ancestor 53547b915 HEAD` |
| **rc** | **0** |
| The rc semantics (pinned by the pack) | **rc=0 ⇒ PASS · rc=1 ⇒ DEFER · anything else ⇒ STOP** |
| **Verdict** | **PASS** — F0.3 **SHIPS**, **+8 series** |
| Its family pack | `metrics_family_f0_3` (in `metrics_family_w1`) — **runs** (it is not conditional) |
| The defect the first implementation had | the first probe compared a **commit** to a **path** — git answers **128**, which the rc semantics map to **STOP**. The probe now compares **two commit-ishs**. The rc SEMANTICS are pinned by the pack (`P4-M13` — the inverted probe is a RED leg) |

The F0.3 **+8** is inside the measured 336 (F0.3's closed label space is 8 — 4 spine tables × 2
metrics).

---

## 6. The F21 conditionality evidence

| Field | Value |
|---|---|
| The question | the D5 row marked the F21 phase families **CONDITIONAL** — "IN only if journal history entries already record phase transitions enabling pure-read derivation — plan-time verify; else OUT" |
| **The evidence** | `journal_history_append` writes `{ts, event, detail}` with `event` drawn from the **ALERT-CLASS** kind set (`daemon/tools/upgrade_journal.py:645-649` — halt / refusal / rollback / sweep_rollback / quarantine), and the promote **PHASE** trail lives in the journal's **EXTENSION** fields, **not** in `history[]` |
| **Verdict** | **OUT** — confirmed on the evidence. There is **no clean pure-read derivation**, so the F21 phase families stay OUT (**+30 series not claimed**) |
| **The absence is pinned** | `P4-M7` — the F21 phase families' absence is a RED-when-registered pin |
| What F21 DOES ship | the **three read gauges** — `ensemble_journal_file_bytes` · `ensemble_journal_history_entries` · `ensemble_upgrade_release_current` (a PURE READ of the twin's own accessors) |

---

## 7. The two frozen-contract structural diffs (AM-16, the 3-carrier allowlist)

**The contract (AM-16).** The `:9798` and `/api/metrics` bodies must be **structurally identical**
to the base artifact for the same registry state — the same series set, TYPE lines, label
vocabularies and HELP structure — **except the enumerated 3-carrier value-delta allowlist**:

1. the `# frozen vocabulary version: N` **footer**,
2. the `ensemble_metrics_vocabulary_version` **gauge value**,
3. the `# HELP ensemble_checkpoint_op_seconds …` **text**.

**Any delta outside the allowlist = a gate red** (`P3-M8`).

**The observed diff — re-verified in this pack** from the preserved inputs
(`evidence/am16-exposition-base.txt` · `evidence/am16-exposition-head.txt`):

| # | Line | Carrier | BASE | HEAD |
|---|---|---|---|---|
| 18 | `# HELP ensemble_checkpoint_op_seconds …` | **carrier 3 — the F7 HELP, under its ALIAS spelling** | `Ops: aget, aput, adelete, alist (migration-only)` | `Ops: aget (the only observed saver op) and other (the reserved fold target)` |
| 20 | `# HELP message_api_saver_op_latency_seconds …` | **carrier 3 — the F7 HELP, under its CANONICAL spelling** | `Ops: aget, aput, adelete, alist (migration-only)` | `Ops: aget (the only observed saver op) and other (the reserved fold target)` |
| 31 | `ensemble_metrics_vocabulary_version` | **carrier 2 — the version gauge** | `1` | `2` |
| 80 | `# frozen vocabulary version:` | **carrier 1 — the version footer** | `1 (current-release gauge only)` | `2 (current-release gauge only)` |

**4 changed lines, 3 carriers.** The F7 HELP carrier renders under **BOTH** the alias
(`ensemble_checkpoint_op_seconds`) and the canonical name
(`message_api_saver_op_latency_seconds`) — the exposition mask covers **both spellings**, because
an allowlist that covered one spelling of a two-spelling carrier would be **incomplete against
its own carrier** (the same class as the P2-G1 / P3-G1 composition-vs-blast-radius finding).
**The contract's own text is unchanged: it names the CARRIER, not its spellings.**

**The digests of the preserved inputs:**
`am16-exposition-base.txt` → `ba0dfcdc639e237e3164a988e7caebfaf0371b335272cda657ee36f8aaa7e201` ·
`am16-exposition-head.txt` → `e31d2e3fb31d737935bd255a6e717c6f899f75bd96e3e89a8ca24cab529cf399`.

---

## 8. The vendor provenance table (AM-24)

**The vendored evidence** (`.agents/shared/planning/central-metrics-v1/inventory-vendor/`) — the
sources live **untracked** in the shared main checkout, so any committed citation of them dangles
in every other checkout.

| File | Source (READ-ONLY) | Source status | Source sha256 | Vendored-body sha256 | Verdict |
|---|---|---|---|---|---|
| `README.md` | `/home/nea/ensemble-src/.agents/shared/planning/metrics-unification/README.md` | **untracked** (`??`), never committed | `1bd508a730fd2fdff7f5156297cd778c13f42e5a1fbe535c41df59df30137b2c` | `1bd508a730fd2fdff7f5156297cd778c13f42e5a1fbe535c41df59df30137b2c` | **BYTE-IDENTICAL** |
| `metrics-inventory-backend-producers.md` | `…/metrics-unification/metrics-inventory-backend-producers.md` | **untracked** | `4101ac1c9d8269a6d6fe812927087fb09b3c5191eb6798f8cdcbe3c03b6ee85b` | `4101ac1c9d8269a6d6fe812927087fb09b3c5191eb6798f8cdcbe3c03b6ee85b` | **BYTE-IDENTICAL** |
| `metrics-inventory-surfaces-fe-deps.md` | `…/metrics-unification/metrics-inventory-surfaces-fe-deps.md` | **untracked** | `f4cc5c4ffdb10028c21fe8ffa38b12855efc9736938b10e0137362b4c60373d8` | `f4cc5c4ffdb10028c21fe8ffa38b12855efc9736938b10e0137362b4c60373d8` | **BYTE-IDENTICAL** |

The vendored files are **larger** than their sources by **exactly** the 8-line provenance header
(+743 / +772 / +771 bytes) — the transformation is **prepend-only**, and the bodies are
byte-identical. The source-side integrity was **re-confirmed** in this phase: the three files are
still `??` (untracked), `git log --all` over the directory returns **0 commits**, and the source
mtimes (2026-11-01 19:46–19:59Z) are **older** than the vendoring (2026-11-02 00:39Z) — nothing
in the source moved since the copy. **Nothing in the shared main checkout was created, modified
or deleted.**

The full recipe + the full-file sha256 table live in `inventory-vendor/PROVENANCE.md`.

---

## 9. The FE-manifest delta (AM-23)

| Field | Value |
|---|---|
| What the staged `frontend` tree is | the **BUILT** bundle (`frontend/dist/frontend/browser`), not the source tree |
| `frontend_manifest` entry count | **93 → 95** (+2 `chunk-*.js`: the metrics page's lazy chunk — **25 938 B** in the local production build, carrying the `metrics-empty-panel` marker — plus one shared chunk split by the new import graph) |
| `frontend_tree_sha256` | **CHANGES** (recomputed at stage time — it cannot be predicted pre-stage) |
| `fe_index_sha256` | **CHANGES** (`index.html` references the new `main-*.js` hash) |
| the per-file map | a full chunk-hash cascade — **83 added / 81 removed / 1 changed** against the v0.28.5 staged manifest. **The cascade is the hash naming, NOT a payload change** |
| `agents/` and `plugins/` trees | **UNCHANGED** by this phase |

**The build legs.** dev AOT **exit 0**; production **exit 0** with **10 budget WARNINGS** — every
one **pre-existing** and in **another component's SCSS** (the initial bundle 5.91 MB vs the 1 MB
warn budget · `agent-selector.scss` 8.77 kB · `add-source-modal.scss` 8.32 kB ·
`blueprint.component.scss` 18.32 kB · `checkpoint-cleanup.component.scss` 8.70 kB ·
`snapshot-detail-drawer.component.scss` 8.70 kB · `snapshots.component.scss` 12.29 kB ·
`instance-list.scss` 10.17 kB · `jobs.component.scss` 14.77 kB ·
`chat-interface.scss` 16.15 kB). **NONE of the lane's own components is over the 8 kB warn
budget.** They are **recorded findings, never auto-fixed.**

---

## 10. The flag dispositions — D-1 … D-11 (all eleven)

| Flag | The conflict | The disposition (the architect's / approver's ruling) |
|---|---|---|
| **D-1** | the trend cadence key | **ADJUDICATED (AM-15): CONFIRM-WITH-AMENDMENT.** The key stays the sampler **TICK** (`limits.metrics_sampler_interval_s`, boot-applied); the envelope's `window.interval_s` reports the **observed effective spacing** `max(sampler, publisher)` — never the configured cadence |
| **D-2** | the series budget arithmetic | **ADJUDICATED (AM-26): CONFIRM + AMEND.** D5's printed 78 / +295 / ≈384 is **OVERTURNED** (its own rows summed to 76); the F7c reconciliation is the prerequisite; **both denominators are pinned: 89 / 87**. **Outcome folded into Phase 6** — see §4 |
| **D-3** | F8 `prune_runs_total{outcome}` — requirements say IN, decisions say OUT | **OUT** — `checkpoint_prune.py:91` `would_delete_count` is a potential count, not an outcome counter. **Confirmed** by the lane: the metric is absent from the registry and the absence is pinned |
| **D-4** | the F21 phase families | **CONDITIONAL ⇒ OUT** — the conditionality was verified on the evidence (§6): the phase trail lives in the journal's extension fields, so there is no pure-read derivation |
| **D-5** | F18's scope — requirements say IN (full), decisions say PARTIAL | **PARTIAL 5/9** — `sessions` + `tombstones` + `audit_total{outcome}` (the amended vocabulary); `session_total{outcome}` stays OUT. **Confirmed** by the lane (the measured F18 closed label space is 5) |
| **D-6** | the F18 amendment's target set | the **WRITTEN** set is **3** (`ok`, `error`, `timeout`) — `OUTCOME_DENIED` is declared+exported but **never written**, so it is excluded from the frozen vocabulary. **Implemented and measured** (the runtime's F18 outcome set is exactly 3) |
| **D-7** | F7's narrowed enum membership | **narrow to the observable set** — the runtime's canonical `CHECKPOINT_OP_VOCABULARY` is `{aget, other}` (2 values), and the catalog was amended accordingly (§4) |
| **D-8** | FR-E5 (an FE "metrics disabled" state) vs D1 (the door is never switch-gated) | **RESOLVED** — the FE empty state is **availability-driven**: the panel appears when **no family is live**, and it reads *"No metrics available yet"* — **never** "metrics disabled". **Implemented** (the copy constant is `METRICS_EMPTY_PANEL_COPY`) |
| **D-9** | the `api_families` census target | **ADJUDICATED (AM-21): the TARGET is OVERTURNED.** The real pin is the **declared-route census `108→111`**; the mount census stays 37; the class-manifest family count stays 33. **Implemented** — the three-way cage pack asserts all three |
| **D-10** | the `VOCABULARY_VERSION` bump | **AD5** — bump 1→2 (the F7 narrowing + the F18 amendment). **Implemented**: the runtime's `VOCABULARY_VERSION` is **2**, and the catalog's §8 freeze marker now states it |
| **D-11** | (a) AC-A.5 "byte-identical exposition" vs AC-F.4's vocabulary bump; (b) "Must-empty-when-OFF" on the door | **ADJUDICATED (AM-16 / AM-17):** (a) the exposition contract is **structural identity + the 3-carrier allowlist** (§7); (b) **D1(b) wins** — the door is never switch-gated, and the OFF semantics are **availability, not emptiness**. **Both implemented and verified** |

---

## 11. The deviation dispositions — DEV-1 … DEV-8 (all eight)

| Dev | The stale copy | The disposition |
|---|---|---|
| **DEV-1** | requirements Theme B still adjudicates F8 `prune_runs_total{outcome}` as IN | **D-3 CONFIRMED: OUT** |
| **DEV-2** | requirements Theme B still adjudicates F18 as IN (full) | **D-5 CONFIRMED: PARTIAL** (5/9) |
| **DEV-3** | requirements FR-C7 still says the trend cadence defaults to `metrics_scrape_interval_hint_s` | **D-1 CONFIRM-WITH-AMENDMENT (AM-15)** — the tick is `metrics_sampler_interval_s`; the reported interval is the **observed** spacing |
| **DEV-4** | requirements Theme B still adjudicates F2 as IN (full) | **AM-25 / LR-2 CONFIRMED: F2 is OUT of v1 by VALUE ruling** — with its verbatim flip condition published in the OUT-list |
| **DEV-5** | requirements C-10 / NFR-5 still carry the pre-fold budget numbers | **NARROWED by the council-W2 fold**; the budget is now the measured table in §3 |
| **DEV-6** | requirements FR-E5 / NFR-9 still name the "metrics disabled" copy | **NARROWED by the council-W2 fold**; the copy is "no metrics available yet" (D-8) |
| **DEV-7** | requirements FR-D2 still says the two-class kill-switch rule "SHALL be the governing evidence" | **AM-19 + D1's demotion** — the rule is a **supporting precedent**; the load-bearing basis is intra-family consistency + the verified blast-radius census |
| **DEV-8** | requirements FR-A4 says the interface "SHALL publish a contract version (semver)" | the frozen contract publishes an **integer** `schema_version` (the envelope-shape axis) **plus** a separate integer `vocabulary_version` (the label-vocabulary axis) — two axes, not semver. **Implemented**: `QUERY_SCHEMA_VERSION = 1` + the vocabulary mirror |

---

## 12. The implementation-phase deviations (recorded, never silent)

| # | The deviation | Where it is recorded |
|---|---|---|
| 1 | The **series budget** landed under the pinned projection (the three shortfalls) | §3.2 |
| 2 | The **P4 bridge does not import `daemon/api.py`** (a mechanical Tier-2 file the plan forbids touching) — it looks the already-imported module up in `sys.modules` (`daemon/services/metrics_query/wiring/sources.py:128`). The shipped code gives a second reason: importing it from there would create a **circular import** (`daemon.api` imports the wiring package). Consequence: in a process where `daemon.api` was never imported, the families degrade to "no series"; the running daemon always has it imported (the uvicorn entry is `daemon.api:app`). The alternative was one line in `api.py` forcing a whole-tree Tier-2 leg | the lane's P4 evidence; `sources.py:20-25` + `:118-129` |
| 3 | The plan's **DECLARED dual-side compositions were short** vs the blast-radius derivation in **P2, P3 AND P4** — the derived sets ran as **disclosed supplementary legs** (digest-equal) and the widened registration input is recorded for the tester lane | §1.2 + the contract doc's per-phase FINDINGs |
| 4 | The whole-tree single-shot `-n auto` form **aborts rc=3 on BOTH legs** (an xdist worker exits mid-node; reproduced at BASE). The first attempt's "digest equal" was **VACUOUS** (both legs aborted before digests existed) and was recorded as a **FAILED leg**, then re-run in the sharded form (BASE 32 450 / HEAD 32 725 ⇒ net-new 0) | §1.2 |
| 5 | The **F7 HELP carrier renders under both spellings** — the exposition mask covers both | §7 |
| 6 | `db_offload` is a **process-level singleton** bound to the first loop that uses it (a test-harness caveat; production unaffected) | the lane's P3 evidence |
| 7 | The pre-existing base red `test_rdrs_lanes_come_from_the_config_kill_switchs` (an 8-vs-7 RDRS lane drift, identical on both legs) is **NOT lane-attributable** — fixing it would edit `SWEEP_LANE_VOCABULARY`, an exposition footer **outside** the AM-16 allowlist | §1.2 + `evidence/p4-failed-digests.txt` |
| 8 | The exposition **base golden** lives under `tests/unit/metrics/baselines/` (the blanket `data/` gitignore would have swallowed it) | the lane's P4 evidence |
| 9 | The **P3-G2 D1 pin has no id** in the plan's table — its file + registration input are recorded; the contract's nine-id block was **NOT** widened | §1.3 |
| 10 | The FE's **9 pre-existing reds** (the date-dependent timezone-DST family + the jobs-grouping / jobs-filter-state) are byte-identical on both legs; `tsconfig.spec.json` is **RED at base** (a stray `});` at `src/app/app.component.spec.ts:2127`) — **not a lane file**, untouched, reported | §1.2 |
| 11 | The FE **chart mode** comes from the catalog's per-name `chart_role` with the door's **kind-derived fallback** (counter ⇒ step, else line) | the published FE page §3 |
| 12 | Stage A's disclosed items: the family-level `role='chart'\|'stat'` + the per-name `chart_role='line'\|'step'` (contract §4.3); **three added public accessors**; `?names=` over the 50-name ceiling ⇒ **400 `invalid_label_filter`** (never a silent truncation); the histogram series carry the **stored cumulative observation count** as the point value; **one disclosed test edit** re-pointing two stale saver-op assertions | the contract doc §4.3 + the lane's P2 evidence |

---

## 13. The open questions this pack could not close

1. ~~**The P2 mutation count.**~~ **RESOLVED (2026-11-02) — by surviving evidence, not by
   re-derivation.** The Stage-A mutation logs survive on disk and are now **preserved in this
   lane** (`evidence/p1-mutations-final.log` · `evidence/p2-mutations-final.log` + the sha256
   table `evidence/p1-p2-mutation-logs.sha256.txt`). The P2 log records **16** legs — the declared
   `P2-M1…P2-M15` **plus the explicitly-named `P2-M13b` variant** (a second planted form of the
   M13 pin) — each `RED-OK`, ending `RESTORED → 156 passed → GREEN-OK`. The observed 16 is a
   **superset** of the declared 15, not a discrepancy. **A tester re-run is OPTIONAL at
   functional-gate time — never a blocker.**
2. **The exact live series count.** Observation-dependent, therefore **not statically
   derivable** (§3.2). The disclosure carries the **measured closed label space (425)** as-is,
   with the non-derivability statement, and **no live number is asserted** (the running daemons do
   not carry the lane). **Resolution path:** read `live_series_count` from
   `/api/metrics/families` at the **staged boot smoke** — once, from the staged artefact — and
   record it in the disclosure before the flip (see the stage-prep checklist §3).
3. **The P2/P3 per-leg FAILED digests.** The contract doc records the P4 and P5 digests; the P2
   and P3 legs are recorded as "digest-equal" without their digest values, and the P2/P3 gate
   logs did not survive in `/tmp`. **Resolution path:** the Stage-A/B executor's transcripts, or
   a re-run by the tester lane. **Not a blocker** — the mutation legs are separately evidenced by
   the preserved Stage-A logs (§2.3).
4. **The P6-G1 reconciliation pack.** The pack id `central_metrics_ledger_reconciliation` is
   **declared** by the plan; `.agents/tester/PACKS.md` is the **tester lane's** file and the
   brief forbids this lane from touching it, so the pack's **registration** (and hence its
   execution) is the tester lane's to make — **the standing pattern for every lane pack id**.
   This phase produced the **docs** the pack asserts against; it did not register or run the
   pack. **Not a blocker.**
