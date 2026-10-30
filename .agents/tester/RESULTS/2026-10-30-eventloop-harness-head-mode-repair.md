# Harness HEAD-mode repair — eventloop-sync-DB hot paths (Phase-0 instrument)

**Commission:** repair the Phase-0 measurement harness so it can assert the
FIXED state at HEAD while still asserting the BROKEN state at BASE.
**Branch / worktree:** `fix/eventloop-sync-db-hotpaths` @
`/home/nea/ensemble-src-wt-eventloop-sync-db-hotpaths`.
**Harness:** `.agents/tester/harness/eventloop-phase0/` (tester-owned per
D-12; the developer never edits it).
**Tree under test today:** `42764588c` — **ABOVE** the lane base
`b1183a637` by the two harness commits only, i.e. **no production fix has
landed** (`git diff b1183a637..42764588c -- . ':!.agents'` is empty). The
"fixed tree" half of the truth table is therefore a **SIMULATION**; that
is labelled everywhere and argued in §7.

**ROUND 1** (`c3d87af26`) was council-reviewed: **REJECT** with 3 blocking
findings (`.agents/reviewer/RESULTS/2026-10-30-wi-3f5bea95-round2-delta-repair-review.md`
PART B). All three are repaired in ROUND 2 below, plus the G5 increment.
**ROUND 2 verdict: REPAIRED — the instrument separates BROKEN from FIXED in
both directions, through the REAL gate path, with the evidence committed
(§8).** No production code touched; no other lane's files touched.

---

## 0. Round 2 — the three blocking findings and the G5 increment

| # | Council finding | Repair | Where |
|---|---|---|---|
| **B1** | G9 HEAD-mode **unit bug**: the gate compared **sample-summed** `statements_by_shape` against **per-request** rules — missions `{20,10}` over 10 samples vs the pinned `(2,1)` ⇒ a shape the lane never touches failed **deterministically forever**; hidden because cell 3 routed through `simulate_report`, which normalized units — a path the real gate never takes | **one normalization site at the gate boundary** (`modes.per_request_by_shape`, dividing by the sample count) — the gate reads ONLY that; the simulation now writes the **summed** form back so real and simulated runs share ONE path; **unit classes declared** for every quantity (`modes.declared_unit_classes`, emitted into every report as `g9_unit_class`, §README table); a **mutation-meaningful selftest leg** reproduces the round-1 bug (summed fails the rule, normalized passes) | `modes.py`, `main.py:gate_g9_on_loop`, `selftest.py` §[8] |
| **B2** | **G4 reference provenance unasserted**: `--reference` was checked for existence only; pointing it at a SIMULATED output (lag clamped to the tick floor) made every G4 condition pass on a broken tree | `modes.reference_provenance()` asserts the reference is a real **`base`-mode** run, **not simulated**, with a **recorded repo_head**; mismatch = **FAIL**, never a silent pass; asserted as the `g4_reference_provenance` fence before G4 consumes it | `modes.py`, `main.py:gate_g4_paired_lag` |
| **B3** | **Truth-table overclaim**: §8 was an empty placeholder while the doc asserted the 2×2; the BASE-red artifacts were workspace-local, not committed | §8 filled with the measured matrix; the four cell reports + the table log are **committed** under `raw/truth-table/` with a manifest; every sentence the old evidence did not support is qualified below | §8, `raw/truth-table/` |

### The G5 increment

* **Fixture clock PINNED** (`seed.py`): every `datetime.now(timezone.utc)`
  anchor → `FIXTURE_EPOCH = 2026-10-29T00:00:00Z` (env-overridable for a
  deliberate re-baseline; a malformed override REFUSES rather than falling
  back to wall clock). Measured effect: **9/9 normalized body hashes
  identical across two independent runs** (that two-run comparison lived in
  a gitignored run workspace and is **not committed**; the only committed
  9/9 is the single-run G5 fence, §8).
* **`gate_g5` implemented** (it did not exist): 9 requests = the **8** censused
  shapes **+ `/api/instances/{id}`** (the plan's §(e); the detail id is chosen
  deterministically — most children, tie-break by id); children-order
  normalization applied **post-processing** to the parsed payload; normalized
  hashes compared against the paired reference; shape-count fence (9, missing
  or non-200 = FAIL loudly); children-arrays-present fence.
* **A measured retraction.** The BASE-side "unordered children" positive
  control is **impossible on this host**: the query has no ORDER BY
  (`repository.py:434-437`, verified statically) yet the planner returns
  primary-key order, so children arrive **already sorted** — measured
  130/130 BFS + 1/1 detail arrays canonical at BASE under **both** ascending
  and **reversed** sibling insertion (the `seed.py` discriminator experiment,
  kept in the fixture). Part (ii) is asserted **at HEAD only**; the
  contract-unspecified half belongs to §5.2 correction 3's static ORDER BY
  pin (the pin pack). The round-1-doc implication "G5's order half
  discriminates BASE↔HEAD" is **retracted** — on this host it does not.
