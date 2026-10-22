# nest-ops — Architecture Recommendation (adversarial review)

**Commission:** nest-ops · `instance_ops` privileged tool category for the maintenancer
**Reviewed:** the four-document set at `.agents/shared/planning/nest-ops/` (plan-overview / decisions / implementation-plan / risk-register)
**Base verified against:** `origin/latest` @ `8e6df58e4` (the working tree HEAD, clean)
**Method:** every load-bearing claim in the doc set was re-derived from the tree. Where the tree contradicts the doc set, the tree wins and the contradiction is stated. Options are scored on five axes (Complexity, Scalability, Maintainability, Risk, Cost) with Low/Med/High; **Cost = implementation + ceremony + review effort**, not infra.

---

## 0. Ground-truth corrections that change the analysis

Five facts verified in the tree materially alter one or more of the six verdicts. They are stated up front because they recur.

**GT-1 — The maintenancer does NOT hold the `instance` category.** `agents/maintenancer/meta.json` allow is exactly `["system-log","ens-db","knowledge","system_upgrade","system_restart","db"]`. The `instance` category tools (`list_instances`, `get_instance_info`, `subtree_status`, `subtree_messages`, `send_message`, `terminate_instance`, `pause_instance`, `resume_instance`) are registered under `"instance"` (`daemon/tools/instance.py:5493-5800`, `@register_tool_category("instance")` + `scan_tools_for_full_docs` → `_tool_metadata[...]["category"]` → `resolve_tool_filter` expansion at `:488-495`). So the commission brief's premise — "the maintenancer ALREADY holds the `instance` category" — is **false**. The redundancy question is real but it is daemon-level redundancy (two surfaces offering overlapping reads), not per-agent duplication.

**GT-2 — The LEADER holds the entire `instance` category, including the UNGOVERNED `terminate_instance`.** `agents/leader/meta.json` allow includes `"instance"`. The tool at `daemon/tools/instance.py:5493-5503` is nine lines: fuzzy-resolve, `manager.terminate_instance(id)`, return `{"terminated": result}`. No dry-run, no reason, no audit, no protected-target gate, no kill-switch. `agents/watcher/meta.json` has `"allow": []` → empty-allow universe → every non-privileged tool, including the ungoverned terminate. **Fleet-wide terminate already exists in this daemon, ungated, and is reachable by at least the leader and every empty-allow agent.** `instance_ops` does not create the blast radius; it adds one *governed* path while leaving the ungoverned ones untouched.

**GT-3 — The repository ALREADY has a SQL-side `status` filter.** `SQLModelInstanceRepository.list(status=...)` (`daemon/repositories/instance/repository.py:1008-1014`, applied in SQL at `:1135-1137`) accepts a status filter on the flat path. The plan's §2.11/L-2 claim ("`GET /instances` has no status filter ⇒ needs either a new repository query or in-Python filtering") is true of the **route** but false of the **repository**: a status filter already exists one layer down and threads up with one additive parameter on `_lifecycle_service.list_instances` / `manager.list_instances`. The plan's L-2 ("applied in Python over a fetched page") is therefore unnecessary for `status` and actively harmful for correctness (see Q1).

**GT-4 — The wedge sweeps expose NO reusable read-only predicates.** Verified across all six claimed sources: `job_recovery_service._is_instance_alive` / `_fail_orphaned_job` (private; the one public method, `reconcile_drift_states` at `:925`, is a *reconciler*, i.e. mutating); `stale_pause_sweep._owner_is_alive` / `_has_live_descendant` (private module functions); `waiting_children_watchdog` exposes counters and `wedge_episodes()` (the already-notified set, not a live detector) plus destructive `run_once()`; `orphan_watcher_sweep` delegates to `bus._sweep_orphan_watchers` — a **private, destructive** UPDATE on the dependency bus; `report_delivery_ledger` exposes exactly one public function (`parent_history_has_internal_report`); `report_delivery_recovery`'s four lanes are all private and all *recover*, not detect. The plan's §2.10 "Composes? YES" column is **not supported by the tree**. There is nothing to compose.

**GT-5 — `_schedule_explicit_handle_resume` takes a pre-computed suspension handle; the selection semantics live in the boot pass, not in the primitive.** `daemon/manager.py:12433-12444` — the signature requires `target_work_id`, `handle_work_id`, `selected_suspension_reason`, `route_outcome`. These are outputs of the boot pass's *selector* (`daemon/services/auto_continue_boot_pass.py`). The plan's §2.8/D-8 claim that the primitive has "RUNNING-only and waiting_children-excluded selection semantics baked in" is wrong: the primitive has no selection semantics at all — the boot pass computes them. `instance_reclaim` as a "thin wrapper over `_schedule_explicit_handle_resume(silent=True)`" is **not implementable without re-implementing the boot pass's selector**, which is precisely what D-3 forbids and D-8 claims to defer past.

Two smaller corrections: (a) `list_instances` (the tool) returns `DEFAULT_PAGE_LIMIT = 10` rows (`daemon/constants.py:16`), not 50, and is fleet-wide across all projects (`project_id=None`) — the recon's "routes to `repo.list()`, NOT subtree-scoped" is confirmed, with the sharper fact that the existing tool is a **10-row pinned-first window**. (b) The tool `get_instance_info` returns the repository `Instance.to_dict()` (`daemon/repositories/instance/models.py:128-154`) — which includes the **raw `metadata` blob** and does *not* include the API `InstanceInfo` fields the plan lists (`pending_count`, `pinned`, `mcp_tool_names`, `watchover_*` — those live on `daemon/models/instance.py:36`, the HTTP response model). The plan's §2.11 conflates the API model with the tool's actual return shape.

