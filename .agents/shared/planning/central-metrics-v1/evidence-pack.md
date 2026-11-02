# Evidence pack — central-metrics-v1

> **Assembled:** 2026-11-02 (UTC) by the implementation lane, Phase 6 task **6.6**.
> **Lane:** `feature/central-metrics-v1` (worktree `/home/nea/ensemble-src-wt-central-metrics-v1`).
> **Plan set:** `.agents/shared/planning/central-metrics-v1/` — `plan-overview.md` (the frozen
> contract + the flags), `decisions.md` (the rulings), `fold-ledger.md` (why every pin exists),
> `phase{1..6}-plan.md` (the per-phase task rows and the declared mutation tables).
> **Contract:** [`docs/architecture/metrics-query-contract.md`](../../../../docs/architecture/metrics-query-contract.md)
> carries the per-phase evidence blocks this pack cites.
> **Machine-readable evidence preserved in this directory (142 files, ~1.5 MB):**
> `evidence/am16-exposition-{base,head}.txt` (the AM-16 structural diff inputs),
> `evidence/p4-failed-digests.txt` (the recomputed P4 FAILED digests),
> `evidence/p1-mutations-final.log` + `evidence/p2-mutations-final.log` (the surviving Stage-A
> mutation logs) and `evidence/p1-p2-mutation-logs.sha256.txt` (their sha256 table + leg census);
> the **P3** set (`p3-gate-final/` in full · `p3-scratch/` · `p3-sweep-crash/` ·
> `p3-sweep-sharded/{20261102T060325Z,20261102T061340Z}/`), the **P4** set (`p4-gate/` ·
> `p4-mutations/`) and the **P5** set (`p5/`); the per-set **sha256 manifests**
> (`p3-preservation-manifest.txt` · `p4-preservation-manifest.txt` ·
> `p5-preservation-manifest.txt` · `set-digests.txt`) and the **verdict-bearing excerpts**
> (`p3-verdict-lines.txt` · `p5-verdict-lines.txt`). **Every preserved file was
> byte-identity-checked** against its source (`cmp`). The multi-MB bulk (`.collect.txt` /
> `.nodes.txt` / `.shard-*.out`) is deliberately **not** copied — the per-shard FAILED/ERROR
> excerpts are extracted instead. **The full preservation table — path + sha256 + what it proves
> — is §13 item 3.**

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
| P2-G1 | D1 137 + D2 281 green | D2 281 green | **bare-id `d78b09cf2549c371` / grep-full `d9bff11a731d8ba3` — IDENTICAL both sides** (recomputed from the preserved Stage-A gate artefacts, `evidence/p2-gate-digests.txt`; the D2 leg: `1 failed, 280 passed` on both sides) | net-new **EMPTY** |
| P3-G1 | D1 110 + D2 302 green | D2 302 green | digest-equal | net-new **EMPTY** |
| P3-G1 blast-radius supplementary | 226 nodes, both legs green | 226 nodes, both legs green | digest-equal | net-new **EMPTY** |
| P3-G1 whole-tree (sharded) | HEAD **32 725** nodes | BASE **32 450** nodes | the sweep's own verdict block was **empty by construction** (a `;` vs `\|` bug — the `comm` output went to stdout, never into the log) and the two EARLIER runs' "DIGEST-EQUAL" verdicts ran over the **empty-string digest** (`nodes=0`) ⇒ vacuous. **RE-DERIVED + ADJUDICATED (2026-11-02, the P3 evidence-repair round)** with the **FIXED comparator** (`evidence/p3-scratch/sweep_digest_verdict.sh`): the two legs' FAILED-id sets DO differ — GREP-FULL `comm -23` = **37** base-only / `comm -13` = **33** head-only; BARE-ID **36** / **33** (the 37th base-only entry is a daemon LOG line, not a test id). **The difference is a MEASUREMENT ARTEFACT, not a regression**, proven by four legs: (a) the **per-id** adjudication (69 diff ids × 3 runs × 2 sides, each id in ISOLATION) ⇒ **0 lane candidates** — every head-only id is green in isolation on BOTH sides; (b) the **file** level (15 files × 3 × 2) ⇒ **13/15 files carry a BYTE-IDENTICAL red set on both sides**, including all five files that own the 33 head-only ids (the two that differ are BASE-side flaky concurrency tests); (c) the **batch** level (the whole 69-id set as ONE session, 3 × 2) ⇒ symmetric difference **1** — the known base-side flaky concurrency test; (d) the **shard-determinism** leg ⇒ **the SAME shard on the SAME tree yields DIFFERENT red sets across two runs** (shard-002: 82 vs 80 reds, 26 ids differ between two runs of the same tree) ⇒ the shard-level digest is not a reproducible instrument | **net-new EMPTY — RE-AFFIRMED** (the difference is a parallel-shard COMPOSITION artefact) · evidence `evidence/p3-adjudication/` + `evidence/p3-adjudication.sha256.txt` (set digest `50d74e64a325e56bed2640d1d85b697fe9d2e80f100f07960ded7c23bc9dc78f` — the CANONICAL recipe, re-anchored 2026-11-02 (W-7); the previous aggregate `76a69edd9bd3ec454541b4d81ecce0cf5e88151d6a36602d4f3373df8f85c218` is **SUPERSEDED and NOT re-derivable** — 28 candidate recipes excluded; the per-file content is intact 21/21) |
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
| 3 | The **lane's pinned planning projection** | **+274 → 363** (F7c-inclusive; 361 on the ledger-rows basis) | the plan set's `decisions.md` D5 + `plan-overview.md` §Re-derived budget; the shipped constant `adjudication.CATALOG_LIVE_ESTIMATE_SERIES_PROJECTION = 363` — the A-6 rename of the old `BUDGET_SERIES` (the value is unchanged; the old name read like an enforced budget). It is a **CATALOG LIVE-ESTIMATE PROJECTION**, not a machine-enforced bound, and it must **never** be compared with the **measured closed label space** (a different convention — a live estimate vs a ceiling) | the D5 per-family delta table (10 full-IN families = 217, 6 partial-IN families = 57) |
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

