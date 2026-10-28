# expected-collected.md — the INDEPENDENT collected-count baseline (DR-23)

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`) · worktree `/home/nea/ensemble-src-wt-dbpool-idletxn`
- **Purpose:** the **independent declaration** of the expected collected count for every gate pack, so that "0 failed" is never vacuous and the whole-tree legs are **asserted**, not **recorded**.
- **Authority:** `final-implementation-spec.md` §4.3 (the rule) and §6.2a (the ladder rows that read this file); `test-strategy.md` §2.3 (the fence script that reads this file).
- **Base the counts are measured at:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87`
- **Status:** **TBD — NOT MEASURED.** Task **R-1** is routed to the tester lane.

---

## Why this file exists

The C3 false-green class: a deselected or skipped run reports "0 failed" and is indistinguishable from a pass. The house rule (`requirements.md:1133`, `:1169-1172`) forbids a gate from reporting "0 failed" without also proving a non-zero collected count **equal to the pack's declared count**.

The pre-fold rule was *"each pack declares its count as a module-level constant **inside the pack**"*. That degrades to **"recorded"** for the pre-existing corpus: the Class-4a/4b DO-NOT-TOUCH packs declare no constant and **must not be modified**, and the whole-tree legs must "reconcile `base_collected` / `head_collected` against the sum of the declared `EXPECTED_COLLECTED_*`" — again with no declaration for the pre-existing corpus. With no independent baseline, `EXPECTED` is filled in from the current run ⇒ the assertion is a **tautology** (the exact C-1 failure mode).

**This file is that independent baseline.** The fence reads it; the pack-local `EXPECTED_COLLECTED` constant applies to **NEW packs only**.

## How to fill it in (task R-1)

For each row: check out the base commit `19fc11c8b` in a clean worktree, run the pack's command **with the full Class-1 fence** (see `test-strategy.md` §2.2 — the `cd` is load-bearing; the certified interpreter is `/home/nea/ensemble-src/.venv/bin/python`), and record the `N tests collected` value the `--collect-only -q` summary prints.

```
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
/home/nea/ensemble-src/.venv/bin/python -m pytest <paths> --collect-only -q
```

**Rules for the measurer:**
- Record the number **as collected at base** — do **not** round, and do **not** substitute a number from another worktree or another interpreter.
- A PG/integration pack's count must be measured with its marker override (`--override-ini="addopts=" -m postgres|integration`) **and** with PG reachable; if the pack cannot be collected at base, record `NOT-COLLECTABLE-AT-BASE` and the reason — **never** invent a number.
- The whole-tree row (G1.5 / G3.5) is the **total default-collected corpus count at base**, and the sum of the pack rows must be reconciled against it; a mismatch is a finding to classify, not a number to fudge.
- **No number in this file may be invented, estimated, or copied from a run at a different commit.**

## The table

**All counts are `TBD (R-1)`.** The pack file names are the authoritative ones from `final-implementation-spec.md` §5.1 — they are **not** guesses.

