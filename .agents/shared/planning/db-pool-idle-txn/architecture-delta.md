# Architecture Delta-Check — db-pool-idle-txn re-based plan-of-record (@ 8628cc15a)

- **Adjudicated object:** the re-based plan-of-record `8628cc15a` (docs only; `git diff 19fc11c8b HEAD -- daemon/ tests/ scripts/ agents/ plugins/ frontend/` empty ⇒ code tree == base `19fc11c8b`), the merged Phase-0 evidence (`BASELINE.md` ≡ `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md`; RAW canonical in `.agents/tester/harness/dbpool-phase0/raw/`), the reviewer's `delta-recheck.md` (17dbfe33d, 24 conditions), and `FOLD-MAP.md` (12/12 C-closures + P-1..P-11 + DR-1..DR-36, 0 open).
- **Inputs:** my r1 recommendation (`architecture-recommendation.md` @ `a1b38260f`) — the planner annotated it additively; the plan moved under it as the dispatcher stated.
- **Delta-check instances:** `e9888d4a-cf6b-4c2e-9a1b-f21db0300112` (`architect-worker-f4-delta`, resilience-design) · `dc97fe05-f644-408d-8786-3aa8410370ad` (`architect-worker-f7-delta`, scalability-design). Every load-bearing worker claim below was **re-verified by the architect** against the plan artifacts, the RAW outputs, and the code (§6); nothing is laundered.
- **Planner artifacts unmodified** — all amendments are expressed here only.

---

## ① VERDICT: **SOUND-WITH-AMENDMENTS**

The re-based design is architecturally sound. The Phase-0 measurements did to round 1 what round 1 did to the RCA: they replaced inference with measurement, and every major re-base move (F4 premise refutation, F8 primary, F5 raised, metric change to `xact_age_s`, statement-cap reversal, disabled backstop, conninfo attribution) is **evidence-following and correct**. The C1/C2 posture is preserved by construction (F4-A/B live inside the two probe bodies; the P2 oracle and its inputs are untouched; the fail-closed classes stay fail-closed).

**Eight amendments (Δ1–Δ8) are required before implementation; none reverses a design decision.** One is a **mechanism hole in a gate fence** (Δ1 — fail-open, must land with the plan, not at implementation time), one closes the single **unowned residual** both delta workers independently found (Δ2 — asyncpg idle-in-txn hold), and the rest are precision/disclosure folds. No question returns UNSOUND.

---

## ② The seven mandate questions, adjudicated

### Q1 — The rewritten F4 (DR-29): **SOUND** (mechanism + C1 + C2 verified; race residual correctly scoped)

