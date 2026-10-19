# Functional Verification — resource-telemetry PostgreSQL write-path fix

**Date:** 2026-10-19
**Commission:** independently verify the resource-telemetry `estimated` boolean fix (items A–I), no merge/push/promote, scratch DBs only.
**Worktree:** `/home/nea/ensemble-src-wt-resource-telemetry-estimated-bool`
**Branch:** `feature/resource-telemetry-estimated-bool`
**Product code verified:** `69cea24ea` (chain `333b2c225` → `dc47f4680` → `cab8eceee` → `69cea24ea`)
**Branch HEAD at end of verification:** `7b0920021` (item H test-only commit, +2/−0 in one test file; `git diff 69cea24ea..HEAD -- daemon/ frontend/` = EMPTY)
**Base:** `333b2c225` (`latest` at claim time). **NOTE: `latest` has since advanced to `ac1e6454b` (v0.25.3) — branch is behind, see Finding F2.**
**Runner:** worktree `.venv/bin/python` — Python 3.14.7, pytest 9.0.2, psycopg 3.3.4
**Local PG:** PostgreSQL 16.15 @ `127.0.0.1:5432` (scratch DBs/roles only)

---

## VERDICT SUMMARY

| Item | Verdict | One-line evidence |
|---|---|---|
| **A** — original defect reproduced, then dead | **PASS** | old `integer NOT NULL` shape → bind bool = `DatatypeMismatch` SQLSTATE **42804**; real flusher rejected; real `_ensure_postgres_columns()` converts to boolean + defaults; flusher then writes |
| **B** — four-table E2E with POPULATED registry | **PASS** | one `run_once()` → T1=`resource_daemon_bucket` **4**, T2=`resource_instance_sample` **2**, T3=`resource_pid_map` **2**, T4=**0**; T4 honesty: no production writer, proven by direct seed |
| **C** — tier isolation, live mutation | **PASS** | rename `estimated` → SQLSTATE **42703**; T1+T3 rows persist, `errors=1`; restore → writes resume; second case (T1 broken) → 6 failures counted, T2/T3 survive |
| **D** — regression pin on PG booleans | **PASS** | False/True round-trip via flusher, raw SQL, and ORM; `pg_typeof=boolean`; `IS TRUE/IS FALSE/IS NULL = 3/3/0` |
| **E** — fresh SQLite boot vs new migration | **PASS** | new migration NOT the cause (byte-identical control with file deleted); `manual_only=True`, **0 ledger rows**, 0 executable statements; two PRE-EXISTING blockers disclosed |
| **F** — regression suites | **PASS** | **204/204** tests, 0 failures, 0 errors; PG pack **12/12** (14.27s) |
| **G** — frontend/API verification | **PASS** | `/history` 7 points incl. seeded values; `/live` non-empty with `estimated` JSON boolean; real Chromium **22/22** assertions, 0 empty-state, 0 pageerrors |
| **H** — 2-line invariant pins | **PASS** | commit `7b0920021`, +2/−0 one file, `2 passed, 43 deselected in 0.44s` |
| **I** — evidence archived | **PASS** | this file + `/tmp/rt-verify/**` (113 files, ~1.2 MB) |

**ensure.md Core (scoped):** Critical **3/3** (+1 inherited), Important **1/1** (+1 inherited), Nice-to-have **1/1** — all PASS, no contradictions.

**Overall: the defect is DEAD on PostgreSQL. The verify-only gate passes; pre-merge action = rebase/merge `latest` and re-judge the product diff (F2).**

---

## Scope Decision

Change set = 7 files, one subsystem (`resource_monitor` + boot column-ensure + migration + tests). No full-suite run; scoped to the resource family + ensure.md Core concurrency pack + a change-set-specific PG/UI verification. No e2e/release-gate suites (not a cross-module/architecture change). No merge/push/promote performed (per brief).

---

## A — ORIGINAL DEFECT REPRODUCED, THEN DEAD

### A1 — OLD shape created (the live `ensemble_prod` shape)
Fresh tables from the FIXED metadata, then 3-step DDL per column (`DROP DEFAULT` → `TYPE integer USING (col::int)` → `SET NOT NULL`).

`information_schema` after A1:

| column | data_type | column_default | is_nullable |
|---|---|---|---|
| `resource_instance_sample.estimated` | `integer` | NULL | NO |
| `resource_limit_policy.enabled` | `integer` | NULL | NO |
| `resource_limit_policy.user_gated` | `integer` | NULL | NO |

