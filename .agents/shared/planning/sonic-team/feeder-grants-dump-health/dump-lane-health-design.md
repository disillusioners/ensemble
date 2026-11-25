# Dump-Lane Health — Design (probe-side visibility for the nightly `pg_dumpall` lane)

**Lane:** `feature/sonic-dump-health` · base `9f464d726c14ea6d070f63ce017f329b1defb254`
**Module:** C (dump-health) of the `feeder-grants-dump-health` commission
**Artifacts:** `scripts/audit/dump_lane_staleness.py` · `docs/runbooks/dump-lane-health-probe.md` ·
`tests/unit/test_dump_lane_staleness_probe.py` · one `.agents/tester/PACKS.md` registration row
**Extended by:** v0.31.54 follow-up slice `dump-health-nits-v03154` (runbook-design module).

---

## 0. The problem this closes

The live VM's databases are backed up by a **cluster-side** nightly `pg_dumpall` CronJob
(k8s psql18 behind the `10.44.0.2:5432` LB; db `ensemble_prod`, user `ensemble` —
`docs/deployment.md` §topology table). The daemon on the VM **cannot reach kubernetes**.

Consequence: when the dump CronJob stops running, stops succeeding, or starts failing,
**nothing on the VM goes red**. There is no heartbeat, no alert, no receipt, and no
dashboard — the backup lane can be dead for weeks while every VM-side health check stays
green. That is precisely the *silent outage* shape this module closes: the failure is not
that the dump fails loudly, it is that **nobody ever finds out**.

The signal therefore belongs on the **maintenancer / devops PROBE side**, not in the
daemon. This module delivers the smallest honest version of that: a READ-ONLY operator
probe that answers one question — *when did the nightly dump last SUCCEED?* — and answers
it **loudly** when the answer is bad.

**Non-goals (explicit).** This module does not:

* add daemon code, a daemon metric, a tool category, or a vocabulary bump;
* make the backup lane self-healing, retrying, or alerting;
* wire the probe into the maintenancer's dispatch (see §3.3 — operator-actionable);
* change the CronJob itself (cluster-side change — see §1.1, operator-actionable).

---

## 1. The five decisions, as implemented

### 1.1 MARKER SOURCE — a heartbeat the CronJob side writes, read over PostgreSQL

The last-success timestamp comes from a **heartbeat row written by the cluster side**
that a NON-k8s probe can observe over the ONE channel that already exists between the
cluster and the VM: **PostgreSQL at `10.44.0.2:5432`** (the same LB the daemon uses; db
`ensemble_prod`, user `ensemble`).

The cluster-side CronJob upserts **exactly ONE row** — `lane='pgdumpall'`,
`last_success_at timestamptz` — and **ONLY AFTER a successful `pg_dumpall` exit 0**. A
failed dump leaves the heartbeat untouched, so a failure ages the marker rather than
refreshing it. That is the whole contract: *the row says when the last SUCCESS was.*

**This cluster-side step is a CLUSTER-SIDE CHANGE ⇒ an OPERATOR-ACTIONABLE RUNBOOK STEP,
not daemon code.** The runbook (`docs/runbooks/dump-lane-health-probe.md`) carries the
copy-paste DDL, the copy-paste upsert, and the CronJob edit. Nothing in this module
touches kubernetes, and nothing in this module writes to any database.

The heartbeat table is **operator-created**, not daemon-managed. It therefore carries
**no `daemon/migrations/versions/*.sql` file** — that ledger is the daemon's own
SQLModel schema trail (and its runner is SQLite-only). This is stated in the runbook so
nobody reads the missing migration as an oversight.

**Two providers behind ONE state machine.** The probe supports:

| provider | marker source | shape |
|---|---|---|
| `pg` (default) | read-only `SELECT` of the heartbeat row | the production shape — a non-k8s probe reaching the cluster's database |
| `file` | the **mtime** of a marker/artifact file named by `--marker-file` | the **immediately-usable fallback** (needs no cluster change at all) and the hermetic test shape |

Both providers return the **same** `MarkerObservation` and feed the **same** state
machine (§2) and the **same** exit-code contract (§4). The unit pin
`test_both_providers_feed_the_same_state_machine` holds that equivalence.

### 1.2 THRESHOLD — 8 days (691200 s), overridable

