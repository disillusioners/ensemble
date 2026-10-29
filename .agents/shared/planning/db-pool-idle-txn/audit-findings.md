# Audit Findings — db-pool borrow-site census (the F1 audit record)

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`).
- **Artifact:** the F1 audit record (decision D-11 — a NEW artifact in this
  planning dir; the RCA itself is under a read-only fence for this lane).
- **Method:** `audit-method.md` (same directory, cited by filename) — the
  scanner definition, the family taxonomy, and the reproduction matrix are
  the method of record. This file publishes the headline and the
  audited-and-clean verdicts; it does not restate the method.

## 1. The census headline — the AST baseline (CANONICAL)

**779 borrow sites in `daemon/`** under the canonical AST definition, with
**0 unclosed borrows**:

| Family | Pattern | Count |
|---|---|---|
| A | `with SQLModelSession(...)` / context-managed Session borrows (AST scope, A+B combined per `audit-method.md` §5) | **613** |
| B | `engine.connect()` context-managed borrows | **175** |
| C | raw cursor/psycopg borrows | **4** |
| D | `sessionmaker` / factory-constructed borrows (AST definition is the contract) | **148** |
| E | thread-local / long-lived session holders | **14** |
| F | raw psycopg connections outside any context manager | **0** |
| **TOTAL (AST baseline — CANONICAL)** | | **779** |

Every one of the 779 is **close-guaranteed** (context-managed `with` /
`async with` / explicit `finally: close()` — see `audit-method.md` §5 for
the close-path enumeration). **0 unclosed borrows at the borrow-hygiene
level.**

## 2. The ≈689 aggregate is RETIRED / NON-CANONICAL (DR-6)

The earlier census headline of **≈689 aggregate borrow sites is
NON-CANONICAL** and MUST NOT be cited as the census figure. The aggregate
used an **unrecorded pattern breadth for families D and E**, so it is not
scanner-independent (`audit-method.md` §5.3: *"the scanner definition is
the contract, not the aggregate number"*). The aggregate is conservative
(lower than the AST baseline), so the audit's CONCLUSION is unaffected by
its retirement — but the number itself is retired.

## 3. RCA sites B and C — audited and CLEAN, with premise corrections

### 3.1 Site B — the `/api/instances` list path (RCA premise #2 — CORRECTED)

The observed fingerprint `SELECT instances.instance_id, instances.project_id …`
is owned by `SQLModelInstanceRepository.list`
(`daemon/repositories/instance/repository.py:1259+`); the session is created
**inside the repository**, context-managed:
- flat path — `daemon/repositories/instance/repository.py:1383`
- root+BFS path — `daemon/repositories/instance/repository.py:1434`
  (with the sibling borrow at `:1429` in the same family)

**Audited and CLEAN** — 0 never-closed borrows. The RCA's leak claim
(*"ORM read path, autobegin without commit/rollback"*) is literally true of
the code text but **NOT SUPPORTED as a leak**: the session is
context-managed, `close()` returns the connection to the pool, and the
pool's `reset_on_return` (library default `'rollback'`, not overridden at
base) issues the server-side rollback. The residual exposure at this site
is the **R2** class (a thread hung *inside* the with-block — a DWELL, not a
missing close), plus the BFS depth-loop transaction span
(`:1434-1645`), both **routed out of this lane** (the event-loop sync-DB /
dwell class — see `final-implementation-spec.md` §7).

### 3.2 Site C — the `job_queue_items` count path (RCA premises #3 and #4 — REFUTED / CORRECTED)

The readiness `queue_freshness` probe does **NOT** read `job_queue_items`
(both its statements read `FROM task`), so the RCA's premise #3 ("the probe
leaks a transaction per tick") is **REFUTED** (re-verified at base; measured
Phase-0: `readiness_probe_actually_reads_from_task: true`,
`readiness_probe_matches_incident_fingerprint: false` — `BASELINE.md` T4).

The fingerprint's real owner is `_DEFER_PENDING_COUNT_SQL`
(`daemon/services/defer_block_resolver.py:185-191`), executed
context-managed at `:274` and at `:321` (inside the `engine.connect()`
opened at `:306`). **Audited and CLEAN** — both borrows close-guaranteed;
no leak class exists at this site.

### 3.3 Adjacent audited-and-clean site (same census family)

`daemon/tools/instance_read.py:1408-1416` — `with engine.connect()` at
`:1412`, context-managed ⇒ **CLEAN** (recorded because it renders an
instances-shaped fingerprint and was part of the attribution
reconciliation).

## 4. The five-backend reconciliation — **INFERENCE, not attribution**

The incident window recorded **5 stuck backends** whose `last_query`
fingerprints reconcile against the census as follows:

| Incident fingerprint | Count | Census owner |
|---|---|---|
| `BEGIN` | 2 | Site A — `daemon/tools/ens_db_tools.py:1479` / `:1491` (the only `text("BEGIN")` in `daemon/`; measured-unique standalone-BEGIN marker — `BASELINE.md` T4, D-56) |
| `SELECT instances.instance_id, instances.project_id …` | 2 | Site B — the repository list path (§3.1 above) |
| `SELECT count(*) FROM job_queue_items …` | 1 | Site C — `defer_block_resolver.py` (§3.2 above) |

**This reconciliation is INFERENCE, not measured attribution.** The
daemon's connections carry an **empty `application_name`** at base, so
`pg_stat_activity` cannot attribute a backend to a pool post-hoc; a
reproduced fingerprint places the STATEMENT in the window, not a LEAK at
that site. The Phase-0 measurement (D-56) strengthens the BEGIN-attribution
to measured-unique; the holder attribution for the other two families
remains inference. The census found **no never-closed borrow** at either
site — which is exactly why the incident's occupancy shape (15/15 slots
held, only 6 `idle in transaction`) is the DWELL/OCCUPANCY class this lane
discloses as NOT repaired (`final-implementation-spec.md` §8.5).

## 5. No leak class was found where the census found none

The systematic audit found **no leak class at any site the census
classified clean**: every borrow in the 779-site AST baseline is
close-guaranteed, and the individually audited suspect dossiers
(`audit-method.md` §6) each close. The defects this lane fixes are:
the explicit-`BEGIN` transaction-abort gap (F3, Site A — a HYGIENE fix),
the borrow-then-await reconcile hold (F8, M-R3), the non-idempotent
concurrent mission mint (F6), the wedge-capture wrong-table cascade root
(F5), and the probe admission/statement bounds (F4-A/F4-B) — none of which
is an unclosed borrow. **No leak class was found where the census found
none**, and no over-claim is made beyond the census evidence.

---

## 6. Addendum — the census instrument re-measured (T-H2, tidier cycle)

The **instrument** (`scripts/audit/borrow_census.py`) was re-verified
against `audit-method.md` §3.1 and found to be a **false-clean on two
families**: the published §3.1 source has **no B branch** (every
`SQLModelSession` with-borrow was counted into A) and **no F branch at
all** (F was structurally unmeasurable, hence the recorded `F=0`). The
instrument now implements both detectors and the TOTAL is relabelled
to the families actually summed.

**Re-measured with the fixed instrument** (same instrument, base
`19fc11c8b` and HEAD — the two runs are IDENTICAL, so **the lane added
ZERO borrow sites**; the audit-method §7.2 diff gate is satisfied with
an empty delta):

| Family | base `19fc11c8b` | HEAD | note |
|---|---:|---:|---|
| A (`with Session(...)` / `WriteGuardSession(Session(...))`) | **438** | **438** | |
| B (`with SQLModelSession(...)`) | **175** | **175** | split out of A |
| C (non-`with` session assignment) | **4** | **4** | the same four named sites |
| D (`with engine.connect()` / `.begin()`) | **148** | **148** | |
| E (`async with pool.acquire()`) | **14** | **14** | |
| F (raw psycopg / `raw_connection` / `getconn` / `putconn`) | **1** | **1** | the one-shot `psycopg.connect` at `daemon/services/wedge_capture/db_capture.py:195` |
| **TOTAL (as summed)** | **780** | **780** | |

**How this reconciles with the headline above (no over-claim, no
fudge):**

1. The headline's **A=613** is the AST scanner's **A+B COMBINED** —
   `audit-method.md` §5.2 says so verbatim (*"the AST scanner's `A`
   also counts `SQLModelSession`"*). With the split implemented,
   **438 + 175 = 613** — the headline's A is reproduced exactly, now as
   two separately measured families.
2. The headline's **F=0** was a **SCANNER GAP, not a tree fact**: the
   published scanner had no F detector, so the family could not be
   measured at all. The implemented detector finds **F=1** — the ONE
   audited site, the one-shot `psycopg.connect` in the wedge-capture
   module, which the contract already records as CLEAN (F5.3: *"a
   one-shot `psycopg.connect` with `close()` in `finally` ⇒ no pool,
   so it cannot leak"*). It is a **known, individually-audited,
   close-guaranteed** site — not a leak and not a new finding.
3. Consequently the summed total is **780**, one more than the
   headline's 779 — the difference is **exactly** the one F site the
   old instrument could not see. Nothing else moved: every other
   family is identical to the recorded baseline, and the §3.2 pattern
   cross-check (whose F vocabulary does not name plain
   `psycopg.connect`) still reports A=439 / B=175 / C=4 / F=0 — the
   same PATTERN-DEPENDENT divergence the audit records for D and E.

**The census conclusion is unchanged**: every borrow site in the
measured set is close-guaranteed (the F site included — it closes in a
`finally` and holds no pool), **0 unclosed borrows**, and the retired
**≈689** aggregate stays **NON-CANONICAL**.

*Addendum written by the tidier fix cycle (T-H2); the headline above is
unchanged and remains the record's base baseline.*

---

*Produced by the db-pool-idle-txn lane, Phase 1 task T1.11 (F1.1). The
method of record is `audit-method.md`; the measured Phase-0 record is
`BASELINE.md`; the binding change list is `final-implementation-spec.md`.*