### A3 — RAW reproduction (Python bool bound to the old column)
```
SQLSTATE: 42804
outer:  sqlalchemy.exc.ProgrammingError
orig:   psycopg.errors.DatatypeMismatch
msg:    column "estimated" is of type integer but expression is of type boolean
        HINT:  You will need to rewrite or cast the expression.
```
This is the exact production failure chain.

### A4 — Real flusher against the old shape
`run_once()` with 2 raw samples, 2 instance samples, 2 exited pids:
```
{"hour_rows":1,"instance_rows":0,"minute_rows":1,"rows_written":5,
 "swept":{"raw":0,"minute":0,"hour":0,"instance":0,"pid_map":0}}
counters: errors=1  batches=1  last_window_rows=5  bytes_written=519
log: ERROR ... flusher tier write FAILED — savepoint rolled back, other tiers continue
     (sqlstate=42804, exc=ProgrammingError: (psycopg.errors.DatatypeMismatch) ...)
```
Per-table deltas: `daemon_bucket 0→3`, `instance_sample 2→2 (flusher +0)`, `pid_map 0→2`, `limit_policy 0→0`.

**Amplifier fix proven:** T1/T3 persisted while T2 was rejected; `errors == 1` exactly.
**A4b (table emptied):** `instance_sample` stayed **0→0**, `errors=1`, siblings still wrote. Both sub-cases recorded because A2's seeded rows and "stays empty" cannot both hold in one window.

### A5 — REAL repair path (`_ensure_postgres_columns`, `daemon/manager.py:8219-8427`)
```
INFO _ensure_postgres_columns: resource_monitor boolean repair complete
  (applied=['resource_instance_sample.estimated','resource_limit_policy.enabled',
            'resource_limit_policy.user_gated'], already_ok=[], unrecognised=[], missing=[])
post-repair: estimated   -> ('boolean','true','NO')
             enabled     -> ('boolean','false','NO')
             user_gated  -> ('boolean','true','NO')
seeded old rows preserved: inst-old-0 estimated=False (pg_typeof boolean)
                           inst-old-1 estimated=True  (pg_typeof boolean)
second hook run: applied=[] already_ok=[...=boolean ×3]   (idempotent)
```

### A6 — Flusher on the repaired schema
```
run_once() -> {"hour_rows":1,"instance_rows":2,"minute_rows":1,"rows_written":7,...}
counters: errors=0 batches=1
deltas: daemon_bucket 4→6, instance_sample 2→4 (+2), pid_map 4→6
written: inst-rep-1 estimated=True, inst-rep-2 estimated=False
```

### A7 — Live-window statement
The live `ensemble_prod` historical window is **unrecoverable** — every flush window raised 42804 and rolled back, so the four tables are empty and there is nothing to backfill. The conversion preserves any pre-existing row via `USING (col <> 0)` (proven on A2→A5).
*Honesty caveat: live emptiness was NOT re-read (the hard fence forbids touching `ensemble_prod`, even read-only). The "conversion preserves rows" half IS independently proven.*

---

## B — FOUR-TABLE END-TO-END WITH A POPULATED REGISTRY

Fresh schema; Flusher wired with 2 registered→exited pids, 2 distinct instance ids, 2 raw samples. **One** `run_once()`:
```
{"hour_rows":1,"instance_rows":2,"minute_rows":1,"rows_written":8,
 "swept":{"raw":0,"minute":0,"hour":0,"instance":0,"pid_map":0}}
counters: errors=0 batches=1 last_window_rows=8 bytes_written=1038
```

| table | rows after flush |
|---|---|
| `resource_daemon_bucket` (T1) | **4** (`interval_s`: 30×2, 60×1, 3600×1) |
| `resource_instance_sample` (T2) | **2** (`inst-b-1`, `inst-b-2`) |
| `resource_pid_map` (T3) | **2** (pids `51001`, `51002`) |
| `resource_limit_policy` (T4) | **0** — no flusher writer |

Representative rows:
```
resource_daemon_bucket : {id:1, bucket_ts:"2026-10-19 16:19:07", interval_s:30, rss_kb:1000000,
  pss_kb:900000, anon_kb:600000, swap_kb:0, vsize_kb:2000000, hw_peak_kb:1100000, threads:8,
  fds:256, cpu_user_usec:10000000, cpu_sys_usec:2000000, coverage_pct:95.5, sample_cost_ms:0.5,
  sampler_lag_ms:0.1, missed_samples:0, ...}
resource_instance_sample: {id:1, bucket_ts:"2026-10-19 16:19:07", instance_id:"inst-b-1",
  agent_id:"agent-rt-verify", status:"RUNNING", scan_kind:"counter", charged_bytes:12345,
  graph_cache_present:true, in_flight_tasks:1, all_state_messages_len:3, child_rss_kb:8192,
  child_cpu_user_usec:100000, cpu_share_usec_est:4321, estimated:true}
resource_pid_map : {id:1, pid:51001, start_time:3001, instance_id:"inst-b-1",
  agent_id:"agent-rt-verify", kind:"bash", command:"ls -la /tmp", exit_code:0,
  exit_kind:"exit", peak_rss_kb:4096, cpu_user_usec:1000, ...}
resource_limit_policy: (no rows from the flusher)
```

