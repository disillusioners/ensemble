# Δ3 (J2) — post-F8 legitimate manager-engine hold measurement — 2026-10-29

Worktree: `/home/nea/ensemble-src-wt-tdbp-delta3` detached @ `4cc63574a` (lane HEAD with F8 landed; porcelain clean; nothing committed).
Scratch PG: native disposable cluster, port **5444** (`initdb -A trust`), db `dbpool_d3m` OWNER role `dbpool_d3m` (LOGIN, later +CREATEDB for the packs). Fence: per-variable `env -u` scrub + `mktemp -d` HOME on every command; in-script fence refuses any ambient `POSTGRES_*`/`DAEMON_PORT` and asserts the engine URL is `127.0.0.1:5444/dbpool_d3m`.
Engine under test: the daemon's OWN `create_postgres_engine` (pool 5/10, `pool_pre_ping`, conninfo `application_name=ensemble-manager`, UTC connect args) — D-23.
Instruments:
  (i) per-scope self-read `xact_age_s = clock_timestamp() − xact_start` (D-48 metric) from inside each `_session_scope` just before close — P0 = scope #1, P2 = scope #2 of the REAL `InstanceManager` reconcile methods rebound on the house holder;
  (ii) `body_wall` (scope open → body end incl. commit) — upper bound for committed scopes (SQLAlchemy starts a fresh post-commit txn for the self-read; self-read alone would under-report);
  (iii) 100 ms external sampler: three-state census of the scratch DB (`backend_start > run_start`, own pid excluded) + `engine.pool.checkedout()`;
  (iv) the F8-b slowed-fetch seam (`ChildReportsService._get_last_assistant_message` → configurable `asyncio.sleep`).
`hold_s` per scope = max(xact self-read, body_wall) — conservative. First 3 iterations per leg are `:warmup` (plan-cache/pool-fill) and EXCLUDED from the derivation (recorded separately).

## 1. Structural result — the post-F8 property (F8-b reproduction)

Fetch windows (P0-close → P2-enter), measured iterations only:

| leg | n | window | max checkedout | max idle-in-txn backends |
|---|---|---|---|---|
| (a) async, fetch slowed 0.05 s | 20 | ~0.05 s | **0** | 1† |
| (a) async, fetch slowed 0.5 s | 20 | ~0.50 s | **0** | **0** |
| (a) async, fetch slowed 2.0 s | 20 | ~2.00 s | **0** | **0** |
| (a) sync bridge, fetch slowed 0.2 s | 20 | ~0.20 s | **0** | **0** |

† boundary artifact of the 50 ms window vs the 100 ms sampler tick: `checkedout()` is read before the census query inside one tick; a scope edge can fall between them. The ≥0.2 s windows (clean separation) are authoritative: **no manager-pool slot is held across the content fetch at any tested fetch duration**.

P0/P2 holds are INVARIANT to the fetch duration (medians 4.8–5.7 ms / 14.0–17.0 ms across 0.05→2.0 s fetches) — pre-F8 the hold equaled the fetch latency (Phase-0: median hold 1029.9 ms ≈ await 1026.5 ms; sync twin ≥ 8 s). Sampler: `max_checkedout_any_time = 1`, `idle_in_txn_max_xact_age_s = 0.013`, only `application_name` seen = `ensemble-manager`, `checkedout` returned to baseline 0 after every leg.

## 2. Per-operation hold distributions (measured; warm-ups excluded)

The deferred-report reconcile (F7 table row 3 call-site), both entry points, all sub-shapes:

| operation (scope) | n | min | median | p95 | max |
|---|---|---|---|---|---|
| reconcile (a) async P0 (fetch .05 s) | 20 | 0.004005 | 0.004822 | 0.005672 | 0.006065 |
| reconcile (a) async P2 (fetch .05 s) | 20 | 0.012818 | 0.014707 | 0.016230 | 0.018472 |
| reconcile (a) async P0 (fetch 0.5 s) | 20 | 0.004689 | 0.005170 | 0.006200 | 0.006823 |
| reconcile (a) async P2 (fetch 0.5 s) | 20 | 0.015278 | 0.016357 | 0.017907 | **0.021607** |
| reconcile (a) async P0 (fetch 2.0 s) | 20 | 0.004083 | 0.005158 | 0.007027 | 0.007228 |
| reconcile (a) async P2 (fetch 2.0 s) | 20 | 0.014485 | 0.016976 | **0.019867** | 0.021228 |
| reconcile (a) sync P0 (fetch 0.2 s) | 20 | 0.004428 | 0.005003 | 0.006188 | 0.008407 |
| reconcile (a) sync P2 (fetch 0.2 s) | 20 | 0.013869 | 0.015422 | 0.017077 | 0.017467 |
| reconcile (b) P0 (carrier-readiness reads) | 20 | 0.007856 | 0.008595 | 0.011037 | 0.019379 |
| reconcile (b) P2 (create task + stamp) | 20 | 0.011554 | 0.012631 | 0.015270 | 0.016023 |
| reconcile (c) P0 (delivery, live carrier) | 20 | 0.007839 | 0.008918 | 0.009358 | 0.009971 |
| reconcile (c) P2 (delivery-only, no commit) | 20 | 0.007653 | 0.009033 | 0.010850 | 0.013849 |
| reconcile (c) P0 (revival shape) | 20 | 0.008563 | 0.008934 | 0.009822 | 0.010110 |
| reconcile (c) P2 (c_revival: INSERT+commit) | 20 | 0.012918 | 0.014003 | 0.015045 | 0.015201 |

Warm-up maxima (record-only, excluded): global warm-up max **0.032895 s** (first (a)-leg P2).

The other F7-table call-sites, through the same manager engine:

