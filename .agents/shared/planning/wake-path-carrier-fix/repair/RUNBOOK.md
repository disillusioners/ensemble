# RUNBOOK — guarded live repair request for the wedged Ari root

**Target:** live instance `17efd2ef-9f98-46d4-b41b-60e3e32b2d13` (agent `ari`), daemon **v0.31.0**, database `ensemble_prod`.
**Artifact pair:** `live_wedge_repair.py` (read-only inspector + request emitter) and this runbook.
**Status of the repair itself:** **NOT EXECUTED. NOT AUTHORISED.** See §2.
**Scope:** **WIDENED to the 8-row set by LEADER RULING 2026-11-18** (the original commission scoped 4 rows). See **"SCOPE DRIFT AND THE WIDENED GATE"** immediately after §1.
**Gate string (mandatory, byte-exact):** `wake-path-carrier-fix/live-wedge/17efd2ef/w8`.
**Measured at:** 2026-11-17T23:0x–2026-11-18T00:3xZ (see Appendix B for the drift the measurements caught and the widening ruling).
**Base commit of every code citation below:** `eca98397d9e0109611764ac418132c9d35afde2c` (worktree `/home/nea/ensemble-src-wt-wake-path-carrier-fix`). Line numbers were re-measured at that commit — do not trust them on any other tree.

---

## 1. What the wedge is

The root parks in `WAITING_CHILDREN` and never leaves. Two **independent** gates hold it, and both are row-state gates that nothing will ever clear on their own:

| Gate | Read | Citation (re-measured at base) |
|---|---|---|
| **Bus gate** — the park predicate | `count_pending_for_target(parent) > 0` ⇒ transition `RUNNING → WAITING_CHILDREN` at turn end | `daemon/services/message_processing_pipeline.py:1279` (`_maybe_transition_to_waiting_children`), the decisive read `:1329` (`pending = await bus.count_pending_for_target(instance_id)`), early-out `:1330`, CAS transition `:1335-1340` |
| **Mission gate** | `count_outstanding_commissions(parent) > 0` ⇒ the root-completion gate refuses to publish `completed` (downgrades to `WAITING_CHILDREN`) | `daemon/services/child_reports.py:2691-2695` (`_root_completion_gate`, mission leg) → `daemon/manager.py:13897-13936` → `daemon/services/mission_record_reads.py:239-289` → `daemon/repositories/mission_record/repository.py:401-451` (`count_outstanding_commissions_for_anchor`: `state='outstanding'` AND `advisory_backfill = false`, JOINed on `mission_records.anchor_instance_id`) |

The count the bus gate reads is `daemon/repositories/dependency_bus/repository.py:471-515` — literal predicate at `:506-515`: `WHERE target_instance_id = :t AND state = 'PENDING'`. **It never inspects the watched work** — a permanently-unfireable watcher counts exactly like a live one.

The rows that hold the gates are **nudge-armed** watchers: `daemon/tools/instance_ops.py:609-752` (`_register_nudge_completion_watcher`) mints a `dependency_watchers` row for any nudge, with **no tree guard and no self guard** (contrast the spawn-lane arm at `daemon/tools/instance.py` which is tree-guarded). The bus has exactly three fire primitives (`emit_terminal`, `emit_terminal_for_child_instance`, `fire_for_terminated_target`) and the only target-agnostic one is reachable **only from TERMINATE** (`daemon/services/instance_lifecycle.py:385`). So a nudge watcher whose watched worker *completes* can never fire — and a nudge watcher whose watched worker *is the nudger itself* can never fire for any reason.

Result: the parent re-parks at every turn end. It is reachable and it answers — **the wedge is not fatal**, it is a permanent loop plus a misleading hourly notice.

---

## SCOPE DRIFT AND THE WIDENED GATE (leader ruling 2026-11-18)

**The commissioned scope was FOUR rows** (2 watchers + 2 commissions; the
original measurement is preserved in Appendix B, 23:05–23:20). **The live
measurement at 2026-11-18 ~00:30Z found EIGHT** — four `PENDING` watchers
targeting the parent and four `outstanding` commissions of the parent. The
`--emit-request` runs on the night (23:55Z and the 00:0xZ re-run) **REFUSED**
on **A8** (`count=4`, expected 2) and named exactly the four rows the
commission did not cover. **That refusal is evidence, not a fault:** the
artifact refused to widen its own authority.

**The leader RULED**: widen the gate to the **full 8-row target set**. The gate
string is now `wake-path-carrier-fix/live-wedge/17efd2ef/w8`; any other value
refuses byte-exactly. Ceremony-time re-inspection is the guard against FURTHER
drift: **A8 is now a drift guard** — every `PENDING` watcher targeting the
parent and every `outstanding` commission of the parent must be INSIDE the
8-row set. An UNKNOWN extra row refuses the emit and is listed precisely; the
scope is never auto-extended.

| # | Kind | Row (prefix) | Role |
|---|---|---|---|
| W1 | `dependency_watchers` | `8a1f60d7` | source task `39320` (completed) → child sonic-leader `542b49a6` (completed) |
| W2 | `dependency_watchers` | `999efa71` | source task `39424` (completed) → child == the parent (self-nudge) |
| W3 | `dependency_watchers` | `5602e91d` | source task `39564` (completed) → child leader `82551f59` |
| W4 | `dependency_watchers` | `c83e12a2` | source task `39564` (completed) → child leader `82551f59` |
| M1 | `mission_commissions` | `0cdc0b31` | worker sonic-leader `542b49a6` (terminal) → `worker_terminal_no_report` |
| M2 | `mission_commissions` | `90493206` | worker == the parent (non-terminal) → `ttl_expired_not_live` |
| M3 | `mission_commissions` | `06001f88` | worker leader `82551f59` (non-terminal) → `ttl_expired_not_live` |
| M4 | `mission_commissions` | `a1561f19` | worker leader `82551f59` (non-terminal) → `ttl_expired_not_live` |

> ### ⚠️ PROMINENT DISCLOSURE — this repair closes ledger entries for IN-FLIGHT work
>
> Two of the four commissions (`06001f88`, `a1561f19`) belong to **this
> commission's own lane** (worker = leader `82551f59`, still working).
> Abandoning them closes the **mission-ledger** entries for in-flight work; it
> does **NOT** block that lane's report from reaching Ari — the report routes
> through the session lane, not the mission ledger. Without the repair, lane 7
> TTL-abandons them at ≈04:52–04:53Z anyway, so the widened repair accelerates
> the system's own behavior; it does not change its outcome.

**The ruling's justification (VERBATIM, leader 2026-11-18):**

1. All 4 watchers sit on TERMINAL source tasks (39320, 39424, 39564x2). The boot-only orphan sweep's own predicate (task-status-based, state='PENDING', source_task_id not in running/pending/paused) would CANCEL all 4 at the next daemon restart. Cancelling them now is the system's own semantics, not an invention — AND it stamps enqueued_at, which the sweep does NOT do, closing the re-arm hole that would otherwise let 3 of 4 rows resurrect on a future pause+resume. So the repair is STRICTLY SAFER than the restart path for the watcher gate.
2. All 4 commissions drain naturally anyway: 90493206 by TTL ~02:30Z, 06001f88/a1561f19 by TTL ~04:52-04:53Z, 0cdc0b31 on lane-7's next pass — all via lane 7's own sanctioned reasons. The repair merely reaches the same end-state ~3.5h earlier, atomically, with a receipt.
3. Two of the four commissions (06001f88, a1561f19) belong to THIS commission's own lane (worker = leader 82551f59, still working). DISCLOSE THIS PROMINENTLY: abandoning them closes the ledger entries for in-flight work; it does NOT block the lane's report from reaching Ari (the report routes through the session lane, not the mission ledger). Without the repair, lane 7 TTL-abandons them at ~04:52-04:53Z anyway — so the widened repair accelerates the system's own behavior; it does not change its outcome.
4. A 4-row repair that leaves both gates > 0 is not 'ready' — it is a partial that would fail at the moment of execution. Ari asked for READY.

