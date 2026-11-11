# Phase 7 — Recorded Deliverables (durability, operator memo, follow-ups)

> **Status:** CLOSED (docs-only phase, no code / no tests / no migration / no fence change).
> **Deliverable:** `docs/runbooks/ram-guard-and-unit-bounds.md` — the operator memo (primary).
> **Source of truth:** `architecture-recommendation.md` §3.3/§3.3a/§3.4/§3.5/§15 (B22, B23, B5) and
> `phase2-measurements.md` §2/§3 (the corrected measured `R`).
> **This file exists for two things the memo cannot own:** the persistence facts a future
> table-bearing commission must read (T3), and the routed follow-up queue in the project's
> planning area rather than only in prose (T5's risk mitigation).

---

## 1. T1 / T12 — the durability posture (asserted, nothing built)

**In-process + the durable event as the forensic record. NO migration lands in v1 at all.**

* The guard's registry (`SpawnRecord`, keyed by `tracking_id`) is RAM-only.
* `EventKind.RAM_LIMIT_EXCEEDED` (`code="ram_limit_exceeded"`) is the durable forensic record —
  exactly one event per `WATCHING→KILLED` transition, emitted by the execution-lane thread,
  non-blocking, wrapped so it can never raise into the lane. Refusals emit **no** event.
* Retention ≈ 24 h (`EventRepository.cleanup_old(max_age_hours=24)`).
* Rollback = remove the lifespan block + the lane seams; **zero schema residue**.
* The bash lane's `bash_spawn_registry` row stays **OPEN** on a RAM kill (the reaper's env-stamp
  escapee net stays armed); it closes via the existing `ram-tier-dedup` path at terminal cleanup.

**Qualifying sentence (house debt, out of scope — arch §15 B22, verbatim):**

```
the SQLite `event.kind` CHECK constraint lists only 8 kinds while `EventKind` has 16 — it is already stale for 8 pre-existing kinds; the production deployment is PostgreSQL (`create_all`; the SQLModel column carries no CHECK — `models.py:74`), so `ram_limit_exceeded` (the 17th member) is unaffected
```

The stale CHECK is **pre-existing house debt and is deliberately not fixed by this feature**. No
migration is added. (Tree note: the enum literal in the current tree lists **17** names — the 16
the pin counts plus `ram_limit_exceeded`, which is the "17th member" the pin names.)

**T2 is deleted at scope level** — the "if durable ⇒ migration" branch does not exist; no `.sql`
file is added in v1.

**The 3-vs-8 GiB statement (T12 — also carried in the memo and the lane's final report).** The
commissioned fixed 8 GB ceiling is **superseded** by USER RULING #2 (dynamic 70 % of the detected
effective total RAM); the general feasibility bound is **`T ≥ 26.5 + 2.33·R GiB`**; the operator
has exactly three levers — **no-cap / lower-the-knob / resize**. The pre-freeze user-confirmation
gate (b) is **DISCHARGED** by USER RULING #1 (intent: disaster prevention) + #2 (mechanism). 3 GiB
survives only as guidance under the lower-the-knob lever.

---

## 2. T3 — persistence facts a future table-bearing commission needs

**Nothing is built here.** If a later commission adds a table:

1. Its own `daemon/migrations/versions/<YYYYMMDD>_<HHMMSS>_create_<table>.sql` with `-- UP` /
   `-- DOWN` (the runner raises `Missing -- UP section`; filename regex `^\d{8}_\d{6}_(.+)$`).
2. Idempotent `CREATE TABLE IF NOT EXISTS`; **no semicolons inside SQL comments** — the runner
   strips only *full-line* `--` comments before splitting on `;`, so a semicolon in an inline
   comment mis-splits the batch.
3. Index names **byte-identical** to the model's `__table_args__`.
4. **A brand-new table needs NO `_ensure_postgres_columns` mirror** (the `bash_spawn_registry`
   precedent) — that mirror exists for column additions to pre-existing tables.
5. Model registration **BEFORE** `create_all` (`daemon/manager.py`: registration block →
   `SQLModel.metadata.create_all(...)` → `MigrationRunner(...).run_pending_migrations()` →
   `_ensure_postgres_columns()`).
6. The table must materialize on **both** boot paths (SQLite migration runner AND PostgreSQL
   `create_all`).

**Current-tree truth about the ensure list (verified 2026-11-11) — order is not containment:**

* `_ensure_postgres_columns` (`daemon/manager.py:8396`) is **NOT one transaction**: many separate
  `engine.begin()` blocks open at `:10036`, `:10073`, `:10164`, `:10282`, `:10375`, `:10583`.
* The `terminal_reason` backfill runs a **per-statement transaction** in a loop
  (`:10162`–`:10192`), each with its own `try`.
* The checkpoint-autovacuum block (`:10575`–`:10634`) is **fail-open** by design (a
  permission-denied `ALTER … SET (autovacuum…)` logs a WARNING and continues).
* **Unguarded call site:** `if self._ensemble_config is not None and
  self._ensemble_config.is_postgres: self._ensure_postgres_columns()` at `manager.py:1057-1058` —
  an exception escaping any block *not* internally guarded propagates out of
  `InstanceManager.__init__` and **aborts boot**.
* Consequence for a new commissioner: "it runs after `create_all`" says nothing about failure
  isolation; each block's blast radius is decided by whether *that block* has a `try`.

---

## 3. T5 — the routed follow-up queue (numbered, with entry points)

Recorded here so they are actionable, not prose. Items 4 and 5 are **deliberately deferred**,
not forgotten.

