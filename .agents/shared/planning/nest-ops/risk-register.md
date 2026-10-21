# nest-ops — Risk Register

**Commission:** nest-ops · `instance_ops` privileged tool category for the maintenancer
**Base:** `origin/latest` @ `8e6df58e445cd56b5e775dacebe0ef53f0add0b4`

**Posture:** the leader's residuals R1-R8 are recorded here **verbatim in substance, not
papered over**. R9-R20 are the commission's own failure modes, each with a mitigation and a
test. **Amended 2026-10-21** by the architect adjudication: **R1 is now CLOSED for this lane
(D-18)**, **R2 is substantially closed (D-17)**, **R11 is now an honest two-tier property
(D-15)**, **R21 is NEW (leader's ungoverned terminate)**; L-9 is a new accepted limitation.

**Severity key:** 🔴 critical (can destroy live work) · 🟠 high (can wedge or mislead) ·
🟡 medium · 🟢 low / disclosed limitation.

---

## Part A — The leader's residuals (recorded, not fixed here)

### R1 🔴 — USER-ORIGIN FORGE PATH (pre-existing, cross-lane)

`daemon/services/upgrade_journal_sweep.py:1734` and `:1209` fall back to
`source=(head.source or "api")` for promote-kind wakes, **minting a DIRECT user-origin
window with NO genuine user ancestor**. Any user-origin-gated destructive op **inherits this
bypass**; the upgrade lane's own exposure is already filed in **project critical note
`f4cf9c4f`**.

**Scope of the exposure — narrow, and stated precisely.** The reserved/other sources
(`scheduler`, `system:*`, `internal_*:`, `cascade_resume`, `agent:*`) are **correctly
refused** by `classify_user_origin` (`daemon/tools/upgrade_journal.py:3376-3426`). The
exposure is specifically the promote-kind wake fallback.

**Disposition — RE-OPENED for the DELEGATED LEG by review C1 (the original "CLOSED FOR THIS
LANE by D-18" disposition is RETRACTED; D-18's real bounds are below).**

**The reviewer's trace, independently re-verified in-tree before this amendment — CORRECT:**
1. the sweep delivers the promote-kind wake to **`ari_instance_id`**
   (`upgrade_journal_sweep.py:1205`) or the arming instance (`:1724`) — **NOT to the
   maintenancer** — *except when the arming instance itself is a maintenancer (it holds
   `system_upgrade`); that shape mints direct-on-the-acting-instance and falls in the
   CLOSED (refused) class* —;
2. the funnel stamps a **DIRECT** window on that woken target (`daemon/manager.py:9615` →
   `stamp_user_origin_window:4850`; `classify_user_origin("api")` → `exact:api`,
   `upgrade_journal.py:3391-3392`) — **no genuine user ancestor**;
3. Ari delegates to the maintenancer with `source=f"internal_agent:{ari_iid}"`
   (`daemon/tools/instance.py:964-969`, `:3493`, `:4152`, `:4259`);
4. `_inherit_provenance_window` mints `pclass="inherited"`, `depth=1` from the caller's live
   direct window **regardless of the root's genuineness** (`daemon/manager.py:4914-4990` —
   checks prefix-match, caller-liveness, TTL, depth cap **ONLY; never the root's
   provenance**);
5. `root_source="api"` is **ambiguous** — a genuine operator POST is also `exact:api`.

**Consequence: D-18's `pclass=="inherited"` requirement ADMITS the delegated forge and
REFUSES the genuine operator-direct path — the gate's polarity was inverted relative to its
justification.** The original AC-23 fixture (forge → direct mint **on the maintenancer**)
tested a shape the real forge cannot mint: it would have **passed while proving nothing
about R1**.

**What D-18 DOES close — and it is not nothing:** the **direct-window-on-the-acting-instance
class**, i.e. the trivial `localhost` POST with `source="api"` straight at a maintenancer
instance (**the F2 forgery class**, critical note `fd3689a1`) and a raw direct dispatch to
the maintenancer. Both mint `pclass="direct"` on the acting instance ⇒ refused.

**Re-scoped precisely:**
* **CLOSED by D-18:** the direct-window-on-the-acting-instance class.
* **OPEN (this lane's residual):** a **forged root that propagates as inherited** (the
  promote-wake sweep path). **Root fix = DF-4 / critical note `f4cf9c4f`** (the sweep's
  `or "api"` fallback) — a separate lane, **NOT fixed here**.
* **Pinned as truth:** **AC-23b** asserts the delegated forge is **ADMITTED**, with the
  assertion message naming DF-4/`f4cf9c4f` — so the suite certifies the documented residual
  rather than a property that does not hold.

**The retained defense-in-depth, named honestly (four layers):**
(a) **D-17 attributable confirmation** — the act names its target; the audit records
`confirmed_target_id`, `reason`, and the user's `root_message_id`, so a post-hoc reviewer can
detect a false claim; (b) **D-16's N=1/24h protected-target budget**; (c) **the audit row's
`root_source` + `depth` make the forge shape countable post-hoc**; (d) the protected static
set + the self-surgery refusal + the dry-run default + the kill-switch.
**Stated plainly: this is a SPEED BUMP PLUS AUDIT, NOT A PROOF OF USER INTENT.**

**Bonus hardening recorded (C1 point 4):** `_inherit_provenance_window` keys ONLY on the
`internal_agent:` prefix — **`internal_invoke_and_wait:*` deliberately NEVER inherits**, and
**any non-`internal_agent:` dispatch CLEARS the inherited window** (`daemon/manager.py:4914+`
docstring). So a delegated turn arriving on another lane REFUSES (fail-closed). This must be
in the refusal doc surface.

### R2 🟠 — THE GATE PROVES A USER SPOKE, NOT THAT THEY SPOKE ABOUT THIS TARGET

The provenance window carries `root_source` + `root_message_id`, but **there is no mechanism
binding a user utterance to a specific target**. A user message that said *"yes, do it"*
about something else entirely still arms the window for any target.

**Disposition — SUBSTANTIALLY CLOSED by D-17.** The original mitigation was **two-factor**
(`user_confirmed=True` + a live window), but `user_confirmed` was an **unfalsifiable
boolean** and the window constrained the *caller*, not the *target*: within the 3600 s TTL,
**any** target satisfied the gate. **D-17 replaces it with `confirmed_target_id: str`** — the
agent must **name the target it believes the user consented to**; the gate requires
exact equality. This does not make a false claim impossible, but it makes one **attributable
and auditable**: a reviewer compares `confirmed_target_id`, `reason`, and the user's
`root_message_id` (all recorded on the audit row) and can detect a mismatch. Cost: one
parameter rename + one equality check.

**Residual:** a true **confirmation-intent** mechanism (binding the utterance's *content* to
the target) remains **deferred** (DF-3). The absence is materially less dangerous, not
eliminated.

---

### R3 🟡 — WINDOWS ARE IN-MEMORY → a daemon restart between the user's message and the act makes the gate REFUSE

`_user_origin_windows` (`daemon/manager.py:1560`) and `_inherited_provenance_windows`
(`daemon/manager.py:1586`) are **in-memory dicts on the manager** and are **wiped on daemon
restart**.

**This is fail-closed and CORRECT.** It is disclosed rather than implied. The operational
consequence: a maintenancer turn that spans a restart cannot terminate a protected target
without a fresh user turn. That is the intended direction of failure.

**Mitigation (in scope):** the refusal text names the reason (`no-user-provenance`) and the
refusal message discloses what counts as user-origin
(`user_origin_sources_display()`, `daemon/tools/upgrade_journal.py:3363`).

---

### R4 🟠 — `last_activity_at` FREEZES on a `waiting_children` parent → a naive stale-running detector false-positives on every live parent

Documented at `daemon/repositories/instance/repository.py:670-700`:
*"the per-row signal provably fails — `last_activity_at` FREEZES on a waiting_children
parent while a descendant streams mid-LLM — so the guard must aggregate MAX over the whole
permanent lineage"*.

**Mitigation (in scope, MANDATORY).** The `instance_health` stale-running detector must
use `get_max_last_activity_in_instances` (the existing lineage-MAX primitive,
`daemon/repositories/instance/repository.py:670`) over the permanent lineage — exactly as the
Pattern-f1 subtree-alive guard does. A per-row staleness check is **forbidden** in this
commission.

**Test:** the health test includes a live `waiting_children` parent with a streaming
descendant and asserts it is **NOT** flagged.

---

### R5 🟡 — There is NO house precedent for a category-wide actor/target/before/after audit

The `event` table is **not an audit table** (filtered by `JobFeedbackObserver` on
`event_type != "instance_lifecycle"`), and ari-tools rolls its own trails (`board_notes`;
`self_alarm` has none). ⇒ **No generic audit table exists.**

**Disposition.** This commission **establishes one, for `instance_ops` only** (D-2). The
vocabulary is copied from `repair_log` (`daemon/repositories/ens_db/models.py:44`) so it is
house-consistent, but the table is **dedicated** — no cross-category coupling, and
`repair_log`'s SQL-repair-specific columns and its documented `sql_class` enum are not
polluted.

**Residual risk:** the new table is a **second** audit mechanism in the daemon, and a future
commissioner may try to generalise it. That generalisation (DF-5) is deliberately **not**
this commission's job; the table's docstring must state that it is `instance_ops`-specific.

---

### R6 🟠 — The 3-red inherited pin AND the `ROLLOUT.md:51` exact-string assert must BOTH be handled in the ONE pin commit

* `tests/integration/test_maintenancer_spawn_resolves_tools.py` — 3 reds at `:125`, `:137`,
  `:208`, caused by `ff27305c4`. **Fixed INLINE** (leader D-1): the commission edits the
  exact allow-list the pin asserts; leaving it red would destroy the enforcement signal.
* `agents/maintenancer/ROLLOUT.md:51` — an exact **ORDERED** string assert that turns RED
  with **no test runner involved**. **Invisible to pytest.**

**Mitigation.** The commit disclosure block names both, with the red counts (3 → 0, net-new
0). The build sequence makes them one commit with the registry edit.

**Standing procedural lesson recorded here:** *a runbook assert is invisible to pytest —
grep the agent dir for allow-list assertions, not just the tests dir.* The commission's
build sequence carries this as an explicit step.

---

### R7 🟡 — A daemon restart is REQUIRED for the category to be live

`_registry` is an import-time singleton (`daemon/registry.py:1474-1496`); `discover()` runs
**once per process**. Without a restart the category is **not** registered and the
maintenancer's allow entry is inert.

**Mitigation.** Stated in the implementation plan's deployment section, the release notes,
and the operator runbook. The isolated boot smoke (S-15) asserts the 10 tools appear in the
maintenancer's resolved set — proving the restart path works.

---

### R8 🟠 — Question packs are IN-MEMORY → after a restart the pack is LOST

`daemon/services/question_manager.py:223-226` — `self._packs: dict[str, QuestionPack] = {}`
behind a `threading.Lock`. There is **no questions table** in the DB. The pack does not
survive a daemon restart.

**This is exactly the "tree-wedging kind" the commission names**: a tree parked on a
question whose pack no longer exists — the release path has nothing to release.

**Mitigation (in scope).** `instance_answer` and `instance_dismiss` must return
`410 QUESTION_PACK_LOST` **honestly** when the pack is absent, reproducing the route's
documented error contract (`daemon/routers/instances.py:1088-1130`). They must **not**
fabricate a pack, must not treat "no pack" as "answered", and must not silently no-op.

**Residual.** The pack-loss itself is not fixed (a durable pack store is a new subsystem —
D-3 forbids it). The commission makes the loss **visible and correctly typed**.

---

## Part B — The commission's own failure modes

### R9 🔴 — Terminating a LIVE CHAT ROOT (the user's active session)

**Failure mode.** `instance_terminate` on a user-facing chat instance kills the user's
conversation, its in-flight turn, and any tree under it.

**Mitigation (layered).**
1. **Static protection** — `_is_user_facing_root(parent_id=…, source_type=…)`
   (`daemon/tools/instance.py:2561-2592`) — fail-closed on **both** legs.
2. **Static role protection** — `agent_id == "leader"`.
3. **Dynamic protection** — the `instance_ops.protect` marker.
4. **Three-condition confirmation (D-17/D-18)** — `confirmed_target_id == id` (the act
   **names its target**; a mismatch ⇒ `confirmed-target-mismatch`) **AND** a live
   provenance verdict **AND** `pclass == "inherited"` (D-18 — see R1 for its real, honestly
   bounded value). Any missing ⇒ refusal.
5. **Dry-run first** — `dry_run` defaults **TRUE**, and the preview names the exact
   instances that would be terminated. The operator sees the list before the act.
6. **Audit** — one row, same transaction.

**Residual.** The gate proves *a user spoke*, not *about this target* (R2).

---

### R10 🔴 — WEDGING A TREE (terminating a parent while descendants run)

**Failure mode.** `cascade=False` terminates the parent, leaves descendants running, and
creates exactly the wedge class this commission's own `instance_health` must detect —
descendants waiting on a dead parent.

**Mitigation.** The service cascade is **implicit and total**
(`daemon/services/instance_lifecycle.py:2539`) — there is no service-level single-instance
path. The tool implements the single-instance semantics in the tool layer and **REFUSES**
`cascade=False` on a target with live descendants
(`cascade-false-would-orphan-descendants`) unless `allow_orphan_descendants=True` is passed,
in which case the **dry-run preview AND the audit row both name the orphans** (D-5).

**Residual.** With `allow_orphan_descendants=True` the operator has accepted the wedge;
`instance_health` will then report it. That is the designed loop.

---

### R11 🔴 — AUDIT GAP (an act that happens but is not recorded)

**Failure mode.** A destructive act commits; the audit row does not. The fleet has no
record of who terminated what.

**Mitigation — an HONEST TWO-TIER property (D-15), stated per tool.** The original uniform
claim ("the act and the audit row commit together or neither commits") was **partly
unsound**: `instance_ops` acts are daemon service calls that own their own transactions, and
terminate's DB half runs on a **worker thread** via `asyncio.to_thread`
(`daemon/services/instance_lifecycle.py:2827`) where a Session cannot be shared.

* **TIER 1 — true same-transaction:** `instance_terminate` (via an **additive `audit_sink`
  hook inside the service's existing `WriteGuardSession`, before commit; default `None` ⇒
  byte-identical for every existing caller**) and `instance_mark` (via an optional `session=`
  on `repo.set_metadata`). An audit-write failure **rolls back the act**. **AC-9a** — a
  fault-injection test is mandatory.
* **TIER 2 — write-ahead:** `instance_nudge` / `instance_answer` / `instance_dismiss` /
  `instance_reclaim` / **`instance_mark(park)` (W3 / leader decision — `park` writes through
  the fail-open `mark_deliberate_pause_override`, and threading `session=` into the live
  pause/resume machinery is out of scope)** — the audit row is **INSERTed FIRST** with
  `outcome="pending"` and the full intent, the act runs, then the row is UPDATEd to
  `"committed"` / `"error"`. **A crash mid-act (deterministically: exception injection,
  per A5) leaves a durable `pending` row** — the ledger always contains the intent, surfaced
  by `fleet_capacity_summary.audit_pending` (W4). **AC-9b.**

**Three of the six shipping acts are NOT transaction-couplable — declared in the table, not
silently de-coupled.** The original R11 rule ("a future non-couplable act must be excluded")
was the honest tiebreaker and it applied to **three acts shipping now**; D-15 generalizes the
write-ahead tier instead of cutting them, and states the property in the module docstring and
the table's DDL comment.

**Residual — the remaining hole.** If a future act can be neither transaction-coupled **nor**
honestly write-ahead, it must be **excluded from the category**. Stated in the repository
docstring so the generalization does not become licence.

---

### R12 🔴 — PROTECTED-TARGET BYPASS

**Failure mode.** The category gate is bypassed by a **literal tool name** in an allow list
(the tool-layer category-expansion loop adds a bare name verbatim), or the in-body gate is
forgotten on one of the ten tools.

**Mitigation.**
* Layer 1 — `instance_ops` ∈ `PRIVILEGED_TOOL_CATEGORIES` (default-deny for the
  empty-allow universe).
* Layer 3 — **every** closure calls the shared `_is_instance_ops_authorized(agent_id)`,
  fail-closed on a blank / non-`str` `agent_id`.
* **One shared helper, ten call sites** — the `views_auth.py` pattern (one helper, two call
  sites) generalised. A copy-paste of the gate into ten bodies is the exact divergence the
  shared helper exists to prevent.
* A **registration pin** asserts the gate helper exists and that the category is privileged.

---

### R13 🟠 — SELF-SURGERY (an agent acting on its own instance)

**Failure mode.** The maintenancer terminates the very turn it is running in, or answers
its own pending question pack, or marks itself protected.

**Mitigation.** A **direct identity check** (`target_id == current_instance_id`) on every
act tool ⇒ refusal `self-surgery-refused`. This is **strictly stronger** than the ens-db
lane's `_refuse_self_surgery` heuristic (`daemon/tools/ens_db_tools.py:437-476`), which
inspects SQL text.

**Residual.** The check is per-target. A **tree** self-surgery (terminating the caller's
own parent) is not covered — the caller has no reason to reach its own parent, and the
protected-target gate would still apply if the parent is a chat root. Recorded as a known
boundary.

---

### R14 🟠 — TOCTOU between the check and the act

**Failure mode.** The protected-target determination, the cascade tree snapshot, and the
provenance verdict are all read **before** the act. Between the read and the act, the
target's state can change — a chat root can be created, a marker can be removed, the
provenance window can expire.

**Mitigation.**
* The **cascade tree snapshot is taken inside the act**, by the service
  (`repo.get_cascade_tree_ids` inside `terminate_instance`,
  `daemon/services/instance_lifecycle.py:2539`) — the tool's preview snapshot is a
  *preview*, and the tool's doc surface must say so: **the applied cascade may differ from
  the preview if the tree changed in between.** The audit row records the **actual** tree
  that was terminated (`before_snapshot`/`after_snapshot` carry the real ids), so the ledger
  shows the truth, not the preview.
* The provenance verdict is checked **immediately before** the act, and the window TTL is
  3600 s — long enough that an intra-turn expiry is possible but the window is read fresh.
* The `protect` marker is read fresh inside the act.

**Residual.** The preview and the act can differ. Documented; the audit row is authoritative.

---

### R15 🟡 — POOL CONTENTION (the commission is dispatched into a contended worker pool)

**Failure mode.** The category's own tools compete with live commissions for the worker
pool; a `instance_nudge` or `instance_reclaim` could be starved while the fleet it is
diagnosing is busy.

**Mitigation.** The read tools are cheap and bounded (`limit` clamped to `[1, 200]`; the
health report is predicate-only, no repair sweeps). `fleet_capacity_summary` reads
counters, not rows-in-heat.

**Residual.** Accepted. The commission's own build constraint (the host worker pool was
contended) was honoured by doing this design work **without spawning children**.