**Original commission record:** the 4-row commission text lives in Appendix B
(23:05–23:20 and 23:41–23:54 entries) and in the `--emit-request` refusals
preserved under `observations/emit_request_live_20261117T235550Z.txt` and
`review/inspect_rerun_20261118T001608Z.txt`. Those artifacts are kept as the
audit trail for *why* the gate was widened.

---

## 2. CEREMONY-CLASS — who may execute this

> **The repair's execution is 3-factor gated — user attestation (`user_confirmed`), an audit stamp, and a dry run — and the Emperor's word is the ONLY gate.**
>
> **This artifact never executes anything.** `live_wedge_repair.py` has **no `--apply`**, contains no DML execution path, and opens every database connection with `options='-c default_transaction_read_only=on -c statement_timeout=15000'` and then asserts `SHOW transaction_read_only` is ON before it runs a single query. It writes exactly one kind of thing: files under `repair/emitted/` (and `repair/selftest/` for the offline self-test).
>
> A request file appearing under `repair/emitted/` means **"a proposal exists"**, never **"a change happened"**.

Classification: **ceremony-class, not operator-routine.** The wedge is not fatal (§1), so there is no incident-clock forcing a unilateral fix.

---

## 3. INSPECT — exact command sequence

```bash
cd /home/nea/ensemble-src-wt-wake-path-carrier-fix
.venv/bin/python .agents/shared/planning/wake-path-carrier-fix/repair/live_wedge_repair.py \
    --inspect \
    --env-file /home/nea/agents-ensemble/.env
```

* Reads `POSTGRES_*` out of the env file **in-process**; it never prints any credential value (only `current_database()` / `current_user`).
* Mandatory identity gate: `SELECT current_database()` must equal `--require-db` (default `ensemble_prod`), printed before any row query. Any other database name ⇒ hard fail, exit 3.
* Add `--json` to also dump the full machine-readable observation (assertions + every row + the extended census + the inventory fingerprint).
* `--inspect` **never refuses on data grounds** — it reports. Exit 0 means "observation completed", regardless of classification.

**Expect one of:** `classification = not_yet` / `partial` / `already_repaired` / `unexpected`.

---

## 4. EMIT — exact command sequence (gate flag mandatory)

```bash
cd /home/nea/ensemble-src-wt-wake-path-carrier-fix
.venv/bin/python .agents/shared/planning/wake-path-carrier-fix/repair/live_wedge_repair.py \
    --emit-request \
    --gate wake-path-carrier-fix/live-wedge/17efd2ef/w8 \
    --env-file /home/nea/agents-ensemble/.env
```

* Without `--gate wake-path-carrier-fix/live-wedge/17efd2ef/w8` (byte-exact) it refuses and writes nothing.
* Still fully read-only. It runs A1–A14, prints the per-row disposition table, and then:
  * **all 8 stale (`not_yet`)** ⇒ writes `repair/emitted/live_wedge_repair_request__<UTC-ts>.sql`, `.receipt.md`, `.receipt.json` — up to **8 repair blocks** (+ any conditional backfill block); exit 0.
  * **some rows already resolved (`partial`)** ⇒ writes the request for the **remainder only**; every row's disposition (and the chosen abandon reason / worker status) is disclosed in the console output and in the receipt; exit 0.
  * **all 8 resolved (`already_repaired`)** ⇒ prints `ALREADY REPAIRED / NO-OP`, writes nothing, exit 0 (§7).
  * **anything else (`unexpected`)** ⇒ refuses, prints every failed assertion with observed-vs-expected **plus every UNKNOWN row holding the gate (A8 drift guard)**, writes nothing, exit 4.
* **Operational note (measured, real):** A1 and A9 require the parent to be observed in `waiting_children` at that instant. The live parent alternates `running ⇄ waiting_children` (Appendix B measured a 17-minute `running` window with an unclaimed queued task). During a `running` window `--emit-request` correctly refuses. **Do not relax A1/A9** — wait for the re-park and re-run. A refusal here is the assertion doing its job, not a script fault. (`already_repaired` is checked before the parent gate, so a parent that already woke after the rows resolved is a clean NO-OP, not a refusal.)

---

## 5. Hand-off to the gated repair path — what actually exists in source

**A gated DB-repair execution path EXISTS** (source determination S5):

| Fact | Citation |
|---|---|
| Tool: `ens_db_repair_execute(sql, confirm, nonce, target_label, dry_run)` — "three independent gates ALL must clear before commit" | `daemon/tools/ens_db_tools.py:939-1180` |
| Gate 0 — kill-switch `ENSEMBLE_REPAIR_ENABLED` (default ON; flag-OFF = byte-identical no-op) | `daemon/tools/ens_db_tools.py:137-190` (`_resolve_repair_enabled`), gate site `:996-1002` |
| Gate 1 — input validation: migration-runner trap, idempotency (`DO $$` blocks classify as **OTHER** and pass the idempotency gate's fall-through; DML must avoid `now()`/`current_timestamp`/`random()`…), self-surgery refusal (calling instance id literal / audit-sensitive tables `instances`, `messages`, `message_queue`, `tasks`, `events`, `schema_migrations`) | `daemon/tools/ens_db_tools.py:401-445` (`_is_idempotent`), `:253-261` (`_SELF_SURGERY_TABLES`), `:461-468` (`_refuse_self_surgery`) |
| Gate 2 — single-use confirm nonce, 5-minute TTL, action-bound to `target=<target_label>`; **the DRY RUN mints it, the confirm call consumes it** | `daemon/tools/ens_db_tools.py:498+` (`_RepairNonceStore`), gate site `:1023-1040` |
| Audit — every dry-run and every commit writes `repair_log` in the SAME transaction, fail-closed | `daemon/tools/ens_db_tools.py` (module docstring §"Audit contract"), `daemon/repositories/ens_db/models.py:45` |
| Who can call it: the privileged `ens-db` category, allow-listed on **`maintenancer` only** | `agents/maintenancer/meta.json` → `tools.allow` contains `"ens-db"` |

**There is NO tool that executes a request FILE.** No `ens_db_repair_apply`, no "run this .sql" surface. The `.sql` file is a human-reviewable carrier; execution is **statement by statement** through `ens_db_repair_execute`, one dry-run + one confirm per block.

**Operator path under Emperor authorisation (the only one):**

1. Emperor reads `…__<ts>.receipt.md` and authorises (the `user_confirmed` factor).
2. The authorised agent (a `maintenancer` instance — **mandatory**; sole holder of `ens-db`) submits **block 1** with `dry_run=True`, `target_label="wake-path-carrier-fix/live-wedge/17efd2ef/w8#block1"` and the **raw `DO $$ … $$;` block body** (never a `*.sql` path — rejected by the migration-runner trap). Expect `sql_class='OTHER'`, **preview skipped (NO projected rowcount)**, and a minted nonce **valid for 300 s**.
3. **Within 300 s:** same block with `confirm=True`, `dry_run=False`, `nonce=<from step 2>` ⇒ commits (`repair_log.sql_class='OTHER'`). The first SQL parse/execution happens HERE, on confirm.
4. Repeat for blocks 2..N in the file's order (one block per call — caller-enforced).
5. Run §6 `--verify`. If interrupted mid-sequence, safely resume from the next unapplied block.

**Wrong-session signal:** `dependency_watchers` / `mission_commissions` are NOT in `_SELF_SURGERY_TABLES`, so `_refuse_self_surgery` should not fire on blocks ④/⑤ — but councilors disagreed, so treat any self-surgery refusal as a **WRONG-SESSION signal, not a repair failure** (re-check the identity is `maintenancer`).

Every block is `WHERE <full PK> AND <current state>`-guarded and idempotent: a block that has already landed yields **0 rows** and raises nothing, so an interrupted sequence is safe to resume.

**Why every block is a `DO $$` block instead of a bare `UPDATE`:** the executor's idempotency gate rejects DML containing `now()` (`_NON_IDEMPOTENT_TOKENS`, `ens_db_tools.py:229-241`) — and these stamps must be evaluated at **execution** time, not emit time. At `eca98397d`, `DO $$ … $$;` is classified **OTHER**, not DDL (`_classify_sql` first-word heuristic, `ens_db_tools.py:267-293`), which has two consequences: (a) the **dry run takes the preview-skipped branch** (`:1548-1552`) — it mints the nonce but returns **no projected rowcount**, and the first SQL parse/execution is on `confirm=True`; (b) `repair_log.sql_class` records **`'OTHER'`**. The block still keeps the guarded `WHERE`, keeps `now()` at execution time, reports rowcount via `GET DIAGNOSTICS` (0 ⇒ `NOTICE: no-op`, 1 ⇒ repaired, >1 ⇒ `EXCEPTION` which rolls the transaction back), and passes the idempotency gate via the OTHER-class fall-through. **Declared trade-off:** `repair_log` carries `sql_class='OTHER'` and no before/after row image; the compensating control is the inventory `sha256` / parent-scoped row diff, re-checked by `--verify` (§6, check 4). *(The emitter's `_do_block` docstring still says "classified DDL" — same overstatement, deferred to the next emitter touch; the emitter is NOT edited here.)*