---

## Q1 — Redundancy vs the existing `instance` category

### Per-tool adjudication (the four reads)

| Tool | Existing sibling | Adjudication | Evidence |
|---|---|---|---|
| `instance_list` | `list_instances` (10-row pinned window, no filters, no count) | **EXTENSION, not restatement** — but the plan's filter mechanism is the weak part | GT-1, GT-3 |
| `instance_get` | `get_instance_info` (repo `to_dict()` + children + raw metadata) | **RESTATEMENT in a different shape + genuine additions (lineage, pause_marker, pending pack) + one genuine improvement (metadata redaction)** | §0 GT-1, §0(b) |
| `instance_health` | none | **GENUINE GAP** — no fleet/wedge surface exists anywhere (verified: no `health|overview|summary|fleet|census` GET route in 42 route modules) | plan §2.11 confirmed |
| `instance_fleet_summary` | none *for the maintenancer* | **GENUINE GAP for the holder, MIS-NAMED surface** — the maintenancer holds no queue/pool/DLQ lens (allow list, GT-1); but the tool's content is ~⅔ *system capacity* (queue_depth, worker_pool, dlq_depth), not instances | `agents/maintenancer/meta.json`; `WorkerPool.get_stats` at `worker_pool.py:1518` exists and is reusable |

### The filter problem (the plan's L-2 is a correctness defect, not a limitation)

L-2 accepts "in-Python filtering of a fetched page." With `limit=50` against a fleet larger than 50, `status=running` filters a *pinned-ordered first page* and silently returns an arbitrary subset. The house rule the plan itself cites for the refusal behaviour — "filters apply in SQL, never as post-page Python filtering" (`daemon/services/mission_resolver.py:157-180`) — is then violated in the same tool. The plan cites the rule to justify a typed refusal for an unknown liveness value and then breaks it for the known ones.

### Options

**A — ship all four reads as specified** (Python-side filters, 10 tools, one category).
**B — ship four reads; make `status` SQL-side via the existing `repo.list(status=...)`; drop the remaining server-side filters (`agent`, `parent_id`, `stale_minutes`, `liveness`) from the tool and let the agent filter the returned rows client-side.**
**C — cut `instance_get`; fold `pause_marker` + `pending_question_pack` into `instance_list` rows; three reads.**

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| A: as specified | Low | **Low** — Python-side filters silently wrong past one page | Med — second near-homophone surface (`instance_list` vs `list_instances`, `instance_get` vs `get_instance_info`) | Med — silent wrong-subset results misdirect acts | Low | **REJECT** — L-2 is a correctness defect, not a disclosed limitation |
| B: SQL status + client-side rest | Low | **High** — no page-bound filter lies; agent triages bounded JSON | High — no new repository machinery; GT-3 makes `status` a one-line param thread | Low | Low | **RECOMMENDED** |
| C: cut `instance_get`, fold markers into list | Med | High | Med — markers must join every census row (`pending_question_pack` is a RAM dict lookup; cheap; `pause_marker` a metadata read; fine) — but `current_task` (task-table join) and `last_messages` (checkpoint read) are per-row hydration you want to pay for on ONE instance, not 200 | Med | Med | Not recommended — `instance_get` earns its place as the *hydration* tool if `instance_list` stops carrying hydration fields |

**Recommended shape (B, refined):** keep all four reads. `instance_list` = census only (identity, status, parent_id, children_count, ages, `pending_count`, `pause_marker`, `pending_question_pack` — all cheap), with `status` applied in SQL via the existing repository parameter (GT-3) and every other filter performed by the agent on the returned JSON. `instance_get` = single-instance hydration (ancestors, descendants, `current_task`, `last_messages`, redacted metadata). Cut `current_task` and `last_messages` from `instance_list`'s row shape so the two tools are complementary rather than nested.

**The rule for "worth a new category"** (the question the brief asks): a read tool earns a new-category slot only if (1) it is not reachable by the holder through an existing category, AND (2) its output cannot be composed from the other tools in the same category at bounded cost. Under that rule: `instance_health` and `instance_fleet_summary` pass on both legs. `instance_list` passes leg (1) — the maintenancer has no census at all today — and leg (2) only in the B shape. `instance_get` passes leg (1) and leg (2) **only as the hydration tool** (census + parent_id per row already computes the tree; `current_task`/`last_messages`/pack lookups do not). `instance_reclaim` fails leg (1)'s spirit on a different axis — see the CUT below.

**Verdict: AMEND.** Keep 4 reads; make `status` SQL-side (GT-3); drop the other server-side filters; strip hydration fields from `instance_list`. Rename `instance_fleet_summary` → `fleet_capacity_summary` (its content is capacity, not instances) — cosmetic but cheap while the category is unbuilt.

---

## Q2 — Category granularity

The plan's implicit cost model — "each new category multiplies the pin ceremony" — is **wrong, and the error matters because it forecloses an option on a false basis**. The 7 pin files assert the exact `PRIVILEGED_TOOL_CATEGORIES` frozenset. A second privileged category adds **one entry to each of the same 7 files** plus a count bump (`len == 12` → 14 at `test_board_registration.py:187` / `test_self_alarm_registration.py:187`) plus one more registration-pin assertion. That is ~8 lines of pin edits and one extra `CATEGORY_MODULES` row + construction extend — **not a second ceremony**. The commission's real one-commit invariant is untouched: the same commit still carries every pin site.

**Options**