The dump is **DAILY**; the check cadence is **WEEKLY**. The default staleness threshold is
therefore **8 days = 691200 s**:

* **one day of slack** over the daily dump period (a dump that slips by a day is not a
  stale lane — but two days is), and
* **longer than the 7-day check period**, so **one missed check cannot flap** the
  verdict (a weekly probe with a 7-day threshold would report STALE every time the probe
  itself was skipped).

`--max-age-hours` overrides it (default `192`). A non-positive value is a **CONFIG
refusal (78)** — never a silent fallback to the default.

### 1.3 CADENCE — the weekly central-maintenance run + ad-hoc operator invocation

The probe is invoked by the **weekly central-maintenance run** (the maintenancer lane's
weekly chain — `docs/runbooks/central-maintenance-manual-trigger.md`) and, ad-hoc, by an
operator.

**Wiring the probe INTO the maintenancer's dispatch is an OPERATOR-ACTIONABLE step this
module deliberately does NOT implement.** Doing so would edit a DB-versioned prompt
artifact (`agents/maintenancer/*.md`), which is out of scope for a small additive
commission (and is fenced by the commission). The runbook names the exact command and
states this explicitly; so does this design doc. Until an operator wires it, the probe
is run by hand — which is still strictly better than the status quo (no signal at all).

### 1.4 LOUD-REPORT CHANNEL — stderr banner + one-line stdout RESULT + a documented exit code

The probe writes:

* a **LOUD banner on stderr** for every non-FRESH verdict (`*** STALE ***`,
  `*** NO-MARKER ***`, `*** CONFIG REFUSAL ***`), framed by `=` rules, naming the lane,
  the age, the threshold, the next action, and the receipt rule. A FRESH verdict gets a
  **single quiet stderr line** — a fresh dump lane must not cry wolf;
* **exactly one structured line on stdout**:

  ```
  RESULT: state=STALE lane=pgdumpall provider=pg last_success_at=2026-11-10T02:00:00Z age_seconds=1296000 max_age_seconds=691200 evaluated_at=2026-11-25T03:31:31Z reason=older_than_threshold
  ```

  The RESULT line is **the receipt** — it is what the weekly maintenance receipt carries.
  Field set (closed): `state`, `lane`, `provider`, `last_success_at`, `age_seconds`,
  `max_age_seconds`, `evaluated_at`, `reason`. Exactly one line, so the receipt parses
  without a multi-line grammar.

* a **documented exit code** (§4).

**A STALE or NO-MARKER result MUST land as a `needs_emperor_word` recommendation** — the
maintenance system's class-2 recommendation (`recommendations.needs_emperor_word` in the
frozen PD-4a per-lane result block, `.agents/shared/planning/maintenance-system/decisions.md`).
A stale or unobservable backup lane is a **user-decision** item (restore the lane? accept
the risk? investigate the cluster?), never an `auto_fix_now`.

### 1.5 FAILURE BEHAVIOR — fail-closed; absence of evidence IS a failure

* An **unreadable / unreachable / absent marker NEVER reports FRESH.** There is no
  "assume OK" path anywhere in the code: a missing heartbeat row, an unreachable
  database, a missing marker file, an unreadable marker file, a driver that is not
  installed — every one of them lands on **NO-MARKER (75)**.
* The probe **never guesses a connection**: `--db-url` is REQUIRED for the `pg` provider
  and there is **no env fallback and no default**. `POSTGRES_*` / `PG*` in the ambient
  environment are never consulted (pinned by
  `test_never_guesses_a_connection_from_the_environment`).
* The probe **never writes to any database**: the only SQL it can execute is a
  read-only session `SET` plus **one bound-parameter `SELECT`**, and the session is
  pinned READ-ONLY server-side (see §2.1).
* The probe **never writes any file** — it emits stdout/stderr only; there is no receipt
  file, no state file, no cache.

---

## 2. The state machine

### 2.1 The read path (provider `pg`)

1. open the engine (lazy import; the `file` provider is **stdlib-only**);
2. `SET default_transaction_read_only = on` — then **COMMIT** it. A plain `SET` is
   transactional, so it only becomes session-durable when the transaction commits; the
   next statement then opens a NEW transaction which **PostgreSQL itself starts
   READ-ONLY** because the session default says so. A write is refused **server-side**,
   not merely by our good manners;