| # | Follow-up | Entry point | Why out of scope |
|---|---|---|---|
| 1 | **Host-wide OOM posture** — the unit memory drop-in | `docs/runbooks/ram-guard-and-unit-bounds.md` §(e)/(f) + `/etc/systemd/system/ensemble-main.service.d/30-memory-bounds.conf` | Operator-gated, restart-class host change; this lane executes zero live mutations |
| 2 | **MCP-server RAM limits** — the guard over MCP stdio children | `daemon/mcp/builtin_servers/` + the MCP spawn path | Different ownership + kill authority; the commission names the bash + proc tools only |
| 3 | **The T4 ceremony path** — promote enforcement into `resource_limit_policy` | `.agents/shared/planning/resource-cost-control/approach-comparison.md` §6 (the adopted "seam + dormant table" row); `daemon/routers/resources.py` `/control-capabilities` | **User-gated** behind an existing fence; needs the durable enforcement port first |
| 4 | **Dormant-column unfencing** — `resource_pid_map.action` / `action_by` / `acted_at` | `tests/test_resource_control_seam.py` (`test_no_daemon_file_names_action_by_acted_at`, `test_no_resource_pid_map_string_names_action`, the AC5 tokenizer audit) | **DELIBERATELY DEFERRED, not forgotten** — a test-lifting change that must be conscious and user-gated, never a side effect |
| 5 | **The pre-existing bash generic-exception-arm leak** — releases neither `BashProcessRegistry` nor `pid_registry` and does not kill the child; `MemoryError` lands exactly there during a RAM incident | `daemon/tools/bash.py` — the `except Exception as e:` arm (the plan cited `:1223-1224`; current tree ~`:1742`) | **DELIBERATELY DEFERRED, not forgotten** — Phase 3 adds the **guard** release there (done); repairing the two pre-existing registry leaks is a separate reviewable change |
| 6 | **The proc lane's missing escapee net** — bash has the reaper's env-stamp sweep; proc has none | `daemon/tools/proc_tools.py` (spawn path + `ENSEMBLE_PROC_TRACKING_ID`) beside `daemon/services/bash_spawn_reaper.py` | New reaper surface + ownership rules; disclosed as honesty item 2 instead |
| 7 | **The stale SQLite `event.kind` CHECK** — 8 kinds enforced while the enum is 16 at the pin / 17 in the tree | `daemon/migrations/versions/20260412_000002_create_event_table.sql` | House debt deliberately not fixed (no migration in v1); harmless in production (PostgreSQL, no CHECK) |
| 8 | *(arch-named)* **Post-return agent-injection channel** | the event bus + the instance-injection path | Honesty item 14: no channel exists in v1; a new delivery lane is not a guard change |
| 9 | *(arch-named)* **Widening the bash registration gate for instance-less spawns** | `daemon/tools/bash.py` — the S1 gate `if instance_id is not None and proc is not None:` | A restructure the commission does not name; disclosed as honesty item 9b instead |

**Additional lane-routed disclosure (phase 4, D-5).** The **proc lane keys its guard record by the
8-hex `process_id`** (its own id space) rather than a uuid-per-spawn, so a collision would
**silently overwrite** the earlier watch. The current tree **warns instead of refusing** — a
content-free `logger.warning` naming only the tracking id and the prior state (no argv, no env
values, and **no counter bump**, so B5's frozen set is untouched and non-colliding keys are
unaffected) at `daemon/services/ram_guard/guard.py` (`register()` collision branch). Recorded
because the blast radius is a **lost watch**, not a wrong kill.

---

## 3b. Draft-time findings that changed the memo (evidence, not opinion)

Recorded because they are **not** in the plan or the arch doc, and a reviewer will
otherwise read them as drift.

1. **A SECOND incident day.** Three more kernel `oom-kill` deaths on **2026-11-09**
   (`09:26:27Z` pid 597787, `09:31:47Z` pid 598669, `09:42:57Z` pid 599644; unit
   `Failed with result 'oom-kill'` at `09:27:38Z` / `09:32:58Z` / `09:44:07Z`), all
   `task_memcg=.../ensemble-main.service`, all `anon-rss` ≈10.1–10.7 GiB. **Six kernel OOM
   deaths in twelve days, the last two days before this memo.** The memo records them in (b) and
   marks the death class as *current*. `MemoryMax` was still `infinity` on 2026-11-11.
2. **`journalctl -k --since <date>` is a trap on this host.** For a window that crosses an
   offline boot boundary it returned **0 entries** for both incident windows (it reported "no
   OOM" until `-b all` was added). The memo's reproduction lines therefore carry `-b all`, and
   the trap is named inline so a future operator does not repeat it.
3. **The unit file is mode 600 root.** The plan's (a) verify step ("grep the unit file for
   `Memory*`") silently fails for a non-root operator. The memo uses `systemctl show` as the
   authoritative effective-value resolution and names the mode.
4. **`MemoryCurrent` was 7.10 GiB / `memory.peak` 9.726 GiB at drafting** — i.e. the recommended
   `MemoryHigh=7G` sits at or below today's steady state. The memo keeps the pair (the plan's
   number) but adds a **re-measure-before-persisting** advisory: lift `MemoryHigh` toward, never
   above, `MemoryMax=8700M`.
5. **`EventKind` today lists 17 names** (the 16 the B22 pin counts + `ram_limit_exceeded`),
   because the pin's "16 kinds / 17th member" is the escalation-pass frame and the feature
   appended its member since. The B22 sentence is reproduced **verbatim** and the count is
   explained immediately after it — the pin is not edited.

---

## 4. T6 — the T4 hand-off (one-line pointer)

The memo's **Appendix F** carries the operator mental model (safety net vs future policy layer),
the disable surfaces, and the **three fences a future ceremony must consciously lift** — the
`/control-capabilities` `available:false` shape, the observe-only fence comment block in
`daemon/routers/resources.py`, and `tests/test_resource_control_seam.py`. **None was lifted.**