---

## 6. VERIFY — exact command sequence

```bash
cd /home/nea/ensemble-src-wt-wake-path-carrier-fix
.venv/bin/python .agents/shared/planning/wake-path-carrier-fix/repair/live_wedge_repair.py \
    --verify \
    --env-file /home/nea/agents-ensemble/.env
# or point at an explicit receipt:
#   --verify --receipt …/repair/emitted/live_wedge_repair_request__<ts>.receipt.json
```

Read-only. With no `--receipt` it uses the newest `*.receipt.json` under `emitted/`. Checks, each printed `PASS`/`FAIL`:

1. **0 PENDING watchers remain for the parent** — via the *mirrored* predicate (`repository.py:506-515`), not an ad-hoc count.
2. **0 outstanding commissions remain for the parent** — via the *mirrored* predicate (`count_outstanding_commissions_for_anchor`, `mission_record/repository.py:436-448`).
3. **The parent row is untouched BY THE REPAIR** — identity/creation fields (`instance_id`, `agent_id`, `parent_id`, `created_at`) must be identical to the receipt snapshot; a difference in `status`/`updated_at`/`version` is reported explicitly as `advanced-consistent-with-natural-wake` (the repair never writes `instances`, so any movement there is the daemon's own).
4. **The parent-scoped inventory is unchanged** — the check compares the receipt's ordered row dumps of `dependency_watchers` EXCLUDING the two targets and `mission_commissions` EXCLUDING the two targets, as `added` / `removed` / `changed` sets (e.g. `changed=['5602e91d-…']`), **not** a sha256 equality. The receipt also carries a `parent_scoped_sha256`, but that digest mixes in the parent's key fields (`status` / `updated_at` / `version`), so it is **not** stable across the parent's natural `running ⇄ waiting_children` cycle and must not be compared across runs — the row diff is the hard anchor. (Reviewer proof: `repair/review/digest_probe_*.txt` — the row-only digest was byte-identical across ~20 minutes while the recorded sha256 moved `d79ccfce…` → `2cf0274e…`, the difference being carried entirely by `status`/`updated_at`.)

Exit 0 = all PASS; exit 5 = at least one FAIL (then re-run `--inspect` and diff — **do not re-submit the request blindly**).

**What the daemon does next (stated honestly by `--verify` itself):** the parent leaves `WAITING_CHILDREN` **at its next turn end**, because the park predicate then reads 0 and the mission leg reads 0. **The repair forces nothing** — it removes the two blockers. A turn must actually occur; a queued-but-unclaimed task (Appendix B observed one for 17 minutes) still needs the worker pool to claim it, and nothing in this repair creates a turn.

---

## 7. What "ALREADY REPAIRED / NO-OP" means

Per-row disposition is evaluated **before** any statement is built (the
leader's partial-resolution boundary — the gate must not be brittle against
rows the system itself resolves):

| Row disposition | Condition | Behaviour |
|---|---|---|
| `in_scope_stale` | watcher `PENDING` / commission `outstanding` | include its repair block |
| `naturally_resolved` | watcher `CANCELLED`/`FIRED`; commission `abandoned`/`satisfied`/`superseded` | EXCLUDE from statements; disclose per row (a `CANCELLED` watcher with `enqueued_at IS NULL` instead gets ONE guarded backfill block) |
| `unexpected` | any other state / missing row / unreadable worker | refuse with per-row detail, write nothing, exit 4 |

Classification from those dispositions:

| Classification | Condition | Behaviour |
|---|---|---|
| `not_yet` | all 8 stale, A1/A9 hold, A8 holds | build + write the 8-block request |
| `partial` | ≥1 stale and ≥1 resolved, A1/A9 hold, A8 holds | build + write the **remainder-only** request, disclose every disposition |
| `already_repaired` | all 8 resolved, A8 holds | print `ALREADY REPAIRED / NO-OP`, **write nothing**, exit 0 (the parent may already have woken — success, not refusal) |
| `unexpected` | unexpected disposition, identity mismatch, A1/A9 fail, or an UNKNOWN row (A8) | refuse + per-row detail, write nothing, exit 4 |

A second run after a successful repair therefore **cannot** emit a second, conflicting request file. It also cannot "re-repair": the guards are `state='PENDING'` / `state='outstanding'`, so re-running an applied block is a 0-row no-op.

---

## 8. Blast radius

**This request touches up to 8 rows in 2 tables (was 4 before the leader ruling):**

| # | Table | Row (full PK) | Change |
|---|---|---|---|
| 1 | `dependency_watchers` | `8a1f60d7-…` (resolved by prefix, uniqueness asserted) | `PENDING → CANCELLED`, `fired_at` + `enqueued_at` stamped |
| 2 | `dependency_watchers` | `999efa71-…` | `PENDING → CANCELLED`, same stamps |
| 3 | `dependency_watchers` | `5602e91d-…` | `PENDING → CANCELLED`, same stamps |
| 4 | `dependency_watchers` | `c83e12a2-…` | `PENDING → CANCELLED`, same stamps |
| 5 | `mission_commissions` | `0cdc0b31-…` | `outstanding → abandoned`, `resolved_at` stamped, `abandon_reason='worker_terminal_no_report'` |
| 6 | `mission_commissions` | `90493206-…` | `outstanding → abandoned`, `resolved_at` stamped, `abandon_reason='ttl_expired_not_live'` |
| 7 | `mission_commissions` | `06001f88-…` | `outstanding → abandoned`, `resolved_at` stamped, `abandon_reason='ttl_expired_not_live'` |
| 8 | `mission_commissions` | `a1561f19-…` | `outstanding → abandoned`, `resolved_at` stamped, `abandon_reason='ttl_expired_not_live'` |

A row already resolved at emit time is **excluded** from the statements and
disclosed in the receipt (`partial`). A watcher a pre-ceremony boot sweep
already `CANCELLED` with `enqueued_at IS NULL` instead gets **one guarded
backfill block** (`state='CANCELLED' AND enqueued_at IS NULL` ⇒ stamp) — the
same end state the repair's own cancel writes.

**NOT touched, by construction:**

* **the parent instance row** (`instances` — excluded by design; no statement names that table, which is also why the executor's self-surgery table set is not tripped);
* `task`, `message_queue`, `messages`, `report_injections`, `job_queue_items`, `events`, `schema_migrations`;
* **no rows are deleted** — every statement is a state transition; nothing vanishes from the audit trail;
* no schema change, no migration, no daemon restart, no `releases/`, no `state.json`, no promote field;
* **the two out-of-scope 2026-11-12 missions are never named in any statement** (§11).

**Scope note that must be read before authorising:** the target set is now the
**full 8 rows** that hold the parent's two gates (see "SCOPE DRIFT AND THE
WIDENED GATE"). If a NINTH row appears, the A8 drift guard refuses and names
it; this artifact never widens its own authority.

---

## 9. ROLLBACK — read this before authorising

**Prior state (what the rows look like today):**

| Row | Prior state |
|---|---|
| `8a1f60d7` | `state='PENDING'`, `fired_at=NULL`, `enqueued_at=NULL`, `created_at='2026-11-17T18:06:07.999373+00:00'` |
| `999efa71` | `state='PENDING'`, `fired_at=NULL`, `enqueued_at=NULL`, `created_at='2026-11-17T20:30:47.131454+00:00'` |
| `0cdc0b31` | `state='outstanding'`, `resolved_at=NULL`, `satisfied_at=NULL`, `abandon_reason=NULL`, `created_at='2026-11-17T18:06:07.951608+00:00'` |
| `90493206` | `state='outstanding'`, `resolved_at=NULL`, `satisfied_at=NULL`, `abandon_reason=NULL`, `created_at='2026-11-17T20:30:46.971252+00:00'` |
| `5602e91d` | `state='PENDING'`, `fired_at=NULL`, `enqueued_at=NULL`, `created_at='2026-11-17T22:51:59.886304+00:00'` |
| `c83e12a2` | `state='PENDING'`, `fired_at=NULL`, `enqueued_at=NULL`, `created_at='2026-11-17T22:53:05.940442+00:00'` |
| `06001f88` | `state='outstanding'`, `resolved_at=NULL`, `satisfied_at=NULL`, `abandon_reason=NULL`, `created_at='2026-11-17T22:51:59.855255+00:00'` |
| `a1561f19` | `state='outstanding'`, `resolved_at=NULL`, `satisfied_at=NULL`, `abandon_reason=NULL`, `created_at='2026-11-17T22:53:05.912797+00:00'` |

**Reversibility — stated plainly, no free lunch:**

* The **data** is recoverable: the receipt records every prior value, and the transitions are reversible by hand (`state='outstanding'` / `state='PENDING'`, nulls restored). Nothing is deleted; no cascade fires; `repair_log` keeps the audit rows.
* **But this IS a one-way door in the operationally meaningful sense.** "Rollback" means **re-inserting a PENDING watcher row targeting the parent — which RE-WEDGES the parent.** You are not restoring a neutral configuration; you are restoring the defect. Say it out loud before authorising: *rollback = re-wedge.*
* Restoring `0cdc0b31`/`90493206` to `outstanding` likewise re-arms the mission gate and re-parks the parent.
* **What rollback cannot restore:** anything the cancelled watcher would have delivered. The `enqueued_at` stamp is the "never delivered" tombstone; a re-inserted row is a *new* obligation, not the original one.
* **Residual re-arm hazard that the repair deliberately closes:** the only code path that can resurrect a `CANCELLED` row is the pause/resume durability hook — `find_cancelled_unenqueued_for_target` (`repository.py:797-843`, candidate predicate `state='CANCELLED' AND enqueued_at IS NULL`) feeding `_rearm_cancelled_watchers_for_resumed` (`daemon/services/instance_lifecycle.py:4125-4260`), which skips rows whose child is terminal (`:4230-4239`) and re-arms the rest (`:4240-4241`). For `8a1f60d7` the child (`542b49a6`, `completed`) already fails that guard. For `999efa71` the child **is the parent** — non-terminal — so `enqueued_at IS NULL` alone would make a future pause+resume rebuild the self-watcher. That is exactly why both blocks stamp `enqueued_at`: the repair is inert against the resurrection path, not merely quiet against the fire paths. **Reverting that stamp re-opens the resurrection path.**

---

## 10. RESTART ALTERNATIVE

**Question:** would a daemon restart cancel these two stale watcher rows?
**Answer: YES — the boot sweep WOULD cancel them, and it cancels the two same-class EXTRAS at the same time (4 of 4 parent watchers). And it still would NOT release the parent — but the reason is NOT the one previously stated in this section: after the restart the BUS gate is CLEAR (`count_pending_for_target(parent) = 0`) and the MISSION gate alone still holds the parent.** Both halves are measured from source and re-measured live; they are not contradictory.

| Fact | Citation |
|---|---|
| A **boot-only** orphan sweep exists and runs inside `DependencyBus.start()` | `daemon/services/dependency_bus.py:2501` (`_sweep_orphan_watchers` def), called once at `:1753` from `start()` (`:1699`); `start()` is invoked from `init_dependency_bus` at `daemon/api.py:5371` |
| Its **exact predicate** (one atomic conditional UPDATE) | `daemon/services/dependency_bus.py:2653-2666`: `UPDATE dependency_watchers SET state='CANCELLED', fired_at=:now WHERE state='PENDING' AND created_at < :cutoff_iso AND source_task_id NOT IN (SELECT CAST(id AS TEXT) FROM task WHERE status IN ('running','pending','paused'))` |
| Predicate shape | **source-task-status-based**, plus a **30s age grace** (`DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS`, `daemon/services/orphan_watcher_sweep.py:82`; startup path hard-defaults to 30s and ignores the config knob — `daemon/config.py:2739-2752`). **No waiting-children grace window.** |
| The live rows against that predicate | source tasks `39320` and `39424` are both `completed` (`task.completed_at` = 18:08:26 / 20:31:27) ⇒ `NOT IN (active set)` ⇒ **both rows would be CANCELLED at boot.** Read-only re-measurement of the predicate (`SELECT … older_than_grace, orphan_by_predicate`) returned `t/t` for both. **The two same-class EXTRAS are swept too:** they are keyed on source task `39564`, whose `task.status` is likewise `completed` ⇒ `NOT IN (active set)` ⇒ the boot sweep cancels **4 of 4** of the parent's PENDING watchers, not 2. Reviewer re-measurement 2026-11-18T00:2xZ (`repair/review/r8_census_*.txt`): `older_than_grace = t` and `orphan_by_predicate = t` for **4 of 4** rows (`8a1f60d7`/39320, `999efa71`/39424, `5602e91d`/39564, `c83e12a2`/39564). |
| Does the sweep re-home / wake the parent? | **No.** It transitions state only; it enqueues nothing, emits nothing, and touches no `instances` row (`dependency_bus.py:2653-2678`). |
| Is there also a periodic sweep? | Yes — `OrphanWatcherSweepService` (`daemon/services/orphan_watcher_sweep.py`), **but it is permanently inert on the live build**: it is constructed with `dependency_bus=get_dependency_bus()` at `daemon/api.py:1919-1920`, which runs **before** `set_dependency_bus(bus)` at `daemon/api.py:5710` inside `init_dependency_bus` (called at `:2972`). Every tick therefore logs `dependency_bus is not wired … relying on the startup sweep` and cancels nothing — observed live (log `data/logs/ensemble.log`, ticks 228–232, e.g. `23:17:55 … tick 232`). |

**So: restart cancels the rows — YES. Restart unwedges the parent — NO.** Reason, precisely (reviewer re-derivation, 2026-11-18): the boot sweep cancels **all four** of the parent's PENDING watchers — the two commissioned ones (source tasks `39320` / `39424`) *and* the two same-class extras (source task `39564`) — because the predicate is **source-task-status-based** and `39564` is `completed` exactly like the other two (read-only re-measurement: `older_than_grace = t`, `orphan_by_predicate = t` for 4 of 4). After the restart `count_pending_for_target(parent) = 0`, so the **bus gate is CLEAR**: the park site `daemon/services/message_processing_pipeline.py:1329` reads 0 and never fires, and the root-lane completion defer `daemon/services/child_reports.py:3511` does not defer either. What still holds the parent is the **mission gate alone**: the boot sweep never touches `mission_commissions`, so **all four** commissions (`0cdc0b31`, `90493206`, `06001f88`, `a1561f19`) are still `outstanding` at the first turn after the restart — `count_outstanding_commissions(parent) = 4 > 0` — and `_root_completion_gate` (`daemon/services/child_reports.py:2691-2695`) refuses the publish and downgrades the instance back to `WAITING_CHILDREN` (`daemon/services/child_reports.py:5554-5577`). *(This paragraph previously claimed the bus gate also stayed `> 0` because of the two extras; that was wrong — the extras are swept by the same predicate. The outcome "restart does not unwedge" is unchanged; the holder is the mission gate alone.)*

**What a restart actually leaves behind — and when it drains.** The mission gate is not permanent, but it is slow, and the restart buys it for free only because the sweep releases the mutual HOLD: lane 7's bus-watcher probe is a **per-pass** decision, re-evaluated against the *current* watcher set (`daemon/services/report_delivery_recovery.py:2833-2841`, probe → `DependencyBus.pending_watchers_for_pair` (`daemon/services/dependency_bus.py:1271-1290`) → `fetch_pending_for_target_and_child` (`daemon/repositories/dependency_bus/repository.py:239`), PENDING-only). Once the four watchers are `CANCELLED` the HOLD releases and the commissions proceed on their own sanctioned legs: `0cdc0b31` → lane 7's **R1** (worker `542b49a6` is terminal, nothing was delivered) ⇒ `abandoned / worker_terminal_no_report` at the next lane-7 pass (`daemon/services/report_delivery_recovery.py:2929-2937`); `90493206` → **R2b** TTL (created `2026-11-17T20:30:46Z` + 21600s ≈ **02:30Z**) ⇒ `abandoned / ttl_expired_not_live` (`:2941-2947`, TTL default `daemon/config.py:2401-2421`, `daemon/constants.py:715`); the two extras → their own TTLs (created 22:51:59Z / 22:53:05Z ⇒ ≈ **04:52Z / 04:53Z**). So the mission gate reaches 0 at ≈ **04:53Z**, and only then — on a subsequent turn (or on the `MissionStrandHealer`'s production-completion drive, `daemon/services/mission_strand_healer.py:1225-1265`, whose own bus leg is satisfied once the count is 0) — can the parent leave `WAITING_CHILDREN`.

**The restart is also a WEAKER repair than the repair: it re-opens the resurrection path (§9) that the repair closes.** The boot sweep writes `state='CANCELLED', fired_at=:now` and leaves `enqueued_at` NULL (`daemon/services/dependency_bus.py:2653-2666` sets no `enqueued_at`), so all four swept rows immediately become re-arm candidates for `find_cancelled_unenqueued_for_target` (`daemon/repositories/dependency_bus/repository.py:797-843` — `state='CANCELLED' AND enqueued_at IS NULL`). On a later pause+resume of the parent, `_rearm_cancelled_watchers_for_resumed` (`daemon/services/instance_lifecycle.py:4125-4260`) re-arms every one of them whose child is still non-terminal (terminal-child skip `:4229-4239`, re-arm `:4240-4241`) — that is **three of the four**: `999efa71` (child = the parent itself, `waiting_children`), `5602e91d` and `c83e12a2` (child `82551f59`, `waiting_children`). Only `8a1f60d7` is protected, because its child `542b49a6` is `completed`. A restart therefore leaves the bus gate re-armable, while the repair's `enqueued_at` stamp closes it for good.

**And the deeper reason a restart is not a substitute for the repair (S6): cancel ≠ fire.** Nothing in the restart path delivers the report sonic-leader owed Ari. The delivery lanes are all keyed on a durable obligation row or an inbound message, and the Ari root has **neither**:

* `report_injections` for the parent: **0 rows** (all-time, measured) — so RDRS lanes 1/3/4/5 (`daemon/services/report_delivery_recovery.py:1216-1272`, `:1233-1243`, lane predicates documented at `:1201-1215`) admit nothing;
* the no-row backstop (Lane 2) admits *completed children of non-terminal parents* — the parent has **0 child instances** (`instances.parent_id` = none matching), so it admits nothing;
* `message_queue` for the parent: 0 pending/ready/processing rows for report traffic.

⇒ **A restart is Emperor-owned, ceremony-class, and out of scope for this script. It would clear two of row-states and deliver nothing.**

---

## 11. Out-of-scope: the 2026-11-12 commissions (and the drift this lane measured)

**§11.1 — Out-of-scope rows, observation only.** Three `mission_commissions` rows created 2026-11-12 by commissioner `4e261d34-b350-4315-a8bb-1ae96205ab3a` (a **terminated** Maintenancer instance) are still `outstanding`:

```
f4abf712-92a9-5037-936f-558b57f5f9b8  worker a16dc01c  lane instance_nudge  2026-11-12T06:57:50Z
2fc563f9-eb69-59e8-8218-23733966d7c3  worker a16dc01c  lane instance_nudge  2026-11-12T06:57:50Z
6f529835-77c4-5315-a82d-fdc16b334267  worker 804085bf  lane instance_nudge  2026-11-12T07:43:17Z
```

They are **never named in any repair statement** and are not part of this request's target set. They are listed by `--inspect` under `OUT-OF-SCOPE` purely so the operator sees the whole ledger. They are the daemon's own problem twice over: their commissioner is terminated and their mission epochs are unrelated to the Ari root. (`--inspect` also shows the two *satisfied* and two *abandoned* siblings from 2026-11-11, which is the evidence that lane 7 does resolve this family over time.)

**§11.2 — The measured drift (this is the decision the Emperor actually faces tonight).** The commission's premise was "2 PENDING watchers + 2 stale missions, and repairing them releases the parent". At measurement time (2026-11-17T23:30–23:50Z) the population had **doubled**:

| Gate | Target set (this request) | Same-class extras holding the gate |
|---|---|---|
| `count_pending_for_target(parent)` = **4** | `8a1f60d7` (task 39320 → child 542b49a6), `999efa71` (task 39424 → self) | `5602e91d` + `c83e12a2` — both source task **39564** (status **`completed`**, 23:03:54) → child `82551f59` (leader, `waiting_children`). Armed 22:51:59.886 and 22:53:05.940 — i.e. **during the investigation**, by the same recurring self-nudge behaviour. |
| `count_outstanding_commissions(parent)` = **4** | `0cdc0b31` (worker 542b49a6), `90493206` (self) | `06001f88`, `a1561f19` — worker `82551f59`, minted 22:51:59 / 22:53:05, mission `cb904c16` |

Both extra watchers are **already unfireable** (their source task is terminal, and the child is not a structural child of the parent, so F1/F2 cannot match and F3 is terminate-only) — the same defect class, past the point of no return. Their commissions are held by lane 7's bus-watcher probe (`report_delivery_recovery.py:2841-2859`: a PENDING watcher on the (commissioner, worker) pair ⇒ HOLD), so **the watchers and the commissions hold each other open.**

Consequences, stated plainly:

1. **Repairing the four commissioned rows does not release the parent** (2 + 2 remain). `--emit-request` therefore refuses on today's data (A8 = `count=2`, observed `count=4`; A1/A9 also refuse while the parent is mid-turn) and names these exact rows.
2. **A restart does not release it either** (§10) — but the holder differs and the tail is slower: the boot sweep cancels **all four** PENDING watchers (the two commissioned ones *and* the two extras — their source task `39564` is terminal too), so the **bus gate clears**; what still holds the parent is the **mission gate alone**, with **all four** commissions still `outstanding` (the boot sweep never touches `mission_commissions`). That gate then drains only on the sanctioned legs: `0cdc0b31` via lane 7's R1 (terminal worker) at the next lane-7 pass, `90493206` at its TTL ≈ 02:30Z, the two extras at their TTLs ≈ 04:52Z / 04:53Z. And the restart leaves **three of the four** swept rows re-armable on a later pause+resume (§10) — a hole the repair closes.
3. **Widening the target set was a NEW Emperor/leader decision — and it HAS been made.** The machinery is reusable (`live_wedge_repair.py` derives its statements from the observed rows; re-pinning the constants and re-running `--emit-request` under the widened gate produces the same block shape for the extra rows). **Leader ruling 2026-11-18 widened the gate to the full 8-row set** — see "SCOPE DRIFT AND THE WIDENED GATE". The artifact still refuses to silently absorb anything beyond that set: a ninth row trips A8.

**§11.3 — OUT-OF-SCOPE OBSERVATION (recorded, routed, NOT part of this repair).**
The parent's wake task `39632` (`process_message`, enqueued 23:30:03Z by the
`system:watchdog:wedge` notice) was observed **unclaimed for 17+ minutes**
during the 2026-11-17 sampling window (Appendix B) while the parent sat in
`running`. That is a **worker-pool claim-latency** observation, not a row-state
gate — it is NOT in the 8-row target set, no statement here touches `task` or
`message_queue`, and it has been **routed to the leader** (being routed
onward). Do not fold it into this repair.

---

## AMENDMENT 2026-11-18 (post-natural-resolution)

**Emperor attestation:** "Repair", 2026-11-18, Discord; board item `wi-8f60e6f1`.

The world moved again: all four watchers were cancelled by the boot sweep / lane 7, M1+M2 were abandoned, and only M3+M4 remain outstanding. The emitter was amended minimally (before-copy preserved at `repair/amendment/before/live_wedge_repair.py`, sha256 `a564d67a179882f1efb63f498883ebc868c8c589f13e2786740c6500b0447fe3`).

### Two relaxations (with justification)

| assertion | what changed | why it is NOT an unknown world |
|---|---|---|
| **A1 / A9** (parent status) | **INSPECTION tolerates `running`** (recorded; exactly ONE re-read, no loop; any OTHER status or a missing parent refuses). **EMISSION intentionally requires a settled `waiting_children` window** — the classifier still refuses a mid-turn parent (`live_wedge_repair.py:1109-1113`), fail-closed by design, council-affirmed. | the parent alternates `running ⇄ waiting_children` on its hourly turn cycle (Appendix B: 17-minute `running` window). Mid-turn is a documented, self-resolving posture FOR INSPECTION; emission deliberately waits for a settled dock so the proposal is built against a stable world. |
| **A3 / A6 / A14** (source-task existence) | for a watcher still `PENDING` (would be changed by this request) a missing source task remains an UNKNOWN → refuse. For an already-resolved (`CANCELLED`/`FIRED`) watcher a missing source task is an EXPECTED GC artifact → pass, annotated `source_task GC'd after resolution` | a resolved watcher's terminal state is recorded in the row itself and a resolved row can no longer fire (all three fire primitives match `state='PENDING'` only). The source task is no longer needed to prove anything; its GC is housekeeping, not a world change. |

### Kept exactly as-is (rule (c))

A7 identity semantics, A8 drift-guard unknown-count semantics, expected-rowcount semantics (repair blocks expect 1; 0 = idempotent no-op NOTICE; >1 = EXCEPTION), the guarded `DO $$` + `GET DIAGNOSTICS` form. **If the drift guard would refuse → report and stop; do not widen it.**

### New expected population (derived from the target set's dispositions, not hardcoded)

- PENDING watchers for the parent: **0** expected (all four resolved).
- outstanding commissions for the parent: **2** expected (M3, M4).
- unknown = 0 means no PENDING watcher / outstanding commission of the parent exists OUTSIDE the 8-row target set — unchanged semantics, new expected numbers.

### Backfill verdict (from the code, re-measured at `eca98397d`)

Re-arm candidate predicate (`find_cancelled_unenqueued_for_target`, `repository.py:797-843`): `state='CANCELLED' AND enqueued_at IS NULL`. Re-arm skip (`instance_lifecycle.py:4214-4246`): `if child.status in TERMINAL_STATUSES: continue`, where `TERMINAL_STATUSES = {terminated, completed, error, failed}` (`job_queue_service.py:126-131`); a missing child row is also skipped (`child is None -> continue`).

| watcher | child | child status | verdict |
|---|---|---|---|
| W1 `8a1f60d7` | `542b49a6` (sonic-leader) | `completed` → TERMINAL | **skip-protected → NO backfill** |
| W2 `999efa71` | the parent itself | non-terminal | **re-arm eligible → backfill** |
| W3 `5602e91d` | `82551f59` (leader) | non-terminal | **re-arm eligible → backfill** |
| W4 `c83e12a2` | `82551f59` (leader) | non-terminal | **re-arm eligible → backfill** |

Backfill blocks are guarded `UPDATE … SET enqueued_at=now() WHERE watch_id=<full PK> AND state='CANCELLED' AND enqueued_at IS NULL` in the same `DO $$` + `GET DIAGNOSTICS` form (rowcount 0 or 1 both fine: 1 = stamped, 0 = already-stamped no-op — idempotent).

### RECEIPT NOTE (verbatim, carried in the emitted receipt md + json + sql header)

"the wedge's BUS GATE was ALREADY OPEN (watchers cancelled by the boot sweep) before this repair; the remaining hold was the mission leg's two commissions. The Emperor's authorization is recorded on board item wi-8f60e6f1."

### Residual analysis (what could still move before ceremony)

- `82551f59` terminality: M3/M4's reason is `ttl_expired_not_live` only while the worker stays non-terminal. If it goes terminal, lane-7 semantics flip the reason to `worker_terminal_no_report` and the emitter must disclose the flip (the reason is re-derived at emit from the live worker status, never flipped silently).
- Possible new rows: the A8 drift guard **IGNORES advisory rows** (`advisory_backfill=true`) and refuses ONLY on NON-advisory unknowns (naming them). Scope is never auto-extended.
- Parent posture at ceremony: INSPECTION tolerates `running`; EMISSION requires a settled `waiting_children` window (strict, `live_wedge_repair.py:1109-1113`); any other status refuses.
- **Causal precondition:** the mission leg holds the park only under `ENSEMBLE_MISSION_RECORDS=enforce` (`daemon/manager.py:13931-13935` @ `eca98397d`; confirmed enabled in live `.env:117`).
- **Observation (note only):** lane 7's TTL backstop did NOT fire ~11 h past the 04:52/04:53Z expiry for M3/M4. Recorded as a **SEPARATE bug investigation OUTSIDE this repair** (hypothesis: `dependency_bus` wired after the sweep is constructed, `api.py:1919-1920` vs `:5710`); not investigated here.
- **`--verify` check 4 baseline is NOT empty:** the 3 advisory rows remain in the parent-scoped baseline and check 4 passes by sha256 match; check 5 (global churn) is informational only.

### Execution handoff — council-corrected 2026-11-18

> Replaces the earlier preview text. The emitted request (`…T183256Z.*`) is unchanged and sound; only the handoff text was wrong.

**Corrected procedure (VERBATIM):**

1. Run EXCLUSIVELY as maintenancer with ENSEMBLE_REPAIR_ENABLED enabled; confirm parent 17efd2ef is not mid-crisis and worker 82551f59 is still non-terminal (if terminal, HALT and RE-EMIT, never edit SQL).
2. For each block N = 1..5 in file order from live_wedge_repair_request__20261118T183256Z.sql, paste ONLY the single `DO $$ ... $$;` block body (never a .sql filename):
   - `ens_db_repair_execute(sql=<block N body>, target_label="wake-path-carrier-fix/live-wedge/17efd2ef/w8#blockN", dry_run=True)` → expect sql_class='OTHER', preview skipped (NO projected rowcount), and a minted nonce valid for 300 seconds.
   - Within 300s: `ens_db_repair_execute(sql=<block N body>, target_label="wake-path-carrier-fix/live-wedge/17efd2ef/w8#blockN", dry_run=False, confirm=True, nonce=<nonce>)` → commit (repair_log.sql_class='OTHER'). If interrupted mid-sequence, safely resume from the next unapplied block.
3. Run `live_wedge_repair.py --verify --env-file /home/nea/agents-ensemble/.env` → checks 1-4 must PASS (check 5 global churn failure is informational).

**Factual corrections encoded:** (1) `DO $$` = sql_class **OTHER** → preview skipped, no projected rowcount, nonce still minted, first parse on confirm (`ens_db_tools.py:267-293`, `:1548-1552`); (2) nonce TTL **300 s** (`:177`, `:557-561`); (3) **maintenancer mandatory** — any self-surgery refusal is a WRONG-SESSION signal, not a repair failure (`_SELF_SURGERY_TABLES` `:253-261`, `_refuse_self_surgery` `:461-468`); (4) pass ONLY the raw block body, never a `.sql` path (`:222`), one block per call (caller-enforced). The emitter's `_do_block` docstring carries the same DDL overstatement — deferred to the next emitter touch, NOT edited here.

**Guardrails:** pre-execution — if worker 82551f59 goes terminal or an unexpected NON-advisory commission appears → **RE-EMIT in a settled window, never hand-edit the SQL**. Post-execution — `--verify` checks 1-4 must PASS (check 5 informational).

**Advisories (no action now):** the emission gate is an INTENTIONAL fail-closed design (no code change this ceremony; the 1-line classifier relaxation is NOT recommended; settled-window availability ~1 h, refusal risk LOW). **FUTURE re-emission gap:** the classifier never reads `a["ok"]` (`live_wedge_repair.py:1422`, `:1776`) — wire `all(a["ok"] for a in assertions)` into the emission gate before ANY future re-emit and refresh the selftest fixture run; NOT done now. Anchor corrections applied: `find_cancelled_unenqueued_for_target` = `repository.py:797-843`; re-arm skip = `instance_lifecycle.py:4214-4246`; `transition_commission_state` = `mission_record/repository.py:724-771`.


---

## Appendix A — Source determinations (S1–S6), re-measured at `eca98397d`

| id | Determination | Citation |
|---|---|---|
| **S1** | **Park predicate** = bus-count-only. `_maybe_transition_to_waiting_children` reads `bus.count_pending_for_target(instance_id)`; `pending <= 0` returns False; otherwise an atomic `transition_status_if(RUNNING → WAITING_CHILDREN)`. The count itself is `WHERE target_instance_id = :t AND state = 'PENDING'` — it never looks at the watched work. A second park site (root-lane completion defer) uses the same count via `count_pending_for_target_sync`. | `daemon/services/message_processing_pipeline.py:1279`, `:1329`, `:1330`, `:1335-1340`; `daemon/services/child_reports.py:3511`; `daemon/repositories/dependency_bus/repository.py:471-515` (`:506-515`) |
| **S2** | **Boot sweep: source-task-status-based, 30s age grace, no waiting-children grace, boot-only, cancel-only (never re-homes/wakes).** A restart **would** cancel both target rows (both source tasks terminal, both rows far older than 30s) and **would not** release the parent (extras survive; §10/§11.2). The periodic companion is **permanently inert** on the live build (wiring order). | `daemon/services/dependency_bus.py:2501`, `:2653-2666`, `:1753`; `daemon/api.py:5371`, `:1919-1920` vs `:5710`/`:2972`; `daemon/services/orphan_watcher_sweep.py:82`, `:200-280`; live log ticks 228–232 |
| **S3** | **Watcher target state = `CANCELLED`** (the only terminal non-`FIRED` state; enum `PENDING/FIRED/CANCELLED`, no `RETIRED`). Proof it is **inert**: every fire primitive matches `state='PENDING'` only — `fetch_pending_for_source` (`repository.py:149-174`, `:173`), `fetch_pending_for_target_and_child` (`:239`, child_id filter `:388-396`), `fetch_pending_for_child_instance` (`:343-395`), and the sweep's own `state = 'PENDING'` predicate. Proof it **cannot re-arm** once `enqueued_at` is stamped: the resurrection candidate predicate requires `enqueued_at IS NULL` (`repository.py:797-843`) and is driven only from the resume hook (`instance_lifecycle.py:4125-4260`). Proof it **releases the park count**: it leaves the `state='PENDING'` filter at `repository.py:513`. Reads of `watcher_metadata` (the JSONB column): **none** — no read site inspects it (`grep -rn watcher_metadata` → model/repository docstrings only), so a marker string there would be write-only and is deliberately not used. |
| **S4** | **Commission target state = `abandoned`** (terminal NON-success). `CommissionState = outstanding → satisfied | abandoned | superseded`; every transition is a guarded `UPDATE … WHERE state='outstanding'` that also stamps `resolved_at`, and `satisfied_at` is written **only** on `satisfied` — so `abandoned` cannot masquerade as delivery. Reason tokens are a **frozen vocabulary** (`worker_terminal_no_report` / `ttl_expired_not_live` / `worker_row_missing`); anything else raises in the service. It does not "move the problem": every reader keys on `state='outstanding'` (routing, census, quiet, L3 hold), so `abandoned` is invisible to all of them. | `daemon/repositories/mission_record/models.py:153-173`, `:380-500`; `daemon/repositories/mission_record/repository.py:724-771`; `daemon/constants.py:715`, `:721-726`; `daemon/services/mission_record_service.py:1046-1063`; readers `repository.py:159/192/214/240/396/443`, `mission_record_reads.py:239-289`, `child_reports.py:2691-2695` |
| **S4b** | **Which reason per row.** `0cdc0b31` → `worker_terminal_no_report` — identical to what RDRS lane 7 writes for a terminal worker with no delivery evidence. `90493206` → `ttl_expired_not_live` — the only source-sanctioned abandon for a **non-terminal** worker; **declared divergence:** the repair acts *before* the 6h TTL (created 20:30:46 ⇒ TTL ≈ 02:30Z). End state identical, timing differs. Alternative (zero guesswork): drop block 4 and let lane 7 abandon it at TTL. | `report_delivery_recovery.py:2929-2937` (worker_terminal), `:2941-2947` (TTL), `:2841-2859` (watcher HOLD); `daemon/config.py:2401-2421`; `daemon/constants.py:715` |
| **S5** | **A gated repair execution path EXISTS** — `ens_db_repair_execute` with 3 gates + same-transaction `repair_log`. **No tool executes a request file**; execution is one dry-run+confirm per statement, by a `maintenancer` instance (the only agent allow-listing `ens-db`). | `daemon/tools/ens_db_tools.py:939-1180`, `:137-190`, `:401-445`, `:447-500`, `:498+`; `daemon/repositories/ens_db/models.py:45`; `agents/maintenancer/meta.json` |
| **S6** | **Cancelling the watchers does not deliver the lost sonic-leader report.** Every delivery lane requires a durable obligation row or an inbound message: `report_injections` is the admission substrate for lanes 1/3/4/5 (and the parent has **0** such rows), Lane 2's no-row backstop admits *completed children of a non-terminal parent* (the parent has **0** children), and the agent-node drain only delivers rows that exist. `emit_terminal`/`fire_for_terminated_target` move watcher **state**; they enqueue nothing (the bus is a state machine — `child_reports.py:412-417`). ⇒ **CANCEL ≠ FIRE.** | `daemon/services/report_delivery_recovery.py:1088-1160` (window→lane map), `:1201-1272` (lanes 1/5 predicates), `:2138-2200` (Lane 2 admission); `daemon/services/child_reports.py:4140-4160`, `:4651-4800` (obligation mint on actual completion only); measured live: `report_injections` for the parent = 0, child instances = 0, pending/ready messages for report traffic = 0 |

---

## Appendix B — Measurement log (what was observed, when)

| When (UTC) | Observation |
|---|---|
| 23:05–23:20 | Maintainer forensics independently re-verified: 2 target watchers PENDING (source tasks 39320 / 39424, both `completed`), both commissions `outstanding`, watched worker `542b49a6` `completed` @19:04:27, parent `waiting_children` (`updated_at` 23:16:17). |
| 23:16 | Parent `waiting_children`, `version=97`. |
| 23:30:03 | A `system:watchdog:wedge` message (`74840635…`) + `task` `39632` (`process_message`) are enqueued ⇒ parent flips to **`running`**, `version=98`. |
| 23:30 → 23:47 | Sampled 10×18s: parent stays **`running`**, task `39632` stays **`pending`/unclaimed** for 17+ minutes. `--inspect` re-run ⇒ A1 and A9 FAIL (`status=running`). **Not a script fault** — the assertion refuses a world it does not describe; re-run after the re-park. |
| 23:41–23:50 | **Population drift discovered:** `count_pending_for_target(parent)` = **4** (was 2): notices `5602e91d`, `c83e12a2` on source task `39564` (`completed` @23:03:54) → child `82551f59`. `count_outstanding_commissions(parent)` = **4** (was 2): `06001f88`, `a1561f19` on worker `82551f59`. ⇒ A8 FAILS; `--emit-request` refuses by design and names these rows. |
| 23:49 | Guard-predicate re-measure (read-only `SELECT count()`): each of the four emitted `WHERE` guards selects **exactly 1** row. Stamp expression re-measured: `to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')` → `2026-11-17T23:49:38.696912+00:00`, byte-identical in form to live `fired_at` values (`2026-08-24T18:46:01.692260+00:00`). |
| 23:49 | Inner `UPDATE`s validated with `EXPLAIN (COSTS OFF)` — plans cleanly, index-scanned, single-row (`dependency_watchers`: `ix_dependency_watchers_target_state` + `watch_id` filter; `mission_commissions`: pkey + state/commissioner filter). No statement was executed. |
| 23:50 | Offline self-test (`--fixture`, fake PKs) generated the four-block request into `repair/selftest/` — proves the guard/stamp/fingerprint/receipt mechanics end-to-end without a live row. |
| 23:54 | `--emit-request` **without** `--gate` ⇒ refused, nothing written, exit 4. **With** the exact gate ⇒ refused on A1/A8/A9 with per-row detail naming the four extras, nothing written, exit 4. `emitted/` remains EMPTY. |
| 23:56 | Two `--inspect` runs 51s apart: **byte-identical** output ⇒ read-only stability demonstrated. |
| 23:57 | **`--verify` exercised end-to-end** against two harness receipts built from the *current* live inventory (stored under `selftest/`, clearly labelled, not repair requests): (a) matching receipt ⇒ checks 3/4/5 PASS, checks 1/2 FAIL (nothing has been repaired — correct), exit 5; (b) receipt with **one** parent-scoped row synthetically moved (`5602e91d` `PENDING→CANCELLED`) ⇒ check 4 FAILs with exactly `changed=['5602e91d-…']`, proving the row-diff logic. No row was touched: the mutation lived only in the receipt file. |
| 23:57 | `--verify` with no receipt present ⇒ `FAIL: no emitted receipt found …`, exit 5. |
| 23:58–23:59 | Two final `--inspect` runs 47s apart, captured to `observations/inspect_clean_run{1,2}_*.txt`: **byte-identical** (`diff` empty) ⇒ read-only stability. Both show `count=4`/`count=4` and `classification=unexpected`. |
| 23:58 | Digest-design check: two `inventory()` snapshots 70s apart ⇒ parent-scoped digest **stable**, global digest also stable in that quiet window (the global digest DID differ across the 23:41 vs 23:52 snapshots: `fd8351d0…` → `389f7715…`), which is why `--verify` anchors the hard check on the parent-scoped digest and reports the global one as informational. |
| 00:16 | `--inspect` re-run: parent `waiting_children` again (A1/A9 PASS), `count=4`/`count=4`, `classification=unexpected` (A8 expected `count=2`) — the world still does not match the 4-row commission. `review/inspect_rerun_20261118T001608Z.txt`. |
| 00:2x | Independent reviewer re-measured the boot-sweep predicate for **4 of 4** parent watchers (`older_than_grace=t`, `orphan_by_predicate=t`) and proved the parent-scoped ROW diff is stable while the recorded digest moves with `parent_keys.status`. `review/r8_census_*.txt`, `review/digest_probe_*.txt`. |
| ~00:30 | **LEADER RULING: widen to the full 8-row gate set** (`…/live-wedge/17efd2ef/w8`). Original 4-row commission superseded; the ruling's justification is recorded VERBATIM under "SCOPE DRIFT AND THE WIDENED GATE". |
| 00:5x | Artifact widened: constants → 8-row tables; A8 re-specced as a DRIFT GUARD; per-row disposition (`in_scope_stale` / `naturally_resolved` / `unexpected`) before statement build; conditional `enqueued_at` backfill; reason selection mirrors lane 7 (terminal worker → `worker_terminal_no_report`, else TTL). Offline self-tests: full 8-row ⇒ 8 blocks; one commission naturally resolved ⇒ 7 blocks + disclosure; boot-sweep-cancelled watcher ⇒ 7 repair + 1 backfill = 8 blocks; classifier harness 7/7. |

**Reviewer note (independent review, 2026-11-18T00:1x–00:3xZ — read-only; artefacts under `repair/review/`).**

* **Re-inspect (live):** `--inspect` re-run at 00:16:08Z ⇒ `current_database() = ensemble_prod`, the same **4** PENDING watchers and **4** outstanding commissions, `classification = unexpected` (**A8** still `count=4 ≠ 2`), **A1/A9 now PASS** (the parent was observed `waiting_children`; the coder's 23:58Z capture had it `running` — the documented hourly `running ⇄ waiting_children` cycle, recorded as legitimate drift). `review/inspect_rerun_20261118T001608Z.txt`.
* **Boot-sweep predicate re-measured against all four rows:** `older_than_grace = t` AND `orphan_by_predicate = t` for **4 of 4** ⇒ a restart cancels all four, not two (`review/r8_census_*.txt`). §10 and §11.2 were corrected on this point.
* **Restart steady state re-derived from source** (§10, revised): bus gate clears at boot; the mission gate alone holds the parent (4 outstanding commissions); the drain legs and the `enqueued_at` re-arm hole are now stated in §10.
* **Digest stability:** the parent-scoped ROWS are byte-identical across ~20 minutes (row-only digest `09ea774c…` in both snapshots) while the recorded `parent_scoped_sha256` moved `d79ccfce…` → `2cf0274e…` — the delta is carried entirely by `parent_keys.status` (`running → waiting_children`). §6 check 4 re-described as a row diff (`review/digest_probe_*.txt`).
* **`--verify` re-run against the two harness receipts (00:2xZ):** the matching receipt ⇒ checks 1/2 FAIL (nothing repaired — correct), checks 3/4 PASS; the **mutated** receipt ⇒ check 4 FAILs with exactly `changed=['5602e91d-5d4c-4fb6-9e2b-8f351d203f52']` ⇒ the row-diff logic works (`review/verify_rerun_*.txt`). **Correction to the 23:57 capture:** the archived `observations/verify_mutated_20261117T235734Z.txt` does **not** show that FAIL — it shows check 4 PASS. Both harness receipts were (re)written at 23:57:48, i.e. **13 s after** the two captures (23:57:35 / 23:57:37), so the archived "mutated" capture was taken against a not-yet-mutated file. The §11.2/row-diff claim is TRUE and reproducible (proved above), but the archived capture does not evidence it; the reviewer's re-run does.
* **`--emit-request` without `--gate`:** refused, exit 4, `emitted/` still empty (independent re-run).
* **Known minor defect left in place (no script edits per the review mandate):** `selftest/fixture_not_yet.json`'s comment claims "Every identifier below is FAKE (deadbeef/feedface/cafebabe prefixes)"; the target-row selectors in the emitted selftest SQL **are** fake (so the artefact cannot hit a live row), but the fixture also embeds live identifiers (the parent `17efd2ef…`, the extras `5602e91d…`/`c83e12a2…`/`06001f88…`/`a1561f19…`, `542b49a6…`, `82551f59…`, mission `cb904c16…`). The safety conclusion holds (the `WHERE` is anchored on the fake PK ⇒ 0 rows matched); the *wording* of the comment overstates.

**Log-retention caveat:** the live log rotates at ≈10 MB × 6 (≈90 min at DEBUG) — the 18:06–22:33 forensic window is already gone. Do not build any decision on log archaeology; use the DB rows and `--inspect`.