Column types/defaults (`information_schema`):
- **Fresh `create_all` (post-fix):** `estimated` `('boolean', NULL, 'NO')`; `enabled` `('boolean', NULL, 'NO')`; `user_gated` `('boolean', NULL, 'NO')` — **no server default**.
- **After the repair hook:** `('boolean','true','NO')` / `('boolean','false','NO')` / `('boolean','true','NO')`.

**T4 HONESTY STATEMENT (plain):** `resource_limit_policy` has **no production writer in v1**. Re-confirmed by grep: the only `INSERT INTO resource_limit_policy` statements in the repo are in test files; `daemon/routers/resources.py:1108-1118` documents "T4 stays EMPTY in v1 — no seed rows, no CRUD endpoints"; the boot hook only converts its column types.
→ **Three tables (T1/T2/T3) are proven by the flush path; T4 is proven by direct seed + read-back, not by the flusher.**
Direct seed proof:
```
scope=child_kind target=chart_render metric=cpu_pct op=throttle threshold=80.0 window_s=30
enabled=False (bool) user_gated=True (bool)
pg_typeof(enabled)=boolean pg_typeof(user_gated)=boolean
final counts: daemon_bucket=4 instance_sample=2 pid_map=2 limit_policy=1
```

---

## C — TIER ISOLATION, LIVE MUTATION

### C-1 (primary) — broken tier: T2
`ALTER TABLE resource_instance_sample RENAME COLUMN estimated TO estimated_broken` (a column the T2 write uses):
```
log: ERROR ... tier write FAILED — savepoint rolled back, other tiers continue
     (sqlstate=42703, exc=ProgrammingError: (psycopg.errors.UndefinedColumn)
      column "estimated" of relation "resource_instance_sample" does not exist)
run_once() -> {"hour_rows":1,"instance_rows":0,"minute_rows":1,"rows_written":6,...}
counters: errors=1 batches=1
deltas: daemon_bucket 0→4 (T1 +4), instance_sample 0→0 (T2 +0), pid_map 0→2 (T3 +2)
```
**Restore** (rename back): `run_once() -> {..., "instance_rows":2, "rows_written":5}`; `errors` still 1 (counts failures, not windows), `batches=2`; `instance_sample 0→2`, `daemon_bucket 4→6` ⇒ connection not poisoned.

### C-2 (secondary) — broken tier: T1
`RENAME COLUMN resource_daemon_bucket.interval_s TO interval_s_broken`:
```
3 × tier-write FAILED (42703) + 3 × sweep FAILED (42703)
counters: errors=6 batches=1 last_window_rows=2
deltas: daemon_bucket 6→6 (T1 +0), instance_sample 2→3 (T2 +1), pid_map 2→3 (T3 +1)
```
**Restore:** `errors=6, batches=2`; `daemon_bucket 6→8`, `instance_sample 3→4` ⇒ writes resume.

---

## D — REGRESSION PIN ON PG BOOLEANS

Raw SQL read-back (value, Python type, `pg_typeof`, `estimated::int`):
```
inst-d-true      : True  (bool) boolean 1
inst-d-false     : False (bool) boolean 0
inst-d-raw-true  : True  (bool) boolean 1
inst-d-raw-false : False (bool) boolean 0
inst-d-orm-true  : True  (bool) boolean 1
inst-d-orm-false : False (bool) boolean 0
```
Policy read-back: `scope=daemon → enabled=True, user_gated=True`; `scope=agent → enabled=False, user_gated=False`; `pg_typeof = boolean/boolean`.
ORM: identical values, `type = bool` for all six instance rows and both policy rows.
PG-level: all three columns `data_type=boolean`; `DISTINCT pg_typeof(estimated) = ['boolean']`; `estimated IS TRUE / IS FALSE / IS NULL = (3, 3, 0)`.

---

## E — FRESH SQLITE BOOT + NEW MIGRATION