| operation | n | min | median | p95 | max | metric |
|---|---|---|---|---|---|---|
| guard read/write cycle (INSERT+SELECT+commit) | 40 | 0.004324 | 0.004785 | 0.006052 | 0.007280 | wall |
| guard read/write cycle — txn age at last statement | 40 | 0.001736 | 0.001852 | 0.003133 | 0.003996 | xact_age (pre-commit self-read) |
| `_has_live_process_report_carrier` (own session, 1 SELECT) | 20 | 0.003771 | 0.004032 | 0.005078 | 0.005469 | wall (upper bound) |

The churn-census cycle (`test_dbpool_churn_census.py` `_churn_round`) is the same INSERT+SELECT+commit shape through the same engine — the guard-cycle row covers it; the churn pack itself was additionally driven green on this cluster (below).

**Derivation anchors**: worst measured legitimate hold (max of per-op maxima, warm-ups excluded) = **0.021607 s** (reconcile P2 sub-shape (a), the heaviest write scope: re-read + re-asserted guards + parent read + message INSERT + task INSERT + injection UPDATE + commit). Worst p95 = **0.019867 s**.

Cross-cluster variance evidence: Phase-0's guard-cycle max healthy residence on ITS idle cluster was 0.0008 s; the identical cycle on THIS (busy, shared) host measures 0.004 s txn-age max — a 5× host-to-host ratio with zero code change. This is the floor of the safety factor.

## 3. Proposed `MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S`

**Value: 0.25** (seconds).
**Formula: worst measured legitimate hold × 10× safety factor, rounded up** — 0.021607 s × 10 = 0.21607 s → **0.25 s**.

WHY this formula (vs the alternatives):
- The guard-site precedent (`DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S`, 62.5× its max) does NOT transfer: the reconcile's legitimate hold is intrinsically ~20–25× larger than a guard cycle (real ORM work over multiple statements), so 62.5× ⇒ 1.35 s — ABOVE the guard's shortest deliberate-leak dwell family (1.0 s) and comparable to the F8-b 2.0 s slowed-fetch mutation dwell ⇒ mutation legs would go vacuous. Rejected.
- Raw max-of-maxima rounded up (0.05 s) has zero load headroom: the measured host-to-host variance alone is 5× (above); production PG under load inflates round trips several-fold further ⇒ false-red risk on a healthy daemon. Rejected.
- 10× on the worst max keeps: **12.6× above the worst measured p95**, **7.6× above the worst warm-up outlier** (0.0329 s), **4× below** the shortest deliberate-leak dwell (1.0 s — F7-a legs sleep budget+0.5 s and RED with 3× margin), and **8–32× below** the F8-b-class mutation dwell (2.0 s) and the production sync-fetch budget (8.0 s) — a re-introduced borrow-then-await REDs immediately.
- It remains the DEFAULT per-call-site hold budget for any site declaring no tighter budget (constants.py comment) — 0.25 s sits 1–2 orders below the incident class (minutes-stuck backends).

**Provenance line to write**: `measured post-F8 at pin-writing (NOT Phase-0)` — anchor: max measured hold 0.021607 s / p95 0.019867 s, n=20/leg (7 legs × 23 calls incl. 3 warm-ups excluded), scratch PG 16 on 127.0.0.1:5444, lane HEAD 4cc63574a, 2026-10-29; raw: `.agents/tester/RESULTS/raw/2026-10-29-dbpool-delta3/`.

## 4. F7 header call-site table (as found at 4cc63574a)

| call-site | budget constant | derivation (as written) |
|---|---|---|
| guard read/write cycle (guard pack) | `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S` = 0.05 | 62.5× measured MAX healthy residence (0.0008 s, n=40 — BASELINE.md T2(d)); >3 orders above p95 |
| churn census cycle (`test_dbpool_churn_census.py`) | — (none; REPORTED only) | no budget-judgment; occupancy half only (Deferred #5 reconciliation) |
| deferred-report reconcile P2 (`daemon/manager.py`) | `MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S` = **-1.0 sentinel** | NOT-DERIVABLE at pin-writing (Δ3(iii)/DR-11); measured post-F8 by the TESTER; scoped OUT while negative — never guessed |

## 5. Verification runs on this cluster (fenced, PG_TEST_* → 127.0.0.1:5444)

- F7 guard pack `test_db_pool_idle_in_transaction_guard.py`: **7 passed** (5.38 s)
- churn pack `test_dbpool_churn_census.py`: **3 passed** (3.71 s)
- F8 reconcile pack `test_dbpool_deferred_report_reconcile_pg.py`: **7 passed** (15.56 s; first attempt ERRORED on leftover full-metadata schema from the harness in the shared maintenance DB — schema reset, then clean pass; pack defect NONE)
- Required pin pair (unit, timeout 300): `test_dbpool_census_families.py` + `test_dbpool_residence_static.py`: **8 passed** — green with the CURRENT -1.0 sentinel; the apply-step must keep this pair green.

## 6. Raw artifacts

`/tmp/tdbp-delta3/raw/` and `.agents/tester/RESULTS/raw/2026-10-29-dbpool-delta3/` (untracked; nothing committed):
- `00-pg-setup.txt`, `00-initdb.log`, `00-pg.log` — cluster bring-up
- `delta3_measure.py` — the harness (fence + instruments + legs)
- `delta3-measure.json` — full raw record (142 leg entries, per-scope wall/body_wall/xact/hold, fetch traces, sampler rows summary)
- `delta3-measure.stdout.txt` — harness stdout
- `01-f7-guard-pack.txt`, `02-churn-pack.txt`, `03-f8b-pack-rerun.txt`, `04-pin-check.txt`, `05-summary.md` (this file)
- `06-teardown.txt` — teardown evidence