- **(a) Mechanism.** The synchronous admission pre-check is sound and *narrows nothing*. Verified exception path: a `TimeoutError` raised inside the probe body propagates out of `asyncio.to_thread` as the future's exception; `asyncio.wait_for` does not re-wrap it; `readiness.py:1284-1285` catches `TimeoutError` **before** the generic `Exception → hard_component_red` branch (order re-read at HEAD) ⇒ `FAILURE_CLASS_PROBE_TIMEOUT`, indistinguishable from a deadline expiry — which is the intent (F4-c). At capacity the OLD probe was a pool waiter that `wait_for` killed at 0.5 s (measured b3: `checkedout_right_after_timeout: 15`, timeout 0.5012 s, `raw/t5_fix_sites.out:1960-1967`) — **DB-reachability was never measurable in that state**, so the pre-check loses no signal; below capacity the probe is byte-identical. The admission predicate (`checkedout()` vs a pinned configured-capacity constant, D-57) is the right one — `overflow()` flaps with load and `_max_overflow` is private API; the capacity-equality pin catches config drift.
- **(b) C1 — no masking.** Walked both orderings. `pool_leak` arms **only** on the P2 psycopg oracle (`readiness.py:788-806`, `:852-900`); the P1 manager pool is ADVISORY and MUST NOT arm (`:526-538`); P2 sampling is an **in-memory `get_stats()`** read that does not depend on any probe succeeding. Manager-pool-poisoned → db/queue components fail as `probe_timeout` (grace-eligible, grace **engages** — the same timing surface as the old 0.5 s expiry; no new green-over-poisoned path); P2-starved → `pool_leak` fires → grace **refuses** regardless of `probe_timeout`. F4-A can neither suppress, delay, nor reinterpret any `pool_leak` input. F4-A/B live inside the two probe bodies — within the C1 permitted change set; the only addition is one pool-state constant, which is probe-side state, not detector state.
- **(c) C2 — pass.** No conditionality near any rollback path (probes hold no transaction; `SET LOCAL` reverts via `reset_on_return`), no soak-surface touch. The F4-B demotion is honest: b2 measured the held-connection shape yielding an **`active`** backend (not idle-in-txn), so `SET LOCAL` is defence-in-depth for statement-hang only — exactly how the plan now words it (F4-e docstring rewrite).
- **(d) The 30 s race residual — acceptable, correctly scoped in D-57, one pin-wording condition (Δ5).** The race fires only on the 14→15 boundary transition (microsecond window); under the incident's steady 15/15 saturation the pre-check fires deterministically. The residual re-instantiates the executor-parking shape for at most one thread per race, bounded by `pool_timeout` — disclosed at `phase1-plan.md:44` and in F4-f's BASE-vs-HEAD. **Condition:** F4-f's "no waiter" assertion must be scoped to a **quiescent 15-holder setup** (holders created and stable before the probe is invoked); an absolute "no waiter under concurrent fill" wording would false-red on the disclosed race (Δ5).

### Q2 — F8 as Phase-1 primary + F5 raised: **claim boundary CONFIRMED honest; phase order sound**

