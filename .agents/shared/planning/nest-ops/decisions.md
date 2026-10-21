# nest-ops — Decision Record

**Commission:** nest-ops · `instance_ops` privileged tool category for the maintenancer
**Base:** `origin/latest` @ `8e6df58e445cd56b5e775dacebe0ef53f0add0b4`
**Status:** BINDING leader decisions D-1 … D-7, plus the commission's own derived decisions
D-8 … D-12 and the recorded deviations.

Every decision states: **what**, **why** (with evidence), and **what it forecloses** (the
options it removes — so a later reader can see the road not taken).

---

## D-1 — The category allow-list is `maintenancer` ONLY; Ari is excluded BY DESIGN

**What.** `instance_ops` is granted to exactly one agent: `maintenancer`. Ari does **not**
receive it. This is a *ruling*, not an omission — the exclusion is recorded so it is
auditable.

**Why.** The user ruling (Discord, 2026-10-10 onward): **Ari is the user-facing
orchestrator.** She monitors with her existing read lenses and **delegates surgery** to the
maintenancer. Ari already holds 31 allow entries (review A3 — the original doc said 32; the tree has 31)
(`agents/ari/meta.json` — `job`, `question`, `bash`, `filesystem`, `time`, `self`, `help`,
`image`, `image-gen`, `context`, `shared_meta_kv`, `project`, the four `job_*` readers,
`pause_instance`, `resume_instance`, `system_restart`, `system_upgrade`, `mission`,
`service`, `scheduling`, `view-views`, `cu`, `secrets`, `settings`, `board`, `self-alarm`,
`view-write`, `board-link`). She is already the broadest holder in the fleet; the
privileged *destructive* surface belongs to the specialist.

**Forecloses.** "Ari self-serves the surgery" — the commission will not build an
`instance_ops` variant for Ari, will not give Ari `instance_terminate`, and will not make
Ari's patrol convention able to perform acts. Ari's convention (§ `plan-overview.md` §7.2,
`implementation-plan.md` S-11) is **observe → delegate → record → report**, never act.

---

## D-2 — A NEW `instance_ops_audit` TABLE, copying `repair_log`'s vocabulary — a stated deviation from "reuse existing tables"

**What.** Build a dedicated `instance_ops_audit` SQLModel table with its own
`daemon/migrations/versions/20261021_120000_create_instance_ops_audit.sql` (unique prefix
— verified free at HEAD: no `20261021_*` file exists). Copy `repair_log`'s **column
vocabulary and semantics** where they apply. **Do not reuse `repair_log` itself.**

**Why — the commission's "reuse existing tables" line has no table to reuse.** Verified:
* The `event` table is **not an audit table** — it is an SSE fan-out store, filtered by
  `JobFeedbackObserver` on `event_type != "instance_lifecycle"`. It is not queryable as a
  before/after ledger.
* The ari-tools precedent offers **no audit table at all**: `board_*` has none (its trail is
  `board_notes`, a workflow note trail), `self_alarm` has none, `view_write` / `board_link`
  have none.
* `secrets_audit` (`daemon/repositories/secrets/models.py:310`) is a domain-specific
  resolution log for secrets — right shape, wrong domain.
* `restart_audits` is **file-backed** (a JSON dict inside `releases/state.json`,
  `daemon/tools/upgrade_journal.py:454-460`) — the upgrade lane's audit, not a DB table.

**Why a dedicated table rather than `repair_log`.** `repair_log` lives in **another
privileged category's schema home** (`daemon/repositories/ens_db/models.py`), and **6 of its
15 columns are SQL-repair-specific**: `sql_text`, `sql_class`, `sql_hash`, `target_table`,
`nonce`, `ttl_expires_at`. `sql_class` carries a **documented enum** (`"DDL" | "DML" |
"DRY_RUN"`) that a new value would pollute. Copying the vocabulary gives house consistency
**without cross-category coupling**.

**The most important property to copy is NOT the schema — it is the FAIL-CLOSED WRITE.**
`repair_log`'s docstring states it outright (`daemon/repositories/ens_db/models.py:46-52`):
the audit `INSERT` happens **in the SAME transaction as the repair on the SAME
connection** — an audit-write failure **rolls back the repair**, never commits un-audited
DDL/DML. `instance_ops` copies this: **the act and its audit row commit together or
neither commits. A destructive act that cannot be audited must not happen.** This is the
governance core of the category.

**Discrimination copied.** `repair_log` distinguishes a dry-run row from an applied row via
`dry_run: bool` + `sql_class` + `outcome ∈ {"previewed", "committed", "rolled_back",
"error"}`. `instance_ops_audit` copies `dry_run: bool` and uses
`outcome ∈ {"previewed", "committed", "refused", "error"}` — adding `"refused"` so the ledger
records **attempts**, not just successes.

**Link pattern.** `maintenance_runs.dry_run_run_id`
(`daemon/repositories/maintenance_runs/models.py:132`) — a soft reference from a preview run
to its commit run — is adopted so an operator can pair a preview with the applied row.

**Deviation, stated plainly.** The commission brief said *"reuse existing tables"*. This
decision **deviates from that line specifically**. It does **not** violate *additive-only*:
a new table is additive by construction, and no existing table's schema or semantics
change. The deviation is recorded here rather than buried.

**Forecloses.** Reusing `repair_log` (cross-category coupling + enum pollution). Reusing the
`event` table (it is not an audit ledger). Building a **generic** cross-category audit
framework (out of scope — this commission establishes an audit table **for `instance_ops`
only**).

---

## D-3 — NO new primitives: every act wraps an EXISTING daemon primitive; gaps are DEFERRED

> **⚠️ PARTIALLY SUPERSEDED by D-14** (the "import, don't move" clause — Q3 verdict ACCEPT).
> D-14 **moves** the provenance implementation to a neutral home and leaves a delegating
> alias. **The rest of D-3 stands unchanged** — the no-new-primitives rule, the reuse map,
> and the DEFERRED register all remain binding.

**What.** No new daemon subsystem. Each of the 10 tools wraps an existing primitive. Where a
primitive does not exist, the tool is marked **DEFERRED** — the commission does not design
the missing subsystem.

**Why.** The commission brief's own framing: *"this is EXPOSURE + TIERED POLICY + the
missing READ SURFACES"*. The daemon already has terminate at the API level, the
pause/resume cascade, `stale_pause_sweep`, and the question answer/dismiss routes. The
value is in **surfacing and governing** what exists, not in growing the daemon.

**Verified reuse map.**

