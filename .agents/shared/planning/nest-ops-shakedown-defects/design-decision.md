# Nest-ops shakedown — design decision

**Commission:** the nest-ops shakedown defect patch (12 defects on the
live v0.27.x `instance_read` / `instance_ops` surface)
**Date:** 2026-10-24
**Branch:** `fix/nest-ops-shakedown-defects` (base `b257842c9`; merge M
`cdf348057`, M^1 `dfed5926f` — verified from `git rev-list --parents -n1`)
**Status:** IMPLEMENTED (Phases 1–4) — NO promote (the user's 3-factor
nonce ceremony owns it); NO staging performed by the coder lane (the
giter owns it)

This doc is the C1/C2/C3 class-level record the commission requires
(house skeleton: `running-zombie-fix/design-decision.md`). It builds
NOTHING — §6 is a statement, not a task.

---

## 1. The class shape — the husk is a NEW shape no healer can match BY CONSTRUCTION (C1)

The "husk" is a **spawned-but-never-dispatched instance row**: born by
`_spawn_instance_db_sync` with `status='idle'`, `version=1`,
`last_activity_at=NULL`, and — because dispatch never landed — zero
`task` rows, zero `message_queue` rows, zero `job_queue_items` rows, no
children, no question pack, no pause marker (research §A.5: first
dispatch ⟺ task ∧ message ∧ non-null activity; a husk has none of the
three, and `created_at` is its ONLY timestamp).

The two live targets the shakedown found
(`5ba21b2d-43b7-4378-be53-cca781f9c124`, 12 days old;
`7ccd53c3-2448-4f1f-9bb2-969ee9d1f37f`, 16 days) are exactly this shape,
produced by the most ordinary path in the system: a parent spawned a
child and then died or was abandoned before the first message
("the automatic dispatcher of a spawned child is its parent",
`mission_live_guard.py:805-807`; the spawn path itself enqueues nothing —
`instance_lifecycle.py:2330-2387` writes only metadata).

### 1.1 The per-healer early-exit evidence (why no existing lane can even SEE it)

Research §A.2, condensed to the early-exit each component takes:

| Healer / detector | The specific guard that skips a husk |
|---|---|
| running-zombie lane | never a candidate: the anchor join requires `Instance.status == RUNNING` + a `COMPLETED` `Task` with `completed_at` (`mission_strand_healer.py:931-949`); forced in, `has_any == False → "no-task-row"` (`:1015-1022`) |
| WC/ghost lane | requires a `waiting_children` ROOT (`:24-26`); as a member it must sit under a TERMINAL direct parent and be older than `ORPHAN_MIN_AGE_SECONDS` (`:32-38`, `:54-58`) — a root husk, or one under a live parent, is never touched |
| mission-live guard | the member-alive leg keys on non-terminal task rows; `attempt_at IS NULL` ⇒ "no dateable evidence of an in-flight turn" (`mission_live_guard.py:507-510`, `:640-671`) — the guard never blocks on a husk and never cleans it |
| zombie reaper | SQL demands `last_activity_at IS NOT NULL` (`repository.py:3451`, `:3488`) — a husk's NULL stamp is skipped forever |
| boot orphan sweep | selects RUNNING task-less rows ONLY, and runs ONLY at boot (`auto_continue_boot_pass.py:655-663`) |
| `instance_health` orphans (pre-patch) | `if not parent_id: continue` (`instance_read.py:1246-1247`) — a root husk invisible; a live parent ⇒ continue (`:1249-1250`) — a husk under a live parent invisible |
| census / capacity | `_STATUS_CANONICAL_MAP["idle"] = "processing"` (`work_status.py:87`) — the husk is bucketed as **"processing": "work is happening"** — the exact F2 census claim |
| `_ghost_child_filter` | matches the husk signature but only EXCLUDES ghosts from an active-children count (`child_reports.py:2877-2901`) — never retires |

**Class conclusion:** every existing healer requires at least one of
(a) a task row, (b) a message row, (c) a `waiting_children`/`running`
root posture, (d) a terminal reviewer parent, or (e) a non-NULL activity
stamp. A row with NONE is invisible to all seven simultaneously — there
is no predicate in the tree that keys on "spawned, never dispatched,
aged". The patch's answer is detector **#7 `never-dispatched-husk`**
(`instance_read.py`, Phase 1 / F3): REPORT-ONLY (the wedge report never
repairs — a husk is REPORTED, not cleaned), keyed on the evidence
ABSENCE set with the age leg on `created_at` (the only usable age column
— NULL rows have no `last_activity_at` age).

## 2. The adjudicated predicate (D-1 as amended)

