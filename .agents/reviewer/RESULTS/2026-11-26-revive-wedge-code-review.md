# Code Review — revive-wedge terminalization (Component A core + Component B)

**Reviewer:** reviewer[v2] (direct, read-only — no sub-agents, no lifecycle actions, no revives, no edits/commits)
**Date:** 2026-11-26
**Worktree:** `/home/nea/ensemble-src-wt-revive-wedge` (branch `fix/revive-wedge-terminalization`)
**Base:** `35ec50e3c` · **HEAD reviewed:** `a6745d8e9`
**Canonical checkout `/home/nea/ensemble-src`: NOT read** (all reads were issued with `workdir` = the lane worktree; `grep_files`/`read_file` are workspace-bounded to the canonical checkout and were therefore not used for source reads — `bash` only)
**Commits reviewed (code):** `e2eaf3386` (C1), `67898c26b` (C2a), `3b61f9779` (C2b), `97bcdbf62` + `4bf4c459f` (Component B). Docs commits (`dba725030`, `d7df4b070`, `a6745d8e9`) excluded from the code verdict.
**Component C** (the completion guard's pending-task count) is NOT on the branch — its absence is NOT reported as a finding, per instruction.

## VERDICT: ✅ **APPROVE-WITH-NOTES**

**0 critical · 7 advisory.** No critical finding blocks the tester's gate.

### Measured diff scope (`git diff --numstat 35ec50e3c..HEAD`)

| File | +/− |
|---|---|
| `daemon/services/child_reports.py` | +411 / −62 |
| `daemon/tools/system_log_tools.py` | +86 / −7 |
| `tests/unit/services/test_child_reports.py` | +719 / −1 |
| `tests/postgres/test_child_reports_narrow_guard_pg.py` | +213 / −0 (new) |
| `tests/unit/tools/test_system_log_search_paging_pins.py` | +298 / −0 (new) |
| `tests/unit/services/test_stale_running_sweep_rider_pins.py` | +128 / −0 |
| `tests/unit/services/test_stale_running_sweep_pins.py` | +69 / −0 |
| `tests/unit/test_ready_message_completion_report.py` | +117 / −0 |

Matches the brief's figures exactly. Remaining 11 files in the diff are plan/docs/PACKS/CHANGELOG only.

**Verification basis:** static reading of the landed code against the §7 ledger (character-for-character), plus control-flow reconstruction of the changed function. **pytest was NOT run** (the mutation legs are the tester's; the unit lane is fenced by the house scrubber). Every ruling below names the assertion the tester must observe GREEN. All 8 changed files parse (`ast.parse`, zero-write).

---

## Item-by-item

### 1. The two-arm guard — ✅ PASS

| Ledger row | Landed code | Result |
|---|---|---|
| `P-ARM-A-PY` | `daemon/services/child_reports.py:121-134` — `MessageQueue.source == f"internal_report:{instance_id}:{completed_message_id}"` + `status.in_([READY, PROCESSING, COMPLETED])` | ✅ character-for-character |
| `P-ARM-B-PY` | `child_reports.py:135-142` — `MessageQueue.source.startswith(f"internal_report:{instance_id}:", autoescape=True)` + `status.in_([READY, PROCESSING])` | ✅ character-for-character |
| `P-SUPPRESS` | `child_reports.py:90` / `:113-141` — one builder `_mint_guard_suppress_predicate` returning `or_(and_(...), and_(...))` | ✅ |
| `S-A` = `(ready,processing,completed)` · `S-B` = `(ready,processing)` | ARM A carries COMPLETED; **ARM B does not** | ✅ |

- **ONE SELECT.** `child_reports.py:4943-4949` — `session.exec(sqlmodel_select(MessageQueue).where(_mint_guard_suppress_predicate(...))).first()`. One round-trip, one scan; no short-circuit second query. The twin (`:1629-1634`) is the same shape.
- **The `completed` exclusion is exactly where the cure needs it.** `completed` sits in ARM A only. A DELIVERED prior therefore misses ARM B; only an exact same-anchor equality can hit ARM A ⇒ truth-table row (iii) mints, row (i) suppresses. ARM A's `status.in_` list and ARM B's list are disjoint on `completed` — verified in the source, and independently pinned by the compiled-SQL assertions in `test_ready_message_completion_report.py` (`"status IN ('ready', 'processing', 'completed')"` **and** `"status IN ('ready', 'processing')"`).
- **`sqlmodel_select` vs the plan's `select`** (R1's F-01) — re-verified, not re-litigated: the choice is **correct and load-bearing**. `session.exec(...).first()` must yield the ORM row, because the discriminator reads `existing_report.status` (`:4900-4904`) and the blocking row's `source`/`message_id` (`:4905-4909`). A bare SQLAlchemy `select` yields a one-column `Row` wrapper and `.status` would raise. The justification is recorded in-code at `:4934-4938`. **Both** guards use `sqlmodel_select`, so the two are consistent.
- `autoescape=True` is carried → the `LIKE ... ESCAPE '/'` clause is asserted by the twin pin (`assert "ESCAPE '/'" in sql`). ✅

### 2. The decoupling — ✅ PASS

- **`P-EARLY-RETURN`**: `child_reports.py:4988` — `if not should_send and not suppress_mint:` (exact ledger string). Every other `should_send=False` path (`no_completed_message_id`, `pending_messages_exist`, `instance_not_found`, `no_parent_id`) still early-returns unchanged; only the mint-guard case falls through.
- **The fall-through reaches the guarded CAS.** Control flow reconstructed end-to-end: guard (`:4884-4975`) → early return (`:4988`) → tool-invocation lane (`:4999-5067`) → active-children + pending-task guards (`:5110-5165`, defer at `:5164`) → **guarded CAS** (`:5176-5210`) → **suppress branch** (`:5229-5272`). The CAS sits *after* the defer guards and *before* the suppress branch, as plan §3 step 3 requires. `P-CAS` = `UPDATE instances SET status='completed' WHERE instance_id=:id AND status NOT IN ('paused','completed','error')` — unchanged. ✅
- **The suppress branch does the full sequence** (`:5238-5272`): `session.commit()` → `event_bus:instance_completed` → `event_bus:child_completed` → the ONE suppression record → `return _ChildCompletionDbResult(outcome="idempotency_skip", instance_id=…, agent_id=…, parent_id=instance.parent_id)`. Exactly the four fields plan §3 step 4 prescribes ("no other field is set on this path"), and it does **not** fall through to the final return (which reads mint-produced locals `report_message_id`, `completed_parent_id`, `completed_parent_parent_id`, `parent_agent_id` — none exist on this path). ✅
- **The CAS remains the single writer of `instances.status`.** The diff adds no new status writer; the only two `status=InstanceStatus.COMPLETED.value` writes in the function are pre-existing (`:5036` tool-invocation lane, `:5202` the guarded CAS). ✅
- **The B4 gate still fires.** `_dispatch_post_commit_side_effects` keys on `outcome == "idempotency_skip"` and re-reads the row: `child_reports.py:6912-6993` — `inst.status == InstanceStatus.COMPLETED.value` → `_emit_terminal_for_child_instance_via_bus(parent_instance_id=parent_id, child_instance_id=instance_id, status="completed", …)`. The suppress branch's return carries `parent_id`, which the gate reads. On the suppress path the row is COMPLETED *by the time the gate re-reads it* ⇒ the chicken-and-egg is broken. ✅
- **No path leaves a suppressed mint non-terminal** (the whole defect):
  - tool-invocation lane → carries **its own** CAS (`:5036`) ⇒ the child IS terminal;
  - defer guards → the child stays RUNNING — **explicitly sanctioned** (plan §2 rule (ii); the defer is the ordinary path for a child with live children / a queued task, and the plan carves it out);
  - CAS `rowcount == 0` → another writer finalized the row ⇒ terminal.
  No leak. ✅

### 3. The suppression observability — ✅ PASS

| Rule | Evidence | Result |
|---|---|---|
| (i) exactly ONE record per ACTUAL suppression, in the SUPPRESS branch | `:5250-5262` — the sole `_emit_mint_suppression_record(...)` call site; the guard (`:4949-4975`) logs **nothing** (comment at `:4955-4961` matches the code) | ✅ |
| (ii) the DEFER path emits NOTHING and never touches the counter | defer returns at `:5164`, before the suppress branch; the counter is reachable only via `_bump_arm_b_suppress_loss_total` ← `_emit_mint_suppression_record` ← the suppress branch | ✅ |
| (iii) the record carries classification + suppressed anchor + blocking row's source/message_id | `:5252-5258` — `classification=_suppress_class`, `blocking_source`, `blocking_message_id`, `blocking_status`; incoming anchor = `completed_message_id` | ✅ |
| (iv) the counter increments ONLY on an R-K2 loss | `:216-218` — `loss = 0 if matched_anchor == incoming_anchor else 1`; the bump is inside `if loss == 1` (`:219-221`) | ✅ |
| (v) anchor parsed by PREFIX-STRIP, never feeding `suppress_mint` | `:143-156` — `(source or "")[len(prefix):]`, `prefix = f"internal_report:{instance_id}:"`. Call sites of `_report_anchor_from_source` are **only** `:215` (log field) and `:279` (log field). The classification is derived from the row's **status** (`:4900-4904`), never from the anchor | ✅ |
| counter lock-guarded | `:181` `threading.Lock()`; `:186-190` the increment **and** the returned read are inside the lock; `:221-223` the `loss=0` read is inside the lock too | ✅ |

- Format strings are character-for-character the ledger's: `P-ARM-B-SUPPRESS-LOG` (`:159-163`), `P-ARM-A-SUPPRESS-LOG` (`:164-168`), `P-RETRYING-INFO-LOG` (`:169-174`). ✅
- `P-ANCHOR-PARSE` is honoured: prefix-strip, never `.split(":")` — a colon-bearing anchor survives intact. ✅
- **The `retrying` exclusion is made observable** (leader ruling 6) and the probe is mint-path-only: `_emit_retrying_obligation_info` is invoked at `:6113-6115`, immediately after the mint's `session.commit()` (`:6097`) — i.e. never on a suppression, never on a defer. It reuses the **caller's** session (deliberately not a second `Session` — the SQLite/StaticPool rollback hazard, documented at `:236-250`) and swallows every failure. Verified the probe's read transaction cannot strand a DB write: `_event_registry_record` is `event_registry.emit.record` (`daemon/services/event_registry/emit.py:96`) — an in-memory ring + sink, no session. ✅
- The probe is the **one** sanctioned second query on this path (plan §2). ✅

### 4. The twin — ✅ PASS

- **Identical by construction**: both the live guard (`:4944`) and the dead twin (`:1630`) call the **same** builder `_mint_guard_suppress_predicate` (`:113-141`). No duplicated predicate text exists in either function.
- **The "kept verbatim" docstring is corrected** (`:1512-1523`) — it now states the twin is *not* kept verbatim, that both edits (`58fc594eb`, this lane) were lockstep, and points at the shared builder. The stale "It is kept verbatim (no deletion, no edits to the logic)" text is gone. ✅
- **The lockstep pin compares BOTH guards** — `tests/unit/test_ready_message_completion_report.py::TestMintGuardTwinLockstep::test_both_twins_evaluate_the_one_shared_two_arm_predicate`:
  - `inspect.getsource` of the twin **and** of the live function **and** of the builder;
  - asserts the builder symbol is present in **both** guard sources (a local/inlined predicate in *either* twin turns the pin RED — it is not a single-guard check);
  - asserts the builder's ARM-A equality fragment, the ARM-B prefix fragment, and — via the **compiled SQLite SQL** — both status sets and the `ESCAPE '/'` clause.
  - Behavioural half: a REAL in-memory session (not a MagicMock `exec`) proves a DELIVERED prior under a different anchor no longer blocks the twin while a READY prior still does. ✅

### 5. ⭐ THE HARNESS FIX (C2a) — ✅ PASS — **does NOT neuter any pin**

**The patch:** `tests/unit/services/test_child_reports.py:143` — `b._repo.count_pending_for_target = lambda target_instance_id: 0`, in the autouse `bus` fixture.

**Ruling: every bus-count-dependent assertion in this file still discriminates; the stub is VALUE-EQUIVALENT for every test in the file. No pin is weakened. No fix is required.**

Evidence (blast radius measured, not assumed):

1. **The patched attribute is reached.** `DependencyBus.count_pending_for_target_sync` → `return self._repo.count_pending_for_target(target_instance_id)` (`daemon/services/dependency_bus.py:1417`) — an **instance-attribute lookup at call time**, so the lambda wins. The stub is live.
2. **The stub's blast radius inside the completion chain is ONE decision.** `grep '_bus_count_pending_for_target_sync('` returns only its own definition (`child_reports.py:557`) — **it has no call sites** (dead helper). The single live consumer of the patched count is `child_reports.py:1765` → `is_parent_complete = bus.count_pending_for_target_sync(parent.instance_id) == 0`, inside `_update_parent_on_child_complete` (the mint path's parent cascade).
3. **The ROOT lane is NOT affected.** It uses the *fulfillable* count — `bus.count_fulfillable_pending_for_target_sync(...)` (`:4158`, `:4230`) → `self._repo.fetch_pending_for_target(...)` (a **rows** query, `dependency_bus.py:1521-1525`), plus a direct session query at `:4356`. Neither goes through `count_pending_for_target`.
4. **The B4 gate is NOT affected.** `_emit_terminal_for_child_instance_via_bus` (`child_reports.py:870`) calls `bus.emit_terminal_for_child_instance(...)` — a state-machine transition, not the count.
5. **The pre-existing watcher-seeded tests are NOT affected.** The three tests that seed a PENDING `DependencyWatcher` (`:378`, `:2063`, `:2335`) all drive a **root** instance and assert a **defer** — which happens at the root lane (point 3), before the mint, so `is_parent_complete` is never consulted. Their expectations are unchanged.
6. **The new pins seed no watchers**, so the unstubbed query would return **0** as well. The stub returns exactly what the real query returns in every new pin ⇒ value-equivalent ⇒ **no assertion is made vacuous**.

**Per-pin discrimination under the stubbed bus count:**

| Pin | Bus-count dependence | Still discriminating? |
|---|---|---|
| NEW-2 (decoupling, 4 params) | none (guard → CAS → suppress branch; the bus is not on that path) | ✅ yes |
| NEW-3 (B4 gate) | the gate's *reachability* depends on `outcome` + the re-read row status; the helper itself uses `emit_terminal_for_child_instance` (point 4) | ✅ yes — `len(gate_calls) == 1` asserts the method was **called**; the stub cannot pre-empt it |
| NEW-6a / NEW-6b (duplicate freedom) | the mint path reaches `_update_parent_on_child_complete` → `is_parent_complete`; stubbed 0 == real 0 (no watchers) | ✅ yes |
| NEW-7 (a)–(d) (observability) | none | ✅ yes |
| NEW-7 (e) (defer case) | the defer guards read the `instances` / `task` tables, not the bus | ✅ yes |
| NEW-1 / NEW-5 (rider pins) | different file, different harness (`rider_harness`), stub not in scope | ✅ yes |

**The C2a diagnosis is correct and confirmed** (not re-litigated): `DependencyWatcherRepository.count_pending_for_target` opens its **own** `Session(self.engine)` (`daemon/repositories/dependency_bus/repository.py:513`) and closes it; under `StaticPool` (one shared connection) that close rolls back the caller's just-committed transaction, deleting the minted report row before the test can observe it. Production (Postgres) gives every session its own connection, so the defect never occurs there. The patch removes the second session instead of papering over the symptom. ✅

### 6. The pins' quality — ✅ PASS (advisories A4, A5 below)

| Pin | Discriminates? | Why |
|---|---|---|
| **NEW-2** (`test_a_mint_suppressed_call_still_terminalizes_the_child`, 4 params: rows i / ii / iv-ready / iv-processing) | ✅ | Asserts no second row + `outcome == "idempotency_skip"` + `parent_id` + **child COMPLETED**. At base the lifetime-prefix guard early-returns *before* the CAS ⇒ the child stays `running` ⇒ RED. Removing the suppress branch ⇒ the mint proceeds ⇒ 2 rows ⇒ RED. |
| **NEW-3** (`test_the_suppress_outcome_reaches_the_b4_gate_on_a_completed_row`) | ✅ | Monkeypatches `_emit_terminal_for_child_instance_via_bus` and asserts **exactly one** call, with `status_at_gate == COMPLETED`. At base the gate is never reached (the row is still running) ⇒ 0 calls ⇒ RED. |
| **NEW-6a** (`test_a_second_invocation_without_a_revive_mints_no_second_row`) | ✅ | The COMPLETED-prior mark is **load-bearing** as the plan requires: with a READY prior, ARM B would suppress and make the L2d mutation vacuous — the implementation marks the prior **COMPLETED (delivered)** before invocation 2 (`:419` of the diff). Under L2d (terminal-entry short-circuit **and** CAS-rowcount-0 both neutralized) the second invocation reaches the mint ⇒ 2 rows ⇒ RED. |
| **NEW-6b** (`test_an_explicit_revive_then_a_new_anchor_mints_a_second_report`) | ✅ | RED at base (the lifetime-prefix guard blocks the new-anchor mint — the wedge), GREEN at merge (row iii). The revive is explicit (`status → RUNNING`). |
| **NEW-7 (a)** ARM-B record | ✅ | Exactly-one assertion; field-level (`child`, `incoming_anchor`, `outstanding_anchor`, `outstanding_message_id`, `loss`, `total`); level-checked (`WARNING`); counter delta asserted **exactly** (+1 on loss, +0 otherwise); `total` cross-checked against the counter; asserts **no** ARM-A record on an ARM-B-classified suppression. Catches: dropped emit, dropped `+= 1`, wrong `loss` polarity, wrong anchor parse. |
| **NEW-7 (b)** ARM-A-only | ✅ | Exactly one ARM-A record with `matched_status == completed`; **zero** ARM-B records; counter delta 0. |
| **NEW-7 (c)** retrying INFO | ✅ | Exactly one INFO record with the **oldest** row's anchor + `retrying_count == 2`; plus the negative half: no record without an obligation, and **no** record on a suppressed mint. |
| **NEW-7 (d)** the lock | ✅ | 8 threads × 400 increments must equal 3200 (**catches a bare `+= 1`**), **and** a `_CountingLock` wrapper asserts the lock was entered exactly 3200 times (**catches "drop the lock"**). Both mutations are caught. |
| **NEW-7 (e)** the defer case | ✅ | Parametrized over **both** defer guards (`active_children`, `pending_task`) — the plan asked for "either defer guard"; both are covered. Asserts zero ARM-B/ARM-A/retrying records, counter delta 0, and the child's status stays `running`. Captured with `caplog` at INFO+. |
| **NIT-1 companion** (`test_prior_pending_report_under_a_different_anchor_blocks_a_second`) | ✅ | Unchanged by this diff and still GREEN under the two-arm design (a READY prior under a different anchor hits ARM B ⇒ 1 row). It drives the **live** chain (`_process_child_completion_and_notify_parent` under `_patch_drive_seams()`), not a mock ⇒ it is a valid **L2b** mutation target: reverting ARM B ⇒ the pending prior no longer blocks ⇒ 2 rows ⇒ RED. |
| **NEW-1** (row iii, rider file) | ✅ | RED at base (the lifetime-prefix guard blocks the new-anchor mint). GREEN under L2c by design (both arms miss in row iii) — consistent with the plan's assignment of NEW-5 as the L2c target. Asserts 2 rows, the new row's anchor, and the child's COMPLETED. |
| **NEW-5** (ARM A load-bearing, L2c target) | ✅ | A same-anchor **DELIVERED** prior ⇒ ARM A hits ⇒ 1 row + terminalization. Under L2c (collapse to ARM B alone) the COMPLETED prior is not in S-B ⇒ ARM A gone ⇒ a second row ⇒ **RED**. RED at base too (no terminalization at base). |
| **Drive-outcome** (`TestReviveWedgeDriveOutcome`) | ✅ | `healed == 1`, `drive_no_effect == 0`, `errors == 0`, the row COMPLETED, the typed reason `finalize_gap_steady_state` (NIT-6), and the **unchanged** `P-DRIVE-LOG` shape (`transitioned=completed`, `reason=finalize_gap_steady_state`). The negative half keeps the age gate (`class=young` ⇒ `skipped_young >= 1`, `healed == 0`, the row still `running`) — the drive-outcome change does not collapse the age gate. |
| **Component B pins** | ✅ | `test_default_path_output_is_byte_identical_to_pre_change` compares the **whole output string** against a **hardcoded literal** (`FROZEN_DEFAULT_LITERAL`, `test_system_log_search_paging_pins.py:19-22`) — not a value recomputed from the current code ⇒ a genuine byte-identity pin. Plus window/bound/EOF/unsearched-region/match-cap/absolute-line-number/context+redaction pins, the signature-position pin, and the registration pins. |

### 7. The real-PG pin (NEW-8) — ✅ PASS with advisory A3/A4

- **The hard assert is present and correct**: `tests/postgres/test_child_reports_narrow_guard_pg.py` — `SELECT current_database()` → `assert _dbname == "ensemble_test"`, executed before the test's own DDL. ✅
- **postgres-marked**: `pytestmark = pytest.mark.postgres` ✅ (matches the plan's marker, not `-m integration`).
- **Serial**: the gate-matrix's L4 command runs with no `-n` and `-p no:randomly`; the claim "the postgres lane is skipped under xdist" is **TRUE** — `tests/postgres/conftest.py:264-279` adds a `skip` marker to every postgres test when `PYTEST_XDIST_WORKER` is set. ✅
- **The pin exercises the real predicate on the PG dialect** (the `LIKE ... ESCAPE` clause and the `status IN (...)` evaluation), asserts the new-anchor report **IS** minted (2 sources, the new one carrying the new anchor), and asserts the child terminalizes. RED at base (the lifetime-prefix guard blocks the delivered-report + new-anchor mint) ⇒ GREEN at merge. ✅
- **The skip→failure rule**: enforced at the **leg** level — `gate-matrix.md:284-285` ("a SKIP is a leg FAILURE, not a green"; `-rs`, 0 skipped, node id present) and the PACKS row (`§8.2`: "**0 skipped expected** — a skip = PG unreachable = leg FAILURE, not a pass"). The pin file **cannot** detect its own skip: `pg_engine` skips the module before the body runs (`tests/postgres/conftest.py:370-372`) ⇒ pytest RC = 0 ⇒ **a skip is a false green if the leg is judged by RC alone**. See **A3**.

### 8. Component B — ✅ PASS

- **Additive parameter, last in the signature**: `daemon/tools/system_log_tools.py:420` — `start_line: int | None = None` appended **after** `limit`. Pinned by `test_start_line_is_appended_last_in_the_signature`. ✅
- **Byte-identical default path**: every new statement is gated on `start_line is not None` — the two window guards (`:539-548`), the `retained` counter (`:549-550`), the beyond-EOF check (`:565-570`), the two header/no-match branches (`:672-690`, `:707-723`) — and the validation block returns early only for a non-None value (`:489-497`). The default path's output is pinned against a hardcoded literal (see item 6). ✅
- **The second (upper-bound) retention guard cannot alter the default path**: `if start_line is not None and retained >= MAX_LINES_SCAN: continue` — the whole conjunct is False when `start_line is None`. It is **justified by the plan's own window**: the deque's `maxlen` eviction would otherwise keep the **tail** of the requested window; the guard keeps the **first** `MAX_LINES_SCAN` lines, i.e. the forward window `[start_line, start_line + MAX_LINES_SCAN - 1]` the docstring and `P-B3`/`P-B6` specify. ✅
- **Docstring + registration**: the tool docstring (`:435-447`), the module docstring (`:14-16`) and `CATEGORY_DOC` (`:47-50`) are all updated; the tool stays in the `system-log` category; the registration pins assert the tool name, the category and the privileged category set are unchanged. ✅
- `P-B1`…`P-B8` match `observability.md §9:870-877` character-for-character (cross-checked both tables). ✅

### 9. FENCES — ✅ PASS

| Fence | Result |
|---|---|
| `git diff --stat 35ec50e3c..HEAD -- stale_running_sweep.py mission_strand_healer.py waiting_children_watchdog.py mission_live_guard.py instance_lifecycle.py` | **EMPTY** ✅ (executed; no output) |
| No config defaults / knobs / feature flags touched | ✅ the name-status shows only the two source files + 6 test files + docs; no settings/config/env knob in the diff |
| No change to report-delivery semantics beyond the narrow | ✅ `daemon/repositories/report_injection/` and the delivery ledger are untouched; RDRS stays per-child (plan §3) |
| The drive path's anchor unchanged | ✅ `stale_running_sweep.py` untouched — the release only **reads** the stored anchor back, observability-only (`P-DRIVE-ANCHOR`) |
| No new migration or index | ✅ `git diff --name-only 35ec50e3c..HEAD -- daemon/migrations/` is empty; `message_queue.source` remains unindexed (as the plan states) |

### 10. Unsanctioned behaviour — ✅ nothing critical; see advisories

- The `retrying` probe's **second prefix scan per mint** — sanctioned (`P-RETRYING-INFO-LOG`: "the ONE sanctioned second query on this path"). ✅
- `_report_anchor_from_source` is the release's **first** anchor-parsing consumer — sanctioned and observability-only (plan §2). ✅
- The probe's reuse of the caller's already-committed session (rather than a second `Session`) — a deliberate, documented deviation forced by the SQLite/StaticPool hazard; correct in production too (no second connection per mint). ✅

---

## CRITICAL — none.

No finding in this review blocks the tester's gate.

## ADVISORY

| # | Finding | Evidence | Fix |
|---|---|---|---|
| **A1** | **The tool-invocation lane intercepts the suppress fall-through — no suppression record, no counter increment.** The guard sets `suppress_mint=True`, then the fall-through enters the tool-invocation branch, which returns `outcome="tool_invocation_completed"` **before** the suppress branch. The ARM-B/ARM-A record never fires and the R-K2 counter never moves on that lane. Plan §3 step 3 sanctions *traversing* the branch, but §2 rule (i) ("exactly ONE record per ACTUAL suppression") has a hole here. (The child **is** terminalized on that lane — its own CAS — so this is an observability hole, not the wedge.) | `child_reports.py:4999-5067` (tool-invocation branch, returns at `:5060-5066`) vs the suppress branch at `:5238-5272` | Either call `_emit_mint_suppression_record(...)` in the tool-invocation branch when `suppress_mint` is set (before its own return), **or** add a §7 row carving the lane out of rule (i). The second option is cheaper and keeps the branch untouched. |
| **A2** | **The CAS `rowcount == 0` lane returns `idempotency_skip` without the suppression record** ⇒ the R-K2 counter can **under-count** when the terminal CAS loses its race. Plan §3 step 5 sanctions the return and §2 defines the emission point as "after the fall-through CAS has succeeded and committed" — so this is plan-shaped, not a defect. Flagged for the record because the counter is presented as the R-K2 loss metric. | `child_reports.py:5211-5224` | Document the under-count in the `P-ARM-B-SUPPRESS-COUNTER` ledger row (observability-only, "never a control signal" already covers the over-count edge). |
| **A3** | **The PG leg's "a SKIP must be a FAILURE" rule is enforced only by reading the leg output, not mechanically.** `pg_engine` skips the module when PG is unreachable ⇒ pytest RC = 0 ⇒ a skipped pin is a **false green** if the leg is judged by exit code alone. The pin file cannot detect its own skip (the skip precedes the test body). | `tests/postgres/conftest.py:370-372`; the requirement lives in `gate-matrix.md:284-285` and the PACKS row (§8.2) | The leg's pass condition must include the skip count, not just RC. Concrete: the PACKS row's command should capture the `-rs` summary and fail on the string `skipped` (shell `;` separators — **never** a `#` comment inside a `\` continuation, per the standing convention note), or the pin module should shadow `pg_engine` to **raise** rather than skip. |
| **A4** | **The PG pin's `current_database()` assert runs inside the test body — i.e. AFTER the session-scoped `pg_engine` fixture already ran `SQLModel.metadata.drop_all(engine)` + `create_all(engine)` on the target.** The protection against a live DB therefore comes from the conftest's fail-closed `pg_scratch_db_fence` (`FORBIDDEN_DB_NAMES` / `ALLOWED_SCRATCH_DBS`), **not** from the pin. `gate-matrix.md:282` words this as "the NEW pin carries the same assertion in its own session fixture, so the fence travels with the file" — that overstates the pin's role. | `tests/postgres/test_child_reports_narrow_guard_pg.py` (assert inside the test) vs `tests/postgres/conftest.py:344-354, 390-391` | Correct the gate-matrix wording (the conftest fence is the authority; the pin's assert is a redundant confirmation), or move the assert into a fixture that runs before the engine. No code change needed for safety. |
| **A5** | **`_seed_report` in the PG pin is dead code** (never called — the test mints its own report through the service). | `tests/postgres/test_child_reports_narrow_guard_pg.py` | Delete it, or use it in a second pin that seeds a prior report directly. |
| **A6** | **`isinstance(start_line, int)` accepts `True`** (bool ⊂ int) ⇒ `start_line=True` is accepted as 1. `P-B8` is satisfied literally, but a bool is not a meaningful line number. | `daemon/tools/system_log_tools.py:489` | `type(start_line) is not int or isinstance(start_line, bool)` → reject bools explicitly, if `P-B8` is meant strictly. |
| **A7** | **The harness stub is a CONSTANT 0** (`lambda target_instance_id: 0`). Value-equivalent today (item 5), but it would silently neuter any **future** non-root test that needs a non-zero pending count. | `tests/unit/services/test_child_reports.py:143` | Keep as-is today; when such a test lands, replace the constant with a counting stub over the test's own session (a `Session(engine)` inside the stub would re-introduce the StaticPool rollback hazard — count from the caller's session instead). |

