# Phase-0 RAW verification — independent adversarial re-check

**Verifier role:** read-only adversarial checker. No code change, no daemon, no DB, no commit.
**Worktree:** `/home/nea/ensemble-src-wt-dbpool-idletxn` — branch `fix/db-pool-idle-txn` @ `43e577a5bc45edb78fa69cba0c384e0545fdc6a7`
**Raw evidence:** `.agents/tester/harness/dbpool-phase0/raw/{t1_mr3_hold,t2_stat_activity,t3_pool_attribution,t4_fingerprints,t5_fix_sites}.out`
**Harness:** `.agents/tester/harness/dbpool-phase0/{common,t1_mr3_hold,t2_stat_activity,t3_pool_attribution,t4_fingerprints,t5_fix_sites}.py`, `run_all.sh`, `README.md`
**Copies checked:** `.agents/shared/planning/db-pool-idle-txn/BASELINE.md` (599 L) and `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md` (576 L, declared canonical)
**Base claimed:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87`

---

## 0. Method note — what "re-derive" could and could not mean

* The T1/T2/T3/T4/T5 raw files contain **aggregate summaries**, not per-iteration series (T1) and not
  the 40 individual residence values (T2d). The only genuine **time series** in the raw are
  `loop_scheduling_delay_ms` (counts only: n = 269 / 2251 / 7009) and the T5b `retention_samples`
  arrays (t5_fix_sites.out:89-1013, 1028-1953, 1968-2017).
* Therefore: for T1 I re-derived **every** distribution statistic by internal-consistency
  reconstruction (min ≤ median ≤ p95 ≤ max, mean inside the range, n consistency, cross-regime
  max/min) and re-implemented the two headline derivations by hand; for T2d/T2d2 I re-computed
  the aggregates from the raw's own summary fields and the sampling arithmetic; for T5b I read the
  **full retention series** sample-by-sample (that is where the real independent arithmetic was
  possible). Per-iteration recomputation of the T1 min/median/p95 is **NOT VERIFIABLE FROM RAW**
  (the series is not in the evidence) — this is a finding, not a gap in my work.

---

## 1. Re-derived numbers (my own arithmetic, from raw)

### 1.1 T1 — M-R3 hold distribution (raw `t1_mr3_hold.out`)

| Regime | n | hold min / median / p95 / max (ms) | await median (ms) | loop-delay n / median | pg state during await | my consistency verdict |
|---|---|---|---|---|---|---|
| L0 no-load | 30 | 5.929 / 7.121 / 8.475 / 21.654 | 2.692 | 0 / — | idle-in-txn 30/30 | ordered ✓; mean 7.727 ∈ [min,max] ✓ |
| L1 pool-pressure(8) | 30 | 36.123 / 46.471 / 49.977 / 55.307 | 3.686 | 0 / — | idle-in-txn 30/30 | ordered ✓; mean 46.433 ✓ |
| L2 loop-peg | 30 | 26.225 / 52.385 / 61.927 / 73.601 | 47.693 | 269 / 5.123 | idle-in-txn 30/30 | ordered ✓; mean 42.766 < median (left-skew) ✓ plausible |
| L3 pressure+peg | 30 | 55.233 / 78.846 / 100.525 / 114.502 | 40.657 | 2251 / 3.998 | idle-in-txn 30/30 | ordered ✓; mean 79.735 ✓ |
| L4 over-subscribed(20), `pool_timeout=3s` | 4 | 10.275 / 11.220 / 12.502 / 12.502 | 3.782 | 0 / — | idle-in-txn 4/4 | p95 == max ✓ (n=4 ⇒ nearest-rank p95 = max) |
| L5 loop-peg + **modelled** 1.0 s tail | 15 | 1027.955 / 1029.887 / 1033.342 / 1059.877 | 1026.527 | 7009 / 2.087 | idle-in-txn 15/15 | ordered ✓; mean 1032.257 ✓ |
| L4b 16th borrow | 1 | 0 / 0 / 0 / 0 (borrow never happened) | 0 | 0 / — | `null` 1/1 | error recorded; `same_conn=False` ✓ |
| L6 sync twin (8 s bridge) | 1 | 8011.119 (all four stats identical) | 8000.320 | 0 / — | idle-in-txn 1/1 | n=1 ⇒ min=median=p95=max ✓ |

Raw cites: L0 `t1_mr3_hold.out:26-63`; L1 `:64-100`; L2 `:101-142`; L3 `:143-184`; L4 `:185-221`;
L5 `:222-263`; L4b `:264-301`; L6 `:302-338`; L4b over-subscription block `:2-11`;
flip block `:341-397`.

**My independent arithmetic**

* Worst hold over **all** regimes = max{21.654, 55.307, 73.601, 114.502, 12.502, 1059.877, 0, 8011.119}
  = **8011.119 ms** — equals the raw's `worst_max_hold_ms` (`:345`) ✓.
* Worst hold over **async regimes only** (L0-L5, L4b excluded) = **1059.877 ms** (L5 max, n=15).
* No-load median = **7.121 ms** = raw `no_load_median_hold_ms` (`:346`) ✓.
* Hold − await gap at the medians: L5 1029.887 − 1026.527 = **3.360 ms**; L2 52.385 − 47.693 = **4.692 ms**;
  L1 46.471 − 3.686 = 42.785 ms; L0 7.121 − 2.692 = 4.429 ms.
  ⇒ the copies' claim *"the hold is ~exactly the awaited fetch latency"* holds **at the medians**
  (gap ≤ ~4.7 ms) but **fails in L1** (gap 42.8 ms — the pressure term is inside the hold, not the fetch).
  **AGREE with the copies' claim in substance; the "1:1" wording is over-tight.**
* **Is the async twin's hold bounded or unbounded?** *Unbounded by the awaited call's own budget*:
  the hold = checkout wait + statement + **whatever the fetch takes** + close; the harness never imposes a
  fetch deadline (t1_mr3_hold.py:280-282 injects a delay, it does not cap one). The only **measured**
  ceiling is the sync twin's **8.0 s bridge timeout** (`result(timeout=8.0)`, modelled at
  t1_mr3_hold.py:336-347) — and that bound is a property of the *bridge*, not of the fetch.
  The **sync twin is bounded at 8 s**; the **async twin is unbounded in the model** (L5 merely
  demonstrates a 1 s injected tail, not a cap).
* Nested session's checkout wait: the raw contains **no** checkout-wait measurement for the *nested*
  session (the F-1/A1 nested-fetch site) — the only checkout-wait number in the raw is the L4b
  **outer** borrow (3001.116 ms, `:8`). ⇒ **NOT VERIFIABLE FROM RAW** (see §6 M-9).

### 1.2 T2 — base occupancy / residence census (raw `t2_stat_activity.out`)

| What the task asks for | What the raw actually contains | Verdict |
|---|---|---|
| counts by PG state | Per-case, single-instant: T2a0 `idle` (`:6-15`), T2a `idle` (`:25-33`), T2f `idle` (`:170-178`), T2b `idle in transaction` (`:41-50`), T2c1 `idle` (`:60-67`), T2c2 `idle` (`:76-83`). T5b2 backend = `active` (t5:1400-1550). | **Partial**: a *case matrix*, not a census. The only multi-backend census is T3b = **2 backends, both `idle`**, 0 idle-in-txn, 0 active (t3:4-9). |
| residence-time buckets | **No buckets.** T2d gives n=40, min 0.0003 s, median 0.0005 s, p95 0.0007 s, max 0.0008 s, mean 0.0005 s (`:88-95`), with `observed_states_during_borrow {'idle in transaction': 40}` (`:96-98`). The 40 individual values are **not in the raw** ⇒ per-cycle re-derivation impossible. | aggregates AGREE; buckets **NOT VERIFIABLE FROM RAW** |
| arrival rate λ | **Absent from every raw file.** No timing of the churn loop, no borrow-rate count, no window. | **NOT VERIFIABLE FROM RAW** — λ is not measured anywhere; what is missing: a timestamped borrow-arrival series (e.g. per-250 ms census of `state`/`xact_age_s` with a window). |
| "healthy residence band" | median 0.0005 / p95 0.0007 / max 0.0008 s over 40 cycles | **AGREE** (`:88-95`) |

### 1.3 T3 — per-pool attribution (raw `t3_pool_attribution.out`)

* Base attribution: `backends_by_application_name {"": 2}`, `distinct_states ['idle']`, verdict
  *"distinguishable ONLY by last_query text, NOT by pool"* (`:2-25`) — **AGREE**.
* `options=-c application_name=ensemble-manager` **lands** (`:103-109`) — AGREE.
* **Ordering hazard CONFIRMED**: repair options string ends `… -c timezone=UTC -c application_name=ensemble-manager`,
  `repair_engine_reports_manager_name: true` (`:111-119`) — AGREE.
* **Last `-c` wins inside one options string**: `A_then_B → "beta"`, `B_then_A → "alpha"` (`:121-125`) — AGREE.
* **conninfo beats options**: both set → `from-conninfo` (`:127-135`) — AGREE.
* Role default vs startup packet: `from-startup` wins (`:137-141`) — AGREE.
* asyncpg channels both land (`:143-147`) — AGREE.
* asyncpg explicit txn → `idle in transaction`, no server-side reap (cluster default 0 = disabled) (`:149-161`) — AGREE.
* Shared DSN ⇒ identical `application_name` on both pools (`:163-172`) — AGREE.
* **Pool inventory (raw T3a, `:27-101`) — the 12 enumerated sites:**

  | # | kind | file:line | text |
  |---|---|---|---|
  | 1 | AsyncConnectionPool | daemon/persistence.py:316 | `saver_pool = AsyncConnectionPool(` |
  | 2 | asyncpg.create_pool | daemon/persistence.py:355 | `pool = await asyncpg.create_pool(` |
  | 3 | create_engine | daemon/__main__.py:237 | `engine = create_engine(` |
  | 4 | create_engine | daemon/repositories/factory.py:184 | `engine = create_engine(` |
  | 5 | create_engine | daemon/repositories/factory.py:215 | `engine = create_engine(` |
  | 6 | create_engine | **daemon/repositories/factory.py:289** | `# ``create_engine(connect_timeout=...)`` kwarg …` ← **comment false-positive** |
  | 7 | create_engine | daemon/repositories/factory.py:323 | `engine = create_engine(` |
  | 8 | create_engine | daemon/tools/ens_db_tools.py:633 | `repair_engine = create_engine(url, **kwargs)` |
  | 9 | create_engine | daemon/tools/ens_db_tools.py:691 | `return create_engine(` |
  | 10 | create_engine | daemon/services/vacuum_engine.py:79 | `return create_engine(` |
  | 11 | asyncpg.create_pool | daemon/services/db_pool_manager.py:261 | `pool = await asyncpg.create_pool(` |
  | 12 | psycopg.connect | daemon/services/wedge_capture/db_capture.py:195 | `return psycopg.connect(` |

  ⇒ **12 enumerated = 11 real + 1 comment false-positive** — AGREE with both copies.

### 1.4 T4 — fingerprints (raw `t4_fingerprints.out`)

* F-A instances-list ORM render: measured last_query `SELECT instances.instance_id, instances.project_id, instances.agent_id, instances.agent_dir, instances.agent_name, instances.agent_tag, instances.parent_id, instances.status, instances.metadata, insta` (`:6` — **truncated mid-token**; the harness's `left(query, 200)` caps it, common.py:87), `pg_state idle in transaction`, `byte_compatible_with_incident: true`, and a **separate** `full_rendered_sql` (`:10`) = the compiled statement ending `… completion_gate_escalated \nFROM instances`.
* F-B `BEGIN` — byte-exact, owner `ens_db_tools.py:1479/:1491` (`:12-18`).
* F-C `SELECT count(*) FROM job_queue_items j LEFT JOIN job_queues q ON j.queue_id = q.queue_id WHERE j.deleted_at IS NULL AND j.admission_state = 'queued' AND q.queue_type = 'defer'` — owner `defer_block_resolver.py:185-191` (`:19-25`).
* F-D readiness probe reads **`FROM task`**, `byte_compatible_with_incident: false` (`:26-35`).
* F-E `INSERT INTO mission_records (…)` — `MissionStatus.OPEN.value == 'open'` (`:36-43`).
* Matrix: all four incident fingerprints reproduced `true`; `readiness_probe_actually_reads_from_task: true`;
  `readiness_probe_matches_incident_fingerprint: false` (`:46-56`).

### 1.5 T5 — fix-site outcomes (raw `t5_fix_sites.out`)

**(a) ens_db_tools explicit BEGIN (`:2-78`)**
a1 statement fails → `explicit_rollback_ran: true`, `in_transaction_at_close: false`, post-close `idle`,
`last_query ';'`, next borrower OK. a2 rollback fails → no rollback, `in_transaction_at_close: true`,
pre-close `idle in transaction`, post-close `idle`/`ROLLBACK`, next borrower OK. a3 BEGIN fails →
`idle in transaction (aborted)` pre-close, post-close `idle`/`ROLLBACK`, next borrower OK.
a4 aborted conn handed to the pool → `pool_reset_on_return: "ResetStyle.reset_rollback"`, next borrower OK.
a5 wedge-capture 3-step cascade: step 1 `UndefinedTable: relation "tasks" does not exist`, steps 2-3
`InFailedSqlTransaction`, `reproduces_incident_journal_quote: true`.

**(b) readiness-probe retention — read from the FULL retention series (`:80-2021`)**
* b1 `SELECT 1`, budget 0.5 s: `probe_completed: true`, `timeout_fired: false`, completion at
  `timeout_at_s: 0.0058` (**5.8 ms**), `checkedout_at_timeout: 0`, `checkedout_final: 0`,
  `idle_in_txn_observed: false`, `total_held_s: 0.0` (`:82-88`, `:1014-1020`). Sampling: 50 ms.
* b2 `SELECT pg_sleep(5)`, budget 0.5 s: `timeout_fired: true`, **`timeout_at_s: 0.5017`**, probe pid 2325977
  (`:1021-1027`); series shows `checkedout 1` + `pg_state "active"` continuously from t_s 0.05 →
  **last `active` sample t_s 4.4747** (`:1540-1544`), **first `checkedout 0`/`idle` sample t_s 4.5275**
  (`:1551-1556`) ⇒ **release bracketed in (4.4747, 4.5275] s** on the retention clock (which starts at
  the timeout, t5:157) ⇒ absolute release ≈ 4.98-5.03 s after the statement started — consistent with
  `pg_sleep(5)`. `checkedout_at_timeout: 1`, `checkedout_final: 0` (`:1954-1958`).
* b3 pool starvation, 15 holders: `holders: 15`, `checkedout_with_holders: 15`, `timeout_fired: true`,
  **`timeout_at_s: 0.5012`**, **`checkedout_right_after_timeout: 15`** (not 16) (`:1960-1967`),
  `post_release_samples` all `checkedout 0` from t_s 0.0, `checkedout_final: 0`, verdict
  *"the abandoned probe thread was a POOL WAITER, not a holder: checkedout() did not exceed the holder
  count at the timeout"* (`:1968-2020`).

**(c) mission mint (`:2023-2075`)**
c1 first mint → `ok: true`, `rung: 3`. c2 second mint, same anchor, sequential → `ok: true`, `rung: 1`
(**no violation**). c3 different anchor → `rung: 3`. c4 8-way concurrent, no barrier →
**`error_count: 7`, `unique_violation_count: 7`**, every error `psycopg.errors.UniqueViolation` on
`ux_mission_records_anchor_open`. c4b 6-way, forced overlap → `threads: 6`, **`error_count: 5`,
`unique_violation_count: 5`**. c5 caller-supplied session → `IntegrityError` → `PendingRollbackError`
(`caller_session_usable_after: false`).

---

## 2. Score table — every headline number, copy vs copy vs raw

`AGREE` = both copies state the raw's value. `DISAGREE` = both copies state a different value
(raw value given). `NV` = **NOT VERIFIABLE FROM RAW**.

| # | Headline number / claim | BASELINE.md | RESULTS (canonical) | RAW | Verdict |
|---|---|---|---|---|---|
| 1 | L0 hold median 7.121 / p95 8.475 / max 21.654 (n=30) | :83 | :60 | t1:32-35 | **AGREE** |
| 2 | L1 46.471 / 49.977 / 55.307 (n=30) | :84 | :61 | t1:68-71 | **AGREE** |
| 3 | L2 52.385 / 61.927 / 73.601 (n=30) | :85 | :62 | t1:106-109 | **AGREE** |
| 4 | L3 78.846 / 100.525 / 114.502 (n=30) | :86 | :63 | t1:147-150 | **AGREE** |
| 5 | L4 11.220 / 12.502 / 12.502 (n=4, `pool_timeout=3s`) | :87 | :64 | t1:189-194 | **AGREE** |
| 6 | L5 1029.887 / 1033.342 / 1059.877 (n=15) | :88 | :65 | t1:226-231 | **AGREE** |
| 7 | L6 sync twin 8011.119 (n=1), bridge 8000.320 | :90 | :67 | t1:307-320 | **AGREE** |
| 8 | L4b: 15 holders, borrow timeout, wait 3001.116 ms | :103-109 | :80-87 | t1:4-8 | **AGREE** |
| 9 | "structural facts on every iteration (139/139)" | :92 | :69 | t1: sums 120+4+15=139 (L4b/L6 excluded) | **AGREE (scoping implicit)** — the 139 = L0-L5 only; neither copy says that |
| 10 | `same_conn_across_suspension_all=True` / `in_transaction_after_await_all=True` / all idle-in-txn | :94-97 | :71-74 | t1 per-regime flags | **AGREE** |
| 11 | peak checkedout 1 (no pressure) / 9 (8 holders) | :97 | :74 | t1:62, 99 | **AGREE** |
| 12 | Flip verdict **NOT MET**, `worst_max_hold_ms 8011.119` | :129-137 | :106-114 | t1:341-346 | **AGREE** |
| 13 | "async path alone worst 1059.9 ms" | :134 | :111 | t1:230 (L5 max) | **AGREE** |
| 14 | "hold == awaited fetch latency (1:1)" | :135-137 | :112-114 | t1: medians L5 gap 3.36 ms, L2 gap 4.69 ms, **L1 gap 42.8 ms** | **AGREE (substance) / wording over-tight** |
| 15 | sync twin bounded ONLY by the 8 s bridge | :126-127 | :103-104 | t1:347, t1 py:336-347 | **AGREE** |
| 16 | T2 matrix: a0/a/f = `idle`, xact NULL; b = `idle in transaction`; c1/c2 = `idle` | :159-166 | :136-143 | t2:6-15, 25-33, 41-50, 60-67, 76-83, 170-178 | **AGREE** |
| 17 | Residence n=40, min 0.0003 / med 0.0005 / p95 0.0007 / max 0.0008 s, all idle-in-txn | :172-175 | :149-152 | t2:88-98 | **AGREE** |
| 18 | Held-borrow observability "0.502 / 1.502 / **3.003** s" | :178-179 | :155-156 | t2:110-117 → min 0.5017, med 1.5024, p95 3.002, **max 3.002** | **DISAGREE** (3.003 vs 3.002 — 1 ms) |
| 19 | T2e: row gone, `waited_s 2.03`, `state_after BACKEND GONE` | :186-189 | :162-166 | t2:137-140 | **AGREE** |
| 20 | T2e quoted **`client_exception: psycopg.errors.IdleInTransactionSessionTimeout …`** | :190-191 | :167-168 | **absent from raw** (t2:121-141 has no `client_exception`; the harness only records it if the exception surfaces inside the try block — t2 py:184-187) | **NOT VERIFIABLE FROM RAW** |
| 21 | T2e2: terminated, elapsed 2.02 | :194 | :171 | t2:159-161 | **AGREE** |
| 22 | "census blind to checked-out-`idle`" | :200-205 | :177-182 | t2:17-18, 179-180 | **AGREE** |
| 23 | F7 invariant occupancy+residence over 3 states | :207-231 | :184-208 | partially supported (see §6 M-7) | **AGREE as a statement; the residence metric is defective** |
| 24 | "T5b2: 4.5 s" active holder | :229 | :206 | t5 release bracket (4.4747, 4.5275] | **AGREE** |
| 25 | 12 pool sites / 11 real / 1 comment FP (`factory.py:289`) | :239-253 | :216-230 | t3:27-101 | **AGREE** |
| 26 | base attribution `{"": 2}`, all idle, "not distinguishable by pool" | :263-267 | :240-244 | t3:2-25 | **AGREE** |
| 27 | `options=-c application_name` lands | :278 | :255 | t3:103-109 | **AGREE** |
| 28 | shared-constant-last ⇒ repair reports manager name (HAZARD) | :279, :293-299 | :256, :270-276 | t3:111-119 | **AGREE** |
| 29 | last `-c` wins (beta / alpha) | :280-281 | :257-258 | t3:121-125 | **AGREE** |
| 30 | conninfo beats options (`from-conninfo`) | :282 | :259 | t3:127-135 | **AGREE** |
| 31 | role default vs startup packet | :283-284 | :260-261 | t3:137-141 | **AGREE** |
| 32 | asyncpg channels land | :285-286 | :262-263 | t3:143-147 | **AGREE** |
| 33 | asyncpg explicit txn: idle-in-txn, `idle_in_transaction_session_timeout_default_ms: 0`, no reap | :311-320 | :288-297 | t3:149-161 — **except `backend_pid 2317370`** | **DISAGREE on the PID** (copy 2317370 vs raw **2325845**) — stale value from an earlier run |
| 34 | shared-DSN PIDs `2317401` / `2317404` | :331-332 | :308-309 | t3:165-170 → **2325876 / 2325878** | **DISAGREE** (stale PIDs) |
| 35 | T4: 4 fingerprints reproduced; BEGIN byte-exact; F-C exact string; F-D reads `FROM task`; F-E insert | :355-389 | :332-366 | t4:2-56 | **AGREE** |
| 36 | T4 header "reproduced **byte-exactly**" | :355 | :332 | t4:120-124 — F-A's check is `startswith("SELECT instances.instance_id") and "instances.project_id" in last` (a **prefix/substring** test on a **200-char-truncated** `last_query`), not byte equality | **DISAGREE (overstated)** — per-row "byte-compatible" is right; "byte-exactly" for all four is not what the harness verified |
| 37 | T4 F-A row quotes the **full** rendered SQL as "Measured `pg_stat_activity.last_query`" | :360 | :337 | t4:6 (truncated 195-char) vs t4:10 (`full_rendered_sql`) | **DISAGREE (citation)** — the quoted string is the *compiled statement*, not the PG-observed value |
| 38 | T5a a1-a4 table (rollback ran / in-txn at close / post-close state / last_query / next borrower) | :406-411 | :383-388 | t5:3-56 | **AGREE** |
| 39 | `pool_reset_on_return = ResetStyle.reset_rollback` | :413 | :390 | t5:55 | **AGREE** |
| 40 | a5 wedge-capture cascade (3 steps) + `reproduces_incident_journal_quote true` | :441-447 | :418-424 | t5:57-77 | **AGREE** |
| 41 | b1: no timeout, checkedout 0, **`completed in 5.1 ms`** | :466 | :443 | t5:88 → `timeout_at_s 0.0058` (**5.8 ms**) | **DISAGREE** (5.1 vs 5.8 ms) |
| 42 | b2: **timeout at 0.5011 s** | :467 | :444 | t5:1027 → **0.5017** | **DISAGREE** |
| 43 | b2: checkedout 1 right after timeout, pg_state `active` | :467 | :444 | t5:1027, 1957, 1400-1550 | **AGREE** |
| 44 | b2: released at t ≈ 4.52 s | :467 | :444 | t5: last `active` 4.4747, first `idle` 4.5275 | **AGREE (bracket (4.4747, 4.5275])** |
| 45 | b3: **timeout at 0.5015 s** | :468 | :445 | t5:1966 → **0.5012** | **DISAGREE** |
| 46 | b3: checkedout **15** (= holder count, not 16), probe is a waiter | :468, :472-476 | :445, :449-453 | t5:1963, 1967, 2019 | **AGREE** |
| 47 | c1 rung 3 / c2 rung 1 no violation / c3 rung 3 | :494-496 | :471-473 | t5:2025-2045 | **AGREE** |
| 48 | c4: 8-way, no barrier, **7** UniqueViolations | :497 | :474 | t5:2046-2057 | **AGREE** |
| 49 | c4b: 6-way forced overlap, **5** IntegrityErrors | :498 | :475 | t5:2059-2068 | **AGREE** |
| 50 | c5: caller session poisoned (`PendingRollbackError`) | :499 | :476 | t5:2070-2073 | **AGREE** |
| 51 | re-resolve probe **skipped** when `founding_message_id=None`, error re-raises | :511-515 | :488-492 | not in raw; **verified against code** `daemon/services/mission_record_service.py:544-567` (probe guarded by `if founding_message_id is not None:` at :561, else `raise` at :567) | **AGREE (code-verified; not a raw measurement)** |
| 52 | "3 pools" → **11** pool-creation sites | :255-257, :551 | :232-234, :528 | t3:27-101 | **AGREE** |
| 53 | incident's **18 observed backends** | :256 | :233 | not measured (incident record) | **NOT VERIFIABLE FROM RAW** (by design — incident-side datum) |
| 54 | manager engine has **no** server-side timeout / no `pool_timeout` override | :248, :554 | :225, :531 | common.py:127-145 (harness replication of factory.py:323-338: pool_size 5, max_overflow 10, pre_ping only) | **AGREE** (harness replication) |
| 55 | base `pool_timeout` is the **30 s** library default | :112-113, :563 | :89-90, :540 | raw t1:10 note + t1 py:469-472 ("the base manager engine's 30s default would make the run exceed the pack budget") | **AGREE (code/config-derived, not measured)** — the copies themselves list it under "NOT measured" |
| 56 | 3 s stall measured in L4/L4b | :87, :103-109 | :64, :80-87 | t1:186, t1:2, t1:8 (3001.116 ms) | **AGREE** — 3 s is a **harness override** (t1 py:472 `make_manager_engine(pool_timeout=3)`), the 30 s default is code-derived |
| 57 | "5/5 targets measured; 4 change a plan premise; OQ-1 closed" | :32 | :9 | each target has raw backing (see above) | **AGREE** |
| 58 | "No production code was changed" | :20, :28 | :5 | n/a (claim) | **AGREE (claim; harness is non-shipping per README:1-5)** |

**Score tally over the 58 rows: AGREE 46 · DISAGREE 8 (rows 18, 33, 34, 36, 37, 41, 42, 45) ·
NOT VERIFIABLE FROM RAW 2 (rows 20, 53) · AGREE-with-defect 1 (row 23) · AGREE-with-scoping
caveat 1 (row 9).** The 8 DISAGREE rows carry **7 individual raw-divergent values** (3 T5b timings,
3 T3 PID values, 1 T2d2 millisecond) plus **2 rows of over-stated citations** (rows 36/37 and the
absent `client_exception` in row 20).

---

## 3. Divergences between the two copies of the record

**Finding D-0 (important, negative result): there is NO numeric divergence between
`BASELINE.md` and the canonical `RESULTS/2026-10-28-dbpool-phase0-baseline.md`.**
I read both end-to-end: `RESULTS:1-576` ≡ `BASELINE:24-599` (line-for-line, same values, same
wording); `BASELINE:1-22` is a 22-line preamble blockquote (how to consume the report + the
"canonical copy is RESULTS" statement) that `RESULTS` does not carry. The two files differ only by
that preamble — i.e. **BASELINE.md = RESULTS + 22-line preamble**.

Consequence: every "stale vs raw" DISAGREE in §2 is shared by **both** copies. The
"canonical" designation is therefore currently **immaterial** — there is no number where the two
copies disagree with each other, and no number where one copy is closer to the raw than the other.
The copies' mutual-agreement is a *consistency* property, not a *correctness* property: both carry
the same 8 raw-divergent values.

**On the "RESULTS is canonical" claim itself:** it cannot be adjudicated from the artifacts
available to me:
* the harness output names neither file (`run_all.sh`, `raw/*.out` contain no report reference);
* the tester's commit message for `tester/dbpool-phase0-baseline` is **not inspectable** without git
  (read-only) access — the only commit message reachable from the shared repo is
  `/home/nea/ensemble-src/.git/COMMIT_EDITMSG` = *"chore(release): bump version to 0.27.0"*, which
  belongs to an unrelated release commit.
⇒ **"which copy is authoritative" is NOT VERIFIABLE from the artifacts**; but since the contents are
identical, the question has **no consequence** until the two files are edited independently —
which is exactly the risk the claim is meant to manage. (Recommendation, out of scope here: make
BASELINE.md a pointer, or generate one from the other, so a future edit cannot fork them.)

---

## 4. Flip-condition arithmetic — independent verdict

**Condition (architect):** "M-R3 holding a manager slot < 2 s under real load" ⇒ MET / NOT MET.

**My computation, from raw aggregates:**
worst hold over all regimes = max(21.654, 55.307, 73.601, 114.502, 12.502, 1059.877, 0, **8011.119**) = **8011.119 ms**
→ 8011.119 ≥ 2000 ⇒ **NOT MET**.
The harness implements the identical rule (`t1_mr3_hold.py:593-595`: `worst_max_ms = max(...);
flip = "NOT MET" if worst_max_ms >= 2000`) and reports `worst_max_hold_ms: 8011.119`,
`flip_condition: "NOT MET"` (t1:341-346). **My independent arithmetic reproduces the harness's
verdict exactly.**

**Verdict: NOT MET — F8 stands.** Supporting detail:
* The 8011.119 ms is the **sync twin** (n=1, modelled 8 s bridge timeout). The async twin's worst
  measured hold is 1059.877 ms (L5, n=15) — **under** 2 s, but only because the fetch tail was
  *injected* (t1 py:281-282 `await asyncio.sleep(extra_delay_s)`), i.e. the async path's 2 s
  compliance is an artefact of the model, not a property of the real fetch.
* The async twin's hold is **unbounded in the model** (no fetch deadline anywhere in the harness);
  the *only* measured ceiling in the whole evidence base is the sync twin's 8.0 s bridge.
* The copies' proposed restatement ("hold < 2 s **iff** the awaited content fetch < 2 s") is
  consistent with the data (L5 hold−await = 3.36 ms at the median) but is **not established** by it:
  in L1 the hold exceeded the await by 42.8 ms (checkout/pressure term), so the hold ≥ fetch, not
  = fetch.

---

## 5. The two derivations the plan needs

### 5.1 (a) Manager idle-in-txn bound = "4 × measured max legitimate hold, floor 30 s"

**What IS the measured max legitimate hold?**
Candidates present in the raw, in descending relevance:
1. **1059.877 ms** — L5 max (n=15). **Model-tainted**: L5's extra second is an injected
   `asyncio.sleep(1.0)`, not a measured fetch (t1 py:281-282, 486-493; README:72-78).
2. **114.502 ms** — L3 max (n=30) — the largest *un-injected* async hold.
3. **8011.119 ms** — L6 (n=1) — a **modelled bridge timeout**, not a legitimate-hold distribution.
4. The **real** `_fetch_subshape_a_content_async` latency: **NOT MEASURED** (both copies say so:
   BASELINE:560-561, RESULTS:537-538).

**Is the measurement trustworthy enough to apply the bound?** **No.**
* (i) The input the bound multiplies is **model-derived** for the regimes that dominate it (L5's
  1 s tail) and single-sample for the sync twin (L6, n=1).
* (ii) The real fetch latency — the quantity the bound is supposed to cover — is **explicitly
  unmeasured**; it is data-dependent (history length), so the max is not even identified.
* (iii) The measured hold **includes the checkout wait** (`hold_total_ms` spans scope-open → close,
  t1 py:302), so a legitimate borrower can wait up to the base `pool_timeout` = **30 s** for a slot
  *before* its transaction even starts. A legitimate hold therefore has a measured-uncertain upper
  edge **at ≈30 s**, which is exactly the proposed floor.

**Arithmetic:** 4 × 1059.877 ms = **4239.5 ms ≈ 4.24 s**; 4 × 114.502 ms = 458 ms; 4 × 8011.119 ms
= 32.04 s (only if the sync twin were treated as a legitimate hold — it is not; it is a *failure*
shape). So the formula's own arithmetic yields **4.24 s**, i.e. **the 30 s floor dominates** and the
measured multiplier is irrelevant to the shipped value.

**Verdict: NOT derivable from trustworthy data → ship DISABLED (or re-measure first).**
Reasons, in order of force:
1. The quantity the formula consumes (max legitimate hold) is **model-based** for the dominant
   regimes and **unidentified** for the real path; the copies themselves say "the plan should measure
   the real fetch before finalising" (BASELINE:560-561).
2. The proposed floor (**30 s**) coincides with the base `pool_timeout` (30 s), i.e. with the
   **legitimate checkout wait** — a legitimate borrower that waited 29 s for a slot and then opened
   its transaction would be inside the bound's reach. Enabling at 30 s risks a **false kill of a
   legitimate long waiter**, exactly the incident class the lane exists to fix.
3. At the arithmetic value (4.24 s) the bound would fire on **every** modelled legitimate hold
   (L5 max 1059.877 ms is under 4.24 s, but the *unmeasured* real fetch is unbounded) — i.e. either
   it is too tight (fires on legitimate work) or it is too loose (30 s floor) — there is no measured
   value in between that is defensible.
4. Server-side enforcement would also *kill the backend*, not just flag it (T2e proved the server
   terminates the backend: t2:137-140) — a 30 s reap on the manager engine is a
   **behavioural** change with no measured safety margin.
**Recommendation (not a decision): the bound must ship DISABLED, with the re-measurement of the real
fetch as the gate to enable; if it must ship enabled, the floor must be raised above
`pool_timeout` + 4 × (measured async max) and the metric must be `xact_age_s`, not `state_change − xact_start` (§6 M-7).**

### 5.2 (b) F7 residence budget = "measured P100 legitimate hold + margin (≥ 2× noise floor, floor 2.0 s)"

**P100 legitimate hold:** from the raw = **1059.877 ms** (L5 max, n=15, model-tainted) or, if the
sync twin is counted, 8011.119 ms (n=1, a failure shape). The un-injected async max is 114.502 ms (n=30).
**Noise floor:** the raw's healthy observable-lag band = median 0.0005 s, p95 0.0007 s, **max 0.0008 s**
(t2:88-95) ⇒ 2× noise floor = **1.6 ms** ⇒ the **2.0 s floor dominates** the formula, again making
the measured terms irrelevant: 1059.877 + 1.6 ≈ **1.061 s < 2.0 s ⇒ budget = 2.0 s**.

**Would the 2.0 s placeholder red a healthy daemon? YES — two independent ways:**
1. **The sync twin.** Its hold is **unconditionally ≥ 8 s** when the inner call stalls (measured
   8011.119 ms, t1:307-311) and is bounded *only* by the 8 s bridge. Any F7 census sampling a
   legitimately-running sync-twin M-R3 sees an `idle in transaction` backend whose age exceeds 2.0 s
   ⇒ **false red on a healthy daemon**, deterministically, whenever the sync path is in flight.
2. **The async path with a long history.** The real fetch latency is unmeasured and data-dependent
   (copies: BASELINE:560-561); a 2.0 s budget is exceeded by any instance whose
   `_fetch_subshape_a_content_async` takes > 2 s. The only evidence about that path (L5) *injected*
   the delay rather than measuring it — so the 2.0 s placeholder was never tested against a real
   fetch at all.
3. Additionally, with the metric as specified the budget is **not even computable per backend**:
   `state_change − xact_start` is frozen at the last statement's completion, so for a *leaked*
   backend it stays sub-millisecond forever (identical to the healthy band of §1.2) ⇒ the budget
   **never fires on the leak it exists to catch** (§6 M-7). The metric that works — and that the
   harness already collects — is `now() − xact_start` (`xact_age_s`, common.py:88, visible in every
   raw T2 row).

**Verdict: derivation (b) is NOT derivable as specified, and the 2.0 s placeholder WOULD red a
healthy daemon.** It must (i) use `xact_age_s` (now − xact_start), not `state_change − xact_start`;
(ii) be per-call-site — the sync path needs a budget ≥ its own 8 s bridge bound + margin, the async
path needs a budget derived from a *measured* real fetch (not yet available); and (iii) not ship at
2.0 s. Deriving it from the current evidence alone is impossible: the required inputs (real fetch
latency, per-site hold distribution) are absent from the raw.

---

## 6. Methodological weaknesses (each with file:line)

| # | Weakness | Cite | Effect on which number |
|---|---|---|---|
| M-1 | **T1 raw carries no per-iteration series** — only per-regime aggregates. Independent recomputation of min/median/p95/max is impossible; only internal consistency is checkable. | t1_mr3_hold.out:26-339 (aggregate blocks); t1 py:78-92 (`summarize_ms`) | every T1 statistic (rows 1-7) — re-derivation limited to consistency checks |
| M-2 | **p95 definition is non-standard**: nearest-rank with Python banker's rounding `int(round(0.95n))-1` → for n=30 that is the **28th of 30** (one rank below the standard nearest-rank p95 = 29th); n=15 → 14th; n=4 → max; n=40 → 38th (standard). | t1 py:84; common.py:63 | all p95 columns — small downward bias, undocumented in both copies |
| M-3 | **L5's "1.0 s fetch tail" is an injected `asyncio.sleep(1.0)`**, not a measured fetch; the PG half reads a synthetic `phase0_checkpoints` table, the python half is a stand-in. | t1 py:281-282, 486-493; README:72-78 | rows 6, 13, 14; §4; §5.1; §5.2 — the 1059.877 ms "max legitimate hold" is model output |
| M-4 | **The M-R3 shape is approximated**: a raw-text `SELECT … FROM phase0_instances` replaces the real ORM `session.get(ReportInjection, …)`; the real fetch (`saver.aget`) is replaced by a synthetic table read. | t1 py:262-268, 158-170 | the "fingerprint of the held statement" (the real one is the ORM SELECT, per T4 F-A) and the realism of the hold |
| M-5 | **The PG state is sampled ONCE per iteration, AFTER the await returns** — the copies' "observable as `idle in transaction` for the whole hold" is an extrapolation, not a continuous measurement. | t1 py:287-289 (single `activity_by_pid` after the await) | row 10, and the F7 "in-flight vs abandoned" reasoning |
| M-6 | **The hold metric conflates checkout wait + statement + fetch + close** (`hold_total_ms` = scope-open → session.close()). | t1 py:258-302 | §5.1: a legitimate hold can reach `pool_timeout` = 30 s before the transaction even opens |
| M-7 | **The residence metric is defective for leak detection.** `state_change − xact_start` measures *time to the last statement's completion*, not hold or leak duration; for a leaked idle-in-txn backend it stays sub-millisecond forever — i.e. **identical to the healthy band**. The raw itself warns: *"residence here is the OBSERVABLE lag, not the hold length"* (t2:99). The metric that works, `now() − xact_start` (`xact_age_s`), is collected (common.py:88) and visible in every raw T2 row, but is **unused** by the invariant as written in both copies. | t2 py:112-115; common.py:88-89; t2 raw:99, :49 | row 23; §5.2 — the F7 residence budget is **vacuous against the leak** as specified |
| M-8 | **T2e/e2 detection granularity is 250 ms** (poll loop) and the client-side exception is never captured, because the server kills the backend and the client only learns on its next operation. | t2 py:171-175, 184-187, 209-215 | row 20 (the copies' `client_exception` quote is unsupported by the raw) and the precision of `waited_s 2.03` / `elapsed_s 2.02` |
| M-9 | **The nested session's checkout wait is never measured.** The only checkout-wait number in the raw is the *outer* borrow in L4b (3001.116 ms). The F-1/A1 nested-fetch site (a second session opened inside a held scope) has no wait measurement at all. | t1:2-11 (only outer wait) | the "including the nested session's checkout wait" part of task 1(a) — **NOT VERIFIABLE FROM RAW** |
| M-10 | **No time-series census and no arrival-rate (λ) measurement anywhere.** The only multi-backend census is a single instant (T3b: 2 pooled backends, both idle). | t3:2-25; t2 (all) | task 1(b) λ and "counts by state over time" — NOT VERIFIABLE FROM RAW |
| M-11 | **The pool inventory is a 4-pattern regex scan** of `daemon/**/*.py` (excluding `__pycache__`/`migrations`); it matches the *comment* at `factory.py:289` and would miss pool creation via other idioms (wrappers, `sessionmaker`, `engine_from_config`). The raw does **not** record the scanned tree's commit. | t3 py:115-141; run_all.sh:10; t3 raw:65-70 | row 25 / "11 real pools" — plausible but not provably complete; base-commit provenance not in the raw |
| M-12 | **`last_query` is truncated to 200 chars** (`left(query, 200)`), and the T4 "byte-compatibility" test for F-A is a **prefix/substring** check, not byte equality with the incident text. | common.py:87; t4 py:115-126 | rows 36, 37 — "byte-exactly" and the quoted full SQL overstate what was observed |
| M-13 | **T5b retention sampling is 50 ms**, `checkedout_at_timeout` is really "the first sample after the timeout", and the release time is only **bracketed**. | t5 py:156-180 | rows 43, 44 (b2 release bracket) |
| M-14 | **b3's post-release window is truncated to 12 samples (~0.6 s of a 3 s window)**, all showing `checkedout 0` — the abandoned waiter's re-acquisition was never observed; "released after the holders release" is inference. | t5 py:227-235; t5 raw:1968-2017 | row 46 (the waiter verdict itself IS in the raw: t5:2019) |
| M-15 | **The 8 stale values in both copies (rows 18, 33, 34, 41, 42, 45) prove the report was written against an earlier harness run** than the committed `raw/*.out` (T3 PIDs 2317xxx vs committed 2325xxx). The committed raw is therefore the newer record, and the copies are partly stale against it. | t3:150, :165-170 vs copies' 2317370/2317401/2317404; t5:88, 1027, 1966 | the 8 DISAGREE rows — a **provenance** finding: the reports were not re-issued after the final harness run |
| M-16 | The T5b "held" metric is scoped to `idle in transaction` only, so b2's genuine ~4.5 s *active* retention is reported by the harness as `total_held_s: 0.0`. | t5 py:173-178 | row 43-44 — the harness's own summary is misleading if read naively |

---

## 7. Addendum — DR-29…DR-36 clusters re-scored against raw

| Cluster | Claim (as restated) | Raw cite | Verdict |
|---|---|---|---|
| **1 · T5b b1** | SELECT 1 → no timeout, `checkedout()=0` | t5:83-88 (`probe_completed: true`, `timeout_fired: false`), `:1018-1019` (`checkedout_at_timeout: 0`, `checkedout_final: 0`) | **AGREE** (the *copies'* companion figure "completed in **5.1 ms**" is **DISAGREE** — raw `timeout_at_s: 0.0058` = **5.8 ms**) |
| **1 · T5b b2** | pg_sleep(5), 0.5 s budget → exceeded at **~0.5011 s** | t5:1027 → `timeout_at_s: 0.5017` | **DISAGREE** (raw 0.5017; 0.5011 is the copies' stale value) |
| **1 · T5b b2** | `checkedout()=1` | t5:1957 `checkedout_at_timeout: 1` | **AGREE** |
| **1 · T5b b2** | PG state **active** | t5:1400-1550 (every sample `pg_state: "active"`, `checkedout: 1`) | **AGREE** |
| **1 · T5b b2** | total ~4.52 s | t5: last `active` 4.4747 (:1540-1544), first `idle` 4.5275 (:1551-1556) ⇒ bracket (4.4747, 4.5275] | **AGREE (bracketed)** |
| **1 · T5b b3** | exceeded at **~0.5015 s** | t5:1966 → `timeout_at_s: 0.5012` | **DISAGREE** (raw 0.5012) |
| **1 · T5b b3** | `checkedout()=15`, **not 16**; probe never got a connection (pool waiter) | t5:1962-1963 (`holders 15`, `checkedout_with_holders 15`), `:1967` (`checkedout_right_after_timeout: 15`), `:2019` (verdict text) | **AGREE** |
| **2 · flip inputs** | hold median 1029.9 vs await median 1026.5 (L5) | t1:227-228 vs :235-236 | **AGREE** |
| **2 · flip inputs** | 52.4 vs 47.7 (L2) | t1:106-107 vs :114-115 | **AGREE** |
| **2 · flip inputs** | async worst hold 1059.9 ms | t1:230 (L5 max) | **AGREE** (n=15) |
| **2 · flip inputs** | sync twin 8.0 s bridge ceiling, measured worst 8011 ms | t1:18 (`bridge_ms 8000.32`), :307 (hold 8011.119) | **AGREE** (n=1) |
| **2 · flip inputs** | sample sizes | t1: iters 30/30/30/30/4/15/1/1 (`:29, 66, 103, 145, 187, 224, 266, 304`) ⇒ total 141 iterations, 139 excluding L4b/L6 | **AGREE** (the copies' "139/139" is the L0-L5 subtotal; neither copy states the exclusion) |
| **3 · attribution** | conninfo BEATS `options=-c` | t3:127-135 (`both → from-conninfo`) | **AGREE** |
| **3 · attribution** | last `-c` wins within one options string | t3:121-125 (`beta` / `alpha`) | **AGREE** |
| **3 · attribution** | `repair_engine_reports_manager_name: true` | t3:113-118 | **AGREE** |
| **4 · residence band** | median 0.0005 / p95 0.0007 / max 0.0008 s over 40 cycles | t2:88-95 | **AGREE** — but the **40 per-cycle values are not in the raw**; I re-derived nothing per-cycle, only the aggregate (the raw's own note at t2:99 warns the metric is the observable lag, not the hold) |
| **4 · three-state counts** | idle-with-no-xact / idle-in-txn / active | per-case only: idle → t2:6, 25, 60, 76, 170; idle-in-txn → t2:41 (+40 borrows t2:96-98); active → t5:1400-1550; census → t3:4-9 (2 backends, both idle) | **AGREE as a case matrix / DISAGREE if read as a single census** — the raw has **no** unified multi-state census and **no λ** |
| **5 · double-mint** | c4 = 8-way, no barrier → 7 real UniqueViolations | t5:2046-2057 (`error_count: 7`, `unique_violation_count: 7`) | **AGREE** |
| **5 · double-mint** | c4b = forced overlap → 5 IntegrityErrors | t5:2059-2068 (`threads 6`, `error_count 5`, `unique_violation_count 5`) | **AGREE** |
| **5 · double-mint** | re-resolve probe SKIPPED (`founding_message_id=None`) and the error re-raises | **not in raw**; verified against code `daemon/services/mission_record_service.py:544-567` (guard at :561, `raise` at :567) | **AGREE (code-verified) / NOT VERIFIABLE FROM RAW (as a measurement)** |
| **6 · 11 pool sites** | the 11 (12 enumerated) sites | t3:27-101 (table in §1.3) | **AGREE** — 12 rows enumerated, 1 comment false-positive (`factory.py:289`) ⇒ 11 real |
| **7 · 3 s stall** | the stall was measured at **3 s**, not the 30 s default | t1:186 (`[pool_timeout=3s]`), t1:2-8 (L4b header + `mr3_borrow_wait_ms 3001.116`), t1 py:469-472 (the 30 s default would blow the pack budget ⇒ override) | **AGREE** — 3 s is a deliberate harness override; the 30 s default is **code/config-derived**, not measured (both copies list it as NOT measured: BASELINE:563 / RESULTS:540) |

**Addendum tally (25 scored sub-claims): AGREE 20 · DISAGREE 3 (b2 timeout 0.5011, b3 timeout
0.5015, b1 completion 5.1 ms) · mixed / code-verified-not-raw 2 (the three-state "census", which the
raw has only as a case matrix; and the re-resolve-skip mechanism, verified against
`mission_record_service.py:544-567` rather than the raw).**

---

## 8. Bottom line for aggregation

1. **The two copies of the record do not diverge from each other** (RESULTS ≡ BASELINE minus a
   22-line preamble). Both, however, carry the **same 8 score rows that contradict the committed
   raw**: 6 rows with raw-divergent **values** (7 individual values = 3 T5b timings — 5.1 ms /
   0.5011 s / 0.5015 s — + 3 T3 PID values — 2317370 / 2317401 / 2317404 — + 1 T2d2 millisecond —
   3.003 vs 3.002) and 2 rows of **over-stated citations** (T2e's `client_exception`, absent from
   the raw; T4's "byte-exactly" plus the F-A row quoting the *compiled* SQL as the observed
   `last_query`). A silent divergence is not the problem here — a **silent staleness** is.
2. **Flip condition: NOT MET** (worst hold 8011.119 ms ≥ 2000 ms), reproduced by my own arithmetic
   over the raw aggregates. The async path's sub-2 s compliance is an artefact of an *injected*
   fetch tail.
3. **Derivation (a)** (idle-in-txn bound = 4 × max legitimate hold, floor 30 s): **NOT derivable from
   trustworthy data** — the max legitimate hold is model-derived (1059.877 ms) or unidentified (real
   fetch unmeasured); the arithmetic value is 4.24 s so the **30 s floor dominates**, and 30 s
   coincides with the legitimate `pool_timeout` wait. **Verdict: ship DISABLED / re-measure first.**
4. **Derivation (b)** (F7 residence budget): **NOT derivable as specified** — the metric
   (`state_change − xact_start`) is frozen for a leaked backend and therefore **cannot fire on the
   leak**; the 2.0 s floor **would red a healthy daemon** (sync twin holds ≥ 8 s; the real async
   fetch is unmeasured and data-dependent). **Verdict: reject as specified; use `xact_age_s`, make it
   per-call-site, and do not ship 2.0 s.**
5. **Methodological defects that would invalidate a number:** M-7 (residence metric vacuous against
   the leak), M-3/M-4 (T1's decisive numbers are model output), M-1 (no per-iteration series to
   re-derive from), M-9/M-10 (nested checkout wait and λ never measured), M-15 (copies partly
   written against a superseded harness run).