## 12. The implementation-phase deviations + the open-for-council row (recorded, never silent)

| # | The deviation | Where it is recorded |
|---|---|---|
| 1 | The **series budget** landed under the pinned projection (the three shortfalls) | §3.2 |
| 2 | The **P4 bridge does not import `daemon/api.py`** (a mechanical Tier-2 file the plan forbids touching) — it looks the already-imported module up in `sys.modules` (`daemon/services/metrics_query/wiring/sources.py:128`). The shipped code gives a second reason: importing it from there would create a **circular import** (`daemon.api` imports the wiring package). Consequence: in a process where `daemon.api` was never imported, the families degrade to "no series"; the running daemon always has it imported (the uvicorn entry is `daemon.api:app`). The alternative was one line in `api.py` forcing a whole-tree Tier-2 leg | the lane's P4 evidence; `sources.py:20-25` + `:118-129` |
| 3 | The plan's **DECLARED dual-side compositions were short** vs the blast-radius derivation in **P2, P3 AND P4** — the derived sets ran as **disclosed supplementary legs** (digest-equal) and the widened registration input is recorded for the tester lane | §1.2 + the contract doc's per-phase FINDINGs |
| 4 | The whole-tree single-shot `-n auto` form **aborts rc=3 on BOTH legs** (an xdist worker exits mid-node; reproduced at BASE). The first attempt's "digest equal" was **VACUOUS** — both legs aborted before any FAILED ids existed, so the digest was the **empty-string digest** `e3b0c442…b855` (`= sha256("")`, `nodes=0`) and the "DIGEST-EQUAL … net-new = EMPTY" verdict that followed from it was vacuous. It was recorded as a **FAILED leg** and re-run in the sharded form (BASE 32 450 / HEAD 32 725 nodes). **CORRECTION (2026-11-02, from the preserved evidence):** the **first sharded attempt was vacuous too** (every shard `rc=126` ⇒ the empty-string digest again), and in the **real sharded run** the script's net-new verdict block is **empty by construction** (a `;` vs `\|` bug — the `comm` output went to stdout, not into the log) ⇒ **no net-new verdict was recorded**. Recomputing the comparison from the preserved node-id lists gives `comm -23` = **37** base-only and `comm -13` = **33** head-only entries (BASE 488 / HEAD 484 unique ids) ⇒ **the two legs' FAILED-id sets are NOT equal**, and the "net-new 0" figure is **not evidenced as stated**. **CLOSED (2026-11-02, the P3 evidence-repair round): the net-new = 0 claim is RE-AFFIRMED with in-repo evidence.** The comparator's verdict block was FIXED (`evidence/p3-scratch/sweep_digest_verdict.sh` — the diff lists go to FILES and are then LOGGED; `LC_ALL=C` on BOTH sort and comm; the buggy original is kept byte-identical beside the patch `evidence/p3-scratch/sweep_sharded.verdict-block.patch`). The FIXED comparator re-derives the comparison: the two legs' FAILED-id sets DO differ (GREP-FULL 37 base-only / 33 head-only; BARE-ID 36 / 33 — the 37th is a daemon LOG line, not a test id). **The '35 vs 37' delta was a MISCOUNT in the lane's earlier report: 37 is correct.** Every diff id was then ADJUDICATED (69 ids × 3 runs × 2 sides, each id in ISOLATION): **0 lane candidates** — every head-only id is green in isolation on BOTH sides; the file level (15 files × 3 × 2) shows 13/15 files with a byte-identical red set on both sides, including all five files that own the head-only ids; the batch level (the whole 69-id set as one session) shows a symmetric difference of 1 (a base-side flaky concurrency test); and the **shard-determinism** leg proves the mechanism outright — **the SAME shard on the SAME tree yields DIFFERENT red sets across two runs** (shard-002: 82 vs 80 reds; 26 ids differ between two runs of the same tree), so the shard-level digest is not a reproducible instrument and the sweep-level delta is a parallel-shard COMPOSITION artefact. **No product code was changed in this round** | the evidence pack §1.2 (the P3-G1 whole-tree row) + §13 item 3 + `evidence/p3-adjudication/` (the matrices + `grouping-proof-summary.txt`) + `evidence/p3-adjudication.sha256.txt` + `evidence/p3-scratch/sweep_digest_verdict.sh` |
| 5 | The **F7 HELP carrier renders under both spellings** — the exposition mask covers both | §7 |
| 6 | `db_offload` is a **process-level singleton** bound to the first loop that uses it (a test-harness caveat; production unaffected) | the lane's P3 evidence |
| 7 | The pre-existing base red `test_rdrs_lanes_come_from_the_config_kill_switchs` (an 8-vs-7 RDRS lane drift, identical on both legs) is **NOT lane-attributable** — fixing it would edit `SWEEP_LANE_VOCABULARY`, an exposition footer **outside** the AM-16 allowlist | §1.2 + `evidence/p4-failed-digests.txt` |
| 8 | The exposition **base golden** lives under `tests/unit/metrics/baselines/` (the blanket `data/` gitignore would have swallowed it) | the lane's P4 evidence |
| 9 | The **P3-G2 D1 pin has no id** in the plan's table — its file + registration input are recorded; the contract's nine-id block was **NOT** widened | §1.3 |
| 10 | The FE's **9 pre-existing reds** (the date-dependent timezone-DST family + the jobs-grouping / jobs-filter-state) are byte-identical on both legs; `tsconfig.spec.json` is **RED at base** (a stray `});` at `src/app/app.component.spec.ts:2127`) — **not a lane file**, untouched, reported | §1.2 |
| 11 | The FE **chart mode** comes from the catalog's per-name `chart_role` with the door's **kind-derived fallback** (counter ⇒ step, else line) | the published FE page §3 |
| 12 | Stage A's disclosed items: the family-level `role='chart'\|'stat'` + the per-name `chart_role='line'\|'step'` (contract §4.3); **three added public accessors**; `?names=` over the 50-name ceiling ⇒ **400 `invalid_label_filter`** (never a silent truncation); the histogram series carry the **stored cumulative observation count** as the point value; **one disclosed test edit** re-pointing two stale saver-op assertions | the contract doc §4.3 + the lane's P2 evidence |
| 13 | **COUNCIL-RULED AND APPLIED (D-c) — the contract's 403 token now matches the shipped body.** The contract's error-vocabulary block rendered the scope miss lowercase as `scope_insufficient`; the **shipped** body is uppercase **`SCOPE_INSUFFICIENT`** (`daemon/auth/decorators.py:75-78` — `_SCOPE_403_DETAIL = {"error": "SCOPE_INSUFFICIENT", "message": "Token lacks required scope"}`). The status (403) and the meaning are identical — a **token-rendering** difference, not a behavioural one. **The council ruled (D-c): amend the CONTRACT TOKEN, never the wire and never the decorator.** **APPLIED** — `docs/architecture/metrics-query-contract.md`'s error-vocabulary block now reads `403 SCOPE_INSUFFICIENT`, with the amendment note beside it (the sibling blocks already used the shipped spelling). **CONSEQUENCE, measured and routed:** the shipped D1 pack `central_metrics_contract_doc` pins that block against a literal tuple in the TEST file (`tests/unit/metrics/test_central_metrics_contract_doc.py:59` → `"403 scope_insufficient"`), so the pack goes **RED on exactly that one assertion** until the pin follows the ruling — the measured run (1 failed, 20 passed; the diff is index 5 only) is preserved in `evidence/d-c-contract-token-pin.txt`. The test file is **outside this lane's fence** (zero code, no tests/), so the pin update is the **owning test lane's**, authorised by the same ruling | the published query-API page §3.1 (the resolved rendering note) · `daemon/auth/decorators.py:75-78` · `evidence/d-c-contract-token-pin.txt` |


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
3. **The per-leg FAILED digests — CLOSED (2026-11-02).** Every leg's digest is now **preserved
   in-repo** and **digest-backed**, across P2, P3, P4, P5 **AND the fix-cycle FE legs** (the
   cycle-1 and cycle-2 dual-side legs are folded in §14.1–§14.2 with their digests; the
   cycle-2 artefacts are preserved in `evidence/c2-fe-gate/`).
   **The P3 whole-tree leg's net-new verdict is now CLOSED TOO (2026-11-02, the P3
   evidence-repair round): the comparator's verdict block was FIXED and the 69 diff ids
   were ADJUDICATED (each id in isolation, 3 runs per side, both legs) ⇒ 0 lane
   candidates; the difference between the two legs' FAILED-id sets is a parallel-shard
   COMPOSITION artefact, proven by the shard-determinism leg (the same shard on the same
   tree yields different red sets across two runs). See §1.2 (the P3-G1 whole-tree row)
   and §12 row 4. The artefacts that were "not
   recoverable" are recovered (they had never gone — an earlier scan of `/tmp` was truncated by
   `head -20` and cut the listing exactly above them). **The preservation table:**

