# Soak harness — OrphanWatcher engine pilot (48 h window)

**Script:** `scripts/soak/engine_pilot_orphan_soak.py` · **Boot-SLO producer:** `scripts/measure/engine_pilot_boot_slo.sh`

## The clock

The **48-hour window starts at the Emperor's flip ceremony** — at the moment
the flag is set and the daemon restarts. This harness builds and validates
the **machinery**; it does not start the clock and it does not flip anything.

## READ-ONLY CONTRACT (non-negotiable)

* The DSN **must** use a strictly read-only role — in production the
  `ensemble_prod_ro` role. **Never** the daemon's write credentials.
* **ENFORCEMENT (F1, review #5).** The engine is opened with
  `connect_args={"options": "-c default_transaction_read_only=on"}` — a
  **connection-startup option** the server applies *before any transaction
  starts*, so every transaction is read-only and a write is refused by
  PostgreSQL itself. (An earlier implementation issued the same `SET` from a
  `connect` event hook; under psycopg3 that is defeated by the driver's
  implicit `BEGIN` ordering and the guard was **INERT** — the reviewer's
  probe returned "WRITE ALLOWED". Probe transcript:
  `.agents/tester/RESULTS/2026-11-23-engine-pilot-ro-guard-probe.txt`,
  which reproduces BEFORE (WRITE ALLOWED) and AFTER (WRITE REFUSED) on the
  same host.)
* **ROLE ASSERTION (F2, review #6).** After connecting, the harness reads
  `SELECT current_user` and **refuses loudly** when the target database is
  prod-shaped (its name contains `prod`) and the connected role is not
  read-only-shaped (`*_ro` / `ro` / `readonly`). A write-capable
  production role is never silently accepted. The asserted role is
  reported in `role_assertion`.
* **No** DDL, **no** temp tables, **no** LOCKs, **no** INSERT/UPDATE/DELETE,
  **no** flag flips, **no** restarts, **no** writes to the live install.
* Dependencies: the standard library + SQLAlchemy (the repo's own client) —
  it runs from the repo venv on the live host.

## Evidence sources

The engine publishes **no events** (registry→engine is one-way), so the
database and the log are the only evidence:

| Source | What it carries |
|---|---|
| `engine_runs` | append-only per-dispatch rows: `outcome`, `skip_reason`, `started_at`, `finished_at`, `duration_seconds`, `error_class` |
| `engine_jobs` | the mutable job row: `last_run_at`, `last_outcome`, `dead`, `interval_seconds`, `max_runtime_s`, `description` |
| `dependency_watchers` | the swept-over table (functional proxy) |
| daemon log | the **engine boot posture line** (ownership evidence: `executable_specs=`) and the **boot epoch** line |
| boot telemetry | JSONL from the D6 script: `boot_to_readyz_seconds`, `block_elapsed_ms_gated` |

### The ambiguity you must resolve with the posture line

`SKIPPED_SHADOW` **cannot** distinguish "the engine is in shadow" from "there
is no executor attached" (`execution.py:438-440`). Resolve ownership with
the **posture line** (`executable_specs=N (job-ids)`) **plus** the
`SUCCEEDED` rows — never with the skip stamp alone. (Measured: at the
shipped posture the orphan job stamps `SKIPPED_SHADOW` exactly like the
other 11 observer specs.)

## The verdict rule (exact)

Exactly one verdict is emitted:

| Verdict | When |
|---|---|
| **VIOLATION** | at least one rule fired. **A violation with partial data is still a violation** — it dominates. |
| **INCONCLUSIVE** | no rule fired, but the **sufficiency gate** did not pass: the window does not contain enough evidence to judge. The report names exactly which evidence is missing. |
| **PASS** | every rule had data and every rule passed. |

> **MISSING DATA ⇒ INCONCLUSIVE, NEVER A SILENT PASS.**

Exit codes: `0` PASS · `3` VIOLATION · `4` INCONCLUSIVE (`--warn-only`
forces 0 while still reporting the verdict).

## ⚠ PRE-PROMOTE DEGRADATION (F4, review #9)

**The R7 ownership rule needs this lane's `executable_specs=` field in the
daemon's boot posture line.** A **pre-promote** daemon (an older binary
without the field) yields a posture line with no executable count ⇒ R7 has
no ownership evidence and degrades to **INCONCLUSIVE** — it can never
produce a false **VIOLATION**.

⇒ **Run the soak harness only against the FLIPPED release** (the one
carrying `executable_specs=`). Pointed at anything older, expect
**INCONCLUSIVE**, not a verdict. (The harness reports the shape it saw:
`pre-promote posture line (no executable_specs field)`.)

## The rules

| Rule | What it detects | Verdict when it fires |
|---|---|---|
| **R1 sufficiency** | too little evidence: fewer than 2 rows, or the rows span less than 2 intervals, or the density inside the covered span is below half the expected density | INCONCLUSIVE |
| **R2 cadence** | a **wedge/stall**: the gap between consecutive executions exceeds `interval × 2 + grace` (tolerance: ONE missed tick) | VIOLATION |
| **R3 stuck run** | a run with `started_at` and no `finished_at` older than `max_runtime` | VIOLATION |
| **R4 overlap** | two runs whose execution windows **overlap** — the sweep ran twice concurrently (double execution) | VIOLATION |
| **R5 outcome** | `FAILED_BACKOFF` / `TIMED_OUT` / `TIMEOUT_PENDING_EXTEND` on the pilot job | VIOLATION |
| **R6 dead flag** | `engine_jobs.dead = 1` — the CS-9 observer carve-out fired on a live lane (a **false dead**) | VIOLATION |
| **R7 ownership** | **the parity/ownership detector**: executions observed while the boot posture says `executable_specs=0` (or the flag is not set) ⇒ the engine ran a sweep the ownership contract says it must not run | VIOLATION |
| **R8 functional** | the sweep stopped doing its job: PENDING `dependency_watchers` older than the grace accumulate past the bound | VIOLATION |
| **R9 boot-SLO** | a boot that exceeded the Emperor's 120 s boot SLO. A boot with **no measurement** is INCONCLUSIVE (never a silent pass on that boot) | VIOLATION / INCONCLUSIVE |

**Window bounding.** `--window-hours` (default 48) bounds the query
(`started_at BETWEEN start AND end`). The window's **start** is the
ceremony time; the harness never widens it and never reads outside it
except for the boot-epoch context lines.

## Usage

```bash
# 1. produce the boot measurements (one JSONL record per boot)
bash scripts/measure/engine_pilot_boot_slo.sh --release /path/to/release-dir \
     --out boot-measurements.jsonl

# 2. judge the window (READ-ONLY)
scripts/soak/engine_pilot_orphan_soak.py \
    --dsn "postgresql+psycopg://ensemble_prod_ro:***@127.0.0.1:5432/ensemble_prod" \
    --window-hours 48 \
    --log-dir /var/log/ensemble \
    --boot-telemetry boot-measurements.jsonl \
    --json-out soak-report.json
```

Re-run it during the window (the report is a point-in-time judgement of the
window that has elapsed). The **final** judgement is the one over the full
48 h.

## Validated

Against the fenced dev DB (`ensemble_dev`), the harness returns
**INCONCLUSIVE** — correctly: the dev DB has no continuous engine evidence.
R3/R4/R5/R6/R8/R9 PASS; R1/R2 INCONCLUSIVE with the missing evidence named
(`"engine_runs density for orphan-watcher inside the covered span: 49 rows
vs ~383 expected (covered 69096s)"`).