---

### R16 🟡 — STAGE / PROMOTE DISCIPLINE

**Failure mode.** The category lands in a release that is promoted without the required
daemon restart, or a non-tip tree is staged, or the migration delta is adjudicated by
reflex.

**Mitigation.**
* **No promote in this commission.** The 3-factor nonce ceremony is user-gated. **Stage
  only.**
* **Version resolved at stage time**, not assumed — a separate commission owns the v0.27.0
  line.
* **L1 tip-identity guard**: `HEAD == refs/remotes/origin/latest`; a non-tip tree ⇒ exit 78
  `non-tip-tree`. **No override is needed or wanted.**
* **Rollback-safety** is adjudicated by **explicit semantic reasoning** (the standing rule
  for a non-empty delta), not by a reflex `=0`: the delta is **+1 file** (107 vs 106), the
  file is `CREATE TABLE` (additive), the old binary never references `instance_ops_audit`
  ⇒ the table is inert on rollback, no down-migration needed ⇒ `rollback_safe = true`,
  `source=explicit`, with the reasoning recorded verbatim in the manifest.
* **Boot smokes in an isolated `systemd-run` transient unit** with a scratch CWD + scratch
  PG — **never** inside the live daemon's cgroup.
* **Never resolve live-shaped (HOME-derived) paths in test scripts** — fence via
  `mktemp FAKE_HOME` + an escape guard.