| Preserved set | Files | Set digest (sha256 over the sorted per-file manifest) | What it proves |
|---|---|---|---|
| `evidence/p1-mutations-final.log` | 1 | `f59dd12b5b22bbef3777761047688c046ae0e3f518608943da1fea7e514e835e` | the **P1** mutation matrix — **3** legs, each `RED-OK`, ending `RESTORED → 21 passed → GREEN-OK` |
| `evidence/p2-mutations-final.log` | 1 | `be0fe2e24da1ce20e254dc5c14fa1e584b6bb21e468e5e3435af370b44fd29c1` | the **P2** mutation matrix — **16** legs (`P2-M1…M15` **+ `P2-M13b`**), each `RED-OK`, ending `RESTORED → 156 passed → GREEN-OK` |
| `evidence/p1-p2-mutation-logs.sha256.txt` | 1 | (the sha256 table + leg census for the two logs above) | the P1/P2 leg census + the digests + the `/tmp` source paths/sizes/mtimes |
| `evidence/p2-gate/` + `p2-gate-digests.txt` | 12 + 1 | `7e433292dc7a8d7c172785271dab8451fdb01814b90923d1c1461aa202377e2b` | the **P2-G1** dual-side gate: `HEAD.d2.out` / `BASE.d2.out` (`1 failed, 280 passed` each) ⇒ bare-id `d78b09cf2549c371` / grep-full `d9bff11a731d8ba3`, **IDENTICAL both sides**; the D1 leg (`21 passed`); the 9-file D2 composition; the env **fence** record |
| `evidence/p3-gate-final/` | 64 | `252bdf0efba0856086b70c3b31db69b4c5afcb6ce286df4af24918fd63aac72b` | the **P3-G1** gate dir **in full**: the D1/D2/cage legs' rc + summary (HEAD `110 passed` / `1 failed, 301 passed` / `7 passed` / `12 passed`; BASE `1 failed, 301 passed` / `12 passed`), the **exposition bodies** (the AM-16 structural diff), the adjudicate/fixed/netnew sets, the collect outputs (the slicing proof) |
| `evidence/p3-scratch/` | 8 | `b16b41e3c647af92324d404f7c51a3bcf38d0e17d707bb24581d2db11685ef70` | `mutation-ledger.json` (the P3 mutation ledger) + **`sweep_sharded.sh`** — the whole-tree **comparator** whose verdict semantics the council must read (§12 row 4) |
| `evidence/p3-sweep-crash/` | 5 | `166f0cd143ea345c1ac673ce4bdbda87786cc8813245550beac4e4a0b126bc46` | the **single-shot `-n auto` attempt**: `rc=3` on BOTH legs, the `INTERNALERROR>` lines, and the **VACUOUS** digest `e3b0c442…b855` (`= sha256("")`, **nodes=0**) with the "DIGEST-EQUAL … net-new = EMPTY" verdict that followed from it — the disclosure-(4) evidence |
| `evidence/p3-adjudication/` + `evidence/p3-adjudication.sha256.txt` | 21 + 1 | `50d74e64a325e56bed2640d1d85b697fe9d2e80f100f07960ded7c23bc9dc78f` | **the P3 whole-tree net-new adjudication** (2026-11-02): the FIXED comparator's verdict log + the diff id lists (GREP-FULL + BARE-ID) + the **per-id matrix** (69 ids × 3 runs × 2 sides) + the re-derived verdict + the **file-level matrix** (15 files × 3 × 2) + the **batch matrix** (the whole diff set as one session) + the **shard-determinism matrix** (the same shard twice on the same tree) + the rebuilt BASE node list and the two churn shards + `grouping-proof-summary.txt` — the **net-new = 0** re-affirmation (§1.2 + §12 row 4) |
| `evidence/p3-scratch/sweep_digest_verdict.sh` + `…/sweep_sharded.verdict-block.patch` | 2 | (inside the set digest above) | the **FIXED** net-new verdict comparator (the diff lists go to FILES and are then LOGGED; `LC_ALL=C` on BOTH sort and comm; an explicit verdict line; a non-zero exit when the net-new set is not empty) + the patch that repairs the original's `;` vs `\|` verdict block — the buggy original `sweep_sharded.sh` is kept **byte-identical** as the evidence of the defect |
| `evidence/p3-sweep-sharded/20261102T060325Z/` | 11 | `e93b34dac293abf69e4f7d6e9c8c024ca4b5bb932711fef5b3d0204eed7472a0` | the **first sharded attempt** — every shard `rc=126` ⇒ the digests are the **empty-string digest again** ⇒ its "DIGEST-EQUAL" is **vacuous too** |
| `evidence/p3-sweep-sharded/20261102T061340Z/` | 11 | `3210d90ed5bcb5ced9a4c37720d352f346a71ddd876f5ccd3178cf9aa66d55a3` | the **real whole-tree sharded run** — BASE 32 450 / HEAD 32 725 nodes, 275 head-only nodes, the per-shard `progress.tsv`, the FAILED-id lists and the node-id lists (**the digests**, the per-id lists) |
| `evidence/p4-gate/` | 24 | `a95b79e74fa0159bd0c77b5a90ea5f3d9bffd5d11b0500f986ad2b18897edb9e` | the **raw digest inputs** for all six P4 D2 legs and all six widened-leg legs (the `.bare` node-id lists + the `.full` grep forms) — the inputs behind `evidence/p4-failed-digests.txt` |
| `evidence/p4-mutations/results.json` | 1 | `d7acd358d4aa1c30c2788d83d62518e23120a886207d0d1309aa11acfa6c3e43` | the **P4** mutation matrix (the 13 legs), machine-readable |
| `evidence/p5/` | 5 | `8819c59f01fdfce35f74d710ff5f5b9327eaf1777d20d0f5e5e8f2bb9a5c6f30` | the **P5** FE gate: `summary.json` (**11** mutations, **0** failures, all PASS, all `restored_byte_identical`), the two build legs (the **10** budget WARNINGs — all pre-existing, **none** in a lane file), the e2e (**2 passed, 31.3 s**), and the full FE corpus (**9 failed / 4 279 passed / 4 288**) |
| `evidence/p3-preservation-manifest.txt` · `p4-preservation-manifest.txt` · `p5-preservation-manifest.txt` · `set-digests.txt` | 4 | (the per-set manifests + the set-level digests) | the **sha256 manifest of each preserved set** — the p1-p2 pattern. A reviewer re-derives a set digest with `cd <set-dir> && sha256sum * \| sort \| sha256sum` |
| `evidence/p3-verdict-lines.txt` · `p5-verdict-lines.txt` | 2 | (the verdict-bearing excerpts) | the **verdict-bearing lines** of each set, extracted so the council reads the verdicts without re-running anything |