**A — one category `instance_ops`, all 10 tools** (the plan's choice).
**B — two categories: `instance_ops_read` (4) + `instance_ops_act` (6)**, both privileged, both single-owner (`maintenancer`), the act category alone carrying the kill-switch obligation.
**C — one category + a per-tool allow mechanism** (allow lists naming individual `instance_*` tool names inside a privileged category).

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| A: one category | **Low** | Med — a future read grant to a second agent drags the act surface with it unless the category is split then; the split-at-that-time cost is the same 8 lines, but it is paid under pressure | Med — read/act risk split lives in prose + a shared in-body gate + the kill-switch, i.e. re-derived by every reviewer | Low | **Lowest** | Defensible; acceptable if "keep it tight" is a hard instruction |
| B: two categories | Med — two gates, two allow entries, two registration pins | **High** — read grant becomes a one-line allow change on a read category that structurally cannot carry acts; the act category stays single-owner forever | High — the risk asymmetry the commission itself states is expressed *in the privilege model*, not in prose | **Low** | Low+ — ~8 pin lines, 1 extra category + pin | **RECOMMENDED** |
| C: per-tool allow | Med | Low | Low | **HIGH** — the plan's own §2.2 documents that a literal tool name in an allow list is added verbatim and **bypasses the category gate**; a per-tool allow mechanism on a privileged category deliberately opens that bypass and collapses the two-layer gate into one | Med | **REJECT** |

**Why B over A.** Three reasons. (1) The cost asymmetry between A and B is ~8 lines of pin edits — the plan's framing of B as a ceremony-multiplication is factually wrong and should not be the deciding input. (2) The commission's own Q2 premise — "read and act have radically different risk profiles" — is a structural property; encoding it structurally is cheaper than encoding it in prose plus one in-body gate that must be re-audited every time the module is touched. (3) The deferred read-tier question (DF-3-adjacent, the leader-tier discussion) becomes a read-category grant rather than a privileged-surface widening. Acknowledged counter-cost: two in-body gates, one more `CATEGORY_MODULES` entry, one more construction extend, one more allow entry in `meta.json`, one more ROLLOUT assert string. The kill-switch `ENSEMBLE_INSTANCE_OPS_ENABLED` stays and gates only the act category — it is instant, `meta.json` is not.

**The leader-tier ruling re-checked:** the plan's D-2 argument for ruling NO ("leader already has `list_instances`/`get_instance_info`/`subtree_status`") is **factually correct** (GT-2) and stands on its own. Option B does not reopen it.

**Verdict: AMEND** — split into `instance_ops_read` + `instance_ops_act` (or keep the plan's single `instance_ops` name for the act half). If the leader's "keep the scope tight" is a hard instruction rather than a preference, A is acceptable and I would not block on it — but then the split cost (~8 lines) must be recorded in `decisions.md` so the future decision is not taken on the false ceremony-multiplication model.

---

## Q3 — The D-3 provenance decision

### (a) Is the fixture-equality pin sufficient?

**No — and as specified it is vacuous.** D-4 states the gate *imports* `_restart_provenance`. AC-10 then requires "the protected-target gate and the restart gate return identical verdicts … on a shared fixture set." If the instance_ops gate calls the imported helper, the pin compares a function to itself and proves nothing except determinism. It only has teeth if a *second implementation* exists — which D-4 explicitly forbids. The plan cannot have both. **Amendment:** the pin must be restated as (i) a *wiring* pin (the instance_ops gate's source references the single canonical helper — no local re-implementation, source-grepped like `test_agent_restart.py:127-133` does for the directional rule) plus (ii) a *vocabulary* pin (the helper's verdict → the gate's refusal-token mapping: `ok=False` → `no-user-provenance`, `pclass`/`depth` recorded on the audit row). That is a meaningful pin; verdict-equality against itself is not.

### (b) Is a cross-lane private import acceptable? Is there a third option?

The house treats a leading underscore as module-private by convention, but this codebase already imports private names across tool modules where the alternative is duplication (e.g. `job_recovery_service` importing `_TERMINAL_INSTANCE_STATUSES`, `mission_resolver` importing `_STATUS_CANONICAL_MAP` from `work_status` and calling it "the single source of truth — no parallel vocabulary set is maintained"). So a private import is not *unprecedented* — but the plan's own D-10 rationale ("two copies on a set that gates drift-cancels is a silent-divergence risk") argues for a **single canonical home with a public name**.

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| A: private import + drift pin (plan) | **Lowest** | Med | **Low** — instance_ops depends on a private symbol; a rename in the live lane breaks the new module at import time (loud, but a cross-lane breakage owned by nobody) | Med — the pin as specified is vacuous (a) | Lowest | Not recommended |
| B: thin public wrapper in `upgrade_tools.py` (`def user_origin_provenance(...)` → delegates to `_restart_provenance`) | Low | Med | Med — public contract consumed; private name retained so every existing pin still passes; **but** `upgrade_tools.py` carries an explicit fence ("do not grow it further without pulling that fence", module docstring L8) | Low | Low | Acceptable |
| C: move the implementation to a neutral home `daemon/tools/provenance.py`; `upgrade_tools._restart_provenance` becomes a 3-line delegating alias with identical signature and verdict shape | Med — one new module, one alias | **High** — the implementation gets one neutral home; the live lane's *name* survives byte-identically for every pin | **High** — the cross-lane dependency becomes a public contract; future consumers (a read tier, other lanes) consume the same home | Med — the live lane's code is touched (behaviour-preserving); mitigated because the T0a pin (`test_agent_restart.py:127-133`) scopes its source assertion to the `system_upgrade` *tool body*, which a module-level move does not enter — **verified** | Med | **RECOMMENDED** |
| D: promote with the upgrade owner signing off, in their lane | High | Med | Med | **High** — cross-lane coordination on a live, byte-verified gate; the plan rejected this correctly | High | Reject for this commission; record as DF-7 (already deferred) |

**Why C.** The plan's stated reason for not moving — "the upgrade lane is live and its pins reference the private name" — is half-true. The pins reference the *name*, and option C preserves the name as an alias. The behaviour-preservation risk is real but bounded (the helper is a pure read over two manager dicts, ~100 lines, no I/O), and it is the only option that removes the private-import coupling permanently rather than papering it with a pin. If the commission's risk appetite is genuinely "do not touch the live lane's file at all", **fall back to B** — a 3-line delegating wrapper is not the growth the fence means, but say so in the commit message.

### (c) Privilege-surface coupling from importing a `system_upgrade`-category module?

**No privilege widening — verified.** Privilege is per-category + per-agent allow-list + per-tool in-body gate; module imports confer nothing. The maintenancer already holds `system_upgrade` (GT-1). Import-time cost is negligible (the module is constants + functions; no install-dir or config reads at import). One genuine coupling to note: `daemon/tools/instance.py` will import the new module at module level, so whatever `instance_ops.py` imports becomes part of *every* agent's import graph. That is true for any placement and is not a differentiator.

**Verdict: AMEND** — prefer C (neutral home + delegating alias in the live lane, pins untouched, T0a verified unaffected); accept B if the no-touch constraint is absolute. Either way, **restate AC-10 as a wiring + vocabulary pin**, not a self-comparison.

---

## Q4 — The audit transaction boundary

This is the highest-stakes question in the set and the plan's answer is **partly unsound**. The plan's §4 specifies a repository method that "opens ONE session, performs `act()` and the `INSERT`, and commits once," citing the ens-db lane. That pattern works for ens-db because **the ens-db act IS a SQL statement executed in the tool's own session** — the audit joins the same connection trivially. `instance_ops` acts are **daemon service calls that own their own transactions**. The two patterns are not transferable.

**Verified transaction structures:**

| Act | Underlying primitive | Transaction shape | Same-transaction audit achievable today? |
|---|---|---|---|
| `instance_terminate` | `InstanceLifecycleService.terminate_instance` → `_terminate_instance_db_sync(engine, write_guard, …)` via `asyncio.to_thread` (`instance_lifecycle.py:2827`) | **Own `WriteGuardSession`, committed inside the helper**, on a worker thread; post-commit outbox (SSE, cleanup, lock release) fires after | **NO** |
| `instance_mark` | `repo.set_metadata` (`repository.py:2518`) — dialect-aware single-statement UPDATE | Own session, commits | **Cheaply YES** — with an additive optional `session=` parameter |
| `instance_nudge` | `manager.enqueue_message` / `set_injection` | RAM injection or its own message-queue/task session | NO without touching a large path |
| `instance_answer` | `answer_questions_via_instance` (`routers/answer_helper.py:704`) | Pack is **in-memory**; durable shadow is a metadata write; resume is a scheduler | NO — there is no single transaction to join |
| `instance_dismiss` | inline router body (`routers/instances.py:1239`) | Same shape as answer | NO |
| `instance_reclaim` | `_schedule_explicit_handle_resume` | A scheduler, not a transaction | N/A (see CUT) |

The tool-layer "one session around `act()`" is impossible for terminate for a structural reason on top of the ownership one: the service's DB half runs on a **worker thread** via `asyncio.to_thread`; a SQLAlchemy Session cannot be shared across that boundary. Options (a) thread a session into the service and (c) move the boundary into the tool are both dead ends for terminate. That leaves (b) a hook inside the service, or (d) a weaker, honestly-stated property.

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| (a) service signature takes a session | High | Med | Med | **HIGH** — sessions are not thread-crossable; the DB half runs in `asyncio.to_thread` | High | REJECT — not implementable |
| (b) optional `audit_sink: Callable[[Session], None] \| None = None` invoked inside `_terminate_instance_db_sync`'s existing `WriteGuardSession`, before commit; default `None` ⇒ byte-identical for every existing caller | **Low** — ~10 lines in the service + one call site | High | High — the governance core is real, not simulated | Med — touches the H10-hardened terminate path; bounded by the additive-default-None shape and by a dedicated fault-injection test | Low-Med | **RECOMMENDED for `instance_terminate` and `instance_mark`** (mark via an optional `session=` on `repo.set_metadata`, same principle) |
| (c) move the transaction boundary into the tool | High | Low | Low | HIGH | High | REJECT — the service owns the cascade snapshot, the post-commit outbox ordering, and the crash-safety contract (H10); the tool cannot wrap it |
| (d) weaker guarantee: audit-after with a compensating row | Low | Med | Med | Med — the act is already durable when the audit fails; the "cannot be audited ⇒ must not happen" property is gone | Low | **RECOMMENDED for the non-couplable acts, in WRITE-AHEAD form** (below) |

**The recommended shape is a two-tier property, stated per tool — not a uniform claim.**

- **Tier 1 — true same-transaction** (act and audit commit together or neither): `instance_terminate` via the `audit_sink` hook; `instance_mark` via an optional `session=` parameter on `repo.set_metadata`. Both changes are additive with default-off behaviour.
- **Tier 2 — write-ahead audit** for acts that have no single transaction to join (`instance_nudge`, `instance_answer`, `instance_dismiss`): the tool **INSERTs the audit row first** in its own transaction with `outcome="pending"`, performs the act, then UPDATEs the row to `"committed"` / `"error"`. A crash mid-act leaves a durable `pending` row — the ledger always contains the intent, and a sweeper (or `instance_health`) can reconcile. This is strictly stronger than audit-after, it is the pattern the plan's own §4.6 already half-invents for reclaim ("the audit row is written first and the act is skipped on audit failure — fail-closed"), and it costs one extra UPDATE.

The plan's own R11 rule — "if a future act cannot be transaction-coupled, it must be **excluded from the category**, not silently de-coupled" — is the honest tiebreaker, and it applies to **three of the six acts shipping now**, not to a hypothetical future act. Either apply the rule (cut nudge/answer/dismiss) or generalize the write-ahead tier and state it in the module docstring and the table's DDL comment. I recommend the latter; cutting three tools to preserve a uniform property trades real capability for a phrasing win.

**Verdict: AMEND** — replace the uniform same-transaction claim with the two-tier table above; add the `audit_sink` hook for terminate and the optional `session=` for `set_metadata`; adopt write-ahead for the non-couplable acts. AC-9 (fault-injection rolls back the act) applies to Tier-1 tools only; a second AC covers "a crash mid-act leaves a `pending` row" for Tier-2.

---

## Q5 — Blast radius and containment

### Framing correction first

GT-2: ungoverned fleet-wide terminate already exists and is reachable (leader; every empty-allow agent). The commission adds a *governed* path and does not remove the ungoverned one. That does not weaken the case for containment on the new tool — it strengthens the case for the commission's *read* surfaces (detecting wedges the ungoverned path creates) and it means R9's framing ("terminating a live chat root") describes a risk that is already live in this daemon, not one this commission introduces. The long-term right answer is to push the governance down into the service or to bring the `instance` category into the privileged set — both are behaviour changes for existing callers and correctly out of scope; both should be recorded as deferred.

### (a) Is a terminate budget warranted? Yes — and it is nearly free.

The restart lane carries four rails (provenance, cooldown, min-uptime, sliding budget — `upgrade_tools.py:2888-2934`); `instance_ops` carries provenance only. Given that terminate's blast radius is strictly larger than restart's, the asymmetry is unjustified.

**Cheapest form: a DB-side count over the new `instance_ops_audit` table** — `count(action='instance_terminate' AND outcome='committed' AND created_at > now()-24h) >= cap ⇒ refuse `terminate-budget-exceeded``. The table exists by construction (D-2); the query is one SELECT; no new subsystem, no new file, no journal dependency. **Do not** reuse the upgrade lane's file-backed counter (`restart_budget_count` reads `releases/state.json` — an upgrade-lane artifact; coupling an instance lane to the release journal is the wrong direction and would break under a rollback-sweep). Recommended caps, mirroring the restart lane's shape: **N=3 committed terminates / 24h fleet-wide**, plus a **min-age rail on the target** (refuse to terminate an instance younger than, say, 600s — the restart lane's min-uptime rail, transposed; it kills the "terminate the thing that just started" footgun and is one timestamp comparison). Protected-target terminates should count against a tighter budget (N=1/24h is defensible).

### (b) How weak is R2, and the cheap strengthening

R2 (the gate proves a user spoke, not that they spoke about this target) is **weak in a specific, quantifiable way**: the window is per-caller-instance with a 3600s TTL and carries `root_source` + `root_message_id` — neither of which constrains the target. Within the window's TTL, *any* target satisfies the gate. So one genuine user message to the leader arms up to an hour of protected-target terminates against arbitrary targets.

**Cheap strengthening — replace `user_confirmed: bool` with `confirmed_target_id: str`.** The gate requires `confirmed_target_id == target_id` verbatim. This is not a semantic binding (the agent can still assert falsely), but it converts an unfalsifiable boolean into an attributable, audited assertion: the agent must *name the target it believes the user consented to*, the refusal path teaches the correct shape, and a post-hoc reviewer comparing `confirmed_target_id`, `reason`, and the user's `root_message_id` can detect a false claim. Cost: one parameter rename and one equality check. Pair it with recording `root_message_id`, `root_source`, `pclass`, `depth` on the audit row (the plan already commits to the last three — keep all four).

### (c) R1 — does the promote-wake forge materially weaken this gate, and is there a cheap guard?

**It materially weakens it, and there IS a cheap guard the plan missed.**

The forge, verified: `upgrade_journal_sweep.py:1209` and `:1734` deliver promote-kind wakes with `source=(head.source or "api")`; `classify_user_origin("api")` → `True, "exact:api"` (`upgrade_journal.py:3391-3392`); `stamp_user_origin_window` mints a **DIRECT** window on the woken instance. If the woken instance is the maintenancer (it holds `system_upgrade` and can be the arming instance), the gate's strongest verdict class — `pclass="direct"`, depth 0 — is minted with **no genuine user ancestor**. This is not theoretical; it is the recorded R1 exposure arriving through the exact lane this gate reads.

**The cheap guard: require `pclass == "inherited"` for protected-target acts.** The reasoning is structural, and it is verified end-to-end:

- The genuine authorization path for a specialist is always delegated. `spawn_instance` enqueues the child's first turn with `source=f"internal_agent:{caller_instance_id}"` (`daemon/tools/instance.py:964-969`); `_inherit_provenance_window` mints **only** on that prefix (`daemon/manager.py:4914-5000`), inheriting from the caller's live window with monotonic decay and a depth cap of 4. So user → leader (direct window) → maintenancer produces `pclass="inherited"`, `depth=1`.
- The forge produces `pclass="direct"` on the woken instance.
- Therefore: **refusing `pclass="direct"` for protected-target acts blocks the R1 forge completely while preserving the genuine delegated path.** One line in the gate; one test.

Disclosed cost: one legitimate case is refused — an operator POSTing a user message *directly* to the maintenancer's instance id via the API. That is an unusual shape for a fleet-destructive act, the refusal message can name the workaround ("route via the leader"), and the stricter default is the correct direction of failure for this blast radius. Record it as an accepted limitation alongside L-8.

If the commission prefers not to tighten, the fallback mitigation is visibility only: stamp the audit row `provenance_suspect=true` when `pclass=="direct" and root_source=="api"` on a protected target, and surface a suspect-act counter in the fleet summary. That does not block the forge; it makes it countable.

| Containment option set | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| Plan as-is (gate + dry-run + reason + audit + kill-switch) | Low | High | High | **Med-High** — no rate cap on the largest-blast act; R1 forge reaches the strongest verdict class; R2 unfalsifiable | Lowest | Baseline — REJECT as final |
| + DB-side budget + target min-age rail | Low | High | High | Low | Low | **RECOMMENDED (both rails)** |
| + `confirmed_target_id` | **Lowest** | High | High | Low | Lowest | **RECOMMENDED** |
| + `pclass=="inherited"`-only for protected targets | Low | High | High | **Lowest** — closes R1 for this lane without touching the upgrade lane | Low | **RECOMMENDED** |

**Verdict: AMEND** — add the budget + min-age rails, replace `user_confirmed` with `confirmed_target_id`, and require inherited provenance for protected-target acts. None of the three touches the upgrade lane; none adds a subsystem.

---

## Q6 — The read layer's wedge detectors

### (a) Composition-by-calling-service-predicates is not the right shape, because it is not possible

GT-4: there are no public read-only predicates to call. The plan's D-10 ("composes; does not re-implement") and its §2.10 "Composes? YES" column describe a mechanism the tree does not offer. The real choice is between reaching into private methods across module boundaries (a coupling as fragile as copying, plus it couples to *destructive* classes), and writing the predicates in `instance_ops` (the "second source of truth" D-10 exists to prevent).

### (b) Is the second source of truth acceptable for the four new instance-level detectors?

**Yes — if and only if it is pinned, not merely disclaimed.** The reconciliation mechanism the plan lacks:

- **Extract nothing.** Do not refactor six live sweep modules to expose predicates — that is a far larger behaviour-surface change than this commission should make, and it risks the sweeps' own carefully-tuned epistemology (fail-open vs fail-closed choices per sweep).
- **Write the detectors in the new module as *reporting* predicates** — cheap, read-only, and explicitly labelled as a projection.
- **Add a disagreement pin, not a verdict-equality pin.** For each detector whose sweep counterpart is observable, a test constructs a fixture the sweep itself classifies (via its own public entry point where one exists — e.g. `OrphanWatcherSweepService.counters()`, `WaitingChildrenWatchdog.wedge_episodes()` — or via the sweep's documented classification) and asserts the health report flags the same set. This is a real pin: two independent code paths must agree on a labelled corpus. Where no observable counterpart exists, the detector's row in the doc must say so and the pin is skipped — an honest gap, not a pretend one.
- Keep the R4 lineage-MAX rule (`get_max_last_activity_in_instances`, `repository.py:670`) as a hard, tested constraint — it is the one place where the new detector's semantics are *already* settled by the tree and must not be re-derived.

### (c) The three-vocabulary problem

The plan is right that `instances.status` (10 values) and the mission canonical map (6 liveness values, `_STATUS_CANONICAL_MAP` in `daemon/services/work_status.py:67`, documented as "the single source of truth — no parallel vocabulary set is maintained") are two different domains, and right that the canonical map collapses `idle|queued|waiting|waiting_children → "processing"`. Its answer — a third vocabulary `alive|stalled|terminal` — is wrong, for a reason the plan's own design exposes:

**`alive|stalled|terminal` conflates the liveness axis with the time axis, and in doing so it erases exactly the park-state distinction the commission exists to preserve.** Under the plan's derivation, `alive` = alive-status AND not stale; `stalled` = alive-status AND stale. A deliberately parked instance (`paused`, with the `instance_mark(marker="park")` tombstone — the commission's own D-11 primitive) has a frozen `last_activity_at`, so past the staleness threshold it reports `stalled`, byte-identical to a genuinely wedged one. The `park` marker exists precisely to make that distinction; the derived vocabulary deletes it.

**Most defensible design — three orthogonal dimensions, zero new vocabularies:**

1. **Filter on the raw `status`** (the instance's own 10-value domain) — SQL-side via the existing `repo.list(status=...)` (GT-3). This is the instance domain's native vocabulary; no translation, no drift, no third enum.
2. **Report canonical `liveness` as a derived field, never a filter** — computed from `_STATUS_CANONICAL_MAP` (the declared single source of truth), which already covers the full `InstanceStatus` domain and already has an instance-domain-aware inversion helper (`mission_resolver._instance_statuses_for_liveness`). Reuse, do not restate.
3. **Report staleness as a separate, explicitly-named field** — `idle_seconds: int | null` plus `stale: bool` against the caller-supplied threshold, using the lineage-MAX aggregate (R4). Never fold it into the liveness enum.
4. **Report `pause_marker` alongside** — so a `paused` row reads as "deliberately parked, 4h idle", not "stalled".

A caller wanting the plan's `alive`/`stalled` can compute it client-side from `status` + `stale` in one line. The typed `unknown-liveness-value` refusal survives unchanged for anyone passing a non-canonical liveness value.

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| Plan: third vocabulary `alive\|stalled\|terminal` | Low | Med | **Low** — three vocabularies guaranteed to drift; the enum hides the park/wedge distinction it was built to expose | **Med** — a deliberately parked fleet reads as wedged past the threshold | Low | REJECT |
| Reuse canonical mission liveness as the filter | Med | Med | Med — right single source of truth, wrong domain semantics for a maintenance surface (`waiting_children` collapsing to `processing` hides the park state) | Med | Med | Reject as a *filter*; keep as a *field* |
| **Orthogonal: raw `status` filter + canonical `liveness` field + `stale`/`idle_seconds` field + `pause_marker`** | Low | **High** | **High** — zero new vocabularies; every axis maps to an existing single source of truth | **Low** | Low | **RECOMMENDED** |

**Verdict: REJECT the third vocabulary; AMEND to the orthogonal-shape design.** Composition (a/b): AMEND to "write reporting predicates + pin agreement with the sweeps' observable outputs where they exist"; do not extract, do not import privates.

---

## Missed items (the "anything that would bite" sweep)

**(i) Wrong tree assumptions.** Four, all load-bearing: GT-1 (the commission brief's premise about the maintenancer holding `instance` is false — the plan itself never states this clearly and the brief's framing propagates a false redundancy premise), GT-3 (a SQL-side `status` filter already exists one layer down), GT-4 (no composable predicates exist), GT-5 (`_schedule_explicit_handle_resume` has no selection semantics; the boot pass owns them). Additionally §2.11's description of `get_instance_info`'s return shape conflates the HTTP `InstanceInfo` model with the tool's actual repo `to_dict()` output (which includes the raw `metadata` blob and lacks `pending_count`/`pinned`/`watchover_*`) — the `instance_get` contract should be written against the tool's real shape.

**(ii) The DV-2 dismiss extraction is materially riskier than "additive extraction" implies.** The dismiss body at `routers/instances.py:1239-1460` is ~200 lines that reach into `request.app.state.live_hub` for the F3-ordered SSE emit (which must fire **before** the resume cascade), raise `HTTPException` with `ErrorResponse` envelopes at four sites, and run a `finally` block that releases the recorded side-effect set. Moving it into a service requires injecting the live hub and converting four HTTP exceptions into typed service errors the router re-maps — that is a real refactor of a live route, not a delegation edit. The route-table readback pin is a good precaution but guards the wrong risk (the decorator-rebinding trap fires when a helper is inserted *between* decorator and handler; a body-move does not do that). Two cheaper alternatives: **(α)** for `instance_answer`, no extraction is needed at all — `answer_questions_via_instance(manager, instance_id, answers, question_pack_id, live_hub, …)` already takes `live_hub` as a plain parameter (`answer_helper.py:704-712`, docstring: "or `None` in tests"); the tool calls it with `live_hub=None` and maps the raised `HTTPException`s to refusal tokens. Delete S-6's answer-half. **(β)** for `instance_dismiss`, **defer the tool rather than the extraction**: cut `instance_dismiss` from this commission (the category ships with 8 tools), and let the extraction land as its own small change when a dismiss service is actually warranted. If the commission insists on shipping `instance_dismiss` now, then extract — but scope the extraction honestly (live-hub injection + typed error mapping + a test that the SSE emit still precedes the cascade) rather than describing it as behaviour-preserving-by-construction.

**(iii) One-commit ceremony gaps.** Two. First, **the `agents/.prompt-artifacts.sha256` re-stamp is NOT in the §2.5 one-commit list** (items 1-11) even though the prose edits that change the hashes (S-9: `tools_note.md`, `ari/workflow.md`) are — the baseline check is non-blocking at stage (`stage.sh:621-660`, allow-stale by design) and is not asserted against the live repo tree by any test, so a stale baseline is invisible rather than red; put the re-stamp in the same commit anyway, because the *stage journal* will otherwise record a drift warning for the release that introduces the category. Second, **`agents/maintenancer/ROLLOUT.md` carries a second exact-string assert at `:52` (the deny list)** — the plan names `:51` only; the deny list is unchanged by this commission so `:52` stays green, but the ceremony checklist should name both so a future deny edit doesn't rediscover it. The inherited 3-red fix (§2.3) is correctly scoped; the intersection-assert safety claim for `test_maintenancer_end_to_end.py` is verified correct.

---

## The recommended CUT

**Cut `instance_reclaim`.** Three independent reasons, any one sufficient:

1. **It is not implementable as specified.** GT-5: `_schedule_explicit_handle_resume` requires a pre-computed suspension handle (`target_work_id`, `handle_work_id`, `selected_suspension_reason`, `route_outcome`). A tool taking a bare `instance_id` must compute the handle — i.e. re-implement the boot pass's selector (find the suspended turn, classify the suspension reason, compute the route outcome). That is a new primitive by the plan's own D-3 definition, and it is exactly the "second source of truth" D-10 forbids, in the lane where a wrong selection silently resumes the wrong turn.
2. **Its value case is already served automatically.** The primitive's own contract is "resume the interrupted turn of an instance that was RUNNING when the daemon died" — which the boot auto-continue pass already does, at boot, for every qualifying instance. The tool's only added value is re-triggering that recovery without a reboot, for a class the boot pass already covers.
3. **Its audit coupling is the weakest in the set** (Q4 table: a scheduler, no transaction to join), so it contributes disproportionate complexity to the governance contract for the least governable act.

Net effect: the category ships with **8 tools** (3 reads + 5 acts, or 4+4 under the Q1 amendment), the "partially deferred tool with an honest doc surface" ambiguity disappears, and DF-1 absorbs the fully-dead-instance case it was already going to absorb. If a reclaim capability is genuinely needed before a new primitive exists, `instance_nudge` (revive-on-send) already covers the message-carrying approximation, and the plan's own D-8 concedes the silent case is the only distinct value.

**Secondary cut (a field, not a tool):** remove `current_task` and `last_messages` from `instance_list`'s row shape (Q1) — they are per-row hydration and belong to `instance_get`.

---

## Highest-risk finding

**Q4.** The plan's governance core — "the act and its audit row commit together or neither commits" — is **not achievable for `instance_terminate` as designed**, because the terminate service owns its own `WriteGuardSession` on a worker thread (`instance_lifecycle.py:2827`, `_terminate_instance_db_sync`), and the tool-layer "one session around the act" cannot join it (thread boundary + transaction ownership). Shipped as written, the implementation would either (a) silently produce an audit-after pattern on the single most destructive act in the category while the docs and AC-9 claim fail-closed, or (b) fail loudly at integration time and force an unplanned edit to the H10-hardened live terminate path. This is the one item I would resolve **before** any code is written, because it determines whether the service is touched at all (option b: the `audit_sink` hook) and it rewrites AC-9's scope. The second-order risk is Q6: the health report's composition premise is false (GT-4), so `instance_health` — the tool the commission's value proposition rests on — will be built as re-implementation whether or not anyone admits it, and without a disagreement pin it will drift from the sweeps it claims to compose.

---

## Consolidated verdict table

| # | Question | Verdict | The amendment |
|---|---|---|---|
| Q1 | Redundancy vs `instance` | **AMEND** | Keep 4 reads; `status` filter goes SQL-side via existing `repo.list(status=…)`; drop the other server-side filters (agent/parent/stale/liveness) — the agent filters client-side; strip `current_task`/`last_messages` from `instance_list`; rename `instance_fleet_summary` → `fleet_capacity_summary`. The plan's L-2 is a correctness defect, not a disclosed limitation. |
| Q2 | Granularity | **AMEND** | Two categories (`instance_ops_read` + `instance_ops_act`). The plan's ceremony-multiplication cost model is wrong (~8 pin lines, not a second ceremony); the read/act risk split should be structural. Option (c) per-tool allow is REJECTED — it deliberately opens the documented literal-name bypass. If "keep it tight" is a hard instruction, one category is acceptable **provided** the true split cost is recorded. |
| Q3 | D-3 provenance | **AMEND** | Prefer a neutral home `daemon/tools/provenance.py` with `_restart_provenance` retained in `upgrade_tools.py` as a 3-line delegating alias (pins untouched; the T0a source-pin is scoped to the tool body — verified). Fallback: a thin public wrapper. Either way **AC-10 must be restated** — as specified it is a vacuous self-comparison; make it a wiring pin + a verdict→token vocabulary pin. |
| Q4 | Audit transaction boundary | **AMEND** | Two-tier property, stated per tool: Tier 1 true same-transaction via an additive `audit_sink` hook in `_terminate_instance_db_sync` (terminate) and an optional `session=` on `repo.set_metadata` (mark); Tier 2 write-ahead audit (`outcome="pending"` → act → UPDATE) for nudge/answer/dismiss. AC-9 covers Tier 1 only; a new AC covers the Tier-2 pending row. The uniform claim as written is unsound for 3 of 6 acts — and the plan's own R11 rule already says what to do about that. |
| Q5 | Blast radius | **AMEND** | Add a DB-side terminate budget over `instance_ops_audit` (N=3 committed/24h, N=1 for protected targets) + a target min-age rail (600s); replace `user_confirmed: bool` with `confirmed_target_id: str` (must equal target); **require `pclass=="inherited"` for protected-target acts** — this closes the R1 promote-wake forge completely without touching the upgrade lane (verified: `spawn_instance` enqueues `internal_agent:<caller>` → inherited mint; the forge mints `direct`). Record the one refused legitimate case (direct HTTP operator message to the maintenancer). |
| Q6 | Wedge detectors | **REJECT** the third vocabulary; **AMEND** the composition | No third `alive\|stalled\|terminal` enum — it erases the park/wedge distinction the commission's own `park` marker exists to make. Use three orthogonal dimensions: raw `status` as the SQL filter; canonical `liveness` (`_STATUS_CANONICAL_MAP`) as a derived *field*; `stale`/`idle_seconds` (lineage-MAX, R4) + `pause_marker` as separate fields. Composition: write reporting predicates in the new module (there is nothing to compose — GT-4) and pin agreement with the sweeps' observable outputs where they exist; do not extract, do not import privates. |
| — | DV-2 dismiss extraction | **AMEND** | `instance_answer` needs no extraction (`live_hub` is already a parameter — verified). Cut or defer `instance_dismiss`; if it ships now, scope the extraction honestly (live-hub injection, 4 typed error mappings, SSE-before-cascade preserved) — the route-table readback pin guards the wrong risk. |
| — | `instance_reclaim` | **REJECT (cut the tool)** | Not implementable as a thin wrapper (GT-5), duplicates an automatic boot-time recovery, and is the least auditable act in the set. Ship 8 tools. |

**Recommended cut:** `instance_reclaim` (tool). Secondary: `current_task`/`last_messages` from `instance_list` (fields).

**Highest-risk finding:** Q4 — the fail-closed same-transaction audit is unachievable for `instance_terminate` without touching the live terminate service; resolve the `audit_sink` decision before any code is written.
