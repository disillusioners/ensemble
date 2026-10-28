# Delta Re-Check — db-pool-idle-txn fold (council closure verification)

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`). **Worktree:** `/home/nea/ensemble-src-wt-dbpool-idletxn`.
- **Fold commit under review:** `c46a30f29d6732bd5069516a37e07ac9a9e01ee8` ("docs(plan): fold council findings + phase 0 baseline"), 11 files, +1311/−674, **code tree diff vs base EMPTY** (verified: `git diff --stat 19fc11c8b c46a30f29 -- daemon/ tests/ scripts/ plugins/ frontend/ agents/` → empty).
- **Base for every code `file:line`:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87`.
- **Original review (the findings being re-checked):** `council-report.md` (same directory), commit `1798fe0b6` — verdict 🔴 REJECT, 12 CRITICAL / 14 WARNING / 9 ADVISORY.
- **Date:** 2026-10-28.

---

## 1. VERDICT

# 🟡 DELTA-CONDITIONS — **24 conditions**

> **⚠️ READ §14 (FINAL).** The **Phase-0 baseline landed mid-review** (`43e577a5b`) and it **refutes F4's premise** for the incident's shape — a new condition (**DR-29**) that is a *premise rewrite*, not a text fix. §1–§13 are the record as it stood before the baseline landed.

**The fold is real work, not a relabel** — and the Phase-0 baseline that landed during this review is **high-quality measured evidence** that confirms four of my findings empirically (the census blindness, the shared-DSN collision, F8's necessity, the `idle_in_transaction_session_timeout` signature) and settles OQ-1. It also **refutes a plan premise** (F4's), which is now condition **DR-29**. 9 of the 12 council criticals are **genuinely closed** in the plan text with named, correct resolutions; 3 are closed in substance but carry a **binding-text defect that survives** (C-7, C-10, C-12); **0 are relabelings**. All six documented deviations are **improvements** in substance — one of them (C-7) is an improvement in the *narrative* that the *binding decision* did not absorb. The fold's self-honesty (FOLD-MAP ST-5 — "a mechanical closure record of the fold, not a quality judgment") is exemplary and I endorse it.

**But conditions must close before the implementation phase may start.** All of them are **plan-text** defects — no code, no new analysis, no re-planning.

> **⚠️ READ §12 FIRST.** This section was written before the mechanical re-check worker reported. The authoritative close-out list is **§12**: **8 must-close-before-Phase-1** + **13 in the same fold pass** = **21 conditions**.
> The three below are **DR-1, DR-2, DR-3** — all still valid; **DR-16…DR-21** are the additions found by the mechanical pass (§11).

| # | Condition | Why it blocks |
|---|---|---|
| **DR-1** | `decisions.md:124` (D-35) asserts F4-b observes a **backend termination** that F4's mechanism cannot produce | A **false-RED gate**: the pin fails with the fix correctly in place, or the implementer adds a backend-killing mechanism F4 does not install and C-4 ruled provisional. Same *unachievable-gate* class the council called REJECT-level. |
| **DR-2** | The "one canonical ladder" is not one ladder, and **F8's pins (incl. the C-12 measuring pin) and F6's PG leg appear in no canonical gate row** | The flip condition may never be re-verified at HEAD ⇒ R13 ("the dominant producer") is unverified by any gate ⇒ C-12's "gate artifact" requirement is unmet. Three distinct scopes also share the single ID `G2.1–G2.3`. |
| **DR-3** | The W-11 consistency pin's scope was **narrowed** to two files and its exemption is **prose, not machine-delimited** | A **regression** against the council's W-11 ("consistent across all 12 artifacts, with a machine-checkable pin"); the undefined exemption can mask a real red — and DR-2's defect is exactly such a drift, in a file the narrowed pin does not scan. |

**Plus 8 WARNINGS and 4 ADVISORIES** (§4), all mechanical, all foldable in one pass — **plus 5 further CRITICAL, 9 WARNING and 1 ADVISORY from the mechanical pass (§11)**; the authoritative close-out list is **§12**.

**The fold is not the problem — the defects are all in binding text, and every one of them is foldable in one pass.**
**Phase 0 (measurement-only, scratch DB, no live contact) may start now**; the eight must-close conditions in §12 must be folded before Phase 1.

---

## 2. CLOSURE CROSSWALK — C-1..C-12