| Tool | Existing primitive | Location |
|---|---|---|
| `instance_terminate` | `InstanceLifecycleService.terminate_instance` | `daemon/services/instance_lifecycle.py:2539` |
| `instance_reclaim` | `manager._schedule_explicit_handle_resume(silent=True)` | `daemon/manager.py:12433` |
| `instance_nudge` | the `send_message` tool path | `daemon/tools/instance.py:3727` |
| `instance_mark` (`park`) | `mark_deliberate_pause_override` | `daemon/services/question_pause_set.py:608` |
| `instance_mark` (`protect`/`suspect`) | `repo.set_metadata` / `get_metadata_value` | `daemon/repositories/instance/repository.py:2518` / `:2270` |
| `instance_answer` | `answer_questions_via_instance` | `daemon/routers/answer_helper.py` |
| `instance_dismiss` | **NO SERVICE FUNCTION — inline in the router** | `daemon/routers/instances.py:1239` |
| `instance_list` / `instance_get` | `manager.list_instances` / `get_instance_info` / repo readers | `daemon/manager.py:13512` / `:13572` |
| `instance_health` | six existing sweeps' read-only predicates | § 2.10 of `plan-overview.md` |
| `fleet_capacity_summary` (renamed from `instance_fleet_summary`) | `WorkerPool.get_stats`, job-queue repositories, DLQ service | `daemon/services/worker_pool.py:1518` |

**The one shared-code extraction.** `dismiss_question` has no reusable service function —
its body is inline in the router. The commission adds an **additive extraction** into
`daemon/services/` with the router **delegating** to it (behaviour-preserving; the route's
contract and error codes are byte-identical). This is an extraction, not a new subsystem.

**Forecloses.** A "fleet health engine", a new liveness model, a new marker framework, a
new question-pack store, a new detector. Also forecloses **fixing the inherited
user-origin forge path** (R1) inside this commission.

---

## D-4 — The protected-target gate REUSES `_restart_provenance` (imported, not moved, not reimplemented) + two existing static predicates

> **⚠️ AMENDED by D-14 (the import becomes a neutral-home move), D-17 (`user_confirmed: bool`
> → `confirmed_target_id: str`), and D-18 (`pclass == "inherited"` required for protected
> targets).** The static/dynamic protection predicates and the "no nonce" ruling are
> UNCHANGED.

**What.** The "explicit user confirmation" gate for terminating a protected target is built
from three existing pieces:

1. **Provenance** — `from daemon.tools.upgrade_tools import _restart_provenance`, called as
   `_restart_provenance(manager, caller_instance_id)`. It returns
   `(ok, pclass ∈ {"direct","inherited"}, root_source, root_message_id, depth, stamped_at,
   expires_at, detail)`. **No nonce is minted.** The upgrade lane's 3-factor nonce regime is
   untouched, byte-for-byte.
2. **Static protection** — `_is_user_facing_root(*, parent_id, source_type)`
   (`daemon/tools/instance.py:2561-2592`), fail-closed on **both** legs (a non-empty
   `parent_id` is never root; a missing/unknown `source_type` is never root), **OR**
   `agent_id == "leader"`.
3. **Dynamic protection** — an `instance_ops.protect` marker in `instances.metadata`,
   written by `instance_mark`.

**Why — D-4 resolves a question the brief asked.** The brief asked: *"Verify what exists;
if no reusable read helper exists, state that plainly and pick the cheapest fail-closed
option, flagging the duplication honestly."* **A reusable helper DOES exist**:
`_restart_provenance` at `daemon/tools/upgrade_tools.py:1384-1485` — the shipped, pinned
semantics for "genuine-user turn, direct or inherited", fail-closed on every path. It is
**imported**, so no second authorization mechanism is built and no policy is duplicated.

**Why the helper is NOT moved.** The upgrade/restart lane is **LIVE** (v0.26.2), its gate is
byte-verified, and its pins reference the **private name**. Touching it is a risk this
commission does not take. Importing it costs nothing in privilege terms: the maintenancer
**already holds** the `system_upgrade` category, so **no privilege surface widens**.

**The drift guard is a PIN, not a move.** A test asserts the `instance_ops` protected-target
gate and the restart gate return **identical verdicts** for a shared fixture set (same
inputs → same `ok` / `pclass` / `depth`). The two lanes cannot silently diverge, and a
second implementation cannot grow behind the pin.

**Why the static set is two existing predicates, not a new concept.** The brief said "Do not
invent a protected-instance concept." `_is_user_facing_root` **is** "live user-facing chat
instance" — it already exists, is fail-closed on both legs, and is already used by the
ask-questions-hiding commission. The `agent_id == "leader"` rule is the static role rule. A
`protect` marker can only **add** protection; it can **never** un-protect a statically
protected target (the static and dynamic layers are OR-ed, and only the static layer can
be removed — and it cannot be, by any tool in this category).

**Disclosed facts about the provenance window (stated, not implied).**
* The stores are **IN-MEMORY** on the manager — `_user_origin_windows`
  (`daemon/manager.py:1560`) and `_inherited_provenance_windows`
  (`daemon/manager.py:1586`) — and are **wiped on daemon restart**. A restart between "the
  user spoke" and "the agent acts" makes the gate **REFUSE**. That is fail-closed and
  correct; it is **not** durability.
* The window is **CLEARED on every non-user-origin dispatch** — an agent/internal turn never
  inherits an earlier turn's authorization (`daemon/manager.py:4906`).
* **Direct TTL = 3600 s** (`NONCE_TTL_S`, `daemon/tools/upgrade_journal.py:140`).
* **Inherited TTL = 3600 s with MONOTONIC decay** — `expires_at = min(parent_expiry, now+TTL)`
  (`daemon/manager.py:4989-4999`).
* **Ancestry depth cap = 4** (`_INHERITED_PROVENANCE_MAX_DEPTH`, `daemon/manager.py:222`);
  overflow **REFUSES the mint** rather than deepening (`daemon/manager.py:4985-4988`).
* The inherited lane mints **only** when the source is `internal_agent:<caller>` and the
  caller has a live window (`daemon/manager.py:4941-5000`) — that is how a delegated
  maintenancer turn inherits "a genuine user spoke above me".

**Forecloses.** A second provenance mechanism. A second nonce regime. Moving or renaming
`_restart_provenance`. An `unprotect` marker (which could un-protect a statically protected
target). A generic "protected instance" concept.

---

## D-5 — `instance_terminate` cascade semantics are HONEST: `cascade=True` default, `cascade=False` refuses to orphan

**What.** `cascade` defaults to **True** (it matches the existing service behaviour).
`cascade=False` means "terminate the target, leave its descendants running" — which
**creates exactly the wedge class this commission's own health report must detect**
(descendants whose parent is now dead = "waiting-on-a-dead-parent"). Therefore
`cascade=False` on a target that **has live descendants MUST REFUSE** with token
`cascade-false-would-orphan-descendants`, unless an explicit
`allow_orphan_descendants=True` acknowledgement is passed — in which case the **dry-run
preview and the audit row must both NAME the descendants that will be orphaned**.