**The digest column's convention (ND-3, re-anchored):** every row that names a set in `evidence/set-digests.txt` carries THAT SET'S **CANONICAL aggregate** — the one recipe, over the set's members named explicitly, `sha256sum <members> | LC_ALL=C sort | sha256sum`. All ten were RE-DERIVED at the re-anchor (10/10 match, the file counts included) — the eight rows that still carried the OLD-recipe digests are now re-anchored, and the old identifiers stay recorded beside the new ones in `set-digests.txt` (nothing is lost). The two `p1-mutations-final.log` / `p2-mutations-final.log` rows are NOT set-digests entries: they carry their FILE digest, the `p1-p2-mutation-logs.sha256.txt` form.

**Every preserved file was byte-identity-checked against its source** (`cmp`, 119 checked /
119 identical / 0 mismatched for the newly copied sets). **The multi-MB bulk was deliberately
NOT copied**: the `.collect.txt` / `.nodes.txt` / `.shard-*.out` files are excluded, and the
per-shard FAILED/ERROR excerpts are extracted instead
(`*.per-shard-failed-ids.excerpt.txt`). The whole preserved tree is **142 files / ~1.5 MB**.

**One material finding fell out of the preservation — it is recorded, not smoothed.** The
whole-tree sharded leg's **net-new claim is not evidenced as stated** (§12 row 4 carries the
detail). The two legs' FAILED-id **sets are NOT equal**: `comm -23` = **37** base-only (fixed at
HEAD) and `comm -13` = **33** head-only entries, against BASE **488** / HEAD **484** unique ids.
The sweep script's own verdict block is **empty by construction** (a `;` vs `\|` bug: the `comm`
output went to stdout, not into the log), so no net-new verdict was ever recorded for the real
run — while the two earlier runs recorded "DIGEST-EQUAL … net-new = EMPTY" over the
**empty-string digest** (`nodes=0`), i.e. **vacuously**. The 33 head-only entries are
whole-file groupings (e.g. `tests/test_injection_api.py::…`, `tests/unit/routers/test_work_router.py::…`)
that appear on one side and not the other — the signature of an order/parallelism-dependent
flake rather than a lane regression — but **this pack does not adjudicate that**; the per-id
lists are preserved beside the record and the question belongs to the council.

