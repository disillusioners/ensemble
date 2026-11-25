# Runbook — Dump-Lane Health Probe (nightly `pg_dumpall` lane)

**What this is.** A READ-ONLY operator probe that tells you when the nightly
`pg_dumpall` backup lane last **succeeded**. It is the only signal that the dump lane
is alive — the daemon cannot see kubernetes, so a stopped or failing backup CronJob
otherwise goes unnoticed for weeks.

**Who runs it.** The weekly central-maintenance run (the maintenancer lane's weekly
chain) and, ad-hoc, any operator. See §6 — the wiring into that chain is an
operator step that is **not** implemented by the tool's author.

**Design + rationale:** `.agents/shared/planning/sonic-team/feeder-grants-dump-health/dump-lane-health-design.md`.

**Fences.** The probe never writes to any database, never writes any file, never guesses
a connection, and never reports "healthy" when it could not establish health.

---

## 1. The one command

Run from the repo checkout (the script path is `scripts/audit/dump_lane_staleness.py`):

```bash
cd /home/nea/ensemble-src

# Password-free DSN + libpq reads PGPASSWORD / ~/.pgpass.
# (Prefer this over a password on the command line — `ps` is world-readable.)
PGPASSWORD='…' .venv/bin/python scripts/audit/dump_lane_staleness.py \
    --db-url "postgresql+psycopg://ensemble@10.44.0.2:5432/ensemble_prod"
```

Exit code `0` ⇒ the lane is healthy. Anything else ⇒ read §5.

Useful variants:

```bash
# Override the staleness threshold (default: 8 days = 691200 s).
… --max-age-hours 192

# The immediately-usable fallback — a marker/artifact file, no cluster change needed.
.venv/bin/python scripts/audit/dump_lane_staleness.py \
    --provider file --marker-file /var/lib/ensemble/dump-marker

# Replay a specific evaluation instant (for a drill). The RESULT line always records
# the instant actually used. Do NOT pass this from the weekly chain.
.venv/bin/python scripts/audit/dump_lane_staleness.py --db-url "$DSN" \
    --now 2026-11-25T00:00:00Z
```

**What you get.**

* **stdout** — exactly ONE line, the receipt:

  ```
  RESULT: state=FRESH lane=pgdumpall provider=pg last_success_at=2026-11-24T02:00:00Z age_seconds=93600 max_age_seconds=691200 evaluated_at=2026-11-25T03:31:31Z reason=within_threshold
  ```

* **stderr** — a LOUD banner for anything that is not FRESH:

  ```
  ==============================================================================
  [DUMP-LANE] *** STALE *** — the nightly pg_dumpall lane has NOT reported a success inside the threshold
  [DUMP-LANE] lane=pgdumpall provider=pg reason=older_than_threshold
  [DUMP-LANE] last_success_at=2026-11-10T02:00:00Z age=15.00 days (1296000 s) threshold=8.00 days (691200 s)
  [DUMP-LANE] The last successful pg_dumpall exit-0 is OLDER than the threshold. The lane is silent, not necessarily broken — verify before assuming data loss.
  [DUMP-LANE] ACTION: inspect the cluster-side pg_dumpall CronJob and its heartbeat upsert (runbook: docs/runbooks/dump-lane-health-probe.md).
  [DUMP-LANE] RECEIPT: this result MUST land as a needs_emperor_word recommendation — a stale or unobservable dump lane is a user-decision item, not an auto-fix.
  ==============================================================================
  ```

---

## 2. Step 1 — create the heartbeat table (OPERATOR ACTION, one time)

The cluster-side CronJob publishes "last success" as **one row**. The table is
**operator-created** in the `ensemble_prod` database on the k8s psql18 instance
(behind the `10.44.0.2:5432` LB).

> This table is **NOT** a daemon-managed table. It carries **no**
> `daemon/migrations/versions/*.sql` file — that ledger is the daemon's own SQLModel
> schema trail (and its runner is SQLite-only). The missing migration is deliberate,
> not an oversight.