| # | Pack file | Class | Ladder row(s) | Expected collected at base `19fc11c8b` |
|---|---|---|---|---|
| 1 | `tests/unit/repositories/test_pg_engine_utc_session.py` | default (EXISTING) | G2.1, G2.4 | `TBD (R-1)` |
| 2 | `tests/unit/tools/test_dbpool_pool_attribution.py` | default (NEW) | G2.1 | `TBD (R-1)` |
| 3 | `tests/unit/tools/test_dbpool_repair_engine_isolation.py` | default (NEW) | G2.2 | `TBD (R-1)` |
| 4 | `tests/unit/tools/test_dbpool_p2_immutability.py` | default (NEW) | G2.3 | `TBD (R-1)` |
| 5 | `tests/unit/tools/test_dbpool_honesty_blocks.py` | default (NEW) | G2.1 | `TBD (R-1)` |
| 6 | `tests/unit/tools/test_dbpool_txn_hygiene.py` | default (NEW) | G1.1 | `TBD (R-1)` |
| 7 | `tests/unit/tools/test_dbpool_probe_bound.py` | default (NEW) | G1.4 | `TBD (R-1)` |
| 8 | `tests/unit/tools/test_dbpool_probe_failure_class.py` | default (NEW) | G1.4 | `TBD (R-1)` |
| 9 | `tests/unit/tools/test_readyz_grace_detail.py` | default (EXISTING) | G1.4 | `TBD (R-1)` |
| 10 | `tests/unit/tools/test_dbpool_mission_mint.py` | default (NEW) | G1.2 | `TBD (R-1)` |
| 11 | `tests/unit/tools/test_dbpool_wedge_capture_static.py` | default (NEW) | G1.3 | `TBD (R-1)` |
| 12 | `tests/unit/tools/test_dbpool_probe_retention_static.py` | default (NEW) | G3.3 | `TBD (R-1)` |
| 13 | `tests/unit/tools/test_dbpool_occupancy_static.py` | default (NEW) | G3.3 | `TBD (R-1)` |
| 14 | `tests/unit/tools/test_dbpool_residence_static.py` | default (NEW) | G3.2, G3.3 | `TBD (R-1)` |
| 15 | `tests/unit/tools/test_dbpool_guard_pack_selfcheck.py` | default (NEW) | G3.2 | `TBD (R-1)` |
| 16 | `tests/unit/tools/test_dbpool_fix_site_static.py` | default (NEW) | G1.4 | `TBD (R-1)` |
| 17 | `tests/unit/tools/test_dbpool_census_families.py` | default (NEW) | G3.4 | `TBD (R-1)` |
| 18 | `tests/unit/tools/test_dbpool_consistency_pin.py` | default (NEW) | G1.4 | `TBD (R-1)` |
| 19 | `tests/unit/tools/test_dbpool_audit_record.py` | default (NEW) | — (F1-a) | `TBD (R-1)` |
| 20 | `tests/postgres/test_dbpool_mission_mint_pg.py` | postgres (NEW) | G2.5 | `TBD (R-1)` |
| 21 | `tests/postgres/test_dbpool_probe_starvation_pg.py` | postgres (NEW) | G2.5 | `TBD (R-1)` |
| 22 | `tests/postgres/test_dbpool_wedge_capture_pg.py` | postgres (NEW) | G2.6 | `TBD (R-1)` |
| 23 | `tests/integration/test_dbpool_probe_bound_pg.py` | integration (NEW) | G2.5 | `TBD (R-1)` |
| 24 | `tests/integration/test_db_pool_idle_in_transaction_guard.py` | integration (NEW) | G3.2 | `TBD (R-1)` |
| 25 | `tests/integration/test_dbpool_churn_census.py` | integration (NEW) | G3.1 | `TBD (R-1)` |
| 26 | `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` | integration (NEW) | G2.5 | `TBD (R-1)` |
| 27 | `tests/unit/tools/test_readiness_failure_class.py` | default (EXISTING, DO-NOT-TOUCH) | Class 4a | `TBD (R-1)` |
| 28 | `tests/unit/tools/test_readyz_grace_detail.py` | default (EXISTING, DO-NOT-TOUCH) | Class 4a / G1.4 | `TBD (R-1)` *(same file as row 9 — one measurement, two consumers)* |
| 29 | `tests/unit/tools/test_saver_probe_contention_class.py` | default (EXISTING, DO-NOT-TOUCH) | Class 4a | `TBD (R-1)` |
| 30 | `tests/unit/tools/test_promote_txn_hold.py` | default (EXISTING, DO-NOT-TOUCH) | Class 4a | `TBD (R-1)` |
| 31 | `tests/unit/tools/test_checkpoint_pool_config.py` | default (EXISTING, DO-NOT-TOUCH) | Class 4a | `TBD (R-1)` |
| 32 | `tests/unit/test_readiness_mcp_independence.py` | default (EXISTING, DO-NOT-TOUCH) | Class 4a | `TBD (R-1)` |
| 33 | `tests/integration/test_checkpoint_pool_burst_shapes.py` | integration (EXISTING, DO-NOT-TOUCH) | Class 4b | `TBD (R-1)` |
| 34 | `tests/integration/test_checkpoint_pool_ghost_waiters.py` | integration (EXISTING, DO-NOT-TOUCH) | Class 4b | `TBD (R-1)` |
| 35 | **whole-tree default-collected corpus** | default | **G1.5 / G3.5** | `TBD (R-1)` — the total `base_collected`; reconcile against the sum of rows 1–34 |

**The whole-tree reconciliation (G1.5 / G3.5):** `base_collected` (row 35) must equal the sum of the declared `EXPECTED_COLLECTED_*` for the default-collected corpus (rows 1–19 + 27–32, de-duplicated — row 28 is the same file as row 9 and counts once). A **mismatch is FATAL**, not a rounding note.

## Also required by this artifact's role

- The **zero-skips** assertion is separate from the collected count and is **not** in this file: `tests/postgres/conftest.py:167-169` skips the whole module when PostgreSQL is unreachable, so every PG/integration gate additionally asserts `skipped == 0`.
- The **G0** row's non-vacuity check is not a collected count either — it asserts that `BASELINE.md` exists, is reviewed, and that **both** provisional literals (`MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` and the residence budget) are resolved by measurement **or** recorded DISABLED.

---

*Created by the fold round-2 part-B worker (contract + test-strategy re-base). **No number in this file is invented** — every count cell is `TBD (R-1)` pending the tester's base measurement.*