4. **The P6-G1 reconciliation pack.** The pack id `central_metrics_ledger_reconciliation` is
   **declared** by the plan; `.agents/tester/PACKS.md` is the **tester lane's** file and the
   brief forbids this lane from touching it, so the pack's **registration** (and hence its
   execution) is the tester lane's to make — **the standing pattern for every lane pack id**.
   This phase produced the **docs** the pack asserts against; it did not register or run the
   pack. **Not a blocker.**

---

## 14. The fix-cycle record (cycles 1–3 — the DELTA re-review's single record)

This section folds the three fix cycles into **one** record so the delta re-review reads a
single coherent story instead of three separate artefacts.

### 14.1 Cycle 1 — the backend fix (C-1 + W-1…W-5 + A-2/A-3/A-4/A-8/A-9)

| Item | What it was | Commits |
|---|---|---|
| **C-1** | the runtime source bundle was a permanent snapshot taken before the handles exist ⇒ the live handles never reached the provider | `690130c27` (the fix) · `61e5c6b30` (the locator-invariant pin + the W-1…W-5 bounds, mutation-verified) |
| **W-1…W-5** | the bounds pinned in `61e5c6b30` | `61e5c6b30` |
| **A-2/A-3/A-4/A-8/A-9** | the architect's advisories folded in the same cycle | `690130c27` · `61e5c6b30` |
| the **runtime observation** | the fenced-dev boot that PROVED the dark→live transition — the probe's presence map flipped `dsn`/`manager`/`mcps_gateway_service`/`wedge_watchdog` from ABSENT to PRESENT between the sampler's first and second tick | `36c46aa1a` (the observation doc) — **`evidence/c1-runtime-observation.md`** |
| the **Tier-1 gate** | the blast-radius leg: BASE `bfc3a233a` vs HEAD `36c46aa1a`, the same command and the same file list, slicing proof 533 → 559 nodes, the FAILED digest `d78b09cf2549c371…` **identical on both sides** ⇒ net-new EMPTY, the single red on both legs being the lane's own base-proven `test_rdrs_lanes_come_from_the_config_kill_switchs` | `1dde26faf` — **`evidence/fix-cycle-1-gate/`** (+ its `VERDICT.md`) |