---

## What must be true before the tester's gate

Nothing from this review blocks the gate. The tester should confirm, per leg:

1. **L1/L2** — the new pin classes go GREEN at merge: `TestReviveWedgeDecoupling` (NEW-2 ×4, NEW-3), `TestReviveWedgeDuplicateFreedom` (NEW-6a/b), `TestReviveWedgeObservability` (NEW-7 a–e), `TestReviveWedgeNarrow` (NEW-1, NEW-5), `TestReviveWedgeDriveOutcome` (2), `TestMintGuardTwinLockstep` (2 + 2 params), `TestSystemLogSearchStartLinePaging` / `TestSystemLogSearchRegistrationUnchanged` (17).
2. **The three predecessor pins that failed at R1** (the StaticPool rollback artifact) now go GREEN at merge — the harness fix is in place and, per item 5, is value-equivalent for every test in the file.
3. **L4** — NEW-8 goes GREEN on real PG with **0 skipped** and the node id present in the result set. **Do not accept the leg on exit code alone** (A3).
4. **Mutation legs** — L2b (revert ARM B) must RED the NIT-1 companion; L2c (collapse to ARM B) must RED NEW-5; L2d (neutralize the terminal-entry short-circuit **and** the CAS-rowcount-0 return) must RED NEW-6a; the observability mutations (drop the ARM-B emit / drop the `+= 1` / drop the lock / drop the ARM-A emit / drop the retrying probe / emit at the guard / emit or increment on the defer path) must each RED NEW-7.
5. **Fence** — re-run the empty-diff fence proof and the `daemon/migrations/` emptiness at the merge base.