Single-homed evidence expression (`never_dispatched_evidence_expr()`)
+ evidence-only method: the husk detector reuses the SAME evidence
conjunction the canonical ghost signature uses
(`filter_never_dispatched_ids` / `_ghost_child_filter`), narrowed —
never widened — by the additional legs (non-terminal, null activity,
no JobItem, no children, no pack, no marker, age >
`DEFAULT_HUSK_MIN_AGE_SECONDS` = 86 400 s). The containment pin
(`TestF3EvidenceContainment` + the real-PG
`test_husk_set_satisfies_the_canonical_ghost_signature`) makes the
direction enforceable: every husk-flagged row satisfies the canonical
set. Threshold rationale (read-side §3.7): the spawn-then-message ritual
is seconds-to-minutes; `ORPHAN_MIN_AGE_SECONDS = 60` is its floor; 24 h
is 1,440× that — a row idle-shaped for 24 h is not mid-ritual. The
threshold stays a module constant (D-1: no config field). The scan
feeds from F4b's offset-window loop (`_build_health_report`), because
the detector alone is blind past `FLEET_SCAN_LIMIT` — the F3×F4
coupling pin proves the dependency on a >window fleet (the two targets
sit at offsets 1001/1002).

**Cost, MEASURED (T4.8, D-20's arbiter — real PG16, 1,003-row two-tier
fixture; full record `phase4-evidence.md` §1):** per-window query
counts **[1002, 5, 3]** vs the D-20 gate `FLEET_SCAN_LIMIT + 2 = 1002`
— window 1 sits EXACTLY at the gate (1 page read + 1,000 per-row child
lookups: the A2 N+1, bounded at exactly one query per row; a second
per-row query of any kind would FAIL the gate); full-scan wall-time
**3.2–3.6 s** vs the 10 s budget; `scan_stop_reason="end_of_set"`,
`scan_truncated=false`, both husks covered. All three D-20 thresholds
PASS. Batched children (`list_child_ids_permanent_bulk`) remain the
recorded first-class follow-up (see §7).

## 3. Not adjudicated here

The F6 gate split (D-8, leader), the F7 succeed-with-disclosure ruling,
the F11 chain architecture (Option B, D-7) and its forgery controls are
ADJUDICATED decisions recorded in `decisions.md` and implemented in
Phases 1/3; they are restated in the `tools_note.md` agent surface, not
re-argued here. One precedence disclosure carries forward because it is
user-visible: **F6-iv** — the budget/cascade rails run BEFORE the
protected gate, so an exhausted budget (or a `cascade=False` orphan
refusal) on a protected target surfaces `terminate-budget-exceeded`
instead of `protected-target-confirmation-required`. BOTH are refusals;
no authority is granted either way; the precedence is pinned
(`TestF6GateSplit::test_f6_iv_budget_precedes_the_protected_gate` + the
real-PG twin).

## 4. Evidence — spawn-side attribution is unrecoverable past log rotation (C2)

The `instances` row carries `parent_id`, the child's own
`agent_id`/`agent_dir`/`agent_name`/`agent_tag`, and spawn metadata —
and **no durable "who requested this spawn" field**. A repo-wide grep
finds no `spawned_by`/`spawn_source`/`requested_by` column in `daemon/`
(the only `requested_by` is an unrelated resource-router field,
`routers/resources.py:1247`). What exists today:

1. **The parent's row** — `instance_get` derives `spawned_by.agent_id`
   by walking `parent_id` up (`instance_read.py:667-701`), stating
   honestly when it cannot (`"no-parent"`,
   `"parent-agent-unresolved"`). INDIRECT (names the parent's agent,
   not the requesting caller) and evaporates when the parent row is
   deleted.
2. **The spawn log line** —
   `logger.info("Spawning instance {…} (agent=…, parent=…, name=…,
   model=…, source=…)")` (`instance_lifecycle.py:2419-2424`) — the only
   record naming the spawn context, and it **dies with log rotation**.
3. **The transient SSE `instance_created` event** (`:2490-2500`) — not
   durable.

Therefore: after log rotation, a husk's spawner is unrecoverable from
the database. The patch adds no attribution record (out of scope) — but
any future husk-remediation act (terminate/reclaim) acts on a row whose
origin it cannot prove. The act tools' docs must not imply provenance
they lack; `tools_note.md` carries the operator-facing consequence
(T5.2).

## 5. NOT fixed here — the spawn-without-dispatch cleanup STATEMENT (C3)