### 14.2 Cycle 2 — the FE fix (W-6 + A-5 + A-7 + the contract alignment)

`cd06d2a0b` — the FE surface. The preserved gate evidence is
**`evidence/c2-fe-gate/`** + **`evidence/c2-fe-gate-preservation-manifest.txt`** (8 files, all
byte-identical to `/tmp/c2-fe-gate/`; all five dispatch-cited digests recomputed and matching):

| Leg | Result | Digest |
|---|---|---|
| dual-side **head** `cd06d2a0b` | 2 failed · 1 799 passed · **1 801** total | `29a74f92a37c668a` |
| dual-side **base** `1dde26faf` | 2 failed · 1 799 passed · **1 801** total | `e8fd5e396e0b022a` |
| dual-side **base** `36c46aa1a` | 3 failed · 1 798 passed · **1 801** total | `8d1a03e0633aa67f` |
| the **e2e** (Playwright, own proxy config, no daemon) | **2 passed (31.2 s)** | `492dd44bda9f2625` |
| the **mutation matrix** | **13/13 PASS** (P5-M1…P5-M11 + `FIX2-W6` + `FIX2-A5`), every leg `red_exit≠0` / `green_exit=0` / `restored=True`, plus the **POST-RUN GUARD: OK** | `cf582ac6096f447d` |
| the **scoped type-check** ×2 | the logs are **0 bytes** — `tsc --noEmit` is silent on success — so their digest is the **empty-string** digest `e3b0c442…b855`. **The PASS is carried by the exit code, which these logs do not contain** — an empty log is not the evidence of a pass | `e3b0c44298fc1c14` (both) — **RE-CAPTURED in the closure round: both logs now carry their own `TYPECHECK_EXIT=0`** (862 B `cc642451…` / 851 B `5e73233a…`), so the verdict is readable in the artefact itself |

### 14.3 The A-6 rename

`cf12edcfb` — `BUDGET_SERIES` → **`CATALOG_LIVE_ESTIMATE_SERIES_PROJECTION`** (the value **363**
is unchanged; the constant has **zero consumers** and is pinned as such). The old name read like
an enforced budget. The three docs that quoted the old name are updated in this phase:
`plan-overview.md` (§Re-derived budget, two sites), this pack (§3, row 3) and
`docs/architecture/metrics-promote-disclosure.md` (§1.1 row 5). **The two-conventions note rides
every site:** the 363 is a **catalog live-estimate projection** and the 425 is a **measured closed
label space** — different conventions, **never compared**.