3. `SELECT last_success_at FROM public.dump_lane_heartbeat WHERE lane = :lane` — the
   lane is a **bound parameter**, never interpolated;
4. close + dispose.

Any failure at any step ⇒ NO-MARKER. The engine is always disposed.

### 2.2 The verdict

```
observation.error_reason set  ─────────────────────────────────►  NO-MARKER (75)
observation.last_success_at set
        │
        ├── age_seconds = int(now - last_success_at)          ← REPORTED age IS the COMPARED age
        │       (one integer: no rounding drift between the verdict and the receipt)
        ├── age_seconds >  max_age_seconds  ──────────────────►  STALE (76)
        └── age_seconds <= max_age_seconds  ──────────────────►  FRESH (0)
```

**The boundary is exact:** at exactly the threshold the lane is **FRESH**; one second
past it, **STALE**. Pinned by `test_exactly_at_the_threshold_is_fresh` and
`test_one_second_past_the_threshold_is_stale`.

A marker timestamp in the **future** (clock skew on the writer side) renders as a
negative age and is FRESH; the verdict carries an explicit `note=` line saying so, so a
skewed writer is visible rather than silent.

---

## 3. The fences, and what is deliberately NOT done

### 3.1 Hard fences honored by this module

| fence | how it is honored |
|---|---|
| no daemon code | the module touches **zero** files under `daemon/`; the probe imports nothing from the daemon |
| no `agents/**/*.md` | **zero** prompt artifacts touched ⇒ `agents/.prompt-artifacts.sha256` is **byte-identical** (verified before/after) |
| no frontend, no new deps | stdlib + the repo's existing venv packages only (`sqlmodel`/`sqlalchemy`/`psycopg` are already repo deps, and are imported **lazily** so the `file` provider needs nothing at all) |
| no new tool category, no vocabulary bump | the probe adds **no metric label at all**; `.agents/shared/metrics/tool-categories.json` is untouched (66 categories, `vocabulary_version` 11) |
| no live-system writes, no live-store writes from the test run | the unit test is **hermetic**: a fake engine is injected at the `_open_pg_engine` seam (**no socket is ever opened**), the `file` provider reads only inside `tmp_path`, and a provider failure is asserted to leave **no side effect** |
| no push / tag / promote | the module ends at a commit on its own branch |

### 3.2 The house pattern the script follows

`scripts/audit/skill_stale_deactivate.py` is the precedent: argparse, a REQUIRED
explicit `--db-url` (never guess a connection), **DSN redaction in every error path**,
`main(argv) -> int` returning the exit code, and a printed receipt. This probe adds the
exit-code contract the maintenance chain can consume.

**Credential hygiene.** The password is masked in the DSN itself (URL form *and*
libpq keyword form), **and** any password occurrence is scrubbed out of the driver
exception text before it reaches stderr — a driver that echoes the connection string
back cannot leak the credential into a receipt or a transcript.

### 3.3 Deliberately NOT implemented (operator-actionable)

1. **The cluster-side change** — the heartbeat table DDL, the CronJob upsert, and the
   CronJob edit. Copy-paste SQL and the CronJob diff are in the runbook.
2. **The maintenancer dispatch wiring** — the probe is not yet dispatched by the weekly
   chain. That would edit a DB-versioned prompt artifact (`agents/maintenancer/*.md`),
   which this commission does not touch. The runbook names the exact command and the
   exact wiring step.
3. **No alerting, no auto-remediation, no dashboard** — out of scope. The probe reports;
   a human (or the Emperor) decides.

---

## 4. The exit-code contract (documented in the design doc AND the runbook)

| code | meaning | `state=` in the RESULT line | operator meaning |
|---|---|---|---|
| **0** | FRESH — the last success is within the threshold | `FRESH` | the lane is healthy |
| **75** | NO-MARKER — the health gate could **not** be established (absent / unreadable marker, missing row, unreachable database, driver unavailable) | `NO_MARKER` | **absence of evidence is a failure** — investigate; do not read this as "healthy" |
| **76** | STALE — the last success is **older** than the threshold | `STALE` | the lane is silent — investigate; the dump may be broken |
| **78** | CONFIG — a configuration refusal (missing `--db-url`, missing `--marker-file`, bad `--max-age-hours`, bad `--now`, an unknown flag) | `CONFIG_REFUSED` | **this is a probe-configuration error, not a lane verdict** — fix the invocation and re-run; do NOT record it as a dump-lane failure |