---

## ADDENDUM (2026-11-26 — the documentation lane) — Component C LANDED after this review; the verdict above is UNCHANGED

**THE REVIEWED HEAD WAS `a6745d8e9`.** Since this review was issued, **Component C landed on the same lane branch**: `fed66f78d` (the Component C correction + the D32 own-queue fold-in) and `e66d25062` (the C5/A1 tool-invocation-lane fix). **THIS REVIEW DID NOT COVER COMPONENT C** — the doc's own scope line says so ("Component C … is NOT on the branch — its absence is NOT reported as a finding, per instruction"), and that statement remains true OF THE REVIEWED TREE. **Nothing in the verdict, the item-by-item findings, the CRITICAL section or the ADVISORY table above is edited, re-scoped or re-judged by this addendum.**

**ONE ADVISORY IS NOW ADDRESSED BY THE CODE (the code did it, not this addendum):** **A1** — "the tool-invocation lane intercepts the suppress fall-through — no suppression record, no counter increment" — is fixed by `e66d25062` (`_emit_tool_lane_suppression_record` at BOTH tool-lane exits, under `if suppress_mint:`; the lane's outcome value unchanged). It is pinned by `tests/unit/services/test_parent_completion_guard_pins.py::TestParentCompletionGuard::test_c5_tool_invocation_lane_suppression_is_logged_and_counted` (the C5/A1 pin — it goes RED if EITHER the emit OR the counter increment is removed). **A2 … A7 REMAIN AS WRITTEN** — they are NOT addressed by that commit and are NOT re-judged here.

**THE COMPONENT C DOCUMENTATION SURFACES LANDED WITH THE CODE:** the plan set (`.agents/shared/planning/revive-wedge-terminalization/` — `plan.md §15` + the new `§15.8`, `decisions.md §9` + the new `§9.6` (the implementation deviation notes D-A..D-G), `gate-matrix.md §11` + the filled `§11.5` PACKS block, `release-staging.md §7` (j)), the tester's PACKS.md block (`revive_wedge_parent_completion_guard_unit_test`), and the CHANGELOG's `[Unreleased]` Component C entry.

**THE MEASURED CENSUS (documentation lane, 2026-11-26):** the pin file collects **11 nodes** (PC-1 … PC-10 + the C5/A1 pin) and **11 passed** at HEAD `e66d25062`; the TRUE-BASE leg (the base tree `35ec50e3c` + ONLY that pin file, identical command) is **3 failed / 8 passed**, the failures being the ASSERTED ones (PC-1, PC-2, PC-3(a)) with **no collection error**. **Component C has NO postgres node** — its dialect portability is proven by PC-10's no-DB compile pin (PostgreSQL `->>` / SQLite `JSON_EXTRACT`).

**DOC-ONLY.** This addendum exists so the record states WHICH tree was reviewed and what landed afterwards. The reviewer's authorship, verdict, findings and advisory table are preserved verbatim.