---

### R17 🟠 — THE DOC SURFACE DRIFTS FROM THE BEHAVIOUR (the D-V2 lesson)

**Failure mode.** A tool's `args_schema` / `_full_doc_` / error envelope says one thing and
the tool does another. **This already happened**: a stale doc string shipped live in
**v0.26.2** and was only fixed in **v0.26.3** — the behavioural ruling was live while the
doc told the agent the old thing.

**Mitigation.** The doc surface is written **with** the implementation, never after, and a
**doc-surface pin** asserts the three surfaces agree and state the current semantics
(`dry_run` default TRUE; reason required; kill-switch behaviour). Modelled on
`TestDocSurfaceWakeTheCaller`.

**Specific surfaces at risk in this commission:** the `cascade` semantics (the service has
no cascade flag — the doc must not imply one exists); **`instance_reclaim`'s D-20 semantics
(terminal-only, message-carrying, revive-on-send — NOT a silent checkpoint restore)**; the
kill-switch's scope (acts disabled, reads live); the provenance window's in-memory nature
(R3); **D-18's disclosure** (a direct user message to the maintenancer cannot authorize a
protected terminate — design, not accident); **D-19's field semantics** (`liveness` is a
returned field from the canonical map, not a filter; `stale` is separate).

---

### R18 🟠 — THE FORBIDDEN-LAYER TRAP (system-POV prose leaking into agent prompt files)