**Why.** The service cascade is **implicit and total**:
`InstanceLifecycleService.terminate_instance` (`daemon/services/instance_lifecycle.py:2539`)
snapshots `repo.get_cascade_tree_ids(instance_id)` and iterates the snapshot; children always
get `terminal_reason = "aborted"`. **There is no cascade flag and no single-instance-only
path in the service.** Exposing a `cascade` argument that the service does not support
would be a lie in the tool signature. The honest implementation is: implement the
single-instance-only semantics **in the tool layer**, and refuse the mode that
manufactures a known wedge unless the operator explicitly accepts it.

**The dry-run preview must show**: the target; the full cascade tree that **would** be
terminated; the protected-target determination; the confirmation requirement; the orphan
list when `cascade=False`.

**Forecloses.** A silent single-instance terminate. A `cascade` flag that quietly means
"something else". Orphaning descendants without an explicit, named acknowledgement.

---

## D-6 — A self-surgery refusal on every act tool

**What.** `instance_terminate` (and the other act tools) refuse to act on the **calling
instance**. Token: `self-surgery-refused`.

**Why.** An agent that kills its own turn is self-harm. The project has a live precedent:
`_refuse_self_surgery` (`daemon/tools/ens_db_tools.py:437-476`) refused an instance-row
repair in a real production incident — the house record is the kb-writer repair, where the
self-surgery guard refused the instance row.

**Shape difference, stated.** The ens-db guard is a **SQL-text heuristic** (does the caller's
instance id appear in the SQL? does the SQL target an audit-sensitive table?). Ours is a
**direct identity check** (`target_id == current_instance_id`) — **strictly stronger**, with
no heuristic surface.

**Forecloses.** "The agent may repair its own row if it is careful." Any self-targeted act.

---

## D-7 — A kill-switch: `ENSEMBLE_INSTANCE_OPS_ENABLED` (default ON)

**What.** A destructive category must have a kill-switch. `ENSEMBLE_INSTANCE_OPS_ENABLED`
(default **ON**). When **OFF**, the **ACT** tools return a **byte-identical no-op**. The
**READ** tools **stay available**.

**Why.** The ens-db lane has `ENSEMBLE_REPAIR_ENABLED` (default ON) which returns a
byte-identical no-op when disabled (`daemon/tools/ens_db_tools.py:134`, `:137`,
`:968-975`). A destructive category must have the same. Resolution order mirrors
`_resolve_repair_enabled`: env → legacy aliases (if any) → default ON.

**Why the read tools stay available — stated, not assumed.** Read is not destructive, and
the read surfaces are the category's principal value: an operator who disables the act
tools still needs to *diagnose* what is wedged. The ens-db lane's kill-switch does exactly
this — it gates **only** the writer (`ens_db_repair_execute`) and leaves the SELECT-only
readers (`ens_db_postgres_select`, `ens_db_inspect`, `ens_db_pool_status`) available. House
precedent, matched exactly.

**Forecloses.** "Kill-switch everything." "Keep the destructive tools live while the
category is disabled."

---

## D-8 — `instance_reclaim` ships as a **partial** tool; the fully-dead case is DEFERRED

> **⚠️ SUPERSEDED by D-20** (the leader overrule of the architect's cut). The tool is
> **redefined** — it no longer wraps `_schedule_explicit_handle_resume` at all. The
> "silent checkpoint-restore stays deferred" conclusion SURVIVES unchanged (DF-1).

**What.** `instance_reclaim` wraps `manager._schedule_explicit_handle_resume(silent=True)`
(`daemon/manager.py:12433`) — the same primitive the boot auto-continue pass uses
(`daemon/services/auto_continue_boot_pass.py`). Its doc surface must state the limitation
honestly: it resumes an interrupted turn; it does **not** rebuild a dead instance's
subtree, and it does **not** resurrect an instance with no resumable turn. **That case is
DEFERRED** — it needs a new primitive, which D-3 forbids.

**Why.** Verified: there is **no** primitive that reclaims a silently dead instance from
its checkpoint *without* delivering a message. The two available approximations are (a) the
silent-resume primitive (private manager method, boot-pass selection semantics: RUNNING-only
at boot, `waiting_children` excluded) and (b) `send_message` to a terminal instance
(revive-on-send), which **does** carry a message and therefore does not match "reclaim
silently".

**Forecloses.** A new "reclaim" primitive in this commission. A claim that `instance_reclaim`
revives anything at all — the doc surface is scoped to the resume case.

---

## D-9 — "Liveness" is a **derived, instance-domain** vocabulary — `alive` | `stalled` | `terminal` — and it is NOT the mission liveness

> **⚠️ SUPERSEDED by D-19** (Q6 verdict: REJECT the third vocabulary). The finding that
> drove D-9 — that there is **no instance-level `liveness` field** and that the mission
> vocabulary is a different domain — is CONFIRMED and stands. The `alive|stalled|terminal`
> **answer** is rejected: it conflated liveness with time and erased the park/wedge
> distinction (see D-19).

**What.** `instance_list(liveness=?)` filters on a **derived** three-value vocabulary:
`alive`, `stalled`, `terminal`. Any other value is a typed refusal
(`unknown-liveness-value`), not a silent empty set.

**Why — verified.** There is **no instance-level `liveness` field**. The `instances` table has
`status` (10-value enum, `daemon/repositories/instance/models.py:20-31`) and
`last_activity_at` (`:72`) — nothing else liveness-shaped. The `liveness` field that exists
belongs to the **mission projection**: `MISSION_LIVENESS_FILTER_VALUES`
(`daemon/services/mission_resolver.py:141-153`) =
`{pending, processing, paused, completed, failed, cancelled}` — the `_STATUS_CANONICAL_MAP`
targets minus `dead_letter` (a `terminal_reason`, never a liveness), with the wire variant
`"completed (gate escalated — unverified)"` (`daemon/tools/missions.py:249-269`).

**The derivation.**
* `terminal` — `status ∈ TERMINAL_INSTANCE_STATUSES` (`completed`, `terminated`, `error`,
  `failed`).
* `alive` — `status ∈ ALIVE_INSTANCE_STATUSES` (`idle`, `running`, `paused`, `queued`,
  `waiting_children` — `daemon/constants.py:720`+) **and** the instance is not stale by the
  lineage-MAX aggregate (R4).
* `stalled` — alive-status **but** stale by the lineage-MAX aggregate.

**Why a refusal instead of an empty set.** The house rule for filters is *filters apply in
SQL, never as post-page Python filtering* (`daemon/services/mission_resolver.py:157-180`
states this for the mission lane). An unrecognised liveness value that silently returns
`[]` is indistinguishable from "the fleet is empty" — a dangerous ambiguity for a tool whose
whole job is triage. A typed refusal is the honest rendering.

**Forecloses.** Reusing the mission liveness vocabulary for instances (they are different
domains). A silent empty result for an unknown filter value.

---

## D-10 — `instance_health` **composes**; it does not re-implement detection

> **⚠️ SUPERSEDED by D-19** (GT-4: the composition premise was FALSE — no composable
> predicates exist). The **goal** (no second unpinned source of truth) SURVIVES; the
> **mechanism** is replaced: reporting predicates + disagreement pins.

**What.** `instance_health` reports the **read-only predicate match** of the six existing
sweep classes (§ 2.10 of `plan-overview.md`). It **never** triggers the underlying repair.

**Why.** All six detector classes already exist as sweeps
(`stale_task_recovery.py`, `waiting_children_watchdog.py` + `mission_strand_healer.py`,
`stale_pause_sweep.py`, `job_recovery_service.py`, `orphan_watcher_sweep.py` +
`auto_continue_boot_pass.py`, `report_delivery_recovery.py` + `report_integrity_guard.py`).
Re-implementing their predicates would create a **second source of truth** for wedge
detection — the classic way a wedge class silently re-opens after a fix. The house precedent
for exactly this is the alive-instance-status hoist documented at
`daemon/constants.py:720-758`: *"Two copies on a set that gates drift-cancels is a
silent-divergence risk… This constant is the single definition."*

**Forecloses.** A new detection engine. Running a destructive sweep as a side effect of a
read tool.

---

## D-11 — The `protect` marker is a metadata write under a namespaced key; `park` reuses the deliberate-pause tombstone

**What.** `instance_mark(marker=…)` writes:
* `park` → the existing **deliberate-pause tombstone** via
  `mark_deliberate_pause_override` (`daemon/services/question_pause_set.py:608`) —
  `instances.metadata["question_paused"] = {"deliberate": True, "cleared_at": "<iso>"}`.
* `protect` / `suspect` → a namespaced metadata key (`instance_ops.protect` /
  `instance_ops.suspect`) via `repo.set_metadata`.

**Why.** The brief's belief was **CONFIRMED by reading**: `instances.metadata` (the JSONB
`metadata` column on the `instances` table — `daemon/repositories/instance/models.py:64-67`)
holds the pause-cause marker, and the **tombstone (not the absence of the marker)** is what
the release path defers on (`daemon/services/question_pause_set.py:608-655`). A "park"
that merely deleted the marker would be indistinguishable from a failed write — and the
release path is deliberately **fail-open** on an absent marker (a failed marker write must
not strand the instance on both paths). Writing the real tombstone preserves that
distinction.