```sql
-- Run against: ensemble_prod
CREATE TABLE IF NOT EXISTS public.dump_lane_heartbeat (
    lane            text        NOT NULL,
    last_success_at timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT dump_lane_heartbeat_pkey PRIMARY KEY (lane)
);

COMMENT ON TABLE public.dump_lane_heartbeat IS
    'Dump-lane heartbeat: one row per lane, written ONLY after a successful dump (exit 0). Read by scripts/audit/dump_lane_staleness.py.';

-- Verify:
SELECT * FROM public.dump_lane_heartbeat;
```

The probe reads exactly this table with exactly this statement:

```sql
SELECT last_success_at FROM public.dump_lane_heartbeat WHERE lane = 'pgdumpall';
```

If you want a different schema or table name, change `HEARTBEAT_TABLE` /
`MARKER_SELECT_SQL` in `scripts/audit/dump_lane_staleness.py` (one line each) and update
this runbook with it.

---

## 3. Step 2 — teach the CronJob to publish the heartbeat (OPERATOR ACTION)

The heartbeat must be written **ONLY after a successful `pg_dumpall` exit 0**. A failed
dump must leave the row untouched, so a failure AGES the marker instead of refreshing it.

Replace the CronJob container's command with (adjust the paths/credentials to your
existing job — keep the existing `pg_dumpall` invocation verbatim, and add only the
heartbeat step):

```sh
set -eu

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/backup/pgdumpall-${STAMP}.sql"

# 1) take the dump (keep your existing invocation / flags)
if pg_dumpall -h "$PGHOST" -U "$PGUSER" -d "$PGDUMPALL_DBNAME" -f "$OUT"; then
    # 2) ONLY NOW publish the heartbeat — the dump exited 0
    psql -h "$PGHOST" -U "$PGUSER" -d ensemble_prod -v ON_ERROR_STOP=1 <<'SQL'
INSERT INTO public.dump_lane_heartbeat (lane, last_success_at, updated_at)
VALUES ('pgdumpall', now(), now())
ON CONFLICT (lane) DO UPDATE
    SET last_success_at = EXCLUDED.last_success_at,
        updated_at      = now();
SQL
else
    rc=$?
    echo "pg_dumpall FAILED (rc=${rc}) — the heartbeat was NOT updated" >&2
    exit "${rc}"
fi
```

Then confirm it works — the row must appear (and its `last_success_at` must advance
nightly):

```sql
SELECT lane, last_success_at, updated_at FROM public.dump_lane_heartbeat;
```

> **If you cannot edit the CronJob right now**, use the `--provider file` fallback
> (§1): point the probe at the dump artifact (or a touch-file the dump job maintains)
> and the probe reads that file's mtime instead. It needs no cluster change at all.

---

## 4. Step 3 — run the probe

```bash
cd /home/nea/ensemble-src
PGPASSWORD='…' .venv/bin/python scripts/audit/dump_lane_staleness.py \
    --db-url "postgresql+psycopg://ensemble@10.44.0.2:5432/ensemble_prod"
echo "exit=$?"
```

The probe is read-only and safe to run at any time against the live database.

---

## 5. Reading the result — the exit-code contract

| exit | `state=` | meaning | what to do |
|---|---|---|---|
| **0** | `FRESH` | the last success is within the threshold (8 days) | nothing |
| **75** | `NO_MARKER` | the health gate could **not** be established | **absence of evidence is a failure** — go to §5.1, find the `reason=`, fix the OBSERVABILITY first |
| **76** | `STALE` | the last success is older than the threshold | the dump lane is silent — go to §5.2 |
| **78** | `CONFIG_REFUSED` | a configuration refusal (the probe refused to run) | fix the invocation and re-run. **This is NOT a lane verdict** — do not record it as a dump-lane failure |