**Failure mode.** The registry source comment for the `board` category — *"Explicit
`tools.allow: ["board"]` is the only access path"* — is copied into
`agents/maintenancer/tools_note.md`. It is **correct** in the registry and in a
`CATEGORY_DOC` consumed by `tool_help`, and **forbidden** in agent prompt prose.

**Mitigation.** The pre-commit gate (the guide's checklist,
`docs/agent-prompt-writing-guide.md:250`): grep the agent dir for `meta.json`,
`tools.allow`, `daemon/`, `_tool_registry`, `skill-set.yaml`, `agent_id=`, `seed_all`,
`innate_skills`, `default_agent_versions` ⇒ **0 hits** on the two prose files this
commission writes.

**Baseline recorded.** `agents/maintenancer/tools_note.md` is **CLEAN** today — keep it
clean. `agents/ari/soul.md` has a **PRE-EXISTING** deviation (a
`daemon/services/mission_resolver.py::` path at `:103`) — **not a licence** to add one, and
**not** to be treated as a precedent.

---

### R19 🟡 — THE PIN CEREMONY IS INCOMPLETE (a missed pin breaks SILENTLY)

**Failure mode.** A privileged-set pin is missed ⇒ the tree is red for another lane's CI
run, or (worse) a count pin is updated without the set pin ⇒ a divergence that passes one
assert and fails the other.

