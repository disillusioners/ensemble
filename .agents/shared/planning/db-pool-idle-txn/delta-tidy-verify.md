# Tidier-Cycle Delta Verification + Deferred Adjudications — the last gate before the tester

**VERDICT: ✅ TESTER-GATE-READY** — 0 conditions. All seven delta-verification items confirmed **personally by the reviewer** (code read + census executed + 3 pack re-runs). All 10 deferred items adjudicated: **0 FIX-NOW · 2 DEFER-POST-TESTER · 8 NO-ACTION-WITH-REASON**. **No ruling changes the pin inventory or any `expected-collected.md` row** — the only count change (F6 unit 7→8) was already recorded by the tidier as FOLD-MAP **NOTE-4** and is the T-M7 propagation test, which I verified.

- **Range verified:** `1641f7189..32a653c66` (7 fix commits + the tidier's docs commit), 25 files +931/−139. HEAD `32a653c66`, base `19fc11c8b`.
- **Prior-gate warnings W-1/W-2 confirmed landed** (see §1.8) — the code-review council's two behavior-real warnings are closed in the tree.
- **Date:** 2026-10-29.

---

## 1. Delta verification (JOB 1 — personal, scoped)

### 1.1 (a) T-H1 — the ladder fails closed on filter mismatch ✅
`tests/packs/dbpool_gate_ladder.sh`: the matched-row counter is live — `MATCHED=0` at `:78`, incremented at `:149`/`:217`, and the zero-match guard at `:295-297` prints `LADDER: FATAL — no gate row matched '<filter>' (typo'd/renamed/not-yet-added id?); ZERO gates were executed` → **exit 1**; the OK line now reports `rows matched=N` (`:303`). The instrument that every downstream gate trusts **fails closed** on an unmatched `ROW_FILTER`. (The mutation leg — deleting the counter — is the tidier's stated RED proof; the counter's placement at both increment sites and the `-eq 0` guard make the deletion provably redden the zero-match branch.)

### 1.2 (b) T-H2 — the census now measures B and F; the triangle reconciles ✅
`scripts/audit/borrow_census.py`: family **B** has its own detector (`SQLModelSession` split out of A — `_is_family_b` at `:69-76`) and family **F** has one (`psycopg.connect` / `raw_connection()` / `getconn` / `putconn` — `:21-22`). **I executed the census myself (read-only):**

```
A  with Session(...) / WriteGuardSession(Session(...)): 438
B  with SQLModelSession(...): 175
C  non-with session assignment (= Session(...)): 4
D  with engine.connect() / engine.begin(): 148
E  async with pool.acquire(): 14
F  raw psycopg / raw_connection / getconn / putconn: 1
TOTAL borrow sites (A+B+C+D+E+F as summed above): 780
```

— **exactly the claimed A=438 B=175 C=4 D=148 E=14 F=1**, and the TOTAL label now names the families it sums. The evidence triangle (instrument ↔ `audit-findings.md` addendum ↔ the `census_families` pin re-baseline) reconciles **without overclaim**: the base-vs-HEAD delta is 0 for the re-measured families, and the addendum records the matcher split as an instrument change, not a code change.

### 1.3 (c) T-H3 — the F4-b persistence leg is implemented; `EXPECTED_COLLECTED = 2` is true ✅
`tests/integration/test_dbpool_probe_bound_pg.py`: `EXPECTED_COLLECTED = 2` at `:62` with the docstring counting the persistence leg as the second test (`:23`); `test_the_backend_row_persists_in_the_idle_state` at `:203` asserts the **corrected D-35 observable** — the backend row **persists** in `state='idle'` (`:185`: "The backend row is EXPECTED TO PERSIST (state='idle')"), bound to the probe's own census via a **distinctive `_PROBE_APPLICATION_NAME`** (`:74`, `connect_args` at `:224`, the census predicate at `:262`). The declared count is now satisfiable.

### 1.4 (d) T-M6/M7 — the dedicated conflict type; non-conflict IntegrityError propagates ✅
`daemon/repositories/mission_record/repository.py`: `class MissionEpochConflictError(IntegrityError)` at `:45`; raised at `:617` (the conflict shape, when the re-resolve missed); a **plain `IntegrityError`** at `:629` for the non-conflict shape. The service catches **only** `MissionEpochConflictError` (`mission_record_service.py:557`) with the in-code rationale ("`except IntegrityError` ALSO caught NON-conflict shapes"). ⇒ **Only genuine unique-violations re-resolve; everything else propagates.** The mutation leg (broad-catch revert ⇒ DID-NOT-RAISE RED) is the new `TestTm7NonConflictIntegrityErrorPropagates` test — the NOTE-4 count change (7→8).

### 1.5 (e) T-M12 — one enablement path; the boot log cannot be made to lie ✅
`MANAGER_ENGINE_IDLE_IN_TXN_BACKSTOP_ENABLED = False` lives **once**, at `factory.py:277`; the same flag gates **both** the options template (`:283`) **and** the boot log (`:383`) — so flipping it adds the clause **and** silences the DISABLED line in the same edit. `constants.py:287` is a comment quoting the instruction, not a second live constant. **One flag, one site, one consequence.**

### 1.6 (f) Counts — re-run by me where cheap ✅
- **My runs** (certified interpreter, CWD = worktree): `consistency_pin + mission_mint + census_families` → **19 passed, 1 xfailed** (the same W-11 **AC-id cross-artifact canary**, `strict=True` — the identity is stable and the pack/plan/independent artifact agree on "exactly 1 xfail"); the full `-k dbpool` unit sweep → **102 passed, 1 xfailed, 0 failed**. **0 failures anywhere I could reach.**
- **Dev-claimed, not re-run by me** (need a scratch PG): PG 5/5 · integration 19/19 · F8 PG 7/7 (post W-1/W-2) · churn+guard 10/10. Consistent with everything I did run; the tester gate owns them.

### 1.7 (g) Scope — no fix-unrelated hunks; NOTE-4 present; DO-NOT-TOUCH clean ✅
- `daemon/services/readiness.py` in the range: **two comment-only hunks**, both inside the permitted probe bodies (a citation rewording of the `SET LOCAL` house-idiom comment). `daemon/persistence.py`: **one comment-only hunk** (the asyncpg DSN explanation — now more honest: the attribution is 100% `server_settings`, the alias is for reader clarity, the DSN value is byte-identical to the shared string). **No code delta in either.**
- **DO-NOT-TOUCH anchors re-verified identical after the tidier cycle**: `_evaluate_leak`, `_guarded`, `observe` — all IDENTICAL (anchored hashes); the shared `conn_string` at `persistence.py:234` unchanged.
- **FOLD-MAP NOTE-4 present** at `:297` — the F6 unit pack's collected-count change 7→8, recorded with the named test.
- The other daemon files in the range (`manager.py`, `constants.py`, `mission_record/*`, `tools/instance.py`) carry the T-fixes mapped in the tidier report; nothing fix-unrelated surfaced in my hunk sampling.

### 1.8 Prior-gate warnings W-1 and W-2 — confirmed landed ✅
- **W-1** (degenerate-path content delta): `manager.py:10901-10910` now tracks `child_row_missing` and computes `fetch_agent_id: str | None = None if child_row_missing else (child_agent_id or "agent")`, passed at `:10968` (and the async twin at `:11709+`); the helper signature is `child_agent_id: str | None`. **Exactly the required change** — row-missing ⇒ no fetch (base parity), `"agent"` only for row-present/NULL.
- **W-2** (split `_write_guard`): `with self._write_guard:` appears **once per twin** (`:10873` sync, `:11681` async) — one guard around the whole body, sessions still split. **Exactly the required change.**

---

## 2. The 10 deferred adjudications (JOB 2)

| # | Item | Ruling | Reason (one line) |
|---|---|---|---|
| **1** | 57014 → `hard_red` classification semantics | **NO-ACTION-WITH-REASON** | A server-cancelled statement is a statement-level **anomaly**, not a load shape — the grace-eligible set (`probe_timeout`/`pool_busy`/`queue_stale`/`read_timeout`) is deliberately load-shaped, and `hard_red` is the correct fail-closed classification. The 2× bound (D-05) makes the arm **unreachable in the designed path** (the client `wait_for` fires first); reaching it at all means the client budget was bypassed, which *should* be hard-red. Optionally note this in the pin's docstring; no code change. |
| **2** | T-M7 behavioral reachability | **NO-ACTION-WITH-REASON** — confirmed fixed | Verified personally (§1.4): the raise sites are now distinct (`:617` conflict → the dedicated type → re-resolve; `:629` non-conflict → plain `IntegrityError` → propagates), the service catch is narrow with the rationale in-code, and the propagation test exists. **No residual**: a non-conflict IntegrityError cannot reach the anchor-keyed re-resolve. |
| **3** | Carrier-check id P0-vs-P2 consistency | **NO-ACTION-WITH-REASON** | `report_message_id` mutates exactly once (the marker-first backfill, None→id); that case leaves the P0 flag **None**, which the strict-False consumer **defers** — the already-adjudicated Route-A disposition (`review.dbpool.a1`). An id1→id2 change would require an UPDATE nothing performs. A P2 re-derivation would add a second manager-engine read on the hot path for **no reachable case**. |
| **4** | Upsert NULL-filtering semantics | **NO-ACTION-WITH-REASON** | PG treats NULLs as **distinct** under the partial unique index (`NULLS DISTINCT` default), so a NULL-`anchor_instance_id` row is never arbitrated by the `ON CONFLICT` — identical to base's ORM insert — and server defaults apply identically on the non-conflict path. The only behavioral delta is the conflict path, which **is** the fix. |
| **5** | Churn-budget assertion vs the guard-pack table | **NO-ACTION-WITH-REASON** — reconciled | Verified personally: the guard-pack table now says the churn cycle is "— (none; **REPORTED only**)" with the residence-budget assertion located in the guard pack, and the churn pack marks the `λ × budget` comparison **UNMEASURABLE** (λ unmeasured). **The table is truthful.** |
| **6** | The 0.75 s timing-dependent sleep | **DEFER-POST-TESTER** | The 0.75 s holder holds 0.25 s beyond the 0.5 s probe budget — a real but modest flake surface that only bites under >0.25 s of holder-thread scheduling delay. The assertion fails **loud** either way (no false green). **Deferred change:** if the tester gate observes a flake, convert the fixed sleep to a readiness poll on the holder's state. Not FIX-NOW — not defect-class, not gate-integrity. |
| **7** | `F8Holder` dynamic stub | **NO-ACTION-WITH-REASON** | Test-local, anonymous-class hygiene only — no gate effect, no semantics risk. Replace with a `NamedTuple`/dataclass in the next hygiene pass (the tidier's own backlog). |
| **8** | Async twin +2 SELECTs on-loop | **NO-ACTION-WITH-REASON** | This is the **explicit, adjudicated price** of the F8.6/F8.7 double-delivery closure (the P0 read + the P2 re-read + the re-asserted guards) — ~3 sub-ms SELECTs per reconcile against the correctness invariant the whole lane exists to establish. Already recorded as code-review advisory A-2 and in the release composition. |
| **9** | `seam_hook` synchronous on-loop | **NO-ACTION-WITH-REASON** | Test-only today and non-blocking by construction. Recommended (non-blocking) one-line docstring guard — *"hooks MUST be non-blocking; they execute on the event loop inside the reconcile"* — in the next pass. A structural change (off-loop dispatch) would alter the seam the F8-c/F8-d pins depend on, immediately before the tester gate. |
| **10** | `report_message_id_pre: str \| None` vs declared `str` | **NO-ACTION-WITH-REASON** | Unreachable-with-None **by construction** (`carrier_check_needed` gates the call). If a type-checker gate ever lands, narrow at the call site (assert-then-narrow) or widen the helper annotation — a mechanical edit with no behavioral content. |

**FIX-NOW: 0 · DEFER-POST-TESTER: 1 (item 6) · NO-ACTION-WITH-REASON: 9.**

### Do any rulings change the pin inventory or an `expected-collected.md` row?

**No.** All ten are NO-ACTION or DEFER-POST-TESTER; none adds, removes, or re-scopes a pin. The only count movement in the cycle — the F6 unit pack **7→8** (the T-M7 propagation test) — was the tidier's own change, recorded as **FOLD-MAP NOTE-4**, and is reflected in the pack-local `EXPECTED_COLLECTED`; the R-1 baseline rows remain `TBD (R-1)` for the tester to measure at base. *(Item 6's deferred change, if ever needed, would alter a sleep, not a count.)*

---

## 3. What I could not verify

- **The PG/integration counts** (PG 5/5 · integration 19/19 · F8 PG 7/7 · churn+guard 10/10) are dev-claimed; I ran no scratch-PG pack (per the standing skip preference). Everything unit-reachable I ran is **0-failure**.
- **The T-H1 mutation leg** (counter deletion ⇒ RED) is reasoned from the counter's placement and the `-eq 0` guard, not executed as a planted mutation — the tester's mutation discipline owns that class.
- **The base re-measure delta 0** (T-H2's triangle) is the tidier's claim; I verified the HEAD census numbers exactly (780 total, all six families) but did not re-run the census at base.
- **R-1 remains the critical path**: `expected-collected.md`'s rows are still `TBD (R-1)` — every ladder row FATALs by design until the tester measures at base `19fc11c8b`.

## 4. Recommendation

**Proceed to the tester functional gate.** The tidier cycle closed its 6 High findings (T-H1…T-H6 all verified above), the two behavior-real warnings from the code-review council are confirmed landed with exactly the required shapes, the instruments now fail closed, the census is honest, and none of the ten deferred items is defect-class or gate-integrity. The standing caveats carry unchanged: the runtime-PG dimension and the R-1 measurements belong to the tester.

---

*Produced by the reviewer (dispatcher/controller) as the scoped personal verification of the tidier fix cycle plus the adjudication of the 10 deferred items. Read-only: no source file modified, no daemon run, no production database contacted; the census and the unit packs were executed read-only under the certified interpreter with CWD = the worktree.*