### 5.1 `NO_MARKER` — the `reason=` slug tells you which

| `reason=` | what it means | the action |
|---|---|---|
| `marker_row_missing` | the table exists but has **no row** for this lane | the CronJob never published a heartbeat → §3 |
| `db_unreachable` | the database could not be opened (host down, auth refused, DNS) | check the LB `10.44.0.2:5432` and the `ensemble` credential |
| `db_query_error` | the query failed (e.g. `relation "public.dump_lane_heartbeat" does not exist`) | the table is missing → §2 |
| `driver_unavailable` | the psycopg/sqlalchemy driver is not importable | run with the repo venv's python (`.venv/bin/python`) |
| `marker_file_missing` | the `--marker-file` does not exist | point at the right path, or switch to the `pg` provider |
| `marker_file_unreadable` | the marker path is not a readable regular file | fix the path/permissions |

### 5.2 `STALE` — the lane is silent

1. Is the CronJob still scheduled? (a disabled/missed schedule is the common cause)
2. Did the last runs **fail** (non-zero exit) or never **run**? The heartbeat only moves
   on exit 0 — a failing dump ages the marker exactly as a missing one does.
3. Is the dump **artifact** itself present and recent on the backup volume? A recent
   artifact plus a stale heartbeat ⇒ the heartbeat upsert is broken (§3), not the dump.

> A stale lane means *silent*, not *broken*. Verify before assuming data loss — and
> before assuming the backups are gone.

### 5.3 `CONFIG_REFUSED` — the probe refused to run

The banner names the missing/invalid argument (`--db-url`, `--marker-file`,
`--max-age-hours`, `--now`, or an unknown flag). Fix it and re-run. The house
exit-78 idiom is used here so an operator can tell "I mis-configured the probe" apart
from "the dump lane is stale" (76) and "the dump lane is unobservable" (75).

---

## 6. The weekly chain — what is and is NOT wired

**NOT wired (operator step, deliberately out of the tool author's scope).** The probe is
**not** yet dispatched by the maintenancer's weekly chain. Wiring it means editing a
DB-versioned prompt artifact (`agents/maintenancer/*.md`), which the commissioning lane
does not touch.

**The wiring step, when you take it:** the maintenancer's weekly run gains one lane
child that runs exactly the command in §1 and returns a result block. Per the frozen
PD-4a schema (`.agents/shared/planning/maintenance-system/decisions.md`), the block
carries `lane`, `status`, `measured_at`, `summary`, `metrics`, `actions_taken`,
`recommendations` and `evidence_paths` — the `metrics` block carries the probe's
`last_success_at` / `age_seconds` / `max_age_seconds`, and the RESULT line is the
`summary` source.

**The receipt rule.** A `STALE` or `NO_MARKER` result **must** land as a
`needs_emperor_word` recommendation (class 2 — needs a user decision). It is **not**
`auto_fix_now`: restoring a backup lane, or accepting the risk of a stale backup, is a
user decision. A `CONFIG_REFUSED` result is a probe-configuration error — fix the
invocation and re-run; it is **not** a lane verdict and must not be recorded as a
dump-lane failure.

Until the wiring lands, run §4 by hand (weekly) and treat a non-zero exit as
actionable.

---

## 7. Safety notes

* The probe is **read-only**. The only SQL it can execute is
  `SET default_transaction_read_only = on` (committed, so the session default is
  durable) plus one bound-parameter `SELECT`. PostgreSQL opens that transaction
  READ-ONLY, so a write would be refused **server-side**.
* The probe **never writes a file** — stdout/stderr only.
* The probe **never guesses a connection**: `--db-url` is required for the `pg`
  provider; ambient `POSTGRES_*` / `PG*` variables are never consulted.
* The password is masked in every error path (URL form and libpq keyword form), and any
  password occurrence is scrubbed out of the driver exception text before it reaches
  stderr.
* The probe is safe to run concurrently with the nightly dump — it only reads.