**Forecloses.** A parallel "mark" table (no new table — the metadata column already exists
and the setter/getter primitives are `repo.set_metadata` / `repo.get_metadata_value`).
An `unprotect` marker (see D-4).

---

## D-12 — `instance_nudge` reuses the `send_message` tool path, including its revive semantics

**What.** `instance_nudge(id, message)` wraps the existing `send_message` path
(`daemon/tools/instance.py:3727`): a live target gets `manager.set_injection`; anything else
gets `manager.enqueue_message` (the durable lane). A send to a terminal instance
**revives** it.

**Why.** The brief asked for "inject a message into ANY instance (not just a job root)". The
existing tool already does exactly that, with the whole routing matrix (RUNNING-with-live-
consumer → INJECTION; terminal → REVIVE + ENQUEUE; the busy-guard fallback; the
`load_skill`/`context` enqueue exception) already battle-tested. Re-implementing it would
duplicate a delicate routing matrix.

**Forecloses.** A "nudge" that is a silent `pause`/`resume` (different semantics), and a
"nudge" that bypasses the revive path.

---

## D-13 — TWO categories: `instance_read` (4 reads) + `instance_ops` (6 acts), BOTH maintenancer-only

**What.** Split the surface into two privileged categories. `instance_read` holds
`instance_list`, `instance_get`, `instance_health`, `fleet_capacity_summary`. `instance_ops`
holds `instance_terminate`, `instance_reclaim`, `instance_nudge`, `instance_mark`,
`instance_answer`, `instance_dismiss`. Both are privileged; both name `maintenancer` only.

**Why — the original one-category reading was based on a FALSE cost model.** The implicit
premise was "each new category multiplies the pin ceremony." The architect verified this is
wrong: the 7 pin files assert the exact `PRIVILEGED_TOOL_CATEGORIES` frozenset, so a second
category adds **one entry to each of the same 7 files** plus a count bump
(`len == 12` → **14** at `test_board_registration.py:187` / `test_self_alarm_registration.py:187`)
plus one more registration-pin assertion — **~8 lines of pin edits**, not a second ceremony.
The one-commit invariant is untouched.

**Three reasons this wins.**
1. The cost asymmetry is ~8 lines — it must not be the deciding input, and recording the
   corrected cost model prevents a future decision being taken on the false basis.
2. The commission's own premise — "read and act have radically different risk profiles" — is
   a **structural property**. Encoding it structurally (two categories) is cheaper than
   encoding it in prose plus one in-body gate that must be re-audited on every touch.
3. A future read-only grant to another agent becomes a **one-line allow change on a read
   category that structurally cannot carry acts** — the deferred read-tier question becomes
   expressible without widening the act surface.

**Per-tool allow is REJECTED outright.** A literal tool name in an allow list is added
verbatim and **bypasses the category gate** (the documented two-layer doctrine). A per-tool
allow mechanism on a privileged category deliberately opens that bypass and collapses the
two-layer gate into one.

**"Keep the scope tight" honoured.** That instruction was about not building a subsystem.
Two allow-list entries on one agent is not a subsystem.

**Forecloses.** A single mixed category. A per-tool allow mechanism. A future read grant
that drags the act surface with it.

---

## D-14 — The provenance implementation MOVES to a neutral home `daemon/tools/provenance.py`, with a FIVE-name delegating alias block in the live lane

**What.** Create `daemon/tools/provenance.py`; move the provenance implementation
(`_restart_provenance`, `_lookup_provenance_field`, `_capture_user_source`,
`_capture_provenance_class`, the verdict dataclass) there. Keep
`daemon/tools.upgrade_tools._restart_provenance` as a **delegating alias** with the identical
signature and verdict shape — **and (review W1) the alias is NOT just for one name.**

**Why — this supersedes D-3's "import, don't move" and fixes two defects at once.**
* The original reason for not moving — "the upgrade lane is live and its pins reference the
  private name" — was **half-true**: the pins reference the *name*, and the alias preserves
  the name.
* **The original drift-pin design was VACUOUS as specified.** If `instance_ops` imports the
  same function, AC-10's "compare the two gates on a shared fixture set" compares the helper
  **to itself** — it proves nothing except determinism, and it only has teeth if a second
  implementation exists, which D-4 forbids. The plan cannot have both.
* The behaviour-preservation risk is real but bounded: the helper is a pure read over two
  manager dicts, ~100 lines, no I/O.