### 14.4 The FE base-debt rows (the cycle-2 dual-side legs)

| Row | What it is | Where it appears |
|---|---|---|
| the **timezone-DST pair** | `settings.component.spec.ts` — "Timezone preference (user-timezone-setting) › renders the timezone section with the native picker when `Intl.supportedValuesOf` is available" and "… uses the API list when the `GET /api/settings/timezones` response is non-empty" | **on head `cd06d2a0b` AND on base `1dde26faf` AND on base `36c46aa1a`** — the same two ids on every side ⇒ **not** lane-attributable |
| the **`36c46aa1a`-only JobQueueIndicator flake** | `JobQueueIndicatorComponent Logic › recentJobs (public computed) › should filter out pending, processing, and paused jobs (terminal only via isTerminalStatus)` | **base-side only, on `36c46aa1a` alone** — **absent at `1dde26faf` and absent at HEAD**. It is the reason base-36c carries **3** reds where the other two sides carry **2** ⇒ net-new stays EMPTY |
| the **`tsconfig.spec.json` stray `});`** | a **pre-existing syntax error** at `src/app/app.component.spec.ts:2127` — **RED at base**, not a lane file, untouched, reported | see §12 row 10 (the FE base-debt row) |

**The net-new verdict on the FE dual-side legs is EMPTY** — head (2 reds) matches base-1dde
(2 reds) id-for-id, and base-36c's extra red is a **base-side** flake that the head does not have.

### 14.5 Delta-3 — the final fix cycle (ND-1…ND-5 + the ceremony text)

`4023515ee` (ND-3) · `7018162fd` (ND-2) · `575faba9a` (ND-4) · `dd3d7d95d` (ND-5 + the
observation file's ND-1 correction) · `cafe77951` (ND-1's disclosure correction + the ceremony
text). The scoped dual-side gate is preserved in **`evidence/delta-3-gate/`** (+ its
`VERDICT.md`):

| Leg | Result | Digest (BARE-ID / GREP-FULL) |
|---|---|---|
| BASE `0ae9720f0` | 560 nodes · `1 failed, 559 passed` · exit 1 | `d78b09cf2549c371` / `d9bff11a731d8ba3` |
| HEAD `cafe77951` | 570 nodes · `1 failed, 569 passed` · exit 1 | `d78b09cf2549c371` / `d9bff11a731d8ba3` — **IDENTICAL both sides** |

**NET-NEW = EMPTY** (the one red on both legs is the lane's own base-proven debt). The pack legs:
the pagination pack **38 passed**, the contract-doc pack **21 passed**, the lane's full suite
**383 passed**. The mutation matrix is in the gate's `VERDICT.md` (M-ND2 → 4 legs RED, M-ND4 →
2, M-ND5 → 2; every other leg in each pack stayed GREEN).

**ND-3 (the evidence index):** the superseded aggregate is restored to `76a69edd…` beside the
canonical `50d74e64…`; the §13 set rows are **re-anchored** to the canonical aggregates (all ten
re-derived, 10/10); and the P3 adjudication manifest is now the **two-column house form**, so
`sha256sum -c p3-adjudication.sha256.txt` reports **21/21 OK** (the set aggregate is unchanged).

---

## 15. The routed items (for the leader — owners named)

These are the **findings this phase recorded but did not fix**. None is in this lane's file set;
each names its owner. **Nothing here is a blocker for the lane's own gates** — they are the
council's/leader's to route.