`78` is the **house exit-78 idiom** (`EX_CONFIG`, as used by `scripts/run_tests_scrubbed.sh`
and `scripts/install-dir-guard.sh`). The probe's argparse is overridden so that **every**
argument-level refusal lands on 78 — never argparse's default exit 2.

The `state=` vocabulary is **closed**: `FRESH` · `STALE` · `NO_MARKER` · `CONFIG_REFUSED`.
`CONFIG_REFUSED` is a *probe* state, not a *lane* state — a receipt consumer must not
read it as a lane verdict.

### 4.0 Documented invocation form + first-hand execution leg (2026-11-25)

Documented invocation (credential redacted):

```bash
PGPASSWORD='…' .venv/bin/python scripts/audit/dump_lane_staleness.py \
    --db-url "postgresql+psycopg://ensemble@10.44.0.2:5432/ensemble_prod"
```

Checkout precondition: a checkout whose tree contains `scripts/audit/dump_lane_staleness.py`
(tag v0.31.53 or later — NOT `/home/nea/ensemble-src`). Guard present rc=0 at
`/home/nea/ensemble-src-wt-audit @ d07cc64b8` (content >= v0.31.53, pre-tag).

Recorded results (first-hand, 2026-11-25):

- pg §1: exit=75; `RESULT: state=NO_MARKER lane=pgdumpall provider=pg last_success_at=none age_seconds=none max_age_seconds=691200 evaluated_at=2026-11-25T04:49:44Z reason=db_query_error`; stderr detail: `ProgrammingError: (psycopg.errors.UndefinedTable) relation "public.dump_lane_heartbeat" does not exist`.
- pg §4: exit=75, same line with evaluated_at=2026-11-25T04:49:46Z.
- file fresh temp marker: exit=0; `RESULT: state=FRESH lane=pgdumpall provider=file last_success_at=2026-11-25T04:49:59Z age_seconds=0 max_age_seconds=691200 evaluated_at=2026-11-25T04:49:59Z reason=within_threshold`.
- file missing path: exit=75; `RESULT: state=NO_MARKER lane=pgdumpall provider=file last_success_at=none age_seconds=none max_age_seconds=691200 evaluated_at=2026-11-25T04:49:59Z reason=marker_file_missing`.
- file marker 30 days old: exit=76; `RESULT: state=STALE lane=pgdumpall provider=file last_success_at=2026-10-26T04:49:59Z age_seconds=2592000 max_age_seconds=691200 evaluated_at=2026-11-25T04:49:59Z reason=older_than_threshold`.

Credential: sourced from the live install env file (mode 600) into PGPASSWORD (env, never
argv); 0 occurrences of the secret in any captured output; the RESULT line carries no DSN.

Interpretation: the live DB has NO heartbeat table yet — the truthful R4 OPEN state
(fail-closed, correct).

### 4.1 The `reason=` vocabulary (closed)

`within_threshold` · `older_than_threshold` · `marker_file_missing` ·
`marker_file_unreadable` · `marker_row_missing` · `db_unreachable` · `db_query_error` ·
`driver_unavailable` · `missing_db_url` · `missing_marker_file` · `bad_max_age_hours` ·
`bad_now` · `bad_argument`.

As of v0.31.54, `driver_unavailable` is actually emitted (ImportError at the engine
open) and `bad_max_age_hours` also refuses non-finite nan/inf.

Every slug maps to an operator action in the runbook.

---

## 5. The unit pin

`tests/unit/test_dump_lane_staleness_probe.py` — **39 nodes**, hermetic.

Coverage of the brief's required set:

| requirement | node(s) |
|---|---|
| the three states + their exit codes | `test_fresh_marker_exits_zero_and_reports_fresh` (0), `test_stale_marker_exits_76_and_reports_stale` (76), `test_absent_marker_exits_75_and_reports_no_marker` (75), `test_unreadable_marker_directory_is_no_marker` |
| the threshold boundary | `test_exactly_at_the_threshold_is_fresh` (exactly AT ⇒ FRESH), `test_one_second_past_the_threshold_is_stale` (+1 s ⇒ STALE) — asserted BOTH through the state machine AND through the CLI |
| the refusal to run without an explicit connection | `test_missing_db_url_refuses_with_exit_78`, `test_never_guesses_a_connection_from_the_environment` (ambient `POSTGRES_*`/`PG*` do NOT become the connection) |
| DSN redaction in the error path | `test_password_is_redacted_in_the_error_path` (the driver exception itself echoes the password), `test_redact_dsn_masks_the_url_password`, `test_redact_dsn_masks_the_keyword_password`, `test_scrub_secrets_removes_the_password_from_free_text` |
| the no-write assertion | `test_the_only_sql_constants_carry_no_write_verb`, `test_the_select_reads_the_documented_heartbeat_table`, `test_every_executed_statement_is_one_of_the_allowed_constants` (AST: only `MARKER_SELECT_SQL` / `READ_ONLY_SESSION_SQL` can reach a driver), `test_no_string_constant_anywhere_carries_a_write_verb`, `test_the_probe_opens_no_write_path_on_the_filesystem`, `test_every_filesystem_call_the_probe_makes_is_a_read`, `test_a_provider_failure_leaves_no_side_effect` (a failed provider creates/moves/touches nothing) |

**Mutation self-proof (the pins are non-vacuous).** Six mutations of the script were
executed against the pin; each turned it RED, and each restore was verified
**byte-identical** (sha256):

| mutation | result |
|---|---|
| threshold `>` → `>=` | RED — `test_exactly_at_the_threshold_is_fresh` |
| absent marker ⇒ FRESH | RED — `test_fresh_marker_exits_zero_and_reports_fresh` |
| DSN redaction removed | RED — `test_redact_dsn_masks_the_keyword_password` |
| a `DELETE` sneaks into the executed SQL | RED — 2 no-write nodes |
| the `--db-url` refusal removed | RED — 2 refusal nodes |
| the read-only session guard removed | RED — `test_the_pg_provider_issues_read_only_session_then_one_bound_select` |

---

## 6. Risks and known limits

1. **The probe is only as good as the heartbeat.** Until the operator creates the table
   and edits the CronJob, the `pg` provider will read **NO-MARKER** (75) — which is the
   CORRECT fail-closed answer, but it means the probe reports "unobservable" rather than
   "stale" until step 1 of the runbook is done. The `file` provider is the
   immediately-usable fallback in the meantime.
2. **The cluster-side upsert is a write.** It is the ONLY writer of the marker, and it
   runs cluster-side — outside this module's read-only fence by construction (the fence
   covers THIS module's code, not the operator's CronJob).
3. **Clock skew** between the cluster and the VM shows up as a future timestamp (a
   negative age ⇒ FRESH). It is surfaced as a `note=` line, not as a distinct state, so
   a persistently skewed writer could mask staleness. Mitigated by the `note=` line; a
   future-proof shape would be a fourth state, which this module deliberately does not
   add (the brief fixes the state count at three).
4. **`--now` is an operator affordance.** It lets an operator pin the evaluation
   instant (for replay or for a boundary drill). The RESULT line always records the
   instant actually used, so a `--now` run is distinguishable in the receipt. The
   weekly chain must NOT pass `--now`.
5. **The probe is a POINT-IN-TIME read.** It says nothing about dump *completeness* or
   restorability — only that a dump reported success inside the window. A dump that
   "succeeds" but writes a truncated file is out of scope here.
6. **The maintenancer wiring is absent** (§3.3) — until an operator wires it, the
   weekly run does not call the probe, so the signal is manual. The probe is
   intentionally usable by hand first.

7. **R4 — OPEN (NOT in the v0.31.54 slice).** The probe is shipped and verified; the
   remaining operator steps are out of scope for this slice: (1) heartbeat table
   creation — the §2 DDL `public.dump_lane_heartbeat`, a staged migration / operator
   action (NO schema change was made in the slice); (2) CronJob edit — the heartbeat
   upsert, §3 — operator / devops; (3) weekly-cadence wiring into the maintenancer
   chain — this §6, a DB-versioned prompt artifact via the prompt-versioning approval
   lane — a follow-up commission; (4) the probe is reachable only from a checkout (not
   from the staged payload). Interim alarm already standing (state as of 2026-11-25):
   the weekly maintenance schedule carries a 48h green-dump check (added by Ari) and
   the devops lane is reviving the dump lane tonight.