**Faithful fresh boot** (`create_all` → `run_pending_migrations()`, matching `manager.py:751,756`), new file present:
```
MigrationError: Migration 20260714_000001 failed: (sqlite3.OperationalError) near "EXISTS": syntax error
[SQL: ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type]
53 migrations applied before abort; last ledger row 20260710_000001
```
(Correction to the brief's expected shape: a *truly empty* DB + runner alone fails earlier at `20240101_000001` — `no such table: projects` — because the runner is a migration runner, not a schema bootstrapper. Both cases recorded; the faithful-boot case matches the documented defect.)

**Causality control — PASS:** new file deleted from a copied `versions/` dir → **byte-identical** failure, same exception, identical 53-row ledger prefix. Second control (workaround + new file removed) reproduced the later defect byte-identically (timestamp-normalised). **The new file is demonstrably NOT the cause.**

**Workaround run:** pre-marking `20260714_000001` still failed on a **second, newly-found pre-existing defect**:
```
IntegrityError: (sqlite3.IntegrityError) UNIQUE constraint failed: schema_migrations.version
[parameters: ('20261018_000001', 'secrets audit add context', ...)]
```
Root cause: two pre-existing files share version prefix `20261018_000001` (`create_app_settings.sql` @460b0738c and `secrets_audit_add_context.sql` @116032da5) — both on `latest`, neither added by this branch. With **both** pre-marks: SUCCESS, 95 applied, ledger 97 rows (95 + 2 pre-marked).

**New migration's designed state — exactly as intended:**

| Property | Value |
|---|---|
| `manual_only` | **True** |
| Ledger rows for `20261019_120822` | **0** (no row) |
| In `applied` list | **False** |
| In `pending` list | **True** |
| Executable statements after comment-strip | **0** |
| File | 214 lines / 10,842 B, `MANUAL: TRUE` at line 3 |

**Status summary:** `applied_count=97 · pending_count=5 · total_discovered=103 · last_applied=20261018_090004`; ledger queryable; **no duplicate versions**; 103 files → 101 distinct versions (2 invalid auto-skipped: `20260713_000001_create_skill_bank.sql`, `20260927_000001_create_maintenance_runs_table.sql`).
**Idempotency — PASS:** second run on the completed DB applied 0 migrations, ledger byte-identical (97→97), 0.026s.
**Bonus (branch-correct):** the completed SQLite boot declares `resource_instance_sample.estimated`, `resource_limit_policy.enabled`/`user_gated`, and `graph_cache_present` as **BOOLEAN** — the model fix is live in `create_all`.

Runtime: bare 0.061s; boot 0.909s; control 1.011s; workaround 1.394s; workaround2 1.348s; control2 1.502s; idempotency 0.026s.

---

## F — REGRESSION SUITES (@ `7b0920021`)

Each command run separately with `timeout 300`, `-p no:randomly`, no xdist, no `-x`.

| # | Suite | Passed | Failed | Errors | Skipped | Time | Exact summary line |
|---|---|---|---|---|---|---|---|
| 1 | `tests/postgres/test_resource_monitor_estimated_bool_pg.py` | **12** | 0 | 0 | 0 | 14.27s | `12 passed, 1 warning in 14.27s` |
| 2 | `tests/test_resource_monitor_storage.py` | **45** | 0 | 0 | 0 | 3.47s | `45 passed, 1 warning in 3.47s` |
| 3 | `tests/test_resource_control_seam.py` | **32** | 0 | 0 | 0 | 14.37s | `32 passed, 1 warning in 14.37s` |
| 4 | `tests/test_resource_monitor_sampler.py` | **41** | 0 | 0 | 0 | 1.03s | `41 passed, 1 warning in 1.03s` |
| 5 | `tests/test_resource_monitor_api.py` | **18** | 0 | 0 | 0 | 4.52s | `18 passed, 1 warning in 4.52s` |
| 6 | `tests/test_resource_monitor_self_cost.py` | **8** | 0 | 0 | 0 | 0.46s | `8 passed, 1 warning in 0.46s` |
| 7 | `tests/test_resource_pid_registry.py` (bonus) | **48** | 0 | 0 | 0 | 2.27s | `48 passed, 1 warning in 2.27s` |
| | **TOTAL** | **204** | **0** | **0** | **0** | ~40s | — |

- PG pack: exactly **12 passed**, **0 deselected**.
- Zero failures/errors ⇒ nothing to attribute to the 7 changed files. The two known pre-existing files (`test_migration_api_comprehensive.py`, `test_report_deferred_migration_pg.py:321`) were not run.
- **Methodology note (finding F3):** the prescribed `POSTGRES_DB=ensemble_rt_scratch_f` trips a pre-existing self-fence in the six unit packs (`pytest.exit`, 0 tests executed). Prescribed aborts captured verbatim, then a labeled diagnostic pass with `POSTGRES_DB` **unset** (suites self-rewrite to a fictional in-process DB; no real connection) produced the green rows. Pack 1 is immune (reads `PG_TEST_*`).

---

## G — FRONTEND + API VERIFICATION

**Stack:** worktree daemon (`python -m daemon`, PID 627015, port 8079) against scratch `ensemble_rt_scratch_fe`; FE served from the worktree (`ng serve --port 14202`, node_modules temporarily symlinked to the main repo, removed after); real Chromium via Playwright 1.60.0, **no `/api/**` stubbing**.
Boot log: `_ensure_postgres_columns: resource_monitor boolean repair complete (applied=[], already_ok=[...=boolean ×3])` + `resource-monitor started: sample_s=30 flush_s=60`.

**Seeded:** 5 `resource_daemon_bucket` (rss 128→132 MB, cgroup 256 MB, threads 4242→4246), 4 `resource_instance_sample` (3 distinct ids, `estimated` f/t/f/t), 3 `resource_pid_map` (pids 900001-900003). Final counts **34 / 9 / 3** (seeded + the daemon's own monitor rows).

**API — PASS**
- `GET /api/resources/history?window=1h&resolution=raw` → 200, **7 points**; seeded values verbatim:
  `{"t":"2026-10-19 16:11:45","daemon_rss_mb":128,"cgroup_mem_mb":256,"threads":4242,"instances_active":0}`,
  `{"t":"2026-10-19 16:15:45","daemon_rss_mb":132,"cgroup_mem_mb":256,"threads":4246,"instances_active":2}`.
- `GET /api/resources/live` → 200 non-empty: daemon `pid=627015, rss_mb=329.26, threads=30`; `instances[0].estimated` is a **real JSON boolean**; `self_cost.flush = {batches:14, bytes:16312, errors:0}`.
- `GET /api/resources/self-cost` → 200, `flush.errors=0`.
- `GET /api/resources/traceback?t0=…&t1=…` → 200, no degraded header, seeded pids/instance ids present.
- DB level: columns `boolean`; `pg_typeof(estimated)=boolean` on every row; flusher wrote real rows (`estimated=f`, `charged_bytes=32872`, `deep_bytes=62043`).

**UI — PASS (22/22 assertions, 0 errors)**
Live tab: `live-content=1`, `live-error=0`, `live-skeleton=0`; daemon PID **627015** in DOM == port owner; `stat-daemon-rss = "330 MB"` (not `—`); self-cost chip shows 30 samples.
Timeline tab: `timeline-empty=0`, `timeline-error=0`, `timeline-skeleton=0`; 3 `series-line` with non-empty `d`; first tick **"16:11"**; legend ranges `daemon_rss_mb "128–329.3"`, `cgroup_mem_mb "256–8,824"`, `instances_active "0–2"`; `instances_active` path non-degenerate (distinct y = 288/164/40); browser-received history JSON contains the seeded points.
Screenshots (pixel-verified non-blank, 99.98% non-white): `/tmp/rt-verify/g/resources-view.png`, `/tmp/rt-verify/g/resources-live-tab.png`.
Error hygiene: 0 console errors from `/api/resources/*`, 0 pageerrors. Unrelated noise: `/api/prompts/pending-count` 503 `AUTH_NOT_CONFIGURED` (auth disabled in dev) and a Plane iframe CSP violation.

**Disclosures:** `/live` pid/rss/threads are transiently null ~33% of frames (ring drained every 60s, refilled every 30s) — pre-existing, not a defect; `/live.instances` is empty until an instance exists (sampler universe = graph-cache/charge registry); `estimated` is not rendered anywhere in the FE (only `/live` serializes it); HEAD moved mid-run (`69cea24ea → 7b0920021`) with empty `daemon/`+`frontend/` diff.

---

## H — INVARIANT PINS (test-only commit)

```diff
@@ -1189,6 +1189,7 @@ class TestRelationMissingTolerance:
         assert f._tier_write(conn, "INSERT INTO x VALUES (1)", [{}]) == 0
         assert f._missing_table_warned is True
+        assert f.counters.errors == 0
@@ -1233,6 +1234,7 @@ class TestRelationMissingTolerance:
         assert f._sweep(conn, "resource_pid_map", 12345) == 0
+        assert f.counters.errors == 0
```
- Commit **`7b0920021`**, `1 file changed, 2 insertions(+)`, parent `69cea24ea`, tree clean afterwards.
- Run: `2 passed, 43 deselected, 1 warning in 0.44s`.
- Load-bearing: `self._flusher()` initializes a fresh `FlusherCounters()`; both missing-relation early-returns (`flusher.py` ~840 / ~646) precede the `errors += 1` lines (879 / 668). A future refactor hoisting the increment would fail both pins.

---

## ensure.md CORE VALIDATION (scoped)

| Requirement | Priority | Verdict | Evidence |
|---|---|---|---|
| No regressions in changed packs | Critical | **PASS** | 204/204 across the change-set packs |
| Deadlock / concurrency integrity (`concurrency_atomic_unit_test`) | Critical | **PASS** | `99 passed, 74 skipped, 58 warnings in 82.06s`; `RESULT: PASS`; baseline-exact (99P/0F/74S) |
| No sync DB calls on event loop | Critical | **PASS** (inherited) | `TestProcessChildCompletionDbSyncOffloaded` both tests green |
| `dev.sh` has `--timeout-graceful-shutdown 10` | Critical | **PASS** | `dev.sh:142` flag with value 10 |
| Converted-async callers awaited | Important | **PASS** | 0 non-awaited call sites across daemon/ |
| Original deadlock scenario | Important | **PASS** (inherited) | concurrency pack |
| No dead code from the fix | Nice-to-have | **PASS** | old `raise`s at base 594/760 replaced by log+counter+return 0; no dead blocks |

- **ensure.md contradictions: NONE** — every in-scope method is pack-mapped/static, dual-layer timeout, no `-x`, blast-radius scoped.
- PACKS.md integrity: `test/packs/concurrency_atomic_unit_test.sh` present, executable, registered; baseline reproduced exactly.
- Wrapper artifact disclosed: the pack exit_code showed 2 due to a dash `${PIPESTATUS[0]}` bash-ism in the dispatcher wrapper; the pack's `RESULT: PASS` + `99 passed` are authoritative.

---

## FINDINGS / DISCLOSURES (not blockers for A–H)

**F1 — NEW pre-existing defect: duplicate migration version prefix.** `20261018_000001` is used by both `create_app_settings.sql` and `secrets_audit_add_context.sql` (both on `latest`). A fresh SQLite boot that gets past `20260714_000001` then dies with `UNIQUE constraint failed: schema_migrations.version`. The documented workaround (critical note) covers only `20260714_000001`. **Needs a fix commission** (rename one file to a unique prefix; audit the other duplicate prefixes: `20260628_000002` is harmless — both files are MANUAL).

**F2 — Branch is behind `latest`.** `latest` = `ac1e6454b` (v0.25.3); branch merge-base = `333b2c225`. A merge would drag older `daemon/api.py`, `daemon/manager.py`, `CHANGELOG.md`, `pyproject.toml`, `uv.lock`. **Action before judging the product diff: rebase/merge `latest`, re-verify the 4-file release-bump set.** Not done here (brief forbade merge/push).

**F3 — Unit-pack `POSTGRES_DB` self-fence (pre-existing).** Six resource suites hard-abort when `POSTGRES_DB` is any value outside `{ensemble_dev_test_only, ensemble_test_only}`; absent/empty/prod is rewritten in-process. For scoped pack runs, **unset `POSTGRES_DB`** rather than pointing it at a scratch DB. (Not a product defect.)

**F4 — Fresh-DB server-default asymmetry.** A fresh post-fix PG schema has `boolean NOT NULL` with `column_default = NULL` (`Field(default=…)` is a Python-side ORM default). The `SET DEFAULT true/false/true` step fires only on the integer→boolean repair path; `already_ok` short-circuits on fresh DBs. No functional impact (the flusher binds explicitly; ORM supplies the default); documented in the migration file. Documentation-level only.

**F5 — `/live` transient + `estimated` not rendered (informational).** See G disclosures.

**F6 — Hygiene.** `/tmp/rt-verify/ab/.rolepw` is a stale plaintext credential for an already-dropped role (mode 600). `ensemble_resource_fix_test` DB pre-exists and is not ours (not dropped). Several hundred `ensemble_promptver_test_*` / `ensemble_blob_prune_*` DBs are pre-existing litter.

**F7 — A4 wording conflict resolved by running both variants** (seeded-rows and emptied-table); both recorded.

---

## GUARDRAILS / CLEANUP (independently re-verified)

- Scratch DBs `ensemble_rt_scratch_ab`, `ensemble_rt_scratch_f`, `ensemble_rt_scratch_fe` + roles `rt_verify_ab/f/fe`: **0 rows in `pg_database`/`pg_roles`**; TCP logins fail.
- Ports **8079 / 14202 / 4199 not listening**; **9797 untouched** — `ensemble-prod` pid 131667, `/livez` 200, etime 6h36m (no restart); **8088 never touched**.
- Worktree clean at `7b0920021`; `git diff 69cea24ea..HEAD -- daemon/ frontend/` EMPTY; `frontend/node_modules` absent; branch **not pushed**; **no new tags**.
- Live symlink still `releases/v0.25.1` (mtime 09:58) — **no promote**. v0.25.2/v0.25.3 exist staged on disk only.
- Network fence (item E): every child reported zero non-loopback connect attempts.
- No product code modified. Only commit = the item-H test-only pin.

## EVIDENCE PATHS

| Path | Content |
|---|---|
| `/tmp/rt-verify/ab/` (596K) | item A–D drivers + logs (`A_*`, `B_*`, `C_*`, `D_*`), repo-pack corroboration, consolidated clean-slate run, teardown |
| `/tmp/rt-verify/e/` (212K) | SQLite boot harness (`harness*.py`, `driver*.sh`, `logs*/`) |
| `/tmp/rt-verify/f/` (56K) | 7 suite logs + `*-DIAG-nodb.log` per self-fence abort |
| `/tmp/rt-verify/g/` (392K) | daemon log, raw API JSONs, `ui-report.json`, `rendered-text.json`, screenshots, seed script |
| `/tmp/rt-verify/ensure/` | `concurrency_atomic.log` |

Total: 113 files, ~1.2 MB. Scratch DB/role names and all commands are recorded inline above.


---

# R2 — INTEGRATED-TREE RE-VERIFICATION @ `8d7c33884` (2026-10-19)

**Context:** the branch was behind `latest`; `origin/latest` (`ac1e6454b`, v0.25.3) was merged in with a true `--no-ff` merge. Parents: `3bab2790b` (docs) + `ac1e6454b`. Only conflict: `.agents/tester/PACKS.md` (keep-both/newest-first). Zero product-code conflicts. `latest` later moved to `5b524cf17` (docs/test-evidence only, deferred to final integration).

| R2 item | Verdict | Evidence |
|---|---|---|
| 1. Payload intact after merge | **PASS 7/7** | restricted diff `ac1e6454b..HEAD -- daemon/ tests/ frontend/` = exactly the 7 expected files (+1995/−47); no `latest` leak |
| 2. Resource family | **PASS** | PG pack 12, storage 45, control_seam 32, sampler 41, api 18, self_cost 8 |
| 3. Merged-in sibling suites | **PASS** | `test_question_pack_boot_rehydrate.py` 7/7; `test_boot_orphan_sweep.py` 32/32 |
| 4. Boot smoke (merged code) | **PASS** | `/livez` 200 @23.15s (v0.25.3), `/readyz` 200 (database/queue/services/checkpointer all true); ensure line `applied=[], already_ok=[3× boolean]`; monitor started; zero 42804 |

## R2-1 — Payload audit detail
- Merge identity: `8d7c3388496d215aefa01c57ba6a7c788c7c7e1d`, parents `3bab2790b779…` + `ac1e6454bad…`; `merge-base --is-ancestor ac1e6454b HEAD` rc=0; tree clean.
- Restricted diff paths: `daemon/manager.py` M, `daemon/models/resource_monitor.py` M, `daemon/services/resource_monitor/flusher.py` M, `20261019_120822_...sql` A, `tests/postgres/test_resource_monitor_estimated_bool_pg.py` A, `tests/test_resource_control_seam.py` M, `tests/test_resource_monitor_storage.py` M.
- Nothing lost: `git diff 3bab2790b..HEAD` **empty** for the 6 files `latest` did not touch.
- Nothing duplicated: `manager.py` delta vs `3bab2790b` is **only** the question-pack rehydrate move (20+/20−, call moved to `daemon.api._boot_rehydrate_question_packs` after `setup_worker_pool()`), not inside `_ensure_postgres_columns`. The 221-line boolean block is **byte-identical** between `3bab2790b` and HEAD (`sha256 93d92d646d52c364499025cfebd9f45d8f8356d93f6ef6359d3908e95ec56566`). 3 column tuples + the success log string each appear exactly once; +3 `ALTER COLUMN` lines, 0 removed.
- `py_compile` rc=0 on manager/model/flusher; zero real conflict markers (strict anchored grep).
- PACKS.md: our section exactly once at line 3; latest sections restored below (keep-both correct).

## R2-2/3 — Suites detail
Each run: separate `timeout 300`, `-p no:randomly`, no xdist, no `-x`; PG pack on scratch `ensemble_rt_scratch_r2`; unit suites with `env -u POSTGRES_DB`.

| Suite | Passed | Failed | Errors | Skipped | Exact summary |
|---|---|---|---|---|---|
| `tests/postgres/test_resource_monitor_estimated_bool_pg.py` | 12 | 0 | 0 | 0 | `12 passed, 1 warning in 14.98s` |
| `tests/test_resource_monitor_storage.py` | 45 | 0 | 0 | 0 | `45 passed, 1 warning in 3.48s` |
| `tests/test_resource_control_seam.py` | 32 | 0 | 0 | 0 | `32 passed, 1 warning in 15.03s` |
| `tests/test_resource_monitor_sampler.py` | 41 | 0 | 0 | 0 | `41 passed, 1 warning in 0.77s` |
| `tests/test_resource_monitor_api.py` | 18 | 0 | 0 | 0 | `18 passed, 1 warning in 4.89s` |
| `tests/test_resource_monitor_self_cost.py` | 8 | 0 | 0 | 0 | `8 passed, 1 warning in 0.60s` |
| `tests/test_question_pack_boot_rehydrate.py` (merged-in) | 7 | 0 | 0 | 0 | `7 passed, 1 warning in 5.14s` |
| `tests/unit/services/test_boot_orphan_sweep.py` (merged-in) | 32 | 0 | 0 | 0 | `32 passed, 1 warning in 53.63s` |
| **TOTAL** | **195** | **0** | **0** | **0** | — |

No failures/skips ⇒ nothing to attribute; no A/B against pre-merge needed. Scratch `ensemble_rt_scratch_r2` + role dropped (0/0); sibling boot DB untouched.

## R2-4 — Boot smoke detail
- Daemon: worktree `python -m daemon`, PID 939236, port 8079, env fence `POSTGRES_DB=ensemble_rt_scratch_boot` / `POSTGRES_USER=rt_verify_boot` / `DAEMON_PORT=8079` / `ENSEMBLE_SELF_ENV=dev`.
- `/livez` → 200, `{"status":"alive","version":"0.25.3"}`, first success t=23.15s (uptime 5.49s). `/readyz` → 200, `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"draining":false}`. Re-polled at 18.5 min: still 200.
- Ensure line: `_ensure_postgres_columns: resource_monitor boolean repair complete (applied=[], already_ok=['resource_instance_sample.estimated=boolean', 'resource_limit_policy.enabled=boolean', 'resource_limit_policy.user_gated=boolean'], unrecognised=[], missing=[])`.
- Merged boot passes ran: `AutoContinue boot pass: ContinueResult(candidates=0 … errors=0)`; `[BOOT_CONTINUE] orphan-sweep summary: … errors=0`; `question-packs rehydrated: 0`.
- Monitor start: `resource-monitor started: sample_s=30 flush_s=60 retention=(2880, 336, 180)`.
- Forbidden patterns absent: `DatatypeMismatch` 0, `42804` 0, ensure-error 0, ALTER failure 0.
- DB: columns `boolean`; `resource_daemon_bucket` 35→37 (live writes); T2/T3 0 (no instances yet); T4 0 by design; rollback-scoped policy insert stored `t`/`t` then rolled back.
- Connections: 6 backends, all `ensemble_rt_scratch_boot`; zero to any other DB.
- Cleanup: graceful SIGTERM (port-owner re-verified) exit 2s; 8079 free; scratch DB/role 0/0; fence HOME removed; tree clean; 9797 untouched.
- **Scope caveat:** fresh DB → `create_all` already produced booleans, so the INTEGER→BOOLEAN **conversion branch was not exercised** in this smoke. It is proven separately by R1 item A on a genuine legacy integer fixture.
- **Pre-existing noise:** 25 MCP ERROR lines (`libXtst.so.6` missing → computer-use; plane connection closed). Not merge- or fix-caused; branch diff has zero files under `daemon/mcp/**` or MCP services.
- **New informational (display-only):** checkpointer log reads `Creating PostgreSQL checkpointer for localhost:5432/ensemble` from raw config (`daemon/persistence.py:221-225`) while `pg_stat_activity` proves the actual connection is the scratch DB. Mislabelled log line, no functional impact.

## R2 verdict
**No merge-caused regressions. The integrated tree `8d7c33884` is green: payload byte-intact, 195/195 suites, boot healthy with both health endpoints 200 and the resource-monitor ensure/monitor intact.**