| # | The finding | The anchor | Why it is NOT fixed here | The owner |
|---|---|---|---|---|
| **R-i** | **F6's availability reason is INVERTED.** The census's `gated` property is documented as *"whether the census sampler's DB work is currently gated OFF"* but returns `_gate()`, which is **True when the census's DB work is ALLOWED** (`collect()` runs `if self._gate():`). **The observed consequence:** with the switch ON the census published **15 live series** while the door reported F6 as `dark / census_gated` (and `activation.census_enabled` false) — **AM-18 is broken in production**. The lane's own switch-invariance pack pins the *documented* polarity against a **fake** census, so the production census and the lane's contract disagree. | `daemon/services/metrics_census.py:323-335` (`gated`) · `:347-355` (`_gate()`) · consumed at `daemon/services/metrics_query/provider.py:103-109` | the census file is the **metrics-collection P0 lane's** surface (the lane's F6 seam), not this lane's; inverting the provider's read instead would break the lane's own frozen switch-invariance contract | **the metrics-collection lane** — a one-line polarity fix in the census's `gated` property (or at its call site) |
| **R-ii** | **F1/F3's `capture_active` is never wired.** The provider is constructed with `census` / `ring` / `listener_enabled` / `sampler_interval_s` **only**; `capture_active` keeps its `False` default. **The observed consequence:** F1 published **9 live series** while the door reported it `dark / capture_off`. | `daemon/api.py:2528-2533` (the construction site) · `daemon/services/metrics_query/provider.py:70/:78/:134` (`capture_active`) | `daemon/api.py` is the lane's **Tier-2 mechanical file**, which the council's C-1 ⑥ forbids the lane from touching at fix-cycle time | **the mechanical-file lane** — pass the capture-active fact in at the api.py construction site (or derive it from the watchdog's state) |
| **R-iii** | **F7's `live_series_count` is always 0 — a non-internal histogram never publishes.** The non-internal histogram `observe()` path sets `_dirty` but **never calls `_maybe_publish_locked()`** (only the `internal` branch does), so `published_snapshot()` stays `None` and the door's family count is 0 for histogram families. **The observed consequence:** F7 reported `live_series_count = 0` while the exposition carried the observation (`_count{op="aget"} 1`) — the metric IS live; the door cannot see it. | `daemon/checkpoint_metrics.py:830-874` | the registry's publish path is the **canonical registry's** surface (F0.2), not this lane's; making F7 `internal=True` would change the **exposition shape** and the **frozen series census** — a contract decision, not a fix-cycle edit | **the canonical-registry lane** — either the registry publishes non-internal histograms on the writer path, or the lane's family count reads the histogram's live state |
| **R-iv** | **The `stale_pause` sweep lane is DOCUMENTED-DARK for TWO independent reasons**, and **publishing it alone would change nothing.** (i) `StalePauseSweepService` exposes **no `counters()` reader** ⇒ it would render no series even if published; (ii) it is **never assigned to `app.state`** — `daemon/api.py:3058-3074` calls `sweep_stale_pauses(...)` inline on the lifespan loop. Covering the lane needs **BOTH**. `job_lock_sweep` and `upgrade_journal_sweep` **ARE** published and still render no series (no reader). The measured consequence: the observed F12 lanes were `['eligible_pending', 'orphan_watcher', 'watch_reconcile']` — the three with readers. **No family verdict changes** (F12 stays IN). | `daemon/api.py:3058-3074` · `daemon/services/pool_orchestrator.py:341-345` (the instance lives on the manager) · `daemon/services/{job_lock,upgrade_journal,stale_pause}_sweep.py` (no `counters` reader) | the gap is **pre-existing** (the lane did not create it — it only made the locator look for it) and both halves live outside this lane | **the sweeps' own lane** (the `counters()` readers) **+ the mechanical-file lane** (the `app.state` publication) |
| **R-v** | **The house canon needs the FIXED comparator.** Adopt into the sweep tool: **files-first → log** (the diff lists go to files first and are then logged — the original's `;` after the log call sent `comm`'s output to stdout, never to the log), **`LC_ALL=C` on BOTH `sort` and `comm`** (a collation mismatch silently drops entries from the diff), and **FORM-tagged verdict lines** (GREP-FULL and BARE-ID are never compared across forms). **And: a sweep whose verdict block is EMPTY must FAIL LOUD** — the two vacuous `sha256("") = e3b0c442…b855` "passes" are exactly that evidence class. **`LC_ALL=C` into the house gate-script conventions.** | the FIXED comparator is preserved at `evidence/p3-scratch/sweep_digest_verdict.sh`; the buggy original is preserved byte-identical at `evidence/p3-scratch/sweep_sharded.sh` with the repair at `evidence/p3-scratch/sweep_sharded.verdict-block.patch`; the vacuous class is recorded in §12 row 4 + `evidence/p3-verdict-lines.txt` | the sweep tool and the house gate-script conventions are **not this lane's files** | **the tester lane / the house canon** |
| **R-vi** | **The D-c contract token needs its pin updated.** The council ruled (D-c): the contract's error-vocabulary block must render the 403 token as the **shipped** uppercase `SCOPE_INSUFFICIENT`. **APPLIED** — the contract now carries it. **CONSEQUENCE:** the shipped D1 pack `central_metrics_contract_doc` pins that block against a literal tuple in the **test** file that still carries the superseded lowercase token, so the pack goes **RED on exactly that one assertion** (measured: 1 failed, 20 passed; the diff is index 5 only). | `tests/unit/metrics/test_central_metrics_contract_doc.py:59` (`"403 scope_insufficient"`) · the assertion at `:169` · the measured run in `evidence/d-c-contract-token-pin.txt` | the test file is **outside this lane's fence** (zero code, no tests/) | **the owning test lane** — the pin follows the ruling it is pinning (the wire and the decorator are NOT to be changed) |

---