* The architect verified the **T0a source-pin** (`test_agent_restart.py:127-133`) scopes its
  assertion to the `system_upgrade` **tool body**, which a module-level move does not enter.
  **⚠️ This claim MUST be independently re-verified by the reviewer AND the tester before
  merge — a named gate (AC-26), recorded in both reports.**

**The replacement pin (restated AC-10).**
* **AC-10a — WIRING pin:** an identity/source assertion that the `instance_ops` gate resolves
  to the single canonical helper in `daemon/tools/provenance.py` — no local re-implementation
  (source-grepped, the `test_agent_restart.py:127-133` directional-rule style).
* **AC-10b — VOCABULARY pins:** the verdict tokens (`ok`, `pclass` ∈ {direct, inherited},
  `depth`, `detail` strings) and the gate's refusal-token mapping are pinned, so **neither
  lane can silently change semantics**.

**The house precedent for a private import exists** (`job_recovery_service` importing
`_TERMINAL_INSTANCE_STATUSES`; `mission_resolver` importing `_STATUS_CANONICAL_MAP` and
calling it "the single source of truth"), but the commission's own D-10 rationale — "two
copies on a set that gates drift-cancels is a silent-divergence risk" — argues for a single
canonical home with a public name. The move is the only option that removes the coupling
permanently rather than papering it with a pin.

**No privilege widening — verified.** Privilege is per-category + per-agent allow-list +
per-tool in-body gate; module imports confer nothing.

**⚠️ W1 AMENDMENT (binding) — FIVE names move, so FIVE names need aliases.** The original
spec moved `_restart_provenance`, `_lookup_provenance_field`, `_capture_user_source`,
`_capture_provenance_class`, and `_ProvenanceVerdict` but aliased **only the first** — that
would break the live lane at runtime and one existing external pin. Verified consumption
sites **in `upgrade_tools.py` itself**: `:1734` and `:3043` (`_capture_provenance_class`),
`:1744` (`_capture_user_source`), `:2889` (**constructs `_ProvenanceVerdict` inside the
`system_restart` path**), `:1359` (`_lookup_provenance_field`) — plus `_EMPTY_PROVENANCE`
(`:1275`, `:1338`, `:1348`, `:1736`) if it moves with the dataclass (review delta: an earlier
`:2896` cite was misattributed — that line is a `_restart_provenance` call site, and the
`_ProvenanceVerdict` construction at `:2889` is already listed). **And an
EXTERNAL pin**: `tests/unit/tools/test_post_restart_arm_notify_journal.py:833`, `:844`,
`:852` **import `_capture_user_source` from `upgrade_tools`** — a pin the original one-commit
list never named.

**FIX (binding):**
1. Provide **delegating aliases for ALL FIVE moved names** (or a single top-of-module
   `from .provenance import …` rebinding block) in `upgrade_tools`.
2. **Extend the alias-equivalence pin to EACH name the live lane or a test consumes**
   (`_restart_provenance`, `_capture_user_source`, `_capture_provenance_class`,
   `_lookup_provenance_field`, `_ProvenanceVerdict`) — not just the one.
3. **ADD `tests/unit/tools/test_post_restart_arm_notify_journal.py` to the affected-pin list**
   in the implementation plan.

**NAMED PLACEMENT CONSTRAINT (from the reviewer's T0a verification — a HARD constraint in
implementation-plan §1.1a):** the T0a pin (`tests/unit/tools/test_agent_restart.py:127-137`)
slices `upgrade_tools.py` source from `src.index("async def system_upgrade")` to
`src.index("system_upgrade._full_doc_", si)` and asserts the slice contains
`windows.get(current_instance_id)` and **NONE of the moved names**. **The aliases MUST live at
MODULE level (near `:1384`) and MUST NEVER be nested inside `create_upgrade_tools` between
those two markers.**

**The reviewer's scope verdict on this decision (recorded):** *"No deviation is UNNECESSARY;
one is UNDER-SPECIFIED."* DV-1/DV-4 (the new audit table) are **necessary** — verified no
reusable audit table exists and `repair_log` is exactly 15 columns with 6 SQL-specific.
DV-2 (the dismiss extraction) is **necessary-conditional and honestly fenced**. **DV-3/D-14
(the provenance move) was the under-argued one** — option B (a public wrapper, no move) was
architect-acceptable and touches no live-lane code; **D-14 chose the move for
coupling-removal, which is fine, but it must be argued as such** — and W1 made it
under-specified. Both are now fixed above.

**Forecloses.** The private cross-lane import (and its rename-breakage risk: a rename in the
live lane would break the new module at import time — a cross-lane breakage owned by nobody).
The vacuous drift pin. Growing a second provenance implementation.

---

## D-15 — The audit boundary is a **two-tier** property, stated per tool — not a uniform same-transaction claim

**What.** Replace the uniform "the act and its audit row commit together or neither commits"
with the per-tool truth table (`plan-overview.md` §7.5):

* **Tier 1 — true same-transaction** (act + audit commit together or neither):
  `instance_terminate` via an **additive `audit_sink: Callable[[Session], None] | None =
  None`** hook invoked inside `_terminate_instance_db_sync`'s existing `WriteGuardSession`,
  **before commit**; `instance_mark(marker="protect"|"suspect")` via an optional `session=`
  parameter on `repo.set_metadata`. **Default `None` ⇒ byte-identical current behaviour for
  every existing caller** — the hook is strictly opt-in from the new tool layer.
  **⚠️ W3 AMENDMENT (leader decision): `instance_mark(marker="park")` is Tier-2, NOT Tier-1.**
  `park` writes through `mark_deliberate_pause_override` (`question_pause_set.py:608-655`),
  which calls `repo.set_metadata` inside its **own** `asyncio.to_thread`, has **no session
  parameter**, and **swallows exceptions** (fail-open, returns `False`). **Do NOT thread
  `session=` through it** — that is the pause/resume machinery, a live subsystem fixed in
  v0.26.1, and changing its fail-open behaviour is not this commission's business.
* **Tier 2 — write-ahead audit** for the acts with no single transaction to join
  (`instance_nudge`, `instance_answer`, `instance_dismiss`, `instance_reclaim`, **and
  `instance_mark(marker="park")`** — review W3): the tool
  **INSERTs the audit row FIRST** with `outcome="pending"` and the full intent, performs the
  act, then UPDATEs to `"committed"` / `"error"`. **A crash mid-act leaves a durable
  `pending` row** — the ledger always contains the intent.

**Why the original claim was partly unsound.** The ens-db pattern works there because the
ens-db act **is** a SQL statement in the tool's own session. `instance_ops` acts are daemon
service calls that **own their own transactions** — and for terminate there is a structural
blocker on top: the service's DB half runs on a **worker thread** via `asyncio.to_thread**
(`daemon/services/instance_lifecycle.py:2827`), and **a SQLAlchemy Session cannot be shared
across that boundary**. Options "(a) thread a session into the service" and "(c) move the
boundary into the tool" are both dead ends; (b) the in-service hook is the only Tier-1
shape for terminate.