**Mitigation.** The canonical grep recipe is re-run at HEAD and the result is recorded in
`implementation-plan.md` §2.1: **7 files, 9 assertion sites**, plus **one non-test site**
(`ROLLOUT.md:51`), plus **one new registration pin**. The registry's SAME-PR RULE comment
is updated to name **EIGHT** pin files with the grep recipe, so the next commission does
not inherit a stale count.

**Trap recorded:** pin #1
(`tests/integration/test_service_tool_flag_off_byte_identical.py`) is
`@pytest.mark.integration` — the repo's default addopts **deselect** it. A green run that
skipped it is not evidence.

---

### R20 🟢 — THE INHERITED 3-RED PIN MAKES THE NEW ASSERTION UNUSABLE (avoided)

**Failure mode.** The commission adds an `instance_ops` assertion to a file that is already
3-red ⇒ the file's exit status is dominated by the inherited reds ⇒ the new assertion
carries **no usable green/red signal**.

**Mitigation.** **Fixed INLINE** (leader D-1) with the red counts disclosed in the commit
message (3 → 0, net-new 0), and the diff structured so the inherited fix and the new
assertion are independently readable against the base.

---
---

### R21 🔴 — THE LEADER'S `terminate_instance` REMAINS UNGOVERNED AFTER THIS COMMISSION