**No spawn-without-dispatch cleanup exists in the healer/sweep family,
and this patch does NOT build one.** Verified (research §A.4, this
tree): `never_dispatched` grep → the predicate + three consumers + docs
only; `InstanceStatus.IDLE` across `daemon/services/**` → 5 hits, not
one a sweep selection; no periodic service selects
`status == 'idle'`. `filter_never_dispatched_ids` is a CLASSIFIER
(pause-cascade skip; guard liveness exclusion under terminal parents;
WC-lane signature), not a cleaner; `_ghost_child_filter` only declines
to COUNT ghosts. Scope honesty: the absence is verified for the
healer/sweep family, NOT proven for every conceivable code path.

A cleanup is NEEDED as future work — after this patch the class is
REPORTED but not cleaned — and must make its own safety argument in its
own commission:

* **the ritual race** — a spawn's first legitimate message may land
  seconds after any sweep looks; a cleanup racing the
  spawn-then-message ritual terminalizes live children;
* **live-parent protection** — the strand healer explicitly refuses
  never-dispatched rows under a LIVE parent
  (`mission_strand_healer.py:54-56`) and the pause cascade avoids them
  (`instance_lifecycle.py:751-756`, "sets up the wedged-tree class");
* **terminalizing blast radius** — a cleanup WRITE flips status to a
  terminal value (a one-way transition consumers treat as death), on a
  row whose age evidence is a single TEXT timestamp;
* **the version≥2 escape** — the classifier's own `idle ∧ version==1`
  conjuncts miss a never-dispatched row that received ANY committed
  update (`version_id_col` auto-increments; research §A.4 second-order
  hole) — a cleanup keyed on the same predicate inherits the hole.

## 6. Routed-findings register (plan-overview §8, verbatim — NEVER tasks here)