* **`jobs_active_badge` added to the canonical 8-shape set** (the plan froze
  7). Measured **7 round-trips/request** at BASE. The caller sends no limit
  param, so C-4 cannot bound it without a semantics change: it is an
  **ENUMERATED RESIDUAL**, gated on **non-regression only** (HEAD ≤ BASE,
  per-request), never an absolute bound.

### Ride-alongs also fixed

* **W-4** — a simulated run no longer prints a bare `PASS`: the verdict string
  is `"PASS (SIMULATED — not gate evidence)"`, so the README's claim is now
  enforced by the CLI output itself.
* **W-5** — `capture_gate.captures_without_loop_section` lists the per-capture
  "no evidence" cases instead of folding them into a clean aggregate.

---

## 1. The defect (architect-verified)

`architecture-recommendation.md` **§6 "BLOCKING GATE DEFECTS — the lane
cannot pass its own gate as committed"** names four instruments that must
be repaired before any HEAD functional run. All four are in the harness:

| # | Defect | Site (pre-repair) | Consequence |
|---|---|---|---|
| D-i | The family pin is **thread-agnostic** while stored under a main-thread key; the main-thread identification is dead code | `main.py:410-415` (fam over ALL sections) + `:416` (stored as `frame_family_present_in_main_thread`); `:385-401` (`main_native`/`main_section` computed, never used) | At HEAD the **bridge** thread's own instance-repo frames read POSITIVE ⇒ **near-certain FALSE-RED**, not flaky (§6.1) |
| D-ii | The capture-count fences are **BASE-only** | `main.py:337` `captures_collected >= 1`; `:423-427` `fam >= 1`; `:434-437` `narrow >= 1` | At HEAD — where the pins must go NEGATIVE — **the harness exits 1 on the success condition**. It has no HEAD mode (§6.1) |
| D-iii | **G2 is a two-sided band** (0.067–0.23 s) | plan `:203`; not implemented in the harness | A healthy HEAD lands at **0.02–0.10 s** — BELOW the lower anchor ⇒ the band **fails a lane that works** (§6.2) |
| D-iv | **G9 "on-loop 0 in every shape"** | plan `:217`; no on-loop gate in the harness | False-reds the **routed-out missions** shape, whose census legitimately issues `SELECT instances` on the loop (§6.3, census #4) |

The commissioner's brief adds the discipline that governs every change
below: expected-collected-count fences on every gate touched; **mode-aware
about collection** (verify frames were actually collected before
concluding anything); and **do not weaken** the loop-lag regression pin,
the round-trip O(1)/bounded asserts, or the response-bytes diff gates.

---

## 2. What changed, and why (file by file)

### 2.1 `modes.py` — NEW (the whole mode contract in one place)

* the two **mutually exclusive** modes (`base` / `head`) and the
  `simulate` axis, with `validate_mode` refusing anything else;
* the architect's **literal §6.1 predicates**:
  `LOOP_THREAD_MARKER = "asyncio/base_events.py"`,
  `FAMILY_REPO_PATH = "daemon/repositories/instance/repository.py"` +
  `SQLALCHEMY`, and the narrow pin
  (`list_child_ids_permanent` + the router) kept as the BASE positive
  control only;
* `analyze_capture_sections()` — per capture: section count, loop-section
  count, and family/narrow violations **on the loop thread** *plus* the
  thread-agnostic counts as `*_DIAGNOSTIC` (transparency for the §6.1
  false-red mechanism, never as the assertion);
* the **discriminator self-test** (§5);
* the G2 one-sided band, the G9 per-shape rules, the G4 paired
  comparison;
* the **HEAD-fix evidence simulation** (§7).

### 2.2 `main.py` — the gates

| Change | Why |
|---|---|
| `--mode` is **REQUIRED**, `--simulate` / `--reference` / `--capture-bodies` added | the modes must be explicit and mutually exclusive; a default would let a base-mode run be read as a gate run |
| `capture_assertion()` → `collect_captures()` + `gate_captures()` | the pre-repair analyser computed the pin over **every** section (D-i); the new one parses once, applies the §6.1 predicate to the **loop thread only**, and keeps the thread-agnostic count as a diagnostic |
| **collection fences in BOTH modes**: `captures_collected`, `capture_loop_section_collected`, `predicate_discriminator_selftest` | "G1 is INCONCLUSIVE (not PASS) if the capture phase collected 0 captures" (§6.1). Measured reason to go further than the literal: in the committed capture set **one capture has 0 loop-thread sections** (`raw/evidence/…25f8597a/stacks.txt`) — an analyser that reads "0 violations" off an empty section set would PASS vacuously. `capture_loop_section_collected` closes that |
| BASE mode: `fam >= 1`, plus `narrow >= 1` on `full` | today's positive control, unchanged in substance |
| HEAD mode: `fam == 0` **and** `narrow == 0` | the flip target (§6.1/§6.3) |
| `formula_table()` split into a pure computation + `gate_formula_table(mode)` | **the load-bearing structural fix**: the pre-repair harness asserted the BASE census *inside the collection phase*, so neither a mode switch nor a simulation could reach it. Assertions now run after collection + optional simulation |
| `gate_formula_table`: BASE = exact census / HEAD = **bounded** | see §4.3 for why HEAD is a bound and not the plan's band |
| `gate_g9_on_loop()` — NEW | the G9 on-loop fence did not exist; it is now per-shape and mode-aware, with a `g9_shapes_collected` fence so a missing shape cannot make it vacuous |
| `gate_g2_band()` — NEW | G2 had no implementation at all; now one-sided (§6.2) |
| `gate_g4_paired_lag()` — NEW | G4's bare-p95 form is not falsifiable at the tick floor (§6.4); now a **paired** comparison over STALL count / max / p95+1 tick / sample non-vacuity, **required** in HEAD mode |
| `per_endpoint_phase` records `body_sha256` / `body_len` per shape; `--capture-bodies` writes one raw body per shape | the G5 response-bytes gate had **no artifact to diff** — a silent no-op gate. Hash equality ⇒ byte equality; the opt-in raw bodies carry the children-order normalization |
| mode-suffixed output names (`BASELINE.<profile>.<mode>[.simulated].json`) | a mode-aware run must never overwrite the committed pre-repair baseline artifact `BASELINE.full.json` (the historical 34/34 record) nor a run of the other mode |

### 2.3 `child.py` — two instrument gaps the gates could not be expressed without

1. **per-shape on-loop attribution.** `RoundTripCounter` kept
   `on_loop` only as a **per-request total** and `by_shape` as
   `{shape: count}`. The architect's G9 fence is stated over the on-loop
   **shape-set** — unexpressible with that aggregate. `by_shape` now
   carries `{count, on_loop}`. (This is the reason the pre-repair harness
   *could not* have implemented §6.3 even in principle.)
2. **response body hash.** The middleware now folds every
   `http.response.body` chunk into `sha256` + `len` per request (§2.2,
   G5).

### 2.4 `driver.py` — the RAW lag series

`window_lag()` returned a p50/p95/max summary. A **STALL COUNT** cannot be
computed from a percentile summary, and the architect's §6.4 fence is
built on the count ("the synthetic handler-load leg is what carries the
discriminating signal"). The profile result now carries `lag.series`.

### 2.5 `run_all.sh` / `run_truth_table.sh` / `selftest.py` — NEW or reworked

* `run_all.sh` requires `--mode` (and errors on a HEAD run without
  `--reference`), keeps the per-variable env scrub, and writes
  mode-suffixed logs;
* `run_truth_table.sh` runs the 2×2 **sequentially** (the cells share one
  host and measure loop lag / loaded latency — parallel cells would
  corrupt each other) and prints the table;
* `selftest.py` — the fast instrument check (§5).

### 2.6 The SYNTHETIC HANDLER-LOAD LEG — a gap the first full run exposed

The architect names this leg in §6.1 ("the synthetic handler-load leg must
supply ≥1 capture at BASE and 0 instances-path frames at HEAD") and again in
§6.4 ("The synthetic leg does not exist today … a **tester-lane deliverable
the plan references but never schedules**"). It is now implemented
(`driver.synthetic_handler_load`, wired into the capture phase).

**MEASURED justification — this is why the repair needed it, not a
belt-and-braces addition.** The wedge-capture fires on a thread that
*sustains CPU*. At full scale that thread is a **jobs-enrichment worker**,
not the loop thread; the dump then samples every thread, and whether the loop
thread happens to be inside the sync-DB frames at that instant is luck:

| run (same tree, same fixture `seed=20261029`, same knobs) | captures | family pin **on the loop thread** |
|---|---|---|
| the committed pre-repair full run (`raw/evidence/`) | 5 | **4/5** |
| the repaired harness's first full run (2026-10-29T23:05Z) — gitignored run workspace, **not committed** | 8 | **0/8** |

In the 0/8 run every capture found the loop thread idle in
`selectors.select` while 4/8 captures carried the family frames on *other*
threads. A BASE positive control that fires 0/8 is not a control — BASE mode
would red on a tree where the defect is plainly present.

The leg drives the two **100 %-on-loop** instance shapes (`instances_bfs_home`
+ `instances_flat_badge`) from 4 concurrent workers for the capture window, so
the loop spends that window inside (or immediately adjacent to) the sync-DB
frames instead of a few percent of it. It is **mode-neutral by construction**:
the same load runs in both modes and issues the same statements — at HEAD they
are simply off the loop. Result at smoke: **6/8 captures with the family pin
on the loop thread**, plus 6/8 narrow (a pin previously described as
full-only) — smoke runs are gitignored, so these are **not committed**; the
committed full-profile figure is cell 1's **2/8 on the loop thread (7/8
diagnostic across all threads)**. Non-vacuity fence: `capture_load_leg_active`
(145 requests in cell 1; ≥ 10 required).

---

## 3. Where the architect's literal wording could not be applied verbatim

The brief: *"Apply the architect's literal fence wording where it exists;
use judgement (and cite your reasoning) where it does not."* Four places
needed judgement. Each is a **measurement**, not a preference.

### 3.1 `captures_collected` stays a hard fence in BOTH modes

The brief lists `main.py:337` among the "BASE-mode assertions" that
false-red at HEAD. It is not a defect assertion — it is the **collection
fence**, and §6.1's own corrected fence says *"G1 is INCONCLUSIVE (not
PASS) if the capture phase collected 0 captures"*. Keeping it in both
modes is therefore the faithful reading, and removing it would create
exactly the false-green family the brief forbids. **Kept.**

### 3.2 The D-9 prefs residual is TWO statement shapes, not one

§6.3 writes the HEAD residual as `{"SELECT instance_ui_prefs": 2}`. The
committed baseline **measures** the prefs work as two distinct statement
shapes, one of each per request:

```
raw/BASELINE.full.json  per_endpoint.instances_flat_badge.statements_by_shape
  {'SELECT instance_ui_prefs': 30, 'SELECT instance_ui_prefs+instances': 30,
   'SELECT instances': 330}            # 30 samples ⇒ 1 + 1 + 11 per request
```

The architect's **count** (2) is exactly right; the **shape name** is a
transcription of one half of the measured pair (the other half is the
prefs JOIN over instances). Asserting the single-shape literal would
false-red a correct fix.

Resolution: the gate asserts the architect's **intent** against the
**measured** names —

* `forbid_exact = ("SELECT instances",)` — the decisive claim: **no
  instances-path statement stays on the loop**;
* `allow_prefixes = ("SELECT instance_ui_prefs",)` — anything on the loop
  must be the disclosed prefs residual and nothing else;
* `max_on_loop = 2` — the architect's count, as a ceiling.

The missions shape keeps §6.3's **literal exact** counts
(`SELECT instances: 2`, `SELECT job_queue_items: 1`) because the measured
census is exactly that (`{'SELECT instances': 20, 'SELECT job_queue_items':
10}` over 10 samples) and the lane does not touch it.

### 3.3 HEAD round-trip totals: a BOUND, not the plan's band

§6.3 defers the totals to "the plan's §(d) table", whose values are
projections written with "~" anchors (`~8`, `~6–10`, `~10–25`,
`~15–30`). Two independent derivations disagree by 2–5 counts at the lower
edge (`architecture-recommendation §2.2` says the jobs storm "collapses to
~10–25"; the per-shape decomposition of the measured statements yields 8).
The **scientific** claim is O(N) → O(1) — an **upper** bound. A lower
bound would (a) forbid the fix from landing better than projected and (b)
false-red on the projection's own disagreement — precisely the failure
class the architect corrected for G2 in §6.2. The gate asserts the bound
and **reports** the projection. Bounds: flat ≤ 6, BFS ≤ 12, work ≤ 12,
jobs recent ≤ 25, jobs page ≤ 30; defer-blocked and missions are asserted
**exactly unchanged** (the plan says so explicitly).

### 3.4 G2, the narrow pin and G2's non-vacuity are SCALE-dependent

Measured, not assumed: at 1/10 scale (smoke) the loaded flat leg's p95 is
**0.139 s** — inside the healthy band — because the loop-queueing
inflation G2 measures (BASELINE §3: "the flat badge leg's p95 inflates 13×
purely from loop queueing") does not reproduce with 350 instances and a
16 s window. At full scale it is **0.914 s**. Likewise the architect's
`badge_ticks >= 5` non-vacuity cannot be met by a 2-tick smoke window, and
the narrow pin is already documented as scale-dependent.

Those three are therefore **asserted on `full` and reported (loud
`[fence:INFO]`) on `smoke`**. Every other gate — the family pin, the
on-loop attribution, the round-trip counts, the predicate self-test, the
collection fences — is asserted on **both** profiles. The **truth table in
§8 is run at `full`** so that none of them is merely reported.

---

## 4. The mode contract

`--mode=base` asserts the defect is **present**; `--mode=head` asserts it
is **absent**. A mode never changes *what is measured* — only which side
of the defect the shared predicates expect, so the same instrument decides
both verdicts.

| Gate | `base` | `head` |
|---|---|---|
| G1 family | `fam_loop >= 1` | `fam_loop == 0` |
| G1 narrow | `narrow_loop >= 1` (full) | `narrow_loop == 0` |
| G1 non-vacuity | `captures>=1` ∧ `loop_sections>=1` ∧ discriminator self-test | identical |
| G2 | verdict ≠ PASS (the defect is measurable) | verdict == PASS (one-sided, ≤ 0.230 s) |
| G4 | paired vs reference (report-only if absent) | paired vs reference (**required**) |
| G9 on-loop | every round-trip of the instances/missions shapes on the loop; none of the jobs shapes | per-shape SET == disclosed residual; every other shape empty |
| G9 counts | BASE census **exact** | O(1)/bounded |

---

## 5. The non-vacuity proof that does not depend on the tree

Two layers, both cheap:

**(a) The discriminator self-test** (`modes.discriminator_selftest()`,
run as a fence inside every real run *and* standalone via `selftest.py`).
It applies the predicates to three synthetic stacks that mirror the real
captured shapes:

* **BROKEN** — loop thread inside `psycopg → sqlalchemy → sqlmodel →
  repository.py:list_child_ids_permanent → routers/instances.py →
  base_events.py` ⇒ family **fires**, narrow **fires**;
* **FIXED** — the same DB chain on a `[db-offload]` thread, the loop
  thread free ⇒ family **silent**, narrow **silent**, and the
  thread-agnostic count is **1** (the §6.1 false-red, asserted as such);
* **PREFS-RESIDUAL** — the router frame over
  `instance_ui_prefs/repository.py` ⇒ **not** a violation (the architect's
  explicit exclusion).

Without this, HEAD mode's "0 violations" would be unfalsifiable: a gate
that cannot detect the violation would pass it.

**(b) The rehearsal against the committed real evidence** — the same
predicates applied to all five committed captures (see `selftest.py`
section [2]/[3]): 4/5 already carry the violation **on the loop thread**
(the 5th carries it only off-loop: the exact false-red shape), and after
the simulation transform the loop-thread count goes 4 → **0** while the
off-loop count stays ≥ 1.

`python3 .agents/tester/harness/eventloop-phase0/selftest.py` → **62/62
PASS** (re-measured 2026-10-30 from this worktree: exit 0, `SELFTEST: 62/62
checks PASS`; the 43/43 figure was the ROUND-1 count — council inc2 W2).

---

## 6. Entrypoint for the later functional gate (documented)

```bash
H=.agents/tester/harness/eventloop-phase0

# 1. BASE reference (also the positive control)
bash $H/run_all.sh --mode=base --profile=full

# 2. HEAD gate (requires the reference; G4 is paired)
bash $H/run_all.sh --mode=head --profile=full \
     --reference=$H/raw/BASELINE.full.base.json

# 0. (optional, seconds) instrument check before either
python3 $H/selftest.py
```

The gate must run BOTH directions: BASE mode must stay GREEN at BASE and
must RED at HEAD (else the base assertions are vacuous), and HEAD mode
must GREEN at HEAD and must RED at BASE (else the head assertions are
vacuous).

---

## 7. The simulation — what it is, and why it is not a tautology

`--simulate=head` transforms the **measured** evidence into the evidence
the fixed tree would produce, using the fix's published semantics. It is
**not** a production fix and **not** a measurement.

| Rule | Semantics (source) | Derivation from measured input |
|---|---|---|
| **S1** stack frames | the sync DB work moves to the bridge pool, so the loop thread's stack no longer carries the instances-repository frame (nor the DB frames above it); the loop frame and the router frame stay | the contiguous DB-call-chain run ending at the repository frame is **removed from the loop section**; the loop marker survives |
| **S2.instances** | the per-row children N+1 collapses to ONE batched select (§5.2 / plan §(d)) | `count' = count − rows + 1`, `rows` **measured from the response body** → flat 13−10+1 = **4**, BFS 248−241+1 = **8** |
| **S2.batch** | the per-row instance lookups collapse into the batched selects of A12/D-B ("one job_ids IN-select + one instance batch + one mission batch") | `count' = (count − instances_selects) + 3` → work 1873−1869+3 = **7**, jobs recent 1439−1434+3 = **8**, jobs page 1874−1869+3 = **8** |
| **S2.unchanged** | the lane does not change these counts (plan: defer-blocked "89 — UNCHANGED", missions "3 — unchanged") | identity |
| **S3** on-loop | the instances-path DB work is off-loaded; the D-9 prefs merge and the routed-out missions census stay | every statement off-loop except the prefs-family shapes; missions/jobs untouched |
| **G2** loaded flat | the BASE inflation was loop queueing (BASELINE §3) | the loaded flat p95 is replaced by the **measured unloaded floor p95 of the same run** (940.99 ms → **86.5 ms** — the values the committed simulated artifact's `simulation.provenance.rules` records for `S2.g2`; an earlier run's pair 0.9137 s → 0.0678 s is **not committed**) |
| **S4** loop lag | with no handler holding the loop the interval IS the watchdog tick | every sample clamped to the **measured tick floor** (the series' p50) → STALL count 0 |

**Why this is not a tautology.** The rules are stated in terms of the
fix's semantics and consume only **measured** inputs (`rows`, the
per-shape statement counts, the floor p95, the series p50); none writes a
constant. Three independent agreements follow:

* S2's outputs land on the plan's own projections (4 ≤ 6, 8 ≈ 8,
  7 ∈ [6,10], 8 ≤ 25, 8 ≤ 30) — derived from the *measured* statement
  counts, not from the assertions;
* G2's out-of-band value (**0.0865 s**, the value the committed cell-3 fence
  reports) lands inside the architect's *independently* stated healthy range
  **0.02–0.10 s** (a value I did not choose: it is the unloaded floor the
  harness measured at BASE);
* the assertions consume the architect's / plan's published numbers, which
  are **different quantities** from the rule's inputs.

**Where it *is* circular, stated plainly:** S3's residual set and §6.3's
asserted residual set necessarily coincide — both ARE the D-9 disclosure.
The protection there is not independence, it is the **discriminator
self-test** (§5) plus the fact that the same predicates red on the real
BASE evidence (§8, cells 2 and 4). Every transformed value is printed with
its rule and derivation in `report.simulation.provenance.rules` so a
reviewer can check the rule against the plan rather than against the
assertion.

---

## 8. The 2×2 truth table (instrument × tree)

## 8. The 2×2 truth table (instrument × tree)

**All cells below ran through the REAL gate path** — no `simulate_report`
shortcut on any unit-bearing assertion (the round-1 table's cell 3 routed
through the simulation's own unit normalization, which is how Blocking 1
hid; the round-2 gate normalizes units itself, and the simulated reports are
written back in the SAME sample-summed form the collector produces, so real
and simulated evidence take one identical path).

**Trees.** The BASE tree is `3e976696b` (the round-2 harness commit on the
unmodified lane base — no production fix). While this table was being
produced, the developer lane landed the REAL fix on the branch
(`377f0b627` P1 offload seam + `fa42ba328` P2 batch-children/offload
instances surface), so a third, bonus row exists: **HEAD mode against the
real in-flight HEAD tree**. The simulated cells run on the BASE tree via
the documented overlay (`git checkout <harness-commit> --
.agents/tester/harness/eventloop-phase0`) so that every endpoint returns
200 and the collection fences measure the instrument, not an unrelated
endpoint failure.

| cell | mode | tree | verdict | exit | fences | failing |
|---|---|---|---|---|---|---|
| 1 | `base` | **BASE** `3e976696b` (real) | **PASS** | 0 | **52/52** | — |
| 2 | `head` | **in-flight HEAD** `dd6d1ed12` (real, P1+P2 landed) | **FAIL** | 1 | 58/63 | 5 (see below) |
| 3 | `head` | BASE + `--simulate=head` | **PASS** | 0 | **63/63** | — |
| 4 | `base` | BASE + `--simulate=head` | **FAIL** | 1 | 48/57 | 9 (all of them the BASE assertions rejecting the fixed state) |

**Cell 1 — BASE mode on the BASE tree (the positive control).** 52/52.
Reproduces the BASE census exactly and independently of the committed
pre-repair artifact: flat **13** rts / BFS **248** / work **1873** / defer
**89** / jobs recent **1439** / jobs page **1874** / missions **3**; on-loop
attribution 100 % for the instances/work/defer/missions shapes and **0** for
the jobs shapes; loaded flat p95 **1.1027 s** → G2 `FAIL_BASE_EQUIVALENT`
(BASE mode expects the leg outside the healthy band); captures 8/8 with a
loop thread, **family pin 2/8 on the loop thread** (diagnostic 7/8 across
all threads), narrow 2/8; the synthetic handler-load leg drove 145 requests.

**Cell 2 — HEAD mode on the REAL in-flight HEAD tree.** This is the most
informative single artifact of the commission: the instrument measures
exactly what the landed fix achieved and exactly what remains.

*Landed and now GREEN (measured, not asserted):*

| shape | BASE (cell 1) | in-flight HEAD (cell 2) | plan target |
|---|---|---|---|
| instances flat(10) rts | 13 | **4** | ≤ 6 ✔ |
| instances flat(10) on-loop | 13 | **1** (`SELECT instance_ui_prefs` only) | prefs residual ✔ |
| instances BFS(100) rts | 248 | **8** | ~8 ✔ |
| instances BFS(100) on-loop | 248 | **1** | ✔ |
| jobs recent(10) rts | 1439 | **7** | ≤ 25 ✔ |
| jobs page(100) rts | 1874 | **7** | ≤ 30 ✔ |
| loop-thread family pin | 2/8 captures | **0/4 captures** | G1/G3 FLIPPED ✔ |
| narrow pin | 2/8 | **0/4** | ✔ |
| G5 children order | (report-only) | **canonical 47/47 + 1/1** | §5.2 ✔ |
| G5 bytes vs BASE tree | — | **8/8 COMPARED shapes byte-identical** after normalization (9 rows − `shape:work`: 500 ⇒ no `normalized_sha256` ⇒ excluded from the diff; 0 diffs) | ✔ |

*Still RED — correctly, because those phases have not landed:*

- `g9_on_loop_head[defer_blocked]` — 89 statements still **on the loop**
  (C-3 not landed yet).
- `g2_band[head]` — loaded flat p95 **0.9687 s**: the flat leg itself is now
  4 rts, but it still queues behind the defer-blocked leg's 89 on-loop
  round-trips. This is the architect's §2.2 prediction ("G2 is
  D-B-dependent, not bridge-dependent") **confirmed on a real tree**.
- **`/api/work` FAILED to return 200** (`g5_bodies["shape:work"]` =
  `{"status": 500, "error": null}`), so `floor_samples[work]` reads `0/10 ok`
  and `head_round_trips_bounded[work]` reads `measured=None`. **The G5 red
  carrier is `g5_shapes_collected`** — `"9/9 shape(s) fetched,
  all-200=False"`, red on the **all-200 clause**, not on the count. The
  byte-diff did **not** compare the work body at all: a 500 carries no body,
  so the shape has **no `normalized_sha256`**, and `gate_g5` builds `diffs`
  only over rows whose hash is non-null — work is therefore **excluded from
  the comparison** (measured: compared 8, matched 8, excluded
  `['shape:work']`). The fence's "9/9" is the raw-row denominator
  (`len(rows)` = 9), not a comparison count — see §9b item 9. **This is a
  lane finding, not an instrument finding**: the endpoint errors in the
  in-flight tree (P3/C-4 not landed or a regression) — routed to the
  developer lane.
- G4 paired vs the BASE reference: **ok** (provenance ok; the reference is
  a real base-mode run).

**Cell 3 — HEAD mode on the simulated fixed tree: PASS, 63/63, ZERO failing
fences.** This is the cell the round-1 table could not honestly produce: it
runs through the REAL gate path (the simulated report is written back in the
same sample-summed form the collector produces, so the gate's single unit
normalization serves it identically). Measured: rts flat **4** / BFS **8** /
work **7** / jobs recent **8** / jobs page **8** / defer **89** (unchanged) /
missions **3** (unchanged); on-loop flat+BFS **2** (the prefs residual,
decomposed into its two measured statement shapes), work/defer/jobs **0**,
missions **3**; loop-thread family pin **0/8** while the diagnostic
any-thread count stays **6/8** (the bridge-side frames the §6.1 predicate
must ignore); G2 **0.0865 s → PASS** (inside the architect's 0.02–0.10 s
healthy range); G4 paired **ok** with provenance **ok**; G5 **9/9 bodies
byte-identical** to the BASE reference; jobs_active non-regression 7 ≤ 7.
The verdict string is `PASS (SIMULATED — not gate evidence)`.

**Cell 4 — BASE mode on the simulated fixed tree: FAIL, 9 fences — the
BASE-still-red proof.** Every failing fence is a BASE assertion rejecting the
fixed state, which is exactly what a non-vacuous positive control must do:
`formula[flat]` 13 ≠ 4, `formula[BFS]` 248 ≠ 8, the on-loop census for
flat/BFS/work/defer (2 of 4, 2 of 8, 0 of 7, 0 of 89), `stack_pin_family`
0/8, `stack_pin_narrow` 0/8, and `g2_band[base]` (0.0835 s is inside the
healthy band, so "the defect is measurable" no longer holds).

**Committed evidence.** The four cell reports and the table log live in
`.agents/tester/harness/eventloop-phase0/raw/truth-table/`
(`20261030T022710Z-*`), with a `MANIFEST.md` recording the instrument
commit, the harness tree-sha and every cell's verdict/fence count — so the
non-vacuity proof is reviewable from the repository alone.

**Verdict: 4/4 cells match their expectation.** The instrument separates the
BROKEN tree from the FIXED tree in BOTH directions, through the real gate
path.

---

## 9. Findings routed to the DEVELOPER lane (not instrument defects)

1. **`/api/work` errors in the in-flight HEAD tree** (`dd6d1ed12`, P1+P2
   landed): the shape failed to return 200 in the HEAD-mode run —
   `floor_samples[work]` (`0/10 ok`), `g5_shapes_collected`
   (`"9/9 shape(s) fetched, all-200=False"`) and
   `head_round_trips_bounded[work]` (`measured=None`) all red.
   **G5's byte-diff never compared the work body**: a 500 returns no body,
   so the shape carries no `normalized_sha256`, and `gate_g5` builds `diffs`
   only over rows whose hash is non-null — measured compared **8**, diffs
   **0**, excluded **`['shape:work']`**. The fence's "9/9" is the raw-row
   denominator (`len(rows)`), not a comparison count (see §9b item 9). At
   BASE the same shape returns
   200 with 1873 rts, so this is a regression or an unbuilt phase (C-4 /
   D-B), not a harness artifact. **The 8 hash-bearing shapes are
   byte-identical to
   the BASE tree after the children-order normalization.**
2. **G2 remains red with the flat leg already fixed** (0.9687 s loaded p95
   while flat itself is 4 rts): the leg queues behind defer-blocked's 89
   on-loop round-trips. The architect's §2.2 ("G2 is D-B-dependent, not
   bridge-dependent") is confirmed on a real tree — G2 should turn green
   when C-3 lands.
3. **defer-blocked still 89/89 on-loop** — C-3 not landed; the gate is
   correctly red.

## 9b. Assertions I could NOT repair / residuals

1. **The BASE positive control's duty cycle is thin but positive — 2/8 on the
   loop thread in the committed cell-1 artifact** (`stack_pin_family`: "2/8
   capture(s) show the instances-path DB frames on the LOOP thread",
   diagnostic 7/8 across all threads; `stack_pin_narrow` 2/8). An earlier
   full-scale run measured **3 of 8** with the synthetic leg and **0/8**
   without it, but that run lived in a **gitignored run workspace and is not
   committed** — the committed number is 2/8. The gate asserts `>= 1`, so a
   re-run has a ~2 % chance of a spurious BASE red (P(0/8) at p≈0.375).
   The obvious improvement — raising the leg's concurrency from 4 to 8 so the
   loop has queued work at every sampling instant — is a **one-line change
   I deliberately did NOT land**, because the branch tip must remain the exact
   instrument that produced the truth table below (the leader's
   runnable-as-clean requirement). Land it as its own commit and re-measure.
2. **Three gates are full-profile-only** (G2's band, G2's >= 5-tick
   non-vacuity, the BASE narrow pin — the last now also fires at smoke).
   Measured reason: the loaded flat p95 is **0.9687 s at full** (committed
   cell-2 `loaded_flat_p95_s`) vs **0.139–0.149 s at smoke** (gitignored run
   workspace — **not committed**) — the loop-queueing inflation G2 measures
   does not reproduce at 1/10 scale. Smoke prints a loud `[fence:INFO]` instead of a
   silent pass.
3. **G5 (response bytes) IS gated in round 2** — superseding this item's
   round-1 text ("instrumented but not gated"). Cells 2–4 carry
   `g5_normalized_bytes_equal_vs_reference`, `g5_shapes_collected`,
   `g5_children_arrays_present` and the two `g5_children_order` positive
   assertions, and the round-2 truth table ran the BASE↔HEAD comparison the
   round-1 note said was missing (cell 2: compared 8, diffs 0; cells 3 and 4:
   compared 9, diffs 0). The residual is item 9 below, not a missing gate.
4. **The HEAD round-trip bounds are the plan's projections.** The plan
   (`plan-overview.md:243`) says the tester re-pins them **once** from the
   developer's measured HEAD run. They live in exactly one place
   (`modes.HEAD_ROUND_TRIP_MAX`), so that re-pin is one line per shape. I
   deliberately did not add a JSON override: a second source for the same
   numbers is how the §6.2 class of error comes back.
5. **The missions on-loop exact counts need the NEW per-shape counter.** A
   pre-repair report cannot satisfy that gate — it fails loudly
   (`on-loop {} vs exact {...}`) rather than passing vacuously. Intended.
6. **HEAD mode had never run against a real fixed tree in round 1.** Round 2
   closes that: cell 2 (§8) is HEAD mode against the REAL in-flight HEAD tree
   `dd6d1ed12` (P1+P2 landed) — FAIL, 58/63, with its 5 reds itemised in §8
   and §9. What remains true: no HEAD cell has yet run against the COMPLETE
   fix (C-3 / C-4 / D-B outstanding), and cells 3–4 are simulations, labelled.
7. **The simulation mutates the report in place** (`simulate_report`) and
   re-derives the lag summaries it rewrote; it is applied only under the
   explicit `--simulate=head` flag and is labelled in three places.
8. **The capture-phase lag series is not gated.** The synthetic leg holds the
   loop on purpose, so that window's lag is a load artifact. G4 compares the
   `badge` / `home` / `jobs_page` windows only — none of which contains the
   leg.
9. **`g5_normalized_bytes_equal_vs_reference` reports a raw-row denominator and
   names no excluded shape.** The fence detail renders `<rows − diffs>/<rows>`
   — in cell 2 that reads **9/9** while the genuine comparison is **8**,
   because `shape:work` has no `normalized_sha256` (500 ⇒ no body) and is
   skipped by the `is not None` guard. A shape that errors is therefore
   neither compared nor named. Today the run is still red for the right
   reason one fence over (`g5_shapes_collected`'s `all-200` clause), but a
   future run in which a shape 500s *and* that count fence were relaxed would
   read a **vacuous green**. The honest form is `compared/total` plus the
   excluded list in the detail. **Instrument code is FROZEN in this pass —
   reported, not changed.**

---

## 10. Commit discipline, tree proofs (leader ruling C-8)

| Item | Value |
|---|---|
| **Commit** | `c3d87af26ac7a910eb0c76dddd8263859bfdf8c0` |
| **Author** | `tester <tester@ensemble.local>` (tester identity, set per-commit via `git -c`; the repo config was not mutated) |
| **Path class** | **tester instrument** — `.agents/tester/harness/eventloop-phase0/` only |
| **Files** | 9 (`README.md`, `child.py`, `driver.py`, `main.py`, `modes.py`, `run_all.sh`, `run_truth_table.sh`, `selftest.py`, `raw/.gitignore`) |
| **Staging** | path-scoped only: `git add -- <3 explicit new paths>` then `git commit -- <9 explicit pathspecs>`. **Never** `git add -A`, never `git add .` |
| **Harness dir tree-sha BEFORE** | `2d7633cbd355d8778b2d781bae436c6631957a36` (`HEAD^:.agents/tester/harness/eventloop-phase0`) |
| **Harness dir tree-sha AFTER** | `88009e7bfaba8c539d61afdb22de0767e0d92369` (`HEAD:.agents/tester/harness/eventloop-phase0`) |
| **Production trees in this commit** | `git diff --name-only HEAD^ HEAD -- daemon/ tests/ frontend/ plugins/` → **EMPTY** |
| **Committed evidence artifact** | `raw/BASELINE.full.json` blob `a830f933…` **identical** before and after (the pre-repair record is preserved, not overwritten) |
| **Runnable-as-clean** | `git status --porcelain .agents/tester/harness/` → **EMPTY** after the full 2x2 run (§8); every run artifact is gitignored, and `run_all.sh` references committed files only |

The commit message is self-attributing (G12 re-scoped to attribution): it
names the **harness repair**, the **mode switch**, the four architect
citations, the three instrument additions, the synthetic leg, and the **2x2
proof** — so the path class is legible from the message alone.

**Second commit (this note).** The repair note + the truth table land as a
separate docs-only commit under
`.agents/tester/RESULTS/` — the instrument commit stays purely the
instrument, and the evidence names the instrument commit it was produced
with.