**Failure mode (pre-existing, GT-2 — verified).** `agents/leader/meta.json` holds the
`instance` category, which includes `terminate_instance`
(`daemon/tools/instance.py:5493-5503`) — **nine lines: fuzzy-resolve, `manager.terminate_instance(id)`,
return `{"terminated": result}`. No dry-run, no reason, no audit, no protected-target gate,
no kill-switch.** Every empty-allow agent (e.g. `agents/watcher/meta.json` → `"allow": []`)
also reaches it through the default-grant universe.

**This commission does NOT fix it.** `instance_ops` adds one **governed** path; it does not
remove the ungoverned ones. The framing consequence is important and must not be buried:
* **`instance_ops` does not create the blast radius — it already exists.** R9's "terminate a
  live chat root" describes a risk **already live in this daemon**, not one this commission
  introduces.
* **The commission's read surfaces are, in part, a way to DETECT damage the ungoverned path
  creates** — which strengthens, not weakens, the case for `instance_health`.

**The long-term right answers, all recorded as deferred (DF-8) and none taken here:** push
the governance **down into the service** (so every caller inherits it), or bring the
`instance` category **into the privileged set**, or retrofit the leader's tool to the
governed pattern. All three are **behaviour changes for existing callers** and require their
own blast-radius analysis.