**Read-side adjacent findings (read-side §10):** A1 `waiting` in neither
status set (false-positive class in two detectors — own commission); A2
N+1 child lookup per fleet row — **FIRST-CLASS FOLLOW-UP (arch §4(c)),
not merely costed**: exact diff shape = `list_child_ids_permanent_bulk(ids)
-> dict[str, list[str]]` (`SELECT parent_id, instance_id FROM instances
WHERE parent_id IN (…)`, ~12 lines) + 3–5 lines at
`instance_lifecycle.py:5101-5106` + a parity pin ≈ 20 lines / 2 files.
OUT of this patch by blast radius, NOT size: it changes a shared manager
method used by the REST list route and collides with Phase 3's manager
edits (F11). With it the scan is ~40 queries and the 20-window cap
becomes SAFE (bounded by D-20's gate until then); A3 forked
task-terminal constants `instance_read.py:713/:1051` (the husk detector
imports the canonical set — D-1 — but the existing forks are NOT fixed
here); A4 duplicate terminal-status tuple `repository.py:41-46` vs
`constants.py:702-707`; A5 `mission_ref` permanently null (ties to
GT-4).

**Routed clean-up (F3 evidence-expression twin):**
`child_reports._ghost_child_filter` stays untouched IN THIS PATCH
(pre-existing duplicate, pinned in lockstep; touching the
parent-completion gate expands blast radius). Optional clean-up =
delegate the twin to `never_dispatched_evidence_expr()` (3 lines) —
ROUTED, not in this patch (arch §1.2 item 5, D-19).

**Ops-side adjacent findings (ops-side §8):** F-1 protected gate exists
only on terminate (reclaim/nudge/mark/answer/dismiss ungated); F-2 nudge
liveness test diverges from `has_live_graph_task` + no self-surgery
gate; F-3 nudge's dispatch source pops the target's provenance windows;
F-4 uniform no-row-id gap on ALL six apply responses (F10 fixes mark
ONLY; terminate needs a mutable cell in `_audit_sink`); F-5 `"note"` vs
`"note_line"` payload-key inconsistency; F-6 REST-ingress forge residual
(`jobs_crud.py:597`, critical note `257488dc`) + DF-5 defer-resend mint
— explicitly NOT closed by DF-4 or F11; F11 must not widen it (the S3
unconditional pop is the guard).

**Disclosed F11 residuals (owners recorded, arch §7.4):** (a) the DF-5
defer-resend (`job_queue_service.py:2605-2609`) re-enqueues with
`source="api"` → the child gets a **DIRECT window with no chain** (MORE
authority than any chain can grant) — out of scope, disclosed residual
with an owner; (b) the **silent-resume edge**: a checkpoint resume
(`silent=True`) never mints an inherited window → the child refuses
`no-user-provenance` — TODAY's semantics, DOCUMENTED + PINNED in F11,
not fixed.

**Deferral decisions:** `preview_run_id` threading (ops-side §5.2:
optional `preview_row_id` input validated against the ledger — do not
build); census `never_active` field (read-side §4.4 non-goal);
spawn-without-dispatch cleanup (GT-2 — §5 above, statement only); husk
threshold as config field (D-1 keeps it a constant).

## 7. Residuals carried out of this patch

| ID | Residual | Where recorded |
|---|---|---|
| R7 | the >24 h deliberately-held spawn false-positive surface (no such pattern exists today; a future "spawn now, dispatch on a schedule" pattern must raise the threshold or stamp a dispatch-intent artifact) | read-side §3.7; detector docstring |
| GT-4 | `mission_ref` permanently null — the instance surface cannot answer "is this instance part of a mission?" (the husk predicate therefore cannot carry a mission leg — structurally vacuous, a finding not a defect) | read-side §3.4, §10 A5 |
| F8 payload delta | cold reads no longer promote `agent_tag`/`agent_dir` onto the row — a deliberate behaviour delta of read-path purity (the re-elevation write is gone) | Phase-1 F8 pins (`test_cold_read_leaves_the_row_byte_identical`) |
| F11 silent-resume edge | a checkpoint-resumed child never inherits a window — today's semantics, deliberate, pinned (`test_silent_resume_never_mints_an_inherited_window`) | arch §7.4(b); `tools_note.md` |
| DF-5 sibling residual | the defer-resend direct-window mint (`job_queue_service.py:2608`) — MORE authority than any chain grants; own commission | §6 F-6/owners row above |
| A2 cost note | the scan's per-window cost sits exactly AT the D-20 gate (§2, measured) — bounded but not batched; the batched-children follow-up is the relief | §2 above; `phase4-evidence.md` §1 |

## 8. Inherited-drift disclosure (prompt artifacts + the sealed pin)

The `agents/.prompt-artifacts.sha256` re-stamp (T5.2) regenerates the
whole tree and TRANSITIVELY absorbs 6 upstream-drifted files —
`agents/ari/rule.md`, `agents/gaia/rule.md`, `agents/gaia/soul.md`,
`agents/gaia/tools_note.md`, `agents/gaia/workflow.md`,
`agents/leader/soul.md` — all inherited from `origin/latest`
(`074833b45`); this lane's `agents/` delta since the merge is EMPTY.
Those 6 rows are INHERITED upstream drift, not this lane's edits.
Separately, `SEALED_SHA_BASELINES` in
`tests/test_chart_image_delivery_e2e.py` carried exactly 1 stale entry —
`agents/ari/rule.md` (pinned `bb784e8c41af`, actual `7b056aedb1e9`) —
also inherited; re-pinned in its own small commit (payload untouched —
the v0.26.0 V-1 precedent).

### 8.1 A1 — the migration delta: EMPTY for THIS LANE, +2 base→HEAD (upstream-inherited)

**The claim, and its two scopes.** This lane authored NO `.sql` file
and touched NO migration: `git log cdf348057..HEAD --
daemon/migrations/versions/` returns **zero** commits (any author), and
`git diff cdf348057..HEAD -- daemon/migrations/versions/` is **empty**.
Scoped to this lane's own work, the migration delta is **EMPTY**.

Scoped to **base→HEAD** it is **+2**, and both files are UPSTREAM
-INHERITED — they arrive with the lane's merge `cdf348057` (which
merged `074833b45`, "Merge feature/remove-cu-builtin"), and the lane's
base `b257842c9` carried NEITHER of them
(`git ls-tree b257842c9 daemon/migrations/versions/` lists no
`20261022_*`):

| File (added in the base→HEAD diff) | Upstream authoring commit | Upstream lane |
|---|---|---|
| `20261022_000001_create_mcp_gateway_audit.sql` | `46d34d8d0` — *feat(mcps-gateway): Phase B — the remote-hands center gateway + mcp_rpc tools* | mcps-gateway |
| `20261022_000002_create_wedge_captures.sql` | added by `d78412ceb` — *feat(wedge-capture): the evidence-instrument core*; amended by `4f9f01e1f` — *fix(wedge-capture): migration record — jsonb mapping note + neutralized DOWN* | wedge-capture |

**Why the disclosure is load-bearing.** A stage-time gate that reads
"the lane's migration delta is EMPTY" and compares **base→HEAD** would
compute +2 and (correctly, per the house rule) demand an explicit
adjudication. The adjudication is now on the record: the +2 are
inherited upstream migrations that this lane neither authored nor
modified, they are additive CREATE-TABLE records, and the lane's own
schema contribution is nil. A future stage MUST report the +2 (and the
`known_schema_gen` head moves to `20261022_000002_create_wedge_captures.sql`)
— it must NOT report "EMPTY" on a base→HEAD comparison.