**The R11 tiebreaker, honestly applied.** The original rule — "a future act that cannot be
transaction-coupled must be EXCLUDED from the category, not silently de-coupled" — applies
to **three of the six acts shipping now**, not a hypothetical future act. Either apply the
rule (cut nudge/answer/dismiss) or **generalize the write-ahead tier and state it in the
module docstring and the DDL comment**. D-15 takes the latter: cutting three tools to
preserve a uniform property trades real capability for a phrasing win.

**AC-9 splits accordingly:** AC-9a (fault-injection rolls back the act) covers **Tier-1
only** (terminate + mark protect/suspect); AC-9b (a crash mid-act leaves a `pending` row)
covers Tier-2 (nudge / answer / dismiss / reclaim / mark-park).

**W4 AMENDMENT — the Tier-2 reconciliation surface is specified, not hand-waved.**
"`instance_health` (or a sweeper) can reconcile" was under-specified: the six detectors cover
no audit ledger, and a sweeper would be a **new primitive** (D-3 forbids). The fix is the
**`fleet_capacity_summary.audit_pending` field** (`{count, oldest_age_seconds}`) — one cheap
count over the audit table — plus the documented rule that **a stuck `pending` row's TRUE
outcome is read from INSTANCE STATE, not from the ledger** (operator SQL in
`plan-overview.md` §4.4).

**Forecloses.** A uniform same-transaction claim that the tree cannot honour. Cutting three
acts to preserve a phrasing win. Silently de-coupling an act without declaring its tier.

---

## D-16 — A TERMINATE BUDGET + a target MIN-AGE rail (DB-side, over `instance_ops_audit`)

**What.**
* **Budget:** `count(action='instance_terminate' AND outcome='committed' AND created_at >
  now()-24h) >= 3` ⇒ refusal **`terminate-budget-exceeded`** — **N = 3 committed terminates
  / 24h, fleet-wide. N = 1 / 24h for protected-target terminates.**
* **Min-age rail:** refuse to terminate an instance younger than **600 s** ⇒ refusal
  **`target-too-young`** (the restart lane's min-uptime rail, transposed — one timestamp
  comparison; it kills the "terminate the thing that just started" footgun).

**Why.** The restart lane carries **four rails** (provenance, cooldown, min-uptime, sliding
budget — `upgrade_tools.py:2888-2934`); the original `instance_ops` design carried provenance
only. Given terminate's blast radius is **strictly larger** than restart's, the asymmetry was
unjustified.

**Why DB-side and NOT the upgrade journal's counter.** `restart_budget_count` reads
`releases/state.json` — an **upgrade-lane artifact**. Coupling an instance lane to the
release journal is the wrong direction and would break under a rollback-sweep. The
`instance_ops_audit` table exists by construction (D-2); the query is one SELECT; no new
subsystem, no new file, no journal dependency. **Nearly free.**

**Forecloses.** An unbudgeted fleet-destructive act. Coupling the instance lane to the
release journal.

---

## D-17 — `confirmed_target_id: str` replaces `user_confirmed: bool`

**What.** The confirmation parameter **names the target it authorizes**. The gate requires
`confirmed_target_id == id` verbatim; a mismatch ⇒ refusal **`confirmed-target-mismatch`**.

**Why — this substantially closes R2.** R2 was weak in a specific, quantifiable way: the
window is per-caller-instance with a 3600 s TTL and carries `root_source` +
`root_message_id` — **neither of which constrains the target**. Within the TTL, *any* target
satisfied the gate, so one genuine user message to the leader armed up to an hour of
protected-target terminates against arbitrary targets.

**What this is and is not.** It is **not** a semantic binding — the agent can still assert
falsely. But it converts an **unfalsifiable boolean** into an **attributable, audited
assertion**: the agent must *name the target it believes the user consented to*, the refusal
path teaches the correct shape, and a post-hoc reviewer comparing `confirmed_target_id`,
`reason`, and the user's `root_message_id` can detect a false claim. Cost: one parameter
rename and one equality check.

**The audit row records all five provenance fields:** `root_message_id`, `root_source`,
`pclass`, `depth`, `confirmed_target_id`.

**Forecloses.** `user_confirmed: bool`. An unfalsifiable confirmation claim. (Does NOT
foreclose DF-3 — a true confirmation-intent mechanism remains deferred, but its absence is
now materially less dangerous.)

---

## D-18 — Protected targets require `pclass == "inherited"` — a SPEED BUMP, **NOT** a proof of user intent

> **⚠️ REVIEW C1 AMENDMENT (binding): the original title and disposition claimed this
> decision "CLOSES the R1 forge". That claim is RETRACTED.** The reviewer's trace —
> independently verified in-tree before this amendment — is correct, and the original
> justification had its polarity inverted. The gate is KEPT as defense-in-depth with the
> bounds below honestly stated. The original text is retained below the corrected analysis
> (struck where it was wrong).

**What — unchanged.** For a protected-target terminate, require `pclass == "inherited"` in
addition to the live user-origin verdict. A **direct** window ⇒ refusal
`direct-provenance-insufficient` — genuine or forged.

**THE CORRECTED ANALYSIS (what the gate ACTUALLY closes).**

* **CLOSED — the direct-window-on-the-acting-instance class:**
  * the trivial `localhost` POST with `source="api"` straight at a maintenancer instance
    (**the F2 forgery class**, critical note `fd3689a1`); and
  * a raw direct dispatch to the maintenancer instance.
  Both mint `pclass="direct"` on the acting instance ⇒ refused.