**Why not fix it here.** It is a separate authorization decision about surfaces this
commission does not own; folding it in would widen a category-addition commission into a
fleet-authorization change. **Named follow-up, explicitly out of scope.**

---

## Part C — Explicitly-accepted limitations (not risks; boundary statements)

| # | Limitation | Why accepted |
|---|---|---|
| L-1 | `instance_reclaim` cannot resurrect a fully-dead instance with no resumable turn | No such primitive exists; D-3 forbids creating one (DF-1) |
| ~~L-2~~ | ~~`instance_list`'s agent/status/stale filters are applied in Python over a fetched page~~ | **RETRACTED — it was a CORRECTNESS DEFECT, not a limitation.** `status` is now **SQL-side** via the existing `repo.list(status=...)` (GT-3), and the hydration fields were moved out of the census. The remaining client-side predicates (`agent`, `parent_id`, staleness) operate on a page **whose status predicate is truthful** (AC-21) |
| L-3 | `instance_health` reports matches; it never repairs, and its detectors are **reporting projections** rather than composed sweep predicates | GT-4: no composable predicates exist. D-19 pairs the projections with **disagreement pins** where a counterpart is observable, and declares `disagreement_pin: null` where it is not — an honest gap, not a pretend one |
| L-4 | There is no `unprotect` marker | It would let a marker tool un-protect a **statically** protected target (D-4, DF-2) |
| L-5 | The question-pack store stays in-memory | A durable pack store is a new subsystem (D-3). The loss is surfaced honestly as `410 QUESTION_PACK_LOST` (R8) |
| L-6 | `instance_audit` is `instance_ops`-specific, not a generic framework | DF-5; a generic framework is a separate architecture commission |
| L-7 | The `instance_ops.protect` marker has no expiry | A protective marker that expires would silently un-protect a target. Expiry is a product decision, not a default |
| L-8 | The gate does not bind a user utterance to a specific target | R2 / DF-3 — needs a confirmation-intent mechanism. **Substantially mitigated by D-17** (`confirmed_target_id` makes a false claim attributable), not eliminated |
| L-9 | **A user message sent DIRECTLY to a maintenancer instance cannot authorize a protected terminate** (it carries a `pclass="direct"` window, which D-18 refuses) | **Design, not accident.** The maintenancer is a **delegated leaf, not user-facing**; a fleet-destructive act against a protected target should require a delegation chain from a user-facing turn. The refusal message names the workaround ("route via the leader"). This is the disclosed cost of closing R1 (D-18) |
| L-10 | The kill-switch gates the **act** category only; `instance_read` stays available when it is OFF | Mirrors the ens-db lane, which gates only the writer and leaves the SELECT-only readers live. An operator who disables acts still needs to **diagnose** (D-7) |