| ID | Closure | Evidence (artifact:line) | Verdict |
|---|---|---|---|
| **C-1** | C3 fence inert (0 collected read as green) | `decisions.md:50` (D-29: certified interpreter `/home/nea/ensemble-src/.venv/bin/python`; `uv run` and `/usr/bin/python3` dropped; "a zero-collected gate is FATAL, never green") · `test-strategy.md:11,:144` · `plan-overview.md:162` (lane-wide false-green invariant) · `phase3-plan.md:17` | **REAL** — and **mechanically runnable**: I executed the canonical gate form from the worktree cwd → `30 tests collected in 0.36s`; certification prints `daemon -> /home/nea/ensemble-src-wt-dbpool-idletxn/daemon/__init__.py`, `pytest 9.0.2 py 3.13.15` |
| **C-2** | A5/AC14/AC15/AC16/T2.6/F2-g mutually unsatisfiable | `decisions.md:60` (D-30: shared constant carries NO `application_name`; each pool sets its own at its own connect site; AC14/AC15/AC16/T2.4/T2.6/D-19/D-24/G2.4/F2-f/F2-g all change together) · `phase2-plan.md:80-84` (T2.1-T2.5 amended) | **REAL** |
| **C-3** | Leader ruling: engine-wide `statement_timeout` still live | `decisions.md:70` (D-31: DROP final; D-16 REVERSED) · `phase2-plan.md:80-81` (T2.1/T2.2 **REMOVED** `-c statement_timeout=30000`) · `plan-overview.md:209` (R1 carries a **REVERSED by C-3** marker) · `final-implementation-spec.md:120` (the `SET LOCAL statement_timeout = 0` future-cap note) | **REAL** |
| **C-4** | R15: no ordering rule, no named instrument, unmeasured derivation | `decisions.md:80` (D-32: `≥ 4 × measured max legitimate hold`, floor 30 s; measurement + command recorded; **ships DISABLED if unmeasurable** with enablement gated; the async twin's unbounded hold bounded-or-excluded; victim class from the measured P100) · `final-implementation-spec.md:180` (PROVISIONAL placeholder) · `plan-overview.md:344` | **REAL** (+ residuals DR-5, DR-7, DR-11) |
| **C-5** | F8 as written does not remove the hold; stale `inj` ⇒ double delivery | `decisions.md:90` (D-33: P0 read-decide → P1 content fetch, NO txn open → P2 write scope: re-read `inj`, RE-ASSERT terminal + dead-parent guards, ONE transaction) · `phase1-plan.md:50,55-57` (T1.0, T1.0e, T1.0f, T1.0g) · `phase1-plan.md:110` (AC30/AC33) | **REAL** — the three new invariants are each named **and** each carries a pin (F8-c, F8-d) |
| **C-6** | F8 is three sites, not one | `decisions.md:106` (D-34: all three named; the static pin scoped to the `with`-body only; the three expected sites named in the docstring so a fourth is a deliberate failure) · `phase1-plan.md:52,54` (T1.0b, T1.0d) | **REAL** |
| **C-7** | F4-b asserted an impossible PG state (⇒ green at base) | `test-strategy.md:281` (the pin: statement **cancelled (QueryCanceled)** → `to_thread` returns → connection returned → `checkedout()` back to baseline; mutation = remove the `SET LOCAL`) · `phase1-plan.md:76` (T1.7 **CORRECTED**) · `test-strategy.md:94` (HEAD expectation) — **but** `decisions.md:124` (D-35) still says "the lock-blocked backend is **terminated (row gone from `pg_stat_activity`)**" | **PARTIAL — DR-1.** The closure exists in the pin rows; the **binding decision record contradicts them** |
| **C-8** | A5's two-engine model missed the asyncpg adapter pool | `decisions.md:132` (D-36: third `application_name` on a **separate DSN**; DO NOT touch the shared `conn_string`; AC18 + T2.7 amended; asyncpg added to D-13; A5's saver clause dropped) · `final-implementation-spec.md:113` (F2.6) | **REAL** (+ DR-9) |
| **C-9** | G2.4 "unmodified" unachievable | `decisions.md:142` (D-37: amend `:86-88` value-by-value order-pinned; **keep `:65` untouched** as the D-17 guard; not in `SEALED_SHA_BASELINES` ⇒ no seal ceremony; correct F2-e's stale `:292` anchor) · `phase2-plan.md:84` (T2.5 AMENDED) | **REAL** |
| **C-10** | F7 would be GREEN on the incident's shape | `decisions.md:152` (D-38: occupancy **and** residence, co-equal; "count == 0" **withdrawn**; count may be reported and bounded, never asserted zero; PG state `idle` is a disclosure metric) · `test-strategy.md:326` · `final-implementation-spec.md:257` — **but** `phase3-plan.md:59` (T3.1) still says "G-B asserts both **count==0** AND checkedout() back to baseline" | **PARTIAL — DR-4** |
| **C-11** | No release posture in any artifact | `decisions.md:162` (D-39) · `plan-overview.md:325-355` (§15: stage-only, no promote/restart/`.env`/prod-DB contact, **provisional v0.27.12** with W9 live-census re-derivation, the 5-step merge-latest-if-moved honouring the L1 exit-78 refusal, `rollback_safe` adjudicated under the standing rule) · `final-implementation-spec.md:8` (§8) | **REAL** |
| **C-12** | The flip condition was undecidable (no F8 pin; D2 locked the wrong table on the wrong pool) | `decisions.md:172` (D-40: F8 carries a **measuring** pin; instrument = **instrumented checkpointer** (leader ruling 4), checkpoint-table lock = fallback; F8-a + F8-b added) · `test-strategy.md:102` (BASE expectation ≥1 backend AND `checkedout()` above baseline; if BASE cannot demonstrate, it is a regression guard — A10) · `test-strategy.md:360-364` (F8-a..F8-e with plantable mutations) | **PARTIAL — DR-2.** The pin and the BASE-vs-HEAD requirement are real, but **F8-b is named by no canonical gate row** ⇒ it is not a gate artifact |

**Totals: 9 REAL · 3 PARTIAL (C-7, C-10, C-12) · 0 RELABELING.**

---

## 3. THE SIX DOCUMENTED DEVIATIONS — ADJUDICATION

| # | Deviation | Verdict | Evidence / residual |
|---|---|---|---|
| **(a)** | C-7 — PG `statement_timeout` cancels the STATEMENT, it does not terminate the backend; the true observable is statement cancellation + connection return to baseline; the plantable mutation becomes "remove the probe's `SET LOCAL`" | **IMPROVEMENT in the narrative — REGRESSION in the binding text** | The planner's semantics are **correct** and the mutation is **correct and plantable** (`test-strategy.md:281`, `phase1-plan.md:76`). But `decisions.md:124` still carries my superseded clause ⇒ **DR-1**. *Ownership: the bad clause originated in **my own** C-7 required-change wording; the planner identified the right semantics and my clause was not absorbed into the decision record. I own the origin; the repair is one line.* |
| **(b)** | C-8 — the three-pool attribution model (asyncpg adapter opens an explicit `conn.transaction()` ⇒ can park idle-in-txn with no server-side reap; its DSN is separate so the P2-oracle immutability pin survives) | **IMPROVEMENT** | `decisions.md:132` (D-36) · `final-implementation-spec.md:113` (F2.6) · verified at `daemon/checkpoint_adapter.py:1229-1239` (`async with conn.transaction():` + three awaits between DELETEs) and `daemon/persistence.py:225` (shared `conn_string`) vs `:317` (P2 oracle `conninfo=conn_string`) vs `:355-359` (asyncpg pool). Residual → DR-9 |
| **(c)** | Phase 0 promoted from harness to BINDING PRECONDITION | **IMPROVEMENT** | `final-implementation-spec.md:15-41` (§0 is a GATE with the four evidence outputs (a)-(d) and the four-baseline contract) · `decisions.md:184` (D-41) · `phase0-plan.md:148` (§4 BASELINE.md output contract), `:174` (§6 phase gate) · `phase1-plan.md:5` (Phase 1 depends on Phase 0) · `test-strategy.md:125`. Residual → DR-2 (no canonical gate ID) |
| **(d)** | The two unconditional literals demoted to PROVISIONAL / Phase-0-measured (`4 × measured max legit hold`, floor 30 s; residence budget = measured P100 + margin, 2.0 s only a placeholder because M-R3's ~13-15 s dwell would redden a healthy daemon) | **IMPROVEMENT (with three residuals)** | `decisions.md:80` (D-32) matches my C-4 requirement **exactly** (measured rule · named instrument · floor · recorded measurement · DISABLED-if-unmeasurable with enablement gated · async twin bounded-or-excluded · victim class from the measured P100). `final-implementation-spec.md:180,:184` mark both PROVISIONAL with an explicit "danger" note for the 2.0 s figure. **Safety:** acceptable under the safety-precedence rule — the feature is narrowed, disclosed, and the alternative is documented; and it is **not** a regression against any shipped state, because at base the manager engine carries **no** `idle_in_transaction_session_timeout` at all (the pre-fold 60 s was planning-only, never implemented). Residuals → **DR-5** (AC14 freezes the placeholder), **DR-7** (no enablement mechanism), **DR-8** (honesty block must state applied/not-applied), **DR-11** (measured at base ⇒ sized for a hold class F8 removes) |
| **(e)** | Gate-ladder conflict resolved by git archaeology (pre-fold meanings recovered from `23c232b8d`; the legacy table retired to an audit column) | **IMPROVEMENT on the recovery — the resulting canonical ladder is defective** | I re-derived the legacy ladder independently from `git show 23c232b8d:.agents/shared/planning/db-pool-idle-txn/phase3-plan.md` §2.1: G0=guard reachability, G1=Phase-1 unit, G2=Phase-2 unit, G3=Phase-1 PG (F4 retention, F6 idempotency), G4=Phase-2 PG (wedge-capture), G5=F7 guard, G6=whole-tree — **the fold's mapping matches**. Residuals → **DR-2** (three scopes share one canonical ID; F8 and F6-PG have no row), **DR-12** (the G3 rationale is self-contradictory). *Record note:* the FOLD-MAP's §2 CF-1 claim that "the council's mapping was the more-wrong one" is **not supported** — `council-report.md` W-6 required "ONE canonical ladder" and proposed **no** G0 mapping; there was no council mapping to be wrong. Harmless, but the record should not attribute an error to the council that the council did not make. |
| **(f)** | The 7 cross-worker defects (mapping row, gate-table re-key, pin scope, interpreter refs, the `:11754-11557` typo, F7 static twins) | **IMPROVEMENT with one regression** | DF-1 (`final-implementation-spec.md:385`), DF-2 (`plan-overview.md:171`), DF-3 (`phase3-plan.md:45-51`), DF-5 (no `uv run` / `/usr/bin/python3` in live artifacts), DF-6 (`:11754-11557` → 0 hits in the contract), DF-7 (`test-strategy.md:349-350` F7-b-static / F7-e-static) — each re-verified by me. **DF-3's verification was scoped to the gate table §2.1 only; the pin-pack inventory §5 was NOT re-keyed ⇒ DR-2(c).** **DF-4's narrowing of the pin scope ⇒ DR-3.** |

---

## 4. FINDINGS

### 🔴 CRITICAL — must close before the implementation phase may start

#### DR-1 — D-35's F4-b clause (i) asserts a backend TERMINATION that F4's mechanism cannot produce ⇒ a false-RED gate

**WHERE** `decisions.md:124` (D-35 — the binding decision record) · contradicted by `test-strategy.md:281` (the pin row) and `phase1-plan.md:76` (T1.7).

**WHAT** D-35: *"F4-b asserts (i) the lock-blocked backend is **terminated (row gone from `pg_stat_activity`)** within the bound AND (ii) `engine.pool.checkedout()` returns to baseline within the bound."*

PG's `statement_timeout` **cancels the statement** (SQLSTATE `57014`, `query_canceled`); it does **not** terminate the backend. The backend survives, the transaction aborts, the client rolls back, and the connection is **returned to the pool** — its row **remains** in `pg_stat_activity` in state `idle`. Only `idle_in_transaction_session_timeout` terminates a backend (FATAL `25P03`), and **F4 installs neither**: it installs a probe-scoped `SET LOCAL statement_timeout`; the manager engine's engine-level `idle_in_transaction_session_timeout` is a *separate*, PROVISIONAL, Phase-0-measured knob (D-32) that is **omitted entirely** when unmeasurable.

**WHY IT MATTERS** The plan's own pin rows already state the correct observable (`test-strategy.md:281`: "the statement is **cancelled (QueryCanceled)**, the `to_thread` future returns, the connection is returned to the pool, and `engine.pool.checkedout()` returns to baseline"). The **canonical decision record a developer builds against contradicts them.** An implementer building to D-35 writes a gate that **FAILS with the fix correctly in place** (a false RED — the C3-class defect in the opposite direction, and precisely the *unachievable-gate* class the council called REJECT-level as C-9), or adds a backend-killing mechanism (`pg_terminate_backend`, or an engine-level idle-in-txn timeout) that F4 does not install and that C-4 has explicitly ruled provisional. Under the lane's own "safety-precedence" rule this is the wrong failure: the safe move is to narrow the assertion, not to add a killing mechanism.

**REQUIRED CHANGE** Rewrite `decisions.md:124` (D-35) to match `test-strategy.md:281` verbatim: F4-b asserts **(i)** the probe's statement is **cancelled** (`57014` / `QueryCanceled`), **(ii)** the abandoned `to_thread` future **returns**, and **(iii)** `engine.pool.checkedout()` **returns to baseline** within the bound — and state explicitly that the backend **row is EXPECTED to persist** in `pg_stat_activity` (state `idle`, returned to the pool); its persistence is the *correct* observable, not a failure. Additionally, reword the **leading summary** of `phase1-plan.md:76` (T1.7) so the superseded "assert that no backend is left `idle in transaction`" is not the first clause a reader meets. The mutation is **already correct and plantable** and must not change: "remove the `SET LOCAL statement_timeout` from `make_queue_probe`" ⇒ the statement stays blocked, the connection stays checked out, `checkedout()` does not return to baseline ⇒ RED. ✔

#### DR-2 — The "one canonical ladder" is not one ladder, and it omits the lane's only mechanism item

**WHERE** `test-strategy.md:411-416` (the canonical ladder rows) · `plan-overview.md:170-179` (the C3b mapping) · `final-implementation-spec.md:386-392` (§6.3) · `phase3-plan.md:46-52` (the re-keyed gate table) · `phase3-plan.md:78-92` (the pin-pack inventory).

**WHAT** — three mechanically-proven defects:

1. **Three distinct gate scopes share one canonical ID.** `G2.1–G2.3` denotes legacy **G2** (Phase-2 unit packs), legacy **G3** (Phase-1 PG legs) **and** legacy **G4** (Phase-2 PG legs) — in `phase3-plan.md:46/48/50` and in both mapping tables (`plan-overview.md:172-174`, `final-implementation-spec.md:387-389`). A canonical ID denoting three different commands and scopes is not a canonical ID: the W-11 consistency pin cannot distinguish a correct reference from an incorrect one, and the ladder cannot be executed "as written" because the same ID appears three times.
2. **F8's pins appear in no canonical gate row — and F8 is the lane's only mechanism item.** `test-strategy.md:411` (G1.1–G1.4) reads "F3, F5, F6 unit packs" (**no F8**); `:413` (G2.1–G2.3) reads "F2, F4 packs"; `:415` (G3.1–G3.4) reads "F7 guard packs". **F8-b (the C-12 measuring pin), F8-c, F8-d, F8-e and F6-b (the F6 PG leg) are named by no canonical row.** The contract §6.3 says F8-a "rides G1.x", but no G1.x row mentions F8.
3. **The pin-pack inventory — the table that says WHICH GATE executes each pack — still assigns packs to LEGACY gate IDs** (`phase3-plan.md:78-90`: "G1", "G2", "G3", "**G3/G5**", "G1.1–G1.4 / **G3.1–G3.3**"), which the fold's own lane-wide rule forbids (`plan-overview.md:176`: "the canonical ladder (G1.1…G3.5) is the **only** gate vocabulary … new references must use canonical IDs"). It also maps **F6's PG leg onto `G3.1–G3.3` — the F7 *guard*'s range** (`phase3-plan.md:84`), a gate that runs the guard file only. The FOLD-MAP's DF-3 verification was scoped to the gate table §2.1 only; §5 was not re-keyed. And because the W-11 pin scans only `decisions.md` + `architecture-recommendation.md` (**DR-3**), this drift is **invisible to the pin**.

**WHY IT MATTERS** C-12 required the flip measurement to be "a **gate artifact**". With no canonical row naming F8-b, the measuring pin is never executed by the lane's gate ladder ⇒ the flip condition is never re-verified at HEAD ⇒ **R13** ("the plan's fix set does not reduce the dominant producer") remains **unverified by any gate** — the exact outcome the original REJECT was about. Defect 3 additionally means the developer cannot know, from the plan set, **which gate runs the lane's only mechanism item's pins**.

**REQUIRED CHANGE** (a) Give each gate scope its **own** canonical ID — e.g. `G1.1–G1.4` (Phase-1 unit), `G1.5` (whole-tree), `G2.1–G2.3` (Phase-2 unit), `G2.4` (the amended pin), **`G2.5` (Phase-1 PG: F4 retention + F6 idempotency)**, **`G2.6` (Phase-2 PG: wedge-capture)**, `G3.1–G3.4` (F7 guard), `G3.5` (whole-tree) — and update the legacy→canonical mapping in **all three** artifacts (`plan-overview.md` C3b, `final-implementation-spec.md` §6.3, `phase3-plan.md` §2.1) to match exactly. (b) Add explicit coverage for **F8-a** (in the Phase-1 unit row) and **F8-b/c/d/e** (in the Phase-1 PG row) and **F6-b**. (c) Re-key `phase3-plan.md:78-90` to canonical IDs only and correct the F6 PG leg's gate. (d) Add the canonical ladder **rows** to `final-implementation-spec.md` §6 — see **DR-4a**.

#### DR-3 — The W-11 consistency pin's scope was narrowed to two files, and its exemption is prose, not machine-delimited

**WHERE** `plan-overview.md:179` · `final-implementation-spec.md:397` · `phase3-plan.md:92` (the G-consistency pin row).

**WHAT** (a) **Scope narrowed.** The pin now scans **only** `decisions.md` and `architecture-recommendation.md`. My council's W-11 required the fix-ID set, pack inventory, gate IDs, AC IDs and SC10 numbers to be consistent **across all 12 artifacts** with a machine-checkable pin. Drift introduced in `plan-overview.md`, `phase1/2/3-plan.md`, `test-strategy.md` or the contract is **not caught** — and **DR-2's defect 3 is exactly such a drift, living in `phase3-plan.md:78-90`**, a file the narrowed pin never reads. (b) **The exemption is a prose sentence**, not a machine-detectable region: *"an **explicitly exempt block** … the scanner skips it"*. A scanner cannot deterministically skip a prose-described region ⇒ the pin's behaviour is undefined, and an over-wide skip (the whole file, or a table whose extent shifts) **can mask a real red**.

**WHY IT MATTERS** This is the **documented root cause** of the drift the fold is closing (W-11: the parallel-authorship assumption). Narrowing the only mechanical drift-catcher, and leaving its exemption undefined, re-opens the class the fold claims to have closed. It is a *consistency-gate* regression, not a safety-property regression — but it is the mechanism that keeps C-1…C-12 from silently re-diverging between the four artifacts that now carry them.

**REQUIRED CHANGE** (a) Widen the scan set to every artifact that carries gate IDs or AC IDs — `plan-overview.md`, `phase1-plan.md`, `phase2-plan.md`, `phase3-plan.md`, `test-strategy.md`, `decisions.md`, `architecture-recommendation.md`, `final-implementation-spec.md` — and add the fix-ID, AC-ID and pack-inventory checks W-11 named, not only gate IDs. (b) Delimit the exempt region with **machine-detectable sentinels** (e.g. `<!-- EXEMPT:BEGIN legacy-gate-map -->` … `<!-- EXEMPT:END -->`) and have the pin skip **only** between sentinels; add a sub-assertion that the sentinel pair is present **exactly once**, so deleting or duplicating the markers itself REDs (otherwise the exemption can be widened silently). (c) State in the pin's docstring that the exempt region is the **legacy→canonical audit table only**, and that any gate reference inside it is not a live reference.

### 🟡 WARNING — fold in the same pass; must close before **Phase 1** (not before Phase 0)

#### DR-4 — C-10 residual: `phase3-plan.md:59` (T3.1) still asserts `count==0`

**WHERE** `phase3-plan.md:59` · contradicts `decisions.md:152` (D-38), `plan-overview.md:162` (the lane-wide invariant), `final-implementation-spec.md:257`.
**WHAT** T3.1: *"G-B asserts both **count==0** AND checkedout() back to baseline."* The C-10 amendment added G-E (occupancy) and G-F (residence) to the same row but left the superseded count assertion in place.
**WHY** A `count==0` assertion is exactly the invariant D-38 withdrew: it is **red for a healthy busy daemon** and **green for a slow leak**. Left in the phase-3 task, an implementer will build the withdrawn invariant.
**REQUIRED CHANGE** Rewrite the clause to: *"G-B asserts the census count is **REPORTED and BOUNDED** (`peak_occupancy ≤ λ_measured × budget`) — **never asserted == 0** — plus `checkedout()` back to baseline; G-E asserts occupancy; G-F asserts residence."* Re-check `phase3-plan.md:14` (the phase objective) for the same shape.

#### DR-5 — `AC14` hardcodes `idle_in_transaction_session_timeout=60000` and binds a default-collected pin to that byte-exact string

**WHERE** `phase2-plan.md:97` (AC14) + `:84` (T2.5, the knob-matrix pin) · contradicts `decisions.md:80` (D-32), `final-implementation-spec.md:120` (F2.3) and `:180` (§2).
**WHAT** AC14 requires the manager engine's `connect_args` to be exactly `"-c timezone=UTC -c application_name=ensemble-manager -c idle_in_transaction_session_timeout=60000"` — **asserted value-by-value by a default-collected pin**. D-32 says the value is a Phase-0-measured placeholder; F2.3 says the rendered string is a **TEMPLATE** and that the clause is **omitted entirely** when unmeasurable.
**WHY** Two branches of the fold's own design become unimplementable: a Phase-0-measured value ≠ 60000 makes the pin RED, and the "defined but NOT applied" branch makes the pin RED. The acceptance criterion therefore **freezes the placeholder the fold claims to have demoted** — the demotion is nominal.
**REQUIRED CHANGE** Amend AC14 to reference the **constant** (`MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS`) and the provisional/disabled disposition rather than the literal `60000`; make the knob-matrix pin assert the **rendered shape** (the option present, its value equal to the constant, the option ORDER pinned) rather than a literal; and assert the value against `daemon/constants.py`, not against `60000`.

#### DR-6 — the retired `≈689` census figure survives in seven live places, including SC10

**WHERE** `plan-overview.md:68`, `:242` (**SC10**), `:255` (A3) · `decisions.md:357` · `test-strategy.md:21` · `audit-method.md:249` — against `audit-method.md:283-296` and `test-strategy.md:370` (G-A).
**WHAT** The plan's own method doc states the ≈689 aggregate is **not scanner-independent** and that the canonical AST total is **779** with a **613**-family baseline (A=613 / C=4 / B=175 / D=148 / E=14 / F=0). The AST baseline appears in exactly **one** live place (`test-strategy.md:370`); the retired aggregate appears in **seven**, and **SC10 — the audit record's honesty criterion — pins the retired one**.
**WHY** This is my W-11's SC10-number requirement, and it is **not closed**. Three different totals are in play, and the criterion that judges the audit record's honesty is the one with the retired number. The FOLD-MAP's CF list does not include it as fixed.
**REQUIRED CHANGE** Replace ≈689 with the AST baseline constants in all seven places; have SC10 require the AST baseline; state the scanner-independent total (779) as the cited figure with the aggregate marked non-canonical, matching `audit-method.md`'s own wording.

#### DR-7 — the "ships DEFINED BUT NOT APPLIED" enablement gate has no mechanism

**WHERE** `decisions.md:80` (D-32) · `final-implementation-spec.md:120` (F2.3), `:180`.
**WHAT** The disposition is stated ("the knob is **defined but NOT applied** … enablement gated on the measurement") but no artifact says **how** it transitions, what the daemon does while disabled, or what the operator sees.
**REQUIRED CHANGE** One line: the clause is applied **iff** `BASELINE.md` records a measurement (the constant is then set from it); while disabled the rendered string omits the clause and the daemon logs `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED — no Phase-0 measurement` at boot.

#### DR-8 — the honesty block must state the backstop's applied / not-applied status

**WHERE** `final-implementation-spec.md:114` (F2.7) · `phase2-plan.md:83` (T2.11) · `daemon/constants.py:207-217`, `daemon/persistence.py:306-315`.
**WHAT** F2.7's required text covers "retention / transaction-hygiene **REPAIRED**" and "**DWELL / OCCUPANCY NOT REPAIRED**" — but not that the engine-level idle-in-txn backstop may ship **unapplied** pending the Phase-0 measurement.
**WHY** If Phase 0 cannot measure, the manager engine ships with **no** server-side backstop. That is a fact a future reader must see in the **shipped code**, not only in the plan — the same reasoning the lane itself applied to the honesty blocks.
**REQUIRED CHANGE** Extend the F2.7 / T2.11 honesty-block text with the backstop's status and its enablement condition.

#### DR-9 — the asyncpg "separate DSN" mechanism is unspecified

**WHERE** `decisions.md:132` (D-36) · `final-implementation-spec.md:113` (F2.6) · `daemon/persistence.py:225`, `:355-359`.
**WHAT** F2.6 requires a separate DSN carrying `application_name=ensemble-checkpoint-adapter` and forbids touching the shared `conn_string` — but no artifact says how.
**REQUIRED CHANGE** One implementation line: build the asyncpg DSN from the same config **without** reusing the shared `conn_string` variable, and pass `server_settings={"application_name": "ensemble-checkpoint-adapter"}` to `asyncpg.create_pool`; the shared `conn_string` stays byte-identical so the P2 oracle's `conninfo` is unchanged; F2-h asserts the oracle's conninfo carries **no** `application_name`.

#### DR-10 — the F8-c concurrent-escalation injection technique is named but not specified

**WHERE** `test-strategy.md:362` (F8-c) · `phase1-plan.md:55` (T1.0e) · `final-implementation-spec.md:141` (F8.6).
**WHAT** The mutation is "inject a concurrent escalation at the P0→P2 seam"; no artifact names the seam or the injection mechanism.
**WHY** A pin whose mutation cannot be planted deterministically is not a mutation-proven pin (the lane's own mutation-leg discipline, and my C-5's requirement of a "concurrent escalation injected at the P0→P2 seam").
**REQUIRED CHANGE** Specify the injection point (e.g. a test hook that flips `inj.state` to `INJECTED` / `TASK_DELIVERED` between P0's close and P2's open) and the observable (**no** artefacts created) — in the pin's own mutation description, so the implementer can plant it.

#### DR-11 — the bound is measured on **pre-F8** code but sized as if post-F8

**WHERE** `final-implementation-spec.md:15-41` (§0(a): "**Base-code** M-R3 hold duration") · `:180` (the applied value) · `decisions.md:80` (D-32) · `test-strategy.md:102` (the BASE-vs-HEAD expectation).
**WHAT** The Phase-0 measurement is taken on the **base** tree — i.e. **before** F8 lands. The measured "max legitimate hold" therefore **includes M-R3's 8-38 s dwell**, a hold class F8 removes. The council's C-4 required the bound sized against the **post-F8** legitimate hold, and the F7 residence budget likewise.
**WHY** Sizing from the pre-F8 figure is *safe* (a more generous bound cannot kill legitimate work) but it never shrinks after F8 lands, and the F7 guard's residence budget inherits the same conservatism — which weakens the very guard that must detect the recurrence.
**REQUIRED CHANGE** State explicitly that the Phase-0 (base) measurement is the **upper bound** for the applied value, and that the **post-F8** re-measurement (F8-b's BASE-vs-HEAD comparison) is the figure the applied value should use once available; if the base-measured value is applied in the interim, justify the margin against it explicitly (the base figure already includes M-R3, so the 4× multiplier is ≥ 4× of a hold that will not exist post-F8 — say so, and record the post-F8 re-measurement as the follow-up that tightens the bound).

### 🟢 ADVISORY

- **DR-12** — `plan-overview.md:141` still cites "the conjunction at :734-743"; the conjunction is at **`:739-743`** (`:734` is the section comment). The architect's A9-ii correction was never applied. One-line fix; same for any other stale base-relative refs.
- **DR-13** — the C3b mapping rationale for legacy G3 — *"Phase-1 PG legs are F4 retention (Phase-2 content)"* — is self-contradictory (F4 is Phase-1 content elsewhere). Reword as part of DR-2(a).
- **DR-14** — the council-fold table cites "decisions D-26/D-27 (extended)" for C-1, but the certified-interpreter decision is **D-29** (D-26/D-27 are the collected-count and env-scrub rules). Fix the citation.
- **DR-15** — `F7-e-static`'s static facts (1)-(4) duplicate F8-a's scope and F8-c/F8-d's subject matter. Not wrong, but the "every PG/integration pin has a default-collected static twin" rule (W-13) needs an explicit **pin → twin** map to be checkable at a glance rather than inferred.

---

## 5. ENFORCEABILITY — precondition by precondition

| Precondition | Gate row or prose? | Verdict |
|---|---|---|
| "No implementation change lands until Phase 0 completes" | **Phase-gate row** exists (`plan-overview.md:127` row "0" with a non-vacuity requirement; `phase0-plan.md:174` §6; `phase1-plan.md:5` dependency) — but **no canonical gate ID** (no `G0.x` anywhere) | **PARTIAL** — more than prose, less than addressable. Needs a canonical ID so the W-11 pin and the lane-wide rule cover it (**DR-2a**) |
| Cross-artifact consistency pin (W-11) | Pin row exists (`phase3-plan.md:92`) — but scope narrowed + exemption undefined | **NO** ⇒ **DR-3** |
| "F8 before the engine-level idle-in-txn bound" | **Prose** in a "sequencing constraints (hard)" list (`final-implementation-spec.md:6.1`) — **not** a gate row, and nothing reds if the bound is applied first | **NO** ⇒ must become a gate row (or be gated by DR-7's enablement mechanism) (**DR-11**) |
| "Zero new `.sql` files" | Gate (F6-d, process pin) ✔ | **YES** |
| "Detector untouched" (C1) | Phase-1 diff-review gate + SC6 ✔ | **YES** |
| "Net-new reds = 0" with a collected-count fence | `plan-overview.md:162` lane-wide invariant; G1.5 / G3.5 rows assert `collected > 0` on both sides + reconcile base vs head ✔ | **YES** |

---

## 6. PIN COVERAGE — spot audit

| Check | Result |
|---|---|
| Every numbered change in the contract's §1 has ≥1 pin | **YES** — F8.1–F8.9 → F8-a…F8-e; F3.1–F3.5 → F3-a…F3-e; F4.1–F4.7 → F4-a…F4-e; F6.1–F6.6 → F6-a…F6-d; F2.1–F2.10 → F2-a…F2-j; F5.1–F5.3 → F5-b/F5-c; F1.1 → F1-a |
| Every pin has a plantable mutation | **YES** for the 41 reviewed — each names one concrete code edit. The weakest are re-specified but still concrete: F7-b ("re-introduce a retention path") and F3-e / F6-e (the broadened mutations, now rewritten per my A-8) |
| Every PG/integration pin has a default-collected static twin | **PARTIAL** — F7-b-static / F7-e-static added (test-strategy.md:349-350), F7-d and F8-a present, F6-c/d present. **F8-e (the async twin's bound) and F6-b have no named twin**; the fold's CF-9 claim that "PG/integration pins … now have default-collected static twins" is **not fully true** (**DR-15**) |
| Zero-skips assertion on PG gates | **YES** — `test-strategy.md:405` ("A PG gate fails on skipped > 0") |
| **Two inventories** | **NO** — the contract's §5 (38 rows) and the plan's `test-strategy.md` §3 (40 pins) diverge: `F7-b-static` + `F7-e-static` exist only in the plan; `F1-a` exists only in the contract. W-6 required **ONE** pack inventory (**DR-15**) |
| The canonical ladder **rows** | **NO** — they exist only in `test-strategy.md:411-416`; the contract §6.3 declares itself the ladder's **authority** but contains no rows (**DR-2d**) |

---

## 7. C1–C4 NON-INTERFERENCE — verified

| Coupling | Verdict | Evidence |
|---|---|---|
| **C1** — no weakening of the `pool_leak` predicate or the grace refusal path | **PASS** | `plan-overview.md:36` (detector forbidden) · `:141` (no change to the predicate `:784-941`, exhaustion/trend arms, conjunction, streak arm, grace arming window, detail schema, P1/P2 sampling surfaces, `daemon/api.py:3939-3987`, grace/leak constants) · `:238` (SC6) · `:273` (§10) — the leak predicate and the grace refusal path are untouched; F4 lands on the observed pool, does not relocate the probe, and does not alter the predicate's inputs |
| **C2** — no conditionality near the rollback path | **PASS** | `plan-overview.md:147-149` (never-green **unconditional** at cap-reach; `scripts/upgrade/lib.sh` untouched) · `:198` (DO NOT TOUCH) |
| **C3** — every gate runs in a runner-EXECECUTED form and asserts its EXPECTED COLLECTED COUNT | **PASS on the form, FAIL on the ladder** | The form is right and **runnable** (I executed it: 30 collected, `daemon` → worktree). The ladder is defective ⇒ **DR-2** |
| **C4** — no regression of the soak lane's pool sizing 5+10; D-17 isolation intact | **PASS** | `plan-overview.md:95` (5 + 10 = 15 slots) · `decisions.md:33` (D-17 unchanged) · `:204` (D-43 marks D-17 VERIFIED-CLEAN) — the shared constant stays byte-identical; the P2 saver pool is DO-NOT-TOUCH (`:198`) |

---

## 8. SCOPE — no creep; the symptom class is stated plainly

- `plan-overview.md:16`: *"**This lane does NOT close the incident's symptom class.** The recurrence precondition — **sync DB work on the event loop** (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`) — is a **dwell multiplier** that this lane does not remove. The BFS depth-loop transaction span (`daemon/repositories/instance/repository.py:1434-1645`) is also untouched. After this lane the daemon can still hold 15/15 manager slots…"* ✔
- `decisions.md:194` (D-42): every routed item is named with its exact evidence and owning commission — event-loop sync-DB (the dominant untreated symptom driver), OQ-1 prod-data mint trigger, BFS-depth-loop dwell, missed-finalize, default-executor ceiling, proxy-side `coding-free` drop ✔
- `plan-overview.md:306`: *"The plan does NOT claim to close the incident's symptom class"* ✔
- No out-of-lane work crept into the fix list. ✔

---

## 9. IN FLIGHT — what the Phase-0 measurements MUST confirm or re-derive

**Do not block on it; these are the exact claims the in-flight run must settle.** The run is on a separate branch (`tester/dbpool-phase0-baseline`) against a scratch PG. Each item names the artifact value it fixes.

| # | Claim the measurement must settle | Artifact value it fixes | If it comes back different |
|---|---|---|---|
| **P0-1** | The **base-code M-R3 hold duration** under real load — for the async twin **and** the sync twin, including the nested session's checkout wait and the async twin's currently **unbounded** hold. The sampled `pool.checkedout()` time series (≤ 100 ms), not just a max. | The **F8 flip condition** (`final-implementation-spec.md:33`, `plan-overview.md:266` OQ-6) and R13 | < 2 s ⇒ approach C collapses to B on mechanism grounds; the plan must then say so and re-scope |
| **P0-2** | The **measured max legitimate hold** (P100) and the **continuous idle-in-txn gap** distribution, on pre-F8 code | `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` = 4 × measured, floor 30 s (`decisions.md:80`; `final-implementation-spec.md:180`) | Re-derive; if unmeasurable ⇒ the bound ships **DISABLED** (DR-7) |
| **P0-3** | The **post-F8** legitimate hold, to size the bound against the hold class that will actually exist | Same, plus the F8-before-bound ordering (**DR-11**) | If the post-F8 hold is materially lower, the applied value must be re-derived from it, not from the base figure |
| **P0-4** | The **F7 residence budget** = measured P100 legitimate hold + margin (≥ 2× the noise floor, floor 2.0 s) — and an explicit check that the chosen budget does **not** red a healthy daemon at the post-F8 hold distribution (the plan's own "danger" note: 2.0 s would red M-R3's ~13-15 s dwell) | `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S` (`final-implementation-spec.md:184`) | Re-derive; a budget that reddens a healthy daemon is a false RED (the C3-class defect in the opposite direction) |
| **P0-5** | The **per-pool `application_name` attribution**: that `ensemble-manager` / `ensemble-repair` / `ensemble-checkpoint-adapter` are distinguishable at runtime; that the shared constant carries **no** `application_name`; that the asyncpg separate DSN leaves the P2 oracle's `conninfo` unchanged | D-30 / D-36 / F2.3 / F2.5 / F2.6 / F2-h | Re-derive the census method |
| **P0-6** | The **F7 invariant shape** at base: the occupancy-vs-residence census, **including the count of PG-state `idle` backends** (checked-out-but-no-statement) — the class the census is blind to (C-10) — the residence-time buckets, and the `λ_measured` that `peak_occupancy ≤ λ_measured × budget` depends on | D-38 / `final-implementation-spec.md:255-257` / the F7 legs G-B…G-F | The invariant's thresholds and the λ source are derived from this, not guessed |
| **P0-7** | The **PG runtime semantics**: (i) whether a `statement_timeout` leaves the backend **alive** (row still in `pg_stat_activity`, state `idle`) after the statement is cancelled — **this is the DR-1 question**; (ii) PG last-`-c`-wins under the daemon's exact connect strings (D-17 isolation); (iii) whether `idle_in_transaction_session_timeout` actually fires (threshold + `25P03`); (iv) `SET LOCAL statement_timeout` inside SQLAlchemy's autobegin | D-35 (**DR-1**), D-17, D-32, F4.1 | If (i) is not as expected, DR-1's fix changes shape and the F4-b assertion must be re-derived from the measurement |
| **P0-8** | The **3-pool census** explaining the incident's 18 backends against the 15-slot manager cap | D-36 / the census method | Re-derive the attribution basis |
| **P0-9** | The **A-1 / A-2 BEGIN attribution**: whether psycopg3 **pipelines** the implicit BEGIN (making `pg_stat_activity.query` the *statement*) or not (making a literal `BEGIN` `last_query` a standalone-BEGIN marker) | `decisions.md` A-1 / A-2; the incident's `BEGIN`×2 fingerprint ownership set | Settles whether the fingerprint owner set is "manager engine + asyncpg adapter" (as A-2 now claims) or Site A |
| **P0-10** | The **base occupancy/dwell numbers** that substantiate the plan's claim that occupancy is a **symptom** and that the lane does not close the symptom class | `plan-overview.md:16`; the honesty blocks (F2.7) | If the base numbers do not support it, the honesty-block wording and the disclosure must be corrected |

---

## 10. CONDITIONS TO CLOSE — precisely

**These are foldable plan-text edits; no re-planning, no new analysis.** The leader folds them rather than re-planning.

**Must close before the implementation phase (Phase 1) may start:**

1. **DR-1** — align `decisions.md:124` (D-35) with `test-strategy.md:281`: the statement is **cancelled**, the `to_thread` future **returns**, `checkedout()` returns to baseline; the backend row is **expected to persist** in state `idle`. Reword the T1.7 summary (`phase1-plan.md:76`).
2. **DR-2** — one canonical ladder: give each scope its own ID (`G2.5` Phase-1 PG, `G2.6` Phase-2 PG, …); add F8-a/b/c/d/e and F6-b to the rows that execute them; re-key `phase3-plan.md:78-90` to canonical IDs and fix the F6 PG leg's gate; add the canonical ladder **rows** to `final-implementation-spec.md` §6; update all three mapping tables to agree.
3. **DR-3** — widen the W-11 pin to every artifact carrying gate/AC IDs, add the fix-ID / AC-ID / pack-inventory checks, and replace the prose exemption with **machine-detectable sentinels** (present exactly once).
4. **DR-4** — remove the surviving `count==0` from `phase3-plan.md:59` (T3.1).
5. **DR-5** — amend AC14 + T2.5 to reference the constant and the provisional/disabled disposition instead of the literal `60000`.

**Must close before Phase 1 (same fold pass; Phase 0 may run in parallel):**

6. **DR-6** (the retired ≈689 figure in seven places, including SC10) · 7. **DR-7** (enablement mechanism) · 8. **DR-8** (honesty block states the backstop's status) · 9. **DR-9** (asyncpg DSN mechanism) · 10. **DR-10** (F8-c injection seam) · 11. **DR-11** (pre-F8 vs post-F8 sizing) · 12. **DR-12**–**DR-15** (advisories: the `:739-743` ref, the G3 rationale, the D-29 citation, the pin→twin map and the two-inventory reconciliation).

**Sequencing note.** Phase 0 is measurement-only (no implementation change, scratch DB, no live contact) and can start now. But **DR-1 and DR-2 should be folded before Phase 0 concludes**, because they change what the Phase-0 outputs must confirm: DR-1 fixes the F4-b observable, and DR-2 fixes **which gate** the F8 measuring pin must run in.

---

*Produced by the reviewer (dispatcher/controller) as the delta re-check of the council's own findings. Verification workers: `7a8eafb7-539d-47e5-a1e1-b67929ec9dda` (mechanical) and `ccf5861b-6408-4024-8411-25d051a8c5d9` (substantive). The substantive worker judged all 12 closures REAL; I overrule it on **C-7** (D-35 still carries the impossible termination clause) and **C-12** (the measuring pin has no canonical gate row), both of which I verified by direct artifact inspection. Read-only re-check: no `daemon/`, `tests/`, `scripts/` or payload-tree file was modified.*

---

# ADDENDUM — mechanical re-check folded in

**Added:** 2026-10-28, after the mechanical re-check worker (`7a8eafb7-539d-47e5-a1e1-b67929ec9dda`) reported. Every claim below was **re-verified by me against the artifacts** before being folded in; the worker's own evidence is quoted where it is decisive.

**Correction to my earlier report to the leader:** I described the pin inventory as "a **44-pin** inventory". The **actual** count is **38** rows in the contract's §5 — pre-fold **38**, post-fold **38** (the fold changed **nothing** in the binding inventory; set-diff of pre- vs post-fold §5 = **0 dropped, 0 added**). The folded **plan** union is **41**. The correct framing is DR-17: the contract is 3 pins short of the plan, not that the contract shrank.

## 11. ADDITIONAL VERIFIED FINDINGS

### 🔴 CRITICAL

#### DR-16 — the C-1 closure is **partial**: Class-4a/4b remain unfenced

**WHERE** `test-strategy.md:196-212` (the Class-4 block) · claims it falsifies: `plan-overview.md:360` ("**R18** … **Closed by C-1 fold** — the Class-1 env/interpreter fence applies to 4a (per-variable scrub + mktemp HOME + certified interpreter)") and `plan-overview.md:226`.

**WHAT** The Class-4a/4b commands carry the **certified interpreter** and **nothing else** — no `cd` into the worktree, **no per-variable `POSTGRES_*` scrub**, **no `HOME="$(mktemp -d)"` fence**. The fold applied **1 of the 3** parts the council required ("Apply to Classes 1/2/3/**4a/4b** and the §2.3 script").

**WHY IT MATTERS** The worker proved the `cd` is load-bearing: from `/tmp`, the certified interpreter resolves `daemon` to the **shared checkout** (`/home/nea/ensemble-src/daemon/__init__.py`), not the worktree — so the same command form runs against *different code* depending on cwd. A green Class-4a/4b can therefore (a) execute against the **shared checkout** rather than the lane's fixed code, and (b) run with an ambient `POSTGRES_DB=ensemble_prod` still visible, on the **C1/C2 non-interference gate** whose packs touch the saver pool. This is the C1/C2 gate — the one that proves the lane did not weaken soak-gate safety.

**REQUIRED CHANGE** Rewrite `test-strategy.md:196-212` as literal copies of the Class-1 form (`cd /home/nea/ensemble-src-wt-dbpool-idletxn && \` + the 6-variable per-variable scrub + `HOME="$(mktemp -d)" \` + the certified interpreter); apply the same fence to the **§2.3 fence script** (`test-strategy.md:223-242`) and to the ladder's **unit rows** and **whole-tree row** (`phase3-plan.md:45-47`, `:51` — bare interpreter invocations with no `cd`); and correct `plan-overview.md:226`/`:360` to state that **only the interpreter** was applied until the full fence lands.

#### DR-17 — the binding contract's pin inventory is **38 of 41**, and 8 PG pins carry no zero-skips assertion

**WHERE** `final-implementation-spec.md` §5 (the artifact the developer builds from — `final-implementation-spec.md:53`) vs `test-strategy.md:349-350` (F7-b-static, F7-e-static) and `test-strategy.md:370` (G-A) · `final-implementation-spec.md:307` (F6-c), `:320` (F7-a), `:321` (F7-b), `:324` (F7-e), `:326` (F8-b), `:327` (F8-c), `:328` (F8-d), `:329` (F8-e).

**WHAT** (a) Three pins exist **only** in the plan: **F7-b-static**, **F7-e-static** (the W-13 static twins the fold added) and **G-A** (the census-family pin). The contract's §5 rules state *"every PG/integration pin has a **default-collected static twin** (W-13)"* and *"every PG pin asserts **zero skips**"* — but the contract's table contains **no row** for either twin, and **8** of its PG/integration rows show `✅` where the rule requires `✅ + zero-skips`. (b) `tests/postgres/conftest.py:167-169` **skips the whole module** when PostgreSQL is unreachable ⇒ a "green" PG gate that collected 0 and skipped everything is indistinguishable from a real pass unless the skip count is asserted.

**WHY IT MATTERS** A developer implementing from the **binding contract** builds no static twin for F7-b / F7-e and no census pin ⇒ deleting those PG packs leaves the default `pytest` run green — the W-13 false-green **survives the fold in the artifact that binds**. And the missing zero-skips cells are the C3 false-green class, in the same table.

**REQUIRED CHANGE** Add the three missing rows to spec §5 (same `Where / Marker / Mutation / Default-collected? / Count asserted?` columns as `test-strategy.md:349-350`, `:370`); change the 8 PG rows' last column to `✅ + zero-skips`; add `G-consistency` (`phase3-plan.md:92`) to spec §5 as well — the W-11 pin is currently absent from the contract the developer builds from.

#### DR-18 — `F6-e` is an **orphan** whose mutation cell is a TODO pointing at the wrong subsystem

**WHERE** `final-implementation-spec.md:309` · `test-strategy.md:322` · compare `final-implementation-spec.md:297` (F3-e) and `test-strategy.md:274`.

**WHAT** The contract's F6-e mutation cell reads **"Rewrite F6-c's old broad mutation as one concrete edit (A-8)"** — a TODO, not a mutation. The plan resolves it to *the same* edit as F3-e: *"Change the preview string at `:1482`"* — i.e. `daemon/tools/ens_db_tools.py:1482`, the **repair-tool dry-run preview string**, which has nothing to do with F6 (the **mission-record boot mint**, `daemon/repositories/mission_record/repository.py:498-514`). No F6.x numbered change lists F6-e (F6.1→F6-a/F6-b; F6.2→F6-a; F6.3→F6-a/F6-b; F6.4→F6-a; F6.5→F6-c; F6.6→F6-d).

**WHY IT MATTERS** **Deleting F6's entire upsert leaves F6-e GREEN** — the whole F6 change set has a pin that can never fire, and planting the mutation reds F3-e instead. The pin inventory is not mutation-proven for the lane's second-largest fix.

**REQUIRED CHANGE** Replace F6-e with a concrete F6-subject mutation — e.g. *"`OPEN_EPOCH_STATUS_SQL` at `daemon/repositories/mission_record/models.py:209` changed from `status = 'open'` to `status = \"OPEN\"` ⇒ the conflict-target predicate no longer byte-matches the partial index ⇒ RED"* — add F6-e to F6.1/F6.2's Verify cell, and delete the TODO cell at `final-implementation-spec.md:309`.

#### DR-19 — the **task graph** does not gate the preconditions the council made binding

**WHERE** `phase1-plan.md` "Depends" column (T1.0–T1.12) · `phase2-plan.md` "Depends" column (T2.1–T2.12) · claims: `final-implementation-spec.md:20` ("This section is a GATE … no implementation change lands until Phase 0 COMPLETES"), `test-strategy.md:400` (sequencing constraint 1), `council-report.md` C-4 REQUIRED CHANGE (a).

**WHAT — mechanically extracted:**

```
phase1-plan.md:  T1.0→Phase 0   T1.1→—   T1.4→—   T1.8→—   T1.11→—   T1.12→T1.0-T1.11
phase2-plan.md:  T2.1→—   T2.2→T2.1   T2.3→T2.2   T2.4→—   T2.5→T2.3   T2.6→T2.4   T2.7→—   …
```

(a) **Only the F8 tasks depend on Phase 0.** F3 (T1.1), F4 (T1.4), F6 (T1.8) and F1 (T1.11) have **`Depends = —`** ⇒ four of the five change groups can land with **no `BASELINE.md`**, directly violating the binding precondition. (b) **No Phase-1/F8 predecessor exists anywhere in phase 2** — T2.1/T2.2/T2.3 are the tasks that assemble the manager options string carrying the bound ⇒ the engine-level `idle_in_transaction_session_timeout` can land **before** F8 removes the hold, which is precisely the C-4 hazard (the bound can kill M-R3's legitimate 13–15 s dwell). The council's C-4 REQUIRED CHANGE (a) — *"F8 becomes a hard **task-level predecessor** of the bound, **not prose**"* — is **not implemented**.

**WHY IT MATTERS** The preconditions are stated as *GATEs* in the contract but are **prose** in the graph a developer actually follows. Nothing reds if the ordering is violated.

**REQUIRED CHANGE** (a) Add a canonical-ladder row **`G0`** = "BASELINE.md exists, satisfies `phase0-plan.md:150-158`, is reviewed, and the two provisional literals have been replaced by Phase-0 measurements", with a non-vacuity assertion; add it to `phase3-plan.md:45-51` and `test-strategy.md:410-416`. (b) Set the "Depends on" cell of **T1.1, T1.4, T1.8, T1.11** to `G0`. (c) Set the "Depends on" cell of **T2.1, T2.2, T2.3** to `T1.0–T1.0g (F8) + G0`, and add a phase-2 gate cell: *"F8-a…F8-e are green AND the F8 commits are ancestors of this commit"*. (d) Express sequencing constraints 2/3/4 (`test-strategy.md:401-403`) as task dependencies plus a phase-2 gate cell asserting the F2 commit contains the `application_name` change, the AC amendments, the `statement_timeout` DROP and the asyncpg DSN **in one commit**.

#### DR-20 — the pins **hard-code the placeholder**, so Phase 0's measurement cannot be honoured (extends DR-5)

**WHERE** `test-strategy.md:292` (F2-c), `:294` (F2-e) · `phase2-plan.md:29`, `:54`, `:57`, `:97` · `final-implementation-spec.md:180`, `:184`.

**WHAT** `test-strategy.md:292` asserts *"`idle_in_transaction_session_timeout=60000` is in the manager options string"* and `:294` pins the full literal string ending `…idle_in_transaction_session_timeout=60000` (order-pinned). `phase2-plan.md:57` states `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS = 60_000` **flatly** (no provisional marker). Meanwhile spec §2 marks both literals **PROVISIONAL** and F2.3 says the clause is **omitted entirely** when unmeasurable.

**WHY IT MATTERS** Two mutually exclusive outcomes: the developer hard-codes 60000 and ships an **unmeasured** bound (the C-4 defect the fold claims closed), **or** Phase 0 measures a different value and F2-c/F2-e go RED. The pins make the placeholder permanent, so the measurement cannot be honoured. **No gate row asserts "the constant == 4 × measured P100 (or is disabled with the reason recorded)".**

**REQUIRED CHANGE** F2-c/F2-e must assert the **structure** — the clause is present, its value `== MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS`, the option order pinned — never the literal `60000`; the knob-matrix pin asserts the value against `daemon/constants.py`, not against a number. Add a ladder cell asserting the constant equals `4 × measured P100` from `BASELINE.md` §(a) **or** is disabled with the reason recorded in the gate report. Amend `phase2-plan.md:29/:54/:57/:97` and AC14 to the same template form (this supersedes DR-5's AC14-only scope).

#### DR-21 — the W-11 pin will **false-RED** on the architect's artifact, and **cannot see** the live legacy IDs (sharpens DR-3)

**WHERE** `architecture-recommendation.md:162` (**inside** the pin's scan scope) · `phase3-plan.md:81-86` (**outside** it) · scope defined at `plan-overview.md:179`.

**WHAT** (a) The pin's scope is *"decisions.md and **architecture-recommendation.md**"*, and the exemption covers *"the legacy→canonical mapping table above"* — a table that **does not exist in `architecture-recommendation.md` at all**. The A8 amendment at `:162` contains legacy IDs (`G0…G6`) in plain prose, with no table to exempt ⇒ the pin either REDs on the architect's own amendment (and the developer's only fix is to delete the amendment text) or the skip is widened until it doesn't — which is exactly the masking risk. (b) The **live** legacy IDs sit in `phase3-plan.md:81-86`'s **"Executed by"** column — the field the pin exists to police — and `phase3-plan.md` is **not** in the scan scope. The same table is also internally contradictory with its own ladder (`:48` maps legacy G3 → G2.1–G2.3, while `:84` assigns the F6 PG pack to `G3.1–G3.3` and `:86` assigns the F8 PG pins to `G3/G5`) and with `final-implementation-spec.md:388` (the declared authority), which maps legacy G3 → **G2.1–G2.3**. **The two tables the spec says "must agree" do not agree.**

**REQUIRED CHANGE** See DR-3: sentinel-delimit the exempt regions in all three artifacts; **write the scanner down** (file set, regex, sentinel handling) inside the pin's own artifact; replace the range notation `{G1.1…G3.5}` with an **explicit enumeration** (the range literally admits non-existent IDs such as `G1.2`, `G1.3`, `G2.2`, `G3.2`, `G3.3` — see the could-not-verify below); extend the scan scope to `phase3-plan.md`, `test-strategy.md` and `final-implementation-spec.md` (or split into two pins: canonical IDs everywhere + legacy IDs permitted only inside exempt blocks); re-key `phase3-plan.md:81-86` to canonical IDs; and reword `architecture-recommendation.md:162` to avoid literal legacy IDs (e.g. "not the retired seven-step ladder").

### 🟡 WARNING

- **DR-22 — the F5 store-census keep-green pin was silently dropped and its ID re-purposed.** Pre-fold `test-strategy.md:571` had `F5-c *(keep-green)*` — *"The **store census** query (`:351-363`, over `pg_class`/`pg_namespace`) still works — the wrong-table fix must not have touched it"*, mutation *"Break the census SQL ⇒ RED"*. The folded `test-strategy.md:312` re-purposes F5-c as the static twin of F5-b. Nothing pins the **separate** census query against a regression, and the drop has no disposition in FOLD-MAP. **Fix:** restore it as `F5-d`, add it to F5.3's Verify cell and to spec §5, and record the drop/restore in FOLD-MAP §3.
- **DR-23 — `EXPECTED` has no independent declared source for the pre-existing packs ⇒ "asserted" degrades to "recorded".** The rule is *"each pack declares its count as a module-level constant **inside the pack**"* (`test-strategy.md:223-242`), but the Class-4a/4b packs are **existing DO-NOT-TOUCH files** that declare no such constant and must not be modified — and the whole-tree legs must *"reconcile `base_collected`/`head_collected` against the sum of the declared `EXPECTED_COLLECTED_*`"*, again with no declaration for the pre-existing corpus. With no independent baseline, `EXPECTED` is filled in from the current run ⇒ the assertion is a **tautology** ⇒ the whole-tree leg is "recorded", not "asserted" — the exact C-1 failure mode. **Fix:** commit a baseline table (e.g. `.agents/shared/planning/db-pool-idle-txn/expected-collected.md`) recording the count per pack **measured at base `19fc11c8b`**, and have the fence read it; keep the pack-local constant for **new** packs only.
- **DR-24 — F6-c and F7-a have no default-collected static twin, and FOLD-MAP lists F6-c as one.** FOLD-MAP.md:122 enumerates "F7-d, F8-a, **F6-c/d** (original)" as twins — but **F6-c is itself a `postgres` pin** and **F6-d is a process gate** (`n/a`). The W-4 index-presence assertion is therefore invisible to a default run: if `ux_mission_records_anchor_open` is absent in the target DB, the boot mint raises **42P10** instead of the IntegrityError being repaired, and only a PG leg sees it. `test-strategy.md:353` likewise claims *"F7-a … has the F7-c self-pin as its static backstop"*, but F7-c's mutation deletes the assertion **and** decrements the constant — deleting the **residence assertion alone** leaves F7-c **GREEN**, so it is not a backstop for F7-a. **Fix:** add a default-collected static twin for F6-c (assert `OPEN_EPOCH_STATUS_SQL` at `models.py:209` is byte-identical to the predicate at `models.py:305-313` and to `20261025_140000_add_mission_open_epoch_unique_index.sql:67-69`) and for F7-a (assert `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S` exists in `daemon/constants.py` and that the guard's assertion references it); correct FOLD-MAP.md:122 and `test-strategy.md:353`.
- **DR-25 — F4.4's Verify cell names a pin that does not assert it.** `final-implementation-spec.md:117` says *"F4-a (source-level) + diff review"*, but F4-a's assertion list (`test-strategy.md:277`) does **not** include the docstring. The pins that do assert the `readiness.py:107-113` correction are **F4-e** (`final-implementation-spec.md:301`) and **F7-b-static fact (4)** (`test-strategy.md:349`) — neither is named. **Fix:** F4.4 Verify → `F4-e, F7-b-static (fact 4) + diff review`.
- **DR-26 — F8-a vs F8.4 is ambiguous: the sync twin's bridge "stays unchanged" *and* must be "moved out".** `final-implementation-spec.md:74` (F8.4) says the `asyncio.run_coroutine_threadsafe(…).result(timeout=8.0)` bridge *"stays unchanged"*; `:225` (§3.2) says it is *"moved out (F8.4)"*; `:325` (F8-a) bans `run_coroutine_threadsafe(...).result(...)` **inside the `with`-body**. If the bridge is moved out, F8-a stays green; if it stays in place, F8-a **reds**. The intended reading is almost certainly "the bridge's **call shape and 8.0 s timeout** stay unchanged; its **position** moves out of the held scope" — but it is not stated, and the two readings produce opposite outcomes. **Fix:** one sentence in F8.4 / §3.2 stating that the bridge's timeout and call shape are preserved and only its position changes, and that F8-a's `with`-body scope therefore requires the bridge to be **outside** the scope.
- **DR-27 — FOLD-MAP's closure record contains two **false verification claims**.** (a) DF-5 (`FOLD-MAP.md:141`) claims *"zero hits in plan-overview.md / research-pool-couplings.md / audit-method.md"* — there are hits in **two of the three** (`plan-overview.md:341`, `audit-method.md:409`); and `:114`'s *"Residual `uv run`/`/usr/bin/python3` references are **GONE from live artifacts**"* is false (they remain in `plan-overview.md`, `phase0-plan.md:71`, `phase3-plan.md:17`, `test-strategy.md:11/:144`, `decisions.md:52-58`). **The defect class is nonetheless closed** — every gate *command* uses the certified interpreter, which the worker verified by execution — so the **record** is false, not the fix. (b) DF-2 (`FOLD-MAP.md:138`) cites `sed -n '171p' plan-overview.md` but quotes the **172** line (`171p` is the G2 row). The corrected text is present at 172, so the defect is genuinely fixed — only the record is wrong. **Fix:** restate DF-5 as *"the interpreter in every gate COMMAND is the certified one; residual `/usr/bin/python3` strings remain as prose describing the defect at …"*; correct DF-2's command to `sed -n '172p'`.

### 🟢 ADVISORY

- **DR-28** — 12 pins use the `tests/unit/…` wildcard instead of a named pack file (`final-implementation-spec.md:305-330`), so W-6's "**ONE** pack inventory" is not an inventory; F3-c's mutation is test-side with no `file:line`; F4-c's "Where" cell is the literal string `readiness unit pack` — **no file at all**; F2.9's Verify cell is a process step with no pin. **Fix:** name the concrete pack files and edit sites; convert F4-c's Where cell to a path.

---

## 12. UPDATED VERDICT AND CONDITIONS

**VERDICT unchanged: 🟡 DELTA-CONDITIONS** — but the condition list grows from 5 to **21**, and two of my original conditions are **re-scoped** by the mechanical pass (DR-3 sharpened by DR-21; DR-5 absorbed into DR-20).

**The fold is still a good fold.** The C-1 interpreter defect **is** closed in every gate command (the worker executed the fenced form: `30 tests collected in 0.40s`; the §2.3 fence script is **mechanically sound** — it correctly caught `collected=0` on a marker-deselected run and `collected≠EXPECTED`); the docs-only claim is verified; the certified interpreter resolves `daemon` to the worktree when cwd = worktree. The residual is that the **fence** (not the interpreter) was not carried to Class 4a/4b, the unit rows, the whole-tree row, and the §2.3 script — and that the **binding contract** was not updated with the pins the plan added.

### MUST CLOSE before the implementation phase (Phase 1) may start — 8 items

| # | Condition | Artifacts to edit |
|---|---|---|
| 1 | **DR-1** — align D-35 with `test-strategy.md:281` (statement **cancelled**, `to_thread` returns, `checkedout()` → baseline; the backend row is **expected to persist**) | `decisions.md:124`; reword the T1.7 summary `phase1-plan.md:76` |
| 2 | **DR-2** — one canonical ladder: one ID per scope (`G2.5` Phase-1 PG, `G2.6` Phase-2 PG, …); F8-a/b/c/d/e and F6-b named in the rows that execute them; re-key `phase3-plan.md:78-90`; add the ladder **rows** to the contract §6; all three mapping tables agree | `test-strategy.md:410-416`; `plan-overview.md:170-179`; `final-implementation-spec.md:381-397`; `phase3-plan.md:45-52`, `:78-90` |
| 3 | **DR-3 + DR-21** — widen the W-11 pin to every artifact carrying gate/AC IDs; add the fix-ID / AC-ID / pack-inventory checks; **sentinel-delimit** the exempt regions in all three artifacts; **write the scanner down**; replace the `{G1.1…G3.5}` range with an explicit enumeration; reword `architecture-recommendation.md:162` | `plan-overview.md:179`; `final-implementation-spec.md:394-397`; `phase3-plan.md:92`; `architecture-recommendation.md:162` |
| 4 | **DR-16** — apply the full Class-1 fence (cd + 6-var scrub + `mktemp` HOME) to Class 4a/4b, the §2.3 fence script, the ladder's unit rows and the whole-tree row; correct the R18 disposition | `test-strategy.md:196-212`, `:223-242`; `phase3-plan.md:45-51`; `plan-overview.md:226`, `:360` |
| 5 | **DR-17** — add F7-b-static, F7-e-static, G-A, G-consistency to the contract's §5; change the 8 PG rows to `✅ + zero-skips` | `final-implementation-spec.md` §5 (`:284-331`) |
| 6 | **DR-18** — replace F6-e with a concrete F6-subject mutation; add F6-e to F6.1/F6.2's Verify; delete the TODO cell | `final-implementation-spec.md:309`; `test-strategy.md:322` |
| 7 | **DR-19** — add the canonical **`G0`** row (Phase 0 / BASELINE.md) to the ladder; set T1.1/T1.4/T1.8/T1.11 `Depends = G0`; set T2.1/T2.2/T2.3 `Depends = F8 + G0`; add the phase-2 gate cells for the F8→bound ordering and the one-commit F2 group | `phase1-plan.md` Depends column; `phase2-plan.md` Depends column + T2.12; `phase3-plan.md:45-51`; `test-strategy.md:410-416` |
| 8 | **DR-20** — F2-c/F2-e assert the **structure**, never the literal `60000`; AC14 + `phase2-plan.md:29/:54/:57/:97` to the template form; add a ladder cell asserting the constant == 4 × measured P100 (or disabled with the reason recorded) | `test-strategy.md:292`, `:294`; `phase2-plan.md:29`, `:54`, `:57`, `:97`; `final-implementation-spec.md:180` |

### MUST CLOSE before Phase 1 (same fold pass; Phase 0 may run in parallel)

9. **DR-6** (the retired ≈689 figure in seven places incl. SC10) · 10. **DR-7** (enablement mechanism) · 11. **DR-8** (honesty block states the backstop's status) · 12. **DR-9** (asyncpg DSN mechanism) · 13. **DR-10** (F8-c injection seam) · 14. **DR-11** (pre-F8 vs post-F8 sizing) · 15. **DR-22** (restore the F5 census keep-green pin as F5-d) · 16. **DR-23** (commit an independent `expected-collected.md` baseline) · 17. **DR-24** (static twins for F6-c and F7-a) · 18. **DR-25** (F4.4's Verify cell) · 19. **DR-26** (the F8-a vs F8.4 bridge sentence) · 20. **DR-27** (correct FOLD-MAP's two false verification rows) · 21. **DR-12**–**DR-15**, **DR-28** (advisories).

**Sequencing.** Phase 0 is measurement-only (scratch DB, no live contact) and can start now. But **DR-1, DR-2, DR-16, DR-19 and DR-20 should be folded before Phase 0 concludes**, because they change what the Phase-0 outputs must confirm: DR-1 fixes the F4-b observable, DR-2 fixes which gate runs the F8 measuring pin, DR-19 creates the `G0` row that consumes `BASELINE.md`, and DR-20 fixes what the pins accept as the applied bound.

### STILL UNVERIFIED (both workers + me)

- **Whether the canonical ladder has 14 entries or 9.** The canonical set is written as **ranges** (`G1.1–G1.4`, `G2.1–G2.3`, `G3.1–G3.4`, `{G1.1…G3.5}`); only G1.1, G1.4, G1.5, G2.1, G2.3, G2.4, G3.1, G3.4, G3.5 appear literally. **No artifact enumerates the ladder's members** ⇒ the W-11 pin's "exists in the canonical set" test has **no authoritative set to test against**, and IDs like `G1.2`, `G1.3`, `G2.2`, `G3.2`, `G3.3` may not exist. Folded into DR-3's required change (explicit enumeration).
- **Runtime behaviour of any pin** — no pin is written; the *forms* were verified, the assertions were not.
- **All PG-semantics claims** — documentation-only until `BASELINE.md` lands (the §9 P0-1…P0-10 list).
- **Whether the `env -u DAEMON_PORT` scrub is required** — it is in every fence but its purpose is stated nowhere; not a defect, flagged for the author.


---

# ADDENDUM 2 — the Phase-0 baseline landed mid-review (`43e577a5b`, `2fd4480bc`)

**The in-flight measurement arrived while this re-check was running.** `BASELINE.md` (37 KB) + the raw harness evidence (5 targets, 6610 lines) are now on the branch, merged into the plan. This is the evidence §9 was written to demand — and it **answers 6 of the 10 in-flight questions, empirically confirms four of my findings, and refutes a plan premise** (F4's). The verdict stays **DELTA-CONDITIONS**; the condition list grows again, and the new **#1** condition is a premise rewrite, not a text fix.

**Status of the §9 in-flight list, against the landed baseline:**

| P0 | Question | Answered? | Result |
|---|---|---|---|
| P0-1 | F8 flip condition | ✅ | **NOT MET ⇒ F8 stands.** Worst hold **8011 ms** (sync twin ceiling 8.0 s); async worst 1059.9 ms. The hold **equals** the awaited fetch latency (median hold 1029.9 ms vs await 1026.5 ms) ⇒ the flip condition must be restated as a **precondition** (**DR-30**) |
| P0-2/3 | measured max legitimate hold | ⚠️ partial | The hold is **coupled to the fetch, not to the DB**; the real `_fetch_subshape_a_content_async` latency is explicitly **NOT measured** (the harness models the python half) ⇒ the bound's derivation input is still open (**DR-11** stands) |
| P0-4 | F7 residence budget | ✅ | Healthy residence measured: median **0.0005 s**, p95 **0.0007 s**, max **0.0008 s** over 40 cycles; the invariant is now **measured** (**DR-35**) |
| P0-5 | per-pool `application_name` | ✅ | **Viable** — but the **conninfo parameter is the stronger channel** than the `-c options` form (**DR-31**); the shared-constant ordering hazard is **re-confirmed** by measurement |
| P0-6 | F7 invariant shape | ✅ | **The census-blindness claim is CONFIRMED** — a backend checked out before its first statement reports `state='idle'` with `xact_start=NULL` ⇒ an idle-in-txn census is **blind to the dominant occupancy shape** (my C-10, now empirical) |
| P0-7 | PG runtime semantics | ✅ | **`idle_in_transaction_session_timeout` fires as documented** — `after_wait: null` ("the row is GONE"), `BACKEND GONE (terminated by server)`, FATAL `IdleInTransactionSessionTimeout` ⇒ **this is the P0-7(i) answer and it CONFIRMS DR-1**: the "row gone from `pg_stat_activity`" observable is the signature of the **engine-level idle-in-txn timeout**, not of `statement_timeout` |
| P0-8 | 3-pool census vs the 15-slot cap | ⚠️ | **11 pool-creation sites**, not 3 ⇒ the attribution model is a **floor**, not the census scope (**DR-32**) |
| P0-9 | the BEGIN attribution | ✅ | Fingerprints measured per pool; the incident's "aborted connection handed back out" premise is **REFUTED** for the pool path (**DR-34**) |
| P0-10 | base occupancy/dwell numbers | ✅ | Occupancy across all three states + residence is the correct invariant; the lane's "does not close the symptom class" disclosure is **substantiated** |

## 13. ADDITIONAL FINDINGS FROM THE LANDED BASELINE

### 🔴 CRITICAL

#### DR-29 — the baseline **refutes F4's premise**: under pool starvation the abandoned probe holds **NO** connection

**WHERE** `BASELINE.md` T5b (the "Two findings that change F4's shape" block) and the consolidated table row **3** (*"rewrite F4's premise; a probe-scoped `statement_timeout` does not address the starvation shape"*) · the plan it refutes: `phase1-plan.md` D-04/T1.4 (F4 as the **primary**), `plan-overview.md` §4 (F4 "primary"), `decisions.md` D-35.

**WHAT — measured.** The baseline's T5b table:

| case | timeout | exceeded? | `checkedout()` after | PG state | notes |
|---|---|---|---|---|---|
| b1 `SELECT 1` | 0.5 s | no (5.1 ms) | 0 | `idle` | n/a |
| b2 `SELECT pg_sleep(5)` | 0.5 s | yes at 0.5011 s | **1** | **`active`** | t ≈ 4.52 s |
| b3 **pool starvation** (15 holders) | 0.5 s | yes at 0.5015 s | **15** (= the holder count, **not 16**) | **n/a — never got a connection** | after the holders release |

> *"Under pool starvation — the realistic incident shape — the abandoned probe thread holds **NO connection**. `checkedout()` after the timeout was **15**, exactly the holder count; the probe thread was a **pool WAITER**, not a holder. It is a stranded thread (bounded by `pool_timeout=30 s`), not a retained connection. **A probe-scoped `SET LOCAL statement_timeout` is therefore IRRELEVANT to this shape** — the probe never reached a statement."*

**WHY IT MATTERS** F4 is the plan's Phase-1 **primary** fix and the stated carrier of "the probe's server-side bound". The measurement shows that in the incident's own shape (starvation) the probe never acquires a connection, so there is nothing for a probe-scoped `SET LOCAL` to bound. When the probe *does* hold a connection (b2) the backend is **`active`, not `idle in transaction`** — a shape an idle-in-txn census cannot see (the baseline's own point 5). The measured hazard is therefore **(i)** a stranded waiter thread and **(ii)** an unbounded statement duration on a connection the probe holds, with no server-side bound because the manager engine carries neither timeout (baseline row 9: **CONFIRMED**). **F4 as designed does not address either.** Implementing F4 as specified would ship the lane's "primary" fix against a shape the incident does not have — the same *causal-completeness* failure the council rejected the original plan for, now measured.

**REQUIRED CHANGE** Before Phase 1: **rewrite F4's premise** per the baseline — (a) restate F4's outcome as *"the probe's statement duration is server-bounded **when the probe holds a connection**"* and drop the "abandoned probe worker cannot hold a connection open indefinitely" rationale, which the measurement refutes for the starvation shape; (b) decide F4's scope against the measured hazard — either add the **engine-level** `statement_timeout`/`idle_in_transaction_session_timeout` backstop that the baseline says is missing (which C-4 had dropped for the boot-DDL reason ⇒ then the `SET LOCAL statement_timeout = 0` carve-out at `manager.py:8760` becomes **mandatory**, per the leader's own OQ-3 ruling), or accept that F4 is defence-in-depth only and **demote it from "primary"** everywhere; (c) add a T5b-shaped pin leg: under **starvation** (not a held lock), assert the probe holds **no** connection and that `checkedout()` equals the holder count. The existing F4-b leg (a deliberately held lock) is a **synthetic** shape and does not cover the incident's shape.

#### DR-30 — the flip condition must be restated as a **precondition**; F8's decision rule must measure the **fetch**, not the hold

**WHERE** `BASELINE.md` consolidated rows **1** and **2** · the plan framing: `plan-overview.md:266` (OQ-6), `final-implementation-spec.md:33` (§0(a)), `decisions.md` D-40.

**WHAT — measured.** *"The flip condition must be restated as a **PRECONDITION**, not a constant: 'the M-R3 hold < 2 s **iff** the awaited content fetch < 2 s.' That is a measurable property of the fetch path … and it is **data-dependent** — an instance with a long history makes the fetch slower. The sync twin is unconditionally ≥ 8 s."* And: *"the hold is **not an independent cost**: it is *exactly* the awaited content-fetch latency (L5: hold median 1029.9 ms vs await median 1026.5 ms; L2: hold 52.4 ms vs await 47.7 ms)."* Consolidated row 2: *"measure the **fetch** latency, not the hold, to decide."*

**WHY IT MATTERS** The plan's OQ-6 states the flip condition as a **constant** ("if hold < 2 s …"), which the measurement shows is a **function of the fetch's data** — a threshold that is true for a short-history instance and false for a long-history one. A gate written against the constant form would decide the lane's mechanism on a number that is not a property of the code. (The good news: the condition is **NOT MET** at base ⇒ **F8 stands** ⇒ the lane's central mechanism is confirmed necessary.)

**REQUIRED CHANGE** Restate OQ-6 / §0(a) / D-40 to the precondition form — *"the M-R3 hold < 2 s **iff** the awaited content fetch < 2 s"* — and make the decision instrument the **fetch-latency** distribution (the async twin's content fetch, and the sync twin's 8 s bridge), not the hold. Record the measured base figures (async worst 1059.9 ms; sync twin ceiling 8.0 s) as the baseline the precondition is compared against, and note that the **real** fetch latency remains unmeasured (baseline: *"needs a booted daemon + a real checkpoint store"*), so the precondition's data-dependence is disclosed, not resolved.

### 🟡 WARNING

- **DR-31 — `application_name` belongs in the **conninfo parameter**, not the `-c options` form.** **Measured:** `options=-c application_name=ensemble-manager` **lands**; within one options string **last `-c` wins** (`alpha` then `beta` ⇒ `beta`); but *"the **conninfo parameter is the STRONGER channel**"* — `conninfo application_name=from-conninfo` **beats** `options=-c application_name=from-options`; `ALTER ROLE … SET application_name` lands; a startup-packet value beats the role default; asyncpg `server_settings={...}` and `?application_name=` both land. And the ordering hazard is **re-confirmed** by measurement: the repair engine, with the shared constant appended last, reports `ensemble-manager` ⇒ `repair_engine_reports_manager_name: true`. ⇒ D-30/F2.3's mechanism (a `-c` in the manager's own options string) is *viable* but is the **weaker** channel: a future edit that appends the shared constant could still override it, which is exactly the hazard the baseline measured. **Fix:** put `application_name` in the **conninfo parameter** of each engine's own URL (the manager engine's `URL.create(...)` query dict and the repair engine's own conninfo), and keep the shared constant free of any `application_name` — then the attribution is override-proof rather than merely last-wins-correct. Amend D-30 / F2.3 / F2.5 / F2.6 accordingly, and assert the channel in the F2-g/F2-h pins.
- **DR-32 — the attribution model is a **floor**, not the census scope: there are **11 pool-creation sites** in `daemon/`.** Baseline consolidated row 6: *"3 pools → **11** pool-creation sites; the incident's 18 backends are explainable by multi-pool occupancy → widen the census scope."* The three named engines (D-36) are the ones that can hold idle-in-txn, but the **census** must cover every site that can hold a connection. **Fix:** widen the F7 census scope to every pool-creation site (the baseline's T3(a) static scan already enumerates them), and state in D-36 that the three named `application_name`s are the **daemon-internal** subset, not the census boundary.
- **DR-33 — OQ-1 is **CLOSED**, and its measured mechanism is that the boot path's re-resolve probe is **skipped**, not mis-keyed.** Baseline T5c + row 8: *"the re-resolve probe is **skipped** and the error **re-raises**"*, because the boot mint has `founding_message_id=None`; the trigger is *"a concurrent double-mint for the same anchor"* — and the baseline's own c4 (8-way concurrent, no barrier) produced **7 real `UniqueViolation`s** on `ux_mission_records_anchor_open` *"the race fires NATURALLY; no barrier needed"*, while c4b (probe→insert window forced to overlap) produced **5 `IntegrityError`s**. ⇒ D-09 *"the boot path's re-resolve is keyed on the index's own predicate"* addresses a **probe that does not run** on the boot path. The **upsert** (D-08) is the right fix and is idempotent under any trigger — but the disclosure and the re-resolve wiring must match the measured mechanism. **Fix:** amend D-09 to state the **skip** (and where it happens) as well as the key, and record the measured trigger (concurrent double-mint, 7/8 natural) in the premise ledger; confirm F6.1's upsert is the operative fix and that the anchor-scoped re-resolve is reached on the `rowcount == 0` path.
- **DR-34 — F5's priority must be raised.** Baseline row 4: the incident's *"aborted connection handed back out"* premise is **REFUTED** for the pool path — `reset_on_return='rollback'` cleans it, and the journal quote is *"a **same-connection cascade** in wedge-capture"* ⇒ *"**raise F5's priority** (the `tasks`→`task` rename is the cascade's root)."* F5 is currently a **Phase-2** item. **Fix:** move F5 (or at least its PG correctness pin) into Phase 1, or record the deferral and its rationale explicitly; the cascade's root is the wrong table name, and the baseline has now measured that.
- **DR-35 — the measured F7 invariant supersedes D-38's provisional one.** The baseline's **MEASURED F7 invariant** (T2): occupancy across **all three** PG states (`idle` with `xact_start IS NULL` ⇒ checked-out-but-no-statement; `idle in transaction`; **`active`**) **plus** residence (`state_change − xact_start`) against the hold budget of the operation that opened it. *"A guard that counts only `idle in transaction` is **vacuous against two of the three shapes** and is therefore not a valid F7."* The healthy residence is measured (median 0.0005 s, p95 0.0007 s, max 0.0008 s over 40 cycles) ⇒ the provisional 2.0 s budget is **orders of magnitude** above the healthy value, and the budget's real job is to catch a hold that exceeds the operation's own budget. **Fix:** update D-38 to the measured three-state + residence form; replace the 2.0 s placeholder with the measured healthy-residence band plus the hold budget; and make F7-a assert the three states, not just `idle in transaction` (this also closes the residual half of **DR-4**).

### 🟢 ADVISORY

- **DR-36** — the baseline's own disclosed limits should be carried into the plan's honesty blocks: the real `_fetch_subshape_a_content_async` latency was **not measured** (modelled), the real `ChildReportsService._get_last_assistant_message` python-side cost was **not measured**, the incident-window mint overlap is **inferred**, and the `pool_timeout` stall was measured at 3 s (not the 30 s default) to fit the pack budget.

## 14. FINAL VERDICT (superseding §1 and §12)

# 🟡 DELTA-CONDITIONS — 24 conditions

**The fold is real work** (9 of 12 council criticals genuinely closed; 0 relabelings; all six deviations improvements) **and the Phase-0 baseline is high-quality, measured evidence** that confirms four of my findings empirically (the census blindness, the shared-DSN collision, the F8 necessity, the `idle_in_transaction_session_timeout` signature) and settles OQ-1. **But the baseline has now measured a premise the plan rests on — F4's — to be wrong for the incident's shape**, and the preconditions remain partly prose.

**Must close before Phase 1 (11 items):** DR-1 · DR-2 · DR-3 (+DR-21) · DR-16 · DR-17 · DR-18 · DR-19 · DR-20 · **DR-29 (new — F4's premise)** · **DR-30 (new — the flip condition's form)** · DR-25.

**Must close in the same fold pass (13 items):** DR-6 · DR-7 · DR-8 · DR-9 · DR-10 · DR-11 · DR-12–DR-15 · DR-22 · DR-23 · DR-24 · DR-26 · DR-27 · **DR-31 · DR-32 · DR-33 · DR-34 · DR-35** (new) · DR-28.

**Sequencing.** **Phase 0 is DONE** — its output exists and its evidence is on the branch. The next gate is the **plan fold** of the 24 conditions above; **Phase 1 may start only after the 11 must-close items are folded**, and DR-29/DR-30 must be folded **first** because they change what Phase 1 builds.

**What the baseline settles about the council's own findings:** C-7's closure (DR-1) is now **empirically grounded** — the "row gone from `pg_stat_activity`" observable is the signature of `idle_in_transaction_session_timeout` (measured: `BACKEND GONE`, FATAL `IdleInTransactionSessionTimeout`), which F4 does not install. C-10's closure (DR-4) is **empirically confirmed** — the census-blindness claim is measured, and a census over `idle in transaction` alone is vacuous against two of the three leak shapes. C-8's closure (D-36) is **empirically confirmed** and **extended** (DR-32).