- The claim statement is explicit and evidence-matched (`plan-overview.md` §1): "This lane does NOT close the incident's symptom class… After this lane the daemon can still hold 15/15 manager slots" — with the sync-DB dwell multiplier (`routers/instances.py:476`, `routers/queues.py:654`) and the BFS span (`repository.py:1434-1645`) named as untouched and routed (R-1/R-2, §10 #11/#12).
- The F5 raise is evidence-forced, not scope creep: the incident's "current transaction is aborted" journal lines are **reproduced as a same-connection one-shot cascade inside wedge-capture** (BASELINE T5a-a5; `raw/t5_fix_sites.out` a5: step 1 `UndefinedTable: relation "tasks"` → steps 2-3 `InFailedSqlTransaction`), and T5a-a4 measured `reset_on_return='rollback'` cleaning aborted state on pool return ⇒ the original RCA mechanism ("aborted connections handed back out of the pool") is **refuted by measurement** and the plan records the refutation explicitly (BASELINE :392-403; D-52; DR-34; every "handed back out" occurrence in the plan set sits inside a retraction). Internally consistent.
- Phase order has no inversion: F8 = P1 primary (3 sites, incl. the conditional `instance.py:1207/:1360` shape with leader-ruling/AC31 disclosure); F5 fix lands P1 with its PG gate at G2.6 (disclosed); F2 = P2 with `Depends = F8 + G0`; F7 = P3 asserts P1 shapes. No P1 pin depends on a P2 knob (F4-B's `SET LOCAL` is probe-local; the engine backstop ships DISABLED).

### Q3 — The disabled backstop (D-47): **ACCEPT-WITH-AMENDMENT (Δ2)**

Disabled-with-disclosure is the **right posture** for the *manager-engine* bound: its derivation inputs are model-injected (4 × 1059.877 ms = 4.24 s from the L5 regime ⇒ a 30 s floor would dominate), a wrong value is the r1-R15 collateral-kill shape (it fires on open-but-busy-in-Python sessions — precisely M-R3's class), F8 removes the only measured multi-second hold, and F7 provides detection. Compensating-control census:

| Shape the backstop would have caught | Compensating control | Status |
|---|---|---|
| M-R3 sync-twin 8 s hold | F8 (removal) | covered |
| Manager aborted-on-return | F2 `pool_reset_on_return='rollback'` (measured T5a-a4) | covered |
| Future borrow-then-await regressions | F7 `xact_age_s` guard (detection, fail-loud, not reaping) | acceptable, disclosed |
| **asyncpg adapter explicit-txn idle-in-txn** (T3d: CAN hold idle-in-txn; `server_side_reap_observed: false`; cluster default 0) | **attribution only** (D-36/D-49: own DSN + `application_name`) — **no reaping control, no owner** | **GAP → Δ2** |

A manager-engine backstop would not have covered the asyncpg pool anyway (different pool/DSN), so disabling it loses nothing there — but that is exactly why the asyncpg shape needs its own disposition. The plan discloses it ("NOT 'not a leak site'", §10 #3) and makes it attributable — **disclosure ≠ ownership**: FOLD-MAP §6 has 0 open defects and no routed commission owns the hold. Δ2 names the owner.

### Q4 — The F7 metric change: **CONFIRMED SOUND; budget derivation procedure OPEN → Δ3**

- Old-metric vacuity confirmed from the RAW: `state_change − xact_start` freezes at the last statement (the harness's own note, `raw/t2_stat_activity.out:99`) ⇒ a leaked backend shows the same sub-ms value as a healthy one forever. `xact_age_s = now() − xact_start` grows monotonically with the hold (T2b/T2d2/T1: held borrows stay idle-in-txn the whole hold; sync twin 8011 ms).
- **Leak-sensitive: yes.** **False-red-free post-F8: yes with one scoping requirement** — checkout wait precedes the first statement (`xact_start IS NULL`, T2a0/T2f) so starvation is invisible to `xact_age` (it lands in the occupancy half); the only measured legit >1 s manager-engine transaction remaining post-F8 is the **BFS depth-loop span** (routed R-2, untouched) ⇒ the residence assertion must stay **per-operation-scoped** (or declare a BFS budget), never a pool-wide literal. The 2.0 s withdrawal is correct (it would false-red the ≥8 s sync twin at BASE and any long-history fetch).
- **Budget state: two constants, per-site, consistent — but the derivation procedure is open.** Contract `:162` (F2.1 adds `MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S`, "the measured budget") vs `:227` ("NOT DERIVABLE from the Phase-0 evidence") vs `:230` (`DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S` remains as the per-site **default**; 2.0 s value withdrawn; measured healthy band 0.0005/0.0007/0.0008 s n=40 as reference). `:418` (per-op comparison) and `:424`/F7-a-static (one default constant) do **not** contradict — the static twin pins the default's existence and the metric name. **What is missing:** who measures, when, and where the values are recorded; and F2.1's "measured" label needs a dated provenance that is not Phase-0. Δ3 prescribes it.

### Q5 — Ladder + pins + TBD-by-design: **structure SOUND; the fence FAILS OPEN on a TBD cell → Δ1 (the one must-land-with-the-plan amendment)**

- TBD-by-design is **acceptable for the implementation phase**: counts are gate-time inputs; R-1 (tester lane) carries sound rules (measure at base `19fc11c8b` under the full Class-1 fence; never invent; `NOT-COLLECTABLE-AT-BASE` with reason; whole-tree reconciliation). No artifact requires counts before pack *authoring*.
- **But the gate-time hard requirement is prose, not mechanism.** The §2.3 fence script reads `EXPECTED="$1"` and tests `if [ "$COLLECTED" -ne "$EXPECTED" ]`. With a TBD cell, `test` errors ("integer expression expected"), the `if` evaluates FALSE, the FATAL is skipped, and the script prints `COLLECTED-OK count=N expected=TBD (R-1)` with **exit 0** — a vacuous pass of the exact C3 class the lane exists to kill. Verified against the script text at `test-strategy.md:307+`. Δ1 is a 3-line integer validation + one prose sentence ("a TBD cell at gate time = FATAL").
- Everything else in the ladder is sound: 17 members enumerated individually G0..G3.5 (G0 SATISFIED with both provisional literals resolved); 46 pins mirrored row-for-row (contract §5 ↔ test-strategy §3, W-6); **my r1 A8 survived the rewrite** — the whole-tree legs (G1.5/G3.5) require `collected > 0` on BOTH sides plus base-vs-head reconciliation against the sum of declared counts, mismatch = FATAL; Class-4a/4b counts are declared in `expected-collected.md` rows 27-34 (measured read-only; packs unmodified). One bookkeeping nit (Δ8): the pack-file count reads 26 (contract §5.1) vs 33 unique files (`expected-collected.md`, 35 rows incl. one dup) vs the dispatch's 27 — needs one reconciliation line, non-blocking.

### Q6 — My r1 A4-i (BEGIN fingerprint genericity): **REFUTED — ACCEPTED, on the raw outputs**

Acceptance evidence, from the RAW + harness source (not intuition):

1. `raw/t2_stat_activity.out` T2b: after autobegin + first statement, `last_query = "SELECT 1"` — the implicit BEGIN is **not** a standalone visible statement on this stack.
2. T2a/T2a0: checked-out-before-first-statement shows `xact_start IS NULL`, state `idle` — BEGIN is deferred to the first statement; there is no pre-statement BEGIN window.
3. The T2e/T2e2 rows showing `last_query = "BEGIN"` were created by the **harness explicitly** executing `text("BEGIN")` (`t2_stat_activity.py:165/:204`) — they are consistent with, and only with, an explicit standalone BEGIN.
4. T4 F-B: the explicit-BEGIN fingerprint is byte-compatible with the incident's, and `text("BEGIN")` exists at exactly 2 sites in `daemon/` (`ens_db_tools.py:1479/:1491`).

⇒ On this stack a persistent `last_query='BEGIN'` **requires** an explicit standalone BEGIN; my r1 "generic wire-level BEGIN" reading is wrong for psycopg3-on-this-daemon, and the incident's BEGIN ×2 is **measured-unique to Site A**. r1's downgrading of the attribution to UNVERIFIED is hereby withdrawn.

**Two residual caveats recorded (they bound the acceptance, they do not reopen it):**
- (i) *Stick-ness:* T5a a2/a3 measured the pool's `reset_on_return` cleaning even the aborted BEGIN shapes **on return** — so backends that stayed idle-in-txn for minutes imply the dry-run session was **still open** (the tool call in flight/hung) or the live v0.27.8 binary predates the repair engine's timeout (the plan's OQ-2 dating gap). The attribution says *whose* BEGIN it was, not *why it stuck* — the plan's OQ-2 remains the right owner of that half.
- (ii) *Unmeasured window:* `engine.begin()`-entered-with-no-statement-yet was not directly exercised; T2a's `xact_start IS NULL` before the first statement makes that window at most one round-trip (SQLAlchemy 2.0 defers BEGIN to first statement), so it cannot produce a resting `BEGIN` backend. Residual risk ≈ nil; noted for completeness.

### Q7 — Remaining holes: **5 of 6 routed with owners; 1 unowned → Δ2; 1 stale-comment hazard → Δ7**

| Hole | Routed? | Citation |
|---|---|---|
| Event-loop sync-DB (`instances.py:476`, `queues.py:654`) — dominant untreated symptom driver | ✅ R-1 commission | `plan-overview.md` §1 + out-of-scope #4 |
| BFS depth-loop span (`repository.py:1434-1645`) | ✅ R-2 commission | §10 #11 |
| Default-executor ceiling (12 workers, 1351 sites) | ✅ R-3 assess+disclose | §10 #12 |
| RUNNING-stalled missed-finalize | ✅ R-4/R-7 strand-healer family | §12 |
| asyncpg idle-in-txn hold + shared-DSN attribution | 🟡 attribution **in-lane** (D-36/D-49, F2-h/F2-j); the **hold itself unowned** | §10 #3 discloses, nobody owns → **Δ2** |
| Stale minimising comment at the routed defect (`queues.py:652-654`: "two fast SELECTs, no event-loop-relevant latency") | 🟢 not routed anywhere | contradicts plan §1 and the measured 2+N-SELECT shape (`defer_block_resolver.py:306-345/:470-472`) → **Δ7** |

Nothing else untreated-and-unrouted was found.

---

## ③ Amendments (each with the exact required change)

- **Δ1 (C3 mechanism — must land with the plan, not at implementation time).** In `test-strategy.md` §2.3, immediately after `EXPECTED="$1"; MARKER="$2"; shift 2`, add:
  ```bash
  case "$EXPECTED" in ''|*[!0-9]*)
    echo "FATAL: EXPECTED is not an integer (TBD cell?) for: $*" >&2; exit 1;; esac
  ```
  and append one sentence to §2.3's rules: **"A `TBD` cell in `expected-collected.md` at gate time = FATAL — the fence integer-validates EXPECTED before comparing."** Without this, the fence passes vacuously on every unfilled row (verified: bash `test` with a non-integer EXPECTED skips the FATAL branch and exits 0).

- **Δ2 (the unowned residual).** Name an owning commission for the **asyncpg checkpoint-adapter idle-in-txn hold** (`checkpoint_adapter.py:1229-1240`; measured T3d: leak-capable, no server-side reap, cluster default 0). Recommended exact change: add one routed row to `plan-overview.md` §12 — *"R-x: asyncpg-adapter reap — the follow-up DB-pool commission adds `server_settings={'idle_in_transaction_session_timeout': <measured>}` to the asyncpg pool's NEW dedicated DSN (the same DSN D-49 already mandates), under the SAME D-47 enablement gate (enable iff a trustworthy post-F8 measurement of the adapter's legitimate prune-transaction dwell exists)."* Minimum acceptable alternative: an explicit accepted-residual row with a named owner in the honesty blocks (F2.7) and §12 — disclosure-without-owner is the one shape the plan's own routing discipline (R-1..R-8) does not tolerate.

- **Δ3 (budget derivation procedure — close the open spec).** Reconcile contract `:162` with `:227` and pin the procedure: (i) F2.1's `MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S` provenance line becomes *"measured post-F8 at pin-writing (NOT Phase-0 — see §3.4)"*; (ii) each per-site budget is **measure-then-set at pin-writing**, with its derivation note recorded in the pack header (or a BASELINE addendum) — the measured healthy band (median 0.0005 s / p95 0.0007 s / max 0.0008 s, n=40) is the reference point; (iii) a site whose budget cannot be measured records `NOT-DERIVABLE` + the exclusion in the pack header — **never a guessed number** (the plan's own DR-11 rule, made procedural); (iv) the per-site budget **declaration location** is pinned (recommend: one table in the F7 pack header mapping call-site → budget constant), so F7-a-static can assert reachability.

- **Δ4 (BASELINE C-10 erratum).** Add one erratum line under `BASELINE.md`'s C-10 invariant: *"ERRATUM (D-48/DR-35): 'residence' throughout C-10 means `xact_age_s = now() − xact_start`; the printed `state_change − xact_start` form is the superseded metric and is the exact vacuous form D-48 withdrew (a frozen sub-ms value cannot equal T1's measured 8.0 s hold)."* The as-worded C-10 is internally inconsistent with the plan's own T1 finding; the contract is authoritative per D-53, but BASELINE is the cited measured record and must not re-teach the vacuous form.

- **Δ5 (F4-f race tolerance).** In `phase1-plan.md` T1.7a / the F4-f pin spec, scope the starvation assertion explicitly: *"the 15 holders are created and held STABLE before the probe is invoked (quiescent setup); the 'no waiter / bounded termination' assertion applies to that setup — the D-57 boundary race (14→15 transition during the pre-check) can still strand one waiter for up to `pool_timeout` and is a disclosed residual, not a pin failure."*

- **Δ6 (capacity-constant placement).** Keep the F4-A capacity constant **module-level in `readiness.py`** (D-57 permits either home). If the implementer prefers `constants.py`, the coupling matrix (`plan-overview.md` §9) needs a one-row Phase-1 amendment for `constants.py` first — as written the matrix gives `constants.py` to Phase-2 F2 only, and an unamended Phase-1 addition there would violate the lane's own coupling discipline.

- **Δ7 (stale minimising comment at a routed defect).** `daemon/routers/queues.py:652-654` carries "two fast SELECTs, no event-loop-relevant latency" — factually wrong (the resolve path is 2 + N(holder) SELECTs with connection #1 pinned idle-in-txn across the WS2 loop, `defer_block_resolver.py:306-345/:470-472`) and it contradicts plan §1's own "dominant dwell multiplier" classification, inviting R-1's future owner to close the defect as a non-issue. Attach the one-line comment correction to the **R-1 commission's scope** (comment-only edit; this lane does not otherwise touch the file).

- **Δ8 (bookkeeping, non-blocking).** One reconciliation line in `expected-collected.md`: pack-file inventory reads 26 (contract §5.1) vs 33 unique files / 35 rows (this file, one duplicated row) vs 27 (dispatch brief). State which is authoritative (the contract's §5 per W-6) and reconcile the counts.

---

## ④ Disposition of r1 amendments in the re-based plan (for the record)

| r1 | Disposition in `8628cc15a` |
|---|---|
| A1 (F8) | **Landed, promoted to Phase-1 primary**; 3 sites incl. the `:11754-11757` second-site and the conditional `instance.py:1207/:1360` shape |
| A2 (F7 invariant) | Landed and **improved twice**: count→occupancy became three-state occupancy; my "residence" operationalization was itself corrected by measurement (`xact_age_s`, D-48) — the round-2 version is strictly better than r1's |
| A3 (census method) | Landed (`xact_age_s` + full state/attribution field set + `backend_start > run_start` BASE filter) |
| A4-i (BEGIN genericity) | **Refuted by measurement — withdrawn** (Q6 above); A4's other corrections (occupancy framing, executor-side retention, grace-engages) all landed |
| A5 (application_name) | Landed and **improved**: conninfo channel (measured stronger than `-c`), three-pool attribution (C-8), own asyncpg DSN — supersedes r1's two-engine form |
| A6 (drop global cap; backstop after F8) | Landed and **tightened**: statement cap REVERSED outright; idle-in-txn bound DISABLED-with-enablement (accepted, Q3, with Δ2 closing the asyncpg gap) |
| A7 (executor ceiling) | Landed (R-3) |
| A8 (gate fences) | Landed (`expected-collected.md` is a stronger form than r1's) — **Δ1 fixes the fence's TBD fail-open** |
| A9–A12 | Landed (doc corrections, F7 BASE-demonstration reframing, OQ-1 in-lane pin, non-goals) |

## ⑤ Residual risks (delta register)

- 🟡 **Δ1 un-landed** ⇒ every gate can pass vacuously until the fence integer-validates — the only amendment that gates implementation start.
- 🟡 **asyncpg hold unowned** (Δ2) — attributable but unreapable; same class of decision as D-47, needs the same enablement gate and a named owner.
- 🟡 **R-1/R-2/R-3 routed but unscheduled** — routing ≠ scheduling; the post-lane daemon can still hold 15/15 manager slots by design (disclosed).
- 🟢 Per-site budget derivation procedure open (Δ3); BASELINE C-10 wording (Δ4); queues.py comment (Δ7); inventory counts (Δ8).

## ⑥ Method & verification

Two-worker delta fan-out (one skill each: resilience-design on F4/backstop; scalability-design on F7/ladder/claim/holes). Architect verification pass at HEAD: the §2.3 fence script text (`test-strategy.md:307+` — the fail-open reproduced by inspection of `test` semantics); `plan-overview.md` §1/§9/§10/§12 (claim, coupling matrix, routing); contract `:162/:227/:230` (budget constants); `BASELINE.md:207-233` (C-10 wording); `daemon/routers/queues.py:645-660` (stale comment); `readiness.py:83-91/:520-545/:788-806/:1262-1290` (classes, sampling, oracle, `_guarded` except-order — carried from r1's read and re-confirmed); harness source `t2_stat_activity.py:165/:204` + `t4_fingerprints.py:129-142` (mandate-6 adjudication); RAW t1/t2/t3/t4/t5 re-read for every measured value quoted above. No code, test, or DB access; no planner artifact modified.
