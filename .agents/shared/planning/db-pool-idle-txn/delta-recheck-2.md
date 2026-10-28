# Delta Re-Check — ROUND 2 (final plan gate before the approver)

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`). **Worktree:** `/home/nea/ensemble-src-wt-dbpool-idletxn`.
- **HEAD under review:** `bb3c0a2fc9b685f86dd2a319711c442230d8780e` (the architect's Δ1–Δ8 mini-fold). Base `19fc11c8b`. **Code-tree diff vs base: EMPTY** (re-verified).
- **Lineage since round 1:** `8628cc15a` (plan re-based on Phase-0 measured values) → `4766c92c8` (architect delta-check) → `bb3c0a2fc` (Δ1–Δ8 mini-fold). **Mini-fold diff vs `8628cc15a`: +245/−23 across 10 files, all under `.agents/`** — 136 of those insertions are the architect's own new `architecture-delta.md`; the **amendment edits to existing files are +109/−23 across 9 files**, exactly as briefed. **Docs-only confirmed; nothing new crept in.**
- **Mode:** closure verification. The round-1 findings (`delta-recheck.md`, DR-1…DR-36) and the architect's Δ1–Δ8 are the subject. Load-bearing items were **verified by me directly**, not taken from FOLD-MAP's ST-1.
- **Date:** 2026-10-28.

---

## 1. VERDICT

# ✅ DELTA-CLEAR

**The plan is READY FOR THE APPROVER AND IMPLEMENTATION.**

All 24 round-1 conditions are closed with real (not relabeling) resolutions; the architect's Δ1–Δ8 are landed; C1–C4 non-interference holds after every fold; the release posture and the routed scope are intact and truthful. **Δ1 — the one item that could not be accepted on paper — was proven by me empirically: the fence now FAILS CLOSED, and the defect it fixes was reproduced exactly.**

Three residuals are **disclosed and non-blocking** (§5) — each is a measured limit or a leader-ruled routing, not an open defect in the plan text.

---

## 2. Δ1 — the fence FAILS CLOSED (empirically proven)

**The mechanism, walked on paper.** The round-1 fence compared with `[ "$COLLECTED" -ne "$EXPECTED" ]` inside an `if`. A `TBD` cell makes `test` return **2** ("integer expression expected") — and because the test is an `if`-condition, it is **exempt from `set -e`**; the `if` reads non-zero as **FALSE**, the FATAL branch is skipped, and (with `COLLECTED > 0`) execution falls through to `echo COLLECTED-OK` — **a vacuous pass**. The Δ1 fix integer-validates `EXPECTED` in a `case` **before** any comparison: any string containing a non-digit (including `TBD` and the empty string) exits 1 with a labelled FATAL.

**The four mirror sites carry it** — `test-strategy.md:309` (the script) and `:330` (the rule), `final-implementation-spec.md:351` (§4.3, the independent-declaration rule) and `:502` (the gate-form rule). Verified by grep at all four.

**Empirical run — I executed five cases myself** (certified interpreter `/home/nea/ensemble-src/.venv/bin/python`, from the lane worktree with the proper `cd`; the script itself carries the full Class-1 fence — `cd` + 6-variable per-variable scrub + `HOME="$(mktemp -d)"`):

| # | Case | Result | Expected |
|---|---|---|---|
| 1 | **Fixed script, `EXPECTED=TBD`** | `FATAL: EXPECTED is not an integer (TBD cell?) …` — **exit 1** | FATAL ✅ |
| 2 | **Guard-STRIPPED script, `EXPECTED=TBD`** | `COLLECTED-OK count=30 expected=TBD` — **exit 0**, with `[: TBD: integer expression expected` on stderr (swallowed by the `if`) | **the vacuous pass, reproduced exactly** ✅ |
| 3 | Fixed, correct count (30) | `COLLECTED-OK count=30 expected=30` — exit 0 | pass ✅ |
| 4 | Fixed, wrong count (29) | `FATAL: collected=30 expected=29` — exit 1 | FATAL ✅ |
| 5 | Fixed, marker-deselected path (`-m postgres` on a default-marked pack ⇒ 0 collected) | `FATAL: collected=0 expected=30` — exit 1 | FATAL ✅ |

⇒ **Δ1 is proven fail-closed, and case 2 proves the pre-fix defect was real, not theoretical.** The planner's empirical trace is accurate. The fence now also carries the full Class-1 fence in the script body — **DR-16 closed in the same artifact**.

---

## 3. MY 24 CONDITIONS — load-bearing spot-checks (done by me)

| DR | Claim | My verification |
|---|---|---|
| **DR-16** | Class-4a/4b now carry the full fence | ✅ The fence script at `test-strategy.md:300-330` carries `cd /home/nea/ensemble-src-wt-dbpool-idletxn` + the 6-variable scrub + `HOME="$(mktemp -d)"` + the certified interpreter; the contract's gate form (`:495` region) states "**This fence applies to, without exception:** the Class-1 unit command · the Class-2 PG command · the Class-3 integration command · **Class 4a and Class 4b**". Verified in both artifacts. |
| **DR-17 / Δ8** | Inventory reconciled; authoritative = contract §5; 46 pins | ✅ **I ran the set-diff**: contract §5 = **46** rows; `test-strategy.md` §3 mirror = 47 rows whose **only** difference is a `TOTAL` summary row ⇒ **46/46 set-equal**. The contract states "**This table is the authority** … the plan's §3 must match it 1:1". The process-gate row (F6-d) is present and marked as such. |
| **DR-19** | Task-graph predecessors are hard rows | ✅ `phase1-plan.md` Depends column: **T1.1 / T1.4 / T1.8 / T1.11 = `G0` (SATISFIED)**; `phase2-plan.md`: **T2.1 / T2.2 / T2.3 = `T1.0–T1.0g (F8) + G0`**; `T2.12` carries **(i) the F8-ancestor cell** ("F8-a…F8-e green AND the F8 commits are ancestors of this commit") and **(j) the ONE-COMMIT cell** (the F2 commit contains the `application_name` change, the AC amendments, the `statement_timeout` DROP and the asyncpg DSN **in one commit**). The `G0` ladder row exists in §6.2a. |
| **DR-20** | No hard-coded `60000` where the measurement must be honoured | ✅ `test-strategy.md:374/:376`: F2-c asserts the **clause is present**, F2-e asserts the value **equals `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS`**; `:558` states the rule ("STRUCTURE, never the literal `60000`"); `phase2-plan.md:58` — the knob table is a **TEMPLATE**, "UNCHANGED while the backstop is DISABLED" (D-47); **D-49 routes `application_name` through the conninfo channel**. My grep for surviving literals returns only placeholder/repair-engine/prose-describing-the-rule hits. |
| **DR-21** | An authoritative enumerated ladder to test against | ✅ Contract §6.4.1 declares the **8-artifact scan set** ("the two-artifact scope the fold had narrowed to is **withdrawn**"); §6.4.2 defines **four checks**; §6.2a enumerates the members — **G1.1–G1.5, G2.1–G2.6, G3.1–G3.5 = 16, plus `G0` = 17**. The scanner is written down in full. |
| **DR-29** | F4 rewritten; F8 primary; F5 raised | ✅ **D-04 REWRITTEN**: F4-A = an **admission pre-check** before `engine.connect()` (read `checkedout()`, compare against the lane-pinned capacity constant `5+10=15`, at capacity raise `TimeoutError` **synchronously** ⇒ no blocking checkout, no stranded thread); **F4-B DEMOTED** to "defence-in-depth for the ONLY shape it can address" (D-05); **F4-C / F4-f = the missing starvation-shaped PG leg** ("under 15 holders: the probe holds NO connection; `checkedout()` equals the holder count" — `test-strategy.md:112/:364`, `phase1-plan.md:87`); **F5 RAISED to Phase 1** (D-52, with the measured rationale at `phase1-plan.md:102`). F8 is the Phase-1 mechanism item. |
| **DR-30** | Flip condition as a precondition | ✅ `decisions.md:534`: *"restated as a **PRECONDITION**, not a constant: 'the M-R3 hold < 2 s **iff** the awaited content fetch < 2 s'"*; OQ-6 restated at `plan-overview.md:319` with **answer: NOT MET** ⇒ F8 stands. |
| **DR-33** | OQ-1 closed; D-09 retracted/amended | ✅ `decisions.md:341`: **D-09 (AMENDED)** — *"the boot path's re-resolve probe is actually **SKIPPED** … D-09's mis-keyed-probe claim is **RETRACTED**. The measured trigger is a **concurrent double-mint**"*; **D-51** carries the F6 fix against the concurrent double-mint. |
| **DR-35** | Three-state + residence; 2.0 s withdrawn | ✅ **D-48** — three-state occupancy (`idle` w/ `xact_start IS NULL` · `idle in transaction` · `active`) + residence; **and the fold improved on my condition**: the metric is **`xact_age_s = now() − xact_start`**, because `state_change − xact_start` is **FROZEN at the last statement's completion** ⇒ a leaked backend shows the same sub-ms value as a healthy one ⇒ my D-38 form's metric could never fire on the leak. The 2.0 s placeholder is **displaced**. |

**The remaining 15 conditions** were verified at their FOLD-MAP anchors (`FM-DR-*` rows, each with a re-run command) and, where I sampled them (DR-1's D-35 amendment marker, DR-2's one-id-per-scope ladder, DR-23's `expected-collected.md` — which exists and is cited at contract `:351`), they hold.

---

## 4. The architect's Δ1–Δ8 fold — verified

| Δ | Claim | My verification |
|---|---|---|
| **Δ1** | Fence fails closed | ✅ **Empirically proven** (§2) |
| **Δ2** | asyncpg hold → **R-9 ROUTED** (leader ruling), attribution in-lane via D-49's dedicated DSN; D-47-style enablement gate | ✅ **D-58** ("asyncpg checkpoint-adapter idle-in-txn hold: ROUTED, attribution in-lane (Δ2)") + the **R-9** routed row at `plan-overview.md:371`. Applied, not relitigated — per the leader ruling. |
| **Δ3** | Budget procedure pinned: measure-then-set at pin-writing, tester-executed, provenance post-F8, **NOT-DERIVABLE-never-guessed**, call-site→constant table in the F7 pack header, F7-a-static third mutation leg | ✅ `test-strategy.md:427`: *"NOT-DERIVABLE, never guessed — a site whose budget cannot be measured records `NOT-DERIVABLE` **+ the exclusion in the pack header** (DR-11 made procedural)"*. |
| **Δ4** | Erratum byte-identical in both copies, sha-verified | ✅ `architecture-delta.md:101` carries the erratum text; the mini-fold diff shows **+2 lines in each of `BASELINE.md` and the canonical RESULTS file** — one erratum line each, consistent. |
| **Δ5** | F4-f scoped to a quiescent 15-holder setup; the D-57 14→15 race disclosed | ✅ `test-strategy.md:364` / `phase1-plan.md:87`: "under **15 holders**"; the pre-check-race residual is disclosed at `phase1-plan.md:44`. |
| **Δ6** | Capacity-constant home pinned: module-level in `readiness.py` (D-59) | ✅ **D-59** + `contract:121`: "*The capacity constant's home is **PINNED** … module-level in `daemon/services/readiness.py`*" — and the Phase-1 permitted set is amended to cover it (`contract:609`: the two probe bodies "cover **both** F4-A … and F4-B"). |
| **Δ7** | F9.1 numbered change: `queues.py:652-654` **comment-only**, no behavior pin, report-back-if-differs, release-composition disclosure (leader ruling) | ✅ `contract:198` — F9.1 targets **the comment only**; R-1 (the event-loop sync-DB defect) **remains routed** at `plan-overview.md:363` and in the contract's "Explicitly OUT of this lane" list — **the F9.1 comment edit does NOT close R-1**, exactly as briefed. |
| **Δ8** | Inventory reconciliation; 46 pins; 26 pack files + 1 process-gate row; the "27" was a row count; duplicate 28≡9 marked in-row | ✅ **I ran the set-diff**: 46/46 set-equal, the only mirror delta a `TOTAL` row. The contract is declared the authority; `test-strategy.md` §3 is a 1:1 mirror. |

---

## 5. C1–C4 non-interference after all folds (cheap re-verify)

| Coupling | Verdict | Evidence |
|---|---|---|
| **C1** | ✅ **PASS** | Nothing touches `_evaluate_leak`, the grace arming, the detail schema or the P1/P2 sampling surfaces (DO-NOT-TOUCH rows intact at `plan-overview.md`; `lib.sh` DO NOT TOUCH). **The F4-A shape is C1-clean:** its synchronous `TimeoutError` lands on `FAILURE_CLASS_PROBE_TIMEOUT` at `readiness.py:1284-1285` — **the same class the client-side timeout already produced** (`phase1-plan.md:95`: "the pair and the grace-eligibility are unchanged"). Under starvation the probe still **degrades** (it refuses) — it never reports green while the pool is poisoned, in either ordering. |
| **C2** | ✅ **PASS** | `scripts/upgrade/lib.sh` DO NOT TOUCH (`plan-overview.md:250`); never-green unconditional; no conditionality near any rollback path. |
| **C3** | ✅ **PASS** | Every gate runs in the runner-executed form with an asserted collected count, and the fence is **fail-closed** (§2), integer-validating `EXPECTED`, failing on `0` collected and on `skipped > 0` for PG gates, with the counts sourced from the **independent** `expected-collected.md` (DR-23). |
| **C4** | ✅ **PASS** | `pool_size=5 + max_overflow=10` unchanged; **D-17 intact** ("`PG_SESSION_CONNECT_ARGS` is NOT extended"); the P2 saver pool DO NOT TOUCH; the soak lane's sizing untouched. |

## 6. Release posture and scope

- **Stage-only** (§15.1), **no promote / no daemon restart / no `.env` touch / no prod-DB contact** ✅
- **`v0.27.12` PROVISIONAL** with **W9 live-census re-derivation at stage time** (`plan-overview.md:432`) ✅
- **merge-latest-if-moved** in 5 steps, **the L1 exit-78 refusal honoured** (`:436`: "that refusal is a feature and **must not be overridden**") ✅
- **`rollback_safe` adjudicated** under the standing rule (§15.4) ✅
- **Routed rows R-1…R-9 all present** with evidence and owners (`plan-overview.md:363-371`) — including **R-9** (the asyncpg hold, Δ2's routing) ✅
- **`plan-overview.md:16`: "This lane does NOT close the incident's symptom class"** — still present, and now **substantiated by measurement** (the Phase-0 census + dwell data) ✅
- **F9.1 is comment-only** and R-1 is not closed by it ✅

## 7. Residuals — disclosed, non-blocking

1. **The F4-A pre-check-race residual** (D-57): a checkout that loses the race between the pre-check's read and `engine.connect()`'s acquire can still strand the thread up to 30 s. Disclosed in-plan with the rationale (a per-probe acquisition timeout is not expressible via the public SQLAlchemy API; lowering the engine knob would convert ordinary contention into `TimeoutError` for every caller).
2. **The idle-in-txn backstop ships DISABLED** (D-47) pending a **real** measurement: the baseline's max-legitimate-hold figure is model-derived, and the **real** `_fetch_subshape_a_content_async` latency remains unmeasured (the baseline's own disclosure). This is the leader-ruled safety-precedence narrowing — defined, not applied, enablement gated. **The enablement condition is: a real fetch-latency measurement.**
3. **Two FOLD-MAP NOTES** (recorded, non-blocking): CF-1 — 4 prose lines in `plan-overview.md` still name legacy ids as a *set* or a DR-condition *title* (not gate references; the W-11 pin's token rule does not match them); CF-7 — contract §6.4.3's parenthetical about "exactly one artifact carries the mapping" is stale (two artifacts now carry a delimited mapping) — **the rule is satisfied and the pin is green**.

None of these blocks the approver or the implementation; all three are carried in the plan with their owners.

## 8. Corrections accepted

- **FM-RC-4 is correct and I accept it.** My round-1 `delta-recheck.md` §13 DR-29 table carried the **superseded** harness run's figures (b1 = 5.1 ms, b2 = 0.5011 s, b3 = 0.5015 s). The committed RAW (`t5_fix_sites.out`) reads **`timeout_at_s` 0.0058 / 0.5017 / 0.5012** — the round-2 artifacts carry the RAW values. My figures were stale; the correction is right.
- **D-48 improved on my DR-35.** My condition specified `now() − state_change`; the fold's measurement showed that metric is **frozen** at the last statement's completion and therefore could never fire on a leak — `xact_age_s = now() − xact_start` is the correct metric. The fold went further than my condition required, correctly.
- **D-56 settles P0-9**: the `BEGIN` fingerprint is **measured-unique to Site A** — vindicating the plan's original §3.3 attribution and my round-1 advisory A-1 (that the architect had over-corrected).

## 9. What I did not verify

- **The independent inventory worker** (`661caa7e`) was still running when this report was committed; I performed the pin-inventory set-equality myself (§3, DR-17/Δ8: **46/46**) and sampled the pack-file rows. If the worker surfaces anything beyond §3–§6, it will be appended in a follow-up commit — nothing in this verdict rests on it.
- **Runtime behaviour of any pin** — none is written; the *forms* and *fences* were verified, the assertions were not. That is the implementation's job and the tester's gate.
- **The 26-pack-file count** was taken from the contract's own table (the "27" was a row count, per the FOLD-MAP's explanation — consistent with what I see); I did not independently enumerate the pack files.
- **The real fetch latency** (the D-47 enablement condition) is **modelled, not measured** — the baseline's own disclosure, and the reason the backstop ships disabled.
- No database was contacted by this re-check; every PG-semantics statement above is a citation of the committed Phase-0 RAW evidence, not a new measurement.

---

*Produced by the reviewer (dispatcher/controller). Round-2 closure verification: the Δ1 fence was walked on paper and executed in five cases (including the guard-stripped defect reproduction); the load-bearing DR conditions were verified by direct artifact inspection and set-diff; C1–C4 were re-verified cheaply after all folds. Read-only: no `daemon/`, `tests/`, `scripts/` or payload-tree file was modified; the code-tree diff vs `19fc11c8b` is empty at HEAD.*
