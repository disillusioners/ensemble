# Phase 7: Durability, Operator Runbook, and Follow-up Routing

## Objective

Close the three loops the commission leaves open: what happens to a RAM-kill
**verdict** when the daemon dies (DQ10), what the operator must know about the
**ceiling-vs-unit-cap interaction** (R1), and where the **out-of-scope adjacents**
are routed so they are not silently dropped.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Apply the DQ10 ruling.** If **in-process only** (the lean recommendation): assert that a RAM-killed command's verdict is lost on restart and that this is acceptable (the command died with the daemon). If **durable**: the bash lane can close the existing `bash_spawn_registry` row with a **new `reap_reason` member** — a CLOSED SET (`:180-196`) ⇒ a **migration-backed vocabulary extension**; the write happens **once per killed command**, never per tick. **Note:** the set already carries a RAM-related member — ``ram-tier-dedup`` ("a RAM-tier dedup close") — so the vocabulary is RAM-aware, but ``ram-tier-dedup`` means *dedup close*, **not** *RAM-limit kill*; a kill needs its **own distinct member** (never overload the existing one, or the two events become indistinguishable in forensics) | Phase 1 Task 10 | Either (a) the durability posture is asserted in a test, or (b) a migration file + model change + a closed-vocab extension lands, with the **3-site lockstep** honored (SQLModel + `.sql` + byte-identical index names) |
| 2 | **If a migration is required, create it properly.** A brand-new table ⇒ its own `daemon/migrations/versions/<timestamp>_create_<table>.sql` with `-- UP` / `-- DOWN`, idempotent `CREATE TABLE IF NOT EXISTS`, **no semicolons inside comments** (the runner splits on `;`), and index names byte-identical to the model's `__table_args__`. A brand-new table ⇒ **NO `_ensure_postgres_columns` mirror** (the `bash_spawn_registry` precedent). Model registration happens **BEFORE** `create_all` (`manager.py:1095-1099`: registration → `create_all` → SQLite runner → ensure) | Task 1 | The migration is idempotent, the runner accepts it, the model's index names match the SQL byte-for-byte, and the storage pack passes |
| 3 | **Note the PG ensure-list reality** in the migration's comments: `_ensure_postgres_columns` (`manager.py:8396+`) uses **many separate `engine.begin()` blocks** (`:10036`, `:10073`, `:10164`, `:10282`, `:10375`, `:10583`) — NOT one transaction — with a per-statement txn in the terminal_reason backfill (`:10162-10192`) and a fail-open checkpoint block (`:10575-10634`), but there is still an **unguarded call site** (`:1057-1058`) where an exception **aborts boot**. Order is not containment | Task 2 | The comment is accurate against **this** tree (the commission's "one transaction" premise is stale) |
| 4 | **Write the operator runbook section** — the **Critical Interaction** (R1) in operational form: the unit `MemoryHigh=7G` / `MemoryMax=8G`, the daemon's observed steady band 5.3–6.0 GiB, the effective headroom formula, and the worked examples for the shipped default and for a lower ceiling | Phase 1 Task 11 | The runbook contains the formula + ≥1 worked example + the operator's escape hatch (the env kill-switch) |
| 5 | **Record the routed follow-ups** as a numbered list in the runbook: host-wide OOM posture (`MemoryMax` tuning — operator-gated), MCP-server RAM limits, the T4 ceremony path (the durable enforcement port behind `resource_limit_policy`), and the dormant-column unfencing (if a durable verdict on `resource_pid_map` is ever wanted — it is **fenced** by `tests/test_resource_control_seam.py` and requires a deliberate test-lifting change) | none | Each follow-up names its owner-facing entry point and why it is out of scope here |
| 6 | **Write the T4 hand-off note** — how the P1 default-ON guard coexists with the T4 user-gated seam (`enabled=false`, `user_gated=true`, no writer) and what the future ceremony would have to lift: the `/control-capabilities` `available:false` shape, the observe-only fence comment at `daemon/routers/resources.py:1166-1179`, and the fence test | Phase 1 Task 9 | The note states the structural separation and names every fence the future ceremony must consciously lift — **none** of them are lifted in this commission |

## Coupling

- **Loose with:** Phase 3 and Phase 4 (durability, if ruled for, touches the bash
  lane's registry write and adds a proc-lane write path — a scoped addition, not a
  redesign).
- **Independent of:** Phase 2, Phase 5, Phase 6 (no code dependency; the runbook and
  the follow-up list are documentation and, at most, one vocabulary extension).

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| A durable-verdict path adds a **DB write to the hot path** | Medium | Medium | Task 1 requires the write to happen **once per killed command**, never per tick; the bash lane's existing durable write is bounded at 1.5 s and fail-open (`:1009-1059`) — the same shape applies |
| A closed-vocab extension to `reap_reason` breaks a reader that assumes the old set | Medium | Low | The extension is migration-backed and every reader of the vocabulary is updated in the same phase; the storage pack pins the set |
| The runbook states the unit-cap interaction but the operator reads it as "8 GiB is safe" | **High** | Medium | Task 4 requires the worked examples to show the **effective** headroom; the shipped default is stated with its caveat, whatever the Phase 1 ruling decided |
| The follow-up list is written but never actioned | Low | Medium | The follow-ups are recorded **in the project's planning area** (a pointer to the plan dir), not only in this document's prose |

## Exit Criterion

- The DQ10 durability posture is **implemented or explicitly asserted** as
  in-process-only, with the reasoning recorded.
- If a migration landed: it is idempotent, its index names match the model
  byte-for-byte, the runner accepts it, and the storage pack passes.
- The runbook states the **effective headroom formula** with worked examples and
  names the operator's escape hatch.
- All four routed follow-ups are recorded **with their entry points**, outside this
  feature's scope.
- The T4 hand-off note names every fence a future ceremony must lift — **and lifts
  none of them now**.