* **NOT CLOSED — the delegated forge, which is R1's REAL shape.** The verified trace:
  1. the sweep delivers the promote-kind wake to **`ari_instance_id`**
     (`upgrade_journal_sweep.py:1205`) or the arming instance (`:1724`) — **not to the
     maintenancer**;
  2. the funnel stamps a **DIRECT** window on that woken target (`daemon/manager.py:9615` →
     `stamp_user_origin_window:4850`; `classify_user_origin("api")` → `exact:api`,
     `upgrade_journal.py:3391-3392`) — **with no genuine user ancestor**;
  3. Ari then delegates to the maintenancer with `source=f"internal_agent:{ari_iid}"`
     (`daemon/tools/instance.py:964-969`, `:3493`, `:4152`, `:4259`);
  4. `_inherit_provenance_window` mints `pclass="inherited"`, `depth=1` from the caller's
     live direct window **regardless of whether that root was genuine**
     (`daemon/manager.py:4914-4990` — the mint checks **prefix-match, caller-liveness, TTL,
     and the depth cap ONLY; it never inspects the root's provenance**);
  5. and `root_source="api"` is **ambiguous** — a genuine operator POST is also `exact:api`.

  **Consequence: requiring `pclass=="inherited"` ADMITS the delegated forge and REFUSES the
  genuine operator-direct path. The gate's polarity was inverted relative to its
  justification.** The ROOT fix is **DF-4 / critical note `f4cf9c4f`** (the sweep's
  `or "api"` fallback) — a separate lane, **NOT fixed here**.

**Why the gate is KEPT anyway (defense-in-depth, four layers, named):**
(a) **D-17 attributable confirmation** — the act names its target and the audit records it;
(b) **D-16 the N=1/24h protected-target budget**; (c) **the audit row records `root_source` +
`depth` for post-hoc detection**; (d) the protected static set + the self-surgery refusal +
the dry-run default + the kill-switch.
**Stated plainly: this is a SPEED BUMP PLUS AUDIT, NOT A PROOF OF USER INTENT.**

**AC-23 is AMENDED to TWO fixtures (C1 point 3):**
* **AC-23a** — a direct window **on the maintenancer** ⇒ REFUSED. This is the class D-18
  genuinely closes, **including the genuine operator-direct path** — that refusal is
  INTENDED and L-9 stands.
* **AC-23b** — a **delegated-forge fixture** (forged direct window on the SPAWNER → inherited
  window on the maintenancer) asserting the **DOCUMENTED RESIDUAL behaviour** — the act is
  **ADMITTED**. **The test suite PINS THE TRUTH rather than a false security property.**
  The original single-fixture AC-23 tested a shape the real forge cannot mint (a direct mint
  on the maintenancer) — it would have **passed while proving nothing about R1**.

**Overbroad sentence corrected (C1 point 4).** The original text implied any delegated
dispatch inherits. The truth: `_inherit_provenance_window` keys **ONLY** on the
`internal_agent:` prefix; **`internal_invoke_and_wait:*` deliberately NEVER inherits**, and
**any non-`internal_agent:` dispatch CLEARS the inherited window** (`daemon/manager.py:4914+`
docstring). So **a delegated turn arriving on another lane REFUSES (fail-closed)** — this must
be stated in the refusal doc surface too.

**Disclosed consequence of D-18 (an accepted design, not an accident):** a user message sent
**directly** to a maintenancer instance (an operator POSTing to its instance id via the API)
carries a **direct** window and therefore **cannot** authorize a protected terminate. **That
is intended** — the maintenancer is a **delegated leaf, not user-facing**; a fleet-destructive
act against a protected target should require a delegation chain from a user-facing turn.
The refusal message names the workaround ("route via the leader"). Recorded as accepted
limitation **L-9**.

**The fallback the architect offered (visibility only: stamp `provenance_suspect=true` on
direct windows and surface a counter) is NOT taken as a REPLACEMENT** — but its spirit is
absorbed into layer (c): the audit row's `root_source` + `depth` make the forge shape
**countable post-hoc**.

**~~ORIGINAL (RETRACTED) JUSTIFICATION, retained for the trail~~:**
~~"The genuine authorization path for a specialist is always delegated… Therefore refusing
`pclass="direct"` for protected-target acts blocks the R1 forge completely while preserving
the genuine delegated path. One line in the gate; one test (AC-23). Without touching the
upgrade lane."~~ — **the first sentence is true; the "therefore" is FALSE**: the forge also
arrives delegated (via the woken Ari), and the mint never checks the root's genuineness.

**Forecloses.** The trivial direct-POST forgery class and raw direct dispatch to the
maintenancer. **Does NOT foreclose** the delegated forge (DF-4 owns that).

---

## D-19 — An ORTHOGONAL field model, zero new vocabularies; and reporting predicates + disagreement pins

> **Wording corrected by review A8:** the original heading said "three orthogonal dimensions"
> but the list has **four** items — `pause_marker` is reported alongside. The substantive
> claim is "no new vocabulary is minted"; the count was wrong.

**Part 1 — REJECT the `alive | stalled | terminal` third vocabulary (supersedes D-9).**

**Why it was wrong.** It conflated the liveness axis with the time axis and **erased exactly
the park/wedge distinction the commission exists to preserve**. Under the D-9 derivation,
`stalled` = alive-status AND stale. A deliberately parked instance (`paused`, with the D-11
`park` tombstone) has a **frozen `last_activity_at`**, so past the staleness threshold it
reported `stalled`, **byte-identical to a genuinely wedged one**. The `park` marker exists
precisely to make that distinction; the derived vocabulary deleted it.

**The replacement — an orthogonal field model (four reported items), zero new vocabularies:**
1. **Filter on the raw `status`** (the instance's own 10-value domain) — **SQL-side via the
   existing `repo.list(status=...)`** (GT-3).
2. **Report canonical `liveness` as a derived FIELD, never a filter** — from
   `_STATUS_CANONICAL_MAP` (`daemon/services/work_status.py:67`, *"the single source of
   truth — no parallel vocabulary set is maintained"*), reusing the existing
   instance-domain inversion helper (`mission_resolver._instance_statuses_for_liveness`).
3. **Report staleness as a separate, explicitly-named field** — `idle_seconds: int | null` +
   `stale: bool` against the caller-supplied threshold, via the **lineage-MAX** aggregate
   (R4). Never folded into the liveness enum.
4. **Report `pause_marker` alongside** — so a `paused` row reads as *"deliberately parked,
   4h idle"*, not *"stalled"*.

A caller wanting `alive`/`stalled` computes it client-side from `status` + `stale` in one
line. The typed `unknown-liveness-value` refusal survives for non-canonical values.

**Part 2 — REPLACE "compose the six sweeps' predicates" with reporting predicates +
disagreement pins (supersedes D-10's mechanism; GT-4 showed the premise was false).**

* **Extract nothing.** Refactoring six live sweep modules to expose predicates is a far
  larger behaviour-surface change than this commission should make, and risks the sweeps'
  own carefully-tuned epistemology (their per-sweep fail-open vs fail-closed choices).
* **Write the detectors in the new module as REPORTING predicates** — cheap, read-only,
  explicitly labelled as a projection.
* **Add a DISAGREEMENT PIN, not a verdict-equality pin.** Where a sweep counterpart is
  observable (`OrphanWatcherSweepService.counters()`,
  `WaitingChildrenWatchdog.wedge_episodes()`), a test constructs a fixture the sweep itself
  classifies and asserts the health report flags the same set. **Two independent code paths
  must agree on a labelled corpus.** Where no observable counterpart exists, the detector's
  row says so and the pin is **skipped** — an honest gap, not a pretend one.
* **The R4 lineage-MAX rule stays hard and tested** — the one place where the new detector's
  semantics are already settled by the tree.

**Forecloses.** A third liveness vocabulary (three vocabularies guaranteed to drift). The
park/wedge conflation. Extracting predicates from live sweeps. Importing private methods
across module boundaries (a coupling as fragile as copying, plus it couples to destructive
classes). An unpinned second source of truth.

---

## D-20 — THE CUT OVERRULED, its substance ACCEPTED: `instance_reclaim` REDEFINED as a terminal-only revive-by-documented-message wrapper

**The architect's recommendation: CUT `instance_reclaim`.** Three independent reasons, any
one sufficient — recorded here in full so the overrule is auditable:

1. **It was not implementable as specified.** GT-5: `_schedule_explicit_handle_resume`
   requires a pre-computed suspension handle (`target_work_id`, `handle_work_id`,
   `selected_suspension_reason`, `route_outcome` — `daemon/manager.py:12433-12444`). A tool
   taking a bare `instance_id` must compute the handle — i.e. re-implement the boot pass's
   selector. That is a new primitive by D-3's own definition, and exactly the "second source
   of truth" D-10 forbids, in the lane where a wrong selection silently resumes the wrong turn.
2. **Its value case is already served automatically.** The primitive's contract is "resume
   the interrupted turn of an instance that was RUNNING when the daemon died" — which the
   boot auto-continue pass already does, at boot, for every qualifying instance.
3. **Its audit coupling was the weakest in the set** (a scheduler, no transaction to join),
   so it contributed disproportionate complexity to the governance contract for the least
   governable act.

**THE LEADER OVERRULE.** The cut is overruled; the substance is accepted.

**The redefinition.** `instance_reclaim(id, message, reason)` is a **TERMINAL-ONLY,
REASON-REQUIRED, REVIVE-BY-DOCUMENTED-MESSAGE** wrapper over the existing revive-on-send
primitive: a message to a `COMPLETED` / `TERMINATED` / `ERROR` / `FAILED` instance flips it
to `RUNNING` and reuses the checkpoint (`daemon/services/instance_messaging.py:2365-2387`).
It refuses on non-terminal targets, refuses self-surgery, and writes its audit row (Tier-2).

**The leader's reasoning against the cut — three points:**
1. **The commissioned capability IS deliverable as a thin wrapper.** "A dead instance is
   back and usable" does not require the silent-resume primitive; the existing revive-on-send
   path delivers it today, with no selector to re-implement.
2. **The message itself is the revival record**, which answers the architect's "least
   auditable" criticism (reason 3): the HumanMessage landing in the revived instance's
   checkpoint IS durable evidence of what was revived and why, independent of the audit row.
   A silent resume has no such record — the criticism cuts **against** the silent variant,
   not the wrapper.
3. **The terminal-only guard (`target-not-terminal`) distinguishes it from `instance_nudge`**
   — reclaim is unambiguously "bring a dead one back," nudge is "deliver to anyone." Without
   the tool, the only revival path is nudge, which conflates the two intents.

**The architect's substance that IS accepted:** the silent checkpoint-restore semantics stay
**DEFERRED (DF-1, unchanged)** — the architect is right that the silent primitive is not
wrappable. **10 tools ship, not 8.**

**Forecloses.** The silent-reclaim variant. Cutting the capability. Conflating reclaim with
nudge.

---

## Deviation summary (recorded, not buried)

| # | Commission line | Deviation | Justification | Severity |
|---|---|---|---|---|
| DV-1 | "reuse existing tables" | **New `instance_ops_audit` table** | No generic audit table exists; the `event` table is not an audit ledger; ari-tools rolls no audit table at all. Vocabulary copied from `repair_log`; fail-closed same-transaction write copied from `repair_log`. | **Material** — stated in D-2; additive-only is preserved |
| DV-2 | "no new primitives" | **Additive extraction** of the dismiss body from `daemon/routers/instances.py:1239-1460` into a service function (router delegates) — **scoped honestly** per the architect: ~200 lines, live-hub injection, 4 `HTTPException`→typed-error conversions, an SSE-before-cascade ordering test, and the mandatory route-table readback pin | No reusable dismiss service exists, and the router handler is **not importable as-is** (takes `request: Request`, reaches into `request.app.state.live_hub` at `:1330`). **`instance_answer` needs NO extraction** — `answer_questions_via_instance` takes `live_hub` as a plain parameter (`answer_helper.py:704-712`) | **Medium** — a real refactor of a live route, not a delegation edit. The original "behaviour-preserving-by-construction" framing was too strong |
| DV-3 | "reuse existing user-provenance machinery" | ~~**Import** of the private `_restart_provenance`~~ → **SUPERSEDED by D-14**: neutral home `daemon/tools/provenance.py` + a FIVE-name delegating alias block in `upgrade_tools` (§1.1a of the implementation plan) | The architect verified the T0a pin scopes to the tool **body**, so a module-level move does not break it; the live lane's name/signature/pins are preserved by the alias. **Reviewer AND tester must independently re-verify (a named gate).** | Medium — the live lane's file IS touched (behaviour-preserving); the vacuous-drift-pin defect is also fixed (AC-10a/10b) |
| DV-4 | "reuse existing tables" (audit) | `instance_ops_audit` **copies `repair_log`'s vocabulary** rather than reusing the table | Cross-category schema home + a documented enum that a new value would pollute. | Material — same root as DV-1 |

---

## Deferred (explicitly NOT in this commission)

| # | Deferred item | Why | What would unblock it |
|---|---|---|---|
| DF-1 | `instance_reclaim`'s **SILENT checkpoint-restore** semantics (a reclaim with no delivered message) | GT-5: the silent primitive takes a pre-computed suspension handle and is not wrappable without re-implementing the boot pass's selector; D-3 forbids it. **D-20 keeps this deferred while shipping the message-carrying wrapper** | A new manager primitive (own commission) |
| DF-2 | `unprotect` marker | Would let a marker tool un-protect a **statically** protected target (D-4) | A distinct, separately-gated act with its own confirmation |
| DF-3 | Binding a user utterance to a **specific** target (R2) | Needs a confirmation-intent mechanism | A new mechanism (own commission) |
| DF-4 | Fixing the inherited user-origin forge path (R1) | Cross-lane; the upgrade lane's exposure is already filed (critical note `f4cf9c4f`) | An upgrade-lane fix; `instance_ops` inherits the residual |
| DF-5 | A **generic** cross-category audit framework | Out of scope | A separate architecture commission |
| DF-6 | HTTP routes for the 10 tools (the tool surface only) | The commission is a tool-category commission | A follow-up API commission |
| ~~DF-7~~ | ~~Moving `_restart_provenance` to a neutral module~~ | **SUPERSEDED — now DONE by D-14** (neutral home + delegating alias) | — |
| DF-8 | Retrofitting the leader's **ungoverned** `terminate_instance` (GT-2) to the governed pattern — dry-run / reason / audit / protected-target gate / kill-switch — or pushing that governance down into the service, or bringing the `instance` category into the privileged set | A behaviour change for existing callers; out of scope here (R21). The commission adds a governed path and leaves the ungoverned one untouched | A separate decision, with its own blast-radius analysis |
